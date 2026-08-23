"""Safe persisted fast path for a compiled v4 finite-Via topology.

The canonical v4 certificate remains the authoritative audit artifact.  A
production certificate is, however, several GiB of JSON and parsing it creates
a much larger Python object tree.  This module stores the already validated
compiler result in a separate hash-bound, zlib-compressed SQLite attachment.

SQLite is used only as a fixed-schema, read-only row container.  No SQL from an
attachment is executed, no pickle/code-bearing format is accepted, and every
load checks the compressed/uncompressed hashes, exact schema, bounded counts,
canonical compact views, project/source/compiler binding, logical row hash and
the numerical compiler's topology identity before returning typed objects.
Old v4 bundles without this optional asset continue through the canonical JSON
fallback in :mod:`spd_decap_pi.surface_certificate_asset`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
from math import isfinite
from pathlib import Path
import sqlite3
import tempfile
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote
import zlib

from .canonical_json import (
    canonical_json_bytes,
    concrete_canonical_json_bytes,
    iter_canonical_json_bytes,
    iter_concrete_canonical_json_bytes,
)
from .surface_certificate_asset import (
    FINITE_VIA_SURFACE_COMPILER,
    FINITE_VIA_SURFACE_SCHEMA,
    MAX_SURFACE_CERTIFICATE_COMPILED_ONLY_IDENTITY_BYTES,
    SURFACE_CERTIFICATE_METADATA_KEY,
    SurfaceCertificateAssetError,
    _compiled_only_stub_from_verified_inline,
    _freeze_validation_cache_safe_json_view,
    freeze_json_view,
    is_surface_certificate_asset_stub,
    validate_inline_surface_certificate,
    validated_surface_certificate_storage_stub,
)

if TYPE_CHECKING:
    from ._core.solver.finite_via_layerwise import CompiledFiniteViaBaseTopology


COMPILED_TOPOLOGY_ASSET_METADATA_KEY: Final = "layerwise_compiled_topology_asset"
COMPILED_TOPOLOGY_ASSET_SCHEMA: Final = "spd-layerwise-compiled-topology-asset-v1"
COMPILED_TOPOLOGY_PAYLOAD_SCHEMA: Final = "spd-layerwise-compiled-topology-sqlite-v1"
COMPILED_TOPOLOGY_COMPILER_ID: Final = "layerwise-compiled-topology-sqlite-v1"
COMPILED_TOPOLOGY_COMPRESSION: Final = "zlib"
COMPILED_TOPOLOGY_ASSET_PREFIX: Final = "topology"

# The named 1.1 GiB SPDs compile roughly 0.78 million nodes, 1.69 million
# links, and 1.73 million ownership rows.  Keep finite row/schema/decoded/
# expansion bounds, but leave enough compressed headroom that the persisted
# fast path cannot fail only after the canonical certificate has completed.
MAX_COMPILED_TOPOLOGY_COMPRESSED_BYTES: Final = 512 * 1024 * 1024
MAX_COMPILED_TOPOLOGY_UNCOMPRESSED_BYTES: Final = 3 * 1024 * 1024 * 1024
MAX_COMPILED_TOPOLOGY_EXPANSION_RATIO: Final = 128
MAX_COMPILED_TOPOLOGY_NODES: Final = 5_000_000
MAX_COMPILED_TOPOLOGY_LINKS: Final = 10_000_000
MAX_COMPILED_TOPOLOGY_OWNERS: Final = 20_000_000
MAX_COMPILED_TOPOLOGY_PORTS: Final = 100_000
MAX_COMPILED_TOPOLOGY_PORT_MEMBERS: Final = 5_000_000
MAX_COMPILED_TOPOLOGY_LANDINGS: Final = 5_000_000
MAX_COMPILED_TOPOLOGY_VIEW_BYTES: Final = 512 * 1024 * 1024
MAX_COMPILED_TOPOLOGY_TEXT_BYTES: Final = 16 * 1024

_STREAM_BYTES: Final = 1024 * 1024
_BATCH_ROWS: Final = 10_000
_VIEW_CANCEL_CHECK_CHUNKS: Final = 4_096
_VIEW_PROGRESS_BYTES: Final = 16 * 1024 * 1024
_EXPANSION_GRACE_BYTES: Final = 1024 * 1024
_APPLICATION_ID: Final = 0x53504454  # "SPDT"
_USER_VERSION: Final = 1
_VIEW_INTEGRITY: Final = "full-canonical-certificate-verified"
_EXTERNAL_VIEW_SCHEMA: Final = "spd-layerwise-external-port-proof-view-v1"
_SCENARIO_VIEW_SCHEMA_V1: Final = "spd-layerwise-scenario-certificate-view-v1"
_SCENARIO_VIEW_SCHEMA: Final = "spd-layerwise-scenario-certificate-view-v2"
_SCENARIO_VIEW_SCHEMAS: Final = frozenset(
    {_SCENARIO_VIEW_SCHEMA_V1, _SCENARIO_VIEW_SCHEMA}
)
_SURFACE_VIEW_SCHEMA: Final = "spd-layerwise-surface-certificate-view-v1"
_SCENARIO_BINDING_PROJECTION_SCHEMA: Final = (
    "spd-retarget-binding-runtime-projection-v1"
)
_SCENARIO_BINDING_RUNTIME_KEYS: Final = (
    "source_landing_key",
    "role",
    "target_layer",
    "status",
    "issues",
    "target_rail_id",
    "rail_id",
    "target_net",
    "destination_net",
    "net",
    "refdes",
    "via_template_id",
    "destination_vertex_id",
)
_SCENARIO_TOPOLOGY_BASE_KEYS: Final = frozenset(
    {
        "schema_version",
        "status",
        "conditional_contacts",
        "retarget_landing_xy_coverage",
    }
)
_SCENARIO_RETARGET_KEYS: Final = (
    "retarget_destination_bindings",
    "retarget_landing_xy_bindings",
)
_ANCHOR_KEYS: Final = frozenset({"rail_id", "branch_id", "role", "pin_id"})
_CONTACT_KEYS: Final = frozenset(
    {
        "pin_id",
        "net",
        "exposed_quotient_vertex_id",
        "status",
        "incident_via_id",
        "first_via_quotient_edge_id",
    }
)
_MANIFEST_KEYS: Final = frozenset(
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

_META_KEYS_WITHOUT_LOGICAL: Final = frozenset(
    {
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
        "node_count",
        "topology_link_count",
        "finite_link_count",
        "owner_count",
        "port_count",
        "omitted_rail_count",
        "landing_count",
        "view_count",
    }
)
_META_KEYS: Final = frozenset((*_META_KEYS_WITHOUT_LOGICAL, "logical_rows_sha256"))

_CREATE_SQL: Final = """
CREATE TABLE meta (
    key TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE nodes (
    kind INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    PRIMARY KEY (kind, ordinal)
) WITHOUT ROWID;
CREATE TABLE links (
    kind INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    link_id TEXT NOT NULL,
    first_node INTEGER NOT NULL,
    second_node INTEGER NOT NULL,
    parallel_count INTEGER NOT NULL,
    resistance_ohm REAL,
    inductance_h REAL,
    PRIMARY KEY (kind, ordinal)
) WITHOUT ROWID;
CREATE TABLE link_owners (
    kind INTEGER NOT NULL,
    link_ordinal INTEGER NOT NULL,
    owner_ordinal INTEGER NOT NULL,
    owner_id TEXT NOT NULL,
    PRIMARY KEY (kind, link_ordinal, owner_ordinal)
) WITHOUT ROWID;
CREATE TABLE rail_ports (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    rail_id TEXT NOT NULL,
    selected_net TEXT NOT NULL,
    reference_net TEXT NOT NULL,
    positive_node INTEGER NOT NULL,
    negative_node INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE rail_port_members (
    port_ordinal INTEGER NOT NULL,
    kind INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (port_ordinal, kind, ordinal)
) WITHOUT ROWID;
CREATE TABLE omitted_rails (
    ordinal INTEGER NOT NULL PRIMARY KEY,
    rail_id TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE landing_bindings (
    kind INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    first_key TEXT NOT NULL,
    second_key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (kind, ordinal)
) WITHOUT ROWID;
CREATE TABLE views (
    name TEXT NOT NULL PRIMARY KEY,
    payload BLOB NOT NULL,
    payload_size INTEGER NOT NULL,
    payload_sha256 TEXT NOT NULL
) WITHOUT ROWID;
"""


class CompiledTopologyAssetError(ValueError):
    """A persisted compiled topology is unsafe, stale, or inconsistent."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "COMPILED_TOPOLOGY_ASSET_INVALID"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LoadedCompiledTopologyAsset:
    topology: CompiledFiniteViaBaseTopology
    scenario_certificate_view: Mapping[str, Any]
    external_port_proof_view: Mapping[str, Any]


def _fail(code: str, message: str) -> None:
    raise CompiledTopologyAssetError(code, message)


def _is_sha256(value: object) -> bool:
    text = str(value).strip().casefold()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _hash(value: object, *, label: str) -> str:
    text = str(value).strip().casefold()
    if not _is_sha256(text):
        _fail("COMPILED_TOPOLOGY_MANIFEST_INVALID", f"{label} is not SHA-256")
    return text


def _bounded_int(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        _fail("COMPILED_TOPOLOGY_BOUND_EXCEEDED", f"{label} exceeds its safety bound")
    return value


def _text(value: object, *, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        _fail("COMPILED_TOPOLOGY_ROW_INVALID", f"{label} must be nonblank text")
    if len(value.encode("utf-8")) > MAX_COMPILED_TOPOLOGY_TEXT_BYTES:
        _fail("COMPILED_TOPOLOGY_TEXT_TOO_LARGE", f"{label} exceeds its text bound")
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError, OverflowError) as exc:
        _fail("COMPILED_TOPOLOGY_CANONICAL_JSON_INVALID", str(exc))


def _concrete_canonical_bytes(value: Any) -> bytes:
    try:
        return concrete_canonical_json_bytes(value)
    except (TypeError, ValueError, OverflowError) as exc:
        _fail("COMPILED_TOPOLOGY_CANONICAL_JSON_INVALID", str(exc))


def _canonical_sha256(
    value: Any,
    *,
    trailing_newline: bool = False,
    concrete_containers: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    digest = sha256()
    cancelled = is_cancelled or (lambda: False)
    iterator = (
        iter_concrete_canonical_json_bytes(value)
        if concrete_containers
        else iter_canonical_json_bytes(value)
    )
    try:
        for chunk_index, chunk in enumerate(iterator):
            if (
                chunk_index % _VIEW_CANCEL_CHECK_CHUNKS == 0
                and cancelled()
            ):
                _fail(
                    "COMPILED_TOPOLOGY_CANCELLED",
                    "compiled topology canonical hashing was cancelled",
                )
            digest.update(chunk)
    except CompiledTopologyAssetError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        _fail("COMPILED_TOPOLOGY_CANONICAL_JSON_INVALID", str(exc))
    if cancelled():
        _fail(
            "COMPILED_TOPOLOGY_CANCELLED",
            "compiled topology canonical hashing was cancelled",
        )
    if trailing_newline:
        digest.update(b"\n")
    return digest.hexdigest()


def _metadata(project: Any) -> Mapping[str, Any]:
    if isinstance(project, Mapping):
        value = project.get("metadata", {})
    else:
        value = getattr(project, "metadata", {})
    if not isinstance(value, Mapping):
        _fail("COMPILED_TOPOLOGY_PROJECT_METADATA_INVALID", "project metadata is not a mapping")
    return value


def _spd_import(project: Any) -> Mapping[str, Any]:
    metadata = _metadata(project)
    value = metadata.get("spd_import", {})
    if "spd_import" in metadata and not isinstance(value, Mapping):
        _fail(
            "COMPILED_TOPOLOGY_PROJECT_METADATA_INVALID",
            "project spd_import metadata exists but is not a mapping",
        )
    return value


def _certificate_identity(project: Any) -> tuple[Mapping[str, Any] | None, str, str]:
    spd_import = _spd_import(project)
    if SURFACE_CERTIFICATE_METADATA_KEY not in spd_import:
        return None, "", ""
    certificate = spd_import[SURFACE_CERTIFICATE_METADATA_KEY]
    if not isinstance(certificate, Mapping):
        _fail(
            "COMPILED_TOPOLOGY_SURFACE_STUB_INVALID",
            "surface certificate metadata exists but is not a mapping",
        )
    return (
        certificate,
        str(certificate.get("source_sha256", "")).strip().casefold(),
        str(certificate.get("evidence_sha256", "")).strip().casefold(),
    )


def _project_binding_sha256(project: Any, *, source_sha256: str, evidence_sha256: str) -> str:
    spd_import = _spd_import(project)
    raw_records = spd_import.get("plane_geometries", ())
    if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes)):
        _fail("COMPILED_TOPOLOGY_PROJECT_INVALID", "plane geometry manifest is absent")
    records: list[tuple[Any, ...]] = []
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            _fail("COMPILED_TOPOLOGY_PROJECT_INVALID", "plane geometry row is invalid")
        islands = raw.get("island_ids", ())
        if not isinstance(islands, Sequence) or isinstance(islands, (str, bytes)):
            _fail("COMPILED_TOPOLOGY_PROJECT_INVALID", "geometry island manifest is invalid")
        records.append(
            (
                str(raw.get("layer", "")).strip().casefold(),
                str(raw.get("net", "")).strip().casefold(),
                str(raw.get("asset", "")).strip(),
                str(raw.get("asset_sha256", "")).strip().casefold(),
                tuple(sorted(str(item).strip() for item in islands)),
            )
        )
    raw_rails = (
        project.get("rails", ())
        if isinstance(project, Mapping)
        else getattr(project, "rails", ())
    )
    rails = sorted(
        (
            str(item.get("rail_id", "") if isinstance(item, Mapping) else getattr(item, "rail_id", "")).strip(),
            str(item.get("net", "") if isinstance(item, Mapping) else getattr(item, "net", "")).strip(),
        )
        for item in raw_rails
    )
    raw_aliases = (
        project.get("gnd_aliases", ())
        if isinstance(project, Mapping)
        else getattr(project, "gnd_aliases", ())
    )
    aliases = sorted(str(item).strip() for item in raw_aliases)
    raw_stackup = (
        project.get("stackup_layers", ())
        if isinstance(project, Mapping)
        else getattr(project, "stackup_layers", ())
    )
    if not isinstance(raw_stackup, Sequence) or isinstance(
        raw_stackup, (str, bytes)
    ):
        _fail("COMPILED_TOPOLOGY_PROJECT_INVALID", "stackup layer manifest is invalid")

    def value(row: object, key: str, default: object = None) -> object:
        return row.get(key, default) if isinstance(row, Mapping) else getattr(
            row, key, default
        )

    stackup: list[dict[str, Any]] = []
    for ordinal, raw_layer in enumerate(raw_stackup):
        if not isinstance(raw_layer, Mapping) and not hasattr(raw_layer, "name"):
            _fail("COMPILED_TOPOLOGY_PROJECT_INVALID", "stackup layer row is invalid")
        raw_properties = value(raw_layer, "dielectric_properties", ())
        if not isinstance(raw_properties, Sequence) or isinstance(
            raw_properties, (str, bytes)
        ):
            _fail(
                "COMPILED_TOPOLOGY_PROJECT_INVALID",
                "stackup dielectric property manifest is invalid",
            )
        raw_pwr_nets = value(raw_layer, "pwr_nets", ())
        if not isinstance(raw_pwr_nets, Sequence) or isinstance(
            raw_pwr_nets, (str, bytes)
        ):
            _fail(
                "COMPILED_TOPOLOGY_PROJECT_INVALID",
                "stackup power-net manifest is invalid",
            )
        properties = [
            {
                "frequency_hz": value(item, "frequency_hz"),
                "dk": value(item, "dk"),
                "df": value(item, "df"),
            }
            for item in raw_properties
        ]
        stackup.append(
            {
                "ordinal": ordinal,
                "name": str(value(raw_layer, "name", "")).strip(),
                "thickness_um": value(raw_layer, "thickness_um"),
                "conductivity_s_m": value(raw_layer, "conductivity_s_m"),
                "dk": value(raw_layer, "dk"),
                "df": value(raw_layer, "df"),
                "material": value(raw_layer, "material"),
                "dielectric_properties": properties,
                "pwr_nets": [str(item).strip() for item in raw_pwr_nets],
            }
        )
    return sha256(
        _canonical_bytes(
            {
                "schema": "compiled-topology-project-binding-v2",
                "surface_schema": FINITE_VIA_SURFACE_SCHEMA,
                "surface_compiler": FINITE_VIA_SURFACE_COMPILER,
                "source_sha256": source_sha256,
                "certificate_evidence_sha256": evidence_sha256,
                "geometry": sorted(records),
                "stackup": stackup,
                "rails": rails,
                "gnd_aliases": aliases,
            }
        )
    ).hexdigest()


def _certificate_rows(value: object, *, label: str) -> tuple[Mapping[str, Any], ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or any(not isinstance(item, Mapping) for item in value)
    ):
        _fail("COMPILED_TOPOLOGY_VIEW_INVALID", f"{label} rows are invalid")
    return tuple(value)


def _compact_landing_ownership_rows(
    value: object,
) -> tuple[dict[str, Any], ...]:
    rows = _certificate_rows(value, label="terminal landing contact")
    result: list[dict[str, Any]] = []
    seen_via_ids: set[str] = set()
    for row in rows:
        raw_via_id = row.get("via_id")
        raw_owner_kind = row.get("terminal_owner_kind")
        via_id = raw_via_id.strip() if isinstance(raw_via_id, str) else ""
        owner_kind = (
            raw_owner_kind.strip().casefold()
            if isinstance(raw_owner_kind, str)
            else ""
        )
        via_key = via_id.casefold()
        if (
            not via_id
            or len(via_id.encode("utf-8")) > MAX_COMPILED_TOPOLOGY_TEXT_BYTES
            or owner_kind not in {"device", "decap", "unknown"}
            or via_key in seen_via_ids
        ):
            _fail(
                "COMPILED_TOPOLOGY_VIEW_INVALID",
                "terminal landing ownership rows are blank, duplicated, or unsupported",
            )
        seen_via_ids.add(via_key)
        result.append(
            {
                "via_id": via_id,
                "terminal_owner_kind": owner_kind,
            }
        )
    return tuple(result)


def _scenario_projection_evidence_sha256(
    row: Mapping[str, Any],
    *,
    source_sha256: object,
    certificate_evidence_sha256: object,
    concrete_containers: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    source = str(source_sha256).strip().casefold()
    certificate_evidence = str(certificate_evidence_sha256).strip().casefold()
    if not _is_sha256(source) or not _is_sha256(certificate_evidence):
        _fail(
            "COMPILED_TOPOLOGY_VIEW_INVALID",
            "scenario binding projection is not bound to valid source/certificate evidence",
        )
    return _canonical_sha256(
        {
            "schema": _SCENARIO_BINDING_PROJECTION_SCHEMA,
            "source_sha256": source,
            "certificate_evidence_sha256": certificate_evidence,
            "binding": row,
        },
        concrete_containers=concrete_containers,
        is_cancelled=is_cancelled,
    )


def _scenario_binding_runtime_identity_is_valid(row: Mapping[str, Any]) -> bool:
    source_landing_key = row.get("source_landing_key")
    return bool(
        isinstance(source_landing_key, Sequence)
        and not isinstance(source_landing_key, (str, bytes, bytearray))
        and len(source_landing_key) == 2
        and all(str(item).strip() for item in source_landing_key)
        and str(row.get("role", "")).strip().casefold() in {"power", "ground"}
        and str(row.get("target_layer", "")).strip()
        and str(row.get("destination_vertex_id", "")).strip()
    )


def _validated_original_scenario_binding_row(
    row: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    status = row.get("status")
    issues = row.get("issues")
    if (
        not isinstance(status, str)
        or status.strip().casefold() != "complete"
        or not isinstance(issues, Sequence)
        or isinstance(issues, (str, bytes, bytearray))
        or bool(issues)
    ):
        _fail(
            "COMPILED_TOPOLOGY_VIEW_INVALID",
            f"{label} row is incomplete or carries issues",
        )
    observed = str(row.get("binding_evidence_sha256", "")).strip().casefold()
    if not _is_sha256(observed):
        _fail(
            "COMPILED_TOPOLOGY_VIEW_INVALID",
            f"{label} row has no valid binding SHA-256",
        )
    unsigned = {
        str(key): value
        for key, value in row.items()
        if str(key) != "binding_evidence_sha256"
    }
    if _canonical_sha256(unsigned) != observed:
        _fail(
            "COMPILED_TOPOLOGY_VIEW_INTEGRITY_FAILED",
            f"{label} row binding SHA-256 differs from its full payload",
        )
    projected = {
        key: row[key]
        for key in _SCENARIO_BINDING_RUNTIME_KEYS
        if key in row
    }
    if not _scenario_binding_runtime_identity_is_valid(projected):
        _fail(
            "COMPILED_TOPOLOGY_VIEW_INVALID",
            f"{label} row lacks its runtime route identity",
        )
    projected["binding_evidence_sha256"] = observed
    return projected


def _compact_scenario_binding_rows(
    value: object,
    *,
    label: str,
    source_sha256: object,
    certificate_evidence_sha256: object,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in _certificate_rows(value, label=label):
        projected = _validated_original_scenario_binding_row(row, label=label)
        projected["projection_evidence_sha256"] = (
            _scenario_projection_evidence_sha256(
                projected,
                source_sha256=source_sha256,
                certificate_evidence_sha256=certificate_evidence_sha256,
            )
        )
        result.append(projected)
    return result


def _validate_projected_scenario_view(
    view: Mapping[str, Any],
    *,
    concrete_containers: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    topology = view.get("scenario_decap_terminal_topology")
    quotient = view.get("finite_via_quotient")
    if not isinstance(topology, Mapping) or not isinstance(quotient, Mapping):
        _fail(
            "COMPILED_TOPOLOGY_VIEW_INVALID",
            "scenario binding projection has no topology/quotient mapping",
        )
    if not set(topology).issubset(
        _SCENARIO_TOPOLOGY_BASE_KEYS | frozenset(_SCENARIO_RETARGET_KEYS)
    ) or not set(quotient).issubset({"schema_version", "status"}):
        _fail(
            "COMPILED_TOPOLOGY_VIEW_INVALID",
            "scenario binding projection retains unsupported topology fields",
        )
    allowed_row_keys = frozenset(
        (*_SCENARIO_BINDING_RUNTIME_KEYS, "binding_evidence_sha256", "projection_evidence_sha256")
    )
    for key in _SCENARIO_RETARGET_KEYS:
        if key not in topology:
            continue
        for row in _certificate_rows(topology[key], label=key.replace("_", " ")):
            issues = row.get("issues")
            if (
                not set(row).issubset(allowed_row_keys)
                or str(row.get("status", "")).strip().casefold() != "complete"
                or not isinstance(issues, Sequence)
                or isinstance(issues, (str, bytes, bytearray))
                or bool(issues)
                or not _scenario_binding_runtime_identity_is_valid(row)
                or not _is_sha256(row.get("binding_evidence_sha256"))
                or not _is_sha256(row.get("projection_evidence_sha256"))
            ):
                _fail(
                    "COMPILED_TOPOLOGY_VIEW_INVALID",
                    "scenario binding projection row is incomplete or unsupported",
                )
            unsigned_projection = {
                str(row_key): value
                for row_key, value in row.items()
                if str(row_key) != "projection_evidence_sha256"
            }
            expected = _scenario_projection_evidence_sha256(
                unsigned_projection,
                source_sha256=view.get("source_sha256"),
                certificate_evidence_sha256=view.get("evidence_sha256"),
                concrete_containers=concrete_containers,
                is_cancelled=is_cancelled,
            )
            if (
                str(row.get("projection_evidence_sha256", ""))
                .strip()
                .casefold()
                != expected
            ):
                _fail(
                    "COMPILED_TOPOLOGY_VIEW_INTEGRITY_FAILED",
                    "scenario binding projection SHA-256 differs from its runtime payload",
                )


def compact_finite_certificate_views(
    certificate: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Extract the three bounded post-compile certificate consumers."""

    shared = {
        "schema_version": certificate.get("schema_version"),
        "compiler_id": certificate.get("compiler_id"),
        "source_sha256": certificate.get("source_sha256"),
        "evidence_sha256": certificate.get("evidence_sha256"),
        "status": certificate.get("status"),
    }
    # The complete terminal landing rows intentionally retain physical-path,
    # quotient, and repeated component-island evidence for the one-time strict
    # finite-Via compile.  The persisted surface consumer only needs the exact
    # Via ownership partition used by `_device_terminal_ownership_disclosure`.
    # Persisting every component island again for each of 66k+ landings turns a
    # single-digit-MiB ownership ledger into a >512 MiB compact view on real
    # boards.
    # The projected rows remain bound to the verified full-certificate evidence
    # hash and to this view's canonical self hash.
    compact_landing_ownership = _compact_landing_ownership_rows(
        certificate.get("terminal_landing_contacts", ())
    )
    surface_payload = {
        **shared,
        "view_schema": _SURFACE_VIEW_SCHEMA,
        "integrity_validation": _VIEW_INTEGRITY,
        "geometry_assets": certificate.get("geometry_assets", ()),
        "surface_equivalence_components": certificate.get(
            "surface_equivalence_components", ()
        ),
        "surface_equivalence_proofs": certificate.get("surface_equivalence_proofs", ()),
        "terminal_landing_contacts": compact_landing_ownership,
    }
    surface_view = {
        **surface_payload,
        "view_evidence_sha256": _canonical_sha256(surface_payload),
    }
    topology = certificate.get("scenario_decap_terminal_topology")
    quotient = certificate.get("finite_via_quotient")
    if not isinstance(topology, Mapping) or not isinstance(quotient, Mapping):
        _fail("COMPILED_TOPOLOGY_VIEW_INVALID", "scenario/quotient compact view is absent")
    scenario_topology_view = {
        key: topology[key]
        for key in (
            "schema_version",
            "status",
            "conditional_contacts",
            "retarget_landing_xy_coverage",
        )
        if key in topology
    }
    # These lists are authored twice in the full certificate for historical
    # fallback compatibility. Preserve the same topology-first fallback, but
    # persist one authoritative copy. Each selected full row is verified
    # before projecting away audit-only island/component/geometry fields. The
    # retained original binding hash preserves route/plan identity; the
    # source/certificate-bound projection hash protects the runtime subset.
    for key in _SCENARIO_RETARGET_KEYS:
        if key in topology:
            source_rows = topology[key]
        elif key in quotient:
            source_rows = quotient[key]
        else:
            continue
        scenario_topology_view[key] = _compact_scenario_binding_rows(
            source_rows,
            label=key.replace("_", " "),
            source_sha256=shared["source_sha256"],
            certificate_evidence_sha256=shared["evidence_sha256"],
        )
    scenario_payload = {
        **shared,
        "view_schema": _SCENARIO_VIEW_SCHEMA,
        "integrity_validation": _VIEW_INTEGRITY,
        "scenario_decap_terminal_topology": scenario_topology_view,
        "finite_via_quotient": {
            key: quotient[key]
            for key in (
                "schema_version",
                "status",
            )
            if key in quotient
        },
    }
    scenario_view = {
        **scenario_payload,
        "view_evidence_sha256": _canonical_sha256(scenario_payload),
    }
    anchors = _certificate_rows(certificate.get("rail_anchor_bindings"), label="rail anchor")
    contacts = _certificate_rows(certificate.get("terminal_contacts"), label="terminal contact")
    compact_anchors = tuple({key: row.get(key) for key in _ANCHOR_KEYS} for row in anchors)
    anchor_pins = {str(row.get("pin_id", "")).strip().casefold() for row in compact_anchors}
    compact_contacts = tuple(
        {key: row.get(key) for key in _CONTACT_KEYS}
        for row in contacts
        if str(row.get("pin_id", "")).strip().casefold() in anchor_pins
    )
    if {str(row.get("pin_id", "")).strip().casefold() for row in compact_contacts} != anchor_pins:
        _fail("COMPILED_TOPOLOGY_VIEW_INVALID", "external Device proof is incomplete")
    external_payload = {
        **shared,
        "view_schema": _EXTERNAL_VIEW_SCHEMA,
        "integrity_validation": _VIEW_INTEGRITY,
        "rail_anchor_bindings": compact_anchors,
        "terminal_contacts": compact_contacts,
    }
    external_view = {
        **external_payload,
        # Match the layerwise consumer's historical canonical-json-with-newline
        # proof identity exactly.
        "view_evidence_sha256": _canonical_sha256(
            external_payload, trailing_newline=True
        ),
    }
    return surface_view, scenario_view, external_view


def _validated_compact_view(
    view: Mapping[str, Any],
    *,
    name: str,
    manifest: Mapping[str, Any],
    concrete_containers: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
) -> None:
    schemas = {
        "external": _EXTERNAL_VIEW_SCHEMA,
        "surface": _SURFACE_VIEW_SCHEMA,
    }
    schema_matches = (
        view.get("view_schema") in _SCENARIO_VIEW_SCHEMAS
        if name == "scenario"
        else view.get("view_schema") == schemas[name]
    )
    if (
        not schema_matches
        or view.get("integrity_validation") != _VIEW_INTEGRITY
        or view.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or view.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
        or str(view.get("status", "")).strip().casefold() != "complete"
        or str(view.get("source_sha256", "")).strip().casefold()
        != manifest["source_sha256"]
        or str(view.get("evidence_sha256", "")).strip().casefold()
        != manifest["certificate_evidence_sha256"]
    ):
        _fail("COMPILED_TOPOLOGY_VIEW_INVALID", f"view {name!r} identity differs")
    observed = _hash(view.get("view_evidence_sha256"), label=f"{name} view evidence")
    payload = {
        key: value for key, value in view.items() if key != "view_evidence_sha256"
    }
    if (
        _canonical_sha256(
            payload,
            trailing_newline=name == "external",
            concrete_containers=concrete_containers,
            is_cancelled=is_cancelled,
        )
        != observed
    ):
        _fail(
            "COMPILED_TOPOLOGY_VIEW_INTEGRITY_FAILED",
            f"view {name!r} self hash differs",
        )
    if name == "surface":
        _compact_landing_ownership_rows(view.get("terminal_landing_contacts", ()))
    elif name == "scenario" and view.get("view_schema") == _SCENARIO_VIEW_SCHEMA:
        _validate_projected_scenario_view(
            view,
            concrete_containers=concrete_containers,
            is_cancelled=is_cancelled,
        )


def freeze_compact_certificate_view(value: Any) -> Any:
    """Expose a recursively immutable compact view without duplicating its tree."""

    return freeze_json_view(value)


def _freeze_owned_compact_certificate_view(value: Any) -> Any:
    """Freeze one fresh compact tree for identity-safe validation caching."""

    return _freeze_validation_cache_safe_json_view(value)


def _compiled_manifest(project: Any) -> Mapping[str, Any] | None:
    spd_import = _spd_import(project)
    if COMPILED_TOPOLOGY_ASSET_METADATA_KEY not in spd_import:
        return None
    value = spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY]
    if not isinstance(value, Mapping):
        _fail(
            "COMPILED_TOPOLOGY_MANIFEST_INVALID",
            "compiled topology metadata exists but is not a mapping",
        )
    return value


def _validated_manifest(project: Any, manifest: Mapping[str, Any]) -> dict[str, Any]:
    if set(manifest) != set(_MANIFEST_KEYS):
        _fail("COMPILED_TOPOLOGY_MANIFEST_INVALID", "manifest has missing or extra fields")
    if (
        manifest.get("storage_schema") != COMPILED_TOPOLOGY_ASSET_SCHEMA
        or manifest.get("payload_schema") != COMPILED_TOPOLOGY_PAYLOAD_SCHEMA
        or manifest.get("compiler_id") != COMPILED_TOPOLOGY_COMPILER_ID
        or manifest.get("surface_schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or manifest.get("surface_compiler_id") != FINITE_VIA_SURFACE_COMPILER
        or manifest.get("compression") != COMPILED_TOPOLOGY_COMPRESSION
    ):
        _fail("COMPILED_TOPOLOGY_MANIFEST_UNSUPPORTED", "manifest schema/compiler is unsupported")
    source = _hash(manifest.get("source_sha256"), label="compiled source")
    evidence = _hash(manifest.get("certificate_evidence_sha256"), label="certificate evidence")
    surface_uncompressed_size = _bounded_int(
        manifest.get("surface_asset_uncompressed_size_bytes"),
        label="surface certificate canonical size",
        maximum=MAX_SURFACE_CERTIFICATE_COMPILED_ONLY_IDENTITY_BYTES,
    )
    surface_uncompressed = _hash(
        manifest.get("surface_asset_uncompressed_sha256"), label="surface asset identity"
    )
    project_binding = _hash(manifest.get("project_binding_sha256"), label="project binding")
    topology_identity = _hash(manifest.get("topology_identity_sha256"), label="topology identity")
    logical = _hash(manifest.get("logical_rows_sha256"), label="logical rows")
    compressed_sha = _hash(manifest.get("compressed_sha256"), label="compressed asset")
    uncompressed_sha = _hash(manifest.get("uncompressed_sha256"), label="uncompressed asset")
    compressed_size = _bounded_int(
        manifest.get("compressed_size_bytes"),
        label="compressed size",
        maximum=MAX_COMPILED_TOPOLOGY_COMPRESSED_BYTES,
    )
    uncompressed_size = _bounded_int(
        manifest.get("uncompressed_size_bytes"),
        label="uncompressed size",
        maximum=MAX_COMPILED_TOPOLOGY_UNCOMPRESSED_BYTES,
    )
    expected_name = (
        f"{COMPILED_TOPOLOGY_ASSET_PREFIX}/layerwise-compiled-topology-v1-"
        f"{evidence[:16]}.sqlite.zlib"
    )
    if manifest.get("asset_name") != expected_name:
        _fail("COMPILED_TOPOLOGY_ASSET_NAME_INVALID", "asset name is not canonical")
    if (
        uncompressed_size > _EXPANSION_GRACE_BYTES
        and uncompressed_size > max(1, compressed_size) * MAX_COMPILED_TOPOLOGY_EXPANSION_RATIO
    ):
        _fail("COMPILED_TOPOLOGY_EXPANSION_RATIO_EXCEEDED", "declared compression ratio is unsafe")
    certificate, expected_source, expected_evidence = _certificate_identity(project)
    if certificate is None or not is_surface_certificate_asset_stub(certificate):
        _fail("COMPILED_TOPOLOGY_SURFACE_STUB_MISSING", "compiled asset requires a saved v4 surface stub")
    retained_source = str(_spd_import(project).get("source_sha256", "")).strip().casefold()
    try:
        surface_identity = validated_surface_certificate_storage_stub(certificate)
    except SurfaceCertificateAssetError as exc:
        _fail(
            "COMPILED_TOPOLOGY_SURFACE_STUB_UNSUPPORTED",
            f"surface certificate descriptor is invalid [{exc.code}]: {exc}",
        )
    if (
        not _is_sha256(retained_source)
        or source != retained_source
        or source != expected_source
        or evidence != expected_evidence
    ):
        _fail("COMPILED_TOPOLOGY_CERTIFICATE_MISMATCH", "compiled asset names a different certificate")
    if surface_uncompressed != _hash(
        surface_identity.get("uncompressed_sha256"), label="surface certificate bytes"
    ):
        _fail("COMPILED_TOPOLOGY_CERTIFICATE_MISMATCH", "compiled asset is not bound to exact certificate bytes")
    if surface_uncompressed_size != surface_identity.get("uncompressed_size_bytes"):
        _fail(
            "COMPILED_TOPOLOGY_CERTIFICATE_MISMATCH",
            "compiled asset names a different canonical certificate size",
        )
    if project_binding != _project_binding_sha256(
        project, source_sha256=source, evidence_sha256=evidence
    ):
        _fail("COMPILED_TOPOLOGY_PROJECT_MISMATCH", "compiled asset is stale for this project")
    return {
        "asset_name": expected_name,
        "source_sha256": source,
        "certificate_evidence_sha256": evidence,
        "surface_asset_uncompressed_size_bytes": surface_uncompressed_size,
        "surface_asset_uncompressed_sha256": surface_uncompressed,
        "project_binding_sha256": project_binding,
        "topology_identity_sha256": topology_identity,
        "logical_rows_sha256": logical,
        "compressed_size_bytes": compressed_size,
        "compressed_sha256": compressed_sha,
        "uncompressed_size_bytes": uncompressed_size,
        "uncompressed_sha256": uncompressed_sha,
    }


def _attachment(
    attachments: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    *,
    digest_already_verified: bool = False,
) -> bytes:
    name = str(manifest["asset_name"])
    matches = [(key, value) for key, value in attachments.items() if key.casefold() == name.casefold()]
    if len(matches) != 1 or matches[0][0] != name:
        _fail("COMPILED_TOPOLOGY_ASSET_MISSING", f"compiled topology asset {name!r} is missing")
    content = matches[0][1]
    if not isinstance(content, (bytes, bytearray, memoryview)):
        _fail("COMPILED_TOPOLOGY_ASSET_INVALID", "compiled topology attachment must be bytes")
    raw = bytes(content)
    if len(raw) != manifest["compressed_size_bytes"] or (
        not (digest_already_verified and type(content) is bytes)
        and sha256(raw).hexdigest() != manifest["compressed_sha256"]
    ):
        _fail("COMPILED_TOPOLOGY_COMPRESSED_INTEGRITY_FAILED", "compiled topology compressed hash/size differs")
    return raw


def validate_compiled_topology_asset_envelope(
    project: Any, attachments: Mapping[str, bytes]
) -> Mapping[str, Any] | None:
    """Validate the optional compiled attachment without opening/decompressing it."""

    manifest = _compiled_manifest(project)
    if manifest is None:
        return None
    validated = _validated_manifest(project, manifest)
    _attachment(attachments, validated)
    return manifest


def _batched(values: Iterable[tuple[Any, ...]]) -> Iterable[list[tuple[Any, ...]]]:
    batch: list[tuple[Any, ...]] = []
    for value in values:
        batch.append(value)
        if len(batch) >= _BATCH_ROWS:
            yield batch
            batch = []
    if batch:
        yield batch


def _insert_batches(
    connection: sqlite3.Connection,
    sql: str,
    rows: Iterable[tuple[Any, ...]],
    *,
    cancelled: Callable[[], bool],
) -> None:
    for batch in _batched(rows):
        if cancelled():
            _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology persistence was cancelled")
        connection.executemany(sql, batch)


def _logical_rows_sha256(
    connection: sqlite3.Connection,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    digest = sha256()
    cancelled = is_cancelled or (lambda: False)
    queries = (
        ("meta", "SELECT key,value FROM meta WHERE key <> 'logical_rows_sha256' ORDER BY key"),
        ("nodes", "SELECT kind,ordinal,node_id FROM nodes ORDER BY kind,ordinal"),
        (
            "links",
            "SELECT kind,ordinal,link_id,first_node,second_node,parallel_count,resistance_ohm,inductance_h FROM links ORDER BY kind,ordinal",
        ),
        (
            "link_owners",
            "SELECT kind,link_ordinal,owner_ordinal,owner_id FROM link_owners ORDER BY kind,link_ordinal,owner_ordinal",
        ),
        (
            "rail_ports",
            "SELECT ordinal,rail_id,selected_net,reference_net,positive_node,negative_node FROM rail_ports ORDER BY ordinal",
        ),
        (
            "rail_port_members",
            "SELECT port_ordinal,kind,ordinal,value FROM rail_port_members ORDER BY port_ordinal,kind,ordinal",
        ),
        ("omitted_rails", "SELECT ordinal,rail_id FROM omitted_rails ORDER BY ordinal"),
        (
            "landing_bindings",
            "SELECT kind,ordinal,first_key,second_key,value FROM landing_bindings ORDER BY kind,ordinal",
        ),
        (
            "views",
            "SELECT name,payload_size,payload_sha256 FROM views ORDER BY name",
        ),
    )
    for section, query in queries:
        digest.update(section.encode("ascii"))
        digest.update(b"\x00")
        for ordinal, row in enumerate(connection.execute(query)):
            if ordinal % _BATCH_ROWS == 0 and cancelled():
                _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology operation was cancelled")
            digest.update(_concrete_canonical_bytes(list(row)))
            digest.update(b"\n")
    return digest.hexdigest()


def _bounded_canonical_view_bytes(
    payload: Mapping[str, Any],
    *,
    name: str,
    is_cancelled: Callable[[], bool],
    progress: Callable[[str, int], None],
    concrete_containers: bool,
) -> bytes:
    """Encode one raw SQLite view with an enforced pre-allocation byte bound.

    ``bytes.join`` must not be used here: the generic canonical iterator emits
    millions of small fragments for production certificates, and ``join``
    retains all of them before allocating the final payload.  A ``BytesIO``
    stream keeps one growing canonical buffer, checks the fail-closed bound
    before every write, and permits cancellation/progress while encoding.
    """

    if is_cancelled():
        _fail(
            "COMPILED_TOPOLOGY_CANCELLED",
            f"compiled topology {name} view encoding was cancelled",
        )
    iterator = (
        iter_concrete_canonical_json_bytes(payload)
        if concrete_containers
        else iter_canonical_json_bytes(payload)
    )
    stream = BytesIO()
    size = 0
    next_progress = _VIEW_PROGRESS_BYTES
    progress(name, 0)
    try:
        for chunk_index, chunk in enumerate(iterator):
            if (
                chunk_index % _VIEW_CANCEL_CHECK_CHUNKS == 0
                and is_cancelled()
            ):
                _fail(
                    "COMPILED_TOPOLOGY_CANCELLED",
                    f"compiled topology {name} view encoding was cancelled",
                )
            observed_at_least = size + len(chunk)
            if observed_at_least > MAX_COMPILED_TOPOLOGY_VIEW_BYTES:
                _fail(
                    "COMPILED_TOPOLOGY_VIEW_TOO_LARGE",
                    f"compact certificate view {name!r} canonical payload observed "
                    f"at least {observed_at_least:,} bytes; bound is "
                    f"{MAX_COMPILED_TOPOLOGY_VIEW_BYTES:,} bytes",
                )
            stream.write(chunk)
            size = observed_at_least
            if size >= next_progress:
                progress(name, size)
                next_progress = (
                    size // _VIEW_PROGRESS_BYTES + 1
                ) * _VIEW_PROGRESS_BYTES
    except CompiledTopologyAssetError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        _fail("COMPILED_TOPOLOGY_CANONICAL_JSON_INVALID", str(exc))
    if is_cancelled():
        _fail(
            "COMPILED_TOPOLOGY_CANCELLED",
            f"compiled topology {name} view encoding was cancelled",
        )
    progress(name, size)
    return stream.getvalue()


def _asset_views(
    certificate: Mapping[str, Any],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str, int], None] | None = None,
    concrete_containers: bool = False,
) -> tuple[tuple[str, bytes], ...]:
    surface, scenario, external = compact_finite_certificate_views(certificate)
    cancelled = is_cancelled or (lambda: False)
    report = progress or (lambda _name, _size: None)
    result: list[tuple[str, bytes]] = []
    for name, payload in (
        ("external", external),
        ("scenario", scenario),
        ("surface", surface),
    ):
        encoded = _bounded_canonical_view_bytes(
            payload,
            name=name,
            is_cancelled=cancelled,
            progress=report,
            concrete_containers=concrete_containers,
        )
        result.append((name, encoded))
    return tuple(result)


def _compress_file(
    path: Path,
    *,
    is_cancelled: Callable[[], bool],
) -> tuple[bytes, int, str]:
    compressor = zlib.compressobj(level=6)
    compressed = bytearray()
    size = 0
    digest = sha256()
    with path.open("rb") as stream:
        while True:
            if is_cancelled():
                _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology persistence was cancelled")
            chunk = stream.read(_STREAM_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_COMPILED_TOPOLOGY_UNCOMPRESSED_BYTES:
                _fail("COMPILED_TOPOLOGY_UNCOMPRESSED_TOO_LARGE", "compiled topology exceeds its size bound")
            digest.update(chunk)
            encoded = compressor.compress(chunk)
            if encoded:
                compressed.extend(encoded)
                if len(compressed) > MAX_COMPILED_TOPOLOGY_COMPRESSED_BYTES:
                    _fail("COMPILED_TOPOLOGY_COMPRESSED_TOO_LARGE", "compiled topology exceeds attachment limit")
    compressed.extend(compressor.flush())
    if len(compressed) > MAX_COMPILED_TOPOLOGY_COMPRESSED_BYTES:
        _fail("COMPILED_TOPOLOGY_COMPRESSED_TOO_LARGE", "compiled topology exceeds attachment limit")
    if size > _EXPANSION_GRACE_BYTES and size > max(1, len(compressed)) * MAX_COMPILED_TOPOLOGY_EXPANSION_RATIO:
        _fail("COMPILED_TOPOLOGY_EXPANSION_RATIO_EXCEEDED", "compiled topology compression ratio is unsafe")
    return bytes(compressed), size, digest.hexdigest()


def build_compiled_topology_asset(
    project: Any,
    certificate: Mapping[str, Any],
    *,
    progress: Callable[[int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    concrete_containers: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, bytes]]:
    """Verify/attest one inline v4 certificate and persist its safe fast path."""

    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    # Keep this persistence module directly importable while
    # ``_core.solver.__init__`` is still initializing.  Numerical types are
    # needed only when a complete asset is actually built.
    from ._core.solver.finite_via_layerwise import (
        FiniteViaCertificateError,
        compile_finite_via_base_topology,
    )
    from ._core.solver.layer_surface_network import LayerSurfaceNetworkError
    if cancelled():
        _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology persistence was cancelled")

    def phase_progress(
        start: int, end: int, message: str
    ) -> Callable[[int], None]:
        last_value = start - 1

        def update(processed_bytes: int) -> None:
            nonlocal last_value
            value = min(
                end - 1,
                start
                + int(
                    (end - start)
                    * processed_bytes
                    / max(1, MAX_SURFACE_CERTIFICATE_COMPILED_ONLY_IDENTITY_BYTES)
                ),
            )
            if value > last_value:
                report(value, message)
                last_value = value

        return update

    try:
        report(0, "Verifying canonical finite-Via certificate evidence")
        certificate = validate_inline_surface_certificate(
            certificate,
            is_cancelled=cancelled,
            progress=phase_progress(
                0, 4, "Verifying canonical finite-Via certificate evidence"
            ),
            concrete_containers=concrete_containers,
        )
        # Derive the canonical full-certificate identity inside this public
        # trust boundary.  Callers cannot supply an arbitrary descriptor hash
        # and have it labelled as fully verified in the compact views.
        report(4, "Binding canonical certificate identity")
        surface_stub = _compiled_only_stub_from_verified_inline(
            certificate,
            is_cancelled=cancelled,
            progress=phase_progress(4, 8, "Binding canonical certificate identity"),
            concrete_containers=concrete_containers,
        )
        surface_identity = validated_surface_certificate_storage_stub(surface_stub)
    except SurfaceCertificateAssetError as exc:
        if exc.code == "SURFACE_CERTIFICATE_CANCELLED":
            _fail("COMPILED_TOPOLOGY_CANCELLED", str(exc))
        _fail(
            "COMPILED_TOPOLOGY_INPUT_UNSUPPORTED",
            f"compiled topology requires verified v4 input [{exc.code}]: {exc}",
        )
    if (
        certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or certificate.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
        or str(certificate.get("status", "")).strip().casefold() != "complete"
        or str(certificate.get("source_sha256", "")).strip().casefold()
        != surface_identity["source_sha256"]
        or str(certificate.get("evidence_sha256", "")).strip().casefold()
        != surface_identity["evidence_sha256"]
    ):
        _fail("COMPILED_TOPOLOGY_INPUT_UNSUPPORTED", "compiled topology requires one exact verified v4 input")
    spd_import = _spd_import(project)
    retained_source = str(spd_import.get("source_sha256", "")).strip().casefold()
    if (
        not _is_sha256(retained_source)
        or retained_source != surface_identity["source_sha256"]
    ):
        _fail(
            "COMPILED_TOPOLOGY_CERTIFICATE_MISMATCH",
            "compiled topology input is bound to a different project SPD source",
        )
    records_raw = spd_import.get("plane_geometries", ())
    if not isinstance(records_raw, Sequence) or isinstance(records_raw, (str, bytes)):
        _fail("COMPILED_TOPOLOGY_PROJECT_INVALID", "plane geometry manifest is absent")
    records = tuple(item for item in records_raw if isinstance(item, Mapping))
    if not records or len(records) != len(records_raw):
        _fail("COMPILED_TOPOLOGY_PROJECT_INVALID", "plane geometry manifest is incomplete")
    artwork = tuple(
        str(island).strip()
        for record in records
        for island in (
            record.get("island_ids", ())
            if isinstance(record.get("island_ids"), Sequence)
            and not isinstance(record.get("island_ids"), (str, bytes))
            else ()
        )
    )
    report(8, "Validating finite-Via topology for compact persistence")
    compile_project = project
    if isinstance(project, Mapping):
        # Normal raw-SPD import calls this helper with ProjectSpec before any
        # ScenarioSpec copy.  This compatibility branch is only for callers
        # saving an already materialised inline scenario.
        from ._core.domain import ProjectSpec

        compile_project = ProjectSpec.model_validate(project)
    try:
        topology = compile_finite_via_base_topology(
            compile_project,
            records,
            artwork,
            required_rail_id=None,
            certificate=certificate,
            certificate_verified=True,
            is_cancelled=cancelled,
            progress=lambda value, message: report(
                8 + round(max(0, min(100, value)) * 10 / 100), message
            ),
        )
    except FiniteViaCertificateError as exc:
        raise CompiledTopologyAssetError(exc.code, str(exc)) from exc
    report(18, "Finite-Via topology validation complete")
    source = str(topology.source_sha256).casefold()
    evidence = str(topology.certificate_evidence_sha256).casefold()
    topology_identity = topology.topology_identity_sha256
    surface_uncompressed = _hash(
        surface_identity.get("uncompressed_sha256"), label="surface certificate bytes"
    )
    surface_uncompressed_size = int(surface_identity["uncompressed_size_bytes"])
    project_binding = _project_binding_sha256(
        project, source_sha256=source, evidence_sha256=evidence
    )
    node_values = (
        tuple(topology.quotient_vertex_ids),
        tuple(topology.external_port_node_ids),
        tuple(sorted(artwork)),
    )
    flat_nodes = tuple(item for group in node_values for item in group)
    if len(flat_nodes) > MAX_COMPILED_TOPOLOGY_NODES or len({item.casefold() for item in flat_nodes}) != len(flat_nodes):
        _fail("COMPILED_TOPOLOGY_NODE_INVALID", "compiled nodes are duplicated or exceed bounds")
    node_index = {value: index for index, value in enumerate(flat_nodes)}
    views = _asset_views(
        certificate,
        is_cancelled=cancelled,
        concrete_containers=concrete_containers,
        progress=lambda name, size: report(
            19,
            f"Encoding {name} compact certificate view ({size:,} byte(s))",
        ),
    )
    all_links = (tuple(topology.topology_links), tuple(topology.finite_links))
    landing_groups = (
        tuple(topology.vertex_by_landing_key.items()),
        tuple(topology.first_edge_by_landing_key.items()),
    )
    counts = {
        "node_count": sum(len(group) for group in node_values),
        "topology_link_count": len(topology.topology_links),
        "finite_link_count": len(topology.finite_links),
        "owner_count": sum(
            len(link.owner_ids) for group in all_links for link in group
        ),
        "port_count": len(topology.rail_ports),
        "port_member_count": sum(
            len(values)
            for port in topology.rail_ports
            for values in (
                port.positive_pin_ids,
                port.negative_pin_ids,
                port.positive_anchor_node_ids,
                port.negative_anchor_node_ids,
            )
        ),
        "omitted_rail_count": len(topology.omitted_rail_ids),
        "landing_count": sum(len(group) for group in landing_groups),
        "view_count": len(views),
    }
    prewrite_bounds = {
        "node_count": MAX_COMPILED_TOPOLOGY_NODES,
        "owner_count": MAX_COMPILED_TOPOLOGY_OWNERS,
        "port_count": MAX_COMPILED_TOPOLOGY_PORTS,
        "port_member_count": MAX_COMPILED_TOPOLOGY_PORT_MEMBERS,
        "omitted_rail_count": MAX_COMPILED_TOPOLOGY_PORTS,
        "landing_count": MAX_COMPILED_TOPOLOGY_LANDINGS,
        "view_count": 3,
    }
    if (
        counts["topology_link_count"] + counts["finite_link_count"]
        > MAX_COMPILED_TOPOLOGY_LINKS
        or any(counts[key] > maximum for key, maximum in prewrite_bounds.items())
        or counts["view_count"] != 3
    ):
        _fail(
            "COMPILED_TOPOLOGY_BOUND_EXCEEDED",
            "compiled topology row inventory exceeds a persistence safety bound",
        )
    with tempfile.TemporaryDirectory(prefix="spdpi-compiled-topology-") as temp_dir:
        database_path = Path(temp_dir) / "topology.sqlite"
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={_USER_VERSION}")
            connection.executescript(_CREATE_SQL)
            report(20, "Writing compact topology nodes and links")
            for kind, values in enumerate(node_values):
                _insert_batches(
                    connection,
                    "INSERT INTO nodes(kind,ordinal,node_id) VALUES(?,?,?)",
                    ((kind, ordinal, value) for ordinal, value in enumerate(values)),
                    cancelled=cancelled,
                )
            for kind, links in enumerate(all_links):
                _insert_batches(
                    connection,
                    "INSERT INTO links(kind,ordinal,link_id,first_node,second_node,parallel_count,resistance_ohm,inductance_h) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        (
                            kind,
                            ordinal,
                            link.link_id,
                            node_index[link.first_node_id],
                            node_index[link.second_node_id],
                            link.count,
                            link.resistance_ohm_per_via,
                            link.inductance_h_per_via,
                        )
                        for ordinal, link in enumerate(links)
                    ),
                    cancelled=cancelled,
                )
                _insert_batches(
                    connection,
                    "INSERT INTO link_owners(kind,link_ordinal,owner_ordinal,owner_id) VALUES(?,?,?,?)",
                    (
                        (kind, link_ordinal, owner_ordinal, owner)
                        for link_ordinal, link in enumerate(links)
                        for owner_ordinal, owner in enumerate(link.owner_ids)
                    ),
                    cancelled=cancelled,
                )
            report(60, "Writing compact rail, landing and scenario views")
            _insert_batches(
                connection,
                "INSERT INTO rail_ports(ordinal,rail_id,selected_net,reference_net,positive_node,negative_node) VALUES(?,?,?,?,?,?)",
                (
                    (
                        ordinal,
                        port.rail_id,
                        port.selected_net,
                        port.reference_net,
                        node_index[port.positive_node_id],
                        node_index[port.negative_node_id],
                    )
                    for ordinal, port in enumerate(topology.rail_ports)
                ),
                cancelled=cancelled,
            )
            member_rows = (
                (port_ordinal, kind, ordinal, value)
                for port_ordinal, port in enumerate(topology.rail_ports)
                for kind, values in enumerate(
                    (
                        port.positive_pin_ids,
                        port.negative_pin_ids,
                        port.positive_anchor_node_ids,
                        port.negative_anchor_node_ids,
                    )
                )
                for ordinal, value in enumerate(values)
            )
            _insert_batches(
                connection,
                "INSERT INTO rail_port_members(port_ordinal,kind,ordinal,value) VALUES(?,?,?,?)",
                member_rows,
                cancelled=cancelled,
            )
            _insert_batches(
                connection,
                "INSERT INTO omitted_rails(ordinal,rail_id) VALUES(?,?)",
                ((ordinal, value) for ordinal, value in enumerate(topology.omitted_rail_ids)),
                cancelled=cancelled,
            )
            for kind, values in enumerate(landing_groups):
                ordered = sorted(values, key=lambda item: (item[0][0], item[0][1]))
                _insert_batches(
                    connection,
                    "INSERT INTO landing_bindings(kind,ordinal,first_key,second_key,value) VALUES(?,?,?,?,?)",
                    (
                        (kind, ordinal, key[0], key[1], value)
                        for ordinal, (key, value) in enumerate(ordered)
                    ),
                    cancelled=cancelled,
                )
            connection.executemany(
                "INSERT INTO views(name,payload,payload_size,payload_sha256) VALUES(?,?,?,?)",
                (
                    (name, payload, len(payload), sha256(payload).hexdigest())
                    for name, payload in views
                ),
            )
            meta = {
                "payload_schema": COMPILED_TOPOLOGY_PAYLOAD_SCHEMA,
                "compiler_id": COMPILED_TOPOLOGY_COMPILER_ID,
                "surface_schema_version": FINITE_VIA_SURFACE_SCHEMA,
                "surface_compiler_id": FINITE_VIA_SURFACE_COMPILER,
                "source_sha256": source,
                "certificate_evidence_sha256": evidence,
                "surface_asset_uncompressed_size_bytes": str(surface_uncompressed_size),
                "surface_asset_uncompressed_sha256": surface_uncompressed,
                "project_binding_sha256": project_binding,
                "topology_identity_sha256": topology_identity,
                **{
                    key: str(value)
                    for key, value in counts.items()
                    if key != "port_member_count"
                },
            }
            connection.executemany(
                "INSERT INTO meta(key,value) VALUES(?,?)", sorted(meta.items())
            )
            connection.commit()
            logical = _logical_rows_sha256(
                connection, is_cancelled=cancelled
            )
            connection.execute(
                "INSERT INTO meta(key,value) VALUES('logical_rows_sha256',?)", (logical,)
            )
            connection.commit()
        except (KeyError, sqlite3.Error, LayerSurfaceNetworkError) as exc:
            _fail("COMPILED_TOPOLOGY_BUILD_FAILED", str(exc))
        finally:
            connection.close()
        # The SQLite file now owns the complete typed rows.  Release the
        # compiler graph, node index and compact-view byte strings before the
        # compressed attachment buffer is allocated.
        del topology
        del compile_project
        del node_index
        del flat_nodes
        del node_values
        del views
        del all_links
        del landing_groups
        del links
        del member_rows
        del ordered
        report(85, "Compressing compiled topology attachment")
        compressed, uncompressed_size, uncompressed_sha = _compress_file(
            database_path,
            is_cancelled=cancelled,
        )
    name = (
        f"{COMPILED_TOPOLOGY_ASSET_PREFIX}/layerwise-compiled-topology-v1-"
        f"{evidence[:16]}.sqlite.zlib"
    )
    manifest = {
        "storage_schema": COMPILED_TOPOLOGY_ASSET_SCHEMA,
        "payload_schema": COMPILED_TOPOLOGY_PAYLOAD_SCHEMA,
        "compiler_id": COMPILED_TOPOLOGY_COMPILER_ID,
        "surface_schema_version": FINITE_VIA_SURFACE_SCHEMA,
        "surface_compiler_id": FINITE_VIA_SURFACE_COMPILER,
        "source_sha256": source,
        "certificate_evidence_sha256": evidence,
        "surface_asset_uncompressed_size_bytes": surface_uncompressed_size,
        "surface_asset_uncompressed_sha256": surface_uncompressed,
        "project_binding_sha256": project_binding,
        "topology_identity_sha256": topology_identity,
        "logical_rows_sha256": logical,
        "asset_name": name,
        "compression": COMPILED_TOPOLOGY_COMPRESSION,
        "compressed_size_bytes": len(compressed),
        "compressed_sha256": sha256(compressed).hexdigest(),
        "uncompressed_size_bytes": uncompressed_size,
        "uncompressed_sha256": uncompressed_sha,
    }
    report(100, "Compiled topology attachment is ready")
    return surface_stub, manifest, (name, compressed)


def _decompress_to_file(
    compressed: bytes,
    manifest: Mapping[str, Any],
    destination: Path,
    *,
    is_cancelled: Callable[[], bool],
) -> None:
    expected_size = int(manifest["uncompressed_size_bytes"])
    decompressor = zlib.decompressobj()
    written = 0
    digest = sha256()
    cursor = 0
    try:
        with destination.open("wb") as stream:
            while cursor < len(compressed):
                if is_cancelled():
                    _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
                pending = compressed[cursor : cursor + _STREAM_BYTES]
                cursor += len(pending)
                while pending:
                    if is_cancelled():
                        _fail(
                            "COMPILED_TOPOLOGY_CANCELLED",
                            "compiled topology load was cancelled",
                        )
                    produced = decompressor.decompress(pending, _STREAM_BYTES)
                    if produced:
                        written += len(produced)
                        if written > expected_size:
                            _fail("COMPILED_TOPOLOGY_UNCOMPRESSED_TOO_LARGE", "asset expanded beyond declared size")
                        digest.update(produced)
                        stream.write(produced)
                    if decompressor.unused_data:
                        _fail("COMPILED_TOPOLOGY_STREAM_INVALID", "asset has trailing compressed data")
                    pending = decompressor.unconsumed_tail
            tail = decompressor.flush()
            if tail:
                written += len(tail)
                if written > expected_size:
                    _fail("COMPILED_TOPOLOGY_UNCOMPRESSED_TOO_LARGE", "asset expanded beyond declared size")
                digest.update(tail)
                stream.write(tail)
    except zlib.error as exc:
        _fail("COMPILED_TOPOLOGY_STREAM_INVALID", str(exc))
    if not decompressor.eof or written != expected_size:
        _fail("COMPILED_TOPOLOGY_UNCOMPRESSED_SIZE_MISMATCH", "asset did not expand to declared size")
    if digest.hexdigest() != manifest["uncompressed_sha256"]:
        _fail("COMPILED_TOPOLOGY_UNCOMPRESSED_INTEGRITY_FAILED", "asset uncompressed SHA-256 differs")


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.as_posix(), safe='/:')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


@lru_cache(maxsize=1)
def _expected_database_schema_signature() -> tuple[
    tuple[str, str, str], ...,
    tuple[tuple[str, tuple[tuple[Any, ...], ...]], ...],
]:
    """Build the exact trusted schema signature from the local static DDL."""

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
                tuple(
                    tuple(row[1:])
                    for row in reference.execute(f"PRAGMA table_xinfo({name})")
                ),
            )
            for _type, name, _sql in objects
        )
        return objects, xinfo
    finally:
        reference.close()


def _validate_database_schema(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID:
        _fail("COMPILED_TOPOLOGY_DATABASE_INVALID", "database application ID differs")
    if connection.execute("PRAGMA user_version").fetchone()[0] != _USER_VERSION:
        _fail("COMPILED_TOPOLOGY_DATABASE_INVALID", "database version differs")
    expected_objects, expected_xinfo = _expected_database_schema_signature()
    objects = tuple(
        connection.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    )
    if objects != expected_objects:
        _fail("COMPILED_TOPOLOGY_DATABASE_SCHEMA_INVALID", "database has missing or extra schema objects")
    for table, expected in expected_xinfo:
        # table_xinfo includes generated/hidden columns which table_info omits,
        # plus declared type, NOT NULL and PK ordinals.  Compare it before any
        # integrity walk or data query so an attachment cannot smuggle an
        # executable/generated schema extension past the fixed-table contract.
        observed = tuple(
            tuple(row[1:])
            for row in connection.execute(f"PRAGMA table_xinfo({table})")
        )
        if observed != expected:
            _fail(
                "COMPILED_TOPOLOGY_DATABASE_SCHEMA_INVALID",
                f"table {table!r} schema differs",
            )
    integrity = connection.execute("PRAGMA integrity_check(1)").fetchone()
    if integrity != ("ok",):
        _fail("COMPILED_TOPOLOGY_DATABASE_INTEGRITY_FAILED", "SQLite integrity check failed")


def _validate_database_cell_bounds(connection: sqlite3.Connection) -> None:
    """Bound dynamic SQLite cells before any value enters Python."""

    text_columns = {
        "meta": ("key", "value"),
        "nodes": ("node_id",),
        "links": ("link_id",),
        "link_owners": ("owner_id",),
        "rail_ports": ("rail_id", "selected_net", "reference_net"),
        "rail_port_members": ("value",),
        "omitted_rails": ("rail_id",),
        "landing_bindings": ("first_key", "second_key", "value"),
        "views": ("name", "payload_sha256"),
    }
    for table, columns in text_columns.items():
        invalid_type = connection.execute(
            f"SELECT 1 FROM {table} WHERE "
            + " OR ".join(f"typeof({column}) <> 'text'" for column in columns)
            + " LIMIT 1"
        ).fetchone()
        if invalid_type is not None:
            _fail(
                "COMPILED_TOPOLOGY_CELL_TYPE_INVALID",
                f"table {table!r} contains a non-text value in a text column",
            )
        expressions = ",".join(
            f"COALESCE(MAX(length({column})),0)"
            for column in columns
        )
        maxima = connection.execute(f"SELECT {expressions} FROM {table}").fetchone()
        if maxima is None or any(
            type(value) is not int
            or value < 0
            or value > MAX_COMPILED_TOPOLOGY_TEXT_BYTES
            for value in maxima
        ):
            _fail(
                "COMPILED_TOPOLOGY_TEXT_TOO_LARGE",
                f"table {table!r} contains an oversized dynamic text cell",
            )
    integer_columns = {
        "nodes": ("kind", "ordinal"),
        "links": ("kind", "ordinal", "first_node", "second_node", "parallel_count"),
        "link_owners": ("kind", "link_ordinal", "owner_ordinal"),
        "rail_ports": ("ordinal", "positive_node", "negative_node"),
        "rail_port_members": ("port_ordinal", "kind", "ordinal"),
        "omitted_rails": ("ordinal",),
        "landing_bindings": ("kind", "ordinal"),
        "views": ("payload_size",),
    }
    for table, columns in integer_columns.items():
        invalid_type = connection.execute(
            f"SELECT 1 FROM {table} WHERE "
            + " OR ".join(f"typeof({column}) <> 'integer'" for column in columns)
            + " LIMIT 1"
        ).fetchone()
        if invalid_type is not None:
            _fail(
                "COMPILED_TOPOLOGY_CELL_TYPE_INVALID",
                f"table {table!r} contains a non-integer value in an integer column",
            )
    invalid_real = connection.execute(
        "SELECT 1 FROM links WHERE "
        "typeof(resistance_ohm) NOT IN ('null','integer','real') OR "
        "typeof(inductance_h) NOT IN ('null','integer','real') LIMIT 1"
    ).fetchone()
    if invalid_real is not None:
        _fail(
            "COMPILED_TOPOLOGY_CELL_TYPE_INVALID",
            "table 'links' contains a non-numeric value in a real column",
        )
    invalid_payload = connection.execute(
        "SELECT 1 FROM views WHERE typeof(payload) <> 'blob' LIMIT 1"
    ).fetchone()
    if invalid_payload is not None:
        _fail(
            "COMPILED_TOPOLOGY_CELL_TYPE_INVALID",
            "table 'views' contains a non-BLOB compact view",
        )
    payload_maximum = connection.execute(
        "SELECT COALESCE(MAX(length(payload)),0) FROM views"
    ).fetchone()
    if (
        payload_maximum is None
        or type(payload_maximum[0]) is not int
        or payload_maximum[0] < 0
        or payload_maximum[0] > MAX_COMPILED_TOPOLOGY_VIEW_BYTES
    ):
        _fail(
            "COMPILED_TOPOLOGY_VIEW_TOO_LARGE",
            "compiled topology contains an oversized compact view cell",
        )


def _validated_actual_counts(
    connection: sqlite3.Connection, counts: Mapping[str, int]
) -> None:
    """Reject oversized/inconsistent row sets before their logical hash walk."""

    actual_counts = {
        "node_count": connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
        "topology_link_count": connection.execute(
            "SELECT COUNT(*) FROM links WHERE kind=0"
        ).fetchone()[0],
        "finite_link_count": connection.execute(
            "SELECT COUNT(*) FROM links WHERE kind=1"
        ).fetchone()[0],
        "owner_count": connection.execute("SELECT COUNT(*) FROM link_owners").fetchone()[0],
        "port_count": connection.execute("SELECT COUNT(*) FROM rail_ports").fetchone()[0],
        "omitted_rail_count": connection.execute(
            "SELECT COUNT(*) FROM omitted_rails"
        ).fetchone()[0],
        "landing_count": connection.execute(
            "SELECT COUNT(*) FROM landing_bindings"
        ).fetchone()[0],
        "view_count": connection.execute("SELECT COUNT(*) FROM views").fetchone()[0],
    }
    maxima = {
        "node_count": MAX_COMPILED_TOPOLOGY_NODES,
        "topology_link_count": MAX_COMPILED_TOPOLOGY_LINKS,
        "finite_link_count": MAX_COMPILED_TOPOLOGY_LINKS,
        "owner_count": MAX_COMPILED_TOPOLOGY_OWNERS,
        "port_count": MAX_COMPILED_TOPOLOGY_PORTS,
        "omitted_rail_count": MAX_COMPILED_TOPOLOGY_PORTS,
        "landing_count": MAX_COMPILED_TOPOLOGY_LANDINGS,
        "view_count": 3,
    }
    if any(
        type(actual_counts[name]) is not int
        or actual_counts[name] < 0
        or actual_counts[name] > maximum
        for name, maximum in maxima.items()
    ):
        _fail(
            "COMPILED_TOPOLOGY_BOUND_EXCEEDED",
            "compiled topology actual row counts exceed a safety bound",
        )
    member_count = connection.execute(
        "SELECT COUNT(*) FROM rail_port_members"
    ).fetchone()[0]
    if (
        type(member_count) is not int
        or member_count < 0
        or member_count > MAX_COMPILED_TOPOLOGY_PORT_MEMBERS
    ):
        _fail(
            "COMPILED_TOPOLOGY_BOUND_EXCEEDED",
            "port member rows exceed their safety bound",
        )
    if actual_counts != dict(counts):
        _fail(
            "COMPILED_TOPOLOGY_DATABASE_COUNT_MISMATCH",
            "database row counts differ from metadata",
        )


def _canonical_view_matches_raw(
    payload: Mapping[str, Any],
    raw: bytes,
    *,
    name: str,
    is_cancelled: Callable[[], bool],
) -> bool:
    """Compare a decoded concrete view with its raw canonical bytes in place."""

    offset = 0
    raw_view = memoryview(raw)
    try:
        for chunk_index, chunk in enumerate(
            iter_concrete_canonical_json_bytes(payload)
        ):
            if (
                chunk_index % _VIEW_CANCEL_CHECK_CHUNKS == 0
                and is_cancelled()
            ):
                _fail(
                    "COMPILED_TOPOLOGY_CANCELLED",
                    f"compiled topology {name} view validation was cancelled",
                )
            end = offset + len(chunk)
            if end > len(raw_view) or raw_view[offset:end] != chunk:
                return False
            offset = end
    except CompiledTopologyAssetError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        _fail("COMPILED_TOPOLOGY_CANONICAL_JSON_INVALID", str(exc))
    if is_cancelled():
        _fail(
            "COMPILED_TOPOLOGY_CANCELLED",
            f"compiled topology {name} view validation was cancelled",
        )
    return offset == len(raw_view)


def _strict_view(
    raw: bytes,
    *,
    name: str,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("COMPILED_TOPOLOGY_VIEW_DUPLICATE_KEY", f"view {name!r} duplicates {key!r}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=reject_duplicates)
    except CompiledTopologyAssetError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("COMPILED_TOPOLOGY_VIEW_INVALID", f"view {name!r} is invalid JSON: {exc}")
    if not isinstance(payload, dict) or not _canonical_view_matches_raw(
        payload,
        raw,
        name=name,
        is_cancelled=is_cancelled or (lambda: False),
    ):
        _fail("COMPILED_TOPOLOGY_VIEW_NONCANONICAL", f"view {name!r} is not canonical JSON")
    return payload


def _meta_counts(connection: sqlite3.Connection, manifest: Mapping[str, Any]) -> dict[str, int]:
    rows = connection.execute("SELECT key,value FROM meta ORDER BY key").fetchall()
    meta = {str(key): str(value) for key, value in rows}
    if set(meta) != set(_META_KEYS):
        _fail("COMPILED_TOPOLOGY_DATABASE_META_INVALID", "database metadata has missing or extra keys")
    identity_fields = {
        "payload_schema": COMPILED_TOPOLOGY_PAYLOAD_SCHEMA,
        "compiler_id": COMPILED_TOPOLOGY_COMPILER_ID,
        "surface_schema_version": FINITE_VIA_SURFACE_SCHEMA,
        "surface_compiler_id": FINITE_VIA_SURFACE_COMPILER,
        "source_sha256": manifest["source_sha256"],
        "certificate_evidence_sha256": manifest["certificate_evidence_sha256"],
        "surface_asset_uncompressed_size_bytes": manifest[
            "surface_asset_uncompressed_size_bytes"
        ],
        "surface_asset_uncompressed_sha256": manifest["surface_asset_uncompressed_sha256"],
        "project_binding_sha256": manifest["project_binding_sha256"],
        "topology_identity_sha256": manifest["topology_identity_sha256"],
        "logical_rows_sha256": manifest["logical_rows_sha256"],
    }
    if any(meta[key] != str(value) for key, value in identity_fields.items()):
        _fail("COMPILED_TOPOLOGY_DATABASE_BINDING_MISMATCH", "database identity differs from manifest")
    bounds = {
        "node_count": MAX_COMPILED_TOPOLOGY_NODES,
        "topology_link_count": MAX_COMPILED_TOPOLOGY_LINKS,
        "finite_link_count": MAX_COMPILED_TOPOLOGY_LINKS,
        "owner_count": MAX_COMPILED_TOPOLOGY_OWNERS,
        "port_count": MAX_COMPILED_TOPOLOGY_PORTS,
        "omitted_rail_count": MAX_COMPILED_TOPOLOGY_PORTS,
        "landing_count": MAX_COMPILED_TOPOLOGY_LANDINGS,
        "view_count": 3,
    }
    result: dict[str, int] = {}
    for key, maximum in bounds.items():
        try:
            value = int(meta[key])
        except ValueError:
            _fail("COMPILED_TOPOLOGY_DATABASE_META_INVALID", f"{key} is not integer")
        if value < 0 or value > maximum or str(value) != meta[key]:
            _fail("COMPILED_TOPOLOGY_BOUND_EXCEEDED", f"{key} exceeds its bound")
        result[key] = value
    if result["topology_link_count"] + result["finite_link_count"] > MAX_COMPILED_TOPOLOGY_LINKS:
        _fail("COMPILED_TOPOLOGY_BOUND_EXCEEDED", "combined link count exceeds bound")
    return result


def _load_database(
    connection: sqlite3.Connection,
    *,
    project: Any,
    manifest: Mapping[str, Any],
    artwork_node_ids: Sequence[str],
    is_cancelled: Callable[[], bool],
) -> LoadedCompiledTopologyAsset:
    # Lazy numerical imports avoid a package-init cycle for callers that begin
    # with ``import spd_decap_pi.compiled_topology_asset``.
    from ._core.solver.finite_via_layerwise import (
        CompiledFiniteViaBaseTopology,
        FiniteViaCertificateError,
        FiniteViaRailPortEvidence,
        compiled_finite_via_topology_identity_sha256,
    )
    from ._core.solver.layer_surface_network import (
        LayerSurfaceNetworkError,
        LayerSurfaceViaLink,
    )

    _validate_database_schema(connection)
    _validate_database_cell_bounds(connection)
    counts = _meta_counts(connection, manifest)
    _validated_actual_counts(connection, counts)
    if _logical_rows_sha256(
        connection, is_cancelled=is_cancelled
    ) != manifest["logical_rows_sha256"]:
        _fail("COMPILED_TOPOLOGY_LOGICAL_INTEGRITY_FAILED", "logical row hash differs")

    nodes_by_kind: list[list[str]] = [[], [], []]
    node_keys: set[str] = set()
    flat_nodes: list[str] = []
    for row_index, (kind, ordinal, raw_node) in enumerate(
        connection.execute(
            "SELECT kind,ordinal,node_id FROM nodes ORDER BY kind,ordinal"
        )
    ):
        if row_index % _BATCH_ROWS == 0 and is_cancelled():
            _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
        if type(kind) is not int or kind not in (0, 1, 2) or type(ordinal) is not int:
            _fail("COMPILED_TOPOLOGY_NODE_INVALID", "node kind/ordinal is invalid")
        if ordinal != len(nodes_by_kind[kind]):
            _fail("COMPILED_TOPOLOGY_NODE_INVALID", "node ordinals are not contiguous")
        node = _text(raw_node, label="node ID")
        if node.casefold() in node_keys:
            _fail("COMPILED_TOPOLOGY_NODE_INVALID", "node IDs are duplicated")
        node_keys.add(node.casefold())
        nodes_by_kind[kind].append(node)
        flat_nodes.append(node)
    expected_artwork = tuple(sorted(str(item).strip() for item in artwork_node_ids))
    if tuple(nodes_by_kind[2]) != expected_artwork:
        _fail("COMPILED_TOPOLOGY_PROJECT_MISMATCH", "compiled artwork nodes differ from project")

    owner_iterator = iter(
        connection.execute(
            "SELECT kind,link_ordinal,owner_ordinal,owner_id FROM link_owners ORDER BY kind,link_ordinal,owner_ordinal"
        )
    )
    owner_row = next(owner_iterator, None)
    owner_keys: set[str] = set()
    owner_edge_by_id: dict[str, str] = {}
    link_keys: set[str] = set()
    links_by_kind: list[list[LayerSurfaceViaLink]] = [[], []]
    owner_processed = 0
    try:
        for row_index, (
            kind,
            ordinal,
            raw_link_id,
            first_index,
            second_index,
            count,
            resistance,
            inductance,
        ) in enumerate(
            connection.execute(
                "SELECT kind,ordinal,link_id,first_node,second_node,parallel_count,resistance_ohm,inductance_h FROM links ORDER BY kind,ordinal"
            )
        ):
            if row_index % _BATCH_ROWS == 0 and is_cancelled():
                _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
            if type(kind) is not int or kind not in (0, 1) or ordinal != len(links_by_kind[kind]):
                _fail("COMPILED_TOPOLOGY_LINK_INVALID", "link kind/ordinal is invalid")
            if (
                type(first_index) is not int
                or type(second_index) is not int
                or not 0 <= first_index < len(flat_nodes)
                or not 0 <= second_index < len(flat_nodes)
                or type(count) is not int
                or count < 1
            ):
                _fail("COMPILED_TOPOLOGY_LINK_INVALID", "link endpoint/count is invalid")
            link_id = _text(raw_link_id, label="link ID")
            if link_id.casefold() in link_keys:
                _fail("COMPILED_TOPOLOGY_LINK_INVALID", "link IDs are duplicated")
            link_keys.add(link_id.casefold())
            owners: list[str] = []
            while owner_row is not None and owner_row[0:2] == (kind, ordinal):
                owner_ordinal = owner_row[2]
                if owner_processed % _BATCH_ROWS == 0 and is_cancelled():
                    _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
                if type(owner_ordinal) is not int or owner_ordinal != len(owners):
                    _fail("COMPILED_TOPOLOGY_OWNER_INVALID", "owner ordinals are not contiguous")
                owner = _text(owner_row[3], label="owner ID")
                owner_key = owner.casefold()
                if owner_key in owner_keys:
                    _fail("COMPILED_TOPOLOGY_OWNER_INVALID", "owner IDs are duplicated")
                owner_keys.add(owner_key)
                owners.append(owner)
                owner_processed += 1
                if kind == 1:
                    if not owner.startswith("via:"):
                        _fail("COMPILED_TOPOLOGY_OWNER_INVALID", "finite link owner is not a Via")
                    owner_edge_by_id[owner_key] = link_id
                owner_row = next(owner_iterator, None)
            if not owners:
                _fail("COMPILED_TOPOLOGY_OWNER_INVALID", "link has no explicit owner")
            if kind == 0:
                if (
                    count != 1
                    or resistance is not None
                    or inductance is not None
                ):
                    _fail("COMPILED_TOPOLOGY_LINK_INVALID", "topology link carries R/L")
                mode = "topology_only_ideal"
            else:
                if (
                    count > len(owners)
                    or len(owners) % count != 0
                    or isinstance(resistance, bool)
                    or isinstance(inductance, bool)
                    or not isinstance(resistance, (int, float))
                    or not isinstance(inductance, (int, float))
                    or not isfinite(float(resistance))
                    or not isfinite(float(inductance))
                    or float(resistance) < 0.0
                    or float(inductance) < 0.0
                    or (float(resistance) == 0.0 and float(inductance) == 0.0)
                ):
                    _fail("COMPILED_TOPOLOGY_LINK_INVALID", "finite link R/L is invalid")
                mode = "finite_parallel_rl"
            links_by_kind[kind].append(
                LayerSurfaceViaLink(
                    link_id=link_id,
                    first_node_id=flat_nodes[first_index],
                    second_node_id=flat_nodes[second_index],
                    count=count,
                    mode=mode,
                    resistance_ohm_per_via=(None if kind == 0 else float(resistance)),
                    inductance_h_per_via=(None if kind == 0 else float(inductance)),
                    owner_ids=tuple(owners),
                )
            )
    except LayerSurfaceNetworkError as exc:
        _fail("COMPILED_TOPOLOGY_LINK_INVALID", str(exc))
    if owner_row is not None:
        _fail("COMPILED_TOPOLOGY_OWNER_INVALID", "owner references an unknown link")

    members: dict[tuple[int, int], list[str]] = {}
    for row_index, (port_ordinal, kind, ordinal, raw_value) in enumerate(
        connection.execute(
            "SELECT port_ordinal,kind,ordinal,value FROM rail_port_members ORDER BY port_ordinal,kind,ordinal"
        )
    ):
        if row_index % _BATCH_ROWS == 0 and is_cancelled():
            _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
        if (
            type(port_ordinal) is not int
            or not 0 <= port_ordinal < counts["port_count"]
            or type(kind) is not int
            or kind not in (0, 1, 2, 3)
            or type(ordinal) is not int
        ):
            _fail("COMPILED_TOPOLOGY_PORT_INVALID", "port member key is invalid")
        values = members.setdefault((port_ordinal, kind), [])
        if ordinal != len(values):
            _fail("COMPILED_TOPOLOGY_PORT_INVALID", "port member ordinals are not contiguous")
        values.append(_text(raw_value, label="port member"))
    ports: list[FiniteViaRailPortEvidence] = []
    for row_index, (
        ordinal,
        rail_id,
        selected_net,
        reference_net,
        positive,
        negative,
    ) in enumerate(
        connection.execute(
            "SELECT ordinal,rail_id,selected_net,reference_net,positive_node,negative_node FROM rail_ports ORDER BY ordinal"
        )
    ):
        if row_index % _BATCH_ROWS == 0 and is_cancelled():
            _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
        if ordinal != len(ports) or type(positive) is not int or type(negative) is not int:
            _fail("COMPILED_TOPOLOGY_PORT_INVALID", "port ordinal/node is invalid")
        if not 0 <= positive < len(flat_nodes) or not 0 <= negative < len(flat_nodes):
            _fail("COMPILED_TOPOLOGY_PORT_INVALID", "port node is outside topology")
        port = FiniteViaRailPortEvidence(
            rail_id=_text(rail_id, label="rail ID"),
            selected_net=_text(selected_net, label="selected NET"),
            reference_net=_text(reference_net, label="reference NET"),
            positive_node_id=flat_nodes[positive],
            negative_node_id=flat_nodes[negative],
            positive_pin_ids=tuple(members.get((ordinal, 0), ())),
            negative_pin_ids=tuple(members.get((ordinal, 1), ())),
            positive_anchor_node_ids=tuple(members.get((ordinal, 2), ())),
            negative_anchor_node_ids=tuple(members.get((ordinal, 3), ())),
        )
        if (
            not port.positive_pin_ids
            or not port.negative_pin_ids
            or not port.positive_anchor_node_ids
            or not port.negative_anchor_node_ids
            or any(
                value.casefold() not in node_keys
                for value in (
                    *port.positive_anchor_node_ids,
                    *port.negative_anchor_node_ids,
                )
            )
        ):
            _fail("COMPILED_TOPOLOGY_PORT_INVALID", "port evidence is incomplete")
        ports.append(port)
    rail_keys = [item.rail_id.casefold() for item in ports]
    if len(set(rail_keys)) != len(rail_keys):
        _fail("COMPILED_TOPOLOGY_PORT_INVALID", "rail ports are duplicated")

    omitted: list[str] = []
    for row_index, (ordinal, rail_id) in enumerate(
        connection.execute("SELECT ordinal,rail_id FROM omitted_rails ORDER BY ordinal")
    ):
        if row_index % _BATCH_ROWS == 0 and is_cancelled():
            _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
        if ordinal != len(omitted):
            _fail("COMPILED_TOPOLOGY_PORT_INVALID", "omitted rail ordinals are not contiguous")
        omitted.append(_text(rail_id, label="omitted rail ID"))

    landing_maps: list[dict[tuple[str, str], str]] = [{}, {}]
    for row_index, (kind, ordinal, first_key, second_key, raw_value) in enumerate(
        connection.execute(
            "SELECT kind,ordinal,first_key,second_key,value FROM landing_bindings ORDER BY kind,ordinal"
        )
    ):
        if row_index % _BATCH_ROWS == 0 and is_cancelled():
            _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
        if type(kind) is not int or kind not in (0, 1) or ordinal != len(landing_maps[kind]):
            _fail("COMPILED_TOPOLOGY_LANDING_INVALID", "landing ordinal is invalid")
        key = (_text(first_key, label="landing first key"), _text(second_key, label="landing second key"))
        value = _text(raw_value, label="landing binding")
        if key in landing_maps[kind]:
            _fail("COMPILED_TOPOLOGY_LANDING_INVALID", "landing keys are duplicated")
        if kind == 0 and value.casefold() not in node_keys:
            _fail("COMPILED_TOPOLOGY_LANDING_INVALID", "landing vertex is unknown")
        if kind == 1 and value.casefold() not in link_keys:
            _fail("COMPILED_TOPOLOGY_LANDING_INVALID", "landing edge is unknown")
        landing_maps[kind][key] = value

    views: dict[str, dict[str, Any]] = {}
    for row_index, (name, raw_payload, payload_size, payload_sha) in enumerate(
        connection.execute(
            "SELECT name,payload,payload_size,payload_sha256 FROM views ORDER BY name"
        )
    ):
        if row_index % _BATCH_ROWS == 0 and is_cancelled():
            _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
        name = _text(name, label="view name")
        if name not in {"external", "scenario", "surface"} or name in views:
            _fail("COMPILED_TOPOLOGY_VIEW_INVALID", "view name is invalid")
        if not isinstance(raw_payload, bytes) or type(payload_size) is not int:
            _fail("COMPILED_TOPOLOGY_VIEW_INVALID", "view payload type is invalid")
        if payload_size != len(raw_payload) or payload_size > MAX_COMPILED_TOPOLOGY_VIEW_BYTES:
            _fail("COMPILED_TOPOLOGY_VIEW_INVALID", "view payload size differs")
        if _hash(payload_sha, label="view payload") != sha256(raw_payload).hexdigest():
            _fail("COMPILED_TOPOLOGY_VIEW_INVALID", "view payload hash differs")
        views[name] = _strict_view(
            raw_payload,
            name=name,
            is_cancelled=is_cancelled,
        )
    if set(views) != {"external", "scenario", "surface"}:
        _fail("COMPILED_TOPOLOGY_VIEW_INVALID", "required compact views are absent")
    for view_name, view in views.items():
        _validated_compact_view(
            view,
            name=view_name,
            manifest=manifest,
            concrete_containers=True,
            is_cancelled=is_cancelled,
        )

    try:
        topology_identity = compiled_finite_via_topology_identity_sha256(
            certificate_evidence_sha256=str(
                manifest["certificate_evidence_sha256"]
            ),
            artwork_node_ids=expected_artwork,
            quotient_vertex_ids=nodes_by_kind[0],
            external_port_node_ids=nodes_by_kind[1],
            topology_links=links_by_kind[0],
            finite_links=links_by_kind[1],
            rail_ports=ports,
            omitted_rail_ids=omitted,
            is_cancelled=is_cancelled,
        )
    except FiniteViaCertificateError as exc:
        if exc.code == "COMPILED_TOPOLOGY_CANCELLED" or is_cancelled():
            _fail(
                "COMPILED_TOPOLOGY_CANCELLED",
                "compiled topology load was cancelled",
            )
        _fail("COMPILED_TOPOLOGY_IDENTITY_INVALID", str(exc))
    if topology_identity != manifest["topology_identity_sha256"]:
        _fail("COMPILED_TOPOLOGY_IDENTITY_MISMATCH", "decoded numerical topology identity differs")
    if is_cancelled():
        _fail(
            "COMPILED_TOPOLOGY_CANCELLED",
            "compiled topology load was cancelled",
        )
    surface = _freeze_owned_compact_certificate_view(views["surface"])
    topology = CompiledFiniteViaBaseTopology(
        certificate=surface,
        certificate_evidence_sha256=str(manifest["certificate_evidence_sha256"]),
        source_sha256=str(manifest["source_sha256"]),
        quotient_vertex_ids=tuple(nodes_by_kind[0]),
        external_port_node_ids=tuple(nodes_by_kind[1]),
        topology_links=tuple(links_by_kind[0]),
        finite_links=tuple(links_by_kind[1]),
        rail_ports=tuple(ports),
        omitted_rail_ids=tuple(omitted),
        owner_edge_by_id=MappingProxyType(owner_edge_by_id),
        vertex_by_landing_key=MappingProxyType(landing_maps[0]),
        first_edge_by_landing_key=MappingProxyType(landing_maps[1]),
        topology_identity_sha256=topology_identity,
    )
    return LoadedCompiledTopologyAsset(
        topology=topology,
        scenario_certificate_view=_freeze_owned_compact_certificate_view(
            views["scenario"]
        ),
        external_port_proof_view=_freeze_owned_compact_certificate_view(
            views["external"]
        ),
    )


def load_compiled_topology_asset(
    project: Any,
    attachments: Mapping[str, bytes],
    artwork_node_ids: Sequence[str],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    storage_envelope_verified: bool = False,
) -> LoadedCompiledTopologyAsset | None:
    """Load the optional fast path without hydrating the canonical certificate."""

    raw_manifest = _compiled_manifest(project)
    if raw_manifest is None:
        return None
    cancelled = is_cancelled or (lambda: False)
    if cancelled():
        _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
    manifest = _validated_manifest(project, raw_manifest)
    compressed = _attachment(
        attachments,
        manifest,
        digest_already_verified=storage_envelope_verified,
    )
    with tempfile.TemporaryDirectory(prefix="spdpi-compiled-topology-read-") as temp_dir:
        database_path = Path(temp_dir) / "topology.sqlite"
        _decompress_to_file(
            compressed,
            manifest,
            database_path,
            is_cancelled=cancelled,
        )
        try:
            connection = _open_readonly(database_path)
            try:
                connection.set_progress_handler(
                    lambda: 1 if cancelled() else 0,
                    _BATCH_ROWS,
                )
                return _load_database(
                    connection,
                    project=project,
                    manifest=manifest,
                    artwork_node_ids=artwork_node_ids,
                    is_cancelled=cancelled,
                )
            finally:
                connection.close()
        except sqlite3.Error as exc:
            if cancelled():
                _fail("COMPILED_TOPOLOGY_CANCELLED", "compiled topology load was cancelled")
            _fail("COMPILED_TOPOLOGY_DATABASE_INVALID", str(exc))


__all__ = [
    "COMPILED_TOPOLOGY_ASSET_METADATA_KEY",
    "COMPILED_TOPOLOGY_ASSET_SCHEMA",
    "COMPILED_TOPOLOGY_COMPILER_ID",
    "COMPILED_TOPOLOGY_PAYLOAD_SCHEMA",
    "CompiledTopologyAssetError",
    "LoadedCompiledTopologyAsset",
    "build_compiled_topology_asset",
    "compact_finite_certificate_views",
    "load_compiled_topology_asset",
    "validate_compiled_topology_asset_envelope",
]
