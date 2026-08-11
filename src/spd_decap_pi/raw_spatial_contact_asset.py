"""Source-faithful persistence for raw SPD spatial contacts.

The asset deliberately stores raw finite-via topology and source geometry, not
solver strips or WKB.  It is independently versioned and has no production
imports: callers must explicitly build and load it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, fields
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
from types import MappingProxyType
from typing import Any, Final, TypeVar
from urllib.parse import quote
import zlib


RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY: Final = "raw_spatial_contact_asset"
RAW_SPATIAL_CONTACT_ASSET_SCHEMA: Final = "spd-raw-spatial-contact-asset-v2"
RAW_SPATIAL_CONTACT_PAYLOAD_SCHEMA: Final = "spd-raw-spatial-contact-sqlite-v2"
RAW_SPATIAL_CONTACT_COMPILER_ID: Final = "raw-spd-finite-via-spatial-contact-v2"
RAW_SPATIAL_CONTACT_ASSET_PREFIX: Final = "spatial/"
RAW_SPATIAL_CONTACT_COMPRESSION: Final = "zlib"

# A raw asset is one already-compressed ``.zlib`` scenario member.  Keep this
# exactly aligned with scenario_io.MAX_SCENARIO_MEMBER_BYTES; the archive stores
# these members without a second compression layer.
MAX_RAW_SPATIAL_COMPRESSED_BYTES: Final = 1024 * 1024 * 1024
MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES: Final = 4 * 1024 * 1024 * 1024
MAX_RAW_SPATIAL_EXPANSION_RATIO: Final = 128
MAX_RAW_SPATIAL_ROWS_PER_SECTION: Final = 20_000_000
MAX_RAW_SPATIAL_TEXT_BYTES: Final = 16 * 1024
MAX_RAW_SPATIAL_QUERY_ROWS: Final = 100_000

_APPLICATION_ID: Final = 0x53505257  # "SPRW"
_USER_VERSION: Final = 2
_STREAM_BYTES: Final = 1024 * 1024
_BATCH_ROWS: Final = 10_000
_SQLITE_PAGE_SIZE: Final = 4096
_SQLITE_PROGRESS_STEPS: Final = 10_000
_EXPANSION_GRACE_BYTES: Final = 1024 * 1024
_SHA_FIELDS: Final = frozenset(
    {
        "source_sha256",
        "section_sha256",
        "source_record_sha256",
        "artwork_asset_sha256",
        "island_manifest_sha256",
    }
)
_SECTIONS: Final = (
    "source_coverage",
    "section_coverage",
    "layers",
    "padstacks",
    "pad_shapes",
    "surfaces",
    "nodes",
    "traces",
    "vias",
)
_GEOMETRY_SECTIONS: Final = (
    "layers",
    "padstacks",
    "pad_shapes",
    "surfaces",
    "nodes",
    "traces",
    "vias",
)
_MANIFEST_KEYS: Final = frozenset(
    {
        "storage_schema",
        "payload_schema",
        "compiler_id",
        "source_sha256",
        "project_binding_sha256",
        "certificate_evidence_sha256",
        "compiled_topology_identity_sha256",
        "geometry_identity_sha256",
        "logical_rows_sha256",
        "asset_name",
        "compression",
        "compressed_size_bytes",
        "compressed_sha256",
        "uncompressed_size_bytes",
        "uncompressed_sha256",
        "counts",
    }
)
_META_KEYS_WITHOUT_LOGICAL: Final = frozenset(
    {
        "payload_schema",
        "compiler_id",
        "source_sha256",
        "project_binding_sha256",
        "certificate_evidence_sha256",
        "compiled_topology_identity_sha256",
        "geometry_identity_sha256",
        *(f"{section}_count" for section in _SECTIONS),
    }
)
_META_KEYS: Final = frozenset((*_META_KEYS_WITHOUT_LOGICAL, "logical_rows_sha256"))

# The raw asset is meaningful only beside the exact compiled topology from
# which its project/topology bindings were derived.  Keep this small envelope
# contract local so archive validation does not open either SQLite payload (or
# hash the compiled attachment a second time after the existing topology gate
# has already validated it).
_COMPILED_TOPOLOGY_ASSET_METADATA_KEY: Final = (
    "layerwise_compiled_topology_asset"
)
_SURFACE_CERTIFICATE_METADATA_KEY: Final = (
    "layerwise_surface_connectivity_certificate"
)
_RESERVED_SPD_IMPORT_METADATA_KEYS: Final = frozenset(
    {
        "source_sha256",
        RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY,
        _COMPILED_TOPOLOGY_ASSET_METADATA_KEY,
        _SURFACE_CERTIFICATE_METADATA_KEY,
    }
)
_COMPILED_TOPOLOGY_MANIFEST_KEYS: Final = frozenset(
    {
        "storage_schema",
        "payload_schema",
        "compiler_id",
        "surface_schema_version",
        "surface_compiler_id",
        "source_sha256",
        "certificate_evidence_sha256",
        "surface_asset_uncompressed_size_bytes",
        "surface_asset_uncompressed_sha256",
        "project_binding_sha256",
        "topology_identity_sha256",
        "logical_rows_sha256",
        "asset_name",
        "compression",
        "compressed_size_bytes",
        "compressed_sha256",
        "uncompressed_size_bytes",
        "uncompressed_sha256",
    }
)
_COMPILED_TOPOLOGY_ASSET_SCHEMA: Final = (
    "spd-layerwise-compiled-topology-asset-v1"
)
_COMPILED_TOPOLOGY_PAYLOAD_SCHEMA: Final = (
    "spd-layerwise-compiled-topology-sqlite-v1"
)
_COMPILED_TOPOLOGY_COMPILER_ID: Final = (
    "layerwise-compiled-topology-sqlite-v1"
)
_COMPILED_TOPOLOGY_SURFACE_SCHEMA: Final = (
    "spd-layer-surface-connectivity-v4"
)
_COMPILED_TOPOLOGY_SURFACE_COMPILER: Final = (
    "powersi-same-layer-trace-artwork-finite-via-quotient-v4"
)
_MAX_COMPILED_TOPOLOGY_COMPRESSED_BYTES: Final = 512 * 1024 * 1024
_MAX_COMPILED_TOPOLOGY_UNCOMPRESSED_BYTES: Final = 3 * 1024 * 1024 * 1024
_MAX_COMPILED_SURFACE_UNCOMPRESSED_BYTES: Final = 8 * 1024 * 1024 * 1024
_MAX_COMPILED_TOPOLOGY_EXPANSION_RATIO: Final = 128


class RawSpatialContactAssetError(ValueError):
    """The raw spatial asset is invalid, unsafe, stale, or inconsistent."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "RAW_SPATIAL_ASSET_INVALID"
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise RawSpatialContactAssetError(code, message)


def _strict_int(value: object, label: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        _fail("RAW_SPATIAL_ROW_INVALID", f"{label} must be an integer")
    if minimum is not None and value < minimum:
        _fail("RAW_SPATIAL_ROW_INVALID", f"{label} is below its minimum")
    if value < -(2**63) or value > 2**63 - 1:
        _fail("RAW_SPATIAL_ROW_INVALID", f"{label} exceeds SQLite integer range")
    return value


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        _fail("RAW_SPATIAL_ROW_INVALID", f"{label} must be non-empty trimmed text")
    if (
        "\x00" in value
        or len(value.encode("utf-8")) > MAX_RAW_SPATIAL_TEXT_BYTES
        or len(value.casefold().encode("utf-8")) > MAX_RAW_SPATIAL_TEXT_BYTES
    ):
        _fail("RAW_SPATIAL_ROW_INVALID", f"{label} is unsafe or too long")
    return value


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _sha(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64:
        _fail("RAW_SPATIAL_SHA_INVALID", f"{label} is not a SHA-256 digest")
    lowered = value.casefold()
    if lowered != value or any(character not in "0123456789abcdef" for character in value):
        _fail("RAW_SPATIAL_SHA_INVALID", f"{label} is not canonical SHA-256")
    return value


def _bbox(values: tuple[int, int, int, int], label: str) -> None:
    for index, value in enumerate(values):
        _strict_int(value, f"{label}[{index}]")
    if values[0] > values[2] or values[1] > values[3]:
        _fail("RAW_SPATIAL_GEOMETRY_INVALID", f"{label} is inverted")


def _normalized_rotation(value: object, label: str) -> int:
    result = _strict_int(value, label)
    if not -180_000_000 <= result < 180_000_000:
        _fail("RAW_SPATIAL_PAD_SHAPE_INVALID", f"{label} is not normalized")
    return result


@dataclass(frozen=True, slots=True)
class RawSpatialSourceCoverageRow:
    ordinal: int
    source_basename: str
    source_size_bytes: int
    source_sha256: str
    raw_header_count: int
    logical_record_count: int

    def __post_init__(self) -> None:
        _strict_int(self.ordinal, "source coverage ordinal", minimum=0)
        if self.ordinal != 0:
            _fail("RAW_SPATIAL_ORDINAL_INVALID", "source coverage ordinal must be zero")
        _text(self.source_basename, "source_basename")
        if any(character in self.source_basename for character in ("/", "\\")):
            _fail("RAW_SPATIAL_COVERAGE_INVALID", "source_basename is not a basename")
        _strict_int(self.source_size_bytes, "source_size_bytes", minimum=1)
        _sha(self.source_sha256, "coverage source")
        _strict_int(self.raw_header_count, "raw_header_count", minimum=0)
        _strict_int(self.logical_record_count, "logical_record_count", minimum=0)
        if self.logical_record_count != self.raw_header_count:
            _fail(
                "RAW_SPATIAL_COVERAGE_INVALID",
                "source raw-header and logical-record counts differ",
            )


@dataclass(frozen=True, slots=True)
class RawSpatialSectionCoverageRow:
    ordinal: int
    section_name: str
    byte_start: int
    byte_end: int
    byte_size: int
    section_sha256: str
    raw_header_count: int
    logical_record_count: int
    parsed_count: int
    resolved_count: int
    unresolved_count: int
    retained_count: int
    out_of_scope_count: int

    def __post_init__(self) -> None:
        _strict_int(self.ordinal, "section coverage ordinal", minimum=0)
        if self.section_name not in {"Node", "Trace", "Via"}:
            _fail("RAW_SPATIAL_COVERAGE_INVALID", "coverage section name differs")
        for name in (
            "byte_start",
            "byte_end",
            "byte_size",
            "raw_header_count",
            "logical_record_count",
            "parsed_count",
            "resolved_count",
            "unresolved_count",
            "retained_count",
            "out_of_scope_count",
        ):
            _strict_int(getattr(self, name), name, minimum=0)
        if self.byte_end < self.byte_start or self.byte_size != self.byte_end - self.byte_start:
            _fail("RAW_SPATIAL_COVERAGE_INVALID", "section byte interval differs")
        if self.logical_record_count and self.byte_size == 0:
            _fail(
                "RAW_SPATIAL_COVERAGE_INVALID",
                "non-empty section records require a non-empty byte interval",
            )
        _sha(self.section_sha256, "source section")
        if (
            self.parsed_count != self.logical_record_count
            or self.raw_header_count != self.logical_record_count
            or self.parsed_count != self.resolved_count + self.unresolved_count
            or self.parsed_count != self.retained_count + self.out_of_scope_count
        ):
            _fail("RAW_SPATIAL_COVERAGE_INVALID", "section cardinalities do not balance")


@dataclass(frozen=True, slots=True)
class RawSpatialLayerRow:
    ordinal: int
    layer_id: str
    is_conductor: bool
    source_record_sha256: str

    def __post_init__(self) -> None:
        _strict_int(self.ordinal, "layer ordinal", minimum=0)
        _text(self.layer_id, "layer_id")
        if type(self.is_conductor) is not bool:
            _fail("RAW_SPATIAL_ROW_INVALID", "layer is_conductor must be a boolean")
        _sha(self.source_record_sha256, "layer source record")


@dataclass(frozen=True, slots=True)
class RawSpatialSurfaceRow:
    ordinal: int
    surface_id: str
    net_name: str
    layer_id: str
    artwork_asset_sha256: str
    island_manifest_sha256: str
    source_record_sha256: str
    min_x_pm: int
    min_y_pm: int
    max_x_pm: int
    max_y_pm: int

    def __post_init__(self) -> None:
        _strict_int(self.ordinal, "surface ordinal", minimum=0)
        for name in ("surface_id", "net_name", "layer_id"):
            _text(getattr(self, name), name)
        for name in (
            "artwork_asset_sha256",
            "island_manifest_sha256",
            "source_record_sha256",
        ):
            _sha(getattr(self, name), name)
        _bbox((self.min_x_pm, self.min_y_pm, self.max_x_pm, self.max_y_pm), "surface bbox")


@dataclass(frozen=True, slots=True)
class RawSpatialPadstackRow:
    ordinal: int
    padstack_id: str
    drill_diameter_pm: int | None
    material: str | None
    source_record_sha256: str

    def __post_init__(self) -> None:
        _strict_int(self.ordinal, "padstack ordinal", minimum=0)
        _text(self.padstack_id, "padstack_id")
        if self.drill_diameter_pm is not None:
            _strict_int(self.drill_diameter_pm, "drill_diameter_pm", minimum=1)
        _optional_text(self.material, "padstack material")
        _sha(self.source_record_sha256, "padstack source record")


@dataclass(frozen=True, slots=True)
class RawSpatialPadShapeRow:
    ordinal: int
    padstack_id: str
    layer_id: str
    shape_kind: str
    width_pm: int
    height_pm: int
    source_record_sha256: str

    def __post_init__(self) -> None:
        _strict_int(self.ordinal, "pad shape ordinal", minimum=0)
        _text(self.padstack_id, "pad shape padstack_id")
        _text(self.layer_id, "pad shape layer_id")
        if self.shape_kind not in {"CIRCLE", "RECTANGLE"}:
            _fail("RAW_SPATIAL_PAD_SHAPE_INVALID", "pad shape kind is unsupported")
        _strict_int(self.width_pm, "pad width_pm", minimum=1)
        _strict_int(self.height_pm, "pad height_pm", minimum=1)
        if self.shape_kind == "CIRCLE" and self.width_pm != self.height_pm:
            _fail("RAW_SPATIAL_PAD_SHAPE_INVALID", "circle dimensions must be equal")
        _sha(self.source_record_sha256, "pad shape source record")


@dataclass(frozen=True, slots=True)
class RawSpatialNodeRow:
    ordinal: int
    node_id: str
    net_name: str | None
    net_status: str
    layer_id: str
    x_pm: int
    y_pm: int
    padstack_id: str | None
    rotation_microdegrees: int | None
    source_record_sha256: str

    def __post_init__(self) -> None:
        _strict_int(self.ordinal, "node ordinal", minimum=0)
        for name in ("node_id", "layer_id"):
            _text(getattr(self, name), name)
        if self.net_status not in {"EXPLICIT", "INCIDENCE", "OUT_OF_SCOPE"}:
            _fail("RAW_SPATIAL_NODE_INVALID", "node net_status is unsupported")
        if self.net_status == "OUT_OF_SCOPE":
            if self.net_name is not None:
                _fail(
                    "RAW_SPATIAL_NODE_INVALID",
                    "out-of-scope node must not retain a net",
                )
        elif self.net_name is None:
            _fail("RAW_SPATIAL_NODE_INVALID", "resolved node must retain a net")
        else:
            _text(self.net_name, "node net_name")
        _strict_int(self.x_pm, "node x_pm")
        _strict_int(self.y_pm, "node y_pm")
        _optional_text(self.padstack_id, "node padstack_id")
        if (self.padstack_id is None) != (self.rotation_microdegrees is None):
            _fail("RAW_SPATIAL_PAD_SHAPE_INVALID", "node padstack rotation evidence differs")
        if self.rotation_microdegrees is not None:
            _normalized_rotation(self.rotation_microdegrees, "node rotation")
        _sha(self.source_record_sha256, "node source record")


@dataclass(frozen=True, slots=True)
class RawSpatialTraceRow:
    ordinal: int
    trace_id: str
    net_name: str
    layer_id: str
    start_node_id: str
    end_node_id: str
    width_pm: int | None
    width_status: str
    width_location: str
    geometry_status: str
    owner_id: str
    source_record_sha256: str
    min_x_pm: int
    min_y_pm: int
    max_x_pm: int
    max_y_pm: int

    def __post_init__(self) -> None:
        _strict_int(self.ordinal, "trace ordinal", minimum=0)
        for name in (
            "trace_id",
            "net_name",
            "layer_id",
            "start_node_id",
            "end_node_id",
            "width_location",
        ):
            _text(getattr(self, name), name)
        if self.width_status not in {"EXACT", "MISSING", "UNRESOLVED"}:
            _fail("RAW_SPATIAL_WIDTH_INVALID", "trace width_status is unsupported")
        if self.geometry_status not in {"EXACT", "TOPOLOGY_ONLY", "UNRESOLVED"}:
            _fail("RAW_SPATIAL_GEOMETRY_INVALID", "trace geometry_status is unsupported")
        if self.width_location not in {
            "same_line",
            "continuation",
            "ABSENT",
            "UNKNOWN",
        }:
            _fail("RAW_SPATIAL_WIDTH_INVALID", "trace width_location is unsupported")
        if self.width_status == "EXACT":
            _strict_int(self.width_pm, "trace width_pm", minimum=1)
            if self.width_location not in {"same_line", "continuation"}:
                _fail("RAW_SPATIAL_WIDTH_INVALID", "exact width location differs")
            if self.geometry_status != "EXACT":
                _fail("RAW_SPATIAL_GEOMETRY_INVALID", "exact width geometry differs")
        elif self.width_pm is not None:
            _fail("RAW_SPATIAL_WIDTH_INVALID", "non-exact width must be absent")
        elif self.width_status == "MISSING":
            if self.width_location != "ABSENT" or self.geometry_status != "TOPOLOGY_ONLY":
                _fail("RAW_SPATIAL_WIDTH_INVALID", "missing width state differs")
        elif self.width_location == "ABSENT" or self.geometry_status != "UNRESOLVED":
            _fail("RAW_SPATIAL_WIDTH_INVALID", "unresolved width state differs")
        expected_owner = f"trace:{self.net_name.casefold()}:{self.trace_id.casefold()}"
        if self.owner_id != expected_owner:
            _fail("RAW_SPATIAL_OWNER_INVALID", "trace owner is not composite-net exact")
        _sha(self.source_record_sha256, "trace source record")
        _bbox((self.min_x_pm, self.min_y_pm, self.max_x_pm, self.max_y_pm), "trace bbox")


@dataclass(frozen=True, slots=True)
class RawSpatialViaRow:
    ordinal: int
    via_id: str
    net_name: str
    start_layer_id: str
    end_layer_id: str
    start_node_id: str
    end_node_id: str
    padstack_id: str
    status: str
    owner_id: str
    start_x_pm: int
    start_y_pm: int
    end_x_pm: int
    end_y_pm: int
    rotation_microdegrees: int
    source_record_sha256: str

    def __post_init__(self) -> None:
        _strict_int(self.ordinal, "via ordinal", minimum=0)
        for name in (
            "via_id",
            "net_name",
            "start_layer_id",
            "end_layer_id",
            "start_node_id",
            "end_node_id",
            "padstack_id",
        ):
            _text(getattr(self, name), name)
        if self.status not in {"EXACT", "UNRESOLVED", "OUT_OF_SCOPE"}:
            _fail("RAW_SPATIAL_VIA_INVALID", "via status is unsupported")
        expected_owner = f"via:{self.net_name.casefold()}:{self.via_id.casefold()}"
        if self.owner_id != expected_owner:
            _fail("RAW_SPATIAL_OWNER_INVALID", "via owner is not composite-net exact")
        for name in ("start_x_pm", "start_y_pm", "end_x_pm", "end_y_pm"):
            _strict_int(getattr(self, name), f"via {name}")
        _normalized_rotation(self.rotation_microdegrees, "via rotation")
        if self.status == "EXACT" and (
            (self.start_x_pm, self.start_y_pm) != (self.end_x_pm, self.end_y_pm)
        ):
            _fail("RAW_SPATIAL_VIA_INVALID", "slanted via endpoints are unsupported")
        _sha(self.source_record_sha256, "via source record")


_CREATE_SQL: Final = """
CREATE TABLE meta (
    key TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE source_coverage (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    source_basename TEXT NOT NULL,
    source_size_bytes INTEGER NOT NULL,
    source_sha256 TEXT NOT NULL,
    raw_header_count INTEGER NOT NULL,
    logical_record_count INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE section_coverage (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    section_name TEXT NOT NULL UNIQUE,
    byte_start INTEGER NOT NULL,
    byte_end INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    section_sha256 TEXT NOT NULL,
    raw_header_count INTEGER NOT NULL,
    logical_record_count INTEGER NOT NULL,
    parsed_count INTEGER NOT NULL,
    resolved_count INTEGER NOT NULL,
    unresolved_count INTEGER NOT NULL,
    retained_count INTEGER NOT NULL,
    out_of_scope_count INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE layers (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    layer_id TEXT NOT NULL,
    layer_id_fold TEXT NOT NULL UNIQUE,
    is_conductor INTEGER NOT NULL,
    source_record_sha256 TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE padstacks (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    padstack_id TEXT NOT NULL,
    padstack_id_fold TEXT NOT NULL UNIQUE,
    drill_diameter_pm INTEGER,
    material TEXT,
    source_record_sha256 TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE pad_shapes (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    padstack_id TEXT NOT NULL,
    padstack_id_fold TEXT NOT NULL,
    layer_id TEXT NOT NULL,
    layer_id_fold TEXT NOT NULL,
    shape_kind TEXT NOT NULL,
    width_pm INTEGER NOT NULL,
    height_pm INTEGER NOT NULL,
    source_record_sha256 TEXT NOT NULL,
    UNIQUE (padstack_id_fold, layer_id_fold)
) WITHOUT ROWID;
CREATE TABLE surfaces (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    surface_id TEXT NOT NULL,
    surface_id_fold TEXT NOT NULL UNIQUE,
    net_name TEXT NOT NULL,
    net_fold TEXT NOT NULL,
    layer_id TEXT NOT NULL,
    layer_id_fold TEXT NOT NULL,
    artwork_asset_sha256 TEXT NOT NULL,
    island_manifest_sha256 TEXT NOT NULL,
    source_record_sha256 TEXT NOT NULL,
    min_x_pm INTEGER NOT NULL,
    min_y_pm INTEGER NOT NULL,
    max_x_pm INTEGER NOT NULL,
    max_y_pm INTEGER NOT NULL,
    UNIQUE (net_fold, layer_id_fold)
) WITHOUT ROWID;
CREATE TABLE nodes (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    node_id TEXT NOT NULL,
    node_id_fold TEXT NOT NULL,
    net_name TEXT,
    net_fold TEXT,
    net_status TEXT NOT NULL,
    layer_id TEXT NOT NULL,
    layer_id_fold TEXT NOT NULL,
    x_pm INTEGER NOT NULL,
    y_pm INTEGER NOT NULL,
    padstack_id TEXT,
    padstack_id_fold TEXT,
    rotation_microdegrees INTEGER,
    source_record_sha256 TEXT NOT NULL,
    UNIQUE (node_id_fold)
) WITHOUT ROWID;
CREATE TABLE traces (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    trace_id TEXT NOT NULL,
    trace_id_fold TEXT NOT NULL,
    net_name TEXT NOT NULL,
    net_fold TEXT NOT NULL,
    layer_id TEXT NOT NULL,
    layer_id_fold TEXT NOT NULL,
    start_node_id TEXT NOT NULL,
    start_node_id_fold TEXT NOT NULL,
    end_node_id TEXT NOT NULL,
    end_node_id_fold TEXT NOT NULL,
    width_pm INTEGER,
    width_status TEXT NOT NULL,
    width_location TEXT NOT NULL,
    geometry_status TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    owner_id_fold TEXT NOT NULL UNIQUE,
    source_record_sha256 TEXT NOT NULL,
    min_x_pm INTEGER NOT NULL,
    min_y_pm INTEGER NOT NULL,
    max_x_pm INTEGER NOT NULL,
    max_y_pm INTEGER NOT NULL,
    UNIQUE (net_fold, trace_id_fold)
) WITHOUT ROWID;
CREATE TABLE vias (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    via_id TEXT NOT NULL,
    via_id_fold TEXT NOT NULL,
    net_name TEXT NOT NULL,
    net_fold TEXT NOT NULL,
    start_layer_id TEXT NOT NULL,
    start_layer_id_fold TEXT NOT NULL,
    end_layer_id TEXT NOT NULL,
    end_layer_id_fold TEXT NOT NULL,
    start_node_id TEXT NOT NULL,
    start_node_id_fold TEXT NOT NULL,
    end_node_id TEXT NOT NULL,
    end_node_id_fold TEXT NOT NULL,
    padstack_id TEXT NOT NULL,
    padstack_id_fold TEXT NOT NULL,
    status TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    owner_id_fold TEXT NOT NULL UNIQUE,
    start_x_pm INTEGER NOT NULL,
    start_y_pm INTEGER NOT NULL,
    end_x_pm INTEGER NOT NULL,
    end_y_pm INTEGER NOT NULL,
    rotation_microdegrees INTEGER NOT NULL,
    source_record_sha256 TEXT NOT NULL,
    UNIQUE (via_id_fold)
) WITHOUT ROWID;
CREATE TABLE section_ledger (
    section_name TEXT NOT NULL PRIMARY KEY,
    row_count INTEGER NOT NULL,
    logical_sha256 TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX pad_shapes_layer_idx
ON pad_shapes(layer_id_fold, padstack_id_fold, ordinal);
CREATE INDEX surfaces_net_layer_idx
ON surfaces(net_fold, layer_id_fold, ordinal);
CREATE INDEX nodes_net_layer_idx
ON nodes(net_fold, layer_id_fold, ordinal);
CREATE INDEX traces_net_layer_idx
ON traces(net_fold, layer_id_fold, ordinal);
CREATE INDEX vias_net_idx ON vias(net_fold, ordinal);
"""

_ROW_TYPES: Final = {
    "source_coverage": RawSpatialSourceCoverageRow,
    "section_coverage": RawSpatialSectionCoverageRow,
    "layers": RawSpatialLayerRow,
    "padstacks": RawSpatialPadstackRow,
    "pad_shapes": RawSpatialPadShapeRow,
    "surfaces": RawSpatialSurfaceRow,
    "nodes": RawSpatialNodeRow,
    "traces": RawSpatialTraceRow,
    "vias": RawSpatialViaRow,
}


def _db_values(section: str, row: Any) -> tuple[Any, ...]:
    values = tuple(getattr(row, field.name) for field in fields(row))
    if section in {"source_coverage", "section_coverage"}:
        return values
    if section == "layers":
        return (
            values[0],
            values[1],
            values[1].casefold(),
            1 if values[2] else 0,
            values[3],
        )
    if section == "padstacks":
        return (values[0], values[1], values[1].casefold(), *values[2:])
    if section == "pad_shapes":
        return (
            values[0], values[1], values[1].casefold(), values[2],
            values[2].casefold(), *values[3:]
        )
    if section == "surfaces":
        return (
            values[0], values[1], values[1].casefold(), values[2],
            values[2].casefold(), values[3], values[3].casefold(), *values[4:]
        )
    if section == "nodes":
        net_name = values[2]
        padstack = values[7]
        return (
            values[0], values[1], values[1].casefold(), net_name,
            None if net_name is None else net_name.casefold(), values[3],
            values[4], values[4].casefold(), values[5], values[6], padstack,
            None if padstack is None else padstack.casefold(), values[8], values[9]
        )
    if section == "traces":
        owner_index = 10
        return (
            values[0], values[1], values[1].casefold(), values[2],
            values[2].casefold(), values[3], values[3].casefold(), values[4],
            values[4].casefold(), values[5], values[5].casefold(),
            *values[6 : owner_index + 1], values[owner_index].casefold(),
            *values[owner_index + 1 :]
        )
    owner_index = 9
    return (
        values[0], values[1], values[1].casefold(), values[2],
        values[2].casefold(), values[3], values[3].casefold(), values[4],
        values[4].casefold(), values[5], values[5].casefold(), values[6],
        values[6].casefold(), values[7], values[7].casefold(), values[8],
        values[9], values[owner_index].casefold(),
        *values[owner_index + 1 :]
    )


_INSERT_SQL: Final = {
    "source_coverage": "INSERT INTO source_coverage VALUES(?,?,?,?,?,?)",
    "section_coverage": "INSERT INTO section_coverage VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
    "layers": "INSERT INTO layers VALUES(?,?,?,?,?)",
    "padstacks": "INSERT INTO padstacks VALUES(?,?,?,?,?,?)",
    "pad_shapes": "INSERT INTO pad_shapes VALUES(?,?,?,?,?,?,?,?,?)",
    "surfaces": "INSERT INTO surfaces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    "nodes": "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    "traces": "INSERT INTO traces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
    "vias": "INSERT INTO vias VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
}


def _canonical_row_bytes(values: tuple[Any, ...]) -> bytes:
    return json.dumps(
        values,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _section_digest(
    connection: sqlite3.Connection,
    section: str,
    *,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> tuple[int, str]:
    digest = sha256()
    count = 0
    for values in connection.execute(f"SELECT * FROM {section} ORDER BY ordinal"):
        if count % _BATCH_ROWS == 0 and is_cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial hashing was cancelled")
        digest.update(_canonical_row_bytes(tuple(values)))
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


def _logical_digest(connection: sqlite3.Connection) -> str:
    digest = sha256()
    for section, count, section_sha in connection.execute(
        "SELECT section_name,row_count,logical_sha256 "
        "FROM section_ledger ORDER BY section_name"
    ):
        digest.update(_canonical_row_bytes((section, count, section_sha)))
        digest.update(b"\n")
    for key, value in connection.execute(
        "SELECT key,value FROM meta WHERE key <> 'logical_rows_sha256' ORDER BY key"
    ):
        digest.update(_canonical_row_bytes((key, value)))
        digest.update(b"\n")
    return digest.hexdigest()


def _geometry_digest(connection: sqlite3.Connection) -> str:
    """Derive geometry identity from exact layer/surface/pad evidence ledgers."""

    digest = sha256(RAW_SPATIAL_CONTACT_COMPILER_ID.encode("ascii"))
    for section in _GEOMETRY_SECTIONS:
        row = connection.execute(
            "SELECT row_count,logical_sha256 FROM section_ledger "
            "WHERE section_name=?",
            (section,),
        ).fetchone()
        if row is None:
            _fail("RAW_SPATIAL_LEDGER_INVALID", "geometry ledger is incomplete")
        digest.update(_canonical_row_bytes((section, row[0], row[1])))
        digest.update(b"\n")
    return digest.hexdigest()


def _insert_rows(
    connection: sqlite3.Connection,
    section: str,
    rows: Iterable[Any],
    *,
    batch_rows: int,
    is_cancelled: Callable[[], bool],
) -> int:
    if type(batch_rows) is not int or batch_rows < 1 or batch_rows > _BATCH_ROWS:
        _fail("RAW_SPATIAL_BOUND_INVALID", "batch_rows is outside its bound")
    expected_type = _ROW_TYPES[section]
    expected_ordinal = 0
    batch: list[tuple[Any, ...]] = []
    try:
        iterator = iter(rows)
    except TypeError as exc:
        _fail("RAW_SPATIAL_ROW_INVALID", f"{section} is not iterable: {exc}")
    for row in iterator:
        if is_cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial asset build was cancelled")
        if type(row) is not expected_type:
            _fail("RAW_SPATIAL_ROW_INVALID", f"{section} requires exact typed rows")
        if row.ordinal != expected_ordinal:
            _fail("RAW_SPATIAL_ORDINAL_INVALID", f"{section} ordinals must be contiguous")
        if expected_ordinal >= MAX_RAW_SPATIAL_ROWS_PER_SECTION:
            _fail("RAW_SPATIAL_BOUND_EXCEEDED", f"{section} exceeds its row bound")
        batch.append(_db_values(section, row))
        expected_ordinal += 1
        if len(batch) >= batch_rows:
            connection.executemany(_INSERT_SQL[section], batch)
            batch.clear()
    if batch:
        connection.executemany(_INSERT_SQL[section], batch)
    return expected_ordinal


def _validate_relations(connection: sqlite3.Connection) -> None:
    checks = (
        ("surfaces", "layer_id_fold", "layers", "layer_id_fold"),
        ("nodes", "layer_id_fold", "layers", "layer_id_fold"),
        ("traces", "layer_id_fold", "layers", "layer_id_fold"),
        ("vias", "start_layer_id_fold", "layers", "layer_id_fold"),
        ("vias", "end_layer_id_fold", "layers", "layer_id_fold"),
        ("pad_shapes", "layer_id_fold", "layers", "layer_id_fold"),
        ("pad_shapes", "padstack_id_fold", "padstacks", "padstack_id_fold"),
        ("vias", "padstack_id_fold", "padstacks", "padstack_id_fold"),
    )
    for source, column, target, target_column in checks:
        missing = connection.execute(
            f"SELECT 1 FROM {source} s LEFT JOIN {target} t "
            f"ON s.{column}=t.{target_column} WHERE t.{target_column} IS NULL LIMIT 1"
        ).fetchone()
        if missing is not None:
            _fail("RAW_SPATIAL_REFERENCE_INVALID", f"{source}.{column} is unresolved")
    missing_node_padstack = connection.execute(
        "SELECT 1 FROM nodes n LEFT JOIN padstacks p "
        "ON n.padstack_id_fold=p.padstack_id_fold "
        "WHERE n.padstack_id_fold IS NOT NULL AND p.padstack_id_fold IS NULL LIMIT 1"
    ).fetchone()
    if missing_node_padstack is not None:
        _fail("RAW_SPATIAL_REFERENCE_INVALID", "node padstack is unresolved")
    for section in ("traces", "vias"):
        for endpoint in ("start", "end"):
            missing = connection.execute(
                f"SELECT 1 FROM {section} s LEFT JOIN nodes n "
                f"ON n.net_fold IS NOT NULL AND s.net_fold=n.net_fold "
                f"AND s.{endpoint}_node_id_fold=n.node_id_fold "
                "WHERE n.node_id_fold IS NULL LIMIT 1"
            ).fetchone()
            if missing is not None:
                _fail(
                    "RAW_SPATIAL_REFERENCE_INVALID",
                    f"{section} {endpoint} composite node is unresolved",
                )
    trace_mismatch = connection.execute(
        "SELECT 1 FROM traces t JOIN nodes a ON a.net_fold=t.net_fold "
        "AND a.node_id_fold=t.start_node_id_fold JOIN nodes b "
        "ON b.net_fold=t.net_fold AND b.node_id_fold=t.end_node_id_fold "
        "WHERE a.layer_id_fold<>t.layer_id_fold OR b.layer_id_fold<>t.layer_id_fold "
        "OR a.x_pm<t.min_x_pm OR a.x_pm>t.max_x_pm "
        "OR a.y_pm<t.min_y_pm OR a.y_pm>t.max_y_pm OR b.x_pm<t.min_x_pm "
        "OR b.x_pm>t.max_x_pm OR b.y_pm<t.min_y_pm OR b.y_pm>t.max_y_pm LIMIT 1"
    ).fetchone()
    if trace_mismatch is not None:
        _fail("RAW_SPATIAL_GEOMETRY_INVALID", "trace endpoints disagree with its net/layer/bbox")
    via_mismatch = connection.execute(
        "SELECT 1 FROM vias v JOIN nodes a ON a.net_fold=v.net_fold "
        "AND a.node_id_fold=v.start_node_id_fold JOIN nodes b "
        "ON b.net_fold=v.net_fold AND b.node_id_fold=v.end_node_id_fold "
        "WHERE a.layer_id_fold<>v.start_layer_id_fold "
        "OR b.layer_id_fold<>v.end_layer_id_fold "
        "OR a.x_pm<>v.start_x_pm OR a.y_pm<>v.start_y_pm "
        "OR b.x_pm<>v.end_x_pm OR b.y_pm<>v.end_y_pm LIMIT 1"
    ).fetchone()
    if via_mismatch is not None:
        _fail("RAW_SPATIAL_VIA_INVALID", "via endpoints disagree with its exact location")
    unordered_exact_via = connection.execute(
        "SELECT 1 FROM vias v JOIN layers a "
        "ON a.layer_id_fold=v.start_layer_id_fold JOIN layers b "
        "ON b.layer_id_fold=v.end_layer_id_fold "
        "WHERE v.status='EXACT' AND a.ordinal>=b.ordinal LIMIT 1"
    ).fetchone()
    if unordered_exact_via is not None:
        _fail(
            "RAW_SPATIAL_VIA_INVALID",
            "exact via endpoints must follow the physical stackup order",
        )
    nonconductor_exact_via = connection.execute(
        "SELECT 1 FROM vias v JOIN layers a "
        "ON a.layer_id_fold=v.start_layer_id_fold JOIN layers b "
        "ON b.layer_id_fold=v.end_layer_id_fold "
        "WHERE v.status='EXACT' AND (a.is_conductor<>1 OR b.is_conductor<>1) LIMIT 1"
    ).fetchone()
    if nonconductor_exact_via is not None:
        _fail(
            "RAW_SPATIAL_VIA_INVALID",
            "exact via endpoints must reference conductor layers",
        )
    undrilled_exact_via = connection.execute(
        "SELECT 1 FROM vias v JOIN padstacks p "
        "ON p.padstack_id_fold=v.padstack_id_fold "
        "WHERE v.status='EXACT' "
        "AND (p.drill_diameter_pm IS NULL OR p.drill_diameter_pm<=0) LIMIT 1"
    ).fetchone()
    if undrilled_exact_via is not None:
        _fail(
            "RAW_SPATIAL_VIA_INVALID",
            "exact via padstack must carry a positive drill diameter",
        )
    invalid_surface_scope = connection.execute(
        "SELECT 1 FROM vias v LEFT JOIN surfaces a "
        "ON a.net_fold=v.net_fold AND a.layer_id_fold=v.start_layer_id_fold "
        "LEFT JOIN surfaces b ON b.net_fold=v.net_fold "
        "AND b.layer_id_fold=v.end_layer_id_fold "
        "WHERE (v.status IN ('EXACT','UNRESOLVED') "
        "AND a.ordinal IS NULL AND b.ordinal IS NULL) "
        "OR (v.status='OUT_OF_SCOPE' "
        "AND (a.ordinal IS NOT NULL OR b.ordinal IS NOT NULL)) LIMIT 1"
    ).fetchone()
    if invalid_surface_scope is not None:
        _fail(
            "RAW_SPATIAL_VIA_INVALID",
            "via status disagrees with retained endpoint surface scope",
        )
    missing_shape = connection.execute(
        "SELECT 1 FROM vias v LEFT JOIN pad_shapes a "
        "ON a.padstack_id_fold=v.padstack_id_fold "
        "AND a.layer_id_fold=v.start_layer_id_fold LEFT JOIN pad_shapes b "
        "ON b.padstack_id_fold=v.padstack_id_fold "
        "AND b.layer_id_fold=v.end_layer_id_fold "
        "WHERE v.status='EXACT' AND (a.ordinal IS NULL OR b.ordinal IS NULL) LIMIT 1"
    ).fetchone()
    if missing_shape is not None:
        _fail("RAW_SPATIAL_PAD_SHAPE_INVALID", "exact via endpoint shape is unresolved")
    owner_collision = connection.execute(
        "SELECT 1 FROM traces t JOIN vias v ON t.owner_id_fold=v.owner_id_fold LIMIT 1"
    ).fetchone()
    if owner_collision is not None:
        _fail("RAW_SPATIAL_OWNER_INVALID", "an owner occurs more than once")


def _validate_coverage(connection: sqlite3.Connection) -> None:
    source = connection.execute(
        "SELECT source_size_bytes,raw_header_count,logical_record_count "
        "FROM source_coverage"
    ).fetchone()
    if source is None:
        _fail("RAW_SPATIAL_COVERAGE_INVALID", "source coverage row is absent")
    coverage = {
        name: values
        for name, *values in connection.execute(
            "SELECT section_name,raw_header_count,logical_record_count,parsed_count,"
            "resolved_count,unresolved_count,retained_count,out_of_scope_count "
            "FROM section_coverage"
        )
    }
    if frozenset(coverage) != {"Node", "Trace", "Via"}:
        _fail("RAW_SPATIAL_COVERAGE_INVALID", "coverage section inventory differs")
    if source[1:] != (
        sum(values[0] for values in coverage.values()),
        sum(values[1] for values in coverage.values()),
    ):
        _fail("RAW_SPATIAL_COVERAGE_INVALID", "source coverage totals differ")
    row_counts = {
        "Node": connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
        "Trace": connection.execute("SELECT COUNT(*) FROM traces").fetchone()[0],
        "Via": connection.execute("SELECT COUNT(*) FROM vias").fetchone()[0],
    }
    observed = {
        "Node": connection.execute(
            "SELECT COALESCE(SUM(CASE WHEN net_status IN "
            "('EXPLICIT','INCIDENCE') THEN 1 ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN net_status='OUT_OF_SCOPE' THEN 1 ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN net_status IN "
            "('EXPLICIT','INCIDENCE') THEN 1 ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN net_status='OUT_OF_SCOPE' THEN 1 ELSE 0 END),0) "
            "FROM nodes"
        ).fetchone(),
        "Trace": connection.execute(
            "SELECT COALESCE(SUM(CASE WHEN geometry_status='EXACT' THEN 1 ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN geometry_status<>'EXACT' THEN 1 ELSE 0 END),0),"
            "COUNT(*),0 "
            "FROM traces"
        ).fetchone(),
        "Via": connection.execute(
            "SELECT COALESCE(SUM(CASE WHEN status='EXACT' THEN 1 ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN status<>'EXACT' THEN 1 ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN status<>'OUT_OF_SCOPE' THEN 1 ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN status='OUT_OF_SCOPE' THEN 1 ELSE 0 END),0) "
            "FROM vias"
        ).fetchone(),
    }
    for name, count in row_counts.items():
        values = coverage[name]
        if values[2] != count or tuple(values[3:]) != tuple(observed[name]):
            _fail("RAW_SPATIAL_COVERAGE_INVALID", f"{name} row disposition differs")
    intervals = tuple(
        connection.execute(
            "SELECT byte_start,byte_end FROM section_coverage ORDER BY byte_start"
        )
    )
    if any(end > source[0] for _start, end in intervals) or any(
        right[0] < left[1] for left, right in zip(intervals, intervals[1:])
    ):
        _fail("RAW_SPATIAL_COVERAGE_INVALID", "section byte intervals overlap or escape source")


def _verify_source_file(
    connection: sqlite3.Connection,
    source_path: Path,
    *,
    is_cancelled: Callable[[], bool],
) -> None:
    coverage = connection.execute(
        "SELECT source_basename,source_size_bytes,source_sha256 FROM source_coverage"
    ).fetchone()
    if not source_path.is_file() or source_path.name != coverage[0]:
        _fail("RAW_SPATIAL_COVERAGE_INVALID", "source file or basename differs")
    if source_path.stat().st_size != coverage[1]:
        _fail("RAW_SPATIAL_COVERAGE_INVALID", "source file size differs")
    digest = sha256()
    with source_path.open("rb") as stream:
        while chunk := stream.read(_STREAM_BYTES):
            if is_cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "source coverage verification was cancelled")
            digest.update(chunk)
    if digest.hexdigest() != coverage[2]:
        _fail("RAW_SPATIAL_COVERAGE_INVALID", "source file SHA-256 differs")
    with source_path.open("rb") as stream:
        for start, size, expected in connection.execute(
            "SELECT byte_start,byte_size,section_sha256 "
            "FROM section_coverage ORDER BY ordinal"
        ):
            stream.seek(start)
            remaining = size
            section_digest = sha256()
            while remaining:
                if is_cancelled():
                    _fail(
                        "RAW_SPATIAL_CANCELLED",
                        "source section verification was cancelled",
                    )
                chunk = stream.read(min(_STREAM_BYTES, remaining))
                if not chunk:
                    _fail("RAW_SPATIAL_COVERAGE_INVALID", "source section is truncated")
                section_digest.update(chunk)
                remaining -= len(chunk)
            if section_digest.hexdigest() != expected:
                _fail("RAW_SPATIAL_COVERAGE_INVALID", "source section SHA-256 differs")


def _compress_file(
    path: Path, *, is_cancelled: Callable[[], bool]
) -> tuple[bytes, int, str]:
    if is_cancelled():
        _fail("RAW_SPATIAL_CANCELLED", "raw spatial compression was cancelled")
    compressor = zlib.compressobj(level=6)
    digest = sha256()
    size = 0
    compressed_size = 0

    def write_compressed(stream: Any, content: bytes) -> None:
        nonlocal compressed_size
        compressed_size += len(content)
        if compressed_size > MAX_RAW_SPATIAL_COMPRESSED_BYTES:
            _fail(
                "RAW_SPATIAL_COMPRESSED_TOO_LARGE",
                "raw spatial attachment exceeds its bound",
            )
        if content:
            stream.write(content)

    try:
        # NamedTemporaryFile is created exclusively and deleted when its context
        # closes on success, cancellation, a bound failure, or an I/O failure.
        # Spooling beside the SQLite source avoids a bytearray-to-bytes copy of
        # as much as one GiB while preserving the exact zlib byte stream.
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{path.name}.",
            suffix=".zlib",
            dir=path.parent,
        ) as compressed_stream:
            with path.open("rb") as source_stream:
                while chunk := source_stream.read(_STREAM_BYTES):
                    if is_cancelled():
                        _fail(
                            "RAW_SPATIAL_CANCELLED",
                            "raw spatial compression was cancelled",
                        )
                    size += len(chunk)
                    if size > MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES:
                        _fail(
                            "RAW_SPATIAL_UNCOMPRESSED_TOO_LARGE",
                            "raw spatial SQLite exceeds its bound",
                        )
                    digest.update(chunk)
                    write_compressed(compressed_stream, compressor.compress(chunk))
            if is_cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compression was cancelled")
            write_compressed(compressed_stream, compressor.flush())
            if is_cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compression was cancelled")
            compressed_stream.flush()
            compressed_stream.seek(0)
            compressed = compressed_stream.read()
            if is_cancelled():
                _fail("RAW_SPATIAL_CANCELLED", "raw spatial compression was cancelled")
    except OSError as exc:
        _fail(
            "RAW_SPATIAL_COMPRESSION_IO_FAILED",
            f"raw spatial compression I/O failed: {exc}",
        )
    if len(compressed) != compressed_size:
        _fail(
            "RAW_SPATIAL_COMPRESSION_IO_FAILED",
            "raw spatial compressed spool size differs",
        )
    if size > _EXPANSION_GRACE_BYTES and (
        size > max(1, compressed_size) * MAX_RAW_SPATIAL_EXPANSION_RATIO
    ):
        _fail("RAW_SPATIAL_EXPANSION_RATIO_EXCEEDED", "compression ratio is unsafe")
    return compressed, size, digest.hexdigest()


def _enforce_uncompressed_database_size(
    path: Path, *, is_cancelled: Callable[[], bool]
) -> None:
    if is_cancelled():
        _fail("RAW_SPATIAL_CANCELLED", "raw spatial asset build was cancelled")
    try:
        size = path.stat().st_size
    except OSError as exc:
        _fail("RAW_SPATIAL_DATABASE_INVALID", str(exc))
    if size > MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES:
        _fail("RAW_SPATIAL_UNCOMPRESSED_TOO_LARGE", "raw spatial SQLite exceeds its bound")


def _fail_build_database_error(
    exc: sqlite3.Error, *, is_cancelled: Callable[[], bool]
) -> None:
    message = str(exc)
    folded = message.casefold()
    error_code = getattr(exc, "sqlite_errorcode", None)
    if (
        is_cancelled()
        or error_code == sqlite3.SQLITE_INTERRUPT
        or "interrupted" in folded
    ):
        _fail("RAW_SPATIAL_CANCELLED", "raw spatial asset build was cancelled")
    if error_code == sqlite3.SQLITE_FULL or any(
        phrase in folded
        for phrase in ("database or disk is full", "database is full", "disk full")
    ):
        _fail("RAW_SPATIAL_UNCOMPRESSED_TOO_LARGE", "raw spatial SQLite exceeds its bound")
    if isinstance(exc, sqlite3.IntegrityError):
        _fail("RAW_SPATIAL_DUPLICATE_INVALID", message)
    _fail("RAW_SPATIAL_DATABASE_INVALID", message)


def build_raw_spatial_contact_asset(
    *,
    source_path: str | Path,
    source_sha256: str,
    project_binding_sha256: str,
    certificate_evidence_sha256: str,
    compiled_topology_identity_sha256: str,
    source_coverage: RawSpatialSourceCoverageRow,
    section_coverage: Iterable[RawSpatialSectionCoverageRow],
    layers: Iterable[RawSpatialLayerRow],
    padstacks: Iterable[RawSpatialPadstackRow],
    pad_shapes: Iterable[RawSpatialPadShapeRow],
    surfaces: Iterable[RawSpatialSurfaceRow],
    nodes: Iterable[RawSpatialNodeRow],
    traces: Iterable[RawSpatialTraceRow],
    vias: Iterable[RawSpatialViaRow],
    batch_rows: int = _BATCH_ROWS,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any], tuple[str, bytes]]:
    """Build a deterministic, bounded SQLite attachment from streaming rows."""

    cancelled = is_cancelled or (lambda: False)
    identities = {
        "source_sha256": _sha(source_sha256, "source"),
        "project_binding_sha256": _sha(project_binding_sha256, "project binding"),
        "certificate_evidence_sha256": _sha(
            certificate_evidence_sha256, "certificate evidence"
        ),
        "compiled_topology_identity_sha256": _sha(
            compiled_topology_identity_sha256, "compiled topology identity"
        ),
    }
    if type(source_coverage) is not RawSpatialSourceCoverageRow:
        _fail("RAW_SPATIAL_COVERAGE_INVALID", "exact source coverage row is required")
    if source_coverage.source_sha256 != identities["source_sha256"]:
        _fail("RAW_SPATIAL_COVERAGE_INVALID", "coverage source identity differs")
    source_file = Path(source_path)
    if cancelled():
        _fail("RAW_SPATIAL_CANCELLED", "raw spatial asset build was cancelled")
    streams = {
        "source_coverage": (source_coverage,),
        "section_coverage": section_coverage,
        "layers": layers,
        "padstacks": padstacks,
        "pad_shapes": pad_shapes,
        "surfaces": surfaces,
        "nodes": nodes,
        "traces": traces,
        "vias": vias,
    }
    with tempfile.TemporaryDirectory(prefix="spdpi-raw-spatial-") as temp_dir:
        database_path = Path(temp_dir) / "raw-spatial.sqlite"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(database_path)
            connection.execute(f"PRAGMA page_size={_SQLITE_PAGE_SIZE}")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            if page_size != _SQLITE_PAGE_SIZE:
                _fail("RAW_SPATIAL_DATABASE_INVALID", "database page size differs")
            max_page_count = max(1, MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES // page_size)
            applied_page_count = connection.execute(
                f"PRAGMA max_page_count={max_page_count}"
            ).fetchone()[0]
            if applied_page_count > max_page_count:
                _fail(
                    "RAW_SPATIAL_UNCOMPRESSED_TOO_LARGE",
                    "raw spatial SQLite page bound could not be applied",
                )
            connection.set_progress_handler(
                lambda: 1 if cancelled() else 0, _SQLITE_PROGRESS_STEPS
            )
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={_USER_VERSION}")
            connection.executescript(_CREATE_SQL)
            counts = {
                section: _insert_rows(
                    connection,
                    section,
                    streams[section],
                    batch_rows=batch_rows,
                    is_cancelled=cancelled,
                )
                for section in _SECTIONS
            }
            _validate_relations(connection)
            _validate_coverage(connection)
            _verify_source_file(connection, source_file, is_cancelled=cancelled)
            ledgers: dict[str, tuple[int, str]] = {}
            for section in _SECTIONS:
                ledgers[section] = _section_digest(
                    connection, section, is_cancelled=cancelled
                )
                connection.execute(
                    "INSERT INTO section_ledger VALUES(?,?,?)",
                    (section, *ledgers[section]),
                )
            geometry_identity = _geometry_digest(connection)
            meta = {
                "payload_schema": RAW_SPATIAL_CONTACT_PAYLOAD_SCHEMA,
                "compiler_id": RAW_SPATIAL_CONTACT_COMPILER_ID,
                **identities,
                "geometry_identity_sha256": geometry_identity,
                **{f"{key}_count": str(value) for key, value in counts.items()},
            }
            connection.executemany(
                "INSERT INTO meta(key,value) VALUES(?,?)", sorted(meta.items())
            )
            logical = _logical_digest(connection)
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('logical_rows_sha256',?)",
                (logical,),
            )
            connection.commit()
        except sqlite3.Error as exc:
            _fail_build_database_error(exc, is_cancelled=cancelled)
        finally:
            if connection is not None:
                connection.set_progress_handler(None, 0)
                connection.close()
        _enforce_uncompressed_database_size(database_path, is_cancelled=cancelled)
        compressed, uncompressed_size, uncompressed_sha = _compress_file(
            database_path, is_cancelled=cancelled
        )
    asset_name = (
        f"{RAW_SPATIAL_CONTACT_ASSET_PREFIX}raw-spatial-contact-v2-"
        f"{identities['source_sha256'][:16]}.sqlite.zlib"
    )
    manifest: dict[str, Any] = {
        "storage_schema": RAW_SPATIAL_CONTACT_ASSET_SCHEMA,
        "payload_schema": RAW_SPATIAL_CONTACT_PAYLOAD_SCHEMA,
        "compiler_id": RAW_SPATIAL_CONTACT_COMPILER_ID,
        **identities,
        "geometry_identity_sha256": geometry_identity,
        "logical_rows_sha256": logical,
        "asset_name": asset_name,
        "compression": RAW_SPATIAL_CONTACT_COMPRESSION,
        "compressed_size_bytes": len(compressed),
        "compressed_sha256": sha256(compressed).hexdigest(),
        "uncompressed_size_bytes": uncompressed_size,
        "uncompressed_sha256": uncompressed_sha,
        "counts": dict(counts),
    }
    return manifest, (asset_name, compressed)


def _validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping) or frozenset(manifest) != _MANIFEST_KEYS:
        _fail("RAW_SPATIAL_MANIFEST_INVALID", "manifest keys differ from the v2 contract")
    if manifest["storage_schema"] != RAW_SPATIAL_CONTACT_ASSET_SCHEMA:
        _fail("RAW_SPATIAL_MANIFEST_INVALID", "storage schema differs")
    if manifest["payload_schema"] != RAW_SPATIAL_CONTACT_PAYLOAD_SCHEMA:
        _fail("RAW_SPATIAL_MANIFEST_INVALID", "payload schema differs")
    if manifest["compiler_id"] != RAW_SPATIAL_CONTACT_COMPILER_ID:
        _fail("RAW_SPATIAL_MANIFEST_INVALID", "compiler identity differs")
    if manifest["compression"] != RAW_SPATIAL_CONTACT_COMPRESSION:
        _fail("RAW_SPATIAL_MANIFEST_INVALID", "compression differs")
    validated = dict(manifest)
    for key in (
        "source_sha256",
        "project_binding_sha256",
        "certificate_evidence_sha256",
        "compiled_topology_identity_sha256",
        "geometry_identity_sha256",
        "logical_rows_sha256",
        "compressed_sha256",
        "uncompressed_sha256",
    ):
        validated[key] = _sha(manifest[key], key)
    name = manifest["asset_name"]
    expected_name = (
        f"{RAW_SPATIAL_CONTACT_ASSET_PREFIX}raw-spatial-contact-v2-"
        f"{validated['source_sha256'][:16]}.sqlite.zlib"
    )
    if (
        type(name) is not str
        or name != expected_name
    ):
        _fail("RAW_SPATIAL_MANIFEST_INVALID", "asset_name is not source-derived")
    bounds = {
        "compressed_size_bytes": MAX_RAW_SPATIAL_COMPRESSED_BYTES,
        "uncompressed_size_bytes": MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES,
    }
    for key, maximum in bounds.items():
        value = manifest[key]
        if type(value) is not int or value < 1 or value > maximum:
            _fail("RAW_SPATIAL_BOUND_EXCEEDED", f"{key} exceeds its bound")
    counts = manifest["counts"]
    if not isinstance(counts, Mapping) or frozenset(counts) != frozenset(_SECTIONS):
        _fail("RAW_SPATIAL_MANIFEST_INVALID", "manifest counts differ")
    for section, count in counts.items():
        if type(count) is not int or count < 0 or count > MAX_RAW_SPATIAL_ROWS_PER_SECTION:
            _fail("RAW_SPATIAL_BOUND_EXCEEDED", f"{section} count exceeds its bound")
    if manifest["uncompressed_size_bytes"] > _EXPANSION_GRACE_BYTES and (
        manifest["uncompressed_size_bytes"]
        > manifest["compressed_size_bytes"] * MAX_RAW_SPATIAL_EXPANSION_RATIO
    ):
        _fail("RAW_SPATIAL_EXPANSION_RATIO_EXCEEDED", "declared expansion is unsafe")
    return validated


def _project_spd_import(project: Any) -> Mapping[str, Any]:
    if isinstance(project, Mapping):
        metadata = project.get("metadata", {})
    else:
        metadata = getattr(project, "metadata", {})
    if not isinstance(metadata, Mapping):
        _fail(
            "RAW_SPATIAL_PROJECT_METADATA_INVALID",
            "project metadata is not a mapping",
        )
    spd_import = metadata.get("spd_import", {})
    if "spd_import" in metadata and not isinstance(spd_import, Mapping):
        _fail(
            "RAW_SPATIAL_PROJECT_METADATA_INVALID",
            "project spd_import metadata is not a mapping",
        )
    reserved_by_fold = {
        key.casefold(): key for key in _RESERVED_SPD_IMPORT_METADATA_KEYS
    }
    observed_reserved: set[str] = set()
    for raw_key in spd_import:
        if not isinstance(raw_key, str):
            continue
        folded = raw_key.casefold()
        canonical = reserved_by_fold.get(folded)
        if canonical is None:
            continue
        if raw_key != canonical or folded in observed_reserved:
            _fail(
                "RAW_SPATIAL_PROJECT_METADATA_INVALID",
                f"reserved spd_import metadata key {raw_key!r} is non-canonical or duplicated",
            )
        observed_reserved.add(folded)
    return spd_import


def _validated_compiled_topology_binding(
    spd_import: Mapping[str, Any],
) -> dict[str, Any]:
    if _COMPILED_TOPOLOGY_ASSET_METADATA_KEY not in spd_import:
        _fail(
            "RAW_SPATIAL_COMPILED_TOPOLOGY_REQUIRED",
            "raw spatial metadata requires the bound compiled topology metadata",
        )
    manifest = spd_import[_COMPILED_TOPOLOGY_ASSET_METADATA_KEY]
    if (
        not isinstance(manifest, Mapping)
        or frozenset(manifest) != _COMPILED_TOPOLOGY_MANIFEST_KEYS
    ):
        _fail(
            "RAW_SPATIAL_COMPILED_TOPOLOGY_INVALID",
            "compiled topology metadata has missing or extra fields",
        )
    if (
        manifest.get("storage_schema") != _COMPILED_TOPOLOGY_ASSET_SCHEMA
        or manifest.get("payload_schema") != _COMPILED_TOPOLOGY_PAYLOAD_SCHEMA
        or manifest.get("compiler_id") != _COMPILED_TOPOLOGY_COMPILER_ID
        or manifest.get("surface_schema_version")
        != _COMPILED_TOPOLOGY_SURFACE_SCHEMA
        or manifest.get("surface_compiler_id")
        != _COMPILED_TOPOLOGY_SURFACE_COMPILER
        or manifest.get("compression") != RAW_SPATIAL_CONTACT_COMPRESSION
    ):
        _fail(
            "RAW_SPATIAL_COMPILED_TOPOLOGY_INVALID",
            "compiled topology metadata schema/compiler is unsupported",
        )
    validated = dict(manifest)
    for key in (
        "source_sha256",
        "certificate_evidence_sha256",
        "surface_asset_uncompressed_sha256",
        "project_binding_sha256",
        "topology_identity_sha256",
        "logical_rows_sha256",
        "compressed_sha256",
        "uncompressed_sha256",
    ):
        try:
            validated[key] = _sha(manifest[key], f"compiled topology {key}")
        except RawSpatialContactAssetError as exc:
            _fail("RAW_SPATIAL_COMPILED_TOPOLOGY_INVALID", str(exc))
    size_bounds = {
        "surface_asset_uncompressed_size_bytes": (
            _MAX_COMPILED_SURFACE_UNCOMPRESSED_BYTES
        ),
        "compressed_size_bytes": _MAX_COMPILED_TOPOLOGY_COMPRESSED_BYTES,
        "uncompressed_size_bytes": _MAX_COMPILED_TOPOLOGY_UNCOMPRESSED_BYTES,
    }
    for key, maximum in size_bounds.items():
        value = manifest[key]
        if type(value) is not int or value < 0 or value > maximum:
            _fail(
                "RAW_SPATIAL_COMPILED_TOPOLOGY_INVALID",
                f"compiled topology {key} exceeds its bound",
            )
    expected_name = (
        "topology/layerwise-compiled-topology-v1-"
        f"{validated['certificate_evidence_sha256'][:16]}.sqlite.zlib"
    )
    if manifest.get("asset_name") != expected_name:
        _fail(
            "RAW_SPATIAL_COMPILED_TOPOLOGY_INVALID",
            "compiled topology asset name is not canonical",
        )
    if manifest["uncompressed_size_bytes"] > _EXPANSION_GRACE_BYTES and (
        manifest["uncompressed_size_bytes"]
        > max(1, manifest["compressed_size_bytes"])
        * _MAX_COMPILED_TOPOLOGY_EXPANSION_RATIO
    ):
        _fail(
            "RAW_SPATIAL_COMPILED_TOPOLOGY_INVALID",
            "compiled topology declared expansion is unsafe",
        )
    return validated


def validate_project_raw_spatial_contact_asset_envelope(
    project: Any,
    attachments: Mapping[str, bytes],
) -> dict[str, Any] | None:
    """Validate optional raw spatial persistence without decompressing it.

    The archive boundary owns the reserved ``spatial/`` namespace.  A raw
    descriptor must name its sole member with exact case, bind byte-for-byte to
    the authenticated project source and sibling compiled-topology identities,
    and attest immutable compressed bytes.  Older projects without the
    descriptor remain valid only when they also carry no reserved spatial
    member.
    """

    if not isinstance(attachments, Mapping):
        _fail(
            "RAW_SPATIAL_ATTACHMENT_INVALID",
            "raw spatial attachments are not a mapping",
        )
    reserved = tuple(
        (name, content)
        for name, content in attachments.items()
        if str(name).casefold().startswith(
            RAW_SPATIAL_CONTACT_ASSET_PREFIX.casefold()
        )
    )
    spd_import = _project_spd_import(project)
    if RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY not in spd_import:
        if reserved:
            _fail(
                "RAW_SPATIAL_ATTACHMENT_ORPHANED",
                "reserved spatial attachments have no metadata descriptor",
            )
        return None
    raw_manifest = spd_import[RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY]
    if not isinstance(raw_manifest, Mapping):
        _fail(
            "RAW_SPATIAL_MANIFEST_INVALID",
            "raw spatial metadata exists but is not a mapping",
        )
    validated = _validate_manifest(raw_manifest)
    compiled = _validated_compiled_topology_binding(spd_import)
    try:
        project_source = _sha(
            spd_import.get("source_sha256"), "project SPD source"
        )
    except RawSpatialContactAssetError as exc:
        _fail("RAW_SPATIAL_PROJECT_SOURCE_INVALID", str(exc))
    if (
        validated["source_sha256"] != project_source
        or compiled["source_sha256"] != project_source
    ):
        _fail(
            "RAW_SPATIAL_BINDING_MISMATCH",
            "raw spatial and compiled topology metadata do not match the project SPD source",
        )
    binding_pairs = (
        ("source_sha256", "source_sha256"),
        ("project_binding_sha256", "project_binding_sha256"),
        ("certificate_evidence_sha256", "certificate_evidence_sha256"),
        ("compiled_topology_identity_sha256", "topology_identity_sha256"),
    )
    for raw_key, compiled_key in binding_pairs:
        if validated[raw_key] != compiled[compiled_key]:
            _fail(
                "RAW_SPATIAL_BINDING_MISMATCH",
                f"raw spatial {raw_key} differs from compiled topology metadata",
            )
    asset_name = validated["asset_name"]
    exact = tuple(
        (name, content)
        for name, content in reserved
        if type(name) is str and name == asset_name
    )
    if not exact:
        _fail(
            "RAW_SPATIAL_ATTACHMENT_INVALID",
            f"raw spatial attachment {asset_name!r} is missing or non-canonical",
        )
    if len(reserved) != 1 or len(exact) != 1:
        _fail(
            "RAW_SPATIAL_ATTACHMENT_ORPHANED",
            "reserved spatial attachments do not exactly match their metadata descriptor",
        )
    content = exact[0][1]
    if type(content) is not bytes:
        _fail(
            "RAW_SPATIAL_ATTACHMENT_INVALID",
            "raw spatial attachment must be immutable bytes",
        )
    if len(content) != validated["compressed_size_bytes"]:
        _fail(
            "RAW_SPATIAL_COMPRESSED_SIZE_MISMATCH",
            "raw spatial compressed size differs from its manifest",
        )
    if sha256(content).hexdigest() != validated["compressed_sha256"]:
        _fail(
            "RAW_SPATIAL_COMPRESSED_INTEGRITY_FAILED",
            "raw spatial compressed SHA-256 differs from its manifest",
        )
    result = dict(validated)
    result["counts"] = dict(validated["counts"])
    return result


def _decompress_to_file(
    compressed: bytes,
    manifest: Mapping[str, Any],
    destination: Path,
    *,
    is_cancelled: Callable[[], bool],
) -> None:
    if type(compressed) is not bytes:
        _fail("RAW_SPATIAL_ATTACHMENT_INVALID", "attachment must be immutable bytes")
    if len(compressed) != manifest["compressed_size_bytes"]:
        _fail("RAW_SPATIAL_COMPRESSED_SIZE_MISMATCH", "compressed size differs")
    if sha256(compressed).hexdigest() != manifest["compressed_sha256"]:
        _fail("RAW_SPATIAL_COMPRESSED_INTEGRITY_FAILED", "compressed SHA-256 differs")
    decompressor = zlib.decompressobj()
    digest = sha256()
    written = 0
    cursor = 0
    try:
        with destination.open("wb") as stream:
            while cursor < len(compressed):
                if is_cancelled():
                    _fail("RAW_SPATIAL_CANCELLED", "raw spatial load was cancelled")
                pending = compressed[cursor : cursor + _STREAM_BYTES]
                cursor += len(pending)
                while pending:
                    if is_cancelled():
                        _fail("RAW_SPATIAL_CANCELLED", "raw spatial load was cancelled")
                    produced = decompressor.decompress(pending, _STREAM_BYTES)
                    if produced:
                        written += len(produced)
                        if written > manifest["uncompressed_size_bytes"]:
                            _fail("RAW_SPATIAL_UNCOMPRESSED_TOO_LARGE", "asset over-expanded")
                        digest.update(produced)
                        stream.write(produced)
                    if decompressor.unused_data:
                        _fail("RAW_SPATIAL_STREAM_INVALID", "compressed stream has trailing data")
                    pending = decompressor.unconsumed_tail
            tail = decompressor.flush()
            written += len(tail)
            if written > manifest["uncompressed_size_bytes"]:
                _fail("RAW_SPATIAL_UNCOMPRESSED_TOO_LARGE", "asset over-expanded")
            digest.update(tail)
            stream.write(tail)
    except zlib.error as exc:
        _fail("RAW_SPATIAL_STREAM_INVALID", str(exc))
    if not decompressor.eof or written != manifest["uncompressed_size_bytes"]:
        _fail("RAW_SPATIAL_UNCOMPRESSED_SIZE_MISMATCH", "expanded size differs")
    if digest.hexdigest() != manifest["uncompressed_sha256"]:
        _fail("RAW_SPATIAL_UNCOMPRESSED_INTEGRITY_FAILED", "expanded SHA-256 differs")


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


@lru_cache(maxsize=1)
def _schema_signature() -> tuple[Any, ...]:
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(_CREATE_SQL)
        objects = tuple(
            reference.execute(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        )
        xinfo = tuple(
            (
                name,
                tuple(tuple(row[1:]) for row in reference.execute(f"PRAGMA table_xinfo({name})")),
            )
            for _kind, name, _sql in objects
        )
        return objects, xinfo
    finally:
        reference.close()


def _validate_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA application_id").fetchone() != (_APPLICATION_ID,):
        _fail("RAW_SPATIAL_DATABASE_INVALID", "database application ID differs")
    if connection.execute("PRAGMA user_version").fetchone() != (_USER_VERSION,):
        _fail("RAW_SPATIAL_DATABASE_INVALID", "database user version differs")
    expected_objects, expected_xinfo = _schema_signature()
    objects = tuple(
        connection.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    )
    if objects != expected_objects:
        _fail("RAW_SPATIAL_DATABASE_SCHEMA_INVALID", "SQLite schema objects differ")
    for table, expected in expected_xinfo:
        actual = tuple(
            tuple(row[1:]) for row in connection.execute(f"PRAGMA table_xinfo({table})")
        )
        if actual != expected:
            _fail("RAW_SPATIAL_DATABASE_SCHEMA_INVALID", f"{table} columns differ")
    if connection.execute("PRAGMA integrity_check(1)").fetchone() != ("ok",):
        _fail("RAW_SPATIAL_DATABASE_INTEGRITY_FAILED", "SQLite integrity check failed")


def _validate_cells(connection: sqlite3.Connection) -> None:
    nullable_columns = {
        ("padstacks", "drill_diameter_pm"),
        ("padstacks", "material"),
        ("nodes", "net_name"),
        ("nodes", "net_fold"),
        ("nodes", "padstack_id"),
        ("nodes", "padstack_id_fold"),
        ("nodes", "rotation_microdegrees"),
        ("traces", "width_pm"),
    }
    for section in _SECTIONS:
        info = tuple(connection.execute(f"PRAGMA table_xinfo({section})"))
        for column in info:
            name, declared = column[1], column[2]
            allowed = (
                (declared.casefold(), "null")
                if (section, name) in nullable_columns
                else (declared.casefold(),)
            )
            invalid = connection.execute(
                f"SELECT 1 FROM {section} WHERE typeof({name}) NOT IN "
                f"({','.join('?' for _ in allowed)}) LIMIT 1",
                allowed,
            ).fetchone()
            if invalid is not None:
                _fail("RAW_SPATIAL_CELL_TYPE_INVALID", f"{section}.{name} type differs")
            if declared == "TEXT":
                oversized = connection.execute(
                    f"SELECT 1 FROM {section} WHERE length(CAST({name} AS BLOB))>? LIMIT 1",
                    (MAX_RAW_SPATIAL_TEXT_BYTES,),
                ).fetchone()
                if oversized is not None:
                    _fail("RAW_SPATIAL_CELL_BOUND_INVALID", f"{section}.{name} is too long")
        for name in _SHA_FIELDS.intersection(column[1] for column in info):
            for (value,) in connection.execute(f"SELECT {name} FROM {section}"):
                _sha(value, f"{section}.{name}")
    invalid_layer_kind = connection.execute(
        "SELECT 1 FROM layers WHERE is_conductor NOT IN (0,1) LIMIT 1"
    ).fetchone()
    if invalid_layer_kind is not None:
        _fail("RAW_SPATIAL_ROW_INVALID", "layers.is_conductor is not boolean")


def _validate_semantic_rows(connection: sqlite3.Connection) -> None:
    """Re-run typed row invariants and verify stored casefold projections."""

    fold_fields = {
        "source_coverage": (),
        "section_coverage": (),
        "layers": ("layer_id",),
        "padstacks": ("padstack_id",),
        "pad_shapes": ("padstack_id", "layer_id"),
        "surfaces": ("surface_id", "net_name", "layer_id"),
        "nodes": ("node_id", "net_name", "layer_id", "padstack_id"),
        "traces": (
            "trace_id",
            "net_name",
            "layer_id",
            "start_node_id",
            "end_node_id",
            "owner_id",
        ),
        "vias": (
            "via_id",
            "net_name",
            "start_layer_id",
            "end_layer_id",
            "start_node_id",
            "end_node_id",
            "padstack_id",
            "owner_id",
        ),
    }
    fold_column = {
        "net_name": "net_fold",
        "layer_id": "layer_id_fold",
        "start_layer_id": "start_layer_id_fold",
        "end_layer_id": "end_layer_id_fold",
        "padstack_id": "padstack_id_fold",
        "surface_id": "surface_id_fold",
        "node_id": "node_id_fold",
        "trace_id": "trace_id_fold",
        "via_id": "via_id_fold",
        "start_node_id": "start_node_id_fold",
        "end_node_id": "end_node_id_fold",
        "owner_id": "owner_id_fold",
    }
    for section in _SECTIONS:
        row_type = _ROW_TYPES[section]
        field_names = tuple(field.name for field in fields(row_type))
        cursor = connection.execute(f"SELECT * FROM {section} ORDER BY ordinal")
        database_names = tuple(column[0] for column in cursor.description)
        field_indices = tuple(database_names.index(name) for name in field_names)
        while rows := cursor.fetchmany(_BATCH_ROWS):
            for values in rows:
                typed_values = [values[index] for index in field_indices]
                if section == "layers":
                    conductor_index = field_names.index("is_conductor")
                    typed_values[conductor_index] = bool(typed_values[conductor_index])
                row = row_type(*typed_values)
                for field_name in fold_fields[section]:
                    raw = getattr(row, field_name)
                    expected = None if raw is None else raw.casefold()
                    actual = values[database_names.index(fold_column[field_name])]
                    if actual != expected:
                        _fail(
                            "RAW_SPATIAL_CASEFOLD_INVALID",
                            f"{section}.{field_name} casefold projection differs",
                        )


def _validate_database(
    connection: sqlite3.Connection,
    manifest: Mapping[str, Any],
    *,
    is_cancelled: Callable[[], bool],
) -> None:
    _validate_schema(connection)
    _validate_cells(connection)
    _validate_semantic_rows(connection)
    meta_rows = tuple(connection.execute("SELECT key,value FROM meta ORDER BY key"))
    meta = dict(meta_rows)
    if len(meta) != len(meta_rows) or frozenset(meta) != _META_KEYS:
        _fail("RAW_SPATIAL_META_INVALID", "database metadata keys differ")
    expected_meta = {
        "payload_schema": manifest["payload_schema"],
        "compiler_id": manifest["compiler_id"],
        "source_sha256": manifest["source_sha256"],
        "project_binding_sha256": manifest["project_binding_sha256"],
        "certificate_evidence_sha256": manifest["certificate_evidence_sha256"],
        "compiled_topology_identity_sha256": manifest[
            "compiled_topology_identity_sha256"
        ],
        "geometry_identity_sha256": manifest["geometry_identity_sha256"],
        "logical_rows_sha256": manifest["logical_rows_sha256"],
        **{f"{key}_count": str(value) for key, value in manifest["counts"].items()},
    }
    if meta != expected_meta:
        _fail("RAW_SPATIAL_META_INVALID", "database metadata differs from manifest")
    ledgers = dict(
        (section, (count, digest))
        for section, count, digest in connection.execute(
            "SELECT section_name,row_count,logical_sha256 FROM section_ledger"
        )
    )
    if frozenset(ledgers) != frozenset(_SECTIONS):
        _fail("RAW_SPATIAL_LEDGER_INVALID", "section ledger inventory differs")
    for section in _SECTIONS:
        if is_cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial verification was cancelled")
        count, digest = _section_digest(
            connection, section, is_cancelled=is_cancelled
        )
        if (count, digest) != ledgers[section] or count != manifest["counts"][section]:
            _fail("RAW_SPATIAL_LOGICAL_INTEGRITY_FAILED", f"{section} ledger differs")
        ordinals = connection.execute(
            f"SELECT MIN(ordinal),MAX(ordinal),COUNT(*) FROM {section}"
        ).fetchone()
        expected = (None, None, 0) if count == 0 else (0, count - 1, count)
        if ordinals != expected:
            _fail("RAW_SPATIAL_ORDINAL_INVALID", f"{section} ordinals are not contiguous")
    if _geometry_digest(connection) != manifest["geometry_identity_sha256"]:
        _fail("RAW_SPATIAL_GEOMETRY_IDENTITY_INVALID", "derived geometry identity differs")
    if _logical_digest(connection) != manifest["logical_rows_sha256"]:
        _fail("RAW_SPATIAL_LOGICAL_INTEGRITY_FAILED", "overall logical digest differs")
    _validate_relations(connection)
    _validate_coverage(connection)


T = TypeVar("T")


class LoadedRawSpatialContactAsset:
    """Bounded query facade owning one immutable temporary SQLite database."""

    __slots__ = ("_connection", "_temporary_directory", "manifest")

    def __init__(
        self,
        connection: sqlite3.Connection,
        temporary_directory: tempfile.TemporaryDirectory[str],
        manifest: Mapping[str, Any],
    ) -> None:
        self._connection = connection
        self._temporary_directory = temporary_directory
        copied = dict(manifest)
        copied["counts"] = MappingProxyType(dict(copied["counts"]))
        self.manifest = MappingProxyType(copied)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            self._temporary_directory.cleanup()

    def __enter__(self) -> "LoadedRawSpatialContactAsset":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _iter(
        self,
        section: str,
        *,
        batch_rows: int,
        net_name: str | None = None,
        layer_id: str | None = None,
    ) -> Iterator[Any]:
        if self._connection is None:
            _fail("RAW_SPATIAL_CLOSED", "raw spatial asset is closed")
        if type(batch_rows) is not int or batch_rows < 1 or batch_rows > MAX_RAW_SPATIAL_QUERY_ROWS:
            _fail("RAW_SPATIAL_BOUND_INVALID", "query batch size is outside its bound")
        clauses: list[str] = []
        parameters: list[str] = []
        if net_name is not None:
            _text(net_name, "net_name")
            clauses.append("net_fold=?")
            parameters.append(net_name.casefold())
        if layer_id is not None:
            _text(layer_id, "layer_id")
            if section == "vias":
                clauses.append("(start_layer_id_fold=? OR end_layer_id_fold=?)")
                parameters.extend((layer_id.casefold(), layer_id.casefold()))
            else:
                clauses.append("layer_id_fold=?")
                parameters.append(layer_id.casefold())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cursor = self._connection.execute(
            f"SELECT * FROM {section}{where} ORDER BY ordinal", parameters
        )
        row_type = _ROW_TYPES[section]
        field_names = tuple(field.name for field in fields(row_type))
        db_names = tuple(column[0] for column in cursor.description)
        indices = tuple(db_names.index(name) for name in field_names)
        while rows := cursor.fetchmany(batch_rows):
            for row in rows:
                typed_values = [row[index] for index in indices]
                if section == "layers":
                    conductor_index = field_names.index("is_conductor")
                    typed_values[conductor_index] = bool(typed_values[conductor_index])
                yield row_type(*typed_values)

    def iter_layers(self, *, batch_rows: int = 1000) -> Iterator[RawSpatialLayerRow]:
        return self._iter("layers", batch_rows=batch_rows)

    def iter_source_coverage(
        self,
    ) -> Iterator[RawSpatialSourceCoverageRow]:
        return self._iter("source_coverage", batch_rows=1)

    def iter_section_coverage(
        self, *, batch_rows: int = 3
    ) -> Iterator[RawSpatialSectionCoverageRow]:
        return self._iter("section_coverage", batch_rows=batch_rows)

    def iter_padstacks(
        self, *, batch_rows: int = 1000
    ) -> Iterator[RawSpatialPadstackRow]:
        return self._iter("padstacks", batch_rows=batch_rows)

    def iter_pad_shapes(
        self, *, layer_id: str | None = None, batch_rows: int = 1000
    ) -> Iterator[RawSpatialPadShapeRow]:
        return self._iter("pad_shapes", layer_id=layer_id, batch_rows=batch_rows)

    def iter_surfaces(
        self,
        *,
        net_name: str | None = None,
        layer_id: str | None = None,
        batch_rows: int = 1000,
    ) -> Iterator[RawSpatialSurfaceRow]:
        return self._iter(
            "surfaces", net_name=net_name, layer_id=layer_id, batch_rows=batch_rows
        )

    def iter_nodes(
        self,
        *,
        net_name: str | None = None,
        layer_id: str | None = None,
        batch_rows: int = 1000,
    ) -> Iterator[RawSpatialNodeRow]:
        return self._iter(
            "nodes", net_name=net_name, layer_id=layer_id, batch_rows=batch_rows
        )

    def iter_traces(
        self,
        *,
        net_name: str | None = None,
        layer_id: str | None = None,
        batch_rows: int = 1000,
    ) -> Iterator[RawSpatialTraceRow]:
        return self._iter(
            "traces", net_name=net_name, layer_id=layer_id, batch_rows=batch_rows
        )

    def iter_vias(
        self,
        *,
        net_name: str | None = None,
        layer_id: str | None = None,
        batch_rows: int = 1000,
    ) -> Iterator[RawSpatialViaRow]:
        return self._iter(
            "vias", net_name=net_name, layer_id=layer_id, batch_rows=batch_rows
        )

    def _get(
        self, section: str, id_column: str, net_name: str, value: str
    ) -> Any | None:
        _text(net_name, "net_name")
        _text(value, id_column)
        if self._connection is None:
            _fail("RAW_SPATIAL_CLOSED", "raw spatial asset is closed")
        cursor = self._connection.execute(
            f"SELECT * FROM {section} WHERE net_fold=? AND {id_column}_fold=?",
            (net_name.casefold(), value.casefold()),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        row_type = _ROW_TYPES[section]
        names = tuple(column[0] for column in cursor.description)
        return row_type(*(row[names.index(field.name)] for field in fields(row_type)))

    def get_node(self, net_name: str, node_id: str) -> RawSpatialNodeRow | None:
        return self._get("nodes", "node_id", net_name, node_id)

    def get_padstack(self, padstack_id: str) -> RawSpatialPadstackRow | None:
        _text(padstack_id, "padstack_id")
        if self._connection is None:
            _fail("RAW_SPATIAL_CLOSED", "raw spatial asset is closed")
        cursor = self._connection.execute(
            "SELECT * FROM padstacks WHERE padstack_id_fold=?",
            (padstack_id.casefold(),),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        names = tuple(column[0] for column in cursor.description)
        return RawSpatialPadstackRow(
            *(row[names.index(field.name)] for field in fields(RawSpatialPadstackRow))
        )

    def get_trace(self, net_name: str, trace_id: str) -> RawSpatialTraceRow | None:
        return self._get("traces", "trace_id", net_name, trace_id)

    def get_via(self, net_name: str, via_id: str) -> RawSpatialViaRow | None:
        return self._get("vias", "via_id", net_name, via_id)


def load_raw_spatial_contact_asset(
    manifest: Mapping[str, Any],
    attachments: Mapping[str, bytes],
    *,
    expected_source_sha256: str,
    expected_project_binding_sha256: str,
    expected_certificate_evidence_sha256: str,
    expected_compiled_topology_identity_sha256: str,
    expected_geometry_identity_sha256: str,
    is_cancelled: Callable[[], bool] | None = None,
) -> LoadedRawSpatialContactAsset:
    """Verify and open a source/project/certificate/topology-bound asset."""

    validated = _validate_manifest(manifest)
    expected = {
        "source_sha256": expected_source_sha256,
        "project_binding_sha256": expected_project_binding_sha256,
        "certificate_evidence_sha256": expected_certificate_evidence_sha256,
        "compiled_topology_identity_sha256": expected_compiled_topology_identity_sha256,
        "geometry_identity_sha256": expected_geometry_identity_sha256,
    }
    for key, value in expected.items():
        if validated[key] != _sha(value, f"expected {key}"):
            _fail("RAW_SPATIAL_BINDING_MISMATCH", f"{key} differs from expected binding")
    if not isinstance(attachments, Mapping):
        _fail("RAW_SPATIAL_ATTACHMENT_INVALID", "required attachment is absent")
    asset_name = validated["asset_name"]
    matching_names = tuple(
        name
        for name in attachments
        if type(name) is str and name.casefold() == asset_name.casefold()
    )
    if matching_names != (asset_name,):
        _fail(
            "RAW_SPATIAL_ATTACHMENT_INVALID",
            "required attachment is absent or has an ambiguous casefold collision",
        )
    cancelled = is_cancelled or (lambda: False)
    if cancelled():
        _fail("RAW_SPATIAL_CANCELLED", "raw spatial load was cancelled")
    temporary_directory = tempfile.TemporaryDirectory(prefix="spdpi-raw-spatial-read-")
    database_path = Path(temporary_directory.name) / "raw-spatial.sqlite"
    connection: sqlite3.Connection | None = None
    try:
        _decompress_to_file(
            attachments[asset_name],
            validated,
            database_path,
            is_cancelled=cancelled,
        )
        connection = _open_readonly(database_path)
        connection.set_progress_handler(lambda: 1 if cancelled() else 0, _BATCH_ROWS)
        _validate_database(connection, validated, is_cancelled=cancelled)
        return LoadedRawSpatialContactAsset(connection, temporary_directory, validated)
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        temporary_directory.cleanup()
        if cancelled():
            _fail("RAW_SPATIAL_CANCELLED", "raw spatial load was cancelled")
        _fail("RAW_SPATIAL_DATABASE_INVALID", str(exc))
    except BaseException:
        if connection is not None:
            connection.close()
        temporary_directory.cleanup()
        raise


__all__ = [
    "MAX_RAW_SPATIAL_COMPRESSED_BYTES",
    "MAX_RAW_SPATIAL_EXPANSION_RATIO",
    "MAX_RAW_SPATIAL_QUERY_ROWS",
    "MAX_RAW_SPATIAL_ROWS_PER_SECTION",
    "MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES",
    "RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY",
    "RAW_SPATIAL_CONTACT_ASSET_PREFIX",
    "RAW_SPATIAL_CONTACT_ASSET_SCHEMA",
    "RAW_SPATIAL_CONTACT_COMPILER_ID",
    "RAW_SPATIAL_CONTACT_PAYLOAD_SCHEMA",
    "LoadedRawSpatialContactAsset",
    "RawSpatialContactAssetError",
    "RawSpatialLayerRow",
    "RawSpatialNodeRow",
    "RawSpatialPadShapeRow",
    "RawSpatialPadstackRow",
    "RawSpatialSectionCoverageRow",
    "RawSpatialSourceCoverageRow",
    "RawSpatialSurfaceRow",
    "RawSpatialTraceRow",
    "RawSpatialViaRow",
    "build_raw_spatial_contact_asset",
    "load_raw_spatial_contact_asset",
    "validate_project_raw_spatial_contact_asset_envelope",
]
