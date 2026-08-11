"""Pure scenario-to-topology planning for finite-route Evaluation.

This module deliberately stops at a declarative plan.  It does not parse an
SPD, stamp an admittance matrix, or mutate the base layer-surface network.  A
caller must first convert its source and target route certificates into the
typed, hash-bound evidence sets below.  The resulting immutable plan then
states exactly which ideal pad/contact links remain, which finite retarget
routes replace source PWR roots, and which capacitor bodies should be stamped.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Literal
from urllib.parse import quote

from .scenario import (
    DecapConnectionKind,
    DecapPadState,
    ScenarioDecap,
    ScenarioDecapConnection,
    SharedPadConnectionAnalysis,
    SharedPadClusterState,
    derive_shared_pad_current_components,
)


SCENARIO_TOPOLOGY_PLAN_SCHEMA = "SCENARIO_TOPOLOGY_PLAN_V1"
Terminal = Literal["PWR", "GND"]


class ScenarioTopologyPlanError(ValueError):
    """Fail-closed topology-planning error with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _nonblank(value: str, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{label} must not be blank")
    return text


def _validate_sha256(value: str, *, label: str) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{label} must be a 64-character hexadecimal SHA-256")
    return text


def _canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _payload_sha256(payload: Any) -> str:
    return sha256(_canonical_json_bytes(payload)).hexdigest()


def connection_analysis_evidence_sha256(
    analysis: SharedPadConnectionAnalysis,
) -> str:
    """Hash the exact current connection evidence consumed by the planner."""

    return _payload_sha256(analysis.model_dump(mode="json"))


def _display_sort(value: str) -> tuple[str, str]:
    return value.casefold(), value


def _pair_sort(value: tuple[str, str]) -> tuple[str, str, str, str]:
    return value[0].casefold(), value[0], value[1].casefold(), value[1]


def _node_id(refdes: str, terminal: Terminal) -> str:
    return f"scenario-terminal:{quote(refdes, safe='')}:{terminal}"


def _stable_id(prefix: str, *parts: str) -> str:
    encoded = ":".join(quote(part, safe="") for part in parts)
    return f"{prefix}:{encoded}"


@dataclass(frozen=True, slots=True)
class SourceViaContactEvidence:
    """One exact source terminal contact and its suppressible base cut."""

    via_id: str
    terminal: Terminal
    source_refdes: str
    exposed_node_id: str
    contact_owner_id: str
    cut_link_id: str
    cut_owner_id: str
    contact_evidence_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "via_id",
            "source_refdes",
            "exposed_node_id",
            "contact_owner_id",
            "cut_link_id",
            "cut_owner_id",
        ):
            _nonblank(getattr(self, name), label=name)
        if self.terminal not in {"PWR", "GND"}:
            raise ValueError("terminal must be PWR or GND")
        _validate_sha256(
            self.contact_evidence_sha256, label="contact evidence SHA-256"
        )


def _source_manifest_payload(
    *,
    source_sha256: str,
    connection_evidence_sha256: str,
    finite_route_certificate_sha256: str,
    contacts: Sequence[SourceViaContactEvidence],
) -> dict[str, Any]:
    return {
        "source_sha256": source_sha256,
        "connection_evidence_sha256": connection_evidence_sha256,
        "finite_route_certificate_sha256": finite_route_certificate_sha256,
        "contacts": [asdict(item) for item in contacts],
    }


@dataclass(frozen=True, slots=True)
class SourceContactEvidenceSet:
    """Complete physical-Via contact map bound to one route certificate."""

    source_sha256: str
    connection_evidence_sha256: str
    finite_route_certificate_sha256: str
    contacts: tuple[SourceViaContactEvidence, ...]
    manifest_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_sha256: str,
        connection_evidence_sha256: str,
        finite_route_certificate_sha256: str,
        contacts: Iterable[SourceViaContactEvidence],
    ) -> "SourceContactEvidenceSet":
        ordered = tuple(
            sorted(
                contacts,
                key=lambda item: (
                    item.via_id.casefold(),
                    item.via_id,
                    item.terminal,
                ),
            )
        )
        source_hash = _validate_sha256(source_sha256, label="source SHA-256")
        connection_hash = _validate_sha256(
            connection_evidence_sha256,
            label="connection evidence SHA-256",
        )
        route_hash = _validate_sha256(
            finite_route_certificate_sha256,
            label="finite-route certificate SHA-256",
        )
        manifest = _payload_sha256(
            _source_manifest_payload(
                source_sha256=source_hash,
                connection_evidence_sha256=connection_hash,
                finite_route_certificate_sha256=route_hash,
                contacts=ordered,
            )
        )
        return cls(source_hash, connection_hash, route_hash, ordered, manifest)

    def __post_init__(self) -> None:
        source_hash = _validate_sha256(self.source_sha256, label="source SHA-256")
        connection_hash = _validate_sha256(
            self.connection_evidence_sha256,
            label="connection evidence SHA-256",
        )
        route_hash = _validate_sha256(
            self.finite_route_certificate_sha256,
            label="finite-route certificate SHA-256",
        )
        manifest = _validate_sha256(self.manifest_sha256, label="manifest SHA-256")
        ordered = tuple(
            sorted(
                self.contacts,
                key=lambda item: (
                    item.via_id.casefold(),
                    item.via_id,
                    item.terminal,
                ),
            )
        )
        if self.contacts != ordered:
            raise ValueError("source-contact manifest entries are not canonical")
        expected = _payload_sha256(
            _source_manifest_payload(
                source_sha256=source_hash,
                connection_evidence_sha256=connection_hash,
                finite_route_certificate_sha256=route_hash,
                contacts=self.contacts,
            )
        )
        if manifest != expected:
            raise ValueError("source-contact manifest SHA-256 does not match its content")


@dataclass(frozen=True, slots=True)
class TargetRouteDecision:
    """Verified eligibility decision for one physical PWR Via and rail."""

    via_id: str
    rail_id: str
    eligible: bool
    target_node_id: str | None = None
    route_id: str | None = None
    route_owner_id: str | None = None
    target_net: str | None = None
    target_layer: str | None = None
    via_template_id: str | None = None
    resistance_ohm: float | None = None
    inductance_h: float | None = None
    route_evidence_sha256: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _nonblank(self.via_id, label="via_id")
        _nonblank(self.rail_id, label="rail_id")
        route_values = (
            self.target_node_id,
            self.route_id,
            self.route_owner_id,
            self.target_net,
            self.target_layer,
            self.via_template_id,
            self.resistance_ohm,
            self.inductance_h,
            self.route_evidence_sha256,
        )
        if not self.eligible:
            if not self.reason or not self.reason.strip():
                raise ValueError("an ineligible target route requires a reason")
            if any(value is not None for value in route_values):
                raise ValueError("an ineligible target route cannot contain route data")
            return
        if self.reason is not None:
            raise ValueError("an eligible target route cannot contain a failure reason")
        for name in (
            "target_node_id",
            "route_id",
            "route_owner_id",
            "target_net",
            "target_layer",
            "via_template_id",
        ):
            value = getattr(self, name)
            if value is None:
                raise ValueError(f"an eligible target route requires {name}")
            _nonblank(value, label=name)
        if self.resistance_ohm is None or self.inductance_h is None:
            raise ValueError("an eligible target route requires finite R and L")
        if (
            not isfinite(self.resistance_ohm)
            or not isfinite(self.inductance_h)
            or self.resistance_ohm < 0.0
            or self.inductance_h < 0.0
            or (self.resistance_ohm == 0.0 and self.inductance_h == 0.0)
        ):
            raise ValueError("target route R/L must be finite, passive, and nonzero")
        if self.route_evidence_sha256 is None:
            raise ValueError("an eligible target route requires evidence SHA-256")
        _validate_sha256(
            self.route_evidence_sha256, label="target route evidence SHA-256"
        )


def _target_manifest_payload(
    *,
    source_sha256: str,
    connection_evidence_sha256: str,
    finite_route_certificate_sha256: str,
    decisions: Sequence[TargetRouteDecision],
) -> dict[str, Any]:
    return {
        "source_sha256": source_sha256,
        "connection_evidence_sha256": connection_evidence_sha256,
        "finite_route_certificate_sha256": finite_route_certificate_sha256,
        "decisions": [asdict(item) for item in decisions],
    }


@dataclass(frozen=True, slots=True)
class RetargetRouteEvidenceSet:
    """Explicit eligible/ineligible target decisions for candidate Via/rails."""

    source_sha256: str
    connection_evidence_sha256: str
    finite_route_certificate_sha256: str
    decisions: tuple[TargetRouteDecision, ...]
    manifest_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_sha256: str,
        connection_evidence_sha256: str,
        finite_route_certificate_sha256: str,
        decisions: Iterable[TargetRouteDecision],
    ) -> "RetargetRouteEvidenceSet":
        ordered = tuple(
            sorted(
                decisions,
                key=lambda item: (
                    item.via_id.casefold(),
                    item.via_id,
                    item.rail_id.casefold(),
                    item.rail_id,
                ),
            )
        )
        source_hash = _validate_sha256(source_sha256, label="source SHA-256")
        connection_hash = _validate_sha256(
            connection_evidence_sha256,
            label="connection evidence SHA-256",
        )
        route_hash = _validate_sha256(
            finite_route_certificate_sha256,
            label="finite-route certificate SHA-256",
        )
        manifest = _payload_sha256(
            _target_manifest_payload(
                source_sha256=source_hash,
                connection_evidence_sha256=connection_hash,
                finite_route_certificate_sha256=route_hash,
                decisions=ordered,
            )
        )
        return cls(source_hash, connection_hash, route_hash, ordered, manifest)

    def __post_init__(self) -> None:
        source_hash = _validate_sha256(self.source_sha256, label="source SHA-256")
        connection_hash = _validate_sha256(
            self.connection_evidence_sha256,
            label="connection evidence SHA-256",
        )
        route_hash = _validate_sha256(
            self.finite_route_certificate_sha256,
            label="finite-route certificate SHA-256",
        )
        manifest = _validate_sha256(self.manifest_sha256, label="manifest SHA-256")
        ordered = tuple(
            sorted(
                self.decisions,
                key=lambda item: (
                    item.via_id.casefold(),
                    item.via_id,
                    item.rail_id.casefold(),
                    item.rail_id,
                ),
            )
        )
        if self.decisions != ordered:
            raise ValueError("retarget-route manifest entries are not canonical")
        expected = _payload_sha256(
            _target_manifest_payload(
                source_sha256=source_hash,
                connection_evidence_sha256=connection_hash,
                finite_route_certificate_sha256=route_hash,
                decisions=self.decisions,
            )
        )
        if manifest != expected:
            raise ValueError("retarget-route manifest SHA-256 does not match its content")


class TopologyLinkKind(StrEnum):
    SHARED_PWR_PAD = "SHARED_PWR_PAD"
    SHARED_GND_PAD = "SHARED_GND_PAD"
    SOURCE_PWR_CONTACT = "SOURCE_PWR_CONTACT"
    SOURCE_GND_CONTACT = "SOURCE_GND_CONTACT"


class SourceContactDisposition(StrEnum):
    ACTIVE_SOURCE_CONTACT = "ACTIVE_SOURCE_CONTACT"
    MOVED_CONTACT_REMOVED_BASE_RETAINED = (
        "MOVED_CONTACT_REMOVED_BASE_RETAINED"
    )
    SUPPRESSED_FOR_RETARGET = "SUPPRESSED_FOR_RETARGET"
    CONTACT_REMOVED_BY_GAP = "CONTACT_REMOVED_BY_GAP"


@dataclass(frozen=True, slots=True)
class ScenarioTerminalNodes:
    refdes: str
    power_node_id: str
    ground_node_id: str


@dataclass(frozen=True, slots=True)
class ScenarioTopologyLink:
    link_id: str
    kind: TopologyLinkKind
    first_node_id: str
    second_node_id: str
    owner_ids: tuple[str, ...]
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ScenarioRetargetRoute:
    route_id: str
    via_id: str
    rail_id: str
    first_node_id: str
    target_node_id: str
    route_owner_id: str
    target_net: str
    target_layer: str
    via_template_id: str
    resistance_ohm: float
    inductance_h: float
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ScenarioCapBodyRequest:
    refdes: str
    model_id: str
    rail_id: str
    positive_node_id: str
    negative_node_id: str


@dataclass(frozen=True, slots=True)
class SourceContactOwnerPartition:
    via_id: str
    terminal: Terminal
    contact_owner_id: str
    cut_link_id: str
    cut_owner_id: str
    disposition: SourceContactDisposition


@dataclass(frozen=True, slots=True)
class ScenarioTopologyPlan:
    schema_version: str
    source_sha256: str
    connection_evidence_sha256: str
    source_contact_manifest_sha256: str
    retarget_route_manifest_sha256: str
    finite_route_certificate_sha256: str
    terminal_nodes: tuple[ScenarioTerminalNodes, ...]
    active_topology_links: tuple[ScenarioTopologyLink, ...]
    active_retarget_routes: tuple[ScenarioRetargetRoute, ...]
    cap_body_requests: tuple[ScenarioCapBodyRequest, ...]
    suppressed_base_cut_ids: tuple[str, ...]
    source_owner_partition: tuple[SourceContactOwnerPartition, ...]
    plan_sha256: str


@dataclass(frozen=True, slots=True)
class _PowerComponent:
    identity: str
    member_refdes: tuple[str, ...]
    current_rail_id: str
    current_net: str
    via_ids: tuple[str, ...]
    moved: bool


@dataclass(frozen=True, slots=True)
class _GroundComponent:
    identity: str
    member_refdes: tuple[str, ...]
    via_ids: tuple[str, ...]


def _casefold_objects(
    values: Mapping[str, Any], *, label: str
) -> dict[str, tuple[str, Any]]:
    result: dict[str, tuple[str, Any]] = {}
    for raw_key, value in values.items():
        display = _nonblank(str(raw_key), label=f"{label} key")
        key = display.casefold()
        if key in result:
            raise ScenarioTopologyPlanError(
                "IDENTITY_AMBIGUOUS",
                f"{label} contains duplicate case-insensitive key {display!r}",
            )
        result[key] = (display, value)
    return result


def _unique_index(
    values: Iterable[Any],
    *,
    key_fields: tuple[str, ...],
    label: str,
) -> dict[tuple[str, ...], Any]:
    result: dict[tuple[str, ...], Any] = {}
    for value in values:
        key = tuple(str(getattr(value, field)).casefold() for field in key_fields)
        if key in result:
            raise ScenarioTopologyPlanError(
                "EVIDENCE_CONFLICT",
                f"{label} repeats key {tuple(getattr(value, field) for field in key_fields)!r}",
            )
        result[key] = value
    return result


def _plan_payload(plan: ScenarioTopologyPlan) -> dict[str, Any]:
    payload = asdict(plan)
    payload.pop("plan_sha256", None)
    return payload


def compile_scenario_topology_plan(
    *,
    decap_by_refdes: Mapping[str, ScenarioDecap],
    connection_analysis: SharedPadConnectionAnalysis,
    source_contacts: SourceContactEvidenceSet,
    retarget_routes: RetargetRouteEvidenceSet,
) -> ScenarioTopologyPlan:
    """Compile one deterministic, fail-closed scenario topology plan."""

    connection_hash = connection_analysis_evidence_sha256(connection_analysis)
    binding_values = (
        (source_contacts.source_sha256, "source-contact source SHA-256"),
        (retarget_routes.source_sha256, "retarget source SHA-256"),
    )
    for value, label in binding_values:
        if value != connection_analysis.source_sha256:
            raise ScenarioTopologyPlanError(
                "EVIDENCE_BINDING_MISMATCH",
                f"{label} does not match current connection evidence",
            )
    if (
        source_contacts.connection_evidence_sha256 != connection_hash
        or retarget_routes.connection_evidence_sha256 != connection_hash
    ):
        raise ScenarioTopologyPlanError(
            "EVIDENCE_BINDING_MISMATCH",
            "route evidence is not bound to the exact current connection analysis",
        )
    if (
        source_contacts.finite_route_certificate_sha256
        != retarget_routes.finite_route_certificate_sha256
    ):
        raise ScenarioTopologyPlanError(
            "EVIDENCE_BINDING_MISMATCH",
            "source and target evidence name different finite-route certificates",
        )

    decaps = _casefold_objects(decap_by_refdes, label="decap")
    connections = _casefold_objects(
        connection_analysis.connections, label="connection"
    )
    if set(decaps) != set(connections):
        missing_decaps = sorted(set(connections) - set(decaps))
        missing_connections = sorted(set(decaps) - set(connections))
        raise ScenarioTopologyPlanError(
            "CONNECTION_INVENTORY_INCOMPLETE",
            "decap/connection inventory differs "
            f"(missing decaps={missing_decaps}, missing connections={missing_connections})",
        )
    for key, (_mapping_key, decap) in decaps.items():
        if decap.refdes.casefold() != key:
            raise ScenarioTopologyPlanError(
                "IDENTITY_AMBIGUOUS", "decap mapping key does not match REFDES"
            )
    for key, (_mapping_key, connection) in connections.items():
        if connection.refdes.casefold() != key:
            raise ScenarioTopologyPlanError(
                "IDENTITY_AMBIGUOUS", "connection mapping key does not match REFDES"
            )

    # The legacy source classifier used ``UNRESOLVED`` when a shared top-pad
    # cluster's rail plane was not geometrically present below *every* PWR Via
    # landing.  The v4 topology does not use that plane-under-every-Via
    # approximation: it retains each exact source contact and its finite raw
    # Via route.  Such a cluster is therefore modelable in its unchanged source
    # state, but only after this narrow gate.  Every downstream exact-contact,
    # owner-partition, component-root, and duplicate-Via check still runs.
    source_proven_unresolved_cluster_ids: set[str] = set()
    source_proven_unresolved_members: set[str] = set()
    for cluster in connection_analysis.clusters:
        if cluster.state != SharedPadClusterState.UNRESOLVED:
            continue
        cluster_key = cluster.cluster_id.casefold()
        member_keys = tuple(item.casefold() for item in cluster.member_refdes)
        if (
            not member_keys
            or len(set(member_keys)) != len(member_keys)
            or not cluster.source_graph_is_connected(cluster.power_edges)
            or not cluster.source_graph_is_connected(cluster.ground_edges)
            or any(key not in decaps or key not in connections for key in member_keys)
        ):
            continue
        unchanged_and_bound = True
        for key in member_keys:
            decap: ScenarioDecap = decaps[key][1]
            connection: ScenarioDecapConnection = connections[key][1]
            if (
                connection.cluster_id is None
                or connection.cluster_id.casefold() != cluster_key
                or decap.current_rail_id.casefold()
                != decap.source_rail_id.casefold()
                or decap.current_net.casefold() != decap.source_net.casefold()
            ):
                unchanged_and_bound = False
                break
        if not unchanged_and_bound:
            continue
        source_proven_unresolved_cluster_ids.add(cluster_key)
        source_proven_unresolved_members.update(member_keys)

    terminal_nodes = tuple(
        ScenarioTerminalNodes(
            refdes=decaps[key][1].refdes,
            power_node_id=_node_id(decaps[key][1].refdes, "PWR"),
            ground_node_id=_node_id(decaps[key][1].refdes, "GND"),
        )
        for key in sorted(decaps)
    )
    nodes_by_refdes = {
        item.refdes.casefold(): item for item in terminal_nodes
    }

    # Verify that the typed source map is a complete one-record-per-physical-Via
    # partition of the current connection evidence.
    claims: dict[str, tuple[Terminal, set[str]]] = {}
    for key in sorted(connections):
        connection: ScenarioDecapConnection = connections[key][1]
        source_proven_unresolved = (
            connection.kind == DecapConnectionKind.UNRESOLVED
            and key in source_proven_unresolved_members
        )
        if not source_proven_unresolved and connection.kind in {
            DecapConnectionKind.UNRESOLVED,
            DecapConnectionKind.OUT_OF_SCOPE,
            DecapConnectionKind.FLOATING_DUMMY,
        }:
            raise ScenarioTopologyPlanError(
                "CONNECTION_UNMODELABLE",
                f"{connection.refdes!r} has {connection.kind.value} connection evidence",
            )
        for terminal, landings in (
            ("PWR", connection.power_vias),
            ("GND", connection.ground_vias),
        ):
            for landing in landings:
                via_key = landing.via_id.casefold()
                previous = claims.get(via_key)
                if previous is None:
                    claims[via_key] = (terminal, {connection.refdes.casefold()})
                else:
                    previous_terminal, owners = previous
                    if previous_terminal != terminal:
                        raise ScenarioTopologyPlanError(
                            "SOURCE_VIA_TERMINAL_CONFLICT",
                            f"physical Via {landing.via_id!r} is both PWR and GND",
                        )
                    owners.add(connection.refdes.casefold())

    contact_index = _unique_index(
        source_contacts.contacts,
        key_fields=("via_id",),
        label="source-contact evidence",
    )
    if {key[0] for key in contact_index} != set(claims):
        missing = sorted(set(claims) - {key[0] for key in contact_index})
        extra = sorted({key[0] for key in contact_index} - set(claims))
        raise ScenarioTopologyPlanError(
            "SOURCE_CONTACT_INVENTORY_INCOMPLETE",
            f"source contact map differs from physical Via evidence (missing={missing}, extra={extra})",
        )
    contact_by_via = {key[0]: item for key, item in contact_index.items()}
    owner_ids: set[str] = set()
    cut_ids: set[str] = set()
    cut_owner_ids: set[str] = set()
    for via_key, (terminal, claim_refdes) in claims.items():
        contact = contact_by_via[via_key]
        if contact.terminal != terminal or contact.source_refdes.casefold() not in claim_refdes:
            raise ScenarioTopologyPlanError(
                "SOURCE_CONTACT_EVIDENCE_CONFLICT",
                f"source contact for {contact.via_id!r} conflicts with connection ownership",
            )
        owner_key = contact.contact_owner_id.casefold()
        cut_key = contact.cut_link_id.casefold()
        cut_owner_key = contact.cut_owner_id.casefold()
        if (
            owner_key in owner_ids
            or cut_key in cut_ids
            or cut_owner_key in cut_owner_ids
            or cut_owner_key != f"via:{contact.via_id}".casefold()
        ):
            raise ScenarioTopologyPlanError(
                "SOURCE_CONTACT_EVIDENCE_CONFLICT",
                "source contact, cut-link and canonical raw-Via owner IDs must each be exact and unique",
            )
        owner_ids.add(owner_key)
        cut_ids.add(cut_key)
        cut_owner_ids.add(cut_owner_key)

    decision_index = _unique_index(
        retarget_routes.decisions,
        key_fields=("via_id", "rail_id"),
        label="retarget decision",
    )

    active_links: list[ScenarioTopologyLink] = []
    power_components: list[_PowerComponent] = []
    ground_components: list[_GroundComponent] = []
    power_component_by_refdes: dict[str, _PowerComponent] = {}
    ground_component_by_refdes: dict[str, _GroundComponent] = {}
    clustered_refdes: set[str] = set()

    cluster_ids: set[str] = set()
    for cluster in connection_analysis.clusters:
        cluster_key = cluster.cluster_id.casefold()
        if cluster_key in cluster_ids:
            raise ScenarioTopologyPlanError(
                "CLUSTER_IDENTITY_CONFLICT", f"duplicate cluster {cluster.cluster_id!r}"
            )
        cluster_ids.add(cluster_key)
        if (
            cluster.state != SharedPadClusterState.ANCHORED
            and cluster_key not in source_proven_unresolved_cluster_ids
        ):
            raise ScenarioTopologyPlanError(
                "CLUSTER_UNMODELABLE",
                f"shared cluster {cluster.cluster_id!r} is {cluster.state.value}",
            )
        member_decaps: dict[str, ScenarioDecap] = {}
        member_connections: dict[str, ScenarioDecapConnection] = {}
        for refdes in cluster.member_refdes:
            key = refdes.casefold()
            if key in clustered_refdes or key not in decaps or key not in connections:
                raise ScenarioTopologyPlanError(
                    "CLUSTER_MEMBERSHIP_CONFLICT",
                    f"cluster member {refdes!r} is missing or repeated",
                )
            clustered_refdes.add(key)
            member_decaps[refdes] = decaps[key][1]
            member_connections[refdes] = connections[key][1]
            connection = connections[key][1]
            if connection.cluster_id is None or connection.cluster_id.casefold() != cluster_key:
                raise ScenarioTopologyPlanError(
                    "CLUSTER_MEMBERSHIP_CONFLICT",
                    f"connection for {refdes!r} does not bind cluster {cluster.cluster_id!r}",
                )
        try:
            derivation = derive_shared_pad_current_components(
                cluster,
                member_decaps,
                member_connections,
                analysis_version=connection_analysis.version,
            )
        except ValueError as exc:
            raise ScenarioTopologyPlanError(
                "CURRENT_TOPOLOGY_CONFLICT",
                f"shared cluster {cluster.cluster_id!r} cannot be derived: {exc}",
            ) from exc
        if derivation.shared_power_via_conflicts or derivation.shared_ground_via_conflicts:
            raise ScenarioTopologyPlanError(
                "PHYSICAL_VIA_OWNERSHIP_CONFLICT",
                f"shared cluster {cluster.cluster_id!r} assigns one Via to multiple components",
            )

        local_power: list[_PowerComponent] = []
        for index, component in enumerate(derivation.components):
            moved = any(
                member_decaps[refdes].current_rail_id.casefold()
                != member_decaps[refdes].source_rail_id.casefold()
                or member_decaps[refdes].current_net.casefold()
                != member_decaps[refdes].source_net.casefold()
                for refdes in component.member_refdes
            )
            planned = _PowerComponent(
                identity=f"shared:{cluster.cluster_id}:pwr:{index}",
                member_refdes=component.member_refdes,
                current_rail_id=component.current_rail_id,
                current_net=component.current_net,
                via_ids=tuple(landing.via_id for landing in component.power_vias),
                moved=moved,
            )
            local_power.append(planned)
            power_components.append(planned)
            for refdes in planned.member_refdes:
                power_component_by_refdes[refdes.casefold()] = planned
        local_ground: list[_GroundComponent] = []
        for index, component in enumerate(derivation.ground_components):
            planned = _GroundComponent(
                identity=f"shared:{cluster.cluster_id}:gnd:{index}",
                member_refdes=component.member_refdes,
                via_ids=tuple(landing.via_id for landing in component.ground_vias),
            )
            local_ground.append(planned)
            ground_components.append(planned)
            for refdes in planned.member_refdes:
                ground_component_by_refdes[refdes.casefold()] = planned

        # These are only the ideal top-pad conductors that survive the current
        # NORMAL/gap state.  No transitive shortcut is introduced across a gap.
        for terminal, edges, kind in (
            ("PWR", cluster.power_edges, TopologyLinkKind.SHARED_PWR_PAD),
            ("GND", cluster.ground_edges, TopologyLinkKind.SHARED_GND_PAD),
        ):
            for left, right in sorted(edges, key=_pair_sort):
                left_decap = decaps[left.casefold()][1]
                right_decap = decaps[right.casefold()][1]
                if (
                    left_decap.pad_state == DecapPadState.ISOLATION_GAP
                    or right_decap.pad_state == DecapPadState.ISOLATION_GAP
                ):
                    continue
                left_node = nodes_by_refdes[left.casefold()]
                right_node = nodes_by_refdes[right.casefold()]
                first = left_node.power_node_id if terminal == "PWR" else left_node.ground_node_id
                second = right_node.power_node_id if terminal == "PWR" else right_node.ground_node_id
                active_links.append(
                    ScenarioTopologyLink(
                        link_id=_stable_id(
                            "scenario-shared-pad",
                            cluster.cluster_id,
                            terminal,
                            left,
                            right,
                        ),
                        kind=kind,
                        first_node_id=first,
                        second_node_id=second,
                        owner_ids=(
                            _stable_id(
                                "shared-pad-owner",
                                cluster.cluster_id,
                                terminal,
                                left,
                                right,
                            ),
                        ),
                        evidence_sha256=connection_hash,
                    )
                )

        for mapping in derivation.capacitor_component_mappings:
            power_component_by_refdes[mapping.refdes.casefold()] = local_power[
                mapping.power_component_index
            ]
            ground_component_by_refdes[mapping.refdes.casefold()] = local_ground[
                mapping.ground_component_index
            ]

    # Direct footprints are one-member PWR/GND components.
    for key in sorted(decaps):
        if key in clustered_refdes:
            continue
        decap: ScenarioDecap = decaps[key][1]
        connection: ScenarioDecapConnection = connections[key][1]
        if connection.kind != DecapConnectionKind.DIRECT:
            raise ScenarioTopologyPlanError(
                "CONNECTION_UNMODELABLE",
                f"unclustered {decap.refdes!r} is {connection.kind.value}, not DIRECT",
            )
        if decap.pad_state == DecapPadState.ISOLATION_GAP:
            continue
        power = _PowerComponent(
            identity=f"direct:{decap.refdes}:pwr",
            member_refdes=(decap.refdes,),
            current_rail_id=decap.current_rail_id,
            current_net=decap.current_net,
            via_ids=tuple(landing.via_id for landing in connection.power_vias),
            moved=(
                decap.current_rail_id.casefold() != decap.source_rail_id.casefold()
                or decap.current_net.casefold() != decap.source_net.casefold()
            ),
        )
        ground = _GroundComponent(
            identity=f"direct:{decap.refdes}:gnd",
            member_refdes=(decap.refdes,),
            via_ids=tuple(landing.via_id for landing in connection.ground_vias),
        )
        power_components.append(power)
        ground_components.append(ground)
        power_component_by_refdes[key] = power
        ground_component_by_refdes[key] = ground

    power_component_by_via: dict[str, _PowerComponent] = {}
    for component in power_components:
        for via_id in component.via_ids:
            key = via_id.casefold()
            if key in power_component_by_via and power_component_by_via[key] != component:
                raise ScenarioTopologyPlanError(
                    "PHYSICAL_VIA_OWNERSHIP_CONFLICT",
                    f"PWR Via {via_id!r} belongs to multiple current components",
                )
            power_component_by_via[key] = component
    ground_component_by_via: dict[str, _GroundComponent] = {}
    for component in ground_components:
        for via_id in component.via_ids:
            key = via_id.casefold()
            if key in ground_component_by_via and ground_component_by_via[key] != component:
                raise ScenarioTopologyPlanError(
                    "PHYSICAL_VIA_OWNERSHIP_CONFLICT",
                    f"GND Via {via_id!r} belongs to multiple current components",
                )
            ground_component_by_via[key] = component

    # Any populated NORMAL body must remain anchored on both independently
    # derived terminal graphs.  DNP cells keep copper topology but need no body.
    cap_requests: list[ScenarioCapBodyRequest] = []
    for key in sorted(decaps):
        decap: ScenarioDecap = decaps[key][1]
        if decap.pad_state == DecapPadState.ISOLATION_GAP or not decap.enabled:
            continue
        power = power_component_by_refdes.get(key)
        ground = ground_component_by_refdes.get(key)
        if power is None or ground is None or not power.via_ids or not ground.via_ids:
            raise ScenarioTopologyPlanError(
                "POPULATED_TERMINAL_UNANCHORED",
                f"enabled decap {decap.refdes!r} lacks a PWR or GND physical root",
            )
        if decap.model_id is None or not decap.model_id.strip():
            raise ScenarioTopologyPlanError(
                "CAP_MODEL_MISSING",
                f"enabled decap {decap.refdes!r} has no capacitor model",
            )
        nodes = nodes_by_refdes[key]
        cap_requests.append(
            ScenarioCapBodyRequest(
                refdes=decap.refdes,
                model_id=decap.model_id,
                rail_id=decap.current_rail_id,
                positive_node_id=nodes.power_node_id,
                negative_node_id=nodes.ground_node_id,
            )
        )

    dispositions: dict[str, SourceContactDisposition] = {}
    for via_key, contact in contact_by_via.items():
        source_decap = decaps[contact.source_refdes.casefold()][1]
        component: _PowerComponent | _GroundComponent | None
        component = (
            power_component_by_via.get(via_key)
            if contact.terminal == "PWR"
            else ground_component_by_via.get(via_key)
        )
        if source_decap.pad_state == DecapPadState.ISOLATION_GAP:
            if component is not None:
                raise ScenarioTopologyPlanError(
                    "SOURCE_CONTACT_OWNERSHIP_CONFLICT",
                    f"gap-owned Via {contact.via_id!r} remains in an active component",
                )
            disposition = SourceContactDisposition.CONTACT_REMOVED_BY_GAP
        elif component is None:
            raise ScenarioTopologyPlanError(
                "SOURCE_CONTACT_OWNERSHIP_CONFLICT",
                f"normal source contact {contact.via_id!r} has no current component",
            )
        elif contact.source_refdes.casefold() not in {
            refdes.casefold() for refdes in component.member_refdes
        }:
            raise ScenarioTopologyPlanError(
                "SOURCE_CONTACT_OWNERSHIP_CONFLICT",
                f"source contact {contact.via_id!r} is owned outside its component",
            )
        elif isinstance(component, _PowerComponent) and component.moved:
            decision = decision_index.get(
                (via_key, component.current_rail_id.casefold())
            )
            if decision is None:
                raise ScenarioTopologyPlanError(
                    "TARGET_ROUTE_DECISION_MISSING",
                    f"PWR Via {contact.via_id!r} has no verified decision "
                    f"for rail {component.current_rail_id!r}",
                )
            # Moving a pad component always removes its ideal source-terminal
            # contact.  The immutable base first-Via edge is suppressed only
            # when this exact root is eligible and its canonical raw-Via owner
            # will be transferred to a replacement route.  An ineligible root
            # remains as a passive, isolated base stub and keeps its owner.
            disposition = (
                SourceContactDisposition.SUPPRESSED_FOR_RETARGET
                if decision.eligible
                else SourceContactDisposition.MOVED_CONTACT_REMOVED_BASE_RETAINED
            )
        else:
            disposition = SourceContactDisposition.ACTIVE_SOURCE_CONTACT
        dispositions[via_key] = disposition
        if disposition == SourceContactDisposition.ACTIVE_SOURCE_CONTACT:
            nodes = nodes_by_refdes[contact.source_refdes.casefold()]
            terminal_node = (
                nodes.power_node_id
                if contact.terminal == "PWR"
                else nodes.ground_node_id
            )
            kind = (
                TopologyLinkKind.SOURCE_PWR_CONTACT
                if contact.terminal == "PWR"
                else TopologyLinkKind.SOURCE_GND_CONTACT
            )
            active_links.append(
                ScenarioTopologyLink(
                    link_id=_stable_id(
                        "scenario-source-contact", contact.terminal, contact.via_id
                    ),
                    kind=kind,
                    first_node_id=terminal_node,
                    second_node_id=contact.exposed_node_id,
                    owner_ids=(contact.contact_owner_id,),
                    evidence_sha256=contact.contact_evidence_sha256,
                )
            )

    active_retargets: list[ScenarioRetargetRoute] = []
    active_retarget_owner_ids: set[str] = set()
    for component in sorted(power_components, key=lambda item: item.identity.casefold()):
        if not component.moved:
            continue
        if not component.via_ids:
            raise ScenarioTopologyPlanError(
                "RETARGET_ROOT_MISSING",
                f"moved component {component.identity!r} has no physical PWR root",
            )
        eligible: list[tuple[SourceViaContactEvidence, TargetRouteDecision]] = []
        for via_id in component.via_ids:
            via_key = via_id.casefold()
            contact = contact_by_via.get(via_key)
            if contact is None:
                raise ScenarioTopologyPlanError(
                    "SOURCE_CONTACT_EVIDENCE_CONFLICT",
                    f"moved PWR Via {via_id!r} has no exact source contact",
                )
            decision = decision_index.get(
                (via_key, component.current_rail_id.casefold())
            )
            if decision is None:
                raise ScenarioTopologyPlanError(
                    "TARGET_ROUTE_DECISION_MISSING",
                    f"PWR Via {via_id!r} has no verified decision for rail {component.current_rail_id!r}",
                )
            expected_disposition = (
                SourceContactDisposition.SUPPRESSED_FOR_RETARGET
                if decision.eligible
                else SourceContactDisposition.MOVED_CONTACT_REMOVED_BASE_RETAINED
            )
            if dispositions.get(via_key) != expected_disposition:
                raise ScenarioTopologyPlanError(
                    "SOURCE_CONTACT_EVIDENCE_CONFLICT",
                    f"moved PWR Via {via_id!r} has an inconsistent base-owner disposition",
                )
            if decision.eligible:
                eligible.append((contact, decision))
        if not eligible:
            raise ScenarioTopologyPlanError(
                "RETARGET_ROOT_MISSING",
                f"moved component {component.identity!r} has no target-eligible PWR root",
            )
        for contact, decision in eligible:
            assert decision.route_id is not None
            assert decision.route_owner_id is not None
            assert decision.target_net is not None
            assert decision.target_layer is not None
            assert decision.via_template_id is not None
            assert decision.target_node_id is not None
            assert decision.resistance_ohm is not None
            assert decision.inductance_h is not None
            assert decision.route_evidence_sha256 is not None
            owner_key = decision.route_owner_id.casefold()
            if (
                owner_key in active_retarget_owner_ids
                or owner_key != contact.cut_owner_id.casefold()
                or decision.target_net.casefold()
                != component.current_net.casefold()
            ):
                raise ScenarioTopologyPlanError(
                    "RETARGET_ROUTE_OWNER_CONFLICT",
                    f"retarget route {decision.route_id!r} does not transfer its exact source Via owner/NET",
                )
            active_retarget_owner_ids.add(owner_key)
            nodes = nodes_by_refdes[contact.source_refdes.casefold()]
            active_retargets.append(
                ScenarioRetargetRoute(
                    route_id=decision.route_id,
                    via_id=decision.via_id,
                    rail_id=decision.rail_id,
                    first_node_id=nodes.power_node_id,
                    target_node_id=decision.target_node_id,
                    route_owner_id=decision.route_owner_id,
                    target_net=decision.target_net,
                    target_layer=decision.target_layer,
                    via_template_id=decision.via_template_id,
                    resistance_ohm=decision.resistance_ohm,
                    inductance_h=decision.inductance_h,
                    evidence_sha256=decision.route_evidence_sha256,
                )
            )

    partition = tuple(
        SourceContactOwnerPartition(
            via_id=contact_by_via[key].via_id,
            terminal=contact_by_via[key].terminal,
            contact_owner_id=contact_by_via[key].contact_owner_id,
            cut_link_id=contact_by_via[key].cut_link_id,
            cut_owner_id=contact_by_via[key].cut_owner_id,
            disposition=dispositions[key],
        )
        for key in sorted(contact_by_via)
    )
    suppressed = tuple(
        sorted(
            (
                item.cut_link_id
                for item in partition
                if item.disposition
                == SourceContactDisposition.SUPPRESSED_FOR_RETARGET
            ),
            key=_display_sort,
        )
    )
    active_links_tuple = tuple(
        sorted(
            active_links,
            key=lambda item: (item.link_id.casefold(), item.link_id),
        )
    )
    retarget_tuple = tuple(
        sorted(
            active_retargets,
            key=lambda item: (item.route_id.casefold(), item.route_id),
        )
    )
    cap_tuple = tuple(
        sorted(cap_requests, key=lambda item: _display_sort(item.refdes))
    )
    provisional = ScenarioTopologyPlan(
        schema_version=SCENARIO_TOPOLOGY_PLAN_SCHEMA,
        source_sha256=connection_analysis.source_sha256,
        connection_evidence_sha256=connection_hash,
        source_contact_manifest_sha256=source_contacts.manifest_sha256,
        retarget_route_manifest_sha256=retarget_routes.manifest_sha256,
        finite_route_certificate_sha256=(
            source_contacts.finite_route_certificate_sha256
        ),
        terminal_nodes=terminal_nodes,
        active_topology_links=active_links_tuple,
        active_retarget_routes=retarget_tuple,
        cap_body_requests=cap_tuple,
        suppressed_base_cut_ids=suppressed,
        source_owner_partition=partition,
        plan_sha256="0" * 64,
    )
    return replace(
        provisional,
        plan_sha256=_payload_sha256(_plan_payload(provisional)),
    )


__all__ = [
    "RetargetRouteEvidenceSet",
    "SCENARIO_TOPOLOGY_PLAN_SCHEMA",
    "ScenarioCapBodyRequest",
    "ScenarioRetargetRoute",
    "ScenarioTerminalNodes",
    "ScenarioTopologyLink",
    "ScenarioTopologyPlan",
    "ScenarioTopologyPlanError",
    "SourceContactDisposition",
    "SourceContactEvidenceSet",
    "SourceContactOwnerPartition",
    "SourceViaContactEvidence",
    "TargetRouteDecision",
    "TopologyLinkKind",
    "compile_scenario_topology_plan",
    "connection_analysis_evidence_sha256",
]
