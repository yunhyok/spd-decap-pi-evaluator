"""Validated scenario contracts for SPD Decap PI Evaluator.

The scenario is intentionally separate from :mod:`spd_decap_pi._core` project persistence:
it records edits made to an already-routed SPD design while keeping the raw SPD
as an external, hash-identified source.  UI-only state is persisted for a useful
resume experience, but is excluded from the electrical design fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    ValidationInfo,
    field_validator,
    model_validator,
)

from spd_decap_pi._core.domain import (
    MixedReferenceGroundWitness,
    ProjectSpec,
)

from .version import __version__


SCENARIO_SCHEMA_VERSION = "0.1"
SCENARIO_APP_VERSION = __version__
SHARED_PAD_ANALYSIS_VERSION = "DIRECT_TOP_COPPER_PATH_VIA_CHAIN_V5"
_SUPPORTED_SHARED_PAD_ANALYSIS_VERSIONS = {
    "DIRECT_TOP_PAD_GRAPH_V2",
    "DIRECT_TOP_COPPER_PATH_V3",
    "DIRECT_TOP_COPPER_PATH_V4",
    SHARED_PAD_ANALYSIS_VERSION,
}
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


def _connection_analysis_fingerprint_payload(
    analysis: "SharedPadConnectionAnalysis",
) -> dict[str, Any]:
    """Serialize source connectivity without inventing legacy MLO fill evidence.

    ``padstack_material`` was added in v0.14.  Earlier scenario bundles did
    not serialize the field at all; after Pydantic supplies its ``None``
    default, including that new key would change their persisted electrical
    identities.  A real source material remains in the payload and therefore
    deliberately changes the fingerprint.
    """

    def without_unknown_material(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: without_unknown_material(item)
                for key, item in value.items()
                if (key != "padstack_material" or item is not None)
                and (key != "trace_hops" or item != 0)
                and (key != "trace_alternate_exit" or item is not False)
            }
        if isinstance(value, list):
            return [without_unknown_material(item) for item in value]
        return value

    return without_unknown_material(analysis.model_dump(mode="json"))


def _normalized_project_fingerprint_payload(value: Any) -> Any:
    """Preserve historical hashes when a new optional rail certificate is absent."""

    if isinstance(value, dict):
        return {
            key: _normalized_project_fingerprint_payload(item)
            for key, item in value.items()
            if key not in {"mixed_reference_certificate", "mixed_reference_ground_witness"}
            or item is not None
        }
    if isinstance(value, list):
        return [_normalized_project_fingerprint_payload(item) for item in value]
    return value


def _preserve_legacy_stackup_row_shape(
    serialized_project: dict[str, Any], source_project: Mapping[str, object]
) -> None:
    """Remove newly defaulted stack-up keys that an old payload omitted.

    Scenario schema 0.1 predates ``material`` and
    ``dielectric_properties``.  Pydantic must still materialize those defaults
    for runtime use, but persisted electrical identities must be calculated
    from the old row shape when the source payload did not contain the keys.
    """

    source_rows = source_project.get("stackup_layers")
    serialized_rows = serialized_project.get("stackup_layers")
    if not isinstance(source_rows, list) or not isinstance(serialized_rows, list):
        return
    if len(source_rows) != len(serialized_rows):
        return
    if not all(
        isinstance(source_row, Mapping) and isinstance(serialized_row, dict)
        for source_row, serialized_row in zip(source_rows, serialized_rows, strict=True)
    ):
        return
    if any(
        source_row.get("name") != serialized_row.get("name")
        for source_row, serialized_row in zip(source_rows, serialized_rows, strict=True)
    ):
        return
    for source_row, serialized_row in zip(source_rows, serialized_rows, strict=True):
        if "material" not in source_row and serialized_row.get("material") is None:
            serialized_row.pop("material", None)
        if (
            "dielectric_properties" not in source_row
            and serialized_row.get("dielectric_properties") == []
        ):
            serialized_row.pop("dielectric_properties", None)


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


class DecapPadState(StrEnum):
    """Physical usability of one routed decap pad cell."""

    NORMAL = "NORMAL"
    ISOLATION_GAP = "ISOLATION_GAP"


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


class DecapConnectionKind(StrEnum):
    """Source-derived electrical connection of one physical decap footprint."""

    DIRECT = "DIRECT"
    SHARED_ANCHOR = "SHARED_ANCHOR"
    SHARED_DUMMY = "SHARED_DUMMY"
    FLOATING_DUMMY = "FLOATING_DUMMY"
    UNRESOLVED = "UNRESOLVED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class SharedPadClusterState(StrEnum):
    """Whether a detected pad-short component has usable via connectivity."""

    ANCHORED = "ANCHORED"
    FLOATING = "FLOATING"
    UNRESOLVED = "UNRESOLVED"


class ScenarioViaLanding(ScenarioPoint):
    """Immutable source evidence for one physical via landing at a pad node."""

    model_config = ConfigDict(frozen=True)

    via_id: str = Field(min_length=1)
    net: str = Field(min_length=1)
    endpoint_node_id: str = Field(min_length=1)
    padstack: str = Field(min_length=1)
    rotation_degrees: float = 0.0
    path_evidence: tuple["ScenarioViaPathEvidence", ...] = ()

    @field_validator("rotation_degrees")
    @classmethod
    def finite_rotation(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("via rotation must be finite")
        return value

    @field_validator("path_evidence")
    @classmethod
    def unique_path_targets(
        cls, value: tuple["ScenarioViaPathEvidence", ...]
    ) -> tuple["ScenarioViaPathEvidence", ...]:
        keys = [item.target_layer.casefold() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("Via path evidence must have one result per target layer")
        return tuple(sorted(value, key=lambda item: item.target_layer.casefold()))

    def evidence_for_layer(self, layer: str) -> "ScenarioViaPathEvidence | None":
        key = layer.casefold()
        return next(
            (item for item in self.path_evidence if item.target_layer.casefold() == key),
            None,
        )


class ScenarioViaSegment(ScenarioModel):
    """One source-proven vertical Via segment kept in a saved scenario."""

    model_config = ConfigDict(frozen=True)

    via_id: str = Field(min_length=1)
    padstack: str = Field(min_length=1)
    drill_diameter_um: float = Field(gt=0)
    start_layer: str = Field(min_length=1)
    end_layer: str = Field(min_length=1)
    length_um: float = Field(gt=0)
    end_x_um: float
    end_y_um: float
    rotation_degrees: float = 0.0
    padstack_material: str | None = None

    @field_validator("end_x_um", "end_y_um", "rotation_degrees")
    @classmethod
    def finite_end_coordinate(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("Via segment coordinates and rotation must be finite")
        return value


class ScenarioViaPathEvidence(ScenarioPoint):
    """Compact unique TOP-to-plane path result; raw graph data is not retained."""

    model_config = ConfigDict(frozen=True)

    target_layer: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    target_padstack: str = Field(min_length=1)
    target_pad_kind: str = Field(min_length=1)
    target_pad_width_um: float = Field(gt=0)
    target_pad_height_um: float = Field(gt=0)
    segments: tuple[ScenarioViaSegment, ...] = Field(min_length=1)
    trace_hops: int = Field(default=0, ge=0)
    trace_alternate_exit: bool = False
    provenance: str = "SOURCE_PROVEN_MONOTONIC_VIA_CHAIN"

    @field_validator("provenance")
    @classmethod
    def nonblank_provenance(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Via path provenance must not be blank")
        return value


class ScenarioDecapConnection(ScenarioModel):
    """Complete source-connectivity classification for one scenario decap."""

    model_config = ConfigDict(frozen=True)

    refdes: str = Field(min_length=1)
    kind: DecapConnectionKind
    cluster_id: str | None = None
    power_vias: tuple[ScenarioViaLanding, ...] = ()
    ground_vias: tuple[ScenarioViaLanding, ...] = ()
    reason: str | None = None

    @field_validator("power_vias", "ground_vias")
    @classmethod
    def unique_via_ids(
        cls, value: tuple[ScenarioViaLanding, ...]
    ) -> tuple[ScenarioViaLanding, ...]:
        keys = [item.via_id.casefold() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("connection via IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.via_id.casefold()))

    @model_validator(mode="after")
    def valid_cluster_role(self) -> "ScenarioDecapConnection":
        clustered = self.kind in {
            DecapConnectionKind.SHARED_ANCHOR,
            DecapConnectionKind.SHARED_DUMMY,
        }
        if clustered and self.cluster_id is None:
            raise ValueError(f"{self.kind.value} connection requires cluster_id")
        if self.kind in {
            DecapConnectionKind.DIRECT,
            DecapConnectionKind.OUT_OF_SCOPE,
        } and self.cluster_id is not None:
            raise ValueError(f"{self.kind.value} connection cannot name a cluster")
        if self.kind in {
            DecapConnectionKind.UNRESOLVED,
            DecapConnectionKind.OUT_OF_SCOPE,
        } and not self.reason:
            raise ValueError(f"{self.kind.value} connection requires a reason")
        if self.kind == DecapConnectionKind.DIRECT and (
            not self.power_vias or not self.ground_vias
        ):
            raise ValueError(
                "DIRECT connection requires PWR and GND via evidence"
            )
        if self.kind == DecapConnectionKind.SHARED_ANCHOR and not (
            self.power_vias or self.ground_vias
        ):
            raise ValueError("SHARED_ANCHOR requires PWR or GND via evidence")
        if self.kind in {
            DecapConnectionKind.SHARED_DUMMY,
            DecapConnectionKind.FLOATING_DUMMY,
        } and (self.power_vias or self.ground_vias):
            raise ValueError(f"{self.kind.value} connection cannot contain via evidence")
        power_ids = {item.via_id.casefold() for item in self.power_vias}
        ground_ids = {item.via_id.casefold() for item in self.ground_vias}
        if power_ids.intersection(ground_ids):
            raise ValueError("one via landing cannot be both PWR and GND evidence")
        return self


class SharedPadCluster(ScenarioModel):
    """One immutable set of footprints joined by both top-side pad nodes.

    ``anchor_refdes`` identifies members carrying proven PWR and/or GND Via
    evidence.  An ANCHORED cluster is usable only when its aggregate PWR and
    GND supernodes both have evidence; no nearest PWR/GND pairing is inferred.
    An empty anchor set represents a floating, via-less pad cluster.
    ``eligibility`` is the conservative whole-source-cluster intersection.
    V2 stores ``via_eligibility`` once per unique physical PWR Via so a
    post-edit graph component can derive its own rail choices without
    duplicating data on every member that overlaps the same landing.  V3 adds
    ``isolation_gap_refdes`` only where source TOP copper proves a collinear,
    axis-aligned path whose local links can be cut by removing one pad cell.
    """

    model_config = ConfigDict(frozen=True)

    cluster_id: str = Field(min_length=1)
    state: SharedPadClusterState
    member_refdes: tuple[str, ...] = Field(min_length=1)
    anchor_refdes: tuple[str, ...] = ()
    dummy_refdes: tuple[str, ...] = ()
    power_net: str | None = None
    ground_net: str | None = None
    layer: str | None = None
    power_edges: tuple[tuple[str, str], ...] = ()
    ground_edges: tuple[tuple[str, str], ...] = ()
    isolation_gap_refdes: tuple[str, ...] = ()
    reason: str | None = None
    eligibility: dict[str, RailEligibility] = Field(default_factory=dict)
    via_eligibility: dict[str, dict[str, RailEligibility]] = Field(
        default_factory=dict
    )

    @field_validator(
        "member_refdes",
        "anchor_refdes",
        "dummy_refdes",
        "isolation_gap_refdes",
    )
    @classmethod
    def unique_ordered_refdes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(str(item).strip() for item in value)
        keys = [item.casefold() for item in normalized]
        if any(not item for item in normalized) or len(keys) != len(set(keys)):
            raise ValueError("shared-pad REFDES values must be nonblank and unique")
        return tuple(sorted(normalized, key=str.casefold))

    @field_validator("eligibility")
    @classmethod
    def keyed_by_rail_id(
        cls, value: dict[str, RailEligibility]
    ) -> dict[str, RailEligibility]:
        seen: set[str] = set()
        for key, item in value.items():
            normalized = key.strip().casefold()
            if not normalized or normalized in seen:
                raise ValueError("cluster eligibility keys must be nonblank and unique")
            seen.add(normalized)
            if normalized != item.rail_id.casefold():
                raise ValueError(
                    f"cluster eligibility key {key!r} does not match rail "
                    f"{item.rail_id!r}"
                )
        return value

    @field_validator("via_eligibility")
    @classmethod
    def keyed_by_physical_power_via(
        cls,
        value: dict[str, dict[str, RailEligibility]],
    ) -> dict[str, dict[str, RailEligibility]]:
        result: dict[str, dict[str, RailEligibility]] = {}
        seen_vias: set[str] = set()
        for raw_via_id, eligibility in sorted(
            value.items(), key=lambda item: item[0].casefold()
        ):
            via_id = raw_via_id.strip()
            via_key = via_id.casefold()
            if not via_id or via_key in seen_vias:
                raise ValueError(
                    "shared-pad physical PWR Via IDs must be nonblank and unique"
                )
            seen_vias.add(via_key)
            seen_rails: set[str] = set()
            canonical: dict[str, RailEligibility] = {}
            for raw_rail_id, item in sorted(
                eligibility.items(), key=lambda pair: pair[0].casefold()
            ):
                rail_id = raw_rail_id.strip()
                rail_key = rail_id.casefold()
                if not rail_id or rail_key in seen_rails:
                    raise ValueError(
                        f"PWR Via {via_id!r} eligibility rail keys must be "
                        "nonblank and unique"
                    )
                if rail_key != item.rail_id.casefold():
                    raise ValueError(
                        f"PWR Via {via_id!r} eligibility key {rail_id!r} does "
                        f"not match rail {item.rail_id!r}"
                    )
                seen_rails.add(rail_key)
                canonical[rail_id] = item
            result[via_id] = canonical
        return result

    @field_validator("power_edges", "ground_edges")
    @classmethod
    def valid_edges(
        cls, value: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        normalized: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for left, right in value:
            left = left.strip()
            right = right.strip()
            if not left or not right or left.casefold() == right.casefold():
                raise ValueError("shared-pad edges require two distinct REFDES values")
            edge = tuple(sorted((left, right), key=str.casefold))
            key = (edge[0].casefold(), edge[1].casefold())
            if key in seen:
                raise ValueError("shared-pad edges must be unique")
            seen.add(key)
            normalized.append(edge)
        return tuple(sorted(normalized, key=lambda edge: tuple(map(str.casefold, edge))))

    @model_validator(mode="after")
    def valid_membership(self) -> "SharedPadCluster":
        members = {item.casefold() for item in self.member_refdes}
        anchors = {item.casefold() for item in self.anchor_refdes}
        dummies = {item.casefold() for item in self.dummy_refdes}
        gap_eligible = {item.casefold() for item in self.isolation_gap_refdes}
        if not anchors.issubset(members) or not dummies.issubset(members):
            raise ValueError("shared-pad anchors and dummies must be cluster members")
        if anchors.intersection(dummies) or anchors.union(dummies) != members:
            raise ValueError(
                "shared-pad anchors and dummies must partition the cluster members"
            )
        if not gap_eligible.issubset(members):
            raise ValueError(
                "shared-pad isolation-gap eligibility must reference cluster members"
            )
        if gap_eligible and self.state != SharedPadClusterState.ANCHORED:
            raise ValueError(
                "only an ANCHORED shared-pad cluster can allow isolation gaps"
            )
        edge_members = {
            refdes.casefold()
            for edges in (self.power_edges, self.ground_edges)
            for edge in edges
            for refdes in edge
        }
        if not edge_members.issubset(members):
            raise ValueError("shared-pad edges must reference cluster members")

        if self.state != SharedPadClusterState.UNRESOLVED and not self.source_graph_is_connected(
            self.power_edges
        ):
            raise ValueError(
                "resolved shared-pad cluster PWR source graph must connect every "
                "member"
            )
        if self.state == SharedPadClusterState.ANCHORED:
            if not anchors or len(members) < 2:
                raise ValueError(
                    "an ANCHORED shared-pad cluster requires an anchor and two members"
                )
        elif self.state == SharedPadClusterState.FLOATING:
            if anchors or dummies != members:
                raise ValueError("a FLOATING shared-pad cluster can contain only dummies")
            if self.eligibility or self.via_eligibility:
                raise ValueError("a FLOATING shared-pad cluster cannot have eligibility")
        elif self.eligibility or self.via_eligibility:
            raise ValueError("an UNRESOLVED shared-pad cluster cannot have eligibility")
        if self.state == SharedPadClusterState.UNRESOLVED and not self.reason:
            raise ValueError("an UNRESOLVED shared-pad cluster requires a reason")
        return self

    def source_graph_is_connected(
        self, edges: tuple[tuple[str, str], ...]
    ) -> bool:
        """Whether one immutable source terminal graph spans every member."""

        members = {item.casefold() for item in self.member_refdes}
        if len(members) <= 1:
            return True
        adjacency = {item: set() for item in members}
        for left, right in edges:
            left_key, right_key = left.casefold(), right.casefold()
            adjacency[left_key].add(right_key)
            adjacency[right_key].add(left_key)
        visited: set[str] = set()
        pending = [next(iter(members))]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency[current] - visited)
        return visited == members

    @property
    def is_floating(self) -> bool:
        return self.state == SharedPadClusterState.FLOATING


class SharedPadConnectionAnalysis(ScenarioModel):
    """Versioned, complete decap-connectivity result for one source SPD."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)
    source_sha256: str
    connections: dict[str, ScenarioDecapConnection] = Field(default_factory=dict)
    clusters: tuple[SharedPadCluster, ...] = ()

    @field_validator("version")
    @classmethod
    def supported_analysis_version(cls, value: str) -> str:
        if value not in _SUPPORTED_SHARED_PAD_ANALYSIS_VERSIONS:
            raise ValueError(
                f"unsupported shared-pad analysis version {value!r}; expected one "
                f"of {sorted(_SUPPORTED_SHARED_PAD_ANALYSIS_VERSIONS)!r}"
            )
        return value

    @field_validator("source_sha256")
    @classmethod
    def valid_source_hash(cls, value: str) -> str:
        return _validate_sha256(value, label="connection-analysis source SHA-256")

    @field_validator("connections")
    @classmethod
    def keyed_by_refdes(
        cls, value: dict[str, ScenarioDecapConnection]
    ) -> dict[str, ScenarioDecapConnection]:
        seen: set[str] = set()
        for key, item in value.items():
            normalized = key.strip().casefold()
            if not normalized or normalized in seen:
                raise ValueError("connection REFDES keys must be nonblank and unique")
            seen.add(normalized)
            if key != item.refdes:
                raise ValueError(
                    f"connection key {key!r} does not exactly match REFDES "
                    f"{item.refdes!r}"
                )
        return value

    @field_validator("clusters")
    @classmethod
    def unique_ordered_clusters(
        cls, value: tuple[SharedPadCluster, ...]
    ) -> tuple[SharedPadCluster, ...]:
        keys = [item.cluster_id.casefold() for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("shared-pad cluster IDs must be unique")
        return tuple(sorted(value, key=lambda item: item.cluster_id.casefold()))

    @model_validator(mode="after")
    def legacy_ground_graph_remains_connected(self) -> "SharedPadConnectionAnalysis":
        """Keep V2-V4's documented one-GND source topology unchanged.

        V5 is the first analysis format that can represent disconnected source
        top-GND components explicitly.  Older saved analyses remain readable
        but retain their historical validation/semantics until a source reimport.
        """

        if self.version == SHARED_PAD_ANALYSIS_VERSION:
            return self
        for cluster in self.clusters:
            if (
                cluster.state != SharedPadClusterState.UNRESOLVED
                and not cluster.source_graph_is_connected(cluster.ground_edges)
            ):
                raise ValueError(
                    "legacy resolved shared-pad cluster GND source graph must "
                    "connect every member"
                )
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
    pad_state: DecapPadState = DecapPadState.NORMAL
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

    @model_validator(mode="after")
    def isolation_gap_is_not_populated(self) -> "ScenarioDecap":
        if self.pad_state == DecapPadState.ISOLATION_GAP and self.enabled:
            raise ValueError("an ISOLATION_GAP decap cannot remain enabled")
        return self

    @property
    def x_um(self) -> float:
        return self.center.x_um

    @property
    def y_um(self) -> float:
        return self.center.y_um

    @property
    def power_net(self) -> str:
        return self.current_net


@dataclass(frozen=True, slots=True)
class SharedPadCurrentComponent:
    """One post-edit PWR component derived from immutable source pad edges."""

    cluster_id: str
    current_rail_id: str
    current_net: str
    member_refdes: tuple[str, ...]
    power_anchor_refdes: tuple[str, ...]
    power_vias: tuple[ScenarioViaLanding, ...]
    ground_vias: tuple[ScenarioViaLanding, ...]


@dataclass(frozen=True, slots=True)
class SharedPadGroundComponent:
    """One post-edit GND component derived solely from source ``ground_edges``."""

    cluster_id: str
    member_refdes: tuple[str, ...]
    ground_anchor_refdes: tuple[str, ...]
    ground_vias: tuple[ScenarioViaLanding, ...]


@dataclass(frozen=True, slots=True)
class SharedPadCapacitorComponentMapping:
    """The PWR/GND supernodes joined by one active capacitor footprint."""

    refdes: str
    power_component_index: int
    ground_component_index: int


@dataclass(frozen=True, slots=True)
class SharedPadPowerViaConflict:
    """One physical PWR Via claimed by more than one derived NET component."""

    cluster_id: str
    via_id: str
    owner_refdes: tuple[str, ...]
    component_member_refdes: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class SharedPadGroundViaConflict:
    """One physical GND Via claimed by multiple post-edit GND components."""

    cluster_id: str
    via_id: str
    owner_refdes: tuple[str, ...]
    component_member_refdes: tuple[tuple[str, ...], ...]


class SharedPadRailAliasConflictError(ValueError):
    """Raised when one physical same-NET component has mixed rail identities."""

    def __init__(
        self,
        *,
        cluster_id: str,
        current_net: str,
        member_refdes: tuple[str, ...],
        rail_ids: tuple[str, ...],
    ) -> None:
        self.cluster_id = cluster_id
        self.current_net = current_net
        self.member_refdes = member_refdes
        self.rail_ids = rail_ids
        super().__init__(
            f"shared-pad cluster {cluster_id!r} same-NET component "
            f"{member_refdes} for {current_net!r} has mixed rail IDs {rail_ids}; "
            "every physically shorted same-NET member must use one rail identity"
        )


class SharedPadActiveShortError(ValueError):
    """Raised when adjacent active pad cells carry different PWR NETs."""

    def __init__(
        self,
        *,
        cluster_id: str,
        left_refdes: str,
        right_refdes: str,
        left_net: str,
        right_net: str,
    ) -> None:
        self.cluster_id = cluster_id
        self.left_refdes = left_refdes
        self.right_refdes = right_refdes
        self.left_net = left_net
        self.right_net = right_net
        super().__init__(
            f"shared-pad edge {left_refdes!r}-{right_refdes!r} in "
            f"{cluster_id!r} remains active across different NETs "
            f"{left_net!r}/{right_net!r}; insert an ISOLATION_GAP pad cell"
        )


@dataclass(frozen=True, slots=True)
class SharedPadComponentDerivation:
    """Deterministic source-edge PWR/GND components and Via conflicts."""

    components: tuple[SharedPadCurrentComponent, ...]
    ground_components: tuple[SharedPadGroundComponent, ...] = ()
    capacitor_component_mappings: tuple[SharedPadCapacitorComponentMapping, ...] = ()
    shared_power_via_conflicts: tuple[SharedPadPowerViaConflict, ...] = ()
    shared_ground_via_conflicts: tuple[SharedPadGroundViaConflict, ...] = ()


def _casefold_index(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = str(raw_key).casefold()
        if key in result:
            raise ValueError(f"{label} keys must be case-insensitively unique")
        result[key] = item
    return result


def derive_shared_pad_current_components(
    cluster: SharedPadCluster,
    decap_by_refdes: Mapping[str, ScenarioDecap],
    connection_by_refdes: Mapping[str, ScenarioDecapConnection],
    *,
    analysis_version: str | None = None,
) -> SharedPadComponentDerivation:
    """Derive active PWR/GND components after removing isolation-gap cells.

    The two source graphs are intentionally independent.  A V5 analysis uses
    ``power_edges`` and ``ground_edges`` to map every capacitor onto exactly one
    PWR and one GND component.  Older persisted analyses retain their documented
    implicit continuous-GND semantics; they are readable but must be reanalyzed
    before the accuracy evaluator accepts them.
    """

    decaps = _casefold_index(decap_by_refdes, label="decap")
    connections = _casefold_index(connection_by_refdes, label="connection")
    member_keys = tuple(item.casefold() for item in cluster.member_refdes)
    missing_decaps = [
        cluster.member_refdes[index]
        for index, key in enumerate(member_keys)
        if key not in decaps
    ]
    missing_connections = [
        cluster.member_refdes[index]
        for index, key in enumerate(member_keys)
        if key not in connections
    ]
    if missing_decaps or missing_connections:
        raise ValueError(
            f"shared-pad cluster {cluster.cluster_id!r} cannot be derived "
            f"(missing decaps={missing_decaps}, connections={missing_connections})"
        )

    order = {key: index for index, key in enumerate(member_keys)}
    active_keys = tuple(
        key
        for key in member_keys
        if decaps[key].pad_state != DecapPadState.ISOLATION_GAP
    )
    active_set = set(active_keys)
    def grouped_edges(
        edges: tuple[tuple[str, str], ...],
        *,
        check_active_short: bool,
    ) -> list[tuple[str, ...]]:
        adjacency: dict[str, set[str]] = {key: set() for key in active_keys}
        for left_refdes, right_refdes in edges:
            left = left_refdes.casefold()
            right = right_refdes.casefold()
            if left not in active_set or right not in active_set:
                continue
            if check_active_short and (
                decaps[left].current_net.casefold()
                != decaps[right].current_net.casefold()
            ):
                raise SharedPadActiveShortError(
                    cluster_id=cluster.cluster_id,
                    left_refdes=decaps[left].refdes,
                    right_refdes=decaps[right].refdes,
                    left_net=decaps[left].current_net,
                    right_net=decaps[right].current_net,
                )
            adjacency[left].add(right)
            adjacency[right].add(left)
        result: list[tuple[str, ...]] = []
        remaining = set(active_keys)
        while remaining:
            first = min(remaining, key=order.__getitem__)
            pending = [first]
            component: set[str] = set()
            while pending:
                current = pending.pop()
                if current in component:
                    continue
                component.add(current)
                pending.extend(adjacency[current] - component)
            remaining.difference_update(component)
            result.append(tuple(sorted(component, key=order.__getitem__)))
        return result

    grouped_keys = grouped_edges(cluster.power_edges, check_active_short=True)
    # A pre-V5 scenario was written under the documented one-GND model.  Keep
    # that semantic for deserialization/legacy hashing rather than silently
    # deriving a different topology before a fresh source analysis.
    explicit_ground = analysis_version in {None, SHARED_PAD_ANALYSIS_VERSION}
    ground_grouped_keys = (
        grouped_edges(cluster.ground_edges, check_active_short=False)
        if explicit_ground
        else [active_keys] if active_keys else []
    )

    components: list[SharedPadCurrentComponent] = []
    via_components: dict[str, set[int]] = {}
    via_owners: dict[str, set[str]] = {}
    canonical_via_id: dict[str, str] = {}
    for component_index, keys in enumerate(grouped_keys):
        members = tuple(decaps[key].refdes for key in keys)
        first_decap = decaps[keys[0]]
        rail_ids_by_key = {
            decaps[key].current_rail_id.casefold(): decaps[key].current_rail_id
            for key in keys
        }
        if len(rail_ids_by_key) != 1:
            raise SharedPadRailAliasConflictError(
                cluster_id=cluster.cluster_id,
                current_net=first_decap.current_net,
                member_refdes=members,
                rail_ids=tuple(
                    rail_ids_by_key[key] for key in sorted(rail_ids_by_key)
                ),
            )
        power_by_key: dict[str, ScenarioViaLanding] = {}
        anchor_refdes: list[str] = []
        for key in keys:
            connection = connections[key]
            if connection.power_vias:
                anchor_refdes.append(decaps[key].refdes)
            for landing in connection.power_vias:
                via_key = landing.via_id.casefold()
                power_by_key.setdefault(via_key, landing)
                canonical_via_id.setdefault(via_key, landing.via_id)
                via_components.setdefault(via_key, set()).add(component_index)
                via_owners.setdefault(via_key, set()).add(decaps[key].refdes)
        components.append(
            SharedPadCurrentComponent(
                cluster_id=cluster.cluster_id,
                current_rail_id=first_decap.current_rail_id,
                current_net=first_decap.current_net,
                member_refdes=members,
                power_anchor_refdes=tuple(anchor_refdes),
                power_vias=tuple(power_by_key[key] for key in sorted(power_by_key)),
                ground_vias=(),
            )
        )

    conflicts = tuple(
        SharedPadPowerViaConflict(
            cluster_id=cluster.cluster_id,
            via_id=canonical_via_id[via_key],
            owner_refdes=tuple(
                sorted(via_owners[via_key], key=str.casefold)
            ),
            component_member_refdes=tuple(
                components[index].member_refdes
                for index in sorted(via_components[via_key])
            ),
        )
        for via_key in sorted(via_components)
        if len(via_components[via_key]) > 1
    )
    ground_components: list[SharedPadGroundComponent] = []
    ground_via_components: dict[str, set[int]] = {}
    ground_via_owners: dict[str, set[str]] = {}
    ground_canonical_via_id: dict[str, str] = {}
    for component_index, keys in enumerate(ground_grouped_keys):
        members = tuple(decaps[key].refdes for key in keys)
        ground_by_key: dict[str, ScenarioViaLanding] = {}
        anchor_refdes: list[str] = []
        for key in keys:
            connection = connections[key]
            if connection.ground_vias:
                anchor_refdes.append(decaps[key].refdes)
            for landing in connection.ground_vias:
                via_key = landing.via_id.casefold()
                ground_by_key.setdefault(via_key, landing)
                ground_canonical_via_id.setdefault(via_key, landing.via_id)
                ground_via_components.setdefault(via_key, set()).add(component_index)
                ground_via_owners.setdefault(via_key, set()).add(decaps[key].refdes)
        ground_components.append(
            SharedPadGroundComponent(
                cluster_id=cluster.cluster_id,
                member_refdes=members,
                ground_anchor_refdes=tuple(anchor_refdes),
                ground_vias=tuple(
                    ground_by_key[key] for key in sorted(ground_by_key)
                ),
            )
        )
    ground_conflicts = tuple(
        SharedPadGroundViaConflict(
            cluster_id=cluster.cluster_id,
            via_id=ground_canonical_via_id[via_key],
            owner_refdes=tuple(
                sorted(ground_via_owners[via_key], key=str.casefold)
            ),
            component_member_refdes=tuple(
                ground_components[index].member_refdes
                for index in sorted(ground_via_components[via_key])
            ),
        )
        for via_key in sorted(ground_via_components)
        if len(ground_via_components[via_key]) > 1
    )
    power_by_member = {
        refdes.casefold(): component_index
        for component_index, component in enumerate(components)
        for refdes in component.member_refdes
    }
    ground_by_member = {
        refdes.casefold(): component_index
        for component_index, component in enumerate(ground_components)
        for refdes in component.member_refdes
    }
    capacitor_mappings = tuple(
        SharedPadCapacitorComponentMapping(
            refdes=decaps[key].refdes,
            power_component_index=power_by_member[key],
            ground_component_index=ground_by_member[key],
        )
        for key in active_keys
    )
    # Preserve the former convenience field for callers that only report PWR
    # components.  Evaluation uses the explicit GND components/mappings above.
    all_ground_vias = {
        landing.via_id.casefold(): landing
        for component in ground_components
        for landing in component.ground_vias
    }
    components = [
        SharedPadCurrentComponent(
            cluster_id=component.cluster_id,
            current_rail_id=component.current_rail_id,
            current_net=component.current_net,
            member_refdes=component.member_refdes,
            power_anchor_refdes=component.power_anchor_refdes,
            power_vias=component.power_vias,
            ground_vias=tuple(
                all_ground_vias[key] for key in sorted(all_ground_vias)
            ),
        )
        for component in components
    ]
    return SharedPadComponentDerivation(
        components=tuple(components),
        ground_components=tuple(ground_components),
        capacitor_component_mappings=capacitor_mappings,
        shared_power_via_conflicts=conflicts,
        shared_ground_via_conflicts=ground_conflicts,
    )


def shared_pad_component_eligibility(
    cluster: SharedPadCluster,
    component: SharedPadCurrentComponent,
) -> dict[str, RailEligibility]:
    """Intersect rail eligibility over unique physical PWR vias in a component."""

    via_eligibility = {
        via_id.casefold(): eligibility
        for via_id, eligibility in cluster.via_eligibility.items()
    }
    common: dict[str, RailEligibility] | None = None
    for landing in component.power_vias:
        eligibility = via_eligibility.get(landing.via_id.casefold(), {})
        allowed = {
            item.rail_id.casefold(): item
            for item in eligibility.values()
            if item.allowed
        }
        if common is None:
            common = allowed
        else:
            common = {
                key: item for key, item in common.items() if key in allowed
            }
        if not common:
            return {}
    if common is None:
        return {}
    return {
        item.rail_id: item
        for item in sorted(common.values(), key=lambda value: value.rail_id.casefold())
    }


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


def mixed_reference_ground_landing_identity(
    refdes: str, landing: ScenarioViaLanding
) -> str:
    """Stable identity for one source GND landing covered by a witness."""

    return "|".join(
        (
            str(refdes).strip().casefold(),
            "gnd",
            landing.via_id.strip().casefold(),
            landing.net.strip().casefold(),
            landing.endpoint_node_id.strip().casefold(),
        )
    )


def mixed_reference_ground_witness_failures(
    scenario: "ScenarioSpec",
    *,
    require_current_coverage: bool = True,
    _project: ProjectSpec | None = None,
) -> dict[str, str]:
    """Return fail-closed mixed-reference GND witness diagnostics by rail.

    A witness may include a disabled source decap so a normal DNP edit does not
    invalidate source evidence.  The witness universe is keyed by immutable
    PWR eligibility rather than mutable ``current_rail_id``.  Persistence
    validation uses ``require_current_coverage=False`` so an unsupported,
    unselected rail cannot prevent opening an otherwise valid source drawing.
    Evaluation preflight leaves it enabled and requires every consumed DIRECT
    or derived shared-pad GND landing to be present.
    """

    analysis = scenario.connection_analysis
    if analysis is None:
        return {
            rail.rail_id: "mixed-reference GND reachability witness requires source connection analysis"
            for rail in scenario.base_project.rails
            if rail.mixed_reference_certificate is not None
        }
    decaps = {item.refdes.casefold(): item for item in scenario.decaps}
    connections = {
        item.refdes.casefold(): item for item in analysis.connections.values()
    }
    failures: dict[str, str] = {}
    project = _project if _project is not None else scenario.base_project
    for rail in project.rails:
        certificate = rail.mixed_reference_certificate
        if certificate is None:
            continue
        witness = rail.mixed_reference_ground_witness
        if witness is None:
            failures[rail.rail_id] = "mixed-reference GND reachability witness is missing"
            continue
        try:
            # Revalidate because Pydantic ``model_copy`` intentionally skips
            # validation and is used by scenario edit services.
            witness = MixedReferenceGroundWitness.model_validate(
                witness.model_dump(mode="json")
            )
        except ValueError as exc:
            failures[rail.rail_id] = f"mixed-reference GND witness is invalid ({exc})"
            continue
        if (
            witness.source_sha256 != scenario.source.sha256
            or witness.rail_net.casefold() != rail.net.casefold()
            or witness.gnd_net.casefold() != certificate.gnd_net.casefold()
            or witness.pwr_layer.casefold() != rail.pwr_layer.casefold()
            or witness.gnd_layer.casefold() != rail.gnd_layer.casefold()
            or witness.gnd_asset_sha256 != certificate.gnd_asset_sha256
        ):
            failures[rail.rail_id] = "mixed-reference GND witness does not match current source or rail pair"
            continue
        stable_universe: set[str] = set()
        enabled_required: set[str] = set()
        enabled_wrong_net: set[str] = set()
        for key, decap in decaps.items():
            connection = connections.get(key)
            # Shared-pad evidence is collected below per *cluster*.  In
            # particular, evaluation joins a selected PWR component to its
            # intersecting derived GND component, whose Via anchor may belong
            # to a different member.  Treating a SHARED_ANCHOR as an ordinary
            # decap here makes the stable universe depend on which member
            # happened to own a source Via (and used to make an earlier dummy
            # a tempting but incorrect proxy).
            if connection is None or connection.kind != DecapConnectionKind.DIRECT:
                continue
            eligibility = next(
                (
                    item
                    for rail_id, item in decap.eligibility.items()
                    if rail_id.casefold() == rail.rail_id.casefold()
                ),
                None,
            )
            pwr_eligible = bool(eligibility is not None and eligibility.allowed)
            owner = decap.refdes
            for landing in connection.ground_vias:
                if landing.net.casefold() != certificate.gnd_net.casefold():
                    if (
                        pwr_eligible
                        and decap.enabled
                        and decap.current_rail_id.casefold() == rail.rail_id.casefold()
                    ):
                        enabled_wrong_net.add(
                            "|".join(
                                (
                                    decap.refdes.casefold(), "gnd",
                                    landing.via_id.casefold(), landing.net.casefold(),
                                    landing.endpoint_node_id.casefold(),
                                )
                            )
                        )
                    continue
                identity = mixed_reference_ground_landing_identity(owner, landing)
                if pwr_eligible:
                    stable_universe.add(identity)
                if (
                    pwr_eligible
                    and decap.enabled
                    and decap.current_rail_id.casefold() == rail.rail_id.casefold()
                ):
                    enabled_required.add(identity)
        # Shared-pad evaluation consumes the complete derived GND component,
        # not merely anchors attached to a PWR component's own REFDES.  Mirror
        # the evaluator's component-to-ground-component selection so an anchor
        # owned by another rail member cannot bypass this return-path witness.
        for cluster in analysis.clusters:
            if cluster.state != SharedPadClusterState.ANCHORED:
                continue
            # Import writes source witness entries only for the conservative
            # whole-cluster intersection of its physical PWR-via eligibility.
            # Keep that narrowing here: a later isolation-gap edit must not
            # silently widen a witness to a hypothetical subset of this
            # aggregate source cluster.
            cluster_eligibility = next(
                (
                    item
                    for rail_id, item in cluster.eligibility.items()
                    if rail_id.casefold() == rail.rail_id.casefold()
                ),
                None,
            )
            if cluster_eligibility is None or not cluster_eligibility.allowed:
                continue
            members = {
                key: decaps[key]
                for key in (item.casefold() for item in cluster.member_refdes)
                if key in decaps
            }
            member_connections = {
                key: connections[key] for key in members if key in connections
            }
            if len(members) != len(cluster.member_refdes) or len(member_connections) != len(members):
                continue
            # The import-side witness owns all source GND landings by cluster,
            # after the conservative aggregate PWR eligibility check above.
            # Populate that immutable universe before considering the mutable
            # post-edit current assignment below.
            for connection in member_connections.values():
                for landing in connection.ground_vias:
                    if landing.net.casefold() == certificate.gnd_net.casefold():
                        stable_universe.add(
                            mixed_reference_ground_landing_identity(
                                f"cluster:{cluster.cluster_id}", landing
                            )
                        )
            try:
                derivation = derive_shared_pad_current_components(
                    cluster, members, member_connections, analysis_version=analysis.version
                )
            except ValueError:
                # Connection preflight reports the actionable cluster failure.
                continue
            selected = tuple(
                component
                for component in derivation.components
                if component.current_rail_id.casefold() == rail.rail_id.casefold()
                and any(decaps[refdes.casefold()].enabled for refdes in component.member_refdes)
            )
            selected_members = {
                refdes.casefold()
                for component in selected
                for refdes in component.member_refdes
            }
            for component in derivation.ground_components:
                if not selected_members.intersection(
                    refdes.casefold() for refdes in component.member_refdes
                ):
                    continue
                for landing in component.ground_vias:
                    identity = mixed_reference_ground_landing_identity(
                        f"cluster:{cluster.cluster_id}", landing
                    )
                    if landing.net.casefold() != certificate.gnd_net.casefold():
                        enabled_wrong_net.add(identity)
                    else:
                        enabled_required.add(identity)
        witnessed = set(witness.landing_identities)
        stale = witnessed - stable_universe
        missing = enabled_required - witnessed
        if require_current_coverage and enabled_wrong_net:
            failures[rail.rail_id] = (
                "enabled evaluation GND landing(s) do not use the certificate exact "
                "DGND net: " + ", ".join(sorted(enabled_wrong_net)[:3])
            )
        elif stale:
            failures[rail.rail_id] = (
                "mixed-reference GND witness contains stale/unmapped landing(s): "
                + ", ".join(sorted(stale)[:3])
            )
        elif require_current_coverage and missing:
            failures[rail.rail_id] = (
                "enabled evaluation GND landing(s) lack mixed-reference reachability evidence: "
                + ", ".join(sorted(missing)[:3])
            )
    return failures


@dataclass(slots=True)
class _ScenarioValidationMemo:
    """One-call scratch state for full, non-persistent scenario validation.

    A memo is created by the archive loader and passed through Pydantic's
    validation context.  Every value in it is still derived from the decoded
    scenario during this validation call; no persisted digest or prior load is
    trusted.  Keeping the object outside ``ScenarioSpec`` also prevents stale
    identities after an edit or ``model_copy``.
    """

    owner: Any | None = None
    project: ProjectSpec | None = None
    connection_payload: dict[str, Any] | None = None
    source_state_fingerprint: str | None = None
    design_fingerprint: str | None = None
    decap_by_key: dict[str, ScenarioDecap] | None = None
    rail_by_key: dict[str, Any] | None = None
    cap_model_by_key: dict[str, Any] | None = None
    cap_model_payload_by_key: dict[str, dict[str, Any]] | None = None
    sorted_decaps: tuple[ScenarioDecap, ...] | None = None
    baseline_project_payload: dict[str, Any] | None = None

    def reset(self, project: ProjectSpec) -> None:
        """Discard derived values and retain only the newly validated project."""

        self.owner = None
        self.project = project
        self.connection_payload = None
        self.source_state_fingerprint = None
        self.design_fingerprint = None
        self.decap_by_key = None
        self.rail_by_key = None
        self.cap_model_by_key = None
        self.cap_model_payload_by_key = None
        self.sorted_decaps = None
        self.baseline_project_payload = None

    def bind(self, owner: Any, project: ProjectSpec) -> None:
        """Bind all derived values to one exact ScenarioSpec object identity."""

        if self.owner is owner and self.project is project:
            return
        self.reset(project)
        self.owner = owner


class ScenarioSpec(ScenarioModel):
    """Complete editable SPD decap scenario, including resumable UI state."""

    schema_version: str = SCENARIO_SCHEMA_VERSION
    app_version: str = SCENARIO_APP_VERSION
    source: SourceIdentity
    normalized_project: dict[str, Any]
    decaps: list[ScenarioDecap] = Field(default_factory=list)
    connection_analysis: SharedPadConnectionAnalysis | None = None
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
    def validate_normalized_project(
        cls, value: object, info: ValidationInfo
    ) -> dict[str, Any]:
        if isinstance(value, ProjectSpec):
            project = value
            preserve_legacy_empty_clusters = False
            legacy_project: Mapping[str, object] | None = None
        else:
            preserve_legacy_empty_clusters = (
                isinstance(value, Mapping)
                and "shared_pad_clusters" not in value
            )
            legacy_project = value if isinstance(value, Mapping) else None
            project = ProjectSpec.model_validate(value)
        if isinstance(info.context, _ScenarioValidationMemo):
            # The context is deliberately reset here so even accidental reuse
            # across two model_validate calls cannot reuse a prior design.
            info.context.reset(project)
        payload = project.model_dump(mode="json")
        # ProjectSpec gained an optional shared-pad collection while scenario
        # schema 0.1 remained readable.  Do not inject the new empty default
        # into a legacy normalized project: its saved design/baseline hashes
        # were calculated before this key existed.
        if preserve_legacy_empty_clusters and not project.shared_pad_clusters:
            payload.pop("shared_pad_clusters", None)
        # Frequency-dependent dielectric data was added without a scenario
        # schema bump.  Keep historical scenario hashes stable when an older
        # normalized project did not carry either new stackup key; source data
        # that does carry a table remains part of the electrical fingerprint.
        if legacy_project is not None:
            _preserve_legacy_stackup_row_shape(payload, legacy_project)
        return payload

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

    def _validate_connection_analysis(
        self,
        decap_by_key: dict[str, ScenarioDecap],
        *,
        project: ProjectSpec | None = None,
    ) -> None:
        analysis = self.connection_analysis
        if analysis is None:
            # Schema 0.1 scenarios written by earlier releases must retain
            # their byte-derived fingerprints and remain readable.  PWR-edit
            # services fail closed until source connectivity is analyzed.
            return
        if analysis.source_sha256 != self.source.sha256:
            raise ValueError("connection analysis does not match the source SPD")

        connection_by_key = {
            item.refdes.casefold(): item for item in analysis.connections.values()
        }
        if set(connection_by_key) != set(decap_by_key):
            missing = sorted(set(decap_by_key) - set(connection_by_key))
            extra = sorted(set(connection_by_key) - set(decap_by_key))
            raise ValueError(
                "connection analysis must classify every scenario decap exactly once "
                f"(missing={missing}, extra={extra})"
            )
        for key, connection in connection_by_key.items():
            if connection.refdes != decap_by_key[key].refdes:
                raise ValueError(
                    "connection-analysis REFDES spelling must match the scenario decap"
                )

        physical_vias: dict[
            str, tuple[str, str, ScenarioViaLanding]
        ] = {}
        for connection in connection_by_key.values():
            unit_id = (
                f"cluster:{connection.cluster_id.casefold()}"
                if connection.cluster_id is not None
                else f"decap:{connection.refdes.casefold()}"
            )
            for terminal, landings in (
                ("PWR", connection.power_vias),
                ("GND", connection.ground_vias),
            ):
                for landing in landings:
                    via_key = landing.via_id.casefold()
                    previous = physical_vias.get(via_key)
                    if previous is None:
                        physical_vias[via_key] = (unit_id, terminal, landing)
                        continue
                    previous_unit, previous_terminal, previous_landing = previous
                    if previous_unit != unit_id:
                        raise ValueError(
                            f"physical Via {landing.via_id!r} is reused across "
                            "independent decap/shared-pad units"
                        )
                    if (
                        previous_terminal != terminal
                        or previous_landing != landing
                    ):
                        raise ValueError(
                            f"physical Via {landing.via_id!r} has conflicting "
                            "terminal or landing evidence"
                        )

        cluster_by_key = {
            item.cluster_id.casefold(): item for item in analysis.clusters
        }
        membership: dict[str, SharedPadCluster] = {}
        validated_project = project if project is not None else self.base_project
        rail_by_key = {
            item.rail_id.casefold(): item for item in validated_project.rails
        }
        for cluster in analysis.clusters:
            member_keys = {item.casefold() for item in cluster.member_refdes}
            anchor_keys = {item.casefold() for item in cluster.anchor_refdes}
            unknown_members = member_keys - set(decap_by_key)
            if unknown_members:
                raise ValueError(
                    f"shared-pad cluster {cluster.cluster_id!r} contains unknown "
                    f"REFDES values: {sorted(unknown_members)}"
                )
            overlap = member_keys.intersection(membership)
            if overlap:
                raise ValueError(
                    "scenario decaps cannot belong to more than one shared-pad "
                    f"cluster: {sorted(overlap)}"
                )
            for key in member_keys:
                membership[key] = cluster

            source_nets = {
                decap_by_key[key].source_net.casefold() for key in member_keys
            }
            source_rails = {
                decap_by_key[key].source_rail_id.casefold() for key in member_keys
            }
            if cluster.state != SharedPadClusterState.UNRESOLVED and any(
                len(values) != 1
                for values in (source_nets, source_rails)
            ):
                raise ValueError(
                    f"shared-pad cluster {cluster.cluster_id!r} members must share "
                    "one source PWR assignment"
                )
            if cluster.state == SharedPadClusterState.ANCHORED:
                source_rail_key = next(iter(source_rails))
                source_rail = rail_by_key.get(source_rail_key)
                if source_rail is None:
                    raise ValueError(
                        f"shared-pad cluster {cluster.cluster_id!r} references "
                        f"unknown source rail {source_rail_key!r}"
                    )
                if source_rail.net.casefold() not in source_nets:
                    raise ValueError(
                        f"shared-pad cluster {cluster.cluster_id!r} source NET "
                        f"does not match rail {source_rail.rail_id!r}"
                    )
                for key in member_keys:
                    decap = decap_by_key[key]
                    current_rail = rail_by_key.get(
                        decap.current_rail_id.casefold()
                    )
                    if current_rail is None:
                        raise ValueError(
                            f"{decap.refdes}: shared-pad member references unknown "
                            f"current rail {decap.current_rail_id!r}"
                        )
                    if current_rail.net.casefold() != decap.current_net.casefold():
                        raise ValueError(
                            f"{decap.refdes}: current NET does not match rail "
                            f"{current_rail.rail_id!r}"
                        )
            if (
                cluster.state == SharedPadClusterState.ANCHORED
                and not cluster.via_eligibility
            ):
                raise ValueError(
                    f"anchored shared-pad cluster {cluster.cluster_id!r} requires "
                    "per-physical-PWR-Via eligibility"
                )
            if (
                cluster.state != SharedPadClusterState.UNRESOLVED
                and
                cluster.power_net is not None
                and cluster.power_net.casefold() not in source_nets
            ):
                raise ValueError(
                    f"shared-pad cluster {cluster.cluster_id!r} PWR NET does not "
                    "match its member source assignment"
                )

            for raw_rail_id, eligibility in cluster.eligibility.items():
                rail = rail_by_key.get(raw_rail_id.casefold())
                if rail is None:
                    raise ValueError(
                        f"shared-pad cluster {cluster.cluster_id!r} references "
                        f"unknown rail {raw_rail_id!r}"
                    )
                if (
                    eligibility.rail_id.casefold() != rail.rail_id.casefold()
                    or eligibility.net.casefold() != rail.net.casefold()
                    or eligibility.pwr_layer.casefold() != rail.pwr_layer.casefold()
                    or eligibility.gnd_layer.casefold() != rail.gnd_layer.casefold()
                ):
                    raise ValueError(
                        f"shared-pad cluster {cluster.cluster_id!r} eligibility "
                        f"does not match rail {rail.rail_id!r}"
                    )
            for via_id, via_eligibility in cluster.via_eligibility.items():
                for raw_rail_id, eligibility in via_eligibility.items():
                    rail = rail_by_key.get(raw_rail_id.casefold())
                    if rail is None:
                        raise ValueError(
                            f"shared-pad cluster {cluster.cluster_id!r} PWR Via "
                            f"{via_id!r} references unknown rail {raw_rail_id!r}"
                        )
                    if (
                        eligibility.rail_id.casefold() != rail.rail_id.casefold()
                        or eligibility.net.casefold() != rail.net.casefold()
                        or eligibility.pwr_layer.casefold()
                        != rail.pwr_layer.casefold()
                        or eligibility.gnd_layer.casefold()
                        != rail.gnd_layer.casefold()
                    ):
                        raise ValueError(
                            f"shared-pad cluster {cluster.cluster_id!r} PWR Via "
                            f"{via_id!r} eligibility does not match rail "
                            f"{rail.rail_id!r}"
                        )

            for key in member_keys:
                connection = connection_by_key[key]
                if connection.cluster_id != cluster.cluster_id:
                    raise ValueError(
                        f"{connection.refdes}: connection cluster identity does not "
                        f"match {cluster.cluster_id!r}"
                    )
                if cluster.state == SharedPadClusterState.FLOATING:
                    expected_kind = DecapConnectionKind.FLOATING_DUMMY
                elif cluster.state == SharedPadClusterState.UNRESOLVED:
                    expected_kind = DecapConnectionKind.UNRESOLVED
                else:
                    expected_kind = (
                        DecapConnectionKind.SHARED_ANCHOR
                        if key in anchor_keys
                        else DecapConnectionKind.SHARED_DUMMY
                    )
                if connection.kind != expected_kind:
                    raise ValueError(
                        f"{connection.refdes}: expected connection kind "
                        f"{expected_kind.value}, found {connection.kind.value}"
                    )
            if cluster.state == SharedPadClusterState.ANCHORED:
                member_connections = (
                    connection_by_key[key] for key in member_keys
                )
                aggregate_power: dict[str, ScenarioViaLanding] = {}
                aggregate_ground: set[str] = set()
                for connection in member_connections:
                    for landing in connection.power_vias:
                        aggregate_power.setdefault(
                            landing.via_id.casefold(), landing
                        )
                    aggregate_ground.update(
                        item.via_id.casefold() for item in connection.ground_vias
                    )
                if not aggregate_power or not aggregate_ground:
                    raise ValueError(
                        f"anchored shared-pad cluster {cluster.cluster_id!r} "
                        "requires aggregate PWR and GND via evidence"
                    )
                eligibility_via_keys = {
                    item.casefold() for item in cluster.via_eligibility
                }
                if eligibility_via_keys != set(aggregate_power):
                    missing = sorted(set(aggregate_power) - eligibility_via_keys)
                    extra = sorted(eligibility_via_keys - set(aggregate_power))
                    raise ValueError(
                        f"shared-pad cluster {cluster.cluster_id!r} must persist "
                        "eligibility for every exact physical PWR Via "
                        f"(missing={missing}, extra={extra})"
                    )
                via_eligibility_by_key = {
                    via_id.casefold(): eligibility
                    for via_id, eligibility in cluster.via_eligibility.items()
                }
                for aggregate in cluster.eligibility.values():
                    rail_key = aggregate.rail_id.casefold()
                    if any(
                        not (
                            item := next(
                                (
                                    value
                                    for value in eligibility.values()
                                    if value.rail_id.casefold() == rail_key
                                ),
                                None,
                            )
                        )
                        or not item.allowed
                        for eligibility in via_eligibility_by_key.values()
                    ):
                        raise ValueError(
                            f"shared-pad cluster {cluster.cluster_id!r} aggregate "
                            f"eligibility for rail {aggregate.rail_id!r} is not "
                            "supported by every physical PWR Via"
                        )

                derivation = derive_shared_pad_current_components(
                    cluster,
                    {key: decap_by_key[key] for key in member_keys},
                    {key: connection_by_key[key] for key in member_keys},
                    analysis_version=analysis.version,
                )
                if derivation.shared_power_via_conflicts:
                    conflict = derivation.shared_power_via_conflicts[0]
                    raise ValueError(
                        f"shared-pad cluster {cluster.cluster_id!r} physical PWR "
                        f"Via {conflict.via_id!r} crosses current-NET components "
                        f"{conflict.component_member_refdes}"
                    )
                if derivation.shared_ground_via_conflicts:
                    conflict = derivation.shared_ground_via_conflicts[0]
                    raise ValueError(
                        f"shared-pad cluster {cluster.cluster_id!r} physical GND "
                        f"Via {conflict.via_id!r} crosses current GND components "
                        f"{conflict.component_member_refdes}"
                    )
                for component in derivation.components:
                    if not component.power_vias:
                        raise ValueError(
                            f"shared-pad cluster {cluster.cluster_id!r} has a "
                            "dummy-only current-NET island without a physical PWR "
                            f"Via anchor: {component.member_refdes}"
                        )
                    allowed = shared_pad_component_eligibility(
                        cluster, component
                    )
                    if component.current_rail_id.casefold() not in {
                        item.rail_id.casefold() for item in allowed.values()
                    }:
                        raise ValueError(
                            f"shared-pad cluster {cluster.cluster_id!r} current rail "
                            f"{component.current_rail_id!r} is not eligible at every "
                            "physical PWR Via serving component "
                            f"{component.member_refdes}"
                        )

        for key, connection in connection_by_key.items():
            if connection.cluster_id is None:
                if key in membership:
                    raise ValueError(
                        f"{connection.refdes}: cluster membership is missing from "
                        "its connection status"
                    )
                continue
            cluster = cluster_by_key.get(connection.cluster_id.casefold())
            if cluster is None or key not in {
                item.casefold() for item in cluster.member_refdes
            }:
                raise ValueError(
                    f"{connection.refdes}: connection references an unknown or "
                    "unrelated shared-pad cluster"
                )

        for key, decap in decap_by_key.items():
            if decap.pad_state != DecapPadState.ISOLATION_GAP:
                continue
            cluster = membership.get(key)
            if (
                cluster is None
                or cluster.state != SharedPadClusterState.ANCHORED
                or key
                not in {
                    item.casefold() for item in cluster.isolation_gap_refdes
                }
            ):
                raise ValueError(
                    f"{decap.refdes}: ISOLATION_GAP is not authorized by a "
                    "source-proven collinear TOP copper path"
                )

    @model_validator(mode="after")
    def consistent_indexes(self, info: ValidationInfo) -> "ScenarioSpec":
        memo = (
            info.context
            if isinstance(info.context, _ScenarioValidationMemo)
            else _ScenarioValidationMemo()
        )
        project = memo.project
        if project is None:
            project = ProjectSpec.model_validate(self.normalized_project)
            memo.project = project
        memo.bind(self, project)

        refdes_keys = [item.refdes.casefold() for item in self.decaps]
        if len(refdes_keys) != len(set(refdes_keys)):
            raise ValueError("scenario decap REFDES values must be unique")
        decap_by_key = {item.refdes.casefold(): item for item in self.decaps}
        memo.decap_by_key = decap_by_key

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

        spd_import = project.metadata.get("spd_import")
        geometry_records = (
            spd_import.get("plane_geometries")
            if isinstance(spd_import, dict)
            else None
        )
        if any(
            rail.mixed_reference_certificate is not None
            for rail in project.rails
        ) and not isinstance(geometry_records, list):
            raise ValueError(
                "mixed-reference certificates require the persisted SPD plane index"
            )
        for rail in project.rails:
            certificate = rail.mixed_reference_certificate
            if certificate is None:
                continue
            bindings = (
                (
                    rail.net,
                    rail.pwr_layer,
                    certificate.pwr_asset_sha256,
                    "PWR",
                ),
                (
                    certificate.gnd_net,
                    rail.gnd_layer,
                    certificate.gnd_asset_sha256,
                    "DGND",
                ),
            )
            for net, layer, digest, role in bindings:
                matches = [
                    item
                    for item in geometry_records or ()
                    if isinstance(item, dict)
                    and str(item.get("net", "")).casefold() == net.casefold()
                    and str(item.get("layer", "")).casefold() == layer.casefold()
                    and str(item.get("asset_sha256", "")) == digest
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"rail {rail.rail_id!r} certificate {role} plane asset "
                        "binding is missing or ambiguous"
                    )
                asset_name = str(matches[0].get("asset", ""))
                actual_digest = hash_by_key.get(asset_name.casefold())
                if actual_digest != digest:
                    raise ValueError(
                        f"rail {rail.rail_id!r} certificate {role} attachment "
                        f"{asset_name!r} hash disagrees with certified artwork"
                    )

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

        rail_specs_by_key = {
            item.rail_id.casefold(): item for item in project.rails
        }
        memo.rail_by_key = rail_specs_by_key
        rail_by_key = {
            key: item.rail_id for key, item in rail_specs_by_key.items()
        }
        memo.cap_model_by_key = {
            item.model_id.casefold(): item for item in project.cap_models
        }
        self._validate_connection_analysis(decap_by_key, project=project)
        witness_failures = mixed_reference_ground_witness_failures(
            self, require_current_coverage=False, _project=project
        )
        if witness_failures:
            rail_id, reason = next(iter(sorted(witness_failures.items())))
            raise ValueError(f"rail {rail_id!r} {reason}")
        connected_refdes = {
            item.casefold() for item in self.electrically_connected_refdes
        }
        capture_keys = [key.casefold() for key in self.baseline_captures]
        if len(capture_keys) != len(set(capture_keys)):
            raise ValueError("baseline capture rail keys must be unique")
        expected_capture_refdes_by_rail: dict[str, set[str]] = {}
        if self.baseline_captures:
            for decap in self.decaps:
                decap_key = decap.refdes.casefold()
                if decap.source_mounted and decap_key in connected_refdes:
                    expected_capture_refdes_by_rail.setdefault(
                        decap.source_rail_id.casefold(), set()
                    ).add(decap_key)
        source_state_fingerprint: str | None = None
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
            if source_state_fingerprint is None:
                source_state_fingerprint = self._source_state_fingerprint(memo)
            if capture.source_state_sha256 != source_state_fingerprint:
                raise ValueError("baseline capture physical source state has changed")
            expected = expected_capture_refdes_by_rail.get(rail_key, set())
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
            if (
                capture.evaluation_input_sha256
                != self._baseline_evaluation_input_fingerprint(
                    capture.rail_id,
                    capture.model_bindings,
                    memo=memo,
                )
            ):
                raise ValueError(
                    f"baseline solver inputs for {capture.rail_id!r} have changed"
                )

        capture_by_rail = {
            capture.rail_id.casefold(): capture
            for capture in self.baseline_captures.values()
        }
        baseline_cache_entries = tuple(
            metadata
            for metadata in self.evaluation_cache.values()
            if metadata.role == EvaluationRole.BASELINE
        )
        capture_fingerprint_by_rail: dict[str, str] = {}
        if baseline_cache_entries:
            capture_fingerprint_by_rail = {
                rail_key: capture.capture_fingerprint
                for rail_key, capture in capture_by_rail.items()
            }
        for metadata in baseline_cache_entries:
            capture = capture_by_rail.get(metadata.result_key.rail_id.casefold())
            if capture is None:
                raise ValueError(
                    f"baseline evaluation for {metadata.result_key.rail_id!r} "
                    "has no baseline capture"
                )
            if (
                metadata.baseline_capture_sha256
                != capture_fingerprint_by_rail[capture.rail_id.casefold()]
            ):
                raise ValueError(
                    f"baseline evaluation for {metadata.result_key.rail_id!r} "
                    "does not match its capture"
                )
        return self

    @property
    def base_project(self) -> ProjectSpec:
        return ProjectSpec.model_validate(self.normalized_project)

    @property
    def electrically_connected_refdes(self) -> tuple[str, ...]:
        """Return source-order decaps that can contribute to PI evaluation.

        Legacy scenarios without connection analysis retain their historical
        all-decaps behavior until the verified source SPD is reanalyzed.
        """

        if self.connection_analysis is None:
            return tuple(item.refdes for item in self.decaps)
        connected_kinds = {
            DecapConnectionKind.DIRECT,
            DecapConnectionKind.SHARED_ANCHOR,
            DecapConnectionKind.SHARED_DUMMY,
        }
        connected_keys = {
            item.refdes.casefold()
            for item in self.connection_analysis.connections.values()
            if item.kind in connected_kinds
        }
        return tuple(
            item.refdes
            for item in self.decaps
            if item.refdes.casefold() in connected_keys
        )

    def _owned_validation_memo(
        self, memo: _ScenarioValidationMemo | None
    ) -> _ScenarioValidationMemo | None:
        """Return scratch data only when it belongs to this exact instance."""

        return memo if memo is not None and memo.owner is self else None

    def _sorted_decaps_for_validation(
        self, memo: _ScenarioValidationMemo | None
    ) -> tuple[ScenarioDecap, ...]:
        memo = self._owned_validation_memo(memo)
        if memo is not None and memo.sorted_decaps is not None:
            return memo.sorted_decaps
        ordered = tuple(
            sorted(self.decaps, key=lambda entry: entry.refdes.casefold())
        )
        if memo is not None:
            memo.sorted_decaps = ordered
        return ordered

    def _connection_payload_for_validation(
        self, memo: _ScenarioValidationMemo | None
    ) -> dict[str, Any] | None:
        memo = self._owned_validation_memo(memo)
        if self.connection_analysis is None:
            return None
        if memo is not None and memo.connection_payload is not None:
            return memo.connection_payload
        payload = _connection_analysis_fingerprint_payload(self.connection_analysis)
        if memo is not None:
            memo.connection_payload = payload
        return payload

    def _design_payload(
        self, memo: _ScenarioValidationMemo | None = None
    ) -> dict[str, Any]:
        memo = self._owned_validation_memo(memo)
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
            for item in self._sorted_decaps_for_validation(memo)
        ]
        payload = {
            "schema_version": self.schema_version,
            "source": {
                "size_bytes": self.source.size_bytes,
                "sha256": self.source.sha256,
            },
            "normalized_project": _normalized_project_fingerprint_payload(
                self.normalized_project
            ),
            "decaps": decaps,
            "attachment_hashes": electrical_attachment_hashes,
        }
        connection_payload = self._connection_payload_for_validation(memo)
        if connection_payload is not None:
            payload["connection_analysis"] = connection_payload
        return payload

    def _design_fingerprint(
        self, memo: _ScenarioValidationMemo | None = None
    ) -> str:
        memo = self._owned_validation_memo(memo)
        if memo is not None and memo.design_fingerprint is not None:
            return memo.design_fingerprint
        digest = _hash_payload(self._design_payload(memo))
        if memo is not None:
            memo.design_fingerprint = digest
        return digest

    @property
    def design_fingerprint(self) -> str:
        """Hash of electrical design state, excluding UI/cache/revision/source path."""

        return self._design_fingerprint()

    @property
    def source_state_fingerprint(self) -> str:
        """Hash immutable placement/source assignment data, excluding tuning."""

        return self._source_state_fingerprint()

    def _source_state_fingerprint(
        self, memo: _ScenarioValidationMemo | None = None
    ) -> str:
        memo = self._owned_validation_memo(memo)
        if memo is not None and memo.source_state_fingerprint is not None:
            return memo.source_state_fingerprint

        source_decaps = []
        for item in self._sorted_decaps_for_validation(memo):
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
        payload: dict[str, Any] = {
            "source_sha256": self.source.sha256,
            "decaps": source_decaps,
        }
        connection_payload = self._connection_payload_for_validation(memo)
        if connection_payload is not None:
            payload["connection_analysis"] = connection_payload
        digest = _hash_payload(payload)
        if memo is not None:
            memo.source_state_fingerprint = digest
        return digest

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

        return self._baseline_evaluation_input_fingerprint(
            rail_id, model_bindings, memo=None
        )

    def _baseline_evaluation_input_fingerprint(
        self,
        rail_id: str,
        model_bindings: list[BaselineModelBinding]
        | tuple[BaselineModelBinding, ...],
        *,
        memo: _ScenarioValidationMemo | None,
    ) -> str:
        memo = self._owned_validation_memo(memo)
        project = (
            memo.project
            if memo is not None and memo.project is not None
            else self.base_project
        )
        if memo is not None and memo.rail_by_key is not None:
            rails = memo.rail_by_key
        else:
            rails = {item.rail_id.casefold(): item for item in project.rails}
            if memo is not None:
                memo.rail_by_key = rails
        rail = rails.get(rail_id.casefold())

        if rail is None:
            raise ValueError(f"unknown baseline rail {rail_id!r}")
        bindings = tuple(model_bindings)
        binding_models = {item.model_id.casefold() for item in bindings}
        if memo is not None and memo.cap_model_by_key is not None:
            models = memo.cap_model_by_key
        else:
            models = {
                item.model_id.casefold(): item for item in project.cap_models
            }
            if memo is not None:
                memo.cap_model_by_key = models
        missing_models = binding_models - set(models)
        if missing_models:
            raise ValueError(
                "baseline capture references unknown model(s): "
                + ", ".join(sorted(missing_models))
            )
        if memo is not None and memo.decap_by_key is not None:
            decaps = memo.decap_by_key
        else:
            decaps = {item.refdes.casefold(): item for item in self.decaps}
            if memo is not None:
                memo.decap_by_key = decaps
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

        if memo is not None and memo.baseline_project_payload is not None:
            baseline_project = memo.baseline_project_payload
        else:
            baseline_project = project.model_dump(mode="json")
            _preserve_legacy_stackup_row_shape(
                baseline_project, self.normalized_project
            )
            if (
                "shared_pad_clusters" not in self.normalized_project
                and not baseline_project.get("shared_pad_clusters")
            ):
                baseline_project.pop("shared_pad_clusters", None)
            baseline_project.pop("attachment_names", None)
            baseline_project.pop("metadata", None)
            if memo is not None:
                memo.baseline_project_payload = baseline_project
        if memo is not None and memo.cap_model_payload_by_key is not None:
            model_payloads = memo.cap_model_payload_by_key
        else:
            model_payloads = {
                key: model.model_dump(mode="json")
                for key, model in models.items()
            }
            if memo is not None:
                memo.cap_model_payload_by_key = model_payloads
        selected_project = {
            **baseline_project,
            "cap_models": [model_payloads[key] for key in sorted(binding_models)],
        }
        return _hash_payload(
            {
                "rail_id": rail.rail_id,
                "project": selected_project,
                "source_state_sha256": self._source_state_fingerprint(memo),
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
        connected_keys = {
            item.casefold() for item in self.electrically_connected_refdes
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
                    or decap.refdes.casefold() not in connected_keys
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
        connected_keys = {
            item.casefold() for item in self.electrically_connected_refdes
        }
        for decap in self.decaps:
            model_id = decap.source_model_id
            if (
                decap.source_mounted
                and decap.source_rail_id.casefold() == capture_rail_key
                and decap.refdes.casefold() in connected_keys
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
                        "pad_state": DecapPadState.NORMAL,
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
    "DecapConnectionKind",
    "DecapPadState",
    "EvaluationRole",
    "RailEligibility",
    "SCENARIO_APP_VERSION",
    "SCENARIO_SCHEMA_VERSION",
    "ScenarioDecap",
    "ScenarioDecapConnection",
    "ScenarioPad",
    "ScenarioPoint",
    "ScenarioResultKey",
    "ScenarioSide",
    "ScenarioSpec",
    "ScenarioViaPathEvidence",
    "ScenarioViaSegment",
    "ScenarioViaLanding",
    "mixed_reference_ground_landing_identity",
    "mixed_reference_ground_witness_failures",
    "SharedPadCluster",
    "SharedPadClusterState",
    "SharedPadComponentDerivation",
    "SharedPadConnectionAnalysis",
    "SharedPadCapacitorComponentMapping",
    "SharedPadCurrentComponent",
    "SharedPadGroundComponent",
    "SharedPadGroundViaConflict",
    "SharedPadPowerViaConflict",
    "SharedPadRailAliasConflictError",
    "SharedPadActiveShortError",
    "SourceIdentity",
    "derive_shared_pad_current_components",
    "scenario_design_fingerprint",
    "shared_pad_component_eligibility",
]
