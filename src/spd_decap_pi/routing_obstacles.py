"""Immutable signal-routing clearance evidence for De-cap Distribution.

The source SPD can be unavailable when a saved scenario is reopened.  This
module therefore owns a small, deterministic, versioned attachment containing
only the routing evidence needed by Distribution.  It deliberately does not
turn unresolved source grammar into permission: when protection is enabled,
``UNKNOWN`` is a hard rejection just like ``BLOCKED``.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
import json
from math import floor, hypot, isfinite
from threading import RLock
from typing import Any, Iterable, Mapping, Sequence
import zlib

from ._core.via_model import SOLID_COPPER_FILLED_MICROVIA, classify_via_conductor


ROUTING_ASSET_SCHEMA = "SPD_SIGNAL_ROUTING_V1"
ROUTING_POLICY_VERSION = "SIGNAL_NET_ONLY_RESEARCH_V1"
ROUTING_ASSET_MAGIC = b"SPDROUTE1\n"
ROUTING_GEOMETRY_EPS_UM = 1.0e-6
MAX_ROUTING_ASSET_EXPANDED_BYTES = 256 * 1024 * 1024
_INDEX_CELL_UM = 250.0
_INDEX_CACHE_LIMIT = 8
_MAX_ROUTING_ABS_COORD_UM = 1.0e12
_MAX_ROUTING_WIDTH_UM = 1.0e9
_MAX_TILES_PER_SEGMENT = 65_536
_MAX_TILE_INDEX_ENTRIES_PER_LAYER = 2_000_000

# This policy is deliberately separate from the signal-routing asset schema.
# It is a structural Distribution gate: a source-proven MLO transition may
# not be treated as an immutable vertical column until a translated rebuild
# recipe is independently engineered and persisted.
MLO_TRANSITION_POLICY_VERSION = "MLO_TRANSITION_RECIPE_GATE_V1"
MLO_LANDING_CERTIFICATE_VERSION = "MLO_LANDING_CERTIFICATE_V1"
MLO_LANDING_CERTIFICATE_METADATA_KEY = "spd_mlo_landing_certificates"
MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA = "CONVENTIONAL_THROUGH_VIA"
MLO_LANDING_CLASS_SHORT_SPAN_VIA = "SHORT_SPAN_VIA"
MLO_TRANSITION_RECIPE_REQUIRED_CODE = "MLO_TRANSITION_RECIPE_REQUIRED"
MLO_TRANSITION_RECIPE_REQUIRED_MESSAGE = (
    "source landing/path has a short-span, lateral, or qualified microvia "
    "transition but "
    "no validated translated rebuild recipe; reimport the raw SPD after recipe "
    "engineering before selecting a non-TOP destination"
)
REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE = (
    "REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE"
)
REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_MESSAGE = (
    "scenario landing lacks explicit source-proven via path evidence; a "
    "board-level transition policy cannot certify a pathless landing as a "
    "continuous vertical via, so reimport the raw SPD before selecting a "
    "non-TOP destination"
)


class RoutingNetRole(StrEnum):
    SIGNAL = "SIGNAL"
    POWER = "POWER"
    GROUND = "GROUND"
    UNKNOWN = "UNKNOWN"


class RoutingObjectProvenance(StrEnum):
    PHYSICAL_ROUTING = "PHYSICAL_ROUTING"
    NON_COPPER_TOPOLOGY_OR_MESH = "NON_COPPER_TOPOLOGY_OR_MESH"
    UNRESOLVED = "UNRESOLVED"


class TraceWidthSource(StrEnum):
    INLINE = "INLINE"
    CONTINUATION = "CONTINUATION"
    EXACT_COPPER = "EXACT_COPPER"
    VERIFIED_LAYER_DEFAULT = "VERIFIED_LAYER_DEFAULT"
    UNRESOLVED = "UNRESOLVED"


class RoutingCandidateState(StrEnum):
    SAFE = "SAFE"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class RoutingProtectionScope(StrEnum):
    SIGNAL_NET_ONLY = "SIGNAL_NET_ONLY"


@dataclass(frozen=True, slots=True)
class MloTransitionPolicy:
    """Persisted structural gate for source MLO via transitions.

    ``translated_recipe_validated`` remains false until a future importer can
    persist a source-proven, translated via/trace rebuild recipe.  Keeping the
    state explicit lets Distribution reject unsafe non-TOP retargets while
    preserving conventional vertical-via behavior and legacy bundles.
    """

    policy_version: str = MLO_TRANSITION_POLICY_VERSION
    transition_required: bool = False
    translated_recipe_validated: bool = False
    evidence_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.policy_version != MLO_TRANSITION_POLICY_VERSION:
            raise ValueError(
                f"unsupported MLO transition policy {self.policy_version!r}"
            )
        if type(self.transition_required) is not bool or type(
            self.translated_recipe_validated
        ) is not bool:
            raise ValueError("MLO transition policy flags must be JSON booleans")
        if self.translated_recipe_validated:
            raise ValueError(
                "MLO transition policy V1 has no translated recipe schema"
            )
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence_codes):
            raise ValueError("MLO transition evidence codes must be nonblank strings")
        if len({item.casefold() for item in self.evidence_codes}) != len(
            self.evidence_codes
        ):
            raise ValueError("MLO transition evidence codes must be unique")

    def payload(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "transition_required": self.transition_required,
            "translated_recipe_validated": self.translated_recipe_validated,
            "evidence_codes": list(self.evidence_codes),
        }


@dataclass(frozen=True, slots=True)
class MloLandingCertificate:
    classification: str
    padstack: str
    span_layers: tuple[str, ...]
    drill_diameter_um: float | None = None
    padstack_material: str | None = None


def mlo_landing_certificate_claims_sha256(
    rows: Mapping[str, Mapping[str, object]],
) -> str:
    """Hash canonical per-landing claims, including physical span metadata."""

    claims = []
    for via_id, row in rows.items():
        claims.append(
            [
                str(via_id).casefold(),
                str(row.get("classification", "")),
                str(row.get("padstack", "")),
                [str(item) for item in row.get("span_layers", ())],
                (
                    None
                    if row.get("drill_diameter_um") is None
                    else float(row["drill_diameter_um"])
                ),
                (
                    None
                    if row.get("padstack_material") is None
                    else str(row["padstack_material"]).strip()
                ),
            ]
        )
    return sha256(
        (json.dumps(sorted(claims), separators=(",", ":")) + "\n").encode()
    ).hexdigest()


def parse_mlo_landing_certificates(
    value: object,
    *,
    expected_source_sha256: str | None = None,
) -> Mapping[str, MloLandingCertificate]:
    """Decode source-bound per-landing conventional-via certificates.

    This certificate is intentionally narrower than the board MLO summary:
    only importer-produced ``CONVENTIONAL_THROUGH_VIA`` rows grant permission
    for a pathless landing.  Unknown rows, versions, classes, and source
    bindings fail closed.  The returned keys are case-folded Via IDs.
    """

    if not isinstance(value, Mapping):
        raise ValueError("MLO landing certificates metadata must be an object")
    version = value.get("certificate_version")
    if version != MLO_LANDING_CERTIFICATE_VERSION:
        raise ValueError(f"unsupported MLO landing certificate {version!r}")
    raw_source = value.get("source_sha256")
    if not isinstance(raw_source, str):
        raise ValueError("MLO landing certificate source SHA-256 is required")
    _validate_sha(raw_source, "MLO landing certificate source SHA-256")
    if (
        expected_source_sha256 is not None
        and raw_source.casefold() != expected_source_sha256.casefold()
    ):
        raise ValueError("MLO landing certificates belong to a different source SPD")
    rows = value.get("by_via_id")
    if not isinstance(rows, Mapping):
        raise ValueError("MLO landing certificate rows must be an object")
    result: dict[str, MloLandingCertificate] = {}
    raw_claim_rows: dict[str, dict[str, object]] = {}
    normalized_ids: list[str] = []
    for raw_key, raw_row in rows.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("MLO landing certificate Via IDs must be strings")
        if not isinstance(raw_row, Mapping):
            raise ValueError("MLO landing certificate row must be an object")
        unknown_fields = set(raw_row) - {
            "classification",
            "padstack",
            "span_layers",
            "drill_diameter_um",
            "padstack_material",
        }
        if unknown_fields:
            raise ValueError("unknown MLO landing certificate row fields")
        normalized_ids.append(raw_key.casefold())
        classification = raw_row.get("classification")
        if not isinstance(classification, str) or classification not in {
            MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA,
            MLO_LANDING_CLASS_SHORT_SPAN_VIA,
            "UNRESOLVED",
        }:
            raise ValueError("unknown MLO landing certificate classification")
        padstack = raw_row.get("padstack")
        if not isinstance(padstack, str) or not padstack.strip():
            raise ValueError("MLO landing certificate padstack is required")
        if "padstack_material" not in raw_row:
            raise ValueError("MLO landing certificate padstack material is required")
        raw_material = raw_row.get("padstack_material")
        material = (
            raw_material.strip()
            if isinstance(raw_material, str) and raw_material.strip()
            else None
        )
        raw_layers = raw_row.get("span_layers")
        if not isinstance(raw_layers, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in raw_layers
        ):
            raise ValueError("MLO landing certificate span_layers must be strings")
        layers = tuple(str(item) for item in raw_layers)
        if len({item.casefold() for item in layers}) != len(layers):
            raise ValueError("MLO landing certificate span_layers must be unique")
        drill = raw_row.get("drill_diameter_um")
        if drill is not None:
            try:
                drill = float(drill)
            except (TypeError, ValueError):
                raise ValueError("MLO landing certificate drill must be numeric")
            if not isfinite(drill) or drill <= 0:
                raise ValueError("MLO landing certificate drill must be positive")
        if classification in {
            MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA,
            MLO_LANDING_CLASS_SHORT_SPAN_VIA,
        }:
            if drill is None:
                raise ValueError("classified MLO landing certificate drill is required")
            if len(layers) < 2:
                raise ValueError(
                    "classified MLO landing certificate must span layers"
                )
            if material is None or material.casefold() != "copper":
                raise ValueError(
                    "classified MLO landing certificate material must be COPPER"
                )
        result[raw_key.casefold()] = MloLandingCertificate(
            classification=classification,
            padstack=padstack,
            span_layers=layers,
            drill_diameter_um=drill,
            padstack_material=material,
        )
        raw_claim_rows[raw_key.casefold()] = {
            "classification": classification,
            "padstack": padstack,
            "span_layers": list(layers),
            "drill_diameter_um": drill,
            "padstack_material": material,
        }
    count = value.get("landing_count")
    if (
        type(count) is not int
        or count < 0
        or count != len(normalized_ids)
        or count != len(set(normalized_ids))
    ):
        raise ValueError("MLO landing certificate count is incomplete")
    digest = value.get("landing_ids_sha256")
    if not isinstance(digest, str):
        raise ValueError("MLO landing certificate ID digest is required")
    _validate_sha(digest, "MLO landing certificate ID digest")
    expected_digest = sha256(
        (json.dumps(sorted(set(normalized_ids)), separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    if digest.casefold() != expected_digest:
        raise ValueError("MLO landing certificate ID digest mismatch")
    claims_digest = value.get("claims_sha256")
    if not isinstance(claims_digest, str):
        raise ValueError("MLO landing certificate claims digest is required")
    _validate_sha(claims_digest, "MLO landing certificate claims digest")
    if claims_digest.casefold() != mlo_landing_certificate_claims_sha256(
        raw_claim_rows
    ):
        raise ValueError("MLO landing certificate claims digest mismatch")
    conventional_count = value.get("conventional_count")
    if type(conventional_count) is not int or conventional_count != sum(
        item.classification == MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA
        for item in result.values()
    ):
        raise ValueError("MLO landing certificate conventional_count mismatch")
    short_span_count = value.get("short_span_count")
    if type(short_span_count) is not int or short_span_count != sum(
        item.classification == MLO_LANDING_CLASS_SHORT_SPAN_VIA
        for item in result.values()
    ):
        raise ValueError("MLO landing certificate short_span_count mismatch")
    return result


def parse_mlo_transition_policy(
    value: object,
    *,
    expected_source_sha256: str | None = None,
) -> MloTransitionPolicy:
    """Strictly decode persisted MLO policy metadata.

    This is security-sensitive eligibility input.  Python truthiness is never
    accepted for JSON booleans, unknown policy versions are not interpreted,
    and an optional source binding must match the active scenario exactly.
    V1 cannot claim a validated translated recipe because it defines no recipe
    attachment schema.
    """

    if not isinstance(value, Mapping):
        raise ValueError("MLO transition policy metadata must be an object")
    version = value.get("policy_version")
    if not isinstance(version, str) or version != MLO_TRANSITION_POLICY_VERSION:
        raise ValueError(f"unsupported MLO transition policy {version!r}")
    transition_required = value.get("transition_required")
    recipe_validated = value.get("translated_recipe_validated")
    if type(transition_required) is not bool or type(recipe_validated) is not bool:
        raise ValueError("MLO transition policy flags must be JSON booleans")
    evidence_codes = value.get("evidence_codes", ())
    if not isinstance(evidence_codes, (list, tuple)):
        raise ValueError("MLO transition evidence codes must be an array")
    if any(not isinstance(item, str) for item in evidence_codes):
        raise ValueError("MLO transition evidence codes must be strings")
    raw_source = value.get("source_sha256")
    if raw_source is not None:
        if not isinstance(raw_source, str):
            raise ValueError("MLO transition source SHA-256 must be a string")
        _validate_sha(raw_source, "MLO transition source SHA-256")
        if (
            expected_source_sha256 is not None
            and raw_source.casefold() != expected_source_sha256.casefold()
        ):
            raise ValueError("MLO transition policy belongs to a different source SPD")
    return MloTransitionPolicy(
        policy_version=version,
        transition_required=transition_required,
        translated_recipe_validated=recipe_validated,
        evidence_codes=tuple(evidence_codes),
    )


def detect_mlo_transition_policy(
    landings: Iterable[object],
    *,
    stackup_layers: Sequence[object],
    evidence_by_via: Mapping[str, Iterable[object]] | None = None,
    structural_evidence_by_via: Mapping[str, Iterable[object]] | None = None,
) -> MloTransitionPolicy:
    """Classify source evidence that cannot justify an immutable vertical retarget.

    The detector uses only retained source geometry.  A lateral trace hop or a
    via endpoint that moves from its previous node is evidence of a staggered
    path.  A segment is also treated as MLO when the existing qualified
    source-COPPER microvia classifier proves the small adjacent-layer profile.
    No recipe is synthesized; such evidence simply requires the future recipe
    attachment and therefore fails closed for non-TOP Distribution targets.
    This is a board-level *positive* detector only: ``transition_required=false``
    means no retained MLO evidence was observed, not that every landing was
    proven to be a conventional continuous vertical via.  Distribution must
    still require explicit per-landing path evidence before a non-TOP retarget.
    """

    codes: set[str] = set()
    evidence_lookup = {
        str(key).casefold(): tuple(values)
        for key, values in (evidence_by_via or {}).items()
    }
    structural_lookup = {
        str(key).casefold(): tuple(values)
        for key, values in (structural_evidence_by_via or {}).items()
    }
    for landing in landings:
        via_id = str(getattr(landing, "via_id", "")).casefold()
        # Union full pad evidence and structural-only evidence. A landing may
        # have a valid pad on one target layer but an unsupported pad on another;
        # the latter must still contribute MLO diagnostics.
        path_evidence = (
            tuple(getattr(landing, "path_evidence", ()) or ())
            + (evidence_lookup.get(via_id, ()) if via_id else ())
            + tuple(getattr(landing, "structural_evidence", ()) or ())
            + (structural_lookup.get(via_id, ()) if via_id else ())
        )
        unique_evidence: list[object] = []
        seen_evidence: set[tuple[object, ...]] = set()
        for evidence in path_evidence:
            key = (
                str(getattr(evidence, "target_layer", "")).casefold(),
                str(getattr(evidence, "target_node_id", "")).casefold(),
                int(getattr(evidence, "trace_hops", 0) or 0),
                bool(getattr(evidence, "trace_alternate_exit", False)),
                tuple(
                    (
                        str(getattr(segment, "via_id", "")).casefold(),
                        str(getattr(segment, "start_layer", "")).casefold(),
                        str(getattr(segment, "end_layer", "")).casefold(),
                        float(getattr(segment, "drill_diameter_um", 0.0) or 0.0),
                        float(getattr(segment, "end_x_um", 0.0) or 0.0),
                        float(getattr(segment, "end_y_um", 0.0) or 0.0),
                    )
                    for segment in tuple(getattr(evidence, "segments", ()) or ())
                ),
            )
            if key not in seen_evidence:
                seen_evidence.add(key)
                unique_evidence.append(evidence)
        path_evidence = tuple(unique_evidence)
        for evidence in path_evidence:
            if int(getattr(evidence, "trace_hops", 0) or 0) > 0 or bool(
                getattr(evidence, "trace_alternate_exit", False)
            ):
                codes.add("LATERAL_RECOVERED_PATH")
            previous_x = float(getattr(landing, "x_um", 0.0))
            previous_y = float(getattr(landing, "y_um", 0.0))
            for segment in tuple(getattr(evidence, "segments", ()) or ()):
                end_x = float(getattr(segment, "end_x_um"))
                end_y = float(getattr(segment, "end_y_um"))
                if abs(end_x - previous_x) > ROUTING_GEOMETRY_EPS_UM or abs(
                    end_y - previous_y
                ) > ROUTING_GEOMETRY_EPS_UM:
                    codes.add("STAGGERED_VIA_ENDPOINT")
                try:
                    classification = classify_via_conductor(
                        drill_diameter_um=getattr(segment, "drill_diameter_um"),
                        padstack_material=getattr(segment, "padstack_material", None),
                        start_layer=getattr(segment, "start_layer", None),
                        end_layer=getattr(segment, "end_layer", None),
                        stackup_layers=stackup_layers,
                    )
                except (TypeError, ValueError):
                    classification = None
                if (
                    classification is not None
                    and classification.conductor_model == SOLID_COPPER_FILLED_MICROVIA
                ):
                    codes.add("QUALIFIED_COPPER_MICROVIA")
                previous_x, previous_y = end_x, end_y
    return MloTransitionPolicy(
        transition_required=bool(codes),
        evidence_codes=tuple(sorted(codes)),
    )


class RoutingClearanceMode(StrEnum):
    FIXED_UM = "FIXED_UM"
    TRACE_WIDTH_MULTIPLIER = "TRACE_WIDTH_MULTIPLIER"


@dataclass(frozen=True, slots=True)
class SignalTraceAvoidancePolicy:
    """One reproducible Distribution request policy.

    The interactive research UI uses :meth:`fixed` while the separate
    :meth:`research_width_multiplier` oracle is never a UI default.  The
    policy version deliberately says RESEARCH until physical-routing
    provenance and a planned rebuild-via recipe are independently certified.
    """

    enabled: bool = False
    scope: RoutingProtectionScope = RoutingProtectionScope.SIGNAL_NET_ONLY
    clearance_mode: RoutingClearanceMode = RoutingClearanceMode.FIXED_UM
    clearance_um: float | None = None
    trace_width_multiplier: float | None = None
    policy_version: str = ROUTING_POLICY_VERSION

    def __post_init__(self) -> None:
        if not self.enabled:
            if self.clearance_um is not None or self.trace_width_multiplier is not None:
                raise ValueError("disabled routing protection cannot carry clearance")
            return
        if self.clearance_mode == RoutingClearanceMode.FIXED_UM:
            if (
                self.clearance_um is None
                or not isfinite(self.clearance_um)
                or self.clearance_um < 0
            ):
                raise ValueError("trace-to-via clearance must be finite and >= 0 um")
            if self.trace_width_multiplier is not None:
                raise ValueError("fixed clearance cannot carry a width multiplier")
        elif self.clearance_mode == RoutingClearanceMode.TRACE_WIDTH_MULTIPLIER:
            if (
                self.trace_width_multiplier is None
                or not isfinite(self.trace_width_multiplier)
                or self.trace_width_multiplier < 0
            ):
                raise ValueError("trace-width multiplier must be finite and >= 0")
            if self.clearance_um is not None:
                raise ValueError("width-multiplier clearance cannot carry fixed um")
        else:  # pragma: no cover - enum construction normally prevents this
            raise ValueError(f"unsupported clearance mode {self.clearance_mode!r}")

    @classmethod
    def disabled(cls) -> "SignalTraceAvoidancePolicy":
        return cls()

    @classmethod
    def fixed(cls, clearance_um: float) -> "SignalTraceAvoidancePolicy":
        return cls(enabled=True, clearance_um=float(clearance_um))

    @classmethod
    def research_width_multiplier(
        cls, multiplier: float = 2.0
    ) -> "SignalTraceAvoidancePolicy":
        return cls(
            enabled=True,
            clearance_mode=RoutingClearanceMode.TRACE_WIDTH_MULTIPLIER,
            trace_width_multiplier=float(multiplier),
        )

    def payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "scope": self.scope.value,
            "clearance_mode": self.clearance_mode.value,
            "clearance_um": self.clearance_um,
            "trace_width_multiplier": self.trace_width_multiplier,
            "policy_version": self.policy_version,
        }

    @property
    def fingerprint(self) -> str:
        return sha256(_canonical_json(self.payload())).hexdigest()


@dataclass(frozen=True, slots=True)
class RoutingTraceSegment:
    trace_id: str
    net: str
    layer: str
    x1_um: float
    y1_um: float
    x2_um: float
    y2_um: float
    width_um: float
    width_source: TraceWidthSource
    net_role: RoutingNetRole
    provenance: RoutingObjectProvenance
    source_offset: int = 0
    thermal: bool = False

    def __post_init__(self) -> None:
        values = (
            self.x1_um,
            self.y1_um,
            self.x2_um,
            self.y2_um,
            self.width_um,
        )
        if any(not isfinite(value) for value in values) or self.width_um <= 0:
            raise ValueError("routing segment coordinates and positive width must be finite")
        if any(abs(value) > _MAX_ROUTING_ABS_COORD_UM for value in values[:-1]):
            raise ValueError("routing segment coordinate exceeds the format safety bound")
        if self.width_um > _MAX_ROUTING_WIDTH_UM:
            raise ValueError("routing segment width exceeds the format safety bound")
        if not self.trace_id or not self.net or not self.layer:
            raise ValueError("routing segment identity fields must be nonblank")
        if self.source_offset < 0:
            raise ValueError("routing segment source offset cannot be negative")


@dataclass(frozen=True, slots=True)
class RoutingLayerCompleteness:
    layer: str
    parse_complete: bool = True
    unresolved_width_count: int = 0
    unresolved_role_count: int = 0
    unresolved_provenance_count: int = 0
    unresolved_endpoint_count: int = 0
    unresolved_codes: tuple[str, ...] = ()
    unresolved_examples: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.parse_complete and not any(
            (
                self.unresolved_width_count,
                self.unresolved_role_count,
                self.unresolved_provenance_count,
                self.unresolved_endpoint_count,
                self.unresolved_codes,
            )
        )


@dataclass(frozen=True, slots=True)
class PlannedViaProfile:
    profile_id: str
    radius_um_by_layer: tuple[tuple[str, float], ...]
    fallback_barrel_radius_um: float | None = None
    complete: bool = True
    provenance: str = "SOURCE_PADSTACK_LAYER_ENVELOPE_V1"
    unresolved_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("planned via profile ID must be nonblank")
        seen: set[str] = set()
        for layer, radius in self.radius_um_by_layer:
            key = layer.casefold()
            if not layer or key in seen:
                raise ValueError("planned via profile layers must be nonblank and unique")
            seen.add(key)
            if not isfinite(radius) or radius <= 0:
                raise ValueError("planned via radius must be finite and positive")
        if self.fallback_barrel_radius_um is not None and (
            not isfinite(self.fallback_barrel_radius_um)
            or self.fallback_barrel_radius_um <= 0
        ):
            raise ValueError("fallback barrel radius must be finite and positive")
        if not self.complete and not self.unresolved_reason:
            raise ValueError("incomplete planned via profile requires a reason")

    def radius_for_layer(self, layer: str) -> float | None:
        key = layer.casefold()
        for name, radius in self.radius_um_by_layer:
            if name.casefold() == key:
                return radius
        return self.fallback_barrel_radius_um


@dataclass(frozen=True, slots=True)
class RoutingObstacleAsset:
    source_sha256: str
    stackup_fingerprint: str
    conductor_layers: tuple[str, ...]
    segments: tuple[RoutingTraceSegment, ...]
    layer_completeness: tuple[RoutingLayerCompleteness, ...]
    via_profiles: tuple[PlannedViaProfile, ...]
    schema_version: str = ROUTING_ASSET_SCHEMA
    scope: RoutingProtectionScope = RoutingProtectionScope.SIGNAL_NET_ONLY
    compiler_policy: str = "WIDTHED_SIGNAL_TRACE_PROXY_V1"
    production_ready: bool = False
    scope_limitation: str = (
        "Protects width-resolved SIGNAL-role Trace objects only; routed PWR/GND, "
        "signal vias, pins, pads and fanout pads are outside this initial scope."
    )
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != ROUTING_ASSET_SCHEMA:
            raise ValueError(f"unsupported routing asset schema {self.schema_version!r}")
        _validate_sha(self.source_sha256, "source SPD SHA-256")
        _validate_sha(self.stackup_fingerprint, "stack-up fingerprint")
        keys = [item.casefold() for item in self.conductor_layers]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("conductor layer order must be nonempty and unique")
        layer_keys = set(keys)
        if any(item.layer.casefold() not in layer_keys for item in self.segments):
            raise ValueError("routing segment references a non-conductor layer")
        completeness_keys = [item.layer.casefold() for item in self.layer_completeness]
        if len(completeness_keys) != len(set(completeness_keys)):
            raise ValueError("routing completeness layers must be unique")
        profile_keys = [item.profile_id.casefold() for item in self.via_profiles]
        if len(profile_keys) != len(set(profile_keys)):
            raise ValueError("planned via profile IDs must be unique")
        if self.content_sha256 is not None:
            _validate_sha(self.content_sha256, "routing asset content SHA-256")

    def completeness_for_layer(self, layer: str) -> RoutingLayerCompleteness | None:
        key = layer.casefold()
        return next(
            (item for item in self.layer_completeness if item.layer.casefold() == key),
            None,
        )

    def profile(self, profile_id: str) -> PlannedViaProfile | None:
        key = profile_id.casefold()
        return next(
            (item for item in self.via_profiles if item.profile_id.casefold() == key),
            None,
        )


@dataclass(frozen=True, slots=True)
class RoutingCollisionEvidence:
    code: str
    layer: str | None
    message: str
    trace_id: str | None = None
    trace_net: str | None = None
    trace_width_um: float | None = None
    width_source: str | None = None
    via_radius_um: float | None = None
    center_distance_um: float | None = None
    copper_edge_gap_um: float | None = None
    required_clearance_um: float | None = None
    clearance_source: str | None = None


@dataclass(frozen=True, slots=True)
class RoutingCandidateProof:
    state: RoutingCandidateState
    destination_layer: str
    profile_id: str | None
    evidence: tuple[RoutingCollisionEvidence, ...] = ()


def stackup_fingerprint(layers: Iterable[object]) -> str:
    payload: list[dict[str, object]] = []
    for index, layer in enumerate(layers):
        if isinstance(layer, str):
            payload.append({"index": index, "name": layer, "is_conductor": True})
        else:
            payload.append(
                {
                    "index": index,
                    "name": str(getattr(layer, "name")),
                    "is_conductor": bool(getattr(layer, "is_conductor", True)),
                    "thickness_um": getattr(layer, "thickness_um", None),
                }
            )
    return sha256(_canonical_json(payload)).hexdigest()


def encode_routing_obstacle_asset(asset: RoutingObstacleAsset) -> bytes:
    """Return deterministic compressed bytes suitable for a scenario attachment."""

    payload = {
        "schema_version": asset.schema_version,
        "source_sha256": asset.source_sha256,
        "stackup_fingerprint": asset.stackup_fingerprint,
        "conductor_layers": list(asset.conductor_layers),
        "scope": asset.scope.value,
        "compiler_policy": asset.compiler_policy,
        "production_ready": asset.production_ready,
        "scope_limitation": asset.scope_limitation,
        "segments": [_json_dataclass(item) for item in asset.segments],
        "layer_completeness": [
            _json_dataclass(item) for item in asset.layer_completeness
        ],
        "via_profiles": [_json_dataclass(item) for item in asset.via_profiles],
    }
    raw = _canonical_json(payload)
    raw_digest = sha256(raw).hexdigest().encode("ascii")
    return ROUTING_ASSET_MAGIC + raw_digest + b"\n" + zlib.compress(raw, level=9)


def decode_routing_obstacle_asset(
    payload: bytes,
    *,
    expected_source_sha256: str | None = None,
    expected_stackup_fingerprint: str | None = None,
) -> RoutingObstacleAsset:
    """Decode and fully validate a routing attachment with bounded expansion."""

    if not payload.startswith(ROUTING_ASSET_MAGIC):
        raise ValueError("routing attachment has an invalid magic/version header")
    digest_start = len(ROUTING_ASSET_MAGIC)
    digest_end = payload.find(b"\n", digest_start)
    if digest_end < 0:
        raise ValueError("routing attachment content digest is missing")
    try:
        declared_digest = payload[digest_start:digest_end].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("routing attachment content digest is not ASCII") from exc
    _validate_sha(declared_digest, "routing attachment content SHA-256")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(
            payload[digest_end + 1 :], MAX_ROUTING_ASSET_EXPANDED_BYTES + 1
        )
    except zlib.error as exc:
        raise ValueError("routing attachment compression stream is invalid") from exc
    if len(raw) > MAX_ROUTING_ASSET_EXPANDED_BYTES or decoder.unconsumed_tail:
        raise ValueError("routing attachment exceeds the expanded-size limit")
    if not decoder.eof or decoder.unused_data:
        raise ValueError("routing attachment has a truncated or trailing stream")
    observed_digest = sha256(raw).hexdigest()
    if observed_digest != declared_digest:
        raise ValueError("routing attachment content SHA-256 mismatch")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("routing attachment JSON is invalid") from exc
    if not isinstance(document, dict):
        raise ValueError("routing attachment root must be an object")
    try:
        asset = RoutingObstacleAsset(
            schema_version=str(document["schema_version"]),
            source_sha256=str(document["source_sha256"]),
            stackup_fingerprint=str(document["stackup_fingerprint"]),
            conductor_layers=tuple(str(item) for item in document["conductor_layers"]),
            scope=RoutingProtectionScope(str(document["scope"])),
            compiler_policy=str(document["compiler_policy"]),
            production_ready=_json_bool(
                document["production_ready"], "routing production_ready"
            ),
            scope_limitation=str(document["scope_limitation"]),
            segments=tuple(_segment_from_json(item) for item in document["segments"]),
            layer_completeness=tuple(
                _completeness_from_json(item)
                for item in document["layer_completeness"]
            ),
            via_profiles=tuple(
                _profile_from_json(item) for item in document["via_profiles"]
            ),
            content_sha256=observed_digest,
        )
    # ``LookupError`` (not just ``KeyError``) keeps a short or malformed nested
    # row from escaping this function's documented ValueError contract, which
    # the Distribution worker relies on to fail closed with ROUTING_ASSET_STALE.
    except (LookupError, TypeError, ValueError) as exc:
        raise ValueError(f"routing attachment schema is invalid: {exc}") from exc
    if (
        expected_source_sha256 is not None
        and asset.source_sha256.casefold() != expected_source_sha256.casefold()
    ):
        raise ValueError("routing attachment belongs to a different source SPD")
    if (
        expected_stackup_fingerprint is not None
        and asset.stackup_fingerprint.casefold()
        != expected_stackup_fingerprint.casefold()
    ):
        raise ValueError("routing attachment belongs to a different stack-up")
    return asset


def routing_attachment_name(payload: bytes) -> str:
    return f"routing/obstacles-{sha256(payload).hexdigest()[:16]}.spdrouting.zlib"


def evaluate_routing_candidate(
    asset: RoutingObstacleAsset,
    *,
    x_um: float,
    y_um: float,
    destination_layer: str,
    mount_side: str,
    profile_id: str | None,
    policy: SignalTraceAvoidancePolicy,
) -> RoutingCandidateProof:
    """Evaluate one immutable vertical landing column before MILP creation."""

    if not policy.enabled:
        return RoutingCandidateProof(
            RoutingCandidateState.SAFE, destination_layer, profile_id
        )
    if not isfinite(x_um) or not isfinite(y_um):
        return _unknown_proof(
            destination_layer,
            profile_id,
            "STACKUP_SPAN_UNRESOLVED",
            None,
            "landing coordinate is not finite",
        )
    if asset.scope != policy.scope:
        return _unknown_proof(
            destination_layer,
            profile_id,
            "ROUTING_ASSET_STALE",
            None,
            "routing asset scope does not match the requested protection scope",
        )
    layer_keys = [item.casefold() for item in asset.conductor_layers]
    destination_key = destination_layer.casefold()
    if layer_keys.count(destination_key) != 1:
        return _unknown_proof(
            destination_layer,
            profile_id,
            "STACKUP_SPAN_UNRESOLVED",
            destination_layer,
            "destination layer is absent or ambiguous in the conductor stack",
        )
    destination_index = layer_keys.index(destination_key)
    side = mount_side.strip().upper()
    if side == "TOP":
        span = asset.conductor_layers[: destination_index + 1]
    elif side == "BOTTOM":
        span = asset.conductor_layers[destination_index:]
    else:
        return _unknown_proof(
            destination_layer,
            profile_id,
            "STACKUP_SPAN_UNRESOLVED",
            None,
            f"unsupported mount side {mount_side!r}",
        )
    profile = asset.profile(profile_id or "") if profile_id else None
    if profile is None:
        return _unknown_proof(
            destination_layer,
            profile_id,
            "VIA_PROFILE_UNRESOLVED",
            None,
            "no planned via profile is bound to the destination",
        )

    unknown: list[RoutingCollisionEvidence] = []
    index = _asset_index(asset)
    for layer in span:
        completeness = asset.completeness_for_layer(layer)
        if completeness is None:
            unknown.append(
                RoutingCollisionEvidence(
                    code="ROUTING_ASSET_STALE",
                    layer=layer,
                    message="routing asset has no completeness row for this conductor layer",
                )
            )
        elif not completeness.complete:
            codes = completeness.unresolved_codes or (
                "TRACE_WIDTH_UNRESOLVED",
            )
            unknown.append(
                RoutingCollisionEvidence(
                    code=codes[0],
                    layer=layer,
                    message=(
                        "routing evidence is incomplete on this layer "
                        f"(width={completeness.unresolved_width_count}, "
                        f"role={completeness.unresolved_role_count}, "
                        f"provenance={completeness.unresolved_provenance_count}, "
                        f"endpoint={completeness.unresolved_endpoint_count})"
                    ),
                )
            )
        radius_um = profile.radius_for_layer(layer)
        if radius_um is None or not profile.complete:
            unknown.append(
                RoutingCollisionEvidence(
                    code="VIA_PROFILE_UNRESOLVED",
                    layer=layer,
                    message=(profile.unresolved_reason or "planned via radius is unresolved"),
                )
            )
            continue
        layer_segments = index.segments_by_layer.get(layer.casefold(), ())
        if not layer_segments:
            continue
        max_width = index.max_width_by_layer.get(layer.casefold(), 0.0)
        if policy.clearance_mode == RoutingClearanceMode.FIXED_UM:
            max_required = radius_um + max_width / 2.0 + float(policy.clearance_um)
            clearance_source = "USER_FIXED_UM"
        else:
            max_required = radius_um + (
                0.5 + float(policy.trace_width_multiplier)
            ) * max_width
            clearance_source = "RESEARCH_TRACE_WIDTH_MULTIPLIER"
        for segment in index.query(layer, x_um, y_um, max_required):
            if (
                segment.net_role != RoutingNetRole.SIGNAL
                or segment.provenance != RoutingObjectProvenance.PHYSICAL_ROUTING
            ):
                unknown.append(
                    RoutingCollisionEvidence(
                        code=(
                            "TRACE_NET_ROLE_UNRESOLVED"
                            if segment.net_role != RoutingNetRole.SIGNAL
                            else "TRACE_PHYSICAL_PROVENANCE_UNRESOLVED"
                        ),
                        layer=layer,
                        trace_id=segment.trace_id,
                        trace_net=segment.net,
                        message="nearby routing object classification is unresolved",
                    )
                )
                continue
            clearance_um = (
                float(policy.clearance_um)
                if policy.clearance_mode == RoutingClearanceMode.FIXED_UM
                else float(policy.trace_width_multiplier) * segment.width_um
            )
            required = radius_um + segment.width_um / 2.0 + clearance_um
            distance = point_segment_distance(
                x_um,
                y_um,
                segment.x1_um,
                segment.y1_um,
                segment.x2_um,
                segment.y2_um,
            )
            if distance <= required + ROUTING_GEOMETRY_EPS_UM:
                return RoutingCandidateProof(
                    state=RoutingCandidateState.BLOCKED,
                    destination_layer=destination_layer,
                    profile_id=profile.profile_id,
                    evidence=(
                        RoutingCollisionEvidence(
                            code="IMMUTABLE_SIGNAL_CLEARANCE_BLOCKED",
                            layer=layer,
                            trace_id=segment.trace_id,
                            trace_net=segment.net,
                            trace_width_um=segment.width_um,
                            width_source=segment.width_source.value,
                            via_radius_um=radius_um,
                            center_distance_um=distance,
                            copper_edge_gap_um=(
                                distance - radius_um - segment.width_um / 2.0
                            ),
                            required_clearance_um=clearance_um,
                            clearance_source=clearance_source,
                            message=(
                                f"{segment.trace_id} on {layer} intersects the "
                                "planned via copper/clearance envelope"
                            ),
                        ),
                    ),
                )
    if unknown:
        return RoutingCandidateProof(
            RoutingCandidateState.UNKNOWN,
            destination_layer,
            profile.profile_id,
            tuple(_bounded_unique_evidence(unknown)),
        )
    return RoutingCandidateProof(
        RoutingCandidateState.SAFE, destination_layer, profile.profile_id
    )


def point_segment_distance(
    px: float,
    py: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> float:
    dx = x2 - x1
    dy = y2 - y1
    length_squared = dx * dx + dy * dy
    if length_squared <= 0.0:
        return hypot(px - x1, py - y1)
    position = ((px - x1) * dx + (py - y1) * dy) / length_squared
    position = min(1.0, max(0.0, position))
    return hypot(px - (x1 + position * dx), py - (y1 + position * dy))


@dataclass(slots=True)
class _RoutingAssetIndex:
    segments_by_layer: dict[str, tuple[RoutingTraceSegment, ...]]
    tiles_by_layer: dict[str, dict[tuple[int, int], tuple[int, ...]]]
    tile_bounds_by_layer: dict[str, tuple[int, int, int, int]]
    fallback_indices_by_layer: dict[str, tuple[int, ...]]
    max_width_by_layer: dict[str, float]

    def query(
        self, layer: str, x_um: float, y_um: float, radius_um: float
    ) -> tuple[RoutingTraceSegment, ...]:
        key = layer.casefold()
        segments = self.segments_by_layer.get(key, ())
        tiles = self.tiles_by_layer.get(key, {})
        if not segments:
            return ()
        if not isfinite(radius_um):
            return segments
        indices: set[int] = set(self.fallback_indices_by_layer.get(key, ()))
        if not tiles:
            return tuple(segments[index] for index in sorted(indices))
        low_x = floor((x_um - radius_um) / _INDEX_CELL_UM)
        high_x = floor((x_um + radius_um) / _INDEX_CELL_UM)
        low_y = floor((y_um - radius_um) / _INDEX_CELL_UM)
        high_y = floor((y_um + radius_um) / _INDEX_CELL_UM)
        bounds = self.tile_bounds_by_layer.get(key)
        if bounds is None:
            return tuple(segments[index] for index in sorted(indices))
        bounds_low_x, bounds_high_x, bounds_low_y, bounds_high_y = bounds
        low_x = max(low_x, bounds_low_x)
        high_x = min(high_x, bounds_high_x)
        low_y = max(low_y, bounds_low_y)
        high_y = min(high_y, bounds_high_y)
        if low_x > high_x or low_y > high_y:
            return tuple(segments[index] for index in sorted(indices))
        tile_span = (high_x - low_x + 1) * (high_y - low_y + 1)
        if tile_span >= len(tiles):
            # A very large user clearance must not iterate an astronomical
            # empty square.  Walk only populated, source-bounded tiles.
            for (tile_x, tile_y), tile_indices in tiles.items():
                if low_x <= tile_x <= high_x and low_y <= tile_y <= high_y:
                    indices.update(tile_indices)
        else:
            for tile_x in range(low_x, high_x + 1):
                for tile_y in range(low_y, high_y + 1):
                    indices.update(tiles.get((tile_x, tile_y), ()))
        return tuple(segments[index] for index in sorted(indices))


_INDEX_CACHE: "OrderedDict[str, _RoutingAssetIndex]" = OrderedDict()
_INDEX_LOCK = RLock()


def _asset_index(asset: RoutingObstacleAsset) -> _RoutingAssetIndex:
    cache_key = asset.content_sha256 or sha256(
        encode_routing_obstacle_asset(asset)
    ).hexdigest()
    with _INDEX_LOCK:
        existing = _INDEX_CACHE.get(cache_key)
        if existing is not None:
            _INDEX_CACHE.move_to_end(cache_key)
            return existing
    by_layer: dict[str, list[RoutingTraceSegment]] = defaultdict(list)
    for segment in asset.segments:
        by_layer[segment.layer.casefold()].append(segment)
    segments_by_layer: dict[str, tuple[RoutingTraceSegment, ...]] = {}
    tiles_by_layer: dict[str, dict[tuple[int, int], tuple[int, ...]]] = {}
    tile_bounds_by_layer: dict[str, tuple[int, int, int, int]] = {}
    fallback_indices_by_layer: dict[str, tuple[int, ...]] = {}
    max_width_by_layer: dict[str, float] = {}
    for layer, raw_segments in by_layer.items():
        ordered = tuple(
            sorted(
                raw_segments,
                key=lambda item: (
                    item.trace_id.casefold(),
                    item.source_offset,
                    item.x1_um,
                    item.y1_um,
                    item.x2_um,
                    item.y2_um,
                ),
            )
        )
        segments_by_layer[layer] = ordered
        max_width_by_layer[layer] = max(item.width_um for item in ordered)
        mutable_tiles: dict[tuple[int, int], list[int]] = defaultdict(list)
        fallback_indices: list[int] = []
        index_entry_count = 0
        for index, segment in enumerate(ordered):
            low_x = floor(min(segment.x1_um, segment.x2_um) / _INDEX_CELL_UM)
            high_x = floor(max(segment.x1_um, segment.x2_um) / _INDEX_CELL_UM)
            low_y = floor(min(segment.y1_um, segment.y2_um) / _INDEX_CELL_UM)
            high_y = floor(max(segment.y1_um, segment.y2_um) / _INDEX_CELL_UM)
            tile_count = (high_x - low_x + 1) * (high_y - low_y + 1)
            if (
                tile_count > _MAX_TILES_PER_SEGMENT
                or index_entry_count + tile_count
                > _MAX_TILE_INDEX_ENTRIES_PER_LAYER
            ):
                fallback_indices.append(index)
                continue
            for tile_x in range(low_x, high_x + 1):
                for tile_y in range(low_y, high_y + 1):
                    mutable_tiles[(tile_x, tile_y)].append(index)
            index_entry_count += tile_count
        tiles_by_layer[layer] = {
            tile: tuple(indices) for tile, indices in mutable_tiles.items()
        }
        if mutable_tiles:
            tile_xs = tuple(item[0] for item in mutable_tiles)
            tile_ys = tuple(item[1] for item in mutable_tiles)
            tile_bounds_by_layer[layer] = (
                min(tile_xs),
                max(tile_xs),
                min(tile_ys),
                max(tile_ys),
            )
        fallback_indices_by_layer[layer] = tuple(fallback_indices)
    result = _RoutingAssetIndex(
        segments_by_layer=segments_by_layer,
        tiles_by_layer=tiles_by_layer,
        tile_bounds_by_layer=tile_bounds_by_layer,
        fallback_indices_by_layer=fallback_indices_by_layer,
        max_width_by_layer=max_width_by_layer,
    )
    with _INDEX_LOCK:
        _INDEX_CACHE[cache_key] = result
        _INDEX_CACHE.move_to_end(cache_key)
        while len(_INDEX_CACHE) > _INDEX_CACHE_LIMIT:
            _INDEX_CACHE.popitem(last=False)
    return result


def _unknown_proof(
    destination_layer: str,
    profile_id: str | None,
    code: str,
    layer: str | None,
    message: str,
) -> RoutingCandidateProof:
    return RoutingCandidateProof(
        RoutingCandidateState.UNKNOWN,
        destination_layer,
        profile_id,
        (RoutingCollisionEvidence(code=code, layer=layer, message=message),),
    )


def _bounded_unique_evidence(
    values: Sequence[RoutingCollisionEvidence], limit: int = 16
) -> Iterable[RoutingCollisionEvidence]:
    seen: set[tuple[object, ...]] = set()
    for item in values:
        key = (item.code, item.layer, item.trace_id, item.message)
        if key in seen:
            continue
        seen.add(key)
        yield item
        if len(seen) >= limit:
            return


def _json_dataclass(value: object) -> dict[str, object]:
    result = asdict(value)
    for key, item in tuple(result.items()):
        if isinstance(item, StrEnum):
            result[key] = item.value
        elif isinstance(item, tuple):
            result[key] = list(item)
    return result


def _segment_from_json(value: object) -> RoutingTraceSegment:
    if not isinstance(value, Mapping):
        raise ValueError("routing segment row must be an object")
    return RoutingTraceSegment(
        trace_id=str(value["trace_id"]),
        net=str(value["net"]),
        layer=str(value["layer"]),
        x1_um=float(value["x1_um"]),
        y1_um=float(value["y1_um"]),
        x2_um=float(value["x2_um"]),
        y2_um=float(value["y2_um"]),
        width_um=float(value["width_um"]),
        width_source=TraceWidthSource(str(value["width_source"])),
        net_role=RoutingNetRole(str(value["net_role"])),
        provenance=RoutingObjectProvenance(str(value["provenance"])),
        source_offset=int(value.get("source_offset", 0)),
        thermal=_json_bool(value.get("thermal", False), "routing segment thermal"),
    )


def _completeness_from_json(value: object) -> RoutingLayerCompleteness:
    if not isinstance(value, Mapping):
        raise ValueError("routing completeness row must be an object")
    return RoutingLayerCompleteness(
        layer=str(value["layer"]),
        parse_complete=_json_bool(
            value.get("parse_complete", True), "routing layer parse_complete"
        ),
        unresolved_width_count=int(value.get("unresolved_width_count", 0)),
        unresolved_role_count=int(value.get("unresolved_role_count", 0)),
        unresolved_provenance_count=int(
            value.get("unresolved_provenance_count", 0)
        ),
        unresolved_endpoint_count=int(value.get("unresolved_endpoint_count", 0)),
        unresolved_codes=tuple(str(item) for item in value.get("unresolved_codes", ())),
        unresolved_examples=tuple(
            str(item) for item in value.get("unresolved_examples", ())
        ),
    )


def _profile_from_json(value: object) -> PlannedViaProfile:
    if not isinstance(value, Mapping):
        raise ValueError("planned via profile row must be an object")
    raw_radii = value.get("radius_um_by_layer", ())
    # ``str``/``bytes`` are Sequences; accepting them would index into
    # characters instead of failing closed on a malformed radius table.
    if not isinstance(raw_radii, Sequence) or isinstance(raw_radii, (str, bytes)):
        raise ValueError("planned via radius table must be an array")
    for item in raw_radii:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(
                "planned via radius row must be a [layer, radius_um] pair"
            )
    return PlannedViaProfile(
        profile_id=str(value["profile_id"]),
        radius_um_by_layer=tuple(
            (str(item[0]), float(item[1])) for item in raw_radii
        ),
        fallback_barrel_radius_um=(
            None
            if value.get("fallback_barrel_radius_um") is None
            else float(value["fallback_barrel_radius_um"])
        ),
        complete=_json_bool(
            value.get("complete", True), "planned via profile complete"
        ),
        provenance=str(value.get("provenance", "UNKNOWN")),
        unresolved_reason=(
            None
            if value.get("unresolved_reason") is None
            else str(value["unresolved_reason"])
        ),
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _json_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a JSON boolean")
    return value


def _validate_sha(value: str, label: str) -> None:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{label} must be a 64-character lowercase hexadecimal digest")


__all__ = [
    "MAX_ROUTING_ASSET_EXPANDED_BYTES",
    "MLO_LANDING_CERTIFICATE_METADATA_KEY",
    "MLO_LANDING_CERTIFICATE_VERSION",
    "MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA",
    "MLO_LANDING_CLASS_SHORT_SPAN_VIA",
    "MloLandingCertificate",
    "MLO_TRANSITION_POLICY_VERSION",
    "MLO_TRANSITION_RECIPE_REQUIRED_CODE",
    "MLO_TRANSITION_RECIPE_REQUIRED_MESSAGE",
    "REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_CODE",
    "REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE_MESSAGE",
    "MloTransitionPolicy",
    "PlannedViaProfile",
    "ROUTING_ASSET_SCHEMA",
    "ROUTING_GEOMETRY_EPS_UM",
    "ROUTING_POLICY_VERSION",
    "RoutingCandidateProof",
    "RoutingCandidateState",
    "RoutingClearanceMode",
    "RoutingCollisionEvidence",
    "RoutingLayerCompleteness",
    "RoutingNetRole",
    "RoutingObjectProvenance",
    "RoutingObstacleAsset",
    "RoutingProtectionScope",
    "RoutingTraceSegment",
    "SignalTraceAvoidancePolicy",
    "TraceWidthSource",
    "decode_routing_obstacle_asset",
    "detect_mlo_transition_policy",
    "encode_routing_obstacle_asset",
    "evaluate_routing_candidate",
    "point_segment_distance",
    "parse_mlo_transition_policy",
    "parse_mlo_landing_certificates",
    "mlo_landing_certificate_claims_sha256",
    "routing_attachment_name",
    "stackup_fingerprint",
]
