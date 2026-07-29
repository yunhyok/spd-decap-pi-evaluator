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
from pathlib import Path, PurePosixPath
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


class BaselineModelBinding(ScenarioModel):
    """One model assignment frozen for an original-layout rail evaluation."""

    model_config = ConfigDict(frozen=True)

    refdes: str = Field(min_length=1)
    model_id: str = Field(min_length=1)


class BaselineCapture(ScenarioModel):
    """Immutable model bindings used with the SPD-provided physical state.

    PowerSI files often identify a mounted capacitor without embedding a usable
    SPICE model.  The first confirmed evaluation therefore freezes the current
    model assignment for those original locations, per rail.  Later model,
    enable, and PWR edits cannot rewrite this capture.
    """

    model_config = ConfigDict(frozen=True)

    rail_id: str = Field(min_length=1)
    source_sha256: str
    source_state_sha256: str
    evaluation_input_sha256: str
    model_bindings: tuple[BaselineModelBinding, ...]
    captured_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator(
        "source_sha256", "source_state_sha256", "evaluation_input_sha256"
    )
    @classmethod
    def valid_hash(cls, value: str) -> str:
        return _validate_sha256(value)

    @field_validator("captured_at_utc")
    @classmethod
    def timezone_is_explicit(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at_utc must include a timezone")
        return value.astimezone(timezone.utc)

    @field_validator("model_bindings")
    @classmethod
    def unique_refdes(
        cls, value: tuple[BaselineModelBinding, ...]
    ) -> tuple[BaselineModelBinding, ...]:
        keys = [item.refdes.casefold() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("baseline model binding REFDES values must be unique")
        return tuple(sorted(value, key=lambda item: item.refdes.casefold()))

    @property
    def capture_fingerprint(self) -> str:
        return _hash_payload(
            {
                "rail_id": self.rail_id,
                "source_sha256": self.source_sha256,
                "source_state_sha256": self.source_state_sha256,
                "evaluation_input_sha256": self.evaluation_input_sha256,
                "model_bindings": [
                    item.model_dump(mode="json") for item in self.model_bindings
                ],
            }
        )


class EvaluationRole(StrEnum):
    BASELINE = "baseline"
    TUNED = "tuned"


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
    role: EvaluationRole = EvaluationRole.TUNED
    baseline_capture_sha256: str | None = None
    created_at_utc: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    summary: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attachment_name")
    @classmethod
    def result_attachment_namespace(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            "\\" in value
            or path.is_absolute()
            or path.as_posix() != value
            or len(path.parts) < 2
            or path.parts[0].casefold() != "results"
            or path.suffix.casefold() != ".json"
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(
                "evaluation cache attachments must be safe results/*.json paths"
            )
        return value

    @field_validator("attachment_sha256")
    @classmethod
    def valid_attachment_hash(cls, value: str) -> str:
        return _validate_sha256(value, label="evaluation attachment SHA-256")

    @field_validator("baseline_capture_sha256")
    @classmethod
    def valid_optional_capture_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_sha256(value, label="baseline capture SHA-256")

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

    @model_validator(mode="after")
    def baseline_has_capture_identity(self) -> "CachedEvaluationMetadata":
        if self.role == EvaluationRole.BASELINE and self.baseline_capture_sha256 is None:
            raise ValueError("baseline evaluation metadata requires capture identity")
        if self.role != EvaluationRole.BASELINE and self.baseline_capture_sha256 is not None:
            raise ValueError("only baseline evaluation metadata may name a capture")
        return self

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
    baseline_captures: dict[str, BaselineCapture] = Field(default_factory=dict)

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
            if attached_hash is None:
                # v0.2.0 exposed a metadata-only tuned-result contract without
                # ever writing runtime result attachments.  Preserve those
                # legacy scenarios under schema 0.1; matching_cached_evaluations
                # ignores the orphan entry.  New Original results are always
                # attachment-backed and must fail closed.
                if metadata.role == EvaluationRole.BASELINE:
                    raise ValueError(
                        f"evaluation cache attachment {metadata.attachment_name!r} is missing"
                    )
                continue
            if attached_hash != metadata.attachment_sha256:
                raise ValueError(
                    f"evaluation cache attachment {metadata.attachment_name!r} hash disagrees"
                )
        cache_attachment_keys = [
            metadata.attachment_name.casefold()
            for metadata in self.evaluation_cache.values()
        ]
        if len(cache_attachment_keys) != len(set(cache_attachment_keys)):
            raise ValueError("evaluation cache attachment names must be unique")
        project_attachment_keys = {
            str(name).casefold()
            for name in self.normalized_project.get("attachment_names", [])
        }
        masked_project_assets = project_attachment_keys.intersection(
            cache_attachment_keys
        )
        if masked_project_assets:
            raise ValueError(
                "evaluation cache cannot reference normalized project assets"
            )

        rail_by_key = {
            item.rail_id.casefold(): item.rail_id for item in self.base_project.rails
        }
        decap_by_key = {item.refdes.casefold(): item for item in self.decaps}
        capture_keys = [key.casefold() for key in self.baseline_captures]
        if len(capture_keys) != len(set(capture_keys)):
            raise ValueError("baseline capture rail keys must be unique")
        for raw_rail_id, capture in self.baseline_captures.items():
            rail_key = raw_rail_id.casefold()
            if rail_key != capture.rail_id.casefold():
                raise ValueError(
                    f"baseline capture key {raw_rail_id!r} does not match rail "
                    f"{capture.rail_id!r}"
                )
            if rail_key not in rail_by_key:
                raise ValueError(
                    f"baseline capture rail {capture.rail_id!r} is absent from project"
                )
            if capture.source_sha256 != self.source.sha256:
                raise ValueError("baseline capture source identity does not match scenario")
            if capture.source_state_sha256 != self.source_state_fingerprint:
                raise ValueError("baseline capture physical source state has changed")
            expected = {
                item.refdes.casefold()
                for item in self.decaps
                if item.source_mounted
                and item.source_rail_id.casefold() == rail_key
            }
            actual = {item.refdes.casefold() for item in capture.model_bindings}
            if actual != expected:
                raise ValueError(
                    f"baseline capture for {capture.rail_id!r} must bind every "
                    "originally mounted decap on that rail"
                )
            if any(item.refdes.casefold() not in decap_by_key for item in capture.model_bindings):
                raise ValueError("baseline capture contains an unknown REFDES")
            for binding in capture.model_bindings:
                decap = decap_by_key[binding.refdes.casefold()]
                if (
                    decap.source_model_id is not None
                    and binding.model_id.casefold()
                    != decap.source_model_id.casefold()
                ):
                    raise ValueError(
                        f"baseline binding for {binding.refdes!r} must use its "
                        "SPD source model"
                    )
            if capture.evaluation_input_sha256 != self.baseline_evaluation_input_fingerprint(
                capture.rail_id, capture.model_bindings
            ):
                raise ValueError(
                    f"baseline solver inputs for {capture.rail_id!r} have changed"
                )

        capture_by_rail = {
            capture.rail_id.casefold(): capture
            for capture in self.baseline_captures.values()
        }
        for metadata in self.evaluation_cache.values():
            if metadata.role != EvaluationRole.BASELINE:
                continue
            capture = capture_by_rail.get(metadata.result_key.rail_id.casefold())
            if capture is None:
                raise ValueError(
                    f"baseline evaluation for {metadata.result_key.rail_id!r} "
                    "has no baseline capture"
                )
            if metadata.baseline_capture_sha256 != capture.capture_fingerprint:
                raise ValueError(
                    f"baseline evaluation for {metadata.result_key.rail_id!r} "
                    "does not match its capture"
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

    @property
    def source_state_fingerprint(self) -> str:
        """Hash immutable placement/source assignment data, excluding tuning."""

        source_decaps = []
        for item in sorted(self.decaps, key=lambda entry: entry.refdes.casefold()):
            source_decaps.append(
                {
                    "refdes": item.refdes,
                    "center": item.center.model_dump(mode="json"),
                    "pwr_pad": item.pwr_pad.model_dump(mode="json"),
                    "gnd_pad": item.gnd_pad.model_dump(mode="json"),
                    "side": item.side,
                    "start_layer": item.start_layer,
                    "attach_layer": item.attach_layer,
                    "footprint": item.footprint,
                    "source_net": item.source_net,
                    "source_rail_id": item.source_rail_id,
                    "source_model_id": item.source_model_id,
                    "source_mounted": item.source_mounted,
                    "eligibility": {
                        key: value.model_dump(mode="json")
                        for key, value in sorted(
                            item.eligibility.items(), key=lambda pair: pair[0].casefold()
                        )
                    },
                }
            )
        return _hash_payload(
            {"source_sha256": self.source.sha256, "decaps": source_decaps}
        )

    def baseline_evaluation_input_fingerprint(
        self,
        rail_id: str,
        model_bindings: list[BaselineModelBinding]
        | tuple[BaselineModelBinding, ...],
    ) -> str:
        """Hash only immutable solver inputs used by one Original rail.

        The standalone application may add models to the tuning library after
        the first evaluation.  Unused models, their source attachments, and
        library bookkeeping must not invalidate the saved Original result.
        Definitions of models actually bound to the Original rail remain part
        of this identity and therefore cannot drift silently.
        """

        rail = next(
            (
                item
                for item in self.base_project.rails
                if item.rail_id.casefold() == rail_id.casefold()
            ),
            None,
        )
        if rail is None:
            raise ValueError(f"unknown baseline rail {rail_id!r}")
        bindings = tuple(model_bindings)
        binding_models = {item.model_id.casefold() for item in bindings}
        models = {
            item.model_id.casefold(): item for item in self.base_project.cap_models
        }
        missing_models = binding_models - set(models)
        if missing_models:
            raise ValueError(
                "baseline capture references unknown model(s): "
                + ", ".join(sorted(missing_models))
            )
        decaps = {item.refdes.casefold(): item for item in self.decaps}
        for binding in bindings:
            decap = decaps.get(binding.refdes.casefold())
            if decap is None:
                raise ValueError(
                    f"baseline capture references unknown REFDES {binding.refdes!r}"
                )
            model = models[binding.model_id.casefold()]
            if model.footprint.casefold() != decap.footprint.casefold():
                raise ValueError(
                    f"{binding.refdes}: baseline model footprint does not match"
                )

        project = self.base_project.model_dump(mode="json")
        project.pop("attachment_names", None)
        project.pop("metadata", None)
        project["cap_models"] = [
            models[key].model_dump(mode="json") for key in sorted(binding_models)
        ]
        return _hash_payload(
            {
                "rail_id": rail.rail_id,
                "project": project,
                "source_state_sha256": self.source_state_fingerprint,
                "model_bindings": [
                    item.model_dump(mode="json")
                    for item in sorted(bindings, key=lambda value: value.refdes.casefold())
                ],
            }
        )

    def with_baseline_captures(self, rail_ids: list[str] | tuple[str, ...]) -> "ScenarioSpec":
        """Return a copy with missing per-rail original model maps frozen.

        Callers must explicitly inform the user when current model assignments
        are used because the SPD did not provide ``source_model_id``.
        """

        rail_by_key = {
            item.rail_id.casefold(): item.rail_id for item in self.base_project.rails
        }
        captures = dict(self.baseline_captures)
        for raw_rail_id in rail_ids:
            rail_key = raw_rail_id.casefold()
            try:
                rail_id = rail_by_key[rail_key]
            except KeyError as exc:
                raise ValueError(f"unknown baseline rail {raw_rail_id!r}") from exc
            if any(key.casefold() == rail_key for key in captures):
                continue
            bindings: list[BaselineModelBinding] = []
            missing: list[str] = []
            for decap in self.decaps:
                if (
                    not decap.source_mounted
                    or decap.source_rail_id.casefold() != rail_key
                ):
                    continue
                model_id = decap.source_model_id or decap.model_id
                if model_id is None:
                    missing.append(decap.refdes)
                else:
                    bindings.append(
                        BaselineModelBinding(refdes=decap.refdes, model_id=model_id)
                    )
            if missing:
                preview = ", ".join(sorted(missing, key=str.casefold)[:12])
                suffix = "..." if len(missing) > 12 else ""
                raise ValueError(
                    f"{rail_id}: assign initial models to {len(missing):,} originally "
                    f"mounted decap(s) before baseline evaluation ({preview}{suffix})"
                )
            captures[rail_id] = BaselineCapture(
                rail_id=rail_id,
                source_sha256=self.source.sha256,
                source_state_sha256=self.source_state_fingerprint,
                evaluation_input_sha256=self.baseline_evaluation_input_fingerprint(
                    rail_id, bindings
                ),
                model_bindings=tuple(bindings),
            )
        return ScenarioSpec.model_validate(
            {**self.model_dump(mode="python"), "baseline_captures": captures}
        )

    def original_configuration(self, rail_id: str) -> "ScenarioSpec":
        """Return the immutable original physical state for one captured rail."""

        capture = next(
            (
                item
                for key, item in self.baseline_captures.items()
                if key.casefold() == rail_id.casefold()
            ),
            None,
        )
        if capture is None:
            raise ValueError(f"baseline capture for rail {rail_id!r} does not exist")
        models = {
            item.refdes.casefold(): item.model_id for item in capture.model_bindings
        }
        original_decaps: list[ScenarioDecap] = []
        capture_rail_key = capture.rail_id.casefold()
        for decap in self.decaps:
            model_id = decap.source_model_id
            if (
                decap.source_mounted
                and decap.source_rail_id.casefold() == capture_rail_key
            ):
                model_id = models[decap.refdes.casefold()]
            original_decaps.append(
                ScenarioDecap.model_validate(
                    {
                        **decap.model_dump(mode="python"),
                        "current_net": decap.source_net,
                        "current_rail_id": decap.source_rail_id,
                        "model_id": model_id,
                        "enabled": decap.source_mounted,
                    }
                )
            )
        return ScenarioSpec.model_validate(
            {**self.model_dump(mode="python"), "decaps": original_decaps}
        )

    def matching_cached_evaluations(
        self,
        *,
        design_fingerprint: str | None = None,
        role: EvaluationRole | None = None,
    ) -> dict[str, CachedEvaluationMetadata]:
        """Return hash-valid results for one explicit or current design."""

        fingerprint = design_fingerprint or self.design_fingerprint
        attachment_hashes = {
            name.casefold(): digest for name, digest in self.attachment_hashes.items()
        }
        return {
            key: value
            for key, value in self.evaluation_cache.items()
            if value.result_key.design_fingerprint == fingerprint
            and (role is None or value.role == role)
            and attachment_hashes.get(value.attachment_name.casefold())
            == value.attachment_sha256
        }


def scenario_design_fingerprint(scenario: ScenarioSpec) -> str:
    """Functional spelling for adapters that do not use model properties."""

    return scenario.design_fingerprint


__all__ = [
    "BaselineCapture",
    "BaselineModelBinding",
    "CachedEvaluationMetadata",
    "EvaluationRole",
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
