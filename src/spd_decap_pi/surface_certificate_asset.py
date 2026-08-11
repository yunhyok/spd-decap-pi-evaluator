"""Bounded persistence for verified v4 layer-surface certificates.

The finite-Via certificate can be much larger than ``scenario.json``.  Small
certificates may be retained as a hash-bound zlib attachment.  Complete
production certificates are instead represented by a canonical size/hash
attestation and a required compiled SQLite topology.  The latter preserves all
numerical and mutable-scenario views without writing multi-GiB JSON into the
project archive.  Legacy inline v3/v4 and attachment-backed v4 certificates
remain readable.

Both storage modes are fail-closed, source-bound and share one joint envelope
gate with the compiled topology descriptor.  A compiled-only certificate can
never fall back to hydration or to an unverified rebuild.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
import json
from typing import Any, Callable, Final
import zlib
from threading import RLock

from .canonical_json import (
    iter_canonical_json_bytes,
    iter_concrete_canonical_json_bytes,
)


# Keep persistence importable without loading ``_core.solver.__init__`` (which
# exports layerwise_network and therefore imports this module).  Contract tests
# assert these wire identifiers remain equal to the numerical consumer's
# constants.
FINITE_VIA_SURFACE_SCHEMA: Final = "spd-layer-surface-connectivity-v4"
FINITE_VIA_SURFACE_COMPILER: Final = (
    "powersi-same-layer-trace-artwork-finite-via-quotient-v4"
)


SURFACE_CERTIFICATE_METADATA_KEY: Final = (
    "layerwise_surface_connectivity_certificate"
)
SURFACE_CERTIFICATE_ASSET_SCHEMA: Final = (
    "spd-layer-surface-connectivity-certificate-asset-v1"
)
SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA: Final = (
    "spd-layer-surface-connectivity-certificate-compiled-only-v1"
)
SURFACE_CERTIFICATE_COMPRESSION: Final = "zlib"
SURFACE_CERTIFICATE_ASSET_PREFIX: Final = "topology"

# The compressed member must also fit scenario_io.MAX_SCENARIO_MEMBER_BYTES.
# Both supplied production SPDs crossed the earlier 384 MiB cap.  Keep a finite
# 1 GiB ceiling (plus the independent 8 GiB expansion and 128x ratio gates) so
# the measured sources fit without turning the archive into an unbounded
# container.
# The supplied 260729 source proves that its canonical JSON is larger than
# 4 GiB.  Keep an explicit 8 GiB storage bound, but never hydrate more than
# 1 GiB in-process: production v4 bundles carry the hash-bound compiled SQLite
# topology and must use that fast path.  The ratio gate prevents a tiny member
# from claiming the storage allowance.
MAX_SURFACE_CERTIFICATE_COMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_SURFACE_CERTIFICATE_HYDRATION_BYTES = 1024 * 1024 * 1024
MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO = 128
_EXPANSION_RATIO_GRACE_BYTES: Final = 1024 * 1024
_STREAM_CHUNK_BYTES: Final = 1024 * 1024
_STREAM_CANCEL_CHECK_BYTES: Final = 64 * 1024 * 1024
_COMPRESSION_LEVEL: Final = 6
_HYDRATED_CERTIFICATE_CACHE_LIMIT: Final = 1
_HYDRATED_CERTIFICATE_CACHE: "OrderedDict[str, Mapping[str, Any]]" = OrderedDict()
_HYDRATED_CERTIFICATE_CACHE_LOCK = RLock()

_STUB_KEYS: Final = frozenset(
    {
        "storage_schema",
        "schema_version",
        "compiler_id",
        "source_sha256",
        "evidence_sha256",
        "asset_name",
        "compression",
        "compressed_size_bytes",
        "compressed_sha256",
        "uncompressed_size_bytes",
        "uncompressed_sha256",
    }
)
_COMPILED_ONLY_STUB_KEYS: Final = frozenset(
    {
        "storage_schema",
        "schema_version",
        "compiler_id",
        "source_sha256",
        "evidence_sha256",
        "uncompressed_size_bytes",
        "uncompressed_sha256",
    }
)


class SurfaceCertificateAssetError(ValueError):
    """A v4 certificate attachment is missing, unsafe, or inconsistent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _FrozenJsonMapping(Mapping[str, Any]):
    """Lazy recursive read-only view over a private decoded JSON mapping."""

    __slots__ = ("__value", "__cache_safe")

    def __init__(self, value: Mapping[str, Any], *, cache_safe: bool = False) -> None:
        self.__value = value
        self.__cache_safe = bool(cache_safe)

    def __getitem__(self, key: str) -> Any:
        return _frozen_json_view(
            self.__value[key], cache_safe=self.__cache_safe
        )

    def __iter__(self):
        return iter(self.__value)

    def __len__(self) -> int:
        return len(self.__value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping) or len(self) != len(other):
            return False
        return all(key in other and self[key] == other[key] for key in self)

    @property
    def _validation_cache_safe(self) -> bool:
        return self.__cache_safe


class _FrozenJsonSequence(Sequence[Any]):
    """Lazy recursive read-only view over a private decoded JSON sequence."""

    __slots__ = ("__value", "__cache_safe")

    def __init__(self, value: Sequence[Any], *, cache_safe: bool = False) -> None:
        self.__value = value
        self.__cache_safe = bool(cache_safe)

    def __getitem__(self, index):
        return _frozen_json_view(
            self.__value[index], cache_safe=self.__cache_safe
        )

    def __len__(self) -> int:
        return len(self.__value)

    def __eq__(self, other: object) -> bool:
        if (
            not isinstance(other, Sequence)
            or isinstance(other, (str, bytes, bytearray))
            or len(self) != len(other)
        ):
            return False
        return all(left == right for left, right in zip(self, other, strict=True))

    @property
    def _validation_cache_safe(self) -> bool:
        return self.__cache_safe


def _frozen_json_view(value: Any, *, cache_safe: bool = False) -> Any:
    if isinstance(value, (_FrozenJsonMapping, _FrozenJsonSequence)):
        # Never upgrade a wrapper created around caller-owned containers.  A
        # cache-safe root is minted only while the producer has sole ownership
        # of the decoded tree.
        return value
    if isinstance(value, Mapping):
        return _FrozenJsonMapping(value, cache_safe=cache_safe)
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return _FrozenJsonSequence(value, cache_safe=cache_safe)
    return value


def freeze_json_view(value: Any) -> Any:
    """Return a recursively read-only lazy view without copying its JSON tree."""

    return _frozen_json_view(value)


def _freeze_validation_cache_safe_json_view(value: Any) -> Any:
    """Freeze a decoded tree whose mutable containers have one private owner.

    Only persistence/compiler code that created a fresh tree and does not
    retain or expose the raw containers may call this helper.
    """

    return _frozen_json_view(value, cache_safe=True)


def is_frozen_json_view(value: Any) -> bool:
    """Return whether ``value`` is one of this module's read-only views.

    Every nested mapping/sequence is exposed through the same read-only facade.
    Use :func:`is_validation_cache_safe_json_view` before relying on object
    identity; a public wrapper may still have a caller-owned backing tree.
    """

    return isinstance(value, (_FrozenJsonMapping, _FrozenJsonSequence))


def is_validation_cache_safe_json_view(value: Any) -> bool:
    """Return whether ``value`` safely supports object-identity validation."""

    return bool(
        isinstance(value, (_FrozenJsonMapping, _FrozenJsonSequence))
        and value._validation_cache_safe
    )


def _fail(code: str, message: str) -> None:
    raise SurfaceCertificateAssetError(code, message)


def _is_sha256(value: object) -> bool:
    text = str(value).strip().casefold()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _hash_field(value: object, *, label: str) -> str:
    text = str(value).strip().casefold()
    if not _is_sha256(text):
        _fail(
            "SURFACE_CERTIFICATE_ASSET_MANIFEST_INVALID",
            f"surface certificate {label} is not a SHA-256 digest",
        )
    return text


def _size_field(value: object, *, label: str, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        _fail(
            "SURFACE_CERTIFICATE_ASSET_SIZE_INVALID",
            f"surface certificate {label} is outside its safety limit",
        )
    return value


def _canonical_chunks(
    value: Any, *, concrete_containers: bool = False
) -> Iterable[bytes]:
    try:
        yield from (
            iter_concrete_canonical_json_bytes(value)
            if concrete_containers
            else iter_canonical_json_bytes(value)
        )
    except (TypeError, ValueError, OverflowError) as exc:
        _fail(
            "SURFACE_CERTIFICATE_CANONICAL_JSON_INVALID",
            f"surface certificate cannot be encoded as canonical JSON: {exc}",
        )


def canonical_surface_certificate_sha256(
    value: Any,
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int], None] | None = None,
    concrete_containers: bool = False,
) -> str:
    """Return the no-newline canonical JSON SHA-256 used by v3/v4 evidence."""

    digest = sha256()
    size = 0
    next_check = 0
    cancelled = is_cancelled or (lambda: False)
    for chunk in _canonical_chunks(
        value, concrete_containers=concrete_containers
    ):
        size += len(chunk)
        if size >= next_check:
            if cancelled():
                _fail(
                    "SURFACE_CERTIFICATE_CANCELLED",
                    "surface certificate canonical verification was cancelled",
                )
            if progress is not None:
                progress(size)
            next_check = size + _STREAM_CANCEL_CHECK_BYTES
        digest.update(chunk)
    if cancelled():
        _fail(
            "SURFACE_CERTIFICATE_CANCELLED",
            "surface certificate canonical verification was cancelled",
        )
    return digest.hexdigest()


def canonical_surface_certificate_identity(
    value: Any,
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int], None] | None = None,
    concrete_containers: bool = False,
) -> tuple[int, str]:
    """Return bounded canonical JSON size/SHA-256 without materialising bytes."""

    size = 0
    digest = sha256()
    next_check = 0
    cancelled = is_cancelled or (lambda: False)
    for chunk in _canonical_chunks(
        value, concrete_containers=concrete_containers
    ):
        size += len(chunk)
        if size >= next_check:
            if cancelled():
                _fail(
                    "SURFACE_CERTIFICATE_CANCELLED",
                    "surface certificate canonical identity calculation was cancelled",
                )
            if progress is not None:
                progress(size)
            next_check = size + _STREAM_CANCEL_CHECK_BYTES
        if size > MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES:
            _fail(
                "SURFACE_CERTIFICATE_UNCOMPRESSED_TOO_LARGE",
                "v4 surface certificate exceeds the uncompressed safety limit "
                f"({size} > {MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES} bytes)",
            )
        digest.update(chunk)
    if cancelled():
        _fail(
            "SURFACE_CERTIFICATE_CANCELLED",
            "surface certificate canonical identity calculation was cancelled",
        )
    return size, digest.hexdigest()


def _validated_inline_v4(
    certificate: Mapping[str, Any],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int], None] | None = None,
    concrete_containers: bool = False,
) -> Mapping[str, Any]:
    if (
        certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or certificate.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
    ):
        _fail(
            "SURFACE_CERTIFICATE_UNSUPPORTED",
            "surface certificate is not the supported v4 finite-Via schema/compiler",
        )
    source_sha = _hash_field(certificate.get("source_sha256"), label="source SHA-256")
    evidence = _hash_field(certificate.get("evidence_sha256"), label="evidence SHA-256")
    unsigned = {
        str(key): value
        for key, value in certificate.items()
        if str(key) != "evidence_sha256"
    }
    if canonical_surface_certificate_sha256(
        unsigned,
        is_cancelled=is_cancelled,
        progress=progress,
        concrete_containers=concrete_containers,
    ) != evidence:
        _fail(
            "SURFACE_CERTIFICATE_EVIDENCE_MISMATCH",
            "v4 surface certificate evidence SHA-256 does not match its payload",
        )
    if not source_sha:
        _fail(
            "SURFACE_CERTIFICATE_SOURCE_INVALID",
            "v4 surface certificate has no source identity",
        )
    return certificate


def validate_inline_surface_certificate(
    certificate: Mapping[str, Any],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int], None] | None = None,
    concrete_containers: bool = False,
) -> Mapping[str, Any]:
    """Public trust boundary for callers compiling an inline v4 certificate."""

    return _validated_inline_v4(
        certificate,
        is_cancelled=is_cancelled,
        progress=progress,
        concrete_containers=concrete_containers,
    )


def _compress_canonical_json(value: Any) -> tuple[bytes, int, str]:
    compressor = zlib.compressobj(level=_COMPRESSION_LEVEL)
    compressed = bytearray()
    uncompressed_size = 0
    uncompressed_digest = sha256()
    for chunk in _canonical_chunks(value):
        uncompressed_size += len(chunk)
        if uncompressed_size > MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES:
            _fail(
                "SURFACE_CERTIFICATE_UNCOMPRESSED_TOO_LARGE",
                "v4 surface certificate exceeds the uncompressed safety limit "
                f"({uncompressed_size} > "
                f"{MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES} bytes)",
            )
        uncompressed_digest.update(chunk)
        encoded = compressor.compress(chunk)
        if encoded:
            compressed.extend(encoded)
            if len(compressed) > MAX_SURFACE_CERTIFICATE_COMPRESSED_BYTES:
                _fail(
                    "SURFACE_CERTIFICATE_COMPRESSED_TOO_LARGE",
                    "compressed v4 surface certificate exceeds the scenario member limit",
                )
    compressed.extend(compressor.flush())
    if len(compressed) > MAX_SURFACE_CERTIFICATE_COMPRESSED_BYTES:
        _fail(
            "SURFACE_CERTIFICATE_COMPRESSED_TOO_LARGE",
            "compressed v4 surface certificate exceeds the scenario member limit",
        )
    if (
        uncompressed_size > _EXPANSION_RATIO_GRACE_BYTES
        and uncompressed_size
        > max(1, len(compressed)) * MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO
    ):
        _fail(
            "SURFACE_CERTIFICATE_EXPANSION_RATIO_EXCEEDED",
            "v4 surface certificate compression ratio exceeds the safety limit",
        )
    return bytes(compressed), uncompressed_size, uncompressed_digest.hexdigest()


def _certificate_from_project(project: Any) -> tuple[Mapping[str, Any] | None, str]:
    if isinstance(project, Mapping):
        metadata = project.get("metadata", {})
    else:
        metadata = getattr(project, "metadata", {})
    if not isinstance(metadata, Mapping):
        _fail(
            "SURFACE_CERTIFICATE_PROJECT_METADATA_INVALID",
            "project metadata is not a mapping",
        )
    spd_import = metadata.get("spd_import")
    if "spd_import" in metadata and not isinstance(spd_import, Mapping):
        _fail(
            "SURFACE_CERTIFICATE_PROJECT_METADATA_INVALID",
            "project spd_import metadata exists but is not a mapping",
        )
    certificate: object = None
    if isinstance(spd_import, Mapping) and SURFACE_CERTIFICATE_METADATA_KEY in spd_import:
        certificate = spd_import[SURFACE_CERTIFICATE_METADATA_KEY]
        if not isinstance(certificate, Mapping):
            _fail(
                "SURFACE_CERTIFICATE_METADATA_INVALID",
                "surface certificate metadata exists but is not a mapping",
            )
    expected_source = (
        str(spd_import.get("source_sha256", "")).strip().casefold()
        if isinstance(spd_import, Mapping)
        else ""
    )
    return (certificate if isinstance(certificate, Mapping) else None), expected_source


def _required_project_source_sha256(expected_source: object) -> str:
    source_sha256 = str(expected_source).strip().casefold()
    if not _is_sha256(source_sha256):
        _fail(
            "SURFACE_CERTIFICATE_PROJECT_SOURCE_INVALID",
            "v4 surface certificate metadata requires a valid "
            "spd_import.source_sha256",
        )
    return source_sha256


def _require_project_source_match(
    certificate: Mapping[str, Any], expected_source: object
) -> str:
    source_sha256 = _required_project_source_sha256(expected_source)
    certificate_source = (
        str(certificate.get("source_sha256", "")).strip().casefold()
    )
    if certificate_source != source_sha256:
        _fail(
            "SURFACE_CERTIFICATE_SOURCE_MISMATCH",
            "v4 surface certificate is bound to a different SPD source",
        )
    return source_sha256


def _validate_optional_compiled_topology_asset(
    project: Any, attachments: Mapping[str, bytes]
) -> Mapping[str, Any] | None:
    """Validate an optional compiled asset and preserve its stable error code."""

    from .compiled_topology_asset import (
        CompiledTopologyAssetError,
        validate_compiled_topology_asset_envelope,
    )

    try:
        return validate_compiled_topology_asset_envelope(project, attachments)
    except CompiledTopologyAssetError as exc:
        _fail(exc.code, str(exc))


def is_surface_certificate_asset_stub(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("storage_schema") in {
        SURFACE_CERTIFICATE_ASSET_SCHEMA,
        SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA,
    }


def is_surface_certificate_compiled_only_stub(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("storage_schema") == (
        SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA
    )


def clear_surface_certificate_hydration_cache() -> None:
    """Release the bounded solve-time hydrated-certificate cache."""

    with _HYDRATED_CERTIFICATE_CACHE_LOCK:
        _HYDRATED_CERTIFICATE_CACHE.clear()


def _validated_stub(stub: Mapping[str, Any]) -> dict[str, Any]:
    if set(stub) != set(_STUB_KEYS):
        _fail(
            "SURFACE_CERTIFICATE_ASSET_MANIFEST_INVALID",
            "v4 surface certificate attachment manifest has missing or extra fields",
        )
    if (
        stub.get("storage_schema") != SURFACE_CERTIFICATE_ASSET_SCHEMA
        or stub.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or stub.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
        or stub.get("compression") != SURFACE_CERTIFICATE_COMPRESSION
    ):
        _fail(
            "SURFACE_CERTIFICATE_ASSET_MANIFEST_UNSUPPORTED",
            "v4 surface certificate attachment manifest schema/compiler/compression is unsupported",
        )
    source_sha = _hash_field(stub.get("source_sha256"), label="source SHA-256")
    evidence = _hash_field(stub.get("evidence_sha256"), label="evidence SHA-256")
    compressed_sha = _hash_field(
        stub.get("compressed_sha256"), label="compressed SHA-256"
    )
    uncompressed_sha = _hash_field(
        stub.get("uncompressed_sha256"), label="uncompressed SHA-256"
    )
    compressed_size = _size_field(
        stub.get("compressed_size_bytes"),
        label="compressed size",
        maximum=MAX_SURFACE_CERTIFICATE_COMPRESSED_BYTES,
    )
    uncompressed_size = _size_field(
        stub.get("uncompressed_size_bytes"),
        label="uncompressed size",
        maximum=MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES,
    )
    asset_name = str(stub.get("asset_name", ""))
    expected_name = (
        f"{SURFACE_CERTIFICATE_ASSET_PREFIX}/"
        f"layerwise-surface-connectivity-v4-{evidence[:16]}.json.zlib"
    )
    if asset_name != expected_name:
        _fail(
            "SURFACE_CERTIFICATE_ASSET_NAME_INVALID",
            "v4 surface certificate attachment name is not canonical",
        )
    if (
        uncompressed_size > _EXPANSION_RATIO_GRACE_BYTES
        and uncompressed_size
        > max(1, compressed_size) * MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO
    ):
        _fail(
            "SURFACE_CERTIFICATE_EXPANSION_RATIO_EXCEEDED",
            "v4 surface certificate declared compression ratio exceeds the safety limit",
        )
    return {
        "storage_schema": SURFACE_CERTIFICATE_ASSET_SCHEMA,
        "asset_name": asset_name,
        "source_sha256": source_sha,
        "evidence_sha256": evidence,
        "compressed_sha256": compressed_sha,
        "uncompressed_sha256": uncompressed_sha,
        "compressed_size_bytes": compressed_size,
        "uncompressed_size_bytes": uncompressed_size,
    }


def _validated_compiled_only_stub(stub: Mapping[str, Any]) -> dict[str, Any]:
    if set(stub) != set(_COMPILED_ONLY_STUB_KEYS):
        _fail(
            "SURFACE_CERTIFICATE_COMPILED_ONLY_MANIFEST_INVALID",
            "compiled-only surface certificate reference has missing or extra fields",
        )
    if (
        stub.get("storage_schema") != SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA
        or stub.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or stub.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
    ):
        _fail(
            "SURFACE_CERTIFICATE_COMPILED_ONLY_MANIFEST_UNSUPPORTED",
            "compiled-only surface certificate schema/compiler is unsupported",
        )
    return {
        "storage_schema": SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA,
        "source_sha256": _hash_field(
            stub.get("source_sha256"), label="source SHA-256"
        ),
        "evidence_sha256": _hash_field(
            stub.get("evidence_sha256"), label="evidence SHA-256"
        ),
        "uncompressed_size_bytes": _size_field(
            stub.get("uncompressed_size_bytes"),
            label="canonical size",
            maximum=MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES,
        ),
        "uncompressed_sha256": _hash_field(
            stub.get("uncompressed_sha256"), label="canonical SHA-256"
        ),
    }


def validated_surface_certificate_storage_stub(
    stub: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate either persisted v4 storage descriptor and return its identity."""

    storage_schema = stub.get("storage_schema")
    if storage_schema == SURFACE_CERTIFICATE_ASSET_SCHEMA:
        return _validated_stub(stub)
    if storage_schema == SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA:
        return _validated_compiled_only_stub(stub)
    _fail(
        "SURFACE_CERTIFICATE_STORAGE_SCHEMA_UNSUPPORTED",
        "surface certificate storage schema is unsupported",
    )


def _attachment_bytes(
    attachments: Mapping[str, bytes], manifest: Mapping[str, Any]
) -> bytes:
    asset_name = str(manifest["asset_name"])
    matches = [
        (name, content)
        for name, content in attachments.items()
        if str(name).casefold() == asset_name.casefold()
    ]
    if len(matches) != 1 or matches[0][0] != asset_name:
        _fail(
            "SURFACE_CERTIFICATE_ASSET_MISSING",
            f"v4 surface certificate attachment {asset_name!r} is missing or non-canonical",
        )
    content = matches[0][1]
    if not isinstance(content, (bytes, bytearray, memoryview)):
        _fail(
            "SURFACE_CERTIFICATE_ASSET_INVALID",
            "v4 surface certificate attachment must be bytes",
        )
    compressed = bytes(content)
    if (
        len(compressed) != manifest["compressed_size_bytes"]
        or sha256(compressed).hexdigest() != manifest["compressed_sha256"]
    ):
        _fail(
            "SURFACE_CERTIFICATE_COMPRESSED_INTEGRITY_FAILED",
            "v4 surface certificate compressed size/SHA-256 does not match its manifest",
        )
    return compressed


def _validate_surface_certificate_descriptor_envelope(
    project: Any,
    attachments: Mapping[str, bytes],
) -> Mapping[str, Any] | None:
    """Validate one surface descriptor/member without hydrating canonical JSON."""

    certificate, expected_source = _certificate_from_project(project)
    if certificate is None:
        return None
    storage_schema = certificate.get("storage_schema")
    if storage_schema is not None:
        manifest = validated_surface_certificate_storage_stub(certificate)
        _require_project_source_match(manifest, expected_source)
        if storage_schema == SURFACE_CERTIFICATE_ASSET_SCHEMA:
            _attachment_bytes(attachments, manifest)
        return certificate
    if certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA:
        # v3 is intentionally inline and remains owned by its existing strict
        # compiler validation.
        return certificate
    expected_source = _required_project_source_sha256(expected_source)
    validated = _validated_inline_v4(certificate)
    _require_project_source_match(validated, expected_source)
    return validated


def validate_surface_certificate_asset_envelope(
    project: Any,
    attachments: Mapping[str, bytes],
) -> Mapping[str, Any] | None:
    """Validate surface persistence; compiled-only mode also requires its asset."""

    surface = _validate_surface_certificate_descriptor_envelope(project, attachments)
    if is_surface_certificate_compiled_only_stub(surface):
        compiled = _validate_optional_compiled_topology_asset(project, attachments)
        if compiled is None:
            _fail(
                "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_REQUIRED",
                "compiled-only surface certificate requires its bound compiled topology asset",
            )
    return surface


def validate_project_topology_storage_envelope(
    project: Any,
    attachments: Mapping[str, bytes],
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Validate the joint surface/compiled persistence contract.

    This is the archive/save/solver trust gate.  It keeps legacy attachment
    semantics strict, requires the compiled asset for compiled-only references,
    and rejects unreferenced members in the reserved ``topology/`` namespace.
    """

    surface = _validate_surface_certificate_descriptor_envelope(project, attachments)
    compiled = _validate_optional_compiled_topology_asset(project, attachments)
    if is_surface_certificate_compiled_only_stub(surface) and compiled is None:
        _fail(
            "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_REQUIRED",
            "compiled-only surface certificate requires its bound compiled topology asset",
        )

    referenced_names: set[str] = set()
    if isinstance(surface, Mapping) and surface.get("storage_schema") == (
        SURFACE_CERTIFICATE_ASSET_SCHEMA
    ):
        referenced_names.add(str(surface.get("asset_name", "")))
    if isinstance(compiled, Mapping):
        referenced_names.add(str(compiled.get("asset_name", "")))
    reserved = {
        str(name)
        for name in attachments
        if str(name).casefold().startswith(
            f"{SURFACE_CERTIFICATE_ASSET_PREFIX}/".casefold()
        )
    }
    if reserved != referenced_names:
        _fail(
            "TOPOLOGY_ATTACHMENT_ORPHANED",
            "reserved topology attachments do not exactly match their metadata descriptors",
        )
    return surface, compiled


def _decompress_bounded(compressed: bytes, manifest: Mapping[str, Any]) -> bytearray:
    expected_size = int(manifest["uncompressed_size_bytes"])
    if expected_size > MAX_SURFACE_CERTIFICATE_HYDRATION_BYTES:
        _fail(
            "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_REQUIRED",
            "surface certificate is too large for safe in-process hydration; "
            "the bound compiled topology asset is required",
        )
    decompressor = zlib.decompressobj()
    raw = bytearray()
    cursor = 0
    try:
        while cursor < len(compressed):
            pending = compressed[cursor : cursor + _STREAM_CHUNK_BYTES]
            cursor += len(pending)
            while pending:
                remaining = expected_size - len(raw)
                if remaining < 0:
                    _fail(
                        "SURFACE_CERTIFICATE_UNCOMPRESSED_TOO_LARGE",
                        "v4 surface certificate expanded beyond its declared size",
                    )
                produced = decompressor.decompress(
                    pending,
                    min(_STREAM_CHUNK_BYTES, remaining + 1),
                )
                raw.extend(produced)
                if len(raw) > expected_size:
                    _fail(
                        "SURFACE_CERTIFICATE_UNCOMPRESSED_TOO_LARGE",
                        "v4 surface certificate expanded beyond its declared size",
                    )
                if decompressor.unused_data:
                    _fail(
                        "SURFACE_CERTIFICATE_COMPRESSED_STREAM_INVALID",
                        "v4 surface certificate contains trailing compressed data",
                    )
                pending = decompressor.unconsumed_tail
                if not pending:
                    break
        remaining = expected_size - len(raw)
        raw.extend(decompressor.flush(min(_STREAM_CHUNK_BYTES, remaining + 1)))
    except zlib.error as exc:
        _fail(
            "SURFACE_CERTIFICATE_COMPRESSED_STREAM_INVALID",
            f"v4 surface certificate is not a valid zlib stream: {exc}",
        )
    if (
        not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
        or len(raw) != expected_size
    ):
        _fail(
            "SURFACE_CERTIFICATE_UNCOMPRESSED_SIZE_MISMATCH",
            "v4 surface certificate did not expand to its declared canonical size",
        )
    if sha256(raw).hexdigest() != manifest["uncompressed_sha256"]:
        _fail(
            "SURFACE_CERTIFICATE_UNCOMPRESSED_INTEGRITY_FAILED",
            "v4 surface certificate uncompressed SHA-256 does not match its manifest",
        )
    return raw


def _strict_json_object(raw: bytearray) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail(
                    "SURFACE_CERTIFICATE_JSON_DUPLICATE_KEY",
                    f"v4 surface certificate contains duplicate JSON key {key!r}",
                )
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except SurfaceCertificateAssetError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(
            "SURFACE_CERTIFICATE_JSON_INVALID",
            f"v4 surface certificate is not valid UTF-8 JSON: {exc}",
        )
    if not isinstance(payload, dict):
        _fail(
            "SURFACE_CERTIFICATE_JSON_INVALID",
            "v4 surface certificate attachment must contain a JSON object",
        )
    return payload


def _require_canonical_bytes(raw: bytearray, payload: Mapping[str, Any]) -> None:
    offset = 0
    view = memoryview(raw)
    for chunk in _canonical_chunks(payload):
        end = offset + len(chunk)
        if end > len(raw) or view[offset:end] != chunk:
            _fail(
                "SURFACE_CERTIFICATE_JSON_NONCANONICAL",
                "v4 surface certificate attachment is not canonical JSON",
            )
        offset = end
    if offset != len(raw):
        _fail(
            "SURFACE_CERTIFICATE_JSON_NONCANONICAL",
            "v4 surface certificate attachment is not canonical JSON",
        )


def hydrate_surface_certificate(
    project: Any,
    attachments: Mapping[str, bytes],
) -> Mapping[str, Any] | None:
    """Return an inline certificate after bounded/hash/canonical validation.

    Missing certificates return ``None`` so existing callers can preserve their
    domain-specific missing-certificate error codes.  A malformed or missing
    v4 attachment always raises :class:`SurfaceCertificateAssetError`.
    """

    certificate, expected_source = _certificate_from_project(project)
    if certificate is None:
        return None
    if certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA:
        return certificate
    expected_source = _required_project_source_sha256(expected_source)
    if is_surface_certificate_compiled_only_stub(certificate):
        manifest = _validated_compiled_only_stub(certificate)
        _require_project_source_match(manifest, expected_source)
        validate_project_topology_storage_envelope(project, attachments)
        _fail(
            "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_REQUIRED",
            "raw canonical surface certificate was intentionally omitted; "
            "the bound compiled topology asset must be used",
        )
    if certificate.get("storage_schema") is None:
        validated = _validated_inline_v4(certificate)
        _require_project_source_match(validated, expected_source)
        return dict(validated)

    manifest = validated_surface_certificate_storage_stub(certificate)
    _require_project_source_match(manifest, expected_source)
    compressed = _attachment_bytes(attachments, manifest)
    cache_key = "|".join(
        (
            str(manifest["source_sha256"]),
            str(manifest["evidence_sha256"]),
            str(manifest["compressed_size_bytes"]),
            str(manifest["compressed_sha256"]),
            str(manifest["uncompressed_size_bytes"]),
            str(manifest["uncompressed_sha256"]),
        )
    )
    # Serialize hydration so two legacy fallback workers cannot expand the
    # same large raw certificate concurrently.  Cache only a recursively
    # read-only lazy view: callers never receive the private parsed dict/list
    # objects, so one Evaluation cannot poison evidence reused by another.
    with _HYDRATED_CERTIFICATE_CACHE_LOCK:
        cached = _HYDRATED_CERTIFICATE_CACHE.get(cache_key)
        if cached is not None:
            _HYDRATED_CERTIFICATE_CACHE.move_to_end(cache_key)
            return cached
        raw = _decompress_bounded(compressed, manifest)
        payload = _strict_json_object(raw)
        _require_canonical_bytes(raw, payload)
        if (
            payload.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
            or payload.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
            or str(payload.get("source_sha256", "")).strip().casefold()
            != manifest["source_sha256"]
            or str(payload.get("evidence_sha256", "")).strip().casefold()
            != manifest["evidence_sha256"]
        ):
            _fail(
                "SURFACE_CERTIFICATE_ASSET_BINDING_MISMATCH",
                "hydrated v4 certificate identity differs from its metadata manifest",
            )
        validated = dict(_validated_inline_v4(payload))
        frozen = freeze_json_view(validated)
        _HYDRATED_CERTIFICATE_CACHE[cache_key] = frozen
        _HYDRATED_CERTIFICATE_CACHE.move_to_end(cache_key)
        while len(_HYDRATED_CERTIFICATE_CACHE) > _HYDRATED_CERTIFICATE_CACHE_LIMIT:
            _HYDRATED_CERTIFICATE_CACHE.popitem(last=False)
        return frozen


def externalize_surface_certificate(
    certificate: Mapping[str, Any],
) -> tuple[Mapping[str, Any], tuple[str, bytes] | None]:
    """Return a v4 attachment stub/payload or pass a legacy certificate through."""

    if certificate.get("storage_schema") is not None:
        validated_surface_certificate_storage_stub(certificate)
        return certificate, None
    if certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA:
        return certificate, None
    certificate = _validated_inline_v4(certificate)
    compressed, uncompressed_size, uncompressed_sha = _compress_canonical_json(
        certificate
    )
    evidence = str(certificate["evidence_sha256"]).strip().casefold()
    asset_name = (
        f"{SURFACE_CERTIFICATE_ASSET_PREFIX}/"
        f"layerwise-surface-connectivity-v4-{evidence[:16]}.json.zlib"
    )
    stub: dict[str, Any] = {
        "storage_schema": SURFACE_CERTIFICATE_ASSET_SCHEMA,
        "schema_version": FINITE_VIA_SURFACE_SCHEMA,
        "compiler_id": FINITE_VIA_SURFACE_COMPILER,
        "source_sha256": str(certificate["source_sha256"]).strip().casefold(),
        "evidence_sha256": evidence,
        "asset_name": asset_name,
        "compression": SURFACE_CERTIFICATE_COMPRESSION,
        "compressed_size_bytes": len(compressed),
        "compressed_sha256": sha256(compressed).hexdigest(),
        "uncompressed_size_bytes": uncompressed_size,
        "uncompressed_sha256": uncompressed_sha,
    }
    return stub, (asset_name, compressed)


def compiled_only_surface_certificate_stub(
    certificate: Mapping[str, Any],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int], None] | None = None,
    concrete_containers: bool = False,
) -> dict[str, Any]:
    """Create a no-payload canonical attestation for a complete inline v4 cert."""

    certificate = _validated_inline_v4(
        certificate,
        is_cancelled=is_cancelled,
        progress=progress,
        concrete_containers=concrete_containers,
    )
    return _compiled_only_stub_from_verified_inline(
        certificate,
        is_cancelled=is_cancelled,
        progress=progress,
        concrete_containers=concrete_containers,
    )


def _compiled_only_stub_from_verified_inline(
    certificate: Mapping[str, Any],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int], None] | None = None,
    concrete_containers: bool = False,
) -> dict[str, Any]:
    """Attest canonical bytes after the caller crossed the inline-v4 gate."""

    uncompressed_size, uncompressed_sha = canonical_surface_certificate_identity(
        certificate,
        is_cancelled=is_cancelled,
        progress=progress,
        concrete_containers=concrete_containers,
    )
    stub = {
        "storage_schema": SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA,
        "schema_version": FINITE_VIA_SURFACE_SCHEMA,
        "compiler_id": FINITE_VIA_SURFACE_COMPILER,
        "source_sha256": str(certificate["source_sha256"]).strip().casefold(),
        "evidence_sha256": str(certificate["evidence_sha256"]).strip().casefold(),
        "uncompressed_size_bytes": uncompressed_size,
        "uncompressed_sha256": uncompressed_sha,
    }
    _validated_compiled_only_stub(stub)
    return stub


def _compiled_topology_precondition_failure(
    project: Any,
    certificate: Mapping[str, Any],
) -> str | None:
    """Return one compact reason why compiled-only persistence is unavailable.

    Small parser fixtures intentionally retain incomplete v4 diagnostic
    certificates.  They remain valid canonical-certificate fallback bundles,
    but complete production evidence must never silently enter that path.
    """

    if str(certificate.get("status", "")).strip().casefold() != "complete":
        return "CERTIFICATE_STATUS_INCOMPLETE"
    quotient = certificate.get("finite_via_quotient")
    scenario_topology = certificate.get("scenario_decap_terminal_topology")
    geometry_assets = certificate.get("geometry_assets")
    if not isinstance(quotient, Mapping) or (
        str(quotient.get("status", "")).strip().casefold() != "complete"
    ):
        return "FINITE_QUOTIENT_MISSING_OR_INCOMPLETE"
    if not isinstance(scenario_topology, Mapping) or (
        str(scenario_topology.get("status", "")).strip().casefold()
        != "complete"
    ):
        return "SCENARIO_TOPOLOGY_MISSING_OR_INCOMPLETE"
    if not isinstance(geometry_assets, (list, tuple)) or not geometry_assets:
        return "GEOMETRY_ASSETS_MISSING_OR_EMPTY"

    def geometry_identity(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(row.get("net", "")).strip().casefold(),
            str(row.get("layer", "")).strip().casefold(),
            str(row.get("asset", "")).strip(),
            str(row.get("asset_sha256", "")).strip().casefold(),
        )

    def island_partition(row: Mapping[str, Any]) -> tuple[str, ...] | None:
        raw = row.get("island_ids")
        if (
            not isinstance(raw, Sequence)
            or isinstance(raw, (str, bytes, bytearray))
            or not raw
        ):
            return None
        islands = tuple(str(item).strip() for item in raw)
        if any(not item for item in islands) or len(set(islands)) != len(islands):
            return None
        return islands

    certified: dict[tuple[str, str, str, str], tuple[str, ...]] = {}
    for index, row in enumerate(geometry_assets):
        if not isinstance(row, Mapping):
            return f"CERTIFICATE_GEOMETRY_ROW_INVALID[{index}]"
        identity = geometry_identity(row)
        islands = island_partition(row)
        if (
            not all(identity)
            or len(identity[3]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in identity[3]
            )
            or islands is None
            or identity in certified
        ):
            return f"CERTIFICATE_GEOMETRY_ROW_INVALID[{index}]"
        certified[identity] = islands

    metadata = (
        project.get("metadata", {})
        if isinstance(project, Mapping)
        else getattr(project, "metadata", {})
    )
    spd_import = metadata.get("spd_import", {}) if isinstance(metadata, Mapping) else {}
    records = spd_import.get("plane_geometries", ()) if isinstance(spd_import, Mapping) else ()
    if not isinstance(records, (list, tuple)) or not records:
        return "PROJECT_GEOMETRY_MANIFEST_MISSING_OR_EMPTY"
    project_geometry: dict[
        tuple[str, str, str, str], tuple[str, ...]
    ] = {}
    for index, row in enumerate(records):
        if not isinstance(row, Mapping):
            return f"PROJECT_GEOMETRY_ROW_INVALID[{index}]"
        identity = geometry_identity(row)
        if not all(identity) or identity in project_geometry:
            return f"PROJECT_GEOMETRY_ROW_INVALID[{index}]"
        islands = island_partition(row)
        if islands is None:
            return (
                "PROJECT_GEOMETRY_ISLAND_IDS_MISSING_OR_EMPTY"
                f"[{index}]"
            )
        project_geometry[identity] = islands
    if project_geometry != certified:
        return "PROJECT_CERTIFICATE_GEOMETRY_MISMATCH"
    return None


def _supports_compiled_topology_asset(
    project: Any,
    certificate: Mapping[str, Any],
) -> bool:
    """Return whether a complete production v4 topology can be precompiled."""

    return _compiled_topology_precondition_failure(project, certificate) is None


def _is_complete_production_certificate(
    certificate: Mapping[str, Any],
) -> bool:
    geometry_assets = certificate.get("geometry_assets")
    return bool(
        str(certificate.get("status", "")).strip().casefold() == "complete"
        and isinstance(geometry_assets, (list, tuple))
        and geometry_assets
    )


def _add_attachment_exact(
    attachments: dict[str, bytes],
    name: str,
    content: bytes,
    *,
    collision_code: str,
    label: str,
) -> None:
    matches = [key for key in attachments if key.casefold() == name.casefold()]
    if matches and (
        len(matches) != 1
        or matches[0] != name
        or attachments[matches[0]] != content
    ):
        _fail(
            collision_code,
            f"scenario already contains a different attachment named {name!r} ({label})",
        )
    attachments[name] = content


def _validate_inline_conversion_siblings(
    project: Any, attachments: Mapping[str, bytes]
) -> None:
    """Reject compiled/reserved siblings without re-hashing an inline v4 tree."""

    _validate_optional_compiled_topology_asset(project, attachments)
    if any(
        str(name).casefold().startswith(
            f"{SURFACE_CERTIFICATE_ASSET_PREFIX}/".casefold()
        )
        for name in attachments
    ):
        _fail(
            "TOPOLOGY_ATTACHMENT_ORPHANED",
            "an inline surface certificate cannot carry reserved topology attachments",
        )


def externalize_project_surface_certificate(
    project: Any,
    attachments: Mapping[str, bytes],
    *,
    progress: Callable[[int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[Any, dict[str, bytes]]:
    """Externalize v4 evidence before a ScenarioSpec can copy its huge tree."""

    certificate, expected_source = _certificate_from_project(project)
    updated_attachments = dict(attachments)
    if (
        certificate is None
        or certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
    ):
        # A compiled topology and every reserved topology member are strictly
        # subordinate to a persisted v4 descriptor.
        validate_project_topology_storage_envelope(project, updated_attachments)
        return project, updated_attachments
    expected_source = _required_project_source_sha256(expected_source)
    compiled_manifest: Mapping[str, Any] | None = None

    if certificate.get("storage_schema") is not None:
        validated_surface_certificate_storage_stub(certificate)
        # Retain the exact wire descriptor rather than the normalized identity.
        stub = dict(certificate)
        _require_project_source_match(stub, expected_source)
        _surface, compiled_manifest = validate_project_topology_storage_envelope(
            project, updated_attachments
        )
    else:
        # An inline certificate must not arrive with a sibling compiled
        # descriptor/member.  This preflight deliberately does not canonicalize
        # the multi-GiB tree; the chosen persistence trust boundary below does
        # that exactly once.
        _validate_inline_conversion_siblings(project, updated_attachments)
        _require_project_source_match(certificate, expected_source)
        compiled_precondition_failure = (
            _compiled_topology_precondition_failure(project, certificate)
        )
        if compiled_precondition_failure is None:
            from .compiled_topology_asset import (
                CompiledTopologyAssetError,
                build_compiled_topology_asset,
            )

            try:
                stub, compiled_manifest, compiled_generated = build_compiled_topology_asset(
                    project,
                    certificate,
                    progress=progress,
                    is_cancelled=is_cancelled,
                    concrete_containers=True,
                )
            except CompiledTopologyAssetError as exc:
                _fail(exc.code, str(exc))
            compiled_name, compiled_bytes = compiled_generated
            _add_attachment_exact(
                updated_attachments,
                compiled_name,
                compiled_bytes,
                collision_code="COMPILED_TOPOLOGY_ASSET_COLLISION",
                label="compiled topology",
            )
        elif _is_complete_production_certificate(certificate):
            _fail(
                "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_PRECONDITION_FAILED",
                "compiled-only persistence is required for this complete "
                "production certificate; legacy raw JSON fallback was not "
                f"attempted (reason={compiled_precondition_failure})",
            )
        else:
            # Incomplete/synthetic fixtures retain the legacy bounded raw
            # attachment.  Production-complete inputs never take this path.
            stub, generated = externalize_surface_certificate(certificate)
            _require_project_source_match(stub, expected_source)
            if generated is None:
                _fail(
                    "SURFACE_CERTIFICATE_EXTERNALIZATION_FAILED",
                    "inline v4 surface certificate was not externalized",
                )
            asset_name, compressed = generated
            _add_attachment_exact(
                updated_attachments,
                asset_name,
                compressed,
                collision_code="SURFACE_CERTIFICATE_ASSET_COLLISION",
                label="surface certificate",
            )

    project_payload = dict(project) if isinstance(project, Mapping) else None
    source_metadata = (
        project_payload.get("metadata", {})
        if project_payload is not None
        else getattr(project, "metadata", {})
    )
    metadata = dict(source_metadata)
    spd_import = dict(metadata.get("spd_import", {}))
    spd_import[SURFACE_CERTIFICATE_METADATA_KEY] = dict(stub)
    if compiled_manifest is not None:
        from .compiled_topology_asset import COMPILED_TOPOLOGY_ASSET_METADATA_KEY

        spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY] = dict(compiled_manifest)
    metadata["spd_import"] = spd_import

    if project_payload is not None:
        project_payload["metadata"] = metadata
        updated_project: Any = project_payload
    else:
        updated_project = project.model_copy(update={"metadata": metadata})
    validate_project_topology_storage_envelope(
        updated_project, updated_attachments
    )
    return updated_project, updated_attachments


def externalize_scenario_surface_certificate(
    scenario: Any,
    attachments: Mapping[str, bytes],
    *,
    progress: Callable[[int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[Any, dict[str, bytes]]:
    """Replace inline v4 evidence and update scenario attachment indexes."""

    project_payload, updated_attachments = externalize_project_surface_certificate(
        scenario.normalized_project,
        attachments,
        progress=progress,
        is_cancelled=is_cancelled,
    )
    if not isinstance(project_payload, Mapping):
        project_payload = project_payload.model_dump(mode="python")
    ordered_names = sorted(updated_attachments, key=str.casefold)
    attachment_hashes = {
        name: sha256(updated_attachments[name]).hexdigest() for name in ordered_names
    }
    candidate = scenario.model_copy(
        update={
            "normalized_project": project_payload,
            "attachment_names": ordered_names,
            "attachment_hashes": attachment_hashes,
        }
    )
    # Import locally to keep this storage module independent from scenario_io.
    from .scenario import ScenarioSpec

    return (
        ScenarioSpec.model_validate(candidate.model_dump(mode="python")),
        updated_attachments,
    )


__all__ = [
    "MAX_SURFACE_CERTIFICATE_COMPRESSED_BYTES",
    "MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO",
    "MAX_SURFACE_CERTIFICATE_HYDRATION_BYTES",
    "MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES",
    "SURFACE_CERTIFICATE_ASSET_SCHEMA",
    "SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA",
    "SURFACE_CERTIFICATE_METADATA_KEY",
    "SurfaceCertificateAssetError",
    "canonical_surface_certificate_identity",
    "canonical_surface_certificate_sha256",
    "clear_surface_certificate_hydration_cache",
    "compiled_only_surface_certificate_stub",
    "externalize_project_surface_certificate",
    "externalize_scenario_surface_certificate",
    "externalize_surface_certificate",
    "freeze_json_view",
    "hydrate_surface_certificate",
    "is_frozen_json_view",
    "is_validation_cache_safe_json_view",
    "is_surface_certificate_asset_stub",
    "is_surface_certificate_compiled_only_stub",
    "validate_inline_surface_certificate",
    "validate_project_topology_storage_envelope",
    "validate_surface_certificate_asset_envelope",
    "validated_surface_certificate_storage_stub",
]
