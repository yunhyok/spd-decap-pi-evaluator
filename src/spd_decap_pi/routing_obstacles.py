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
    except (KeyError, TypeError, ValueError) as exc:
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
    if not isinstance(raw_radii, Sequence):
        raise ValueError("planned via radius table must be an array")
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
    "encode_routing_obstacle_asset",
    "evaluate_routing_candidate",
    "point_segment_distance",
    "routing_attachment_name",
    "stackup_fingerprint",
]
