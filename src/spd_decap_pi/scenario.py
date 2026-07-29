"""Validated scenario contracts for SPD Decap PI Evaluator.

The scenario is intentionally separate from :mod:`spd_decap_pi._core` project persistence:
it records edits made to an already-routed SPD design while keeping the raw SPD
as an external, hash-identified source.  UI-only state is persisted for a useful
resume experience, but is excluded from the electrical design fingerprint.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any, Mapping

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from spd_decap_pi._core.domain import ProjectSpec

from .version import __version__


SCENARIO_SCHEMA_VERSION = "0.1"
SCENARIO_APP_VERSION = __version__
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\Z")


def _canonical_json(data: object) -> bytes:
    """Return the one canonical JSON representation used by all hashes."""

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


def _hash_payload(data: object) -> str:
    return sha256(_canonical_json(data)).hexdigest()


def _validate_sha256(value: str, *, label: str = "SHA-256") -> str:
    normalized = value.strip().lower()
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be 64 hexadecimal characters")
    return normalized


class ScenarioModel(BaseModel):
    """Strict base model shared by persisted scenario objects."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class SourceIdentity(ScenarioModel):
    """External SPD identity; the source bytes are never stored in a scenario."""

    path: str = Field(min_length=1)
    name: str = Field(min_length=1)
    size_bytes: int = Field(
        ge=0,
        validation_alias=AliasChoices("size_bytes", "size"),
    )
    sha256: str

    @field_validator("sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        return _validate_sha256(value, label="source SHA-256")

    @property
    def size(self) -> int:
        """Compatibility spelling for callers that use the manifest term size."""

        return self.size_bytes

    @classmethod
    def from_path(cls, path: str | Path) -> "SourceIdentity":
        source = Path(path)
        if not source.is_file():
            raise ValueError(f"SPD source does not exist: {source}")
        before = source.stat()
        digest = sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = source.stat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ValueError(f"SPD source changed while it was being hashed: {source}")
        return cls(
            path=str(source.resolve()),
            name=source.name,
            size_bytes=after.st_size,
            sha256=digest.hexdigest(),
        )


class ScenarioPoint(ScenarioModel):
    x_um: float
    y_um: float

    @field_validator("x_um", "y_um")
    @classmethod
    def finite_coordinate(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("scenario coordinates must be finite")
        return value


class ScenarioPad(ScenarioPoint):
    """Actual SPD terminal coordinate and its physical connection metadata."""

    layer: str | None = None
    padstack: str | None = None


class ScenarioSide(StrEnum):
    TOP = "TOP"
    BOTTOM = "BOTTOM"
    UNKNOWN = "UNKNOWN"


class RailEligibility(ScenarioModel):
    """Precomputed assignment eligibility at a decap's actual power pad."""

    rail_id: str = Field(min_length=1)
    net: str = Field(min_length=1)
    pwr_layer: str = Field(min_length=1)
    gnd_layer: str = Field(min_length=1)
    via_template_id: str | None = None
    allowed: bool
    reason: str | None = None

    @model_validator(mode="after")
    def actionable_reason(self) -> "RailEligibility":
        if not self.allowed and not self.reason:
            raise ValueError("ineligible rails require a reason")
        return self


class ScenarioDecap(ScenarioModel):
    """One imported decap and its editable electrical assignment."""

    refdes: str = Field(min_length=1)
    center: ScenarioPoint
    pwr_pad: ScenarioPad
    gnd_pad: ScenarioPad
    side: ScenarioSide = ScenarioSide.UNKNOWN
    start_layer: str | None = None
    attach_layer: str | None = None
    footprint: str = Field(min_length=1)
    source_net: str = Field(min_length=1)
    current_net: str = Field(min_length=1)
    source_rail_id: str = Field(min_length=1)
    current_rail_id: str = Field(min_length=1)
    source_model_id: str | None = None
    model_id: str | None = None
    enabled: bool = True
    source_mounted: bool = True
    eligibility: dict[str, RailEligibility] = Field(default_factory=dict)

    @field_validator("side", mode="before")
    @classmethod
    def normalize_side(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().upper().replace("-", "_")
            if normalized in {"TOPAIR", "TOP_AIR"}:
                return ScenarioSide.TOP
            return normalized
        return value

    @field_validator("eligibility")
    @classmethod
    def keyed_by_rail_id(
        cls, value: dict[str, RailEligibility]
    ) -> dict[str, RailEligibility]:
        seen: set[str] = set()
        for key, item in value.items():
            normalized = key.strip().casefold()
            if not normalized or normalized in seen:
                raise ValueError("eligibility rail keys must be nonblank and unique")
            seen.add(normalized)
            if normalized != item.rail_id.casefold():
                raise ValueError(
                    f"eligibility key {key!r} does not match rail {item.rail_id!r}"
                )
        return value

    @property
    def x_um(self) -> float:
        return self.center.x_um

    @property
    def y_um(self) -> float:
        return self.center.y_um

    @property
    def power_net(self) -> str:
        return self.current_net


class ScenarioResultKey(ScenarioModel):
    """Inputs that make a cached electrical result reusable."""

    design_fingerprint: str
    rail_id: str = Field(min_length=1)
    settings_sha256: str
    solver_version: str = Field(min_length=1)

    @field_validator("design_fingerprint", "settings_sha256")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        return _validate_sha256(value)

    @property
    def cache_key(self) -> str:
        return _hash_payload(self.model_dump(mode="json"))

    @classmethod
    def from_settings(
        cls,
        *,
        design_fingerprint: str,
        rail_id: str,
        settings: Mapping[str, Any] | BaseModel,
        solver_version: str,
    ) -> "ScenarioResultKey":
        if isinstance(settings, BaseModel):
            payload: object = settings.model_dump(mode="json")
        else:
            payload = dict(settings)
        return cls(
            design_fingerprint=design_fingerprint,
            rail_id=rail_id,
            settings_sha256=_hash_payload(payload),
            solver_version=solver_version,
        )


class CachedEvaluationMetadata(ScenarioModel):
    """Small persisted index entry; result samples live in a hashed attachment."""

    result_key: ScenarioResultKey
    attachment_name: str = Field(min_length=1)
    attachment_sha256: str
    created_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    summary: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attachment_sha256")
    @classmethod
    def valid_attachment_hash(cls, value: str) -> str:
        return _validate_sha256(value, label="evaluation attachment SHA-256")

    @field_validator("created_at_utc")
    @classmethod
    def timezone_is_explicit(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at_utc must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("summary")
    @classmethod
    def summary_is_json_safe(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = _canonical_json(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("evaluation summary must contain finite JSON values") from exc
        return json.loads(encoded)

    @property
    def cache_key(self) -> str:
        return self.result_key.cache_key


class ScenarioSpec(ScenarioModel):
    """Complete editable SPD decap scenario, including resumable UI state."""

    schema_version: str = SCENARIO_SCHEMA_VERSION
    app_version: str = SCENARIO_APP_VERSION
    source: SourceIdentity
    normalized_project: dict[str, Any]
    decaps: list[ScenarioDecap] = Field(default_factory=list)
    net_colors: dict[str, str] = Field(default_factory=dict)
    selected_refdes: list[str] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    attachment_names: list[str] = Field(default_factory=list)
    attachment_hashes: dict[str, str] = Field(default_factory=dict)
    evaluation_cache: dict[str, CachedEvaluationMetadata] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("evaluation_cache", "cached_evaluations"),
    )

    @field_validator("schema_version")
    @classmethod
    def supported_schema(cls, value: str) -> str:
        if value != SCENARIO_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported scenario schema {value!r}; "
                f"expected {SCENARIO_SCHEMA_VERSION!r}"
            )
        return value

    @field_validator("normalized_project", mode="before")
    @classmethod
    def validate_normalized_project(cls, value: object) -> dict[str, Any]:
        if isinstance(value, ProjectSpec):
            project = value
        else:
            project = ProjectSpec.model_validate(value)
        return project.model_dump(mode="json")

    @field_validator("net_colors")
    @classmethod
    def valid_net_colors(cls, value: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        seen: set[str] = set()
        for raw_net, raw_color in value.items():
            net = raw_net.strip()
            key = net.casefold()
            if not net or key in seen:
                raise ValueError("net color keys must be nonblank and unique")
            color = raw_color.strip().upper()
            if _COLOR_RE.fullmatch(color) is None:
                raise ValueError(
                    f"color for net {net!r} must be #RRGGBB or #RRGGBBAA"
                )
            seen.add(key)
            result[net] = color
        return result

    @field_validator("attachment_hashes")
    @classmethod
    def valid_attachment_hashes(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            name: _validate_sha256(digest, label=f"attachment {name!r} SHA-256")
            for name, digest in value.items()
        }

    @model_validator(mode="after")
    def consistent_indexes(self) -> "ScenarioSpec":
        refdes_keys = [item.refdes.casefold() for item in self.decaps]
        if len(refdes_keys) != len(set(refdes_keys)):
            raise ValueError("scenario decap REFDES values must be unique")

        selected_keys = [item.casefold() for item in self.selected_refdes]
        if len(selected_keys) != len(set(selected_keys)):
            raise ValueError("selected REFDES values must be unique")
        unknown_selected = set(selected_keys) - set(refdes_keys)
        if unknown_selected:
            raise ValueError(
                f"selected REFDES values are absent from scenario: "
                f"{sorted(unknown_selected)}"
            )

        attachment_keys = [item.casefold() for item in self.attachment_names]
        if len(attachment_keys) != len(set(attachment_keys)):
            raise ValueError("scenario attachment names must be unique")
        hash_by_key = {name.casefold(): digest for name, digest in self.attachment_hashes.items()}
        if len(hash_by_key) != len(self.attachment_hashes):
            raise ValueError("scenario attachment hash names must be unique")
        if self.attachment_hashes and set(hash_by_key) != set(attachment_keys):
            raise ValueError("attachment names and attachment hashes must match")

        for cache_hash, metadata in self.evaluation_cache.items():
            if cache_hash != metadata.cache_key:
                raise ValueError(
                    f"evaluation cache key {cache_hash!r} does not match its result key"
                )
            attached_hash = hash_by_key.get(metadata.attachment_name.casefold())
            if attached_hash is not None and attached_hash != metadata.attachment_sha256:
                raise ValueError(
                    f"evaluation cache attachment {metadata.attachment_name!r} hash disagrees"
                )
        return self

    @property
    def base_project(self) -> ProjectSpec:
        return ProjectSpec.model_validate(self.normalized_project)

    def _design_payload(self) -> dict[str, Any]:
        cached_attachment_keys = {
            metadata.attachment_name.casefold()
            for metadata in self.evaluation_cache.values()
        }
        electrical_attachment_hashes = {
            name: digest
            for name, digest in self.attachment_hashes.items()
            if name.casefold() not in cached_attachment_keys
        }
        decaps = [
            item.model_dump(mode="json")
            for item in sorted(self.decaps, key=lambda entry: entry.refdes.casefold())
        ]
        return {
            "schema_version": self.schema_version,
            "source": {
                "size_bytes": self.source.size_bytes,
                "sha256": self.source.sha256,
            },
            "normalized_project": self.normalized_project,
            "decaps": decaps,
            "attachment_hashes": electrical_attachment_hashes,
        }

    @property
    def design_fingerprint(self) -> str:
        """Hash of electrical design state, excluding UI/cache/revision/source path."""

        return _hash_payload(self._design_payload())

    def matching_cached_evaluations(self) -> dict[str, CachedEvaluationMetadata]:
        """Return only results produced for the current electrical design."""

        fingerprint = self.design_fingerprint
        attachment_hashes = {
            name.casefold(): digest for name, digest in self.attachment_hashes.items()
        }
        return {
            key: value
            for key, value in self.evaluation_cache.items()
            if value.result_key.design_fingerprint == fingerprint
            and attachment_hashes.get(value.attachment_name.casefold())
            == value.attachment_sha256
        }


def scenario_design_fingerprint(scenario: ScenarioSpec) -> str:
    """Functional spelling for adapters that do not use model properties."""

    return scenario.design_fingerprint


__all__ = [
    "CachedEvaluationMetadata",
    "RailEligibility",
    "SCENARIO_APP_VERSION",
    "SCENARIO_SCHEMA_VERSION",
    "ScenarioDecap",
    "ScenarioPad",
    "ScenarioPoint",
    "ScenarioResultKey",
    "ScenarioSide",
    "ScenarioSpec",
    "SourceIdentity",
    "scenario_design_fingerprint",
]
