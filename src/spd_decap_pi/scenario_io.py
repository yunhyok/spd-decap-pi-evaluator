"""Secure, atomic persistence for independent ``.spdpi`` scenario bundles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from typing import Any, Callable, Final
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from pydantic import ValidationError

from .raw_spatial_contact_asset import (
    RawSpatialContactAssetError,
    validate_project_raw_spatial_contact_asset_envelope,
)
from .scenario import (
    SCENARIO_SCHEMA_VERSION,
    ScenarioSpec,
    _ScenarioValidationMemo,
    _without_absent_destination_pwr_layer,
)
from .routing_obstacles import decode_routing_obstacle_asset
from .surface_certificate_asset import (
    SurfaceCertificateAssetError,
    externalize_scenario_surface_certificate,
    validate_project_topology_storage_envelope,
)


SCENARIO_FORMAT: Final = "spd-decap-pi-scenario"
SCENARIO_FORMAT_VERSION: Final = 1
SCENARIO_FILENAME: Final = "scenario.json"
MANIFEST_FILENAME: Final = "manifest.json"
ATTACHMENT_PREFIX: Final = "attachments"
# Both named production certificates crossed the former 384 MiB member bound.
# Keep the archive finite at 1 GiB per member and 2 GiB aggregate while the
# attachment-backed surface evidence retains bounded expansion/canonical/hash
# gates.  Complete production evidence uses the smaller compiled-only contract.
MAX_SCENARIO_MEMBER_BYTES: Final = 1024 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES: Final = 2 * 1024 * 1024 * 1024
SCENARIO_LOAD_CHUNK_BYTES: Final = 1024 * 1024


class ScenarioFormatError(ValueError):
    """Raised when a scenario archive is invalid, unsafe, or unsupported."""


AttachmentSource = bytes | bytearray | memoryview | str | os.PathLike[str]


@dataclass(frozen=True, slots=True)
class ScenarioBundle:
    scenario: ScenarioSpec
    attachments: dict[str, bytes]
    recovered_from: Path | None = None
    recovery_reason: str | None = None

    @property
    def assets(self) -> dict[str, bytes]:
        """Compatibility alias with the existing MLO project bundle API."""

        return self.attachments


def _canonical_json(data: object) -> bytes:
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _strict_json(content: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ScenarioFormatError(f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            content.decode("utf-8"), object_pairs_hook=reject_duplicate_keys
        )
    except ScenarioFormatError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScenarioFormatError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ScenarioFormatError(f"{label} must contain a JSON object")
    return value


def _safe_attachment_name(name: str) -> str:
    supplied = str(name)
    raw = supplied.strip()
    if not raw or raw != supplied or "\\" in raw:
        raise ScenarioFormatError(f"unsafe scenario attachment name {name!r}")
    path = PurePosixPath(raw)
    normalized = path.as_posix()
    if (
        path.is_absolute()
        or normalized != raw
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(
            ":" in part or any(ord(character) < 32 for character in part)
            for part in path.parts
        )
    ):
        raise ScenarioFormatError(f"unsafe scenario attachment name {name!r}")
    if path.parts[0].casefold() == ATTACHMENT_PREFIX.casefold():
        raise ScenarioFormatError(
            f"attachment name {name!r} must be relative to the attachments directory"
        )
    if path.suffix.casefold() == ".spd":
        raise ScenarioFormatError("raw SPD files cannot be embedded in .spdpi scenarios")
    return normalized


def _validated_destination(path: str | os.PathLike[str]) -> Path:
    destination = Path(path)
    if destination.suffix.casefold() != ".spdpi":
        if destination.suffix:
            raise ScenarioFormatError("scenario file extension must be .spdpi")
        destination = destination.with_suffix(".spdpi")
    return destination


def _read_attachment_source(
    source: AttachmentSource,
    *,
    scenario: ScenarioSpec,
) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        content = bytes(source)
        if len(content) > MAX_SCENARIO_MEMBER_BYTES:
            raise ScenarioFormatError("scenario attachment exceeds size limit")
        return _reject_raw_spd_payload(content, scenario=scenario)

    source_path = Path(source)
    if not source_path.is_file():
        raise ScenarioFormatError(
            f"scenario attachment source does not exist: {source_path}"
        )
    if source_path.suffix.casefold() == ".spd":
        raise ScenarioFormatError("raw SPD files cannot be embedded in .spdpi scenarios")
    try:
        if source_path.resolve() == Path(scenario.source.path).resolve():
            raise ScenarioFormatError(
                "the external source SPD cannot be embedded as a scenario attachment"
            )
    except OSError:
        # A stale external identity is allowed; the actual attachment is still
        # protected by its extension and archive name.
        pass
    size = source_path.stat().st_size
    if size > MAX_SCENARIO_MEMBER_BYTES:
        raise ScenarioFormatError(
            f"scenario attachment {source_path.name!r} exceeds size limit"
        )
    return _reject_raw_spd_payload(source_path.read_bytes(), scenario=scenario)


def _reject_raw_spd_payload(content: bytes, *, scenario: ScenarioSpec) -> bytes:
    """Reject the exact external source even if a caller disguises its filename."""

    if (
        len(content) == scenario.source.size_bytes
        and sha256(content).hexdigest() == scenario.source.sha256
    ):
        raise ScenarioFormatError("raw SPD files cannot be embedded in .spdpi scenarios")
    return content


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    # Topology/geometry ``.zlib`` payloads already carry bounded compression
    # and independent hashes.  Re-deflating hundreds of MiB adds CPU/IO delay
    # without useful size reduction.
    info.compress_type = ZIP_STORED if name.casefold().endswith(".zlib") else ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def _write_member(archive: ZipFile, name: str, content: bytes) -> None:
    archive.writestr(_zip_info(name), content)


def _validate_routing_attachment_binding(
    scenario: ScenarioSpec, attachments: Mapping[str, bytes]
) -> None:
    reference = scenario.routing_obstacle_asset
    if reference is None:
        return
    payload_by_key = {name.casefold(): payload for name, payload in attachments.items()}
    payload = payload_by_key.get(reference.attachment_name.casefold())
    if payload is None:
        raise ScenarioFormatError("routing obstacle attachment is missing")
    try:
        asset = decode_routing_obstacle_asset(
            payload,
            expected_source_sha256=reference.source_sha256,
            expected_stackup_fingerprint=reference.stackup_fingerprint,
        )
    except ValueError as exc:
        raise ScenarioFormatError(f"routing obstacle attachment is invalid: {exc}") from exc
    if asset.content_sha256 != reference.content_sha256:
        raise ScenarioFormatError("routing obstacle content hash disagrees with scenario")
    if asset.schema_version != reference.schema_version:
        raise ScenarioFormatError("routing obstacle schema disagrees with scenario")
    if asset.scope.value != reference.scope:
        raise ScenarioFormatError("routing obstacle scope disagrees with scenario")
    if asset.compiler_policy != reference.compiler_policy:
        raise ScenarioFormatError("routing obstacle compiler policy disagrees with scenario")
    if asset.production_ready != reference.production_ready:
        raise ScenarioFormatError("routing obstacle readiness disagrees with scenario")
    if asset.scope_limitation != reference.scope_limitation:
        raise ScenarioFormatError("routing obstacle scope limitation disagrees with scenario")
    if tuple(item.profile_id for item in asset.via_profiles) != reference.via_profile_ids:
        raise ScenarioFormatError("routing obstacle via profiles disagree with scenario")


def save_scenario(
    scenario: ScenarioSpec,
    path: str | os.PathLike[str],
    *,
    attachments: Mapping[str, AttachmentSource] | None = None,
) -> Path:
    """Validate and atomically replace an independent scenario archive."""

    destination = _validated_destination(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if attachments is None and scenario.attachment_names:
        raise ScenarioFormatError(
            "attachments must be supplied for a scenario that declares them; "
            "use save_scenario_bundle when round-tripping a loaded scenario"
        )

    attachment_bytes: dict[str, bytes] = {}
    seen_names: set[str] = set()
    for raw_name, source in (attachments or {}).items():
        name = _safe_attachment_name(raw_name)
        key = name.casefold()
        if key in seen_names:
            raise ScenarioFormatError(f"duplicate scenario attachment name {name!r}")
        seen_names.add(key)
        content = _read_attachment_source(source, scenario=scenario)
        if len(content) > MAX_SCENARIO_MEMBER_BYTES:
            raise ScenarioFormatError(f"scenario attachment {name!r} exceeds size limit")
        attachment_bytes[name] = content

    try:
        scenario, attachment_bytes = externalize_scenario_surface_certificate(
            scenario, attachment_bytes
        )
        raw_manifest = validate_project_raw_spatial_contact_asset_envelope(
            scenario.normalized_project, attachment_bytes
        )
        if (
            raw_manifest is not None
            and raw_manifest["source_sha256"] != scenario.source.sha256
        ):
            raise RawSpatialContactAssetError(
                "RAW_SPATIAL_BINDING_MISMATCH",
                "raw spatial source differs from the scenario source identity",
            )
    except SurfaceCertificateAssetError as exc:
        raise ScenarioFormatError(
            f"surface certificate attachment failed validation [{exc.code}]: {exc}"
        ) from exc
    except RawSpatialContactAssetError as exc:
        raise ScenarioFormatError(
            f"raw spatial contact attachment failed validation [{exc.code}]: {exc}"
        ) from exc
    except ValidationError as exc:
        raise ScenarioFormatError(f"scenario data failed validation: {exc}") from exc

    if any(
        len(content) > MAX_SCENARIO_MEMBER_BYTES
        for content in attachment_bytes.values()
    ):
        raise ScenarioFormatError("scenario attachment exceeds size limit")
    if sum(len(content) for content in attachment_bytes.values()) > (
        MAX_TOTAL_UNCOMPRESSED_BYTES
    ):
        raise ScenarioFormatError("scenario attachments exceed total size limit")

    ordered_names = sorted(attachment_bytes, key=str.casefold)
    attachment_hashes = {
        name: sha256(attachment_bytes[name]).hexdigest() for name in ordered_names
    }
    validation_memo = _ScenarioValidationMemo()
    try:
        persisted = ScenarioSpec.model_validate(
            scenario.model_copy(
                update={
                    "attachment_names": ordered_names,
                    "attachment_hashes": attachment_hashes,
                }
            ).model_dump(mode="python"),
            context=validation_memo,
        )
    except ValidationError as exc:
        raise ScenarioFormatError(f"scenario data failed validation: {exc}") from exc

    _validate_routing_attachment_binding(persisted, attachment_bytes)

    project_attachment_names = {
        str(name).casefold()
        for name in persisted.normalized_project.get("attachment_names", [])
    }
    missing_project_assets = project_attachment_names - {
        name.casefold() for name in ordered_names
    }
    if missing_project_assets:
        raise ScenarioFormatError(
            "normalized project attachments are missing from the scenario: "
            f"{sorted(missing_project_assets)}"
        )

    scenario_payload = _without_absent_destination_pwr_layer(
        persisted.model_dump(mode="json")
    )
    # Schema 0.1 predates the optional routing evidence reference.  Omitting a
    # null reference keeps a legacy bundle that was merely opened and saved
    # readable by v0.21; a non-null research asset remains intentionally new.
    if scenario_payload.get("routing_obstacle_asset") is None:
        scenario_payload.pop("routing_obstacle_asset", None)
    scenario_bytes = _canonical_json(scenario_payload)
    if len(scenario_bytes) > MAX_SCENARIO_MEMBER_BYTES:
        raise ScenarioFormatError("scenario.json exceeds size limit")
    entries = [
        {
            "name": name,
            "path": f"{ATTACHMENT_PREFIX}/{name}",
            "size": len(attachment_bytes[name]),
            "sha256": attachment_hashes[name],
        }
        for name in ordered_names
    ]
    manifest = {
        "format": SCENARIO_FORMAT,
        "format_version": SCENARIO_FORMAT_VERSION,
        "schema_version": persisted.schema_version,
        "app_version": persisted.app_version,
        "scenario_file": SCENARIO_FILENAME,
        "scenario_size": len(scenario_bytes),
        "scenario_sha256": sha256(scenario_bytes).hexdigest(),
        "design_fingerprint": persisted._design_fingerprint(validation_memo),
        "raw_spd_embedded": False,
        "attachments": entries,
    }
    manifest_bytes = _canonical_json(manifest)
    if len(manifest_bytes) > MAX_SCENARIO_MEMBER_BYTES:
        raise ScenarioFormatError("manifest.json exceeds size limit")
    total_size = (
        len(manifest_bytes)
        + len(scenario_bytes)
        + sum(len(content) for content in attachment_bytes.values())
    )
    if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise ScenarioFormatError("scenario archive exceeds total size limit")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        with ZipFile(temp_path, mode="w", compression=ZIP_DEFLATED) as archive:
            _write_member(archive, MANIFEST_FILENAME, manifest_bytes)
            _write_member(archive, SCENARIO_FILENAME, scenario_bytes)
            for name in ordered_names:
                _write_member(
                    archive,
                    f"{ATTACHMENT_PREFIX}/{name}",
                    attachment_bytes[name],
                )
        with temp_path.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())

        if destination.exists():
            try:
                load_scenario_bundle(destination)
            except ScenarioFormatError:
                pass
            else:
                backup = destination.with_name(destination.name + ".bak")
                shutil.copy2(destination, backup)
        os.replace(temp_path, destination)
        temp_path = None
    except (OSError, BadZipFile) as exc:
        raise ScenarioFormatError(f"cannot save scenario: {exc}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return destination


def _validate_archive_member(info: ZipInfo) -> None:
    name = info.filename
    if not name or "\\" in name:
        raise ScenarioFormatError(f"unsafe archive member {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(
            ":" in part or any(ord(character) < 32 for character in part)
            for part in path.parts
        )
    ):
        raise ScenarioFormatError(f"unsafe archive member {name!r}")
    if info.is_dir():
        raise ScenarioFormatError(f"undeclared directory member {name!r}")
    unix_mode = (info.external_attr >> 16) & 0o170000
    if unix_mode not in {0, 0o100000}:
        raise ScenarioFormatError(f"non-regular archive member {name!r}")
    if info.flag_bits & 0x1:
        raise ScenarioFormatError(f"encrypted archive member {name!r} is unsupported")
    if info.file_size < 0 or info.file_size > MAX_SCENARIO_MEMBER_BYTES:
        raise ScenarioFormatError(f"archive member {name!r} exceeds size limit")


def _manifest_int(manifest: Mapping[str, Any], key: str) -> int:
    value = manifest.get(key)
    if type(value) is not int or value < 0:
        raise ScenarioFormatError(f"manifest {key!r} must be a nonnegative integer")
    return value


def _manifest_hash(value: object, *, label: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ScenarioFormatError(f"manifest {label} is not a valid SHA-256")
    return normalized


def _read_archive_member(
    archive: ZipFile,
    member: str | ZipInfo,
    *,
    is_cancelled: Callable[[], bool] | None,
) -> bytes:
    chunks: list[bytes] = []
    with archive.open(member, mode="r") as stream:
        while True:
            if is_cancelled is not None and is_cancelled():
                raise RuntimeError("scenario load cancelled")
            chunk = stream.read(SCENARIO_LOAD_CHUNK_BYTES)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)


def _validate_archive(
    archive: ZipFile,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any], dict[str, ZipInfo]]:
    infos = archive.infolist()
    by_name: dict[str, ZipInfo] = {}
    folded_names: set[str] = set()
    for info in infos:
        _validate_archive_member(info)
        folded = info.filename.casefold()
        if folded in folded_names:
            raise ScenarioFormatError("scenario archive contains duplicate member names")
        folded_names.add(folded)
        by_name[info.filename] = info
    if sum(info.file_size for info in infos) > MAX_TOTAL_UNCOMPRESSED_BYTES:
        raise ScenarioFormatError("scenario archive exceeds total size limit")
    if MANIFEST_FILENAME not in by_name or SCENARIO_FILENAME not in by_name:
        raise ScenarioFormatError(
            "scenario archive is missing manifest.json or scenario.json"
        )
    manifest = _strict_json(
        _read_archive_member(
            archive, MANIFEST_FILENAME, is_cancelled=is_cancelled
        ),
        label="scenario manifest",
    )
    if manifest.get("format") != SCENARIO_FORMAT:
        raise ScenarioFormatError("unsupported scenario archive format")
    if manifest.get("format_version") != SCENARIO_FORMAT_VERSION:
        raise ScenarioFormatError(
            f"unsupported scenario format version {manifest.get('format_version')!r}"
        )
    if manifest.get("scenario_file") != SCENARIO_FILENAME:
        raise ScenarioFormatError("manifest scenario_file is not canonical")
    if manifest.get("raw_spd_embedded") is not False:
        raise ScenarioFormatError("scenario manifest must declare raw_spd_embedded=false")
    return manifest, by_name


def load_scenario_bundle(
    path: str | os.PathLike[str],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> ScenarioBundle:
    """Load a scenario only after validating every member and declared hash."""

    source = Path(path)
    try:
        if is_cancelled is not None and is_cancelled():
            raise RuntimeError("scenario load cancelled")
        with ZipFile(source, mode="r") as archive:
            manifest, by_name = _validate_archive(
                archive, is_cancelled=is_cancelled
            )
            scenario_info = by_name[SCENARIO_FILENAME]
            expected_scenario_size = _manifest_int(manifest, "scenario_size")
            expected_scenario_hash = _manifest_hash(
                manifest.get("scenario_sha256"), label="scenario SHA-256"
            )
            if scenario_info.file_size != expected_scenario_size:
                raise ScenarioFormatError(
                    "scenario.json size does not match its manifest"
                )

            raw_entries = manifest.get("attachments")
            if not isinstance(raw_entries, list):
                raise ScenarioFormatError("manifest attachments must be a list")
            declared_paths: set[str] = set()
            declared_names: set[str] = set()
            parsed_entries: list[tuple[str, str, int, str]] = []
            for raw_entry in raw_entries:
                if not isinstance(raw_entry, dict):
                    raise ScenarioFormatError(
                        "manifest attachment entries must be JSON objects"
                    )
                try:
                    name = _safe_attachment_name(str(raw_entry["name"]))
                    archive_path = str(raw_entry["path"])
                except (KeyError, TypeError) as exc:
                    raise ScenarioFormatError("invalid manifest attachment entry") from exc
                expected_path = f"{ATTACHMENT_PREFIX}/{name}"
                if archive_path != expected_path:
                    raise ScenarioFormatError(
                        f"attachment path for {name!r} is not canonical"
                    )
                name_key = name.casefold()
                path_key = archive_path.casefold()
                if name_key in declared_names or path_key in {
                    item.casefold() for item in declared_paths
                }:
                    raise ScenarioFormatError(
                        f"attachment {name!r} is declared more than once"
                    )
                declared_names.add(name_key)
                declared_paths.add(archive_path)
                if archive_path not in by_name:
                    raise ScenarioFormatError(
                        f"declared scenario attachment {name!r} is missing"
                    )
                expected_size = raw_entry.get("size")
                if type(expected_size) is not int or expected_size < 0:
                    raise ScenarioFormatError(
                        f"attachment {name!r} has an invalid declared size"
                    )
                if expected_size > MAX_SCENARIO_MEMBER_BYTES:
                    raise ScenarioFormatError(
                        f"attachment {name!r} exceeds size limit"
                    )
                expected_hash = _manifest_hash(
                    raw_entry.get("sha256"), label=f"attachment {name!r} SHA-256"
                )
                if by_name[archive_path].file_size != expected_size:
                    raise ScenarioFormatError(
                        f"scenario attachment {name!r} has wrong size"
                    )
                parsed_entries.append(
                    (name, archive_path, expected_size, expected_hash)
                )

            allowed_members = {
                MANIFEST_FILENAME,
                SCENARIO_FILENAME,
                *declared_paths,
            }
            if set(by_name) != allowed_members:
                raise ScenarioFormatError("scenario archive contains undeclared members")

            scenario_bytes = _read_archive_member(
                archive, SCENARIO_FILENAME, is_cancelled=is_cancelled
            )
            if len(scenario_bytes) != expected_scenario_size:
                raise ScenarioFormatError(
                    "scenario.json size does not match its manifest"
                )
            if sha256(scenario_bytes).hexdigest() != expected_scenario_hash:
                raise ScenarioFormatError(
                    "scenario.json hash does not match its manifest"
                )
            raw_scenario = _strict_json(scenario_bytes, label="scenario.json")
            if raw_scenario.get("schema_version") != manifest.get("schema_version"):
                raise ScenarioFormatError(
                    "scenario schema version disagrees with manifest"
                )
            validation_memo = _ScenarioValidationMemo()
            try:
                scenario = ScenarioSpec.model_validate(
                    raw_scenario, context=validation_memo
                )
            except ValidationError as exc:
                raise ScenarioFormatError(
                    f"scenario data failed schema validation: {exc}"
                ) from exc
            if scenario.schema_version != SCENARIO_SCHEMA_VERSION:
                raise ScenarioFormatError(
                    f"unsupported scenario schema {scenario.schema_version!r}"
                )
            if scenario.app_version != manifest.get("app_version"):
                raise ScenarioFormatError("scenario app version disagrees with manifest")

            attachments: dict[str, bytes] = {}
            for name, archive_path, expected_size, expected_hash in parsed_entries:
                content = _read_archive_member(
                    archive, archive_path, is_cancelled=is_cancelled
                )
                if len(content) != expected_size:
                    raise ScenarioFormatError(
                        f"scenario attachment {name!r} has wrong size"
                    )
                if sha256(content).hexdigest() != expected_hash:
                    raise ScenarioFormatError(
                        f"scenario attachment {name!r} failed hash check"
                    )
                attachments[name] = content

            names_by_key = {name.casefold(): name for name in attachments}
            if {name.casefold() for name in scenario.attachment_names} != set(
                names_by_key
            ):
                raise ScenarioFormatError(
                    "scenario attachment names do not match manifest attachments"
                )
            actual_hashes = {
                name: sha256(content).hexdigest()
                for name, content in attachments.items()
            }
            scenario_hashes_by_key = {
                name.casefold(): digest
                for name, digest in scenario.attachment_hashes.items()
            }
            if {
                name.casefold(): digest for name, digest in actual_hashes.items()
            } != scenario_hashes_by_key:
                raise ScenarioFormatError(
                    "scenario attachment hashes do not match manifest attachments"
                )
            _validate_routing_attachment_binding(scenario, attachments)
            expected_fingerprint = _manifest_hash(
                manifest.get("design_fingerprint"), label="design fingerprint"
            )
            if (
                scenario._design_fingerprint(validation_memo)
                != expected_fingerprint
            ):
                raise ScenarioFormatError(
                    "scenario design fingerprint does not match its manifest"
                )
            try:
                validate_project_topology_storage_envelope(
                    scenario.normalized_project, attachments
                )
            except SurfaceCertificateAssetError as exc:
                raise ScenarioFormatError(
                    "surface certificate attachment failed validation "
                    f"[{exc.code}]: {exc}"
                ) from exc
            try:
                raw_manifest = validate_project_raw_spatial_contact_asset_envelope(
                    scenario.normalized_project, attachments
                )
                if (
                    raw_manifest is not None
                    and raw_manifest["source_sha256"] != scenario.source.sha256
                ):
                    raise RawSpatialContactAssetError(
                        "RAW_SPATIAL_BINDING_MISMATCH",
                        "raw spatial source differs from the scenario source identity",
                    )
            except RawSpatialContactAssetError as exc:
                raise ScenarioFormatError(
                    "raw spatial contact attachment failed validation "
                    f"[{exc.code}]: {exc}"
                ) from exc
            return ScenarioBundle(scenario=scenario, attachments=attachments)
    except FileNotFoundError as exc:
        raise ScenarioFormatError(f"scenario file does not exist: {source}") from exc
    except BadZipFile as exc:
        raise ScenarioFormatError("scenario file is not a valid ZIP archive") from exc
    except OSError as exc:
        raise ScenarioFormatError(f"cannot read scenario file: {exc}") from exc


def load_scenario(path: str | os.PathLike[str]) -> ScenarioSpec:
    return load_scenario_bundle(path).scenario


def save_scenario_bundle(
    bundle: ScenarioBundle, path: str | os.PathLike[str]
) -> Path:
    return save_scenario(bundle.scenario, path, attachments=bundle.attachments)


def read_scenario_attachment(path: str | os.PathLike[str], name: str) -> bytes:
    safe_name = _safe_attachment_name(name)
    bundle = load_scenario_bundle(path)
    try:
        return bundle.attachments[safe_name]
    except KeyError as exc:
        raise ScenarioFormatError(
            f"scenario attachment {safe_name!r} does not exist"
        ) from exc


def load_scenario_with_recovery(
    path: str | os.PathLike[str],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> ScenarioBundle:
    """Load the primary archive, falling back to its last validated backup."""

    source = Path(path)
    try:
        return load_scenario_bundle(source, is_cancelled=is_cancelled)
    except ScenarioFormatError as original_error:
        backup = source.with_name(source.name + ".bak")
        if not backup.is_file():
            raise
        try:
            bundle = load_scenario_bundle(backup, is_cancelled=is_cancelled)
        except ScenarioFormatError:
            raise original_error
        return replace(
            bundle,
            recovered_from=backup,
            recovery_reason=str(original_error),
        )


__all__ = [
    "ATTACHMENT_PREFIX",
    "MANIFEST_FILENAME",
    "MAX_SCENARIO_MEMBER_BYTES",
    "MAX_TOTAL_UNCOMPRESSED_BYTES",
    "SCENARIO_FILENAME",
    "SCENARIO_FORMAT",
    "SCENARIO_FORMAT_VERSION",
    "ScenarioBundle",
    "ScenarioFormatError",
    "load_scenario",
    "load_scenario_bundle",
    "load_scenario_with_recovery",
    "read_scenario_attachment",
    "save_scenario",
    "save_scenario_bundle",
]
