"""Memory-mapped Cadence PowerSI SPD import.

The format is line-oriented but production files are commonly hundreds of
megabytes.  This module therefore indexes byte ranges and only decodes records
that can contribute to the PI model.  In particular, multi-port circuit bodies
are skipped and the Node section is parsed only for IDs referenced by selected
device/capacitor connections.
"""

from __future__ import annotations

from array import array
from bisect import bisect_right
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException, InvalidOperation
import hashlib
import json
from math import isfinite, log10
import mmap
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Literal

from ..domain import (
    DielectricPropertyPoint,
    MLOOutline,
    PinKind,
    PinRecord,
    StackupLayer,
    TerminalKind,
)
from ..models.spice import PassiveSubcircuitModel, SpiceModelError, parse_passive_subcircuit
from ..via_model import ViaModelError, estimate_via_segment_rl
from .shared_pad import (
    DecapPadEvidence,
    SpdDecapConnection,
    SpdPadShape,
    SpdSharedPadCluster,
    SpdTopCopperGeometry,
    ViaTopEndpoint,
    extract_shared_pad_connectivity,
)
from .spd_routing import (
    SpdRoutingExtraction,
    extract_spd_routing_obstacles,
    merge_spd_routing_net_roles,
    parse_spd_routing_net_roles,
)


class SpdImportError(ValueError):
    """Raised when an SPD source cannot be opened or analysis is cancelled."""


@dataclass(frozen=True, slots=True)
class _DielectricMaterial:
    """Nominal and source-tabulated properties for one SPD dielectric model."""

    nominal_dk: float
    nominal_df: float
    properties: tuple[DielectricPropertyPoint, ...] = ()


@dataclass(frozen=True, slots=True)
class SpdDiagnostic:
    severity: Literal["info", "warning", "error"]
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SpdSourceInfo:
    path: Path
    name: str
    size_bytes: int
    mtime_ns: int
    sha256: str
    title: str


@dataclass(frozen=True, slots=True)
class SpdPadStack:
    name: str
    drill_diameter_um: float | None
    pad_width_um: float | None
    pad_height_um: float | None
    layers: tuple[str, ...]
    pad_shapes: tuple[SpdPadShape, ...] = ()
    material: str | None = None

    @property
    def pad_diameter_um(self) -> float | None:
        values = [item for item in (self.pad_width_um, self.pad_height_um) if item is not None]
        return max(values) if values else None


@dataclass(frozen=True, slots=True)
class SpdCapInstance:
    refdes: str
    model_id: str | None
    footprint: str
    power_net: str
    ground_net: str
    site: str | None
    mounted: bool
    x_um: float
    y_um: float
    power_pin_id: str
    ground_pin_id: str
    source_part_name: str | None = None
    start_layer: str | None = None
    attach_layer: str | None = None
    power_pad_x_um: float | None = None
    power_pad_y_um: float | None = None
    ground_pad_x_um: float | None = None
    ground_pad_y_um: float | None = None
    power_padstack: str | None = None
    ground_padstack: str | None = None
    power_pad_rotation_degrees: float = 0.0
    ground_pad_rotation_degrees: float = 0.0
    power_pad_rotation_valid: bool = True
    ground_pad_rotation_valid: bool = True

    @property
    def power_pin(self) -> str:
        return self.power_pin_id.partition(":")[2] or self.power_pin_id

    @property
    def ground_pin(self) -> str:
        return self.ground_pin_id.partition(":")[2] or self.ground_pin_id

    @property
    def power_x_um(self) -> float:
        return self.x_um if self.power_pad_x_um is None else self.power_pad_x_um

    @property
    def power_y_um(self) -> float:
        return self.y_um if self.power_pad_y_um is None else self.power_pad_y_um

    @property
    def geometry_present(self) -> bool:
        # Instances are filtered against selected positive-plane nets before
        # they are materialized.
        return True


@dataclass(frozen=True, slots=True)
class SpdViaUsage:
    net: str
    padstack: str
    count: int


SpdDeviceTerminalViaStatus = Literal[
    "complete",
    "source_node_missing",
    "source_layer_missing",
    "source_pin_not_top",
    "missing_incident_via",
    "ambiguous_incident_via",
    "incident_net_mismatch",
    "incident_padstack_definition_missing",
    "incident_padstack_not_on_top",
]


@dataclass(frozen=True, slots=True)
class SpdDeviceTerminalViaEndpoint:
    """Exact source attachment from one Device Connect pin to its first Via.

    This evidence proves direct incidence at the source TOP Node and preserves
    the exact Via ID/PadStackDef.  A complete row may therefore remove that
    exact terminal-owned Via record from the matching aggregate Via population;
    it does not infer a terminal branch or any missing Trace geometry.
    """

    pin_id: str
    refdes: str
    pin: str
    terminal: str
    net: str
    source_node_id: str | None
    source_layer: str | None
    source_padstack: str | None
    source_x_um: float
    source_y_um: float
    status: SpdDeviceTerminalViaStatus
    issues: tuple[str, ...]
    candidate_count: int
    candidate_via_ids: tuple[str, ...]
    incident_via_id: str | None = None
    incident_net: str | None = None
    incident_padstack: str | None = None
    incident_opposite_node_id: str | None = None


@dataclass(frozen=True, slots=True)
class SpdViaPathSegment:
    """One uniquely traversed source Via segment in a recovered vertical path."""

    via_id: str
    padstack: str
    drill_diameter_um: float
    start_layer: str
    end_layer: str
    length_um: float
    end_x_um: float
    end_y_um: float
    rotation_degrees: float = 0.0
    padstack_material: str | None = None


@dataclass(frozen=True, slots=True)
class SpdViaPathEvidence:
    """Compact source-proven TOP-to-target-plane path evidence."""

    via_id: str
    target_layer: str
    target_node_id: str
    target_padstack: str
    target_pad_kind: str
    target_pad_width_um: float
    target_pad_height_um: float
    target_x_um: float
    target_y_um: float
    segments: tuple[SpdViaPathSegment, ...]
    trace_hops: int = 0
    trace_alternate_exit: bool = False


@dataclass(frozen=True, slots=True)
class SpdViaStructuralEvidence:
    """Source-proven path topology retained when target pad geometry is unusable.

    This is intentionally not an eligibility record: it carries only the
    terminal layer/node/XY and traversed segments needed to classify lateral
    or microvia transitions.  Missing target pad shape must never become
    permission for a non-TOP retarget.
    """

    via_id: str
    target_layer: str
    target_node_id: str
    target_x_um: float
    target_y_um: float
    segments: tuple[SpdViaPathSegment, ...]
    trace_hops: int = 0
    trace_alternate_exit: bool = False


@dataclass(frozen=True, slots=True)
class SpdViaPathRecovery:
    """Ephemeral recovery output; the selected raw graph is never persisted."""

    evidence_by_via: Mapping[str, tuple[SpdViaPathEvidence, ...]]
    diagnostics: tuple[SpdDiagnostic, ...]
    statistics: Mapping[str, int]
    structural_evidence_by_via: Mapping[
        str, tuple[SpdViaStructuralEvidence, ...]
    ] = field(default_factory=dict)

    def evidence_for(
        self, via_id: str, target_layer: str
    ) -> SpdViaPathEvidence | None:
        target_key = target_layer.casefold()
        return next(
            (
                item
                for item in self.evidence_by_via.get(via_id.casefold(), ())
                if item.target_layer.casefold() == target_key
            ),
            None,
        )

    def structural_evidence_for(
        self, via_id: str, target_layer: str
    ) -> SpdViaStructuralEvidence | None:
        target_key = target_layer.casefold()
        return next(
            (
                item
                for item in self.structural_evidence_by_via.get(
                    via_id.casefold(), ()
                )
                if item.target_layer.casefold() == target_key
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class SpdSurfaceConnectivityComponent:
    """One exact same-NET raw-graph component spanning target layers."""

    net: str
    layers: tuple[str, ...]

    def __post_init__(self) -> None:
        net = str(self.net).strip()
        if not net:
            raise ValueError("surface connectivity NET must not be blank")
        layer_by_key: dict[str, str] = {}
        for raw_layer in self.layers:
            layer = str(raw_layer).strip()
            if not layer:
                raise ValueError("surface connectivity layers must not be blank")
            key = layer.casefold()
            previous = layer_by_key.get(key)
            if previous is None or layer < previous:
                layer_by_key[key] = layer
        layers = tuple(layer_by_key[key] for key in sorted(layer_by_key))
        if len(layers) < 2:
            raise ValueError(
                "a surface connectivity component must span at least two target layers"
            )
        object.__setattr__(self, "net", net)
        object.__setattr__(self, "layers", layers)


@dataclass(frozen=True, slots=True)
class SpdSurfaceIslandEquivalenceComponent:
    """One same-layer Trace/artwork equipotential island partition member."""

    net: str
    layer: str
    island_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        net = str(self.net).strip()
        layer = str(self.layer).strip()
        island_ids = tuple(
            sorted({str(item).strip() for item in self.island_ids})
        )
        if (
            not net
            or not layer
            or not island_ids
            or any(not item for item in island_ids)
        ):
            raise ValueError("surface island-equivalence component is incomplete")
        object.__setattr__(self, "net", net)
        object.__setattr__(self, "layer", layer)
        object.__setattr__(self, "island_ids", island_ids)


@dataclass(frozen=True, slots=True)
class SpdLandingSurfaceContact:
    """One terminal Via endpoint's exact same-layer artwork component."""

    via_id: str
    endpoint_node_id: str
    net: str
    contact_island_ids_by_layer: Mapping[str, tuple[str, ...]]
    internal_endpoint_node_id: str | None = None
    terminal_owner_kind: Literal["device", "decap", "unknown"] = "unknown"
    external_endpoint_layer: str | None = None
    padstack: str | None = None
    drill_diameter_um: float | None = None
    material: str | None = None
    segments: tuple[SpdViaIslandPairSegment, ...] = ()
    physical_model_status: Literal["complete", "incomplete"] = "incomplete"
    physical_model_issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        via_id = str(self.via_id).strip()
        endpoint_node_id = str(self.endpoint_node_id).strip()
        net = str(self.net).strip()
        internal_endpoint_node_id = str(
            self.internal_endpoint_node_id or ""
        ).strip()
        terminal_owner_kind = str(self.terminal_owner_kind).strip().casefold()
        if terminal_owner_kind not in {"device", "decap", "unknown"}:
            raise ValueError("terminal landing owner kind is invalid")
        external_endpoint_layer = str(
            self.external_endpoint_layer or ""
        ).strip() or None
        padstack = str(self.padstack or "").strip() or None
        material = str(self.material or "").strip() or None
        drill_diameter_um = self.drill_diameter_um
        if drill_diameter_um is not None:
            try:
                drill_diameter_um = float(drill_diameter_um)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "terminal landing drill diameter is invalid"
                ) from exc
            if not isfinite(drill_diameter_um) or drill_diameter_um <= 0.0:
                raise ValueError(
                    "terminal landing drill diameter must be finite and positive"
                )
        segments = tuple(sorted(self.segments, key=lambda item: item.ordinal))
        if not all(isinstance(item, SpdViaIslandPairSegment) for item in segments):
            raise ValueError("terminal landing physical segment is invalid")
        physical_model_status = str(
            self.physical_model_status
        ).strip().casefold()
        if physical_model_status not in {"complete", "incomplete"}:
            raise ValueError("terminal landing physical-model status is invalid")
        physical_model_issues = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.physical_model_issues
                    if str(item).strip()
                }
            )
        )
        if not via_id or not endpoint_node_id or not net:
            raise ValueError("terminal landing surface-contact identity is incomplete")
        layer_rows: dict[str, tuple[str, tuple[str, ...]]] = {}
        for raw_layer, raw_ids in self.contact_island_ids_by_layer.items():
            layer = str(raw_layer).strip()
            island_ids = tuple(
                sorted({str(item).strip() for item in raw_ids})
            )
            if (
                not layer
                or not island_ids
                or any(not item for item in island_ids)
            ):
                raise ValueError("terminal landing surface-contact layer is invalid")
            layer_key = layer.casefold()
            previous = layer_rows.get(layer_key)
            candidate = (layer, island_ids)
            if previous is not None and previous != candidate:
                raise ValueError(
                    "terminal landing surface-contact duplicates one physical layer"
                )
            layer_rows[layer_key] = candidate
        if len(layer_rows) > 1:
            raise ValueError(
                "one terminal endpoint Node cannot directly contact multiple layers"
            )
        if layer_rows and not internal_endpoint_node_id:
            raise ValueError(
                "a resolved terminal island contact requires its internal Via endpoint"
            )
        endpoint_layer = next(
            (row[0] for row in layer_rows.values()), None
        )
        segment_chain_complete = bool(segments) and (
            tuple(item.ordinal for item in segments) == tuple(range(len(segments)))
            and external_endpoint_layer is not None
            and segments[0].start_layer.casefold()
            == external_endpoint_layer.casefold()
            and endpoint_layer is not None
            and segments[-1].end_layer.casefold() == endpoint_layer.casefold()
            and all(
                first.end_layer.casefold() == second.start_layer.casefold()
                for first, second in zip(segments, segments[1:], strict=False)
            )
        )
        if physical_model_status == "complete" and (
            terminal_owner_kind == "unknown"
            or not padstack
            or drill_diameter_um is None
            or not segment_chain_complete
            or physical_model_issues
        ):
            raise ValueError(
                "complete terminal landing physical model is not self-contained"
            )
        if physical_model_status == "incomplete" and not physical_model_issues:
            physical_model_issues = ("physical_model_not_supplied",)
        object.__setattr__(self, "via_id", via_id)
        object.__setattr__(self, "endpoint_node_id", endpoint_node_id)
        object.__setattr__(self, "net", net)
        object.__setattr__(
            self,
            "internal_endpoint_node_id",
            internal_endpoint_node_id or None,
        )
        object.__setattr__(self, "terminal_owner_kind", terminal_owner_kind)
        object.__setattr__(
            self, "external_endpoint_layer", external_endpoint_layer
        )
        object.__setattr__(self, "padstack", padstack)
        object.__setattr__(self, "drill_diameter_um", drill_diameter_um)
        object.__setattr__(self, "material", material)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "physical_model_status", physical_model_status)
        object.__setattr__(self, "physical_model_issues", physical_model_issues)
        object.__setattr__(
            self,
            "contact_island_ids_by_layer",
            MappingProxyType(
                {
                    layer_rows[key][0]: layer_rows[key][1]
                    for key in sorted(layer_rows)
                }
            ),
        )

    @property
    def landing_key(self) -> tuple[str, str]:
        return (self.via_id.casefold(), self.endpoint_node_id.casefold())

    @property
    def endpoint_layer(self) -> str | None:
        return next(iter(self.contact_island_ids_by_layer), None)

    @property
    def endpoint_island_id(self) -> str | None:
        values = next(iter(self.contact_island_ids_by_layer.values()), ())
        return values[0] if values else None

    @property
    def component_island_ids(self) -> tuple[str, ...]:
        return next(iter(self.contact_island_ids_by_layer.values()), ())


@dataclass(frozen=True, slots=True)
class SpdViaIslandPairSegment:
    """One source-proven physical segment inside an island-pair Via span."""

    ordinal: int
    start_layer: str
    end_layer: str
    length_um: float

    def __post_init__(self) -> None:
        start_layer = str(self.start_layer).strip()
        end_layer = str(self.end_layer).strip()
        try:
            length_um = float(self.length_um)
        except (TypeError, ValueError) as exc:
            raise ValueError("Via island-pair segment length is invalid") from exc
        if (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or self.ordinal < 0
            or not start_layer
            or not end_layer
            or start_layer.casefold() == end_layer.casefold()
            or not isfinite(length_um)
            or length_um <= 0.0
        ):
            raise ValueError("Via island-pair segment is incomplete")
        object.__setattr__(self, "start_layer", start_layer)
        object.__setattr__(self, "end_layer", end_layer)
        object.__setattr__(self, "length_um", length_um)


@dataclass(frozen=True, slots=True)
class SpdViaIslandPairAggregate:
    """Compact source-order aggregate of raw Vias between exact islands."""

    net: str
    padstack: str
    start_layer: str
    end_layer: str
    start_island_id: str
    end_island_id: str
    count: int
    via_ids_sha256: str
    start_component_island_ids: tuple[str, ...] = ()
    end_component_island_ids: tuple[str, ...] = ()
    terminal_owned_count: int | None = None
    substrate_count: int | None = None
    drill_diameter_um: float | None = None
    material: str | None = None
    segments: tuple[SpdViaIslandPairSegment, ...] = ()
    physical_model_status: Literal["complete", "incomplete"] = "incomplete"
    physical_model_issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        strings = tuple(
            str(item).strip()
            for item in (
                self.net,
                self.padstack,
                self.start_layer,
                self.end_layer,
                self.start_island_id,
                self.end_island_id,
            )
        )
        digest = str(self.via_ids_sha256).strip().casefold()
        if any(not item for item in strings):
            raise ValueError("Via island-pair aggregate identity is incomplete")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Via island-pair aggregate SHA-256 is invalid")
        if (
            not isinstance(self.count, int)
            or isinstance(self.count, bool)
            or self.count <= 0
        ):
            raise ValueError("Via island-pair aggregate count must be positive")
        owned = self.terminal_owned_count
        substrate = self.substrate_count
        if (owned is None) != (substrate is None):
            raise ValueError(
                "Via island-pair terminal/substrate counts must be supplied together"
            )
        if owned is not None and (
            not isinstance(owned, int)
            or isinstance(owned, bool)
            or owned < 0
            or owned > self.count
            or not isinstance(substrate, int)
            or isinstance(substrate, bool)
            or substrate < 0
            or substrate != self.count - owned
        ):
            raise ValueError("Via island-pair ownership counts are inconsistent")
        drill_diameter_um = self.drill_diameter_um
        if drill_diameter_um is not None:
            try:
                drill_diameter_um = float(drill_diameter_um)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "Via island-pair drill diameter is invalid"
                ) from exc
            if not isfinite(drill_diameter_um) or drill_diameter_um <= 0.0:
                raise ValueError(
                    "Via island-pair drill diameter must be finite and positive"
                )
        material = str(self.material or "").strip() or None
        start_component_island_ids = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.start_component_island_ids
                    if str(item).strip()
                }
            )
        ) or (strings[4],)
        end_component_island_ids = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.end_component_island_ids
                    if str(item).strip()
                }
            )
        ) or (strings[5],)
        if (
            strings[4] != start_component_island_ids[0]
            or strings[5] != end_component_island_ids[0]
        ):
            raise ValueError(
                "Via island-pair representative must be its component's first island"
            )
        segments = tuple(sorted(self.segments, key=lambda item: item.ordinal))
        if not all(isinstance(item, SpdViaIslandPairSegment) for item in segments):
            raise ValueError("Via island-pair segment record is invalid")
        status = str(self.physical_model_status).strip().casefold()
        if status not in {"complete", "incomplete"}:
            raise ValueError("Via island-pair physical-model status is invalid")
        issues = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.physical_model_issues
                    if str(item).strip()
                }
            )
        )
        segment_chain_complete = bool(segments) and (
            tuple(item.ordinal for item in segments) == tuple(range(len(segments)))
            and segments[0].start_layer.casefold() == strings[2].casefold()
            and segments[-1].end_layer.casefold() == strings[3].casefold()
            and all(
                first.end_layer.casefold() == second.start_layer.casefold()
                for first, second in zip(segments, segments[1:], strict=False)
            )
        )
        if status == "complete" and (
            drill_diameter_um is None or not segment_chain_complete or issues
        ):
            raise ValueError(
                "complete Via island-pair physical model is not self-contained"
            )
        if status == "incomplete" and not issues:
            issues = ("physical_model_not_supplied",)
        (
            net,
            padstack,
            start_layer,
            end_layer,
            start_island_id,
            end_island_id,
        ) = strings
        object.__setattr__(self, "net", net)
        object.__setattr__(self, "padstack", padstack)
        object.__setattr__(self, "start_layer", start_layer)
        object.__setattr__(self, "end_layer", end_layer)
        object.__setattr__(self, "start_island_id", start_island_id)
        object.__setattr__(self, "end_island_id", end_island_id)
        object.__setattr__(self, "via_ids_sha256", digest)
        object.__setattr__(
            self, "start_component_island_ids", start_component_island_ids
        )
        object.__setattr__(
            self, "end_component_island_ids", end_component_island_ids
        )
        object.__setattr__(self, "drill_diameter_um", drill_diameter_um)
        object.__setattr__(self, "material", material)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "physical_model_status", status)
        object.__setattr__(self, "physical_model_issues", issues)

    @property
    def total_count(self) -> int:
        return self.count


@dataclass(frozen=True, slots=True)
class SpdViaIslandPairCoverage:
    """Fail-closed partition of every target-NET raw Via record."""

    raw_target_via_count: int
    paired_via_count: int
    terminal_owned_unpaired_count: int
    terminal_owned_unpaired_via_ids_sha256: str
    unsupported_missing_endpoint_count: int
    unsupported_missing_endpoint_via_ids_sha256: str
    outside_retained_interface_scope_count: int
    outside_retained_interface_scope_via_ids_sha256: str
    model_relevant_via_count: int
    terminal_owned_ids_supplied: bool
    terminal_owned_declared_count: int
    terminal_owned_observed_count: int
    paired_terminal_owned_count: int | None = None
    paired_substrate_count: int | None = None

    def __post_init__(self) -> None:
        counts = (
            self.raw_target_via_count,
            self.paired_via_count,
            self.terminal_owned_unpaired_count,
            self.unsupported_missing_endpoint_count,
            self.outside_retained_interface_scope_count,
            self.model_relevant_via_count,
            self.terminal_owned_declared_count,
            self.terminal_owned_observed_count,
        )
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in counts
        ):
            raise ValueError("Via island-pair coverage count is invalid")
        if self.raw_target_via_count != (
            self.paired_via_count
            + self.terminal_owned_unpaired_count
            + self.unsupported_missing_endpoint_count
            + self.outside_retained_interface_scope_count
        ):
            raise ValueError("Via island-pair coverage does not partition raw Vias")
        if self.model_relevant_via_count != (
            self.paired_via_count
            + self.terminal_owned_unpaired_count
            + self.unsupported_missing_endpoint_count
        ):
            raise ValueError(
                "Via island-pair model-relevant count is inconsistent"
            )
        for digest in (
            self.terminal_owned_unpaired_via_ids_sha256,
            self.unsupported_missing_endpoint_via_ids_sha256,
            self.outside_retained_interface_scope_via_ids_sha256,
        ):
            if not re.fullmatch(r"[0-9a-fA-F]{64}", str(digest).strip()):
                raise ValueError("Via island-pair coverage SHA-256 is invalid")
        paired_owned = self.paired_terminal_owned_count
        paired_substrate = self.paired_substrate_count
        if bool(self.terminal_owned_ids_supplied):
            if (
                not isinstance(paired_owned, int)
                or isinstance(paired_owned, bool)
                or paired_owned < 0
                or not isinstance(paired_substrate, int)
                or isinstance(paired_substrate, bool)
                or paired_substrate < 0
                or paired_owned + paired_substrate != self.paired_via_count
                or self.terminal_owned_observed_count
                != paired_owned + self.terminal_owned_unpaired_count
                or self.terminal_owned_observed_count
                > self.terminal_owned_declared_count
            ):
                raise ValueError("Via island-pair ownership partition is inconsistent")
        elif (
            paired_owned is not None
            or paired_substrate is not None
            or self.terminal_owned_declared_count
            or self.terminal_owned_observed_count
            or self.terminal_owned_unpaired_count
        ):
            raise ValueError(
                "Via island-pair ownership counts require exact terminal IDs"
            )
        object.__setattr__(
            self,
            "terminal_owned_unpaired_via_ids_sha256",
            str(self.terminal_owned_unpaired_via_ids_sha256).strip().casefold(),
        )
        object.__setattr__(
            self,
            "unsupported_missing_endpoint_via_ids_sha256",
            str(self.unsupported_missing_endpoint_via_ids_sha256)
            .strip()
            .casefold(),
        )
        object.__setattr__(
            self,
            "outside_retained_interface_scope_via_ids_sha256",
            str(self.outside_retained_interface_scope_via_ids_sha256)
            .strip()
            .casefold(),
        )

    @property
    def status(self) -> str:
        return (
            "complete"
            if self.terminal_owned_ids_supplied
            and self.terminal_owned_observed_count
            == self.terminal_owned_declared_count
            and self.unsupported_missing_endpoint_count == 0
            else "incomplete"
        )


@dataclass(frozen=True, slots=True)
class SpdFiniteViaSeriesTerm:
    """One repeated physical Via model inside a contracted series path."""

    ordinal: int
    count: int
    padstack: str
    start_layer: str
    end_layer: str
    drill_diameter_um: float | None
    material: str | None
    segments: tuple[SpdViaIslandPairSegment, ...]
    resistance_ohm: float | None
    inductance_h: float | None
    length_um: float | None
    physical_model_status: Literal["complete", "incomplete"]
    physical_model_issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.ordinal, int)
            or isinstance(self.ordinal, bool)
            or self.ordinal < 0
            or not isinstance(self.count, int)
            or isinstance(self.count, bool)
            or self.count <= 0
        ):
            raise ValueError("finite-Via series term ordinal/count is invalid")
        padstack = str(self.padstack).strip()
        start_layer = str(self.start_layer).strip()
        end_layer = str(self.end_layer).strip()
        if (
            not padstack
            or not start_layer
            or not end_layer
        ):
            raise ValueError("finite-Via series term identity is incomplete")
        drill = self.drill_diameter_um
        if drill is not None:
            try:
                drill = float(drill)
            except (TypeError, ValueError) as exc:
                raise ValueError("finite-Via drill diameter is invalid") from exc
            if not isfinite(drill) or drill <= 0.0:
                raise ValueError("finite-Via drill diameter must be positive")
        material = str(self.material or "").strip() or None
        segments = tuple(sorted(self.segments, key=lambda item: item.ordinal))
        if not all(isinstance(item, SpdViaIslandPairSegment) for item in segments):
            raise ValueError("finite-Via physical segment is invalid")
        status = str(self.physical_model_status).strip().casefold()
        if status not in {"complete", "incomplete"}:
            raise ValueError("finite-Via physical-model status is invalid")
        issues = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.physical_model_issues
                    if str(item).strip()
                }
            )
        )
        electrical_values: list[float | None] = []
        for raw in (self.resistance_ohm, self.inductance_h, self.length_um):
            try:
                electrical_values.append(float(raw) if raw is not None else None)
            except (TypeError, ValueError):
                electrical_values.append(None)
        resistance, inductance, length_um = electrical_values
        electrical_complete = (
            resistance is not None
            and inductance is not None
            and length_um is not None
            and isfinite(resistance)
            and isfinite(inductance)
            and isfinite(length_um)
            and resistance >= 0.0
            and inductance >= 0.0
            and length_um > 0.0
            and (resistance > 0.0 or inductance > 0.0)
        )
        if status == "complete" and (
            drill is None or not segments or issues or not electrical_complete
        ):
            raise ValueError("complete finite-Via term is not self-contained")
        if status == "incomplete" and not issues:
            issues = ("physical_model_not_supplied",)
        object.__setattr__(self, "padstack", padstack)
        object.__setattr__(self, "start_layer", start_layer)
        object.__setattr__(self, "end_layer", end_layer)
        object.__setattr__(self, "drill_diameter_um", drill)
        object.__setattr__(self, "material", material)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "resistance_ohm", resistance)
        object.__setattr__(self, "inductance_h", inductance)
        object.__setattr__(self, "length_um", length_um)
        object.__setattr__(self, "physical_model_status", status)
        object.__setattr__(self, "physical_model_issues", issues)


@dataclass(frozen=True, slots=True)
class SpdFiniteViaQuotientVertex:
    """One explicit node of the same-layer Trace/artwork quotient graph."""

    vertex_id: str
    net: str
    layer: str
    representative_node_id: str
    source_node_count: int
    source_node_ids_sha256: str
    roles: tuple[str, ...]
    retained_component_island_ids_by_layer: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    terminal_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        vertex_id = str(self.vertex_id).strip()
        net = str(self.net).strip()
        layer = str(self.layer).strip()
        representative = str(self.representative_node_id).strip()
        digest = str(self.source_node_ids_sha256).strip().casefold()
        if (
            not vertex_id.startswith("spd-finite-via-vertex:")
            or not net
            or not layer
            or not representative
            or not isinstance(self.source_node_count, int)
            or isinstance(self.source_node_count, bool)
            or self.source_node_count <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("finite-Via quotient vertex identity is invalid")
        roles = tuple(sorted({str(item).strip() for item in self.roles}))
        allowed_roles = {
            "retained_surface",
            "terminal",
            "retarget_cut",
            "junction",
            "leaf",
            "cycle_anchor",
        }
        if not roles or any(item not in allowed_roles for item in roles):
            raise ValueError("finite-Via quotient vertex role is invalid")
        retained: dict[str, tuple[str, ...]] = {}
        for raw_layer, raw_ids in self.retained_component_island_ids_by_layer.items():
            retained_layer = str(raw_layer).strip()
            island_ids = tuple(
                sorted({str(item).strip() for item in raw_ids if str(item).strip()})
            )
            if not retained_layer or not island_ids:
                raise ValueError("finite-Via retained component binding is invalid")
            retained[retained_layer] = island_ids
        terminal_ids = tuple(
            sorted({str(item).strip() for item in self.terminal_ids if str(item).strip()})
        )
        if ("retained_surface" in roles) != bool(retained):
            raise ValueError("finite-Via retained-surface role is inconsistent")
        if ("terminal" in roles) != bool(terminal_ids):
            raise ValueError("finite-Via terminal role is inconsistent")
        object.__setattr__(self, "vertex_id", vertex_id)
        object.__setattr__(self, "net", net)
        object.__setattr__(self, "layer", layer)
        object.__setattr__(self, "representative_node_id", representative)
        object.__setattr__(self, "source_node_ids_sha256", digest)
        object.__setattr__(self, "roles", roles)
        object.__setattr__(
            self,
            "retained_component_island_ids_by_layer",
            MappingProxyType(dict(sorted(retained.items(), key=lambda item: item[0].casefold()))),
        )
        object.__setattr__(self, "terminal_ids", terminal_ids)


@dataclass(frozen=True, slots=True)
class SpdFiniteViaQuotientEdge:
    """One degree-2-contracted series path, optionally repeated in parallel."""

    edge_id: str
    net: str
    start_vertex_id: str
    end_vertex_id: str
    parallel_path_count: int
    per_path_via_count: int
    raw_via_count: int
    raw_via_ids_sha256: str
    owner_ids: tuple[str, ...]
    series_terms: tuple[SpdFiniteViaSeriesTerm, ...]
    resistance_ohm: float | None
    inductance_h: float | None
    length_um: float | None
    mode: Literal["retained_explicit", "contracted_series"]
    physical_model_status: Literal["complete", "incomplete"]
    physical_model_issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        strings = tuple(
            str(item).strip()
            for item in (
                self.edge_id,
                self.net,
                self.start_vertex_id,
                self.end_vertex_id,
            )
        )
        if (
            not strings[0].startswith("spd-finite-via-edge:")
            or any(not item for item in strings)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                for value in (
                    self.parallel_path_count,
                    self.per_path_via_count,
                    self.raw_via_count,
                )
            )
            or self.raw_via_count
            != self.parallel_path_count * self.per_path_via_count
        ):
            raise ValueError("finite-Via quotient edge identity/count is invalid")
        digest = str(self.raw_via_ids_sha256).strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("finite-Via quotient edge Via-ID digest is invalid")
        terms = tuple(sorted(self.series_terms, key=lambda item: item.ordinal))
        owner_ids = tuple(str(item).strip() for item in self.owner_ids)
        if (
            len(owner_ids) != self.raw_via_count
            or any(not item.startswith("via:") for item in owner_ids)
            or len({item.casefold() for item in owner_ids}) != len(owner_ids)
        ):
            raise ValueError("finite-Via quotient edge owner ledger is invalid")
        if (
            not terms
            or not all(isinstance(item, SpdFiniteViaSeriesTerm) for item in terms)
            or tuple(item.ordinal for item in terms) != tuple(range(len(terms)))
            or sum(item.count for item in terms) != self.per_path_via_count
        ):
            raise ValueError("finite-Via quotient edge series terms are invalid")
        status = str(self.physical_model_status).strip().casefold()
        issues = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.physical_model_issues
                    if str(item).strip()
                }
            )
        )
        complete = all(item.physical_model_status == "complete" for item in terms)
        if status not in {"complete", "incomplete"} or (status == "complete") != complete:
            raise ValueError("finite-Via quotient edge physical status is invalid")
        if status == "complete" and issues:
            raise ValueError("complete finite-Via quotient edge has issues")
        if status == "incomplete" and not issues:
            issues = tuple(
                sorted(
                    {
                        issue
                        for item in terms
                        for issue in item.physical_model_issues
                    }
                )
            ) or ("physical_model_incomplete",)
        try:
            resistance = (
                float(self.resistance_ohm)
                if self.resistance_ohm is not None
                else None
            )
            inductance = (
                float(self.inductance_h)
                if self.inductance_h is not None
                else None
            )
            length_um = float(self.length_um) if self.length_um is not None else None
        except (TypeError, ValueError):
            resistance = inductance = length_um = None
        electrical_complete = (
            resistance is not None
            and inductance is not None
            and length_um is not None
            and isfinite(resistance)
            and isfinite(inductance)
            and isfinite(length_um)
            and resistance >= 0.0
            and inductance >= 0.0
            and length_um > 0.0
            and (resistance > 0.0 or inductance > 0.0)
        )
        if (status == "complete") != electrical_complete:
            raise ValueError("finite-Via quotient edge electrical model is invalid")
        mode = str(self.mode).strip().casefold()
        if mode not in {"retained_explicit", "contracted_series"}:
            raise ValueError("finite-Via quotient edge reduction mode is invalid")
        if (mode == "retained_explicit") != (self.per_path_via_count == 1):
            raise ValueError("finite-Via quotient edge mode contradicts its path")
        object.__setattr__(self, "edge_id", strings[0])
        object.__setattr__(self, "net", strings[1])
        object.__setattr__(self, "start_vertex_id", strings[2])
        object.__setattr__(self, "end_vertex_id", strings[3])
        object.__setattr__(self, "raw_via_ids_sha256", digest)
        object.__setattr__(self, "owner_ids", owner_ids)
        object.__setattr__(self, "series_terms", terms)
        object.__setattr__(self, "resistance_ohm", resistance)
        object.__setattr__(self, "inductance_h", inductance)
        object.__setattr__(self, "length_um", length_um)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "physical_model_status", status)
        object.__setattr__(self, "physical_model_issues", issues)


@dataclass(frozen=True, slots=True)
class SpdFiniteViaQuotientCoverage:
    """Exact-once raw Via ownership proof for the finite quotient network."""

    raw_target_via_count: int
    modeled_global_via_count: int
    outside_scope_via_count: int
    pruned_dangling_via_count: int
    physical_complete_via_count: int
    physical_incomplete_via_count: int
    raw_target_via_ids_sha256: str
    modeled_global_via_ids_sha256: str
    modeled_owner_ledger_sha256: str
    modeled_owner_canonical_sha256: str
    outside_scope_via_ids_sha256: str
    terminal_exclusive_via_count: int = 0

    def __post_init__(self) -> None:
        counts = (
            self.raw_target_via_count,
            self.modeled_global_via_count,
            self.outside_scope_via_count,
            self.pruned_dangling_via_count,
            self.physical_complete_via_count,
            self.physical_incomplete_via_count,
            self.terminal_exclusive_via_count,
        )
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in counts
        ):
            raise ValueError("finite-Via quotient coverage count is invalid")
        if self.raw_target_via_count != (
            self.modeled_global_via_count + self.outside_scope_via_count
        ):
            raise ValueError("finite-Via quotient does not partition raw Vias")
        if self.pruned_dangling_via_count != self.outside_scope_via_count:
            raise ValueError("finite-Via pruned/outside disposition is inconsistent")
        if self.modeled_global_via_count != (
            self.physical_complete_via_count + self.physical_incomplete_via_count
        ):
            raise ValueError("finite-Via physical coverage is inconsistent")
        if self.terminal_exclusive_via_count != 0:
            raise ValueError(
                "v4 finite-Via ownership keeps every relevant Via in global MNA"
            )
        for name in (
            "raw_target_via_ids_sha256",
            "modeled_global_via_ids_sha256",
            "modeled_owner_ledger_sha256",
            "modeled_owner_canonical_sha256",
            "outside_scope_via_ids_sha256",
        ):
            digest = str(getattr(self, name)).strip().casefold()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("finite-Via quotient coverage digest is invalid")
            object.__setattr__(self, name, digest)

    @property
    def status(self) -> str:
        return "complete" if self.physical_incomplete_via_count == 0 else "incomplete"


@dataclass(frozen=True, slots=True)
class SpdFiniteViaScenarioIsolationCoverage:
    """Proof that editable decap landing Nodes were not ideal-unioned.

    The finite-Via base network must keep each editable decap TOP landing as a
    distinct quotient vertex.  Scenario compilation can then add the
    source-proven pad/contact links conditionally, remove one gap cell and its
    incident links, or detach a moved capacitor without a hidden raw
    Trace/artwork bypass surviving in the permanent quotient.
    """

    requested_landing_count: int
    isolated_landing_count: int
    isolated_node_count: int
    suppressed_artwork_contact_count: int
    suppressed_trace_edge_count: int
    requested_landing_ids_sha256: str
    isolated_landing_ids_sha256: str

    def __post_init__(self) -> None:
        counts = (
            self.requested_landing_count,
            self.isolated_landing_count,
            self.isolated_node_count,
            self.suppressed_artwork_contact_count,
            self.suppressed_trace_edge_count,
        )
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in counts
        ):
            raise ValueError("finite-Via scenario-isolation count is invalid")
        if self.isolated_landing_count > self.requested_landing_count:
            raise ValueError("finite-Via scenario isolation exceeds its request")
        if self.isolated_node_count > self.isolated_landing_count:
            raise ValueError("finite-Via scenario isolated-Node count is invalid")
        for name in (
            "requested_landing_ids_sha256",
            "isolated_landing_ids_sha256",
        ):
            digest = str(getattr(self, name)).strip().casefold()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("finite-Via scenario-isolation digest is invalid")
            object.__setattr__(self, name, digest)

    @property
    def status(self) -> str:
        return (
            "complete"
            if self.requested_landing_count == self.isolated_landing_count
            else "incomplete"
        )


@dataclass(frozen=True, slots=True)
class SpdFiniteViaRetargetDestinationCoverage:
    """Coverage for requested path-evidence destination Nodes."""

    requested_destination_count: int
    resolved_destination_count: int
    requested_destination_ids_sha256: str
    resolved_destination_ids_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.requested_destination_count, int)
            or isinstance(self.requested_destination_count, bool)
            or self.requested_destination_count < 0
            or not isinstance(self.resolved_destination_count, int)
            or isinstance(self.resolved_destination_count, bool)
            or self.resolved_destination_count < 0
            or self.resolved_destination_count > self.requested_destination_count
        ):
            raise ValueError("finite-Via retarget-destination count is invalid")
        for name in (
            "requested_destination_ids_sha256",
            "resolved_destination_ids_sha256",
        ):
            digest = str(getattr(self, name)).strip().casefold()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("finite-Via retarget-destination digest is invalid")
            object.__setattr__(self, name, digest)

    @property
    def status(self) -> str:
        return (
            "complete"
            if self.requested_destination_count == self.resolved_destination_count
            else "incomplete"
        )


@dataclass(frozen=True, slots=True)
class SpdSurfaceEquivalenceProof:
    """Summary gate for one exact layer/NET island equivalence partition."""

    net: str
    layer: str
    island_ids: tuple[str, ...]
    contacted_island_ids: tuple[str, ...]
    graph_component_count: int
    status: str

    def __post_init__(self) -> None:
        net = str(self.net).strip()
        layer = str(self.layer).strip()
        island_ids = tuple(sorted({str(item).strip() for item in self.island_ids}))
        contacted = tuple(
            sorted({str(item).strip() for item in self.contacted_island_ids})
        )
        status = str(self.status).strip()
        if not net or not layer or not island_ids or any(not item for item in island_ids):
            raise ValueError("surface equivalence proof identity is incomplete")
        if any(not item for item in contacted) or not set(contacted) <= set(island_ids):
            raise ValueError("surface equivalence proof contacts unknown islands")
        if (
            not isinstance(self.graph_component_count, int)
            or isinstance(self.graph_component_count, bool)
            or self.graph_component_count < 0
        ):
            raise ValueError("surface equivalence graph-component count is invalid")
        allowed = {"complete", "uncontacted_island"}
        if status not in allowed:
            raise ValueError("surface equivalence proof status is invalid")
        complete = contacted == island_ids and self.graph_component_count >= 1
        if (status == "complete") != complete:
            raise ValueError("surface equivalence proof status contradicts its counts")
        object.__setattr__(self, "net", net)
        object.__setattr__(self, "layer", layer)
        object.__setattr__(self, "island_ids", island_ids)
        object.__setattr__(self, "contacted_island_ids", contacted)
        object.__setattr__(self, "status", status)


@dataclass(frozen=True, slots=True)
class SpdGroundReachability:
    """Batch raw-graph reachability for mixed-reference GND landings.

    Unlike :class:`SpdViaPathRecovery`, this is deliberately not an electrical
    path extractor.  Its single claim is whether a landing's same-NET
    Via+Trace component reaches an exact named target layer.  Branches are
    valid evidence here and no RL is inferred from them.
    """

    reachable_keys: frozenset[tuple[str, str, str]]
    unreachable_keys: frozenset[tuple[str, str, str]]
    statistics: Mapping[str, int]
    surface_components: tuple[SpdSurfaceConnectivityComponent, ...] = ()
    surface_layers_by_landing: Mapping[
        tuple[str, str], tuple[str, ...]
    ] = field(default_factory=dict)
    surface_islands_by_landing: Mapping[
        tuple[str, str], tuple[str, ...]
    ] = field(default_factory=dict)
    surface_equivalence_proofs: tuple[SpdSurfaceEquivalenceProof, ...] = ()
    surface_equivalence_components: tuple[
        SpdSurfaceIslandEquivalenceComponent, ...
    ] = ()
    landing_surface_contacts: tuple[SpdLandingSurfaceContact, ...] = ()
    via_island_pair_aggregates: tuple[SpdViaIslandPairAggregate, ...] = ()
    via_island_pair_coverage: SpdViaIslandPairCoverage | None = None
    finite_via_vertices: tuple[SpdFiniteViaQuotientVertex, ...] = ()
    finite_via_edges: tuple[SpdFiniteViaQuotientEdge, ...] = ()
    finite_via_vertex_id_by_landing: Mapping[
        tuple[str, str], str
    ] = field(default_factory=dict)
    finite_via_edge_id_by_landing: Mapping[
        tuple[str, str], str
    ] = field(default_factory=dict)
    finite_via_coverage: SpdFiniteViaQuotientCoverage | None = None
    finite_via_scenario_isolated_landing_keys: frozenset[
        tuple[str, str]
    ] = frozenset()
    finite_via_scenario_isolation_coverage: (
        SpdFiniteViaScenarioIsolationCoverage | None
    ) = None
    finite_via_vertex_id_by_retarget_destination: Mapping[
        tuple[str, str, str], str
    ] = field(default_factory=dict)
    finite_via_retarget_destination_coverage: (
        SpdFiniteViaRetargetDestinationCoverage | None
    ) = None

    def __post_init__(self) -> None:
        def sorted_tuple_if_needed(
            values: Iterable[Any], *, key: Callable[[Any], Any]
        ) -> tuple[Any, ...]:
            """Reuse an already-canonical tuple without a second list copy."""

            concrete = tuple(values)
            previous: Any = None
            has_previous = False
            for item in concrete:
                current = key(item)
                if has_previous and current < previous:
                    return tuple(sorted(concrete, key=key))
                previous = current
                has_previous = True
            return concrete

        def ordered_mapping_proxy(
            values: dict[Any, Any],
        ) -> Mapping[Any, Any]:
            """Freeze a locally owned mapping, sorting only when necessary."""

            previous: Any = None
            has_previous = False
            for current in values:
                if has_previous and current < previous:
                    return MappingProxyType(
                        {key: values[key] for key in sorted(values)}
                    )
                previous = current
                has_previous = True
            return MappingProxyType(values)

        component_values = tuple(self.surface_components)
        component_set = set(component_values)
        components = sorted_tuple_if_needed(
            component_values if len(component_set) == len(component_values) else component_set,
            key=lambda item: (
                item.net.casefold(),
                tuple(layer.casefold() for layer in item.layers),
                item.net,
                item.layers,
            ),
        )
        layers_by_landing: dict[tuple[str, str], tuple[str, ...]] = {}
        for raw_key, raw_layers in self.surface_layers_by_landing.items():
            if len(raw_key) != 2:
                raise ValueError(
                    "surface landing keys must contain Via and endpoint Node IDs"
                )
            key = (str(raw_key[0]).casefold(), str(raw_key[1]).casefold())
            layer_by_key: dict[str, str] = {}
            for raw_layer in raw_layers:
                layer = str(raw_layer).strip()
                if not layer:
                    raise ValueError("surface landing layers must not be blank")
                layer_key = layer.casefold()
                previous = layer_by_key.get(layer_key)
                if previous is None or layer < previous:
                    layer_by_key[layer_key] = layer
            layers_by_landing[key] = tuple(
                layer_by_key[layer_key] for layer_key in sorted(layer_by_key)
            )
        object.__setattr__(self, "surface_components", components)
        object.__setattr__(
            self,
            "surface_layers_by_landing",
            ordered_mapping_proxy(layers_by_landing),
        )
        islands_by_landing: dict[tuple[str, str], tuple[str, ...]] = {}
        for raw_key, raw_islands in self.surface_islands_by_landing.items():
            if len(raw_key) != 2:
                raise ValueError(
                    "surface landing island keys must contain Via and endpoint Node IDs"
                )
            key = (str(raw_key[0]).casefold(), str(raw_key[1]).casefold())
            islands = tuple(
                sorted({str(item).strip() for item in raw_islands if str(item).strip()})
            )
            islands_by_landing[key] = islands
        proofs = sorted_tuple_if_needed(
            self.surface_equivalence_proofs,
            key=lambda item: (
                item.net.casefold(),
                item.layer.casefold(),
                item.net,
                item.layer,
            ),
        )
        if not all(isinstance(item, SpdSurfaceEquivalenceProof) for item in proofs):
            raise ValueError("surface equivalence proofs have an invalid record")
        proof_keys = {(item.net.casefold(), item.layer.casefold()) for item in proofs}
        if len(proof_keys) != len(proofs):
            raise ValueError("surface equivalence proofs duplicate a layer/NET surface")
        equivalence_components = sorted_tuple_if_needed(
            self.surface_equivalence_components,
            key=lambda item: (
                item.net.casefold(),
                item.layer.casefold(),
                item.island_ids,
                item.net,
                item.layer,
            ),
        )
        if not all(
            isinstance(item, SpdSurfaceIslandEquivalenceComponent)
            for item in equivalence_components
        ):
            raise ValueError("surface equivalence components have an invalid record")
        component_islands_by_surface: dict[
            tuple[str, str], set[str]
        ] = {}
        component_partitions_by_surface: dict[
            tuple[str, str], set[tuple[str, ...]]
        ] = {}
        component_identities: set[tuple[str, str, tuple[str, ...]]] = set()
        for item in equivalence_components:
            surface_key = (item.net.casefold(), item.layer.casefold())
            identity = (*surface_key, item.island_ids)
            if identity in component_identities:
                raise ValueError("surface equivalence components duplicate a partition")
            component_identities.add(identity)
            observed = component_islands_by_surface.setdefault(surface_key, set())
            if observed.intersection(item.island_ids):
                raise ValueError(
                    "surface equivalence components overlap one artwork island"
                )
            observed.update(item.island_ids)
            component_partitions_by_surface.setdefault(surface_key, set()).add(
                item.island_ids
            )
        if proofs and equivalence_components:
            proof_islands = {
                (item.net.casefold(), item.layer.casefold()): set(item.island_ids)
                for item in proofs
            }
            if component_islands_by_surface != proof_islands:
                raise ValueError(
                    "surface equivalence components do not partition every proof island"
                )

        landing_contacts = sorted_tuple_if_needed(
            self.landing_surface_contacts,
            key=lambda item: (
                item.via_id.casefold(),
                item.endpoint_node_id.casefold(),
                item.net.casefold(),
                item.via_id,
                item.endpoint_node_id,
                item.net,
            ),
        )
        if not all(
            isinstance(item, SpdLandingSurfaceContact)
            for item in landing_contacts
        ):
            raise ValueError("terminal landing surface contacts have an invalid record")
        landing_keys = [item.landing_key for item in landing_contacts]
        if len(set(landing_keys)) != len(landing_keys):
            raise ValueError("terminal landing surface contacts duplicate a landing")
        if component_islands_by_surface:
            for contact in landing_contacts:
                net_key = contact.net.casefold()
                for layer, island_ids in contact.contact_island_ids_by_layer.items():
                    partitions = component_partitions_by_surface.get(
                        (net_key, layer.casefold())
                    )
                    if partitions is None or tuple(island_ids) not in partitions:
                        raise ValueError(
                            "terminal landing contact does not reference one exact "
                            "surface-equivalence component"
                        )

        via_aggregates = sorted_tuple_if_needed(
            self.via_island_pair_aggregates,
            key=lambda item: (
                item.net.casefold(),
                item.padstack.casefold(),
                item.start_layer.casefold(),
                item.end_layer.casefold(),
                item.start_island_id,
                item.end_island_id,
                item.net,
                item.padstack,
                item.start_layer,
                item.end_layer,
            ),
        )
        if not all(
            isinstance(item, SpdViaIslandPairAggregate)
            for item in via_aggregates
        ):
            raise ValueError("Via island-pair aggregates have an invalid record")
        aggregate_keys = {
            (
                item.net.casefold(),
                item.padstack.casefold(),
                item.start_layer.casefold(),
                item.end_layer.casefold(),
                item.start_island_id,
                item.end_island_id,
            )
            for item in via_aggregates
        }
        if len(aggregate_keys) != len(via_aggregates):
            raise ValueError("Via island-pair aggregates duplicate a physical pair")
        if component_islands_by_surface:
            for item in via_aggregates:
                start_partitions = component_partitions_by_surface.get(
                    (item.net.casefold(), item.start_layer.casefold())
                )
                end_partitions = component_partitions_by_surface.get(
                    (item.net.casefold(), item.end_layer.casefold())
                )
                if (
                    start_partitions is None
                    or item.start_component_island_ids not in start_partitions
                    or end_partitions is None
                    or item.end_component_island_ids not in end_partitions
                ):
                    raise ValueError(
                        "Via island-pair aggregate does not reference exact "
                        "surface-equivalence components"
                    )
        coverage = self.via_island_pair_coverage
        if coverage is not None:
            if not isinstance(coverage, SpdViaIslandPairCoverage):
                raise ValueError("Via island-pair coverage has an invalid record")
            if sum(item.count for item in via_aggregates) != coverage.paired_via_count:
                raise ValueError(
                    "Via island-pair aggregate counts mismatch their coverage"
                )
            if coverage.terminal_owned_ids_supplied and (
                sum(
                    int(item.terminal_owned_count or 0)
                    for item in via_aggregates
                )
                != coverage.paired_terminal_owned_count
                or sum(int(item.substrate_count or 0) for item in via_aggregates)
                != coverage.paired_substrate_count
            ):
                raise ValueError(
                    "Via island-pair aggregate ownership mismatches coverage"
                )
        finite_vertices = sorted_tuple_if_needed(
            self.finite_via_vertices,
            key=lambda item: (item.vertex_id.casefold(), item.vertex_id),
        )
        if not all(
            isinstance(item, SpdFiniteViaQuotientVertex)
            for item in finite_vertices
        ):
            raise ValueError("finite-Via quotient vertices have an invalid record")
        finite_vertex_ids = [item.vertex_id for item in finite_vertices]
        if len(set(finite_vertex_ids)) != len(finite_vertex_ids):
            raise ValueError("finite-Via quotient duplicates a vertex")
        finite_vertex_id_set = set(finite_vertex_ids)
        finite_edges = sorted_tuple_if_needed(
            self.finite_via_edges,
            key=lambda item: (item.edge_id.casefold(), item.edge_id),
        )
        if not all(
            isinstance(item, SpdFiniteViaQuotientEdge) for item in finite_edges
        ):
            raise ValueError("finite-Via quotient edges have an invalid record")
        finite_edge_ids = [item.edge_id for item in finite_edges]
        if len(set(finite_edge_ids)) != len(finite_edge_ids):
            raise ValueError("finite-Via quotient duplicates an edge")
        if any(
            item.start_vertex_id not in finite_vertex_id_set
            or item.end_vertex_id not in finite_vertex_id_set
            for item in finite_edges
        ):
            raise ValueError("finite-Via quotient edge references an unknown vertex")
        finite_owner_keys = [
            owner_id.casefold()
            for item in finite_edges
            for owner_id in item.owner_ids
        ]
        if len(set(finite_owner_keys)) != len(finite_owner_keys):
            raise ValueError(
                "finite-Via raw owner is assigned to multiple global edges"
            )
        finite_landing_bindings: dict[tuple[str, str], str] = {}
        for raw_key, raw_vertex_id in self.finite_via_vertex_id_by_landing.items():
            if len(raw_key) != 2:
                raise ValueError("finite-Via landing key is invalid")
            key = (str(raw_key[0]).casefold(), str(raw_key[1]).casefold())
            vertex_id = str(raw_vertex_id).strip()
            if not vertex_id or vertex_id not in finite_vertex_id_set:
                raise ValueError("finite-Via landing references an unknown vertex")
            finite_landing_bindings[key] = vertex_id
        finite_landing_edge_bindings: dict[tuple[str, str], str] = {}
        finite_edge_id_set = set(finite_edge_ids)
        for raw_key, raw_edge_id in self.finite_via_edge_id_by_landing.items():
            if len(raw_key) != 2:
                raise ValueError("finite-Via landing edge key is invalid")
            key = (str(raw_key[0]).casefold(), str(raw_key[1]).casefold())
            edge_id = str(raw_edge_id).strip()
            if not edge_id or edge_id not in finite_edge_id_set:
                raise ValueError("finite-Via landing references an unknown edge")
            finite_landing_edge_bindings[key] = edge_id
        finite_coverage = self.finite_via_coverage
        if finite_coverage is not None:
            if not isinstance(finite_coverage, SpdFiniteViaQuotientCoverage):
                raise ValueError("finite-Via quotient coverage has an invalid record")
            if sum(item.raw_via_count for item in finite_edges) != (
                finite_coverage.modeled_global_via_count
            ):
                raise ValueError("finite-Via edge counts mismatch their coverage")
            if len(finite_owner_keys) != finite_coverage.modeled_global_via_count:
                raise ValueError("finite-Via owner ledger mismatches its coverage")
        scenario_isolated_landing_keys = frozenset(
            (str(raw_key[0]).casefold(), str(raw_key[1]).casefold())
            for raw_key in self.finite_via_scenario_isolated_landing_keys
            if len(raw_key) == 2
        )
        if len(scenario_isolated_landing_keys) != len(
            self.finite_via_scenario_isolated_landing_keys
        ):
            raise ValueError("finite-Via scenario-isolated landing key is invalid")
        vertex_by_id = {item.vertex_id: item for item in finite_vertices}
        if any(
            key not in finite_landing_bindings
            or vertex_by_id[finite_landing_bindings[key]].source_node_count != 1
            for key in scenario_isolated_landing_keys
        ):
            raise ValueError(
                "finite-Via scenario-isolated landing is not a singleton vertex"
            )
        scenario_isolation_coverage = (
            self.finite_via_scenario_isolation_coverage
        )
        if scenario_isolation_coverage is not None:
            if not isinstance(
                scenario_isolation_coverage,
                SpdFiniteViaScenarioIsolationCoverage,
            ):
                raise ValueError(
                    "finite-Via scenario-isolation coverage has an invalid record"
                )
            if scenario_isolation_coverage.isolated_landing_count != len(
                scenario_isolated_landing_keys
            ):
                raise ValueError(
                    "finite-Via scenario-isolated landing count mismatches coverage"
                )
        finite_retarget_destination_bindings: dict[
            tuple[str, str, str], str
        ] = {}
        for raw_key, raw_vertex_id in (
            self.finite_via_vertex_id_by_retarget_destination.items()
        ):
            if len(raw_key) != 3:
                raise ValueError("finite-Via retarget-destination key is invalid")
            key = tuple(str(item).casefold() for item in raw_key)
            vertex_id = str(raw_vertex_id).strip()
            vertex = vertex_by_id.get(vertex_id)
            if (
                any(not item for item in key)
                or vertex is None
                or vertex.net.casefold() != key[0]
                or vertex.layer.casefold() != key[1]
                or "retained_surface" not in vertex.roles
            ):
                raise ValueError(
                    "finite-Via retarget destination is not a retained surface vertex"
                )
            finite_retarget_destination_bindings[key] = vertex_id
        retarget_destination_coverage = (
            self.finite_via_retarget_destination_coverage
        )
        if retarget_destination_coverage is not None:
            if not isinstance(
                retarget_destination_coverage,
                SpdFiniteViaRetargetDestinationCoverage,
            ):
                raise ValueError(
                    "finite-Via retarget-destination coverage has an invalid record"
                )
            if retarget_destination_coverage.resolved_destination_count != len(
                finite_retarget_destination_bindings
            ):
                raise ValueError(
                    "finite-Via retarget destinations mismatch their coverage"
                )
        object.__setattr__(
            self,
            "surface_islands_by_landing",
            ordered_mapping_proxy(islands_by_landing),
        )
        object.__setattr__(self, "surface_equivalence_proofs", proofs)
        object.__setattr__(
            self, "surface_equivalence_components", equivalence_components
        )
        object.__setattr__(self, "landing_surface_contacts", landing_contacts)
        object.__setattr__(self, "via_island_pair_aggregates", via_aggregates)
        object.__setattr__(self, "via_island_pair_coverage", coverage)
        object.__setattr__(self, "finite_via_vertices", finite_vertices)
        object.__setattr__(self, "finite_via_edges", finite_edges)
        object.__setattr__(
            self,
            "finite_via_vertex_id_by_landing",
            ordered_mapping_proxy(finite_landing_bindings),
        )
        object.__setattr__(
            self,
            "finite_via_edge_id_by_landing",
            ordered_mapping_proxy(finite_landing_edge_bindings),
        )
        object.__setattr__(self, "finite_via_coverage", finite_coverage)
        object.__setattr__(
            self,
            "finite_via_scenario_isolated_landing_keys",
            scenario_isolated_landing_keys,
        )
        object.__setattr__(
            self,
            "finite_via_scenario_isolation_coverage",
            scenario_isolation_coverage,
        )
        object.__setattr__(
            self,
            "finite_via_vertex_id_by_retarget_destination",
            ordered_mapping_proxy(finite_retarget_destination_bindings),
        )
        object.__setattr__(
            self,
            "finite_via_retarget_destination_coverage",
            retarget_destination_coverage,
        )

    def reaches(self, landing: object, target_layer: str) -> bool:
        return (
            str(getattr(landing, "via_id")).casefold(),
            str(getattr(landing, "endpoint_node_id")).casefold(),
            target_layer.casefold(),
        ) in self.reachable_keys


class _SpdPolygon(Sequence[tuple[float, float]]):
    """Compact immutable X/Y sequence for multi-million-vertex SPD imports."""

    __slots__ = ("_bbox", "_coordinates")

    def __init__(self, flat_coordinates_um: Iterable[float]) -> None:
        coordinates = array("d", flat_coordinates_um)
        if len(coordinates) < 6 or len(coordinates) % 2:
            raise ValueError("SPD polygon requires at least three X/Y pairs")
        self._coordinates = coordinates
        x_values = coordinates[0::2]
        y_values = coordinates[1::2]
        self._bbox = (
            min(x_values),
            max(x_values),
            min(y_values),
            max(y_values),
        )

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self._bbox

    def __len__(self) -> int:
        return len(self._coordinates) // 2

    def __iter__(self) -> Iterator[tuple[float, float]]:
        coordinates = self._coordinates
        for offset in range(0, len(coordinates), 2):
            yield coordinates[offset], coordinates[offset + 1]

    def __getitem__(
        self, index: int | slice
    ) -> tuple[float, float] | tuple[tuple[float, float], ...]:
        if isinstance(index, slice):
            return tuple(self[offset] for offset in range(*index.indices(len(self))))
        normalized = index if index >= 0 else len(self) + index
        if normalized < 0 or normalized >= len(self):
            raise IndexError(index)
        offset = normalized * 2
        return self._coordinates[offset], self._coordinates[offset + 1]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence):
            return tuple(self) == tuple(other)
        return NotImplemented

    def __repr__(self) -> str:
        return repr(tuple(self))


@dataclass(frozen=True, slots=True)
class SpdPlaneGeometry:
    """PowerSI boolean plane primitives for one layer/net assignment."""

    layer: str
    net: str
    positive_polygons_um: tuple[Sequence[tuple[float, float]], ...]
    negative_polygons_um: tuple[Sequence[tuple[float, float]], ...]
    positive_circles_um: tuple[tuple[float, float, float], ...] = ()
    negative_circles_um: tuple[tuple[float, float, float], ...] = ()
    primitive_order: tuple[tuple[str, int], ...] = ()
    positive_subelement_count: int = 0
    negative_subelement_count: int = 0
    polygon_trace_count: int = 0
    box_count: int = 0


@dataclass(frozen=True, slots=True)
class SpdAnalysis:
    source: SpdSourceInfo
    outline: MLOOutline | None
    stackup_layers: tuple[StackupLayer, ...]
    pins: tuple[PinRecord, ...]
    cap_models: dict[str, PassiveSubcircuitModel]
    cap_instances: tuple[SpdCapInstance, ...]
    padstacks: tuple[SpdPadStack, ...]
    via_usage: tuple[SpdViaUsage, ...]
    diagnostics: tuple[SpdDiagnostic, ...]
    model_assets: dict[str, str]
    counts: dict[str, int]
    power_plane_nets: tuple[str, ...] = ()
    ground_nets: tuple[str, ...] = ()
    plane_geometries: tuple[SpdPlaneGeometry, ...] = ()
    decap_connections: tuple[SpdDecapConnection, ...] = ()
    shared_pad_clusters: tuple[SpdSharedPadCluster, ...] = ()
    routing_extraction: SpdRoutingExtraction | None = None
    device_terminal_via_endpoints: tuple[
        SpdDeviceTerminalViaEndpoint, ...
    ] = ()

    @property
    def partial_models(self) -> dict[str, PassiveSubcircuitModel]:
        """Compatibility name used by the SPD-to-project service."""

        return self.cap_models

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)

    @property
    def summary_lines(self) -> tuple[str, ...]:
        return (
            f"Source: {self.source.name} ({self.source.size_bytes:,} bytes)",
            f"Stack-up: {len(self.stackup_layers)} layers; outline: "
            + (
                f"{self.outline.width_um:g} x {self.outline.height_um:g} um"
                if self.outline is not None
                else "not found"
            ),
            f"Pins: {len(self.pins)}; capacitors: {len(self.cap_instances)}; "
            f"models: {len(self.cap_models)}",
            f"Padstacks: {len(self.padstacks)}; via groups: {len(self.via_usage)}",
        )


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
SpdAnalysisScope = Literal["selected_pi", "decap_scenario"]


@dataclass(frozen=True, slots=True)
class _Part:
    name: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Component:
    refdes: str
    start_layer: str | None
    attach_layer: str | None


@dataclass(frozen=True, slots=True)
class _Port:
    pin: str
    node_id: str
    net: str | None


@dataclass(frozen=True, slots=True)
class _Connection:
    refdes: str
    part_name: str
    usage: str | None
    ports: tuple[_Port, ...]


@dataclass(frozen=True, slots=True)
class _Node:
    x_um: float
    y_um: float
    padstack: str | None
    layer: str | None
    rotation_degrees: float = 0.0
    rotation_valid: bool = True


@dataclass(frozen=True, slots=True)
class _PinCandidate:
    refdes: str
    pin: str
    node_id: str
    net: str
    kind: PinKind
    terminal: TerminalKind
    domain: str | None
    site: str | None


@dataclass(frozen=True, slots=True)
class _CapCandidate:
    refdes: str
    model_id: str | None
    footprint: str
    power_net: str
    site: str | None
    mounted: bool
    power: _PinCandidate
    ground: _PinCandidate
    source_part_name: str | None = None
    start_layer: str | None = None
    attach_layer: str | None = None


@dataclass(frozen=True, slots=True)
class _DeviceIncidentVia:
    via_id: str
    net: str
    padstack: str
    source_node_id: str
    opposite_node_id: str


class _Reporter:
    def __init__(
        self, progress: ProgressCallback | None, is_cancelled: CancelCallback | None
    ) -> None:
        self._progress = progress
        self._is_cancelled = is_cancelled
        self._last = -1

    def report(self, percent: int, message: str) -> None:
        self.check()
        value = max(self._last, min(100, max(0, int(percent))))
        if self._progress is not None and value != self._last:
            self._progress(value, message)
        self._last = value

    def check(self) -> None:
        if self._is_cancelled is not None and self._is_cancelled():
            raise SpdImportError("SPD import cancelled")


_LENGTH_RE = re.compile(
    rb"([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)"
    rb"\s*(mil|mm|um|u|m)(?![A-Za-z])",
    re.IGNORECASE,
)
_LENGTH_SCALE_BY_UNIT = {
    b"m": 1.0e6,
    b"mm": 1.0e3,
    b"u": 1.0,
    b"um": 1.0,
    b"mil": 25.4,
}
_FLOAT_RE = re.compile(
    rb"[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?"
)
_POSITIVE_POLYGON_RE = re.compile(
    rb"(?m)^Polygon[^\r\n\s]*::([^\r\n\s+]+)\+\s+"
)
_SHAPE_PRIMITIVE_RE = re.compile(
    rb"(?m)^([A-Za-z_]+)[^\r\n\s]*::(\S+?)([+-])(?:\s+|$)"
)
_SHAPE_RE = re.compile(rb"(?m)^\.Shape[ \t]+(\S+)")
_SHAPE_INDEX_CHUNK_BYTES = 8 * 1024 * 1024
_VIA_RE = re.compile(
    # ``re.MULTILINE`` recognizes LF/CRLF starts; the zero-width CR lookbehind
    # preserves the source grammar's bare-CR record framing as well.
    rb"(?m)(?:^|(?<=\r))(Via[^\r\n:]*)::([^\s]+)\s+"
    rb"UpperNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?\s+"
    rb"LowerNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?\s+"
    rb"PadStack\s*=\s*(\S+)("
    # PowerSI may place Via rotation on exactly one immediately following
    # continuation line.  Keep it inside the historical tail group (6), so
    # every existing finditer consumer sees the same attribute bytes.  The
    # leading guard rejects a duplicate same-line value, and the final guard
    # prevents a continuation chain from being partially accepted.  In either
    # case the continuation bytes remain outside the Via match.
    rb"(?:(?![^\r\n]*(?i:\bAbsoluteRotation)[ \t]*=)[^\r\n]*(?:"
    rb"(?:\r\n|\r|\n)[ \t]*\+[ \t]*AbsoluteRotation[ \t]*=[ \t]*\S+[ \t]*"
    rb"(?=\r\n|\r|\n|\Z)(?!(?:\r\n|\r|\n)[ \t]*\+)"
    rb")|[^\r\n]*)"
    rb")"
)
_TRACE_RE = re.compile(
    rb"(?m)^(Trace[^\r\n:]*)::([^\s]+)\s+"
    rb"StartingNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?\s+"
    rb"EndingNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?"
)
_TRACE_PRIMARY_RE = re.compile(
    rb"^(Trace[^\r\n:]*)::([^\s]+)\s+"
    rb"StartingNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::([^\s]+))?\s+"
    rb"EndingNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::([^\s]+))?"
    rb"(?P<tail>[^\r\n]*)$"
)
_TRACE_WIDTH_ATTRIBUTE_RE = re.compile(
    rb"\bWidth\s*=\s*(\S+)",
    re.IGNORECASE,
)
_TRACE_CONTINUATION_RE = re.compile(
    rb"^[ \t]*\+[ \t]*Width\s*=\s*(\S+)[ \t]*$",
    re.IGNORECASE,
)
_TRACE_PRIMARY_TAIL_RE = re.compile(
    rb"^[ \t]*(?:Width\s*=\s*\S+)?[ \t]*$",
    re.IGNORECASE,
)
_NODE_ATTR_RE = re.compile(
    rb"\bX\s*=\s*(\S+)\s+Y\s*=\s*(\S+).*?(?:\bPadStack\s*=\s*(\S+))?"
)


class SpdTraceRecordError(ValueError):
    """Raised when a Trace section cannot be framed without ambiguity."""

    def __init__(self, code: str, message: str, *, offset: int) -> None:
        super().__init__(message)
        self.code = code
        self.offset = offset


_TRACE_RECORD_FACTORY_TOKEN = object()
_MAX_TRACE_RECORD_BYTES = 1024 * 1024
_MAX_TRACE_RECORD_LINES = 64


@dataclass(frozen=True, slots=True)
class SpdTraceRecord:
    """One exact logical SPD Trace record and its bounded geometry status."""

    source_offset: int
    exact_bytes: bytes
    source_sha256: str
    line_count: int
    line_offsets: tuple[int, ...]
    source_id: str | None
    net: str | None
    starting_node_id: str | None
    ending_node_id: str | None
    starting_node_net_evidence: str | None
    ending_node_net_evidence: str | None
    source_tail: str
    width_pm: int | None
    width_um: float | None
    width_location: Literal["same_line", "continuation", "absent", "unresolved"]
    geometry_status: Literal["resolved", "topology_only", "unresolved"]
    issue_codes: tuple[str, ...]
    _parsed_manifest_sha256: str = field(repr=False, compare=False)
    _factory_token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._factory_token is not _TRACE_RECORD_FACTORY_TOKEN:
            raise ValueError("Trace records must be created by the bounded parser")
        if (
            isinstance(self.source_offset, bool)
            or not isinstance(self.source_offset, int)
            or self.source_offset < 0
            or isinstance(self.line_count, bool)
            or not isinstance(self.line_count, int)
            or self.line_count < 1
            or not isinstance(self.exact_bytes, bytes)
            or not self.exact_bytes
        ):
            raise ValueError("Trace record provenance is incomplete")
        if len(self.exact_bytes.splitlines(keepends=True)) != self.line_count:
            raise ValueError("Trace record physical-line cardinality is inconsistent")
        expected_offsets: list[int] = []
        offset = self.source_offset
        for line in self.exact_bytes.splitlines(keepends=True):
            expected_offsets.append(offset)
            offset += len(line)
        if self.line_offsets != tuple(expected_offsets):
            raise ValueError("Trace record physical-line offsets are inconsistent")
        if hashlib.sha256(self.exact_bytes).hexdigest() != self.source_sha256:
            raise ValueError("Trace record SHA-256 does not match its exact bytes")
        if _trace_record_manifest_sha256(self) != self._parsed_manifest_sha256:
            raise ValueError("Trace record parsed fields were tampered")
        has_identity = all(
            isinstance(item, str) and bool(item.strip())
            for item in (
                self.source_id,
                self.net,
                self.starting_node_id,
                self.ending_node_id,
            )
        )
        if self.geometry_status == "resolved":
            if (
                not has_identity
                or isinstance(self.width_pm, bool)
                or not isinstance(self.width_pm, int)
                or self.width_pm <= 0
                or not isinstance(self.width_um, float)
                or not isfinite(self.width_um)
                or self.width_um != self.width_pm / 1_000_000.0
                or self.width_location not in {"same_line", "continuation"}
                or self.issue_codes
            ):
                raise ValueError("resolved Trace record has incomplete Width evidence")
        elif self.geometry_status == "topology_only":
            if (
                not has_identity
                or self.width_pm is not None
                or self.width_um is not None
                or self.width_location != "absent"
                or self.issue_codes != ("TRACE_WIDTH_MISSING",)
            ):
                raise ValueError("topology-only Trace record status is inconsistent")
        elif self.geometry_status == "unresolved":
            if (
                self.width_pm is not None
                or self.width_um is not None
                or self.width_location != "unresolved"
                or not self.issue_codes
            ):
                raise ValueError("unresolved Trace record status is inconsistent")
        else:
            raise ValueError("Trace record geometry status is invalid")


def _trace_record_manifest_sha256(record: SpdTraceRecord | Mapping[str, object]) -> str:
    def value(name: str) -> object:
        if isinstance(record, Mapping):
            return record[name]
        return getattr(record, name)

    payload = {
        name: value(name)
        for name in (
            "source_offset",
            "source_sha256",
            "line_count",
            "line_offsets",
            "source_id",
            "net",
            "starting_node_id",
            "ending_node_id",
            "starting_node_net_evidence",
            "ending_node_net_evidence",
            "source_tail",
            "width_pm",
            "width_um",
            "width_location",
            "geometry_status",
            "issue_codes",
        )
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _line_without_ending(raw: bytes) -> bytes:
    if raw.endswith(b"\r\n"):
        return raw[:-2]
    if raw.endswith((b"\r", b"\n")):
        return raw[:-1]
    return raw


def _decimal_scaled_integer_exact(
    token: bytes | str,
    scale: int,
    *,
    label: str,
) -> int:
    """Scale one source Decimal into a bounded exact integer.

    Decimal multiplication and quantization use the ambient thread context and
    can silently round before an integrality check.  Work from the immutable
    coefficient/exponent tuple instead so the result is context-independent.
    """

    if type(scale) is not int or scale <= 0:
        raise ValueError(f"{label} scale is invalid")
    try:
        raw = token.encode("ascii", errors="strict") if isinstance(token, str) else token
    except UnicodeEncodeError as exc:
        raise ValueError(f"invalid {label} {token!r}") from exc
    stripped = raw.strip()
    if _FLOAT_RE.fullmatch(stripped) is None:
        raise ValueError(f"invalid {label} {_decode(raw)!r}")
    try:
        value = Decimal(stripped.decode("ascii"))
    except (DecimalException, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid {label} {_decode(raw)!r}") from exc
    if not value.is_finite():
        raise ValueError(f"{label} is not finite")
    sign, raw_digits, raw_exponent = value.as_tuple()
    digits = "".join(str(digit) for digit in raw_digits).lstrip("0")
    if not digits:
        return 0
    trailing_zeros = len(digits) - len(digits.rstrip("0"))
    digits = digits.rstrip("0")
    if len(digits) > 128:
        raise ValueError(f"{label} precision exceeds its exact bound")
    exponent = int(raw_exponent) + trailing_zeros
    coefficient = int(digits)
    numerator = coefficient * scale
    if exponent >= 0:
        if exponent > 19:
            raise ValueError(f"{label} is outside the signed 64-bit range")
        result = numerator * (10**exponent)
    else:
        decimal_places = -exponent
        if decimal_places > len(str(numerator)):
            raise ValueError(f"{label} is not an exact integer")
        divisor = 10**decimal_places
        if numerator % divisor:
            raise ValueError(f"{label} is not an exact integer")
        result = numerator // divisor
    if sign:
        result = -result
    if result < -(2**63) or result > 2**63 - 1:
        raise ValueError(f"{label} is outside the signed 64-bit range")
    return result


def _length_pm_exact(token: bytes | str) -> int:
    """Parse one SPD length as exact integer picometres.

    This is intentionally separate from :func:`_length_um`; existing public
    float-length behavior is unchanged.  Values below one picometre are not
    rounded into geometry evidence.
    """

    raw = token.encode("ascii", errors="strict") if isinstance(token, str) else token
    match = _LENGTH_RE.fullmatch(raw.strip())
    if match is None:
        lowered = raw.strip().lower()
        if lowered in {
            b"nan",
            b"+nan",
            b"-nan",
            b"inf",
            b"+inf",
            b"-inf",
            b"infinity",
            b"+infinity",
            b"-infinity",
        }:
            raise ValueError("SPD length is not finite")
        raise ValueError(f"invalid SPD length {_decode(raw)!r}")
    scale = {
        b"m": 1_000_000_000_000,
        b"mm": 1_000_000_000,
        b"u": 1_000_000,
        b"um": 1_000_000,
        b"mil": 25_400_000,
    }[match.group(2).lower()]
    try:
        return _decimal_scaled_integer_exact(
            match.group(1), scale, label="SPD length in picometres"
        )
    except ValueError as exc:
        if "not an exact integer" in str(exc):
            raise ValueError(
                "SPD length is not an exact integer number of picometres"
            ) from exc
        raise


def _parse_spd_trace_record(
    *,
    source_offset: int,
    lines: Sequence[bytes],
) -> SpdTraceRecord:
    exact_bytes = b"".join(lines)
    primary = _line_without_ending(lines[0])
    match = _TRACE_PRIMARY_RE.fullmatch(primary)
    issue_codes: list[str] = []
    source_id: str | None = None
    net: str | None = None
    starting_node_id: str | None = None
    ending_node_id: str | None = None
    starting_net: str | None = None
    ending_net: str | None = None
    source_tail = ""
    if match is None:
        issue_codes.append("MALFORMED_TRACE_RECORD")
        if primary.startswith(b"Trace") and b"::" in primary:
            source_id = _decode(primary.split(b"::", 1)[0]).strip() or None
            net_fields = primary.split(b"::", 1)[1].split(None, 1)
            if net_fields:
                net = _decode(net_fields[0]).strip() or None
    else:
        source_id = _decode(match.group(1))
        net = _decode(match.group(2))
        starting_node_id = _decode(match.group(3))
        starting_net = _decode(match.group(4)) if match.group(4) else None
        ending_node_id = _decode(match.group(5))
        ending_net = _decode(match.group(6)) if match.group(6) else None
        source_tail = _decode(match.group("tail"))
        if _TRACE_PRIMARY_TAIL_RE.fullmatch(match.group("tail")) is None:
            issue_codes.append("TRACE_PRIMARY_TAIL_UNKNOWN")

    width_tokens: list[bytes] = []
    primary_widths = list(_TRACE_WIDTH_ATTRIBUTE_RE.finditer(primary))
    if b"width" in primary.lower() and not primary_widths:
        issue_codes.append("TRACE_WIDTH_MALFORMED")
    width_tokens.extend(item.group(1) for item in primary_widths)
    for raw_continuation in lines[1:]:
        continuation = _line_without_ending(raw_continuation)
        continuation_match = _TRACE_CONTINUATION_RE.fullmatch(continuation)
        if continuation_match is None:
            issue_codes.append("TRACE_CONTINUATION_UNKNOWN")
        else:
            width_tokens.append(continuation_match.group(1))
            source_tail += "\n" + _decode(continuation)

    if len(width_tokens) > 1:
        issue_codes.append("TRACE_WIDTH_DUPLICATE")
    width_pm: int | None = None
    width_um: float | None = None
    width_location: Literal["same_line", "continuation", "absent", "unresolved"]
    if len(width_tokens) == 1 and not issue_codes:
        try:
            width_pm = _length_pm_exact(width_tokens[0])
        except ValueError as exc:
            code = (
                "TRACE_WIDTH_NONFINITE"
                if "not finite" in str(exc)
                else "TRACE_WIDTH_INVALID"
            )
            issue_codes.append(code)
        else:
            if width_pm <= 0:
                issue_codes.append("TRACE_WIDTH_NONPOSITIVE")
                width_pm = None
            else:
                try:
                    width_um = width_pm / 1_000_000.0
                except OverflowError:
                    issue_codes.append("TRACE_WIDTH_INVALID")
                    width_pm = None
                else:
                    if not isfinite(width_um) or width_um <= 0.0:
                        issue_codes.append("TRACE_WIDTH_INVALID")
                        width_pm = None
                        width_um = None

    issue_codes = list(dict.fromkeys(issue_codes))
    if issue_codes:
        status: Literal["resolved", "topology_only", "unresolved"] = "unresolved"
        width_pm = None
        width_um = None
        width_location = "unresolved"
    elif width_pm is None:
        status = "topology_only"
        issue_codes.append("TRACE_WIDTH_MISSING")
        width_location = "absent"
    else:
        status = "resolved"
        width_location = "same_line" if primary_widths else "continuation"
    line_offsets: list[int] = []
    line_offset = source_offset
    for line in lines:
        line_offsets.append(line_offset)
        line_offset += len(line)
    record_values: dict[str, object] = {
        "source_offset": source_offset,
        "exact_bytes": exact_bytes,
        "source_sha256": hashlib.sha256(exact_bytes).hexdigest(),
        "line_count": len(lines),
        "line_offsets": tuple(line_offsets),
        "source_id": source_id,
        "net": net,
        "starting_node_id": starting_node_id,
        "ending_node_id": ending_node_id,
        "starting_node_net_evidence": starting_net,
        "ending_node_net_evidence": ending_net,
        "source_tail": source_tail,
        "width_pm": width_pm,
        "width_um": width_um,
        "width_location": width_location,
        "geometry_status": status,
        "issue_codes": tuple(issue_codes),
    }
    return SpdTraceRecord(
        **record_values,
        _parsed_manifest_sha256=_trace_record_manifest_sha256(record_values),
        _factory_token=_TRACE_RECORD_FACTORY_TOKEN,
    )


def _iter_spd_trace_records(handle: object, start: int, end: int):
    """Yield bounded logical Trace records with their exact original bytes."""

    file_handle = handle
    file_handle.seek(start)

    def bounded_readline(offset: int, budget: int) -> bytes:
        remaining = max(0, end - offset)
        raw = file_handle.readline(min(remaining, budget + 1))
        carriage = raw.find(b"\r")
        newline = raw.find(b"\n")
        if carriage >= 0 and (newline < 0 or carriage < newline):
            stop = carriage + 1
            if newline == stop:
                stop += 1
            if stop < len(raw):
                file_handle.seek(offset + stop)
                raw = raw[:stop]
        if len(raw) > budget:
            raise SpdTraceRecordError(
                "TRACE_RECORD_BYTE_BOUND_EXCEEDED",
                f"Trace physical/logical record exceeds {_MAX_TRACE_RECORD_BYTES} "
                f"bytes at byte offset {offset}",
                offset=offset,
            )
        return raw

    while file_handle.tell() < end:
        offset = file_handle.tell()
        raw = bounded_readline(offset, _MAX_TRACE_RECORD_BYTES)
        if not raw or offset >= end:
            return
        if offset + len(raw) > end:
            raw = raw[: end - offset]
        physical = _line_without_ending(raw)
        if physical.lstrip().startswith(b"+"):
            raise SpdTraceRecordError(
                "ORPHAN_TRACE_CONTINUATION",
                f"orphan Trace continuation at byte offset {offset}",
                offset=offset,
            )
        if not physical.startswith(b"Trace"):
            continue
        lines = [raw]
        while file_handle.tell() < end:
            continuation_offset = file_handle.tell()
            record_bytes = sum(len(item) for item in lines)
            candidate = bounded_readline(continuation_offset, _MAX_TRACE_RECORD_BYTES)
            if not candidate:
                break
            if continuation_offset + len(candidate) > end:
                candidate = candidate[: end - continuation_offset]
            if not _line_without_ending(candidate).lstrip().startswith(b"+"):
                file_handle.seek(continuation_offset)
                break
            if record_bytes + len(candidate) > _MAX_TRACE_RECORD_BYTES:
                raise SpdTraceRecordError(
                    "TRACE_RECORD_BYTE_BOUND_EXCEEDED",
                    f"Trace logical record exceeds {_MAX_TRACE_RECORD_BYTES} bytes "
                    f"at byte offset {offset}",
                    offset=offset,
                )
            if len(lines) >= _MAX_TRACE_RECORD_LINES:
                raise SpdTraceRecordError(
                    "TRACE_RECORD_LINE_BOUND_EXCEEDED",
                    f"Trace logical record exceeds {_MAX_TRACE_RECORD_LINES} physical "
                    f"lines at byte offset {offset}",
                    offset=offset,
                )
            lines.append(candidate)
        yield _parse_spd_trace_record(source_offset=offset, lines=lines)


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _line_end(data: mmap.mmap, start: int, end: int) -> int:
    found = data.find(b"\n", start, end)
    return end if found < 0 else found


def _iter_lines(data: mmap.mmap, start: int, end: int):
    position = max(0, start)
    if position and data[position - 1 : position] not in {b"\n", b"\r"}:
        newline = data.find(b"\n", position, end)
        position = end if newline < 0 else newline + 1
    while position < end:
        newline = data.find(b"\n", position, end)
        stop = end if newline < 0 else newline
        raw = data[position:stop]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        yield position, raw
        if newline < 0:
            break
        position = newline + 1


def _iter_line_bounded_chunks(
    data: mmap.mmap,
    start: int,
    end: int,
    *,
    chunk_bytes: int | None = None,
):
    """Yield bounded mmap spans whose internal boundaries follow newlines.

    Regex searches on one very large Shape section can monopolize the import
    worker long enough that UI cancellation/heartbeat checks cannot run.  Shape
    headers are single-line records, so splitting only after ``\\n`` preserves
    their byte offsets and CRLF/LF semantics.  A pathological line longer than
    the nominal chunk is kept intact rather than truncating a header.
    """

    chunk_bytes = _SHAPE_INDEX_CHUNK_BYTES if chunk_bytes is None else chunk_bytes
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    position = max(0, start)
    while position < end:
        limit = min(end, position + chunk_bytes)
        if limit == end:
            stop = end
        else:
            newline = data.rfind(b"\n", position, limit)
            if newline < position:
                newline = data.find(b"\n", limit, end)
            stop = end if newline < 0 else newline + 1
        yield position, stop
        position = stop


def _iter_shape_headers(
    data: mmap.mmap,
    start: int,
    end: int,
    reporter: _Reporter,
):
    """Yield Shape-header regex matches with bounded cancellation latency."""

    for chunk_start, chunk_end in _iter_line_bounded_chunks(data, start, end):
        reporter.check()
        yield from _SHAPE_RE.finditer(data, chunk_start, chunk_end)


def _find_line(data: mmap.mmap, prefix: bytes, start: int = 0, end: int | None = None) -> int:
    stop = len(data) if end is None else end
    candidate = max(0, start)
    if (
        candidate < stop
        and (candidate == 0 or data[candidate - 1 : candidate] in {b"\n", b"\r"})
        and candidate + len(prefix) <= stop
        and data[candidate : candidate + len(prefix)] == prefix
    ):
        return candidate
    for chunk_start, chunk_end in _iter_line_bounded_chunks(
        data, candidate, stop
    ):
        # A chunk begins immediately after a newline, so direct prefix scans
        # cannot lose a line-start match at a chunk boundary.  Check the byte
        # before each candidate rather than searching only ``b"\\n" + prefix``:
        # this also preserves CR-only source lines and makes the explicit
        # ``end`` bound apply to every matched prefix.
        search = max(candidate, chunk_start)
        while search < chunk_end:
            found = data.find(prefix, search, chunk_end)
            if found < 0:
                break
            if found == 0 or data[found - 1 : found] in {b"\n", b"\r"}:
                return found
            search = found + max(1, len(prefix))
    return -1


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _length_um(token: bytes | str) -> float:
    raw = token.encode("ascii", errors="ignore") if isinstance(token, str) else token
    match = _LENGTH_RE.fullmatch(raw.strip())
    if match is None:
        raise ValueError(f"invalid SPD length {_decode(raw)!r}")
    return _length_match_um(match)


def _length_match_um(match: re.Match[bytes]) -> float:
    """Convert an already matched length token without a second regex pass.

    Shape and padstack records contain millions of length tokens.  The old
    ``_lengths`` implementation called ``_length_um`` for each regex match,
    which immediately ran ``fullmatch`` again over the same bytes.  Keeping
    this helper separate preserves the strict validation used by
    ``_length_um`` while letting bulk parsing reuse the original match.
    """

    value = float(match.group(1))
    unit = match.group(2).lower()
    scale = _LENGTH_SCALE_BY_UNIT[unit]
    result = value * scale
    if not isfinite(result):
        raise ValueError("SPD length is not finite")
    return result


def _lengths(raw: bytes) -> list[float]:
    return [_length_match_um(match) for match in _LENGTH_RE.finditer(raw)]


def _attribute(raw: bytes, name: bytes) -> bytes | None:
    match = re.search(rb"\b" + re.escape(name) + rb"\s*=\s*(\S+)", raw, re.IGNORECASE)
    return None if match is None else match.group(1)


def _span_percent(position: int, start: int, end: int, low: int, high: int) -> int:
    if end <= start:
        return high
    return low + int((high - low) * (position - start) / (end - start))


def _parse_netlist(
    data: mmap.mmap,
    gnd_keys: set[str],
    diagnostics: list[SpdDiagnostic],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    start = _find_line(data, b".NetList")
    if start < 0:
        diagnostics.append(
            SpdDiagnostic("info", "NETLIST_MISSING", "No .NetList selection was found; positive plane polygons will be used as the rail filter.")
        )
        return (), ()
    end = _find_line(data, b".EndNetList", start)
    if end < 0:
        end = len(data)
        diagnostics.append(
            SpdDiagnostic("warning", "NETLIST_UNTERMINATED", ".NetList has no .EndNetList; parsed selections through end of file.")
        )
    # PowerSI writes .NetList as two ordered groups. Only the first row in a
    # group is guaranteed to contain ``-> GroundNets`` or ``-> PowerNets``;
    # later rows inherit that classification. Lines before the first group
    # marker are document properties or ordinary signal nets and must not be
    # promoted to editable power rails.
    _ = gnd_keys  # Kept in the signature for compatibility with older callers.
    power: list[str] = []
    ground: list[str] = []
    active_group: str | None = None
    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        if not stripped or stripped.startswith((b".", b"*")):
            continue
        token = stripped.split(None, 1)[0]
        if token.lower() in {b"powernets", b"groundnets"} or token.startswith(b"::"):
            continue

        # Production files may encode selection metadata on the destination,
        # e.g. ``VDD -> PowerNets::Unselected||DropShape``. The marker still
        # starts the PowerNets group and does not deselect the source token.
        if b"->" in stripped:
            tail = stripped.split(b"->", 1)[1].split(None, 1)
            destination = (tail[0] if tail else b"").lower()
            destination = destination.split(b"::", 1)[0]
            if destination == b"groundnets":
                active_group = "ground"
            elif destination == b"powernets":
                active_group = "power"

        if active_group is None or b"::unselected" in token.lower():
            continue

        name = _decode(token)
        if active_group == "ground":
            ground.append(name)
        else:
            power.append(name)
    selected_power, selected_ground = _unique(power), _unique(ground)
    if selected_power or selected_ground:
        diagnostics.append(
            SpdDiagnostic("info", "NETLIST_SELECTION_USED", f"Using {len(selected_power)} selected power net(s) and {len(selected_ground)} selected ground net(s) from .NetList.")
        )
    return selected_power, selected_ground


def _parse_shapes(
    data: mmap.mmap,
    start: int,
    end: int,
    selected_keys: set[str],
    geometry_keys: set[str] | None,
    reporter: _Reporter,
    diagnostics: list[SpdDiagnostic],
) -> tuple[
    MLOOutline | None,
    dict[str, tuple[str, ...]],
    tuple[str, ...],
    tuple[SpdPlaneGeometry, ...],
]:
    shape_matches = list(_iter_shape_headers(data, start, end, reporter))
    shape_offsets = [item.start() for item in shape_matches]
    shape_names = [_decode(item.group(1)) for item in shape_matches]
    by_layer: dict[str, list[str]] = {}
    all_nets: list[str] = []
    best_bbox: tuple[float, float, float, float] | None = None
    best_area = -1.0
    geometry: dict[
        tuple[str, str],
        dict[str, object],
    ] = {}
    unsupported_seen: set[tuple[str, str, str]] = set()
    malformed_seen: set[tuple[str, str, str]] = set()
    polygon_kinds = {b"Polygon", b"PolygonTrace"}
    supported_kinds = {*polygon_kinds, b"Circle", b"Box"}
    for index, match in enumerate(_SHAPE_PRIMITIVE_RE.finditer(data, start, end)):
        if index % 128 == 0:
            reporter.report(
                _span_percent(match.start(), start, end, 15, 29),
                "Scanning selected plane geometry",
            )
        line_end = _line_end(data, match.start(), end)
        first_line = data[match.start():line_end]
        primitive_kind = match.group(1)
        net = _decode(match.group(2))
        polarity = match.group(3)
        is_sub_element = b"Sub-element" in first_line
        shape_index = bisect_right(shape_offsets, match.start()) - 1
        layer: str | None = None
        if shape_index >= 0:
            layer = shape_names[shape_index]
            if layer.casefold().endswith("pkgshape"):
                layer = layer[: -len("pkgshape")]
        if polarity == b"+" and not is_sub_element:
            all_nets.append(net)
            if layer is not None:
                by_layer.setdefault(layer.casefold(), []).append(net)

        if primitive_kind not in supported_kinds:
            if (
                layer is not None
                and (
                    geometry_keys is None
                    or net.casefold() in geometry_keys
                )
            ):
                unsupported_key = (
                    layer.casefold(),
                    net.casefold(),
                    _decode(primitive_kind).casefold(),
                )
                if unsupported_key not in unsupported_seen:
                    unsupported_seen.add(unsupported_key)
                    diagnostics.append(
                        SpdDiagnostic(
                            "error",
                            "SPD_PLANE_PRIMITIVE_UNSUPPORTED",
                            f"Unsupported {_decode(primitive_kind)} primitive on "
                            f"{layer}/{net} was not imported; Apply is blocked because "
                            "the selected PWR geometry would be incomplete.",
                        )
                    )
            continue

        retain_geometry = bool(
            layer is not None
            and (
                (
                    geometry_keys is None
                    and (not selected_keys or net.casefold() in selected_keys)
                )
                or (
                    geometry_keys is not None
                    and net.casefold() in geometry_keys
                )
            )
        )
        if retain_geometry:
            key = (layer.casefold(), net.casefold())
            entry = geometry.setdefault(
                key,
                {
                    "layer": layer,
                    "net": net,
                    "positive": [],
                    "negative": [],
                    "positive_circles": [],
                    "negative_circles": [],
                    "order": [],
                    "positive_sub": 0,
                    "negative_sub": 0,
                    "polygon_trace_count": 0,
                    "box_count": 0,
                },
            )
        elif is_sub_element or polarity != b"+":
            continue

        values: list[float] = []
        values.extend(_lengths(first_line))
        cursor = line_end + 1
        while primitive_kind in polygon_kinds and cursor < end:
            continuation_end = _line_end(data, cursor, end)
            continuation = data[cursor:continuation_end]
            if not continuation.lstrip().startswith(b"+"):
                break
            values.extend(_lengths(continuation))
            cursor = continuation_end + 1
        malformed_reason: str | None = None
        if primitive_kind == b"Circle":
            if len(values) != 3 or values[2] <= 0:
                malformed_reason = "Circle requires center X/Y and one positive radius"
            else:
                x_um, y_um, radius_um = values
        elif primitive_kind == b"Box":
            if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
                malformed_reason = "Box requires center X/Y and positive width/height"
            else:
                x_um, y_um, width_um, height_um = values
                half_width = width_um / 2.0
                half_height = height_um / 2.0
                values = [
                    x_um - half_width,
                    y_um - half_height,
                    x_um + half_width,
                    y_um - half_height,
                    x_um + half_width,
                    y_um + half_height,
                    x_um - half_width,
                    y_um + half_height,
                ]
        elif len(values) < 6 or len(values) % 2:
            malformed_reason = (
                f"{_decode(primitive_kind)} requires at least three finite X/Y pairs"
            )

        if malformed_reason is not None:
            if retain_geometry and layer is not None:
                malformed_key = (
                    layer.casefold(),
                    net.casefold(),
                    _decode(primitive_kind).casefold(),
                )
                if malformed_key not in malformed_seen:
                    malformed_seen.add(malformed_key)
                    diagnostics.append(
                        SpdDiagnostic(
                            "error",
                            "SPD_PLANE_PRIMITIVE_MALFORMED",
                            f"Malformed {_decode(primitive_kind)} primitive on "
                            f"{layer}/{net}: {malformed_reason}. Apply is blocked.",
                        )
                    )
            continue

        if retain_geometry and is_sub_element:
            count_key = "positive_sub" if polarity == b"+" else "negative_sub"
            entry[count_key] = int(entry[count_key]) + 1

        if primitive_kind == b"Circle":
            if polarity == b"+" and not is_sub_element:
                bbox = (
                    x_um - radius_um,
                    x_um + radius_um,
                    y_um - radius_um,
                    y_um + radius_um,
                )
                area = (2.0 * radius_um) ** 2
                if area > best_area:
                    best_bbox, best_area = bbox, area
            if retain_geometry:
                circle_key = (
                    "positive_circles"
                    if polarity == b"+"
                    else "negative_circles"
                )
                circles = entry[circle_key]
                assert isinstance(circles, list)
                primitive_order = entry["order"]
                assert isinstance(primitive_order, list)
                primitive_order.append(
                    (
                        "positive_circle"
                        if polarity == b"+"
                        else "negative_circle",
                        len(circles),
                    )
                )
                circles.append((x_um, y_um, radius_um))
        else:
            if polarity == b"+" and not is_sub_element:
                xs, ys = values[0::2], values[1::2]
                bbox = min(xs), max(xs), min(ys), max(ys)
                area = (bbox[1] - bbox[0]) * (bbox[3] - bbox[2])
                if area > best_area:
                    best_bbox, best_area = bbox, area
            if retain_geometry:
                polygon = _SpdPolygon(values)
                polygon_key = "positive" if polarity == b"+" else "negative"
                polygons = entry[polygon_key]
                assert isinstance(polygons, list)
                primitive_order = entry["order"]
                assert isinstance(primitive_order, list)
                primitive_order.append(
                    (
                        "positive_polygon"
                        if polarity == b"+"
                        else "negative_polygon",
                        len(polygons),
                    )
                )
                polygons.append(polygon)
                if primitive_kind == b"PolygonTrace":
                    entry["polygon_trace_count"] = (
                        int(entry["polygon_trace_count"]) + 1
                    )
                elif primitive_kind == b"Box":
                    entry["box_count"] = int(entry["box_count"]) + 1

    # Stack-up occupancy is a safety boundary, not a rail-selection list.
    # Preserve every non-subelement positive NET seen on each conductor so a
    # mixed reference layer can never be normalized into a pure GND layer by
    # the selected-PWR filter.  ``geometry_keys`` still bounds exact artwork
    # retention and explicit .NetList PowerNets still governs rail creation.
    filtered: dict[str, tuple[str, ...]] = {
        key: _unique(nets) for key, nets in by_layer.items()
    }
    outline = None
    if best_bbox is not None and best_bbox[1] > best_bbox[0] and best_bbox[3] > best_bbox[2]:
        outline = MLOOutline(
            width_um=best_bbox[1] - best_bbox[0],
            height_um=best_bbox[3] - best_bbox[2],
            origin_x_um=best_bbox[0],
            origin_y_um=best_bbox[2],
        )
    else:
        diagnostics.append(
            SpdDiagnostic("error", "OUTLINE_NOT_FOUND", "No positive Shape polygon with a usable coordinate bounding box was found.")
        )
    nets = _unique(all_nets)
    if not nets:
        diagnostics.append(
            SpdDiagnostic("error", "PLANE_NETS_NOT_FOUND", "No positive Shape polygon nets were found; signal pins cannot be filtered safely.")
        )
    plane_geometries = tuple(
        SpdPlaneGeometry(
            layer=str(entry["layer"]),
            net=str(entry["net"]),
            positive_polygons_um=tuple(entry["positive"]),
            negative_polygons_um=tuple(entry["negative"]),
            positive_circles_um=tuple(entry["positive_circles"]),
            negative_circles_um=tuple(entry["negative_circles"]),
            primitive_order=tuple(entry["order"]),
            positive_subelement_count=int(entry["positive_sub"]),
            negative_subelement_count=int(entry["negative_sub"]),
            polygon_trace_count=int(entry["polygon_trace_count"]),
            box_count=int(entry["box_count"]),
        )
        for _key, entry in sorted(geometry.items())
        if entry["positive"] or entry["positive_circles"]
    )
    circle_count = sum(
        len(item.positive_circles_um) + len(item.negative_circles_um)
        for item in plane_geometries
    )
    if circle_count:
        diagnostics.append(
            SpdDiagnostic(
                "info",
                "SPD_CIRCLE_RADIUS_INTERPRETATION",
                f"Interpreted the third length in {circle_count:,} selected Circle "
                "record(s) as radius, consistent with PowerSI pad/via circle records.",
            )
        )
    polygon_trace_count = sum(item.polygon_trace_count for item in plane_geometries)
    box_count = sum(item.box_count for item in plane_geometries)
    if polygon_trace_count:
        diagnostics.append(
            SpdDiagnostic(
                "info",
                "SPD_POLYGON_TRACE_NORMALIZED",
                f"Normalized {polygon_trace_count:,} selected PolygonTrace record(s) "
                "as ordered Polygon boundaries.",
            )
        )
    if box_count:
        diagnostics.append(
            SpdDiagnostic(
                "info",
                "SPD_BOX_CENTER_SIZE_INTERPRETATION",
                f"Normalized {box_count:,} selected Box record(s) as rectangles, "
                "interpreting the four lengths as center X/Y and width/height.",
            )
        )
    return outline, filtered, nets, plane_geometries


def _frequency_scale(header: bytes) -> float | None:
    folded = header.lower()
    if b"frequency" not in folded:
        return None
    if b"ghz" in folded:
        return 1.0e9
    if b"mhz" in folded:
        return 1.0e6
    if b"khz" in folded:
        return 1.0e3
    return 1.0


def _parse_materials(
    data: mmap.mmap, start: int, end: int, diagnostics: list[SpdDiagnostic]
) -> tuple[dict[str, _DielectricMaterial], dict[str, float]]:
    dielectrics: dict[str, _DielectricMaterial] = {}
    metals: dict[str, float] = {}
    kind: str | None = None
    name = ""
    rows: list[tuple[float, ...]] = []
    scale: float | None = None

    def flush() -> None:
        nonlocal kind, name, rows, scale
        if not kind or not name or not rows:
            kind, name, rows, scale = None, "", [], None
            return
        if scale is not None:
            valid = [row for row in rows if row[0] > 0]
            chosen = min(valid or rows, key=lambda row: abs(log10(max(row[0] * scale, 1e-300) / 1.0e9)))
        else:
            chosen = min(rows, key=lambda row: abs(row[0] - 20.0))
        if kind == "dielectric" and len(chosen) >= 3:
            properties: tuple[DielectricPropertyPoint, ...] = ()
            if scale is not None:
                raw_properties = [
                    (row[0] * scale, row[1], row[2])
                    for row in rows
                    if len(row) >= 3
                    and row[0] > 0.0
                    and row[1] > 0.0
                    and row[2] >= 0.0
                ]
                raw_properties.sort(key=lambda row: row[0])
                frequencies = [row[0] for row in raw_properties]
                if len(raw_properties) != len(rows) or len(frequencies) != len(set(frequencies)):
                    diagnostics.append(
                        SpdDiagnostic(
                            "warning",
                            "DIELECTRIC_MODEL_TABLE_INVALID",
                            f"Dielectric model {name!r} has invalid or duplicate frequency rows; "
                            "its frequency table was not retained.",
                        )
                    )
                else:
                    properties = tuple(
                        DielectricPropertyPoint(
                            frequency_hz=frequency_hz, dk=dk, df=df
                        )
                        for frequency_hz, dk, df in raw_properties
                    )
            dielectrics[name.casefold()] = _DielectricMaterial(
                nominal_dk=chosen[1], nominal_df=chosen[2], properties=properties
            )
        elif kind == "metal" and len(chosen) >= 2:
            metals[name.casefold()] = chosen[1]
        kind, name, rows, scale = None, "", [], None

    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        folded = stripped.lower()
        if folded.startswith(b".dielectricmodel "):
            flush()
            kind, name = "dielectric", _decode(stripped.split(None, 1)[1])
        elif folded.startswith(b".metalmodel "):
            flush()
            kind, name = "metal", _decode(stripped.split(None, 1)[1])
        elif folded.startswith((b".enddielectricmodel", b".endmetalmodel")):
            flush()
        elif kind and stripped.startswith(b"*"):
            detected = _frequency_scale(stripped)
            if detected is not None:
                scale = detected
        elif kind and stripped and stripped[:1] in b"+-.0123456789":
            try:
                numbers = tuple(float(value) for value in _FLOAT_RE.findall(stripped))
            except ValueError:
                continue
            if numbers and all(isfinite(value) for value in numbers):
                rows.append(numbers)
    flush()
    if not dielectrics:
        diagnostics.append(SpdDiagnostic("warning", "DIELECTRIC_MODELS_MISSING", "No usable .DielectricModel property table was found."))
    if not metals:
        diagnostics.append(SpdDiagnostic("warning", "METAL_MODELS_MISSING", "No usable .MetalModel conductivity table was found."))
    return dielectrics, metals


def _parse_layers(
    data: mmap.mmap,
    start: int,
    end: int,
    layer_nets: dict[str, tuple[str, ...]],
    dielectrics: dict[str, _DielectricMaterial],
    metals: dict[str, float],
    selected_keys: set[str],
    diagnostics: list[SpdDiagnostic],
) -> tuple[StackupLayer, ...]:
    result: list[StackupLayer] = []
    seen: set[str] = set()
    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        if not stripped or stripped.startswith((b"*", b"+", b".")) or b"Thickness" not in stripped:
            continue
        match = re.match(rb"(\S+)\s+Thickness\s*=\s*(\S+)(.*)$", stripped, re.IGNORECASE)
        if match is None:
            continue
        name = _decode(match.group(1))
        key = name.casefold()
        if key in seen:
            diagnostics.append(SpdDiagnostic("warning", "DUPLICATE_LAYER", f"Duplicate layer {name!r} was skipped."))
            continue
        try:
            thickness = _length_um(match.group(2))
        except ValueError as exc:
            diagnostics.append(SpdDiagnostic("warning", "LAYER_THICKNESS_INVALID", f"Layer {name!r} was skipped: {exc}."))
            continue
        material_raw = _attribute(stripped, b"Material")
        material = _decode(material_raw) if material_raw else ""
        material_key = material.casefold()
        conductor = key.startswith(("signal$", "power$", "conductor$")) or material_key in metals
        conductivity_raw = _attribute(stripped, b"Conductivity")
        conductivity = float(conductivity_raw) if conductivity_raw else metals.get(material_key)
        if not conductor:
            conductivity = None
        permittivity_raw = _attribute(stripped, b"Permittivity")
        loss_raw = _attribute(stripped, b"LossTangent")
        dk = float(permittivity_raw) if permittivity_raw else None
        df = float(loss_raw) if loss_raw else None
        material_model = dielectrics.get(material_key)
        if (dk is None or df is None) and material_model is not None:
            dk = material_model.nominal_dk if dk is None else dk
            df = material_model.nominal_df if df is None else df
        dielectric_properties: list[DielectricPropertyPoint] = []
        if not conductor and material_model is not None:
            # Layer attributes are explicit source overrides and therefore win
            # independently over the corresponding material-table axis.  A
            # one-axis override keeps the other material curve; two overrides
            # are exactly the legacy scalar model and need no attached table.
            if not (permittivity_raw and loss_raw):
                dielectric_properties = [
                    DielectricPropertyPoint(
                        frequency_hz=item.frequency_hz,
                        dk=float(dk) if permittivity_raw else item.dk,
                        df=float(df) if loss_raw else item.df,
                    )
                    for item in material_model.properties
                ]
        nets = list(layer_nets.get(key, ())) if conductor else []
        if conductor and not nets:
            parenthesized = re.search(r"\(([^)]+)\)", name)
            if parenthesized and (
                not selected_keys or parenthesized.group(1).casefold() in selected_keys
            ):
                nets.append(parenthesized.group(1))
        if conductor and conductivity is None:
            diagnostics.append(SpdDiagnostic("warning", "LAYER_CONDUCTIVITY_MISSING", f"Conductor layer {name!r} references material {material!r} without a usable conductivity table."))
        try:
            result.append(
                StackupLayer(
                    name=name,
                    thickness_um=thickness,
                    conductivity_s_m=conductivity,
                    dk=dk,
                    df=df,
                    material=material or None,
                    dielectric_properties=dielectric_properties,
                    pwr_nets=nets,
                )
            )
            seen.add(key)
        except ValueError as exc:
            diagnostics.append(SpdDiagnostic("warning", "LAYER_INVALID", f"Layer {name!r} was skipped: {exc}."))
    if not result:
        diagnostics.append(SpdDiagnostic("error", "STACKUP_NOT_FOUND", "No valid Thickness layer rows were found in the SPD layer section."))
    return tuple(result)


def _first_conductor_layer_name(
    data: mmap.mmap,
    start: int,
    end: int,
    metals: Mapping[str, float],
) -> str | None:
    """Read only the first conductor identity before the geometry pass.

    The full stack-up is still built after shape NET indexing.  This lightweight
    pre-read lets scenario imports retain configured GND geometry on TOP only,
    avoiding both an all-layer GND import and a second shape scan.
    """

    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        if (
            not stripped
            or stripped.startswith((b"*", b"+", b"."))
            or b"Thickness" not in stripped
        ):
            continue
        match = re.match(
            rb"(\S+)\s+Thickness\s*=\s*(\S+)(.*)$",
            stripped,
            re.IGNORECASE,
        )
        if match is None:
            continue
        name = _decode(match.group(1))
        material_raw = _attribute(stripped, b"Material")
        material_key = _decode(material_raw).casefold() if material_raw else ""
        if name.casefold().startswith(("signal$", "power$", "conductor$")) or (
            material_key in metals
        ):
            return name
    return None


def _parse_padstacks(
    data: mmap.mmap, start: int, end: int, diagnostics: list[SpdDiagnostic]
) -> tuple[SpdPadStack, ...]:
    result: list[SpdPadStack] = []
    name: str | None = None
    drill: float | None = None
    width: float | None = None
    height: float | None = None
    layers: list[str] = []
    shapes: list[SpdPadShape] = []
    active_layer: str | None = None
    variable = False
    material: str | None = None

    def flush() -> None:
        nonlocal name, drill, width, height, layers, shapes, active_layer, variable, material
        if name is not None:
            result.append(
                SpdPadStack(
                    name,
                    drill,
                    width,
                    height,
                    _unique(layers),
                    tuple(shapes),
                    material,
                )
            )
            if variable:
                diagnostics.append(SpdDiagnostic("info", "PADSTACK_VARIABLE_PAD", f"Padstack {name!r} has layer-dependent pad sizes; maximum width/height were retained."))
        name, drill, width, height, layers, shapes, active_layer, variable, material = (
            None,
            None,
            None,
            None,
            [],
            [],
            None,
            False,
            None,
        )

    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        folded = stripped.lower()
        if folded.startswith(b".padstackdef "):
            flush()
            tokens = stripped.split()
            name = _decode(tokens[1]) if len(tokens) > 1 else ""
            values = _lengths(b" ".join(tokens[2:]))
            drill = 2.0 * values[0] if values else None
            material_raw = _attribute(stripped, b"Material")
            material = _decode(material_raw) if material_raw else None
        elif folded.startswith(b".endpadstackdef"):
            flush()
        elif name is not None and folded.startswith(b".paddef "):
            tokens = stripped.split(None, 1)
            if len(tokens) == 2:
                active_layer = _decode(tokens[1])
                layers.append(active_layer)
        elif name is not None and folded.startswith(b".endpaddef"):
            active_layer = None
        elif name is not None and folded.startswith(b"regular "):
            values = _lengths(stripped)
            candidate: tuple[float, float] | None = None
            shape_kind: Literal["CIRCLE", "RECTANGLE", "UNSUPPORTED"]
            reason: str | None = None
            if folded.startswith(b"regular circle") and values:
                candidate = (2.0 * values[0], 2.0 * values[0])
                shape_kind = "CIRCLE"
            elif folded.startswith(b"regular square") and values:
                candidate = (values[0], values[0])
                shape_kind = "RECTANGLE"
            elif folded.startswith(b"regular box") and len(values) >= 2:
                candidate = (values[0], values[1])
                shape_kind = "RECTANGLE"
            else:
                shape_kind = "UNSUPPORTED"
                reason = (
                    f"padstack {name!r} uses unsupported or malformed "
                    f"{_decode(stripped.split(None, 2)[1]) if len(stripped.split(None, 2)) > 1 else 'Regular'} geometry"
                )
            if active_layer is not None:
                shapes.append(
                    SpdPadShape(
                        layer=active_layer,
                        kind=shape_kind,
                        width_um=candidate[0] if candidate is not None else None,
                        height_um=candidate[1] if candidate is not None else None,
                        reason=reason,
                    )
                )
            if candidate is not None:
                if width is not None and (candidate[0] != width or candidate[1] != height):
                    variable = True
                width = candidate[0] if width is None else max(width, candidate[0])
                height = candidate[1] if height is None else max(height, candidate[1])
    flush()
    if result:
        diagnostics.append(SpdDiagnostic("info", "PADSTACK_RADII_CONVERTED", "PadStackDef drill values and Regular Circle values were interpreted as radii and converted to diameters."))
    return tuple(result)


def _parse_partial_circuits(
    data: mmap.mmap,
    start: int,
    end: int,
    frequencies: tuple[float, ...],
    source_name: str,
    reporter: _Reporter,
    diagnostics: list[SpdDiagnostic],
) -> tuple[dict[str, PassiveSubcircuitModel], dict[str, str], dict[str, str], set[str], int]:
    models: dict[str, PassiveSubcircuitModel] = {}
    assets: dict[str, str] = {}
    canonical: dict[str, str] = {}
    empty_names: set[str] = set()
    total = 0
    position = start
    while position < end:
        block_start = _find_line(data, b".PartialCkt", position, end)
        if block_start < 0:
            break
        total += 1
        first_end = _line_end(data, block_start, end)
        header_chunks = [data[block_start:first_end].strip()]
        body_start = first_end + 1
        while body_start < end:
            next_end = _line_end(data, body_start, end)
            continuation = data[body_start:next_end].strip()
            if not continuation.startswith(b"+"):
                break
            header_chunks.append(continuation[1:].strip())
            body_start = next_end + 1
        block_end = _find_line(data, b".EndPartialCkt", body_start, end)
        if block_end < 0:
            diagnostics.append(SpdDiagnostic("error", "PARTIAL_CKT_UNTERMINATED", f"PartialCkt beginning near byte {block_start:,} has no .EndPartialCkt."))
            break
        end_line = _line_end(data, block_end, end)
        header = b" ".join(header_chunks)
        match = re.match(rb"\.PartialCkt\s+(\S+)\s+ExtNode\s*=\s*(.*)$", header, re.IGNORECASE)
        if match is None:
            diagnostics.append(SpdDiagnostic("warning", "PARTIAL_CKT_HEADER_INVALID", f"Malformed PartialCkt header near byte {block_start:,} was skipped."))
            position = end_line + 1
            continue
        raw_name = _decode(match.group(1))
        ext_nodes = tuple(_decode(item) for item in match.group(2).split())
        if len(ext_nodes) != 2:
            position = end_line + 1
            continue
        body = data[body_start:block_end].decode("utf-8", errors="replace").strip("\r\n")
        if not body.strip():
            empty_names.add(raw_name.casefold())
            if "not_mounted" not in raw_name.casefold():
                diagnostics.append(SpdDiagnostic("info", "EMPTY_TWO_PORT", f"Empty two-port PartialCkt {raw_name!r} was not imported as a model."))
            position = end_line + 1
            continue
        asset = f".SUBCKT {raw_name} {ext_nodes[0]} {ext_nodes[1]}\n{body}\n.ENDS {raw_name}\n"
        try:
            model = parse_passive_subcircuit(asset, source_name=f"{source_name}:{raw_name}")
            model.impedance(frequencies)
        except (SpiceModelError, ValueError) as exc:
            diagnostics.append(SpdDiagnostic("warning", "PARTIAL_MODEL_UNSUPPORTED", f"Two-port PartialCkt {raw_name!r} was skipped: {exc}"))
        else:
            if model.model_id in models:
                diagnostics.append(SpdDiagnostic("warning", "DUPLICATE_PARTIAL_MODEL", f"Duplicate two-port model {model.model_id!r} was skipped."))
            else:
                models[model.model_id] = model
                assets[f"{model.model_id}.lib"] = asset
                canonical[raw_name.casefold()] = model.model_id
        position = end_line + 1
        reporter.report(_span_percent(position, start, end, 39, 46), "Parsing two-terminal passive models")
    return models, assets, canonical, empty_names, total


def _parse_port(raw: bytes) -> _Port | None:
    tokens = raw.strip().split()
    if len(tokens) < 2 or not tokens[1].startswith(b"$Package."):
        return None
    pin = _decode(tokens[0])
    reference = tokens[1][len(b"$Package.") :]
    node_part, separator, net_part = reference.partition(b"::")
    node_id = node_part.split(b"!!", 1)[0]
    if not node_id.startswith(b"Node"):
        return None
    return _Port(pin, _decode(node_id), _decode(net_part) if separator else None)


def _parse_metadata(
    data: mmap.mmap, start: int, end: int, diagnostics: list[SpdDiagnostic]
) -> tuple[dict[str, _Part], dict[str, _Component], tuple[_Connection, ...]]:
    parts: dict[str, _Part] = {}
    components: dict[str, _Component] = {}
    connections: list[_Connection] = []
    active_header: tuple[str, str, str | None] | None = None
    active_ports: list[_Port] = []

    def flush_connection(unterminated: bool = False) -> None:
        nonlocal active_header, active_ports
        if active_header is not None:
            connections.append(_Connection(*active_header, tuple(active_ports)))
            if unterminated:
                diagnostics.append(SpdDiagnostic("warning", "CONNECT_UNTERMINATED", f"Connection {active_header[0]!r} had no .EndC before the next record."))
        active_header, active_ports = None, []

    for _, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        folded = stripped.lower()
        if folded.startswith(b".connect "):
            flush_connection(active_header is not None)
            tokens = stripped.split()
            if len(tokens) >= 3:
                usage_raw = _attribute(stripped, b"Usage")
                active_header = (_decode(tokens[1]), _decode(tokens[2]), _decode(usage_raw) if usage_raw else None)
        elif folded.startswith(b".endc"):
            flush_connection()
        elif active_header is not None:
            port = _parse_port(stripped)
            if port is not None:
                active_ports.append(port)
        elif folded.startswith(b".part ") and not folded.startswith(b".partialckt"):
            tokens = stripped.split()
            if len(tokens) >= 2:
                part_name = _decode(tokens[1])
                tag_match = re.search(rb"\bTags\s*=\s*\"([^\"]*)\"", stripped, re.IGNORECASE)
                tags = () if tag_match is None else tuple(item.strip() for item in _decode(tag_match.group(1)).split(",") if item.strip())
                parts[part_name.casefold()] = _Part(part_name, tags)
        elif folded.startswith(b".component "):
            tokens = stripped.split()
            if len(tokens) >= 2:
                refdes = _decode(tokens[1])
                start_layer = _attribute(stripped, b"StartLayer")
                attach_layer = _attribute(stripped, b"AttachLayer")
                components[refdes.casefold()] = _Component(refdes, _decode(start_layer) if start_layer else None, _decode(attach_layer) if attach_layer else None)
    flush_connection(active_header is not None)
    return parts, components, tuple(connections)


def _site_for(refdes: str, net: str) -> str | None:
    if refdes.casefold().startswith("site"):
        return refdes
    match = re.search(r"/(\d+)$", net)
    return f"SITE{match.group(1)}" if match else None


def _ground_alias_key(net: str, ground_keys: set[str]) -> str | None:
    """Return a configured ground key, accepting only a trailing site suffix.

    The original SPD net string remains attached to candidates.  This helper
    is solely for classification, so ``DGND/1`` is accepted when ``DGND`` is
    configured while unrelated nets such as ``VDD/1`` stay PWR nets.
    """

    key = net.casefold()
    if key in ground_keys:
        return key
    match = re.fullmatch(r"(.+)/(\d+)", key)
    if match and match.group(1) in ground_keys:
        return match.group(1)
    return None


def _footprint_for_part(part_name: str) -> str:
    match = re.match(r"CAP_([^_]+)", part_name, re.IGNORECASE)
    return match.group(1).upper() if match else part_name


def _is_capacitor_connection(
    connection: _Connection,
    part: _Part | None,
    canonical: dict[str, str],
    empty_names: set[str],
) -> bool:
    """Return whether a two-terminal connection is recognizably a decap.

    Normal PI import remains model-driven.  Scenario analysis additionally
    exposes DNP footprints whose original SPD part has no usable PartialCkt so
    the user can assign a separately imported capacitor model later.
    """

    if len(connection.ports) != 2:
        return False
    key = connection.part_name.casefold()
    if key in canonical or key in empty_names or "not_mounted" in key:
        return True
    tokens = [connection.part_name, connection.refdes]
    if part is not None:
        tokens.extend((part.name, *part.tags))
    folded = " ".join(tokens).casefold()
    return bool(
        re.match(r"^c(?:\d|[_-])", connection.refdes, re.IGNORECASE)
        or any(token in folded for token in ("capacitor", "decap", " cap", "cap_"))
    )


def _select_candidates(
    parts: dict[str, _Part],
    components: dict[str, _Component],
    connections: tuple[_Connection, ...],
    canonical: dict[str, str],
    empty_names: set[str],
    plane_keys: set[str],
    selected_power_keys: set[str],
    ground_keys: set[str],
    diagnostics: list[SpdDiagnostic],
    *,
    include_unmodeled_caps: bool = False,
    include_unselected_caps: bool = False,
) -> tuple[list[_PinCandidate], list[_CapCandidate], set[str], int, int]:
    device: list[_PinCandidate] = []
    caps: list[_CapCandidate] = []
    referenced: set[str] = set()
    skipped_unselected = 0
    ambiguous = 0
    for connection in connections:
        part = parts.get(connection.part_name.casefold())
        component = components.get(connection.refdes.casefold())
        is_io = bool(part and any(tag.casefold() == "io" for tag in part.tags)) and bool(
            component and component.attach_layer and component.attach_layer.casefold() == "topair"
        )
        if is_io:
            for port in connection.ports:
                if port.net is None:
                    continue
                ground_key = _ground_alias_key(port.net, ground_keys)
                if ground_key is None and port.net.casefold() not in plane_keys:
                    continue
                terminal = TerminalKind.GND if ground_key is not None else TerminalKind.PWR
                # Raw Port terminals retain their refdes/net site provenance,
                # including a site-specific DGND terminal such as ``DGND/1``.
                site = _site_for(connection.refdes, port.net)
                candidate = _PinCandidate(connection.refdes, port.pin, port.node_id, port.net, PinKind.DEVICE_BUMP, terminal, port.net if terminal == TerminalKind.PWR else None, site)
                device.append(candidate)
                referenced.add(port.node_id.casefold())
            continue

        raw_model_key = connection.part_name.casefold()
        model_id = canonical.get(raw_model_key)
        mounted = connection.usage != "0b111000"
        if "not_mounted" in raw_model_key:
            mounted = False
            base = re.sub(r"_?not_mounted$", "", connection.part_name, flags=re.IGNORECASE)
            model_id = canonical.get(base.casefold())
        if len(connection.ports) != 2 or (
            model_id is None
            and not (
                include_unmodeled_caps
                and _is_capacitor_connection(connection, part, canonical, empty_names)
            )
        ):
            continue
        ground_ports = [
            port
            for port in connection.ports
            if port.net and _ground_alias_key(port.net, ground_keys) is not None
        ]
        power_ports = [
            port
            for port in connection.ports
            if port.net and _ground_alias_key(port.net, ground_keys) is None
        ]
        if len(ground_ports) != 1 or len(power_ports) != 1:
            ambiguous += 1
            continue
        power, ground = power_ports[0], ground_ports[0]
        assert power.net is not None and ground.net is not None
        if power.net.casefold() not in plane_keys and not include_unselected_caps:
            skipped_unselected += 1
            continue
        site = _site_for(connection.refdes, power.net)
        power_candidate = _PinCandidate(connection.refdes, power.pin, power.node_id, power.net, PinKind.DECAP_PAD, TerminalKind.PWR, power.net, site)
        ground_candidate = _PinCandidate(connection.refdes, ground.pin, ground.node_id, ground.net, PinKind.DECAP_PAD, TerminalKind.GND, power.net, site)
        part_name = part.name if part else connection.part_name
        caps.append(
            _CapCandidate(
                connection.refdes,
                model_id,
                _footprint_for_part(part_name),
                power.net,
                site,
                mounted,
                power_candidate,
                ground_candidate,
                source_part_name=part_name,
                start_layer=component.start_layer if component else None,
                attach_layer=component.attach_layer if component else None,
            )
        )
        referenced.update((power.node_id.casefold(), ground.node_id.casefold()))
    if skipped_unselected:
        diagnostics.append(SpdDiagnostic("info", "UNSELECTED_DECAPS_SKIPPED", f"Skipped {skipped_unselected} two-terminal capacitor instance(s) whose power net is not selected for PI analysis."))
    if ambiguous:
        diagnostics.append(SpdDiagnostic("warning", "AMBIGUOUS_TWO_PORT_CONNECTION", f"Skipped {ambiguous} two-port connection(s) that did not map to exactly one power and one ground terminal."))
    return device, caps, referenced, skipped_unselected, ambiguous


def _parse_referenced_nodes(
    data: mmap.mmap,
    start: int,
    end: int,
    referenced: set[str],
    reporter: _Reporter,
    *,
    top_layer: str | None = None,
    progress_low: int = 58,
    progress_high: int = 82,
) -> dict[str, _Node]:
    result: dict[str, _Node] = {}
    if not referenced and top_layer is None:
        return result
    top_marker = (
        b"Layer = " + top_layer.encode("utf-8") if top_layer is not None else None
    )
    for index, (offset, raw) in enumerate(_iter_lines(data, start, end)):
        if index % 16384 == 0:
            reporter.report(
                _span_percent(offset, start, end, progress_low, progress_high),
                "Resolving decap and TOP Via Node coordinates",
            )
        if not raw.startswith(b"Node"):
            continue
        top_position = raw.find(top_marker) if top_marker is not None else -1
        top_end = top_position + len(top_marker) if top_marker is not None else -1
        is_top = bool(
            top_position >= 0
            and (top_end == len(raw) or raw[top_end : top_end + 1].isspace())
        )
        cuts = [value for value in (raw.find(b"!!"), raw.find(b"::"), raw.find(b" ")) if value >= 0]
        if not cuts:
            continue
        node_id = _decode(raw[: min(cuts)])
        key = node_id.casefold()
        if key not in referenced and not is_top:
            continue
        match = _NODE_ATTR_RE.search(raw)
        if match is None:
            continue
        try:
            x_um, y_um = _length_um(match.group(1)), _length_um(match.group(2))
        except ValueError:
            continue
        padstack_raw = _attribute(raw, b"PadStack")
        padstack = (
            _decode(padstack_raw)
            if padstack_raw
            else _decode(match.group(3))
            if match.group(3)
            else None
        )
        layer_raw = _attribute(raw, b"Layer")
        rotation_raw = _attribute(raw, b"AbsoluteRotation")
        try:
            rotation = float(rotation_raw) if rotation_raw else 0.0
            rotation_valid = isfinite(rotation)
        except ValueError:
            rotation = 0.0
            rotation_valid = False
        result[key] = _Node(
            x_um,
            y_um,
            padstack,
            _decode(layer_raw) if layer_raw else None,
            rotation,
            rotation_valid,
        )
    return result


def _materialize_geometry(
    device: list[_PinCandidate],
    caps: list[_CapCandidate],
    nodes: dict[str, _Node],
    diagnostics: list[SpdDiagnostic],
) -> tuple[tuple[PinRecord, ...], tuple[SpdCapInstance, ...], int]:
    pins: list[PinRecord] = []
    instances: list[SpdCapInstance] = []
    missing = 0

    def make_pin(candidate: _PinCandidate) -> PinRecord | None:
        nonlocal missing
        node = nodes.get(candidate.node_id.casefold())
        if node is None:
            missing += 1
            return None
        return PinRecord(
            refdes=candidate.refdes,
            pin=candidate.pin,
            net=candidate.net,
            x_um=node.x_um,
            y_um=node.y_um,
            kind=candidate.kind,
            terminal=candidate.terminal,
            domain=candidate.domain,
            site=candidate.site,
            bump_group=candidate.refdes if candidate.kind == PinKind.DEVICE_BUMP else None,
            # A raw SPD padstack is physical provenance, not a calibrated
            # solver ViaLoopTemplate identifier.
            via_template_id=None,
            source_node_id=candidate.node_id,
            source_layer=node.layer,
            source_padstack=node.padstack,
        )

    for candidate in device:
        pin = make_pin(candidate)
        if pin is not None:
            pins.append(pin)
    for candidate in caps:
        power_pin, ground_pin = make_pin(candidate.power), make_pin(candidate.ground)
        if power_pin is None or ground_pin is None:
            continue
        pins.extend((power_pin, ground_pin))
        instances.append(
            SpdCapInstance(
                refdes=candidate.refdes,
                model_id=candidate.model_id,
                footprint=candidate.footprint,
                power_net=candidate.power_net,
                ground_net=candidate.ground.net,
                site=candidate.site,
                mounted=candidate.mounted,
                x_um=(power_pin.x_um + ground_pin.x_um) / 2.0,
                y_um=(power_pin.y_um + ground_pin.y_um) / 2.0,
                power_pin_id=power_pin.pin_id,
                ground_pin_id=ground_pin.pin_id,
                source_part_name=candidate.source_part_name,
                start_layer=candidate.start_layer,
                attach_layer=candidate.attach_layer,
                power_pad_x_um=power_pin.x_um,
                power_pad_y_um=power_pin.y_um,
                ground_pad_x_um=ground_pin.x_um,
                ground_pad_y_um=ground_pin.y_um,
                power_padstack=(
                    nodes[candidate.power.node_id.casefold()].padstack
                    if candidate.power.node_id.casefold() in nodes
                    else None
                ),
                ground_padstack=(
                    nodes[candidate.ground.node_id.casefold()].padstack
                    if candidate.ground.node_id.casefold() in nodes
                    else None
                ),
                power_pad_rotation_degrees=(
                    nodes[candidate.power.node_id.casefold()].rotation_degrees
                    if candidate.power.node_id.casefold() in nodes
                    else 0.0
                ),
                ground_pad_rotation_degrees=(
                    nodes[candidate.ground.node_id.casefold()].rotation_degrees
                    if candidate.ground.node_id.casefold() in nodes
                    else 0.0
                ),
                power_pad_rotation_valid=(
                    nodes[candidate.power.node_id.casefold()].rotation_valid
                    if candidate.power.node_id.casefold() in nodes
                    else False
                ),
                ground_pad_rotation_valid=(
                    nodes[candidate.ground.node_id.casefold()].rotation_valid
                    if candidate.ground.node_id.casefold() in nodes
                    else False
                ),
            )
        )
    if missing:
        diagnostics.append(SpdDiagnostic("warning", "REFERENCED_NODE_MISSING", f"Could not resolve {missing} referenced pin Node record(s); affected pins/instances were skipped."))
    pins.sort(key=lambda item: (item.refdes.casefold(), item.pin.casefold()))
    instances.sort(key=lambda item: item.refdes.casefold())
    return tuple(pins), tuple(instances), missing


def _parse_vias(
    data: mmap.mmap,
    start: int,
    end: int,
    plane_keys: set[str],
    connection_keys: set[str],
    nodes: dict[str, _Node],
    device_pins: Sequence[PinRecord],
    reporter: _Reporter,
    *,
    top_layer: str | None,
    ground_keys: set[str],
    padstacks: tuple[SpdPadStack, ...],
) -> tuple[
    tuple[SpdViaUsage, ...],
    int,
    tuple[ViaTopEndpoint, ...],
    tuple[str, ...],
    tuple[SpdDeviceTerminalViaEndpoint, ...],
]:
    counter: Counter[tuple[str, str]] = Counter()
    total = 0
    endpoints: list[ViaTopEndpoint] = []
    uncertain_nets: set[str] = set()
    padstack_by_key = {item.name.casefold(): item for item in padstacks}
    top_key = top_layer.casefold() if top_layer else None
    device_nodes = {
        pin.source_node_id.casefold()
        for pin in device_pins
        if pin.kind == PinKind.DEVICE_BUMP and pin.source_node_id
    }
    incident_by_node: dict[str, list[_DeviceIncidentVia]] = {}
    plane_key_bytes = {item.encode("utf-8") for item in plane_keys}
    connection_key_bytes = {item.encode("utf-8") for item in connection_keys}
    padstack_by_bytes = {
        key.encode("utf-8"): value for key, value in padstack_by_key.items()
    }
    top_padstack_keys = (
        {
            item.name.casefold()
            for item in padstacks
            if top_key in {layer.casefold() for layer in item.layers}
        }
        if top_key is not None
        else set()
    )
    top_padstack_key_bytes = {
        item.encode("utf-8") for item in top_padstack_keys
    }
    for index, match in enumerate(_VIA_RE.finditer(data, start, end)):
        total += 1
        if index % 8192 == 0:
            reporter.report(
                _span_percent(match.start(), start, end, 83, 96),
                "Counting vias and resolving TOP landing evidence",
            )
        net_raw = match.group(2)
        padstack_raw = match.group(5)
        net_key_raw = net_raw.lower()
        padstack_key_raw = padstack_raw.lower()
        via_id = _decode(match.group(1))
        upper_node_id = _decode(match.group(3))
        lower_node_id = _decode(match.group(4))
        raw_net = _decode(net_raw)
        raw_padstack = _decode(padstack_raw)
        for source_node_id, opposite_node_id in (
            (upper_node_id, lower_node_id),
            (lower_node_id, upper_node_id),
        ):
            source_key = source_node_id.casefold()
            if source_key in device_nodes:
                incident_by_node.setdefault(source_key, []).append(
                    _DeviceIncidentVia(
                        via_id=via_id,
                        net=raw_net,
                        padstack=raw_padstack,
                        source_node_id=source_node_id,
                        opposite_node_id=opposite_node_id,
                    )
                )
        net: str | None = None
        padstack: str | None = None
        if not plane_keys or net_key_raw in plane_key_bytes:
            net = _decode(net_raw)
            padstack = _decode(padstack_raw)
            counter[(net, padstack)] += 1
        if top_key is None or net_key_raw not in connection_key_bytes:
            continue
        definition = padstack_by_bytes.get(padstack_key_raw)
        if definition is None:
            # An unknown padstack matters only when one of the source endpoints
            # is demonstrably on TOP; otherwise it cannot affect TOP decaps.
            if any(
                (node := nodes.get(_decode(node_id).casefold())) is not None
                and node.layer is not None
                and node.layer.casefold() == top_key
                for node_id in (match.group(3), match.group(4))
            ):
                uncertain_nets.add(net or _decode(net_raw))
            continue
        if padstack_key_raw not in top_padstack_key_bytes:
            continue
        net = net or _decode(net_raw)
        padstack = padstack or _decode(padstack_raw)
        rotation_raw = _attribute(match.group(6), b"AbsoluteRotation")
        try:
            rotation = float(rotation_raw) if rotation_raw else 0.0
            if not isfinite(rotation):
                raise ValueError("non-finite Via rotation")
        except ValueError:
            rotation = 0.0
            uncertain_nets.add(net)
        for node_id in (upper_node_id, lower_node_id):
            node = nodes.get(node_id.casefold())
            if node is None or node.layer is None:
                # Every TOP node is retained by the single Node pass.  A missing
                # endpoint therefore cannot be TOP and needs no distance guess.
                continue
            if node.layer.casefold() != top_key:
                continue
            endpoints.append(
                ViaTopEndpoint(
                    via_id=via_id,
                    net=net,
                    endpoint_node_id=node_id,
                    x_um=node.x_um,
                    y_um=node.y_um,
                    padstack=padstack,
                    rotation_degrees=rotation,
                )
            )
    result = tuple(
        SpdViaUsage(net, padstack, count)
        for (net, padstack), count in sorted(counter.items(), key=lambda item: (item[0][0].casefold(), item[0][1].casefold()))
    )

    def same_terminal_net(pin_net: str, via_net: str) -> bool:
        if pin_net.casefold() == via_net.casefold():
            return True
        pin_ground = _ground_alias_key(pin_net, ground_keys)
        via_ground = _ground_alias_key(via_net, ground_keys)
        return pin_ground is not None and pin_ground == via_ground

    device_endpoints: list[SpdDeviceTerminalViaEndpoint] = []
    for pin in sorted(
        (item for item in device_pins if item.kind == PinKind.DEVICE_BUMP),
        key=lambda item: (item.pin_id.casefold(), item.pin_id),
    ):
        node_key = pin.source_node_id.casefold() if pin.source_node_id else None
        candidates = sorted(
            incident_by_node.get(node_key, ()) if node_key is not None else (),
            key=lambda item: (
                item.via_id.casefold(),
                item.padstack.casefold(),
                item.net.casefold(),
                item.opposite_node_id.casefold(),
                item.via_id,
                item.padstack,
                item.net,
                item.opposite_node_id,
            ),
        )
        selected = candidates[0] if len(candidates) == 1 else None
        if pin.source_node_id is None:
            status: SpdDeviceTerminalViaStatus = "source_node_missing"
        elif pin.source_layer is None:
            status = "source_layer_missing"
        elif top_key is None or pin.source_layer.casefold() != top_key:
            status = "source_pin_not_top"
        elif not candidates:
            status = "missing_incident_via"
        elif len(candidates) != 1:
            status = "ambiguous_incident_via"
        elif not same_terminal_net(pin.net, selected.net):
            status = "incident_net_mismatch"
        elif selected.padstack.casefold() not in padstack_by_key:
            status = "incident_padstack_definition_missing"
        elif top_key not in {
            layer.casefold()
            for layer in padstack_by_key[selected.padstack.casefold()].layers
        }:
            status = "incident_padstack_not_on_top"
        else:
            status = "complete"
        device_endpoints.append(
            SpdDeviceTerminalViaEndpoint(
                pin_id=pin.pin_id,
                refdes=pin.refdes,
                pin=pin.pin,
                terminal=str(pin.terminal),
                net=pin.net,
                source_node_id=pin.source_node_id,
                source_layer=pin.source_layer,
                source_padstack=pin.source_padstack,
                source_x_um=float(pin.x_um),
                source_y_um=float(pin.y_um),
                status=status,
                issues=() if status == "complete" else (status,),
                candidate_count=len(candidates),
                candidate_via_ids=tuple(item.via_id for item in candidates),
                incident_via_id=selected.via_id if selected is not None else None,
                incident_net=selected.net if selected is not None else None,
                incident_padstack=(
                    selected.padstack if selected is not None else None
                ),
                incident_opposite_node_id=(
                    selected.opposite_node_id if selected is not None else None
                ),
            )
        )
    return (
        result,
        total,
        tuple(
            sorted(
                endpoints,
                key=lambda item: (
                    item.via_id.casefold(),
                    item.endpoint_node_id.casefold(),
                ),
            )
        ),
        tuple(sorted(uncertain_nets, key=str.casefold)),
        tuple(device_endpoints),
    )


@dataclass(frozen=True, slots=True)
class _RecoveredViaNode:
    node_id: str
    net: str
    x_um: float
    y_um: float
    layer: str
    padstack: str | None


def recover_spd_via_paths(
    path: str | Path,
    *,
    landings: Iterable[object],
    target_layers_by_net: Mapping[str, Iterable[str]],
    stackup_layers: Sequence[StackupLayer],
    padstacks: Iterable[SpdPadStack],
    top_layer: str | None,
    max_segments: int = 16,
    expected_source: SpdSourceInfo | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> SpdViaPathRecovery:
    """Recover compact, source-proven vertical Via chains after SPD planning.

    This intentionally reopens the source only after the import plan has selected
    the editable terminal Vias and plane layers.  It keeps just the frontier
    nodes/segments for those requests, never serializes a board-wide Via graph,
    and retains only bounded route evidence.  A same-NET Trace may bridge Via
    segments only when that continuation is unique; its RL is not inferred.
    """

    reporter = _Reporter(progress, is_cancelled)
    reporter.report(0, "Recovering source-proven vertical Via paths")
    source_path = Path(path)
    if max_segments < 1:
        raise ValueError("max_segments must be >= 1")
    if not source_path.is_file():
        raise SpdImportError(f"SPD source does not exist: {source_path}")

    def stat_identity(
        *, phase: str, enforce_expected: bool = True
    ) -> tuple[int, int]:
        try:
            observed = source_path.stat()
        except OSError as exc:
            raise SpdImportError(
                f"cannot stat SPD source {source_path} {phase}: {exc}"
            ) from exc
        identity = (int(observed.st_size), int(observed.st_mtime_ns))
        if expected_source is not None and enforce_expected:
            try:
                same_path = source_path.resolve() == expected_source.path.resolve()
            except OSError as exc:
                raise SpdImportError(
                    f"cannot resolve SPD source identity {source_path}: {exc}"
                ) from exc
            expected_identity = (
                int(expected_source.size_bytes),
                int(expected_source.mtime_ns),
            )
            if not same_path or identity != expected_identity:
                raise SpdImportError(
                    "SPD source identity changed after analysis before source Via "
                    "path recovery; import aborted so path evidence cannot be "
                    "mixed with a different source SHA-256"
                )
        return identity

    before_recovery = stat_identity(phase="before source Via path recovery")

    def finish(result: SpdViaPathRecovery) -> SpdViaPathRecovery:
        after_recovery = stat_identity(
            phase="after source Via path recovery", enforce_expected=False
        )
        if after_recovery != before_recovery:
            raise SpdImportError(
                "SPD source changed during source Via path recovery; import "
                "aborted and no mixed-source path evidence was persisted"
            )
        return result

    if not top_layer:
        return finish(SpdViaPathRecovery(
            evidence_by_via={},
            diagnostics=(
                SpdDiagnostic(
                    "warning",
                    "SPD_VIA_PATH_FALLBACK",
                    "No TOP conductor layer was available; terminal Via paths use "
                    "the documented legacy rail template.",
                ),
            ),
            statistics={"requested": 0, "recovered": 0, "fallback": 0},
        ))

    layer_by_key = {item.name.casefold(): item.name for item in stackup_layers}
    depth_by_key = {
        item.name.casefold(): index for index, item in enumerate(stackup_layers)
    }
    top_key = top_layer.casefold()
    if top_key not in depth_by_key:
        return finish(SpdViaPathRecovery(
            evidence_by_via={},
            diagnostics=(
                SpdDiagnostic(
                    "warning",
                    "SPD_VIA_PATH_FALLBACK",
                    "The source TOP layer is absent from the normalized stack-up; "
                    "terminal Via paths use the documented legacy rail template.",
                ),
            ),
            statistics={"requested": 0, "recovered": 0, "fallback": 0},
        ))
    targets_by_net = {
        str(net).casefold(): tuple(
            dict.fromkeys(
                layer_by_key[layer.casefold()]
                for layer in layers
                if layer.casefold() in layer_by_key
            )
        )
        for net, layers in target_layers_by_net.items()
    }
    padstack_by_key = {item.name.casefold(): item for item in padstacks}

    def landing_value(landing: object, name: str) -> object:
        try:
            return getattr(landing, name)
        except AttributeError as exc:
            raise ValueError(f"Via landing lacks {name!r} source evidence") from exc

    states: dict[tuple[str, str], dict[str, object]] = {}
    nodes: dict[str, _RecoveredViaNode] = {}
    diagnostics: list[SpdDiagnostic] = []
    for landing in landings:
        via_id = str(landing_value(landing, "via_id"))
        net = str(landing_value(landing, "net"))
        endpoint_node_id = str(landing_value(landing, "endpoint_node_id"))
        via_key = via_id.casefold()
        net_key = net.casefold()
        endpoint_key = endpoint_node_id.casefold()
        for target_layer in targets_by_net.get(net_key, ()):
            state_key = (via_key, target_layer.casefold())
            previous = states.get(state_key)
            if previous is not None:
                if previous["node_key"] != endpoint_key or previous["net_key"] != net_key:
                    diagnostics.append(
                        SpdDiagnostic(
                            "warning",
                            "SPD_VIA_PATH_FALLBACK",
                            f"Via {via_id!r} has conflicting TOP landing evidence; "
                            "its terminal path uses the legacy rail template.",
                        )
                    )
                    previous["status"] = "CONFLICTING_TOP_LANDING"
                continue
            states[state_key] = {
                "via_id": via_id,
                "via_key": via_key,
                "net": net,
                "net_key": net_key,
                "target_layer": target_layer,
                "target_key": target_layer.casefold(),
                "node_id": endpoint_node_id,
                "node_key": endpoint_key,
                "layer": top_layer,
                "segments": [],
                # Trace hops are retained only as provenance used to reach a
                # terminal plane.  The compact electrical evidence schema is
                # Via-RL-only, so their RL is deliberately not invented here.
                "trace_steps": 0,
                "transition_steps": 0,
                "visited_node_keys": {endpoint_key},
                "status": "PENDING",
            }
            nodes.setdefault(
                endpoint_key,
                _RecoveredViaNode(
                    node_id=endpoint_node_id,
                    net=net,
                    x_um=float(landing_value(landing, "x_um")),
                    y_um=float(landing_value(landing, "y_um")),
                    layer=top_layer,
                    padstack=str(landing_value(landing, "padstack")),
                ),
            )

    if not states:
        return finish(SpdViaPathRecovery(
            evidence_by_via={}, diagnostics=tuple(diagnostics),
            statistics={"requested": 0, "recovered": 0, "fallback": 0},
        ))

    def node_id_and_net(raw: bytes) -> tuple[str, str] | None:
        cuts = [
            value
            for value in (raw.find(b"!!"), raw.find(b"::"), raw.find(b" "))
            if value >= 0
        ]
        separator = raw.find(b"::")
        if not cuts or separator < 0:
            return None
        node_id = _decode(raw[: min(cuts)])
        net_token = raw[separator + 2 :].split(None, 1)[0]
        if not node_id or not net_token:
            return None
        return node_id, _decode(net_token)

    path_node_section_passes = 0

    def resolve_nodes(
        data: mmap.mmap,
        start: int,
        end: int,
        requested: set[str],
    ) -> None:
        nonlocal path_node_section_passes
        # Do not materialize all accumulated node keys on every frontier pass.
        # The recovery cache can contain hundreds of thousands of entries on a
        # production SPD, while each pass requests only the next compact frontier.
        missing = {node_key for node_key in requested if node_key not in nodes}
        if not missing:
            return
        path_node_section_passes += 1
        for line_index, (_offset, raw) in enumerate(_iter_lines(data, start, end)):
            if line_index % 16384 == 0:
                reporter.check()
            if not raw.startswith(b"Node"):
                continue
            identity = node_id_and_net(raw)
            if identity is None:
                continue
            node_id, net = identity
            node_key = node_id.casefold()
            if node_key not in missing:
                continue
            attributes = _NODE_ATTR_RE.search(raw)
            layer_raw = _attribute(raw, b"Layer")
            if attributes is None or layer_raw is None:
                continue
            try:
                x_um = _length_um(attributes.group(1))
                y_um = _length_um(attributes.group(2))
            except ValueError:
                continue
            layer = _decode(layer_raw)
            nodes[node_key] = _RecoveredViaNode(
                node_id=node_id,
                net=net,
                x_um=x_um,
                y_um=y_um,
                layer=layer,
                padstack=(
                    _decode(_attribute(raw, b"PadStack"))
                    if _attribute(raw, b"PadStack") is not None
                    else _decode(attributes.group(3))
                    if attributes.group(3) is not None
                    else None
                ),
            )

    def trace_neighbors(
        data: mmap.mmap,
        start: int,
        end: int,
        pending: Mapping[str, list[tuple[str, str]]],
    ) -> dict[tuple[str, str], set[str]]:
        """Return same-NET Trace neighbors for pending vertical paths.

        Trace traversal is intentionally a one-hop-at-a-time continuation: a
        state may move only through one unique neighboring node, and the main
        loop then proves the next vertical step.  This keeps branches and
        cycles fail-closed without treating arbitrary copper as a Via model.
        """

        neighbors: dict[tuple[str, str], set[str]] = {}
        if start < 0 or end <= start:
            return neighbors
        for match_index, match in enumerate(_TRACE_RE.finditer(data, start, end)):
            if match_index % 8192 == 0:
                reporter.check()
            net_key = _decode(match.group(2)).casefold()
            first = _decode(match.group(3)).casefold()
            second = _decode(match.group(4)).casefold()
            for node_key, other_key in ((first, second), (second, first)):
                for state_key in pending.get(node_key, ()):
                    if str(states[state_key]["net_key"]) == net_key:
                        neighbors.setdefault(state_key, set()).add(other_key)
        return neighbors

    # Alternate-exit proof used to retain a nested Node -> set[Node] Trace
    # adjacency graph.  Production sources can contain more than a million
    # relevant Trace records, for which the Python object overhead alone is
    # several gigabytes.  Reachability needs only connected-component identity,
    # so retain one exact dense union-find forest plus compact directed
    # Trace-incident Via edges.  After Node layers are resolved, every component
    # is reduced to (source-layer, destination-depth) exit witnesses.  Two
    # distinct source IDs per bucket are sufficient and exact: a query excludes
    # only its own start node, so one of two witnesses must remain.
    alternate_node_index_by_net: dict[str, dict[str, int]] = {}
    alternate_net_code_by_key: dict[str, int] = {}
    alternate_numeric_net_codes = array("i")
    alternate_numeric_capacity = 0
    alternate_parents = array("I")
    alternate_ranks = bytearray()
    alternate_trace_nodes = bytearray()
    alternate_node_depths = array("i")
    alternate_via_sources = array("I")
    alternate_via_destinations = array("I")
    alternate_exit_sources: dict[
        tuple[int, int], dict[int, tuple[int, int | None]]
    ] = {}
    alternate_exit_cache: dict[tuple[str, int, int, int, int], bool] = {}
    alternate_node_section_passes = 0
    alternate_exit_via_edges_retained = 0
    alternate_exit_nodes_retained = 0
    alternate_nodes_resolved = 0
    relevant_trace_records_indexed = 0
    alternate_trace_components = 0
    graph_index_ready = False

    def canonical_numeric_node_id(node_key: str) -> int | None:
        """Return the dense PowerSI Node number without conflating aliases."""

        if not node_key.startswith("node"):
            return None
        digits = node_key[4:]
        if not digits or not digits.isascii() or not digits.isdecimal():
            return None
        # ``Node01`` and ``Node1`` are distinct under the established
        # case-folded string identity and therefore must not share an index.
        if len(digits) > 1 and digits.startswith("0"):
            return None
        return int(digits)

    def alternate_find(index: int) -> int:
        root = index
        while alternate_parents[root] != root:
            root = alternate_parents[root]
        while alternate_parents[index] != index:
            parent = alternate_parents[index]
            alternate_parents[index] = root
            index = parent
        return root

    def alternate_index_for(
        net_key: str, node_key: str, *, trace_node: bool
    ) -> int:
        nonlocal alternate_trace_components, alternate_exit_nodes_retained
        net_code = alternate_net_code_by_key.setdefault(
            net_key, len(alternate_net_code_by_key)
        )
        numeric_id = canonical_numeric_node_id(node_key)
        if numeric_id is not None and numeric_id < alternate_numeric_capacity:
            owner = alternate_numeric_net_codes[numeric_id]
            if owner in {-1, net_code}:
                if owner < 0:
                    alternate_numeric_net_codes[numeric_id] = net_code
                    alternate_exit_nodes_retained += 1
                if trace_node and not alternate_trace_nodes[numeric_id]:
                    alternate_trace_nodes[numeric_id] = 1
                    alternate_trace_components += 1
                return numeric_id
        by_node = alternate_node_index_by_net.setdefault(net_key, {})
        existing = by_node.get(node_key)
        if existing is not None:
            if trace_node and not alternate_trace_nodes[existing]:
                alternate_trace_nodes[existing] = 1
                alternate_trace_components += 1
            return existing
        index = len(alternate_parents)
        by_node[node_key] = index
        alternate_parents.append(index)
        alternate_ranks.append(0)
        alternate_trace_nodes.append(int(trace_node))
        alternate_node_depths.append(-1)
        alternate_exit_nodes_retained += 1
        if trace_node:
            alternate_trace_components += 1
        return index

    def alternate_lookup(net_key: str, node_key: str) -> int | None:
        net_code = alternate_net_code_by_key.get(net_key)
        numeric_id = canonical_numeric_node_id(node_key)
        if (
            net_code is not None
            and numeric_id is not None
            and numeric_id < alternate_numeric_capacity
            and alternate_numeric_net_codes[numeric_id] == net_code
        ):
            return numeric_id
        return alternate_node_index_by_net.get(net_key, {}).get(node_key)

    def alternate_union(net_key: str, first: str, second: str) -> None:
        nonlocal alternate_trace_components
        first_root = alternate_find(
            alternate_index_for(net_key, first, trace_node=True)
        )
        second_root = alternate_find(
            alternate_index_for(net_key, second, trace_node=True)
        )
        if first_root == second_root:
            return
        if alternate_ranks[first_root] < alternate_ranks[second_root]:
            first_root, second_root = second_root, first_root
        alternate_parents[second_root] = first_root
        if alternate_ranks[first_root] == alternate_ranks[second_root]:
            alternate_ranks[first_root] += 1
        alternate_trace_components -= 1

    def ensure_trace_via_index(
        data: mmap.mmap, *, trace_start: int, trace_end: int, via_start: int, via_end: int
    ) -> None:
        nonlocal graph_index_ready, alternate_node_section_passes
        nonlocal alternate_exit_via_edges_retained, alternate_exit_nodes_retained
        nonlocal relevant_trace_records_indexed, alternate_numeric_capacity
        nonlocal alternate_nodes_resolved
        if graph_index_ready:
            return
        relevant_net_keys = {
            str(state["net_key"]) for state in states.values()
        }
        numeric_max = -1
        numeric_endpoints = 0
        all_trace_endpoints_numeric = True
        if trace_start >= 0 and trace_end > trace_start:
            for match_index, match in enumerate(
                _TRACE_RE.finditer(data, trace_start, trace_end)
            ):
                if match_index % 8192 == 0:
                    reporter.check()
                net = _decode(match.group(2)).casefold()
                if net not in relevant_net_keys:
                    continue
                relevant_trace_records_indexed += 1
                for raw_node in (match.group(3), match.group(4)):
                    node_number = canonical_numeric_node_id(
                        _decode(raw_node).casefold()
                    )
                    if node_number is None:
                        all_trace_endpoints_numeric = False
                    else:
                        numeric_endpoints += 1
                        numeric_max = max(numeric_max, node_number)

        # PowerSI production Node IDs are canonical, globally sparse integers.
        # When their numeric range is reasonably dense, using that integer as
        # the initial DSU index removes millions of Python string/dict/value
        # objects.  Any noncanonical, very sparse, or cross-NET identity falls
        # through to the exact arbitrary-string map below.
        if (
            all_trace_endpoints_numeric
            and numeric_max >= 0
            and numeric_max <= max(4_096, numeric_endpoints * 2)
            and numeric_max < 0xFFFF_FFFF
        ):
            alternate_numeric_capacity = numeric_max + 1
            alternate_parents.extend(range(alternate_numeric_capacity))
            alternate_ranks.extend(b"\0" * alternate_numeric_capacity)
            alternate_trace_nodes.extend(b"\0" * alternate_numeric_capacity)
            alternate_node_depths.extend(
                array("i", [-1]) * alternate_numeric_capacity
            )
            alternate_numeric_net_codes.extend(
                array("i", [-1]) * alternate_numeric_capacity
            )

        if trace_start >= 0 and trace_end > trace_start:
            for match_index, match in enumerate(
                _TRACE_RE.finditer(data, trace_start, trace_end)
            ):
                if match_index % 8192 == 0:
                    reporter.check()
                net = _decode(match.group(2)).casefold()
                if net not in relevant_net_keys:
                    continue
                first = _decode(match.group(3)).casefold()
                second = _decode(match.group(4)).casefold()
                alternate_union(net, first, second)

        for match_index, match in enumerate(_VIA_RE.finditer(data, via_start, via_end)):
            if match_index % 8192 == 0:
                reporter.check()
            net = _decode(match.group(2)).casefold()
            if net not in relevant_net_keys:
                continue
            first = _decode(match.group(3)).casefold()
            second = _decode(match.group(4)).casefold()
            first_index = alternate_lookup(net, first)
            second_index = alternate_lookup(net, second)
            first_is_trace = (
                first_index is not None and bool(alternate_trace_nodes[first_index])
            )
            second_is_trace = (
                second_index is not None and bool(alternate_trace_nodes[second_index])
            )
            if first_is_trace:
                if second_index is None:
                    second_index = alternate_index_for(
                        net, second, trace_node=False
                    )
                alternate_via_sources.append(first_index)
                alternate_via_destinations.append(second_index)
            if second_is_trace:
                if first_index is None:
                    first_index = alternate_index_for(net, first, trace_node=False)
                alternate_via_sources.append(second_index)
                alternate_via_destinations.append(first_index)

        alternate_exit_via_edges_retained = len(alternate_via_sources)
        # Alternate-exit decisions need only source NET and layer identity.  A
        # single compact Node pass resolves every Trace endpoint and every far
        # endpoint of a Trace-incident Via; no coordinates or padstack are kept.
        if alternate_parents:
            alternate_node_section_passes += 1
            for line_index, (_offset, raw) in enumerate(
                _iter_lines(data, node_start, node_end)
            ):
                if line_index % 16384 == 0:
                    reporter.check()
                if not raw.startswith(b"Node"):
                    continue
                identity = node_id_and_net(raw)
                if identity is None:
                    continue
                node_id, net = identity
                node_key = node_id.casefold()
                net_key = net.casefold()
                node_index = alternate_lookup(net_key, node_key)
                if node_index is None:
                    continue
                attributes = _NODE_ATTR_RE.search(raw)
                layer_raw = _attribute(raw, b"Layer")
                if attributes is None or layer_raw is None:
                    continue
                # Match the main resolver's validity gate without retaining the
                # coordinates or padstack that alternate-exit checks never use.
                try:
                    _length_um(attributes.group(1))
                    _length_um(attributes.group(2))
                except ValueError:
                    continue
                layer_depth = depth_by_key.get(_decode(layer_raw).casefold())
                if layer_depth is not None:
                    if alternate_node_depths[node_index] < 0:
                        alternate_nodes_resolved += 1
                    alternate_node_depths[node_index] = layer_depth

        for source_index, destination_index in zip(
            alternate_via_sources, alternate_via_destinations, strict=True
        ):
            source_depth = alternate_node_depths[source_index]
            destination_depth = alternate_node_depths[destination_index]
            if source_depth < 0 or destination_depth < 0:
                continue
            component_root = alternate_find(source_index)
            by_destination = alternate_exit_sources.setdefault(
                (component_root, source_depth), {}
            )
            witnesses = by_destination.get(destination_depth)
            if witnesses is None:
                by_destination[destination_depth] = (source_index, None)
            elif witnesses[0] != source_index and witnesses[1] is None:
                by_destination[destination_depth] = (witnesses[0], source_index)
        graph_index_ready = True

    def trace_component_has_alternate_exit(
        data: mmap.mmap,
        state: dict[str, Any],
        current: _RecoveredViaNode,
        *,
        trace_start: int,
        trace_end: int,
        via_start: int,
        via_end: int,
    ) -> bool:
        """Prove a direct Via is not secretly one serial terminal path.

        A direct monotonic Via is insufficient if same-layer source copper can
        reach another monotonic Via.  We retain that connectivity evidence but
        force the complete legacy terminal template because trace RL was not
        extracted.  This scan is deliberately conservative and bounded by the
        parsed Trace section.
        """

        if trace_start < 0 or trace_end <= trace_start:
            return False
        ensure_trace_via_index(
            data, trace_start=trace_start, trace_end=trace_end,
            via_start=via_start, via_end=via_end,
        )
        net_key = str(state["net_key"])
        start_key = current.node_id.casefold()
        start_index = alternate_lookup(net_key, start_key)
        if start_index is None or not alternate_trace_nodes[start_index]:
            return False
        component_root = alternate_find(start_index)
        current_depth = depth_by_key.get(current.layer.casefold())
        target_depth = depth_by_key.get(str(state["target_key"]))
        if current_depth is None or target_depth is None:
            return False
        cache_key = (
            net_key,
            component_root,
            start_index,
            current_depth,
            target_depth,
        )
        cached = alternate_exit_cache.get(cache_key)
        if cached is not None:
            return cached
        direction = 1 if target_depth > current_depth else -1
        by_destination = alternate_exit_sources.get(
            (component_root, current_depth), {}
        )
        for next_depth, witnesses in by_destination.items():
            if (
                direction * (next_depth - current_depth) > 0
                and direction * (target_depth - next_depth) >= 0
                and (witnesses[0] != start_index or witnesses[1] is not None)
            ):
                alternate_exit_cache[cache_key] = True
                return True
        alternate_exit_cache[cache_key] = False
        return False

    evidence: dict[str, list[SpdViaPathEvidence]] = {}
    structural_evidence: dict[str, list[SpdViaStructuralEvidence]] = {}
    failures: Counter[str] = Counter()
    try:
        with source_path.open("rb") as handle, mmap.mmap(
            handle.fileno(), 0, access=mmap.ACCESS_READ
        ) as data:
            if expected_source is not None:
                observed_sha256 = hashlib.sha256(data).hexdigest()
                if observed_sha256.casefold() != expected_source.sha256.casefold():
                    raise SpdImportError(
                        "SPD source SHA-256 mismatch after analysis before source "
                        "Via path recovery; import aborted so path evidence cannot "
                        "be mixed with replacement bytes"
                    )
            node_start = _find_line(data, b"* Node description lines")
            trace_start = _find_line(data, b"* Trace description lines")
            via_start = _find_line(data, b"* Via description lines")
            pad_start = _find_line(data, b"* PadStack collection description lines")
            if node_start < 0 or via_start < 0:
                diagnostics.append(
                    SpdDiagnostic(
                        "warning",
                        "SPD_VIA_PATH_FALLBACK",
                        "The SPD has no readable Node/Via section for per-landing "
                        "path recovery; terminal paths use the legacy rail template.",
                    )
                )
                return finish(SpdViaPathRecovery(
                    evidence_by_via={},
                    diagnostics=tuple(diagnostics),
                    statistics={
                        "requested": len(states),
                        "recovered": 0,
                        "fallback": len(states),
                    },
                ))
            node_end = trace_start if trace_start > node_start else via_start
            trace_end = via_start if via_start > trace_start else pad_start
            via_end = pad_start if pad_start > via_start else len(data)

            max_trace_steps = max_segments
            max_total_steps = max_segments + max_trace_steps
            for _step in range(max_total_steps + 1):
                reporter.report(
                    min(98, 4 + round(94 * _step / max_total_steps)),
                    "Recovering source-proven vertical Via paths "
                    f"(pass {_step + 1}/{max_total_steps + 1})",
                )
                pending_by_node: dict[str, list[tuple[str, str]]] = {}
                for state_key, state in states.items():
                    if state["status"] != "PENDING":
                        continue
                    current_key = str(state["node_key"])
                    current_node = nodes.get(current_key)
                    if current_node is None:
                        state["status"] = "MISSING_NODE"
                        failures[str(state["status"])] += 1
                        continue
                    if current_node.layer.casefold() == str(state["target_key"]):
                        segments = tuple(state["segments"])
                        # Raw Node records commonly omit ``PadStack`` away from
                        # TOP and may name a component feature rather than the
                        # actual barrel that reaches this plane.  The last
                        # traversed Via segment is therefore unconditionally
                        # authoritative for target-pad geometry.
                        target_padstack_name = (
                            segments[-1].padstack if segments else None
                        )
                        padstack = (
                            padstack_by_key.get(target_padstack_name.casefold())
                            if target_padstack_name
                            else None
                        )
                        shape = next(
                            (
                                item
                                for item in (padstack.pad_shapes if padstack else ())
                                if item.layer.casefold()
                                == str(state["target_key"])
                                and item.kind in {"CIRCLE", "RECTANGLE"}
                                and item.width_um is not None
                                and item.height_um is not None
                            ),
                            None,
                        )
                        if not segments or shape is None or target_padstack_name is None:
                            # Keep topology only when full target-pad evidence
                            # cannot be formed. This is for MLO
                            # classification/diagnostics only and never grants
                            # Distribution eligibility by itself.
                            if segments:
                                structural_evidence.setdefault(
                                    str(state["via_key"]), []
                                ).append(
                                    SpdViaStructuralEvidence(
                                        via_id=str(state["via_id"]),
                                        target_layer=str(state["target_layer"]),
                                        target_node_id=current_node.node_id,
                                        target_x_um=current_node.x_um,
                                        target_y_um=current_node.y_um,
                                        segments=segments,
                                        trace_hops=int(state["trace_steps"]),
                                        trace_alternate_exit=bool(
                                            state.get("trace_alternate_exit", False)
                                        ),
                                    )
                                )
                            state["status"] = "TARGET_PAD_UNSUPPORTED"
                            failures[str(state["status"])] += 1
                            continue
                        target_width_um = float(shape.width_um)
                        target_height_um = float(shape.height_um)
                        terminal_rotation = segments[-1].rotation_degrees % 360.0
                        if shape.kind == "RECTANGLE" and terminal_rotation in {
                            90.0,
                            270.0,
                        }:
                            target_width_um, target_height_um = (
                                target_height_um,
                                target_width_um,
                            )
                        item = SpdViaPathEvidence(
                            via_id=str(state["via_id"]),
                            target_layer=str(state["target_layer"]),
                            target_node_id=current_node.node_id,
                            target_padstack=target_padstack_name,
                            target_pad_kind=shape.kind,
                            target_pad_width_um=target_width_um,
                            target_pad_height_um=target_height_um,
                            target_x_um=current_node.x_um,
                            target_y_um=current_node.y_um,
                            segments=segments,
                            trace_hops=int(state["trace_steps"]),
                            trace_alternate_exit=bool(
                                state.get("trace_alternate_exit", False)
                            ),
                        )
                        evidence.setdefault(str(state["via_key"]), []).append(item)
                        state["status"] = "RECOVERED"
                        continue
                    if len(state["segments"]) >= max_segments:
                        state["status"] = "SEGMENT_LIMIT"
                        failures[str(state["status"])] += 1
                        continue
                    if int(state["transition_steps"]) >= max_total_steps:
                        state["status"] = "TOTAL_TRANSITION_LIMIT"
                        failures[str(state["status"])] += 1
                        continue
                    pending_by_node.setdefault(current_key, []).append(state_key)
                if not pending_by_node:
                    break

                candidates: dict[
                    tuple[str, str], list[tuple[str, str, str, float | None]]
                ] = {
                    state_key: []
                    for state_keys in pending_by_node.values()
                    for state_key in state_keys
                }
                candidate_nodes: set[str] = set()
                for match_index, match in enumerate(
                    _VIA_RE.finditer(data, via_start, via_end)
                ):
                    if match_index % 8192 == 0:
                        reporter.check()
                    upper_key = _decode(match.group(3)).casefold()
                    lower_key = _decode(match.group(4)).casefold()
                    if upper_key not in pending_by_node and lower_key not in pending_by_node:
                        continue
                    via_id = _decode(match.group(1))
                    via_key = via_id.casefold()
                    net_key = _decode(match.group(2)).casefold()
                    padstack = _decode(match.group(5))
                    rotation_raw = _attribute(match.group(6), b"AbsoluteRotation")
                    try:
                        raw_rotation = float(rotation_raw) if rotation_raw else 0.0
                        if not isfinite(raw_rotation):
                            raise ValueError("non-finite Via rotation")
                        normalized_rotation = raw_rotation % 360.0
                        nearest_quadrant = round(normalized_rotation / 90.0) % 4
                        rotation = float(nearest_quadrant * 90)
                        if abs(normalized_rotation - rotation) > 1.0e-6 and not (
                            rotation == 0.0
                            and abs(normalized_rotation - 360.0) <= 1.0e-6
                        ):
                            rotation = None
                    except ValueError:
                        rotation = None
                    for current_key, next_key in ((upper_key, lower_key), (lower_key, upper_key)):
                        for state_key in pending_by_node.get(current_key, ()):
                            state = states[state_key]
                            if str(state["net_key"]) != net_key:
                                continue
                            if not state["segments"] and via_key != str(state["via_key"]):
                                continue
                            candidates[state_key].append(
                                (via_id, padstack, next_key, rotation)
                            )
                            candidate_nodes.add(next_key)
                resolve_nodes(data, node_start, node_end, candidate_nodes)
                trace_needed: dict[str, list[tuple[str, str]]] = {}
                for state_key, state_candidates in candidates.items():
                    state = states[state_key]
                    current = nodes.get(str(state["node_key"]))
                    assert current is not None
                    current_depth = depth_by_key.get(current.layer.casefold())
                    target_depth = depth_by_key.get(str(state["target_key"]))
                    valid: list[
                        tuple[str, str, _RecoveredViaNode, SpdPadStack, float]
                    ] = []
                    overshoot = False
                    missing_node = False
                    missing_padstack = False
                    unsupported_rotation = False
                    for via_id, padstack_name, next_key, rotation in state_candidates:
                        next_node = nodes.get(next_key)
                        definition = padstack_by_key.get(padstack_name.casefold())
                        if next_node is None:
                            missing_node = True
                            continue
                        if definition is None:
                            missing_padstack = True
                            continue
                        if rotation is None:
                            unsupported_rotation = True
                            continue
                        if (
                            next_node.net.casefold() != str(state["net_key"])
                            or definition.drill_diameter_um is None
                            or definition.drill_diameter_um <= 0
                            or current_depth is None
                            or target_depth is None
                        ):
                            continue
                        next_depth = depth_by_key.get(next_node.layer.casefold())
                        if next_depth is None:
                            continue
                        direction = 1 if target_depth > current_depth else -1
                        delta = direction * (next_depth - current_depth)
                        remaining = direction * (target_depth - next_depth)
                        if delta <= 0 or remaining < 0:
                            overshoot = True
                            continue
                        valid.append(
                            (via_id, padstack_name, next_node, definition, rotation)
                        )
                    unique_valid = {
                        (via_id.casefold(), next_node.node_id.casefold()): item
                        for item in valid
                        for via_id, _padstack, next_node, _definition, _rotation in (item,)
                    }
                    if len(unique_valid) != 1:
                        if len(unique_valid) > 1:
                            state["status"] = "AMBIGUOUS_VERTICAL_BRANCH"
                            failures[str(state["status"])] += 1
                        elif missing_node:
                            state["status"] = "MISSING_NODE"
                            failures[str(state["status"])] += 1
                        elif missing_padstack:
                            state["status"] = "MISSING_PADSTACK"
                            failures[str(state["status"])] += 1
                        elif unsupported_rotation:
                            state["status"] = "UNSUPPORTED_VIA_ROTATION"
                            failures[str(state["status"])] += 1
                        else:
                            trace_needed.setdefault(str(state["node_key"]), []).append(
                                state_key
                            )
                            state["overshoot"] = overshoot
                        continue
                    via_id, padstack_name, next_node, definition, rotation = next(
                        iter(unique_valid.values())
                    )
                    if trace_component_has_alternate_exit(
                        data,
                        state,
                        current,
                        trace_start=trace_start,
                        trace_end=trace_end,
                        via_start=via_start,
                        via_end=via_end,
                    ):
                        state["trace_alternate_exit"] = True
                    segment = SpdViaPathSegment(
                        via_id=via_id,
                        padstack=padstack_name,
                        drill_diameter_um=float(definition.drill_diameter_um),
                        start_layer=current.layer,
                        end_layer=next_node.layer,
                        length_um=abs(
                            float(depth_by_key[next_node.layer.casefold()])
                            - float(depth_by_key[current.layer.casefold()])
                        )
                        * 1.0,
                        end_x_um=next_node.x_um,
                        end_y_um=next_node.y_um,
                        rotation_degrees=rotation,
                        padstack_material=definition.material,
                    )
                    # Stack-up layer indices are monotonic but not physical
                    # distances.  Replace the span with the actual centre-depth
                    # distance below once the compact depth map is available.
                    state["segments"].append(segment)
                    state["node_id"] = next_node.node_id
                    state["node_key"] = next_node.node_id.casefold()
                    state["layer"] = next_node.layer
                    state["visited_node_keys"].add(next_node.node_id.casefold())
                    state["transition_steps"] = int(state["transition_steps"]) + 1
                if trace_needed:
                    neighbors_by_state = trace_neighbors(
                        data, trace_start, trace_end, trace_needed
                    )
                    candidate_nodes = {
                        node_key
                        for node_keys in neighbors_by_state.values()
                        for node_key in node_keys
                    }
                    resolve_nodes(data, node_start, node_end, candidate_nodes)
                    for state_keys in trace_needed.values():
                        for state_key in state_keys:
                            state = states[state_key]
                            current = nodes.get(str(state["node_key"]))
                            assert current is not None
                            candidate_keys = neighbors_by_state.get(state_key, set())
                            valid_neighbors = {
                                key: nodes[key]
                                for key in candidate_keys
                                if key in nodes
                                and nodes[key].net.casefold() == str(state["net_key"])
                                and nodes[key].layer.casefold() == current.layer.casefold()
                                and key not in state["visited_node_keys"]
                            }
                            if not valid_neighbors:
                                same_layer_neighbors = {
                                    key
                                    for key in candidate_keys
                                    if key in nodes
                                    and nodes[key].net.casefold() == str(state["net_key"])
                                    and nodes[key].layer.casefold() == current.layer.casefold()
                                }
                                state["status"] = (
                                    "TRACE_CYCLE"
                                    if same_layer_neighbors
                                    and same_layer_neighbors.issubset(state["visited_node_keys"])
                                    else "TRACE_REQUIRED"
                                    if candidate_keys
                                    else "OVERSHOOT_OR_NO_MONOTONIC_VIA"
                                )
                                failures[str(state["status"])] += 1
                                continue
                            if len(valid_neighbors) != 1:
                                state["status"] = "AMBIGUOUS_TRACE_BRANCH"
                                failures[str(state["status"])] += 1
                                continue
                            next_key, next_node = next(iter(valid_neighbors.items()))
                            if int(state["trace_steps"]) >= max_trace_steps:
                                state["status"] = "TRACE_SEGMENT_LIMIT"
                                failures[str(state["status"])] += 1
                                continue
                            if int(state["transition_steps"]) >= max_total_steps:
                                state["status"] = "TOTAL_TRANSITION_LIMIT"
                                failures[str(state["status"])] += 1
                                continue
                            state["trace_steps"] = int(state["trace_steps"]) + 1
                            state["transition_steps"] = int(state["transition_steps"]) + 1
                            state["visited_node_keys"].add(next_key)
                            state["node_id"] = next_node.node_id
                            state["node_key"] = next_key
                            state["layer"] = next_node.layer
            for state in states.values():
                if state["status"] == "PENDING":
                    state["status"] = "SEGMENT_LIMIT"
                    failures[str(state["status"])] += 1
    except (MemoryError, OverflowError):
        diagnostics.append(
            SpdDiagnostic(
                "warning",
                "SPD_VIA_PATH_RESOURCE_GUARD",
                (
                    "The exact compact alternate-exit component index could not "
                    "be completed with available process resources, so all "
                    "terminal paths use the documented legacy rail template; no "
                    "partial source Via evidence was accepted."
                ),
            )
        )
        return finish(SpdViaPathRecovery(
            evidence_by_via={},
            diagnostics=tuple(diagnostics),
            statistics={
                "requested": len(states),
                "recovered": 0,
                "fallback": len(states),
                "resource_guard_fallback": len(states),
                "relevant_trace_records_indexed": relevant_trace_records_indexed,
                "alternate_exit_via_edges_retained": alternate_exit_via_edges_retained,
                "alternate_exit_nodes_retained": alternate_exit_nodes_retained,
            },
        ))
    except OSError as exc:
        raise SpdImportError(
            f"cannot recover source Via paths from {source_path}: {exc}"
        ) from exc

    # Replace provisional layer-index spans with physical conductor centre spans.
    centre_depth_um: dict[str, float] = {}
    depth_um = 0.0
    for layer in stackup_layers:
        centre_depth_um[layer.name.casefold()] = depth_um + float(layer.thickness_um) / 2.0
        depth_um += float(layer.thickness_um)
    corrected: dict[str, list[SpdViaPathEvidence]] = {}
    corrected_structural: dict[str, list[SpdViaStructuralEvidence]] = {}
    for via_key, items in evidence.items():
        corrected[via_key] = []
        for item in items:
            segments = tuple(
                SpdViaPathSegment(
                    via_id=segment.via_id,
                    padstack=segment.padstack,
                    drill_diameter_um=segment.drill_diameter_um,
                    start_layer=segment.start_layer,
                    end_layer=segment.end_layer,
                    length_um=abs(
                        centre_depth_um[segment.end_layer.casefold()]
                        - centre_depth_um[segment.start_layer.casefold()]
                    ),
                    end_x_um=segment.end_x_um,
                    end_y_um=segment.end_y_um,
                    rotation_degrees=segment.rotation_degrees,
                    padstack_material=segment.padstack_material,
                )
                for segment in item.segments
            )
            if any(segment.length_um <= 0 for segment in segments):
                failures["ZERO_PHYSICAL_SPAN"] += 1
                continue
            corrected[via_key].append(
                SpdViaPathEvidence(
                    via_id=item.via_id,
                    target_layer=item.target_layer,
                    target_node_id=item.target_node_id,
                    target_padstack=item.target_padstack,
                    target_pad_kind=item.target_pad_kind,
                    target_pad_width_um=item.target_pad_width_um,
                    target_pad_height_um=item.target_pad_height_um,
                    target_x_um=item.target_x_um,
                    target_y_um=item.target_y_um,
                    segments=segments,
                    trace_hops=item.trace_hops,
                    trace_alternate_exit=item.trace_alternate_exit,
                )
            )
    for via_key, items in structural_evidence.items():
        corrected_structural[via_key] = []
        for item in items:
            segments = tuple(
                SpdViaPathSegment(
                    via_id=segment.via_id,
                    padstack=segment.padstack,
                    drill_diameter_um=segment.drill_diameter_um,
                    start_layer=segment.start_layer,
                    end_layer=segment.end_layer,
                    length_um=abs(
                        centre_depth_um[segment.end_layer.casefold()]
                        - centre_depth_um[segment.start_layer.casefold()]
                    ),
                    end_x_um=segment.end_x_um,
                    end_y_um=segment.end_y_um,
                    rotation_degrees=segment.rotation_degrees,
                    padstack_material=segment.padstack_material,
                )
                for segment in item.segments
            )
            if any(segment.length_um <= 0 for segment in segments):
                continue
            corrected_structural[via_key].append(
                SpdViaStructuralEvidence(
                    via_id=item.via_id,
                    target_layer=item.target_layer,
                    target_node_id=item.target_node_id,
                    target_x_um=item.target_x_um,
                    target_y_um=item.target_y_um,
                    segments=segments,
                    trace_hops=item.trace_hops,
                    trace_alternate_exit=item.trace_alternate_exit,
                )
            )
    recovered = sum(len(items) for items in corrected.values())
    requested = len(states)
    if recovered:
        diagnostics.append(
            SpdDiagnostic(
                "info",
                "SPD_VIA_PATH_RECOVERED",
                f"Recovered {recovered:,}/{requested:,} unique source-proven TOP-to-plane "
                "Via paths; only their compact segment summaries were retained.",
            )
        )
    if requested - recovered:
        failure_summary = ", ".join(
            f"{code}={count}" for code, count in sorted(failures.items())
        ) or "no unique source path"
        diagnostics.append(
            SpdDiagnostic(
                "warning",
                "SPD_VIA_PATH_LEGACY_FALLBACK",
                f"{requested - recovered:,} terminal path(s) use the documented "
                f"legacy rail template ({failure_summary}).",
            )
        )
    reporter.report(100, "Recovered source-proven vertical Via paths")
    statistics: dict[str, int] = {
        "requested": requested,
        "recovered": recovered,
        "fallback": requested - recovered,
        "segments": sum(
            len(item.segments) for items in corrected.values() for item in items
        ),
        # Structural performance evidence: each relevant trace component and
        # (component, current-layer, target-layer) exit decision is memoized.
        "trace_components_indexed": alternate_trace_components,
        "alternate_exit_cache_entries": len(alternate_exit_cache),
        "relevant_trace_records_indexed": relevant_trace_records_indexed,
        "alternate_exit_trace_nodes_indexed": int(sum(alternate_trace_nodes)),
        "alternate_exit_via_edges_indexed": alternate_exit_via_edges_retained,
        "alternate_exit_via_edges_retained": alternate_exit_via_edges_retained,
        "alternate_exit_nodes_retained": alternate_exit_nodes_retained,
        "alternate_exit_numeric_capacity": alternate_numeric_capacity,
        "path_node_section_passes": path_node_section_passes,
        "alternate_exit_node_section_passes": alternate_node_section_passes,
        "alternate_exit_nodes_resolved": alternate_nodes_resolved,
    }
    statistics.update(
        {
            f"failure_{code.casefold()}": int(count)
            for code, count in sorted(failures.items())
            if count
        }
    )
    structural_recovered = sum(
        len(items) for items in corrected_structural.values()
    )
    if structural_recovered:
        # Keep the historical statistics shape byte-for-byte when no
        # structural-only evidence exists; old callers compare this mapping.
        statistics["structural_recovered"] = structural_recovered
    return finish(SpdViaPathRecovery(
        evidence_by_via={
            key: tuple(sorted(items, key=lambda item: item.target_layer.casefold()))
            for key, items in sorted(corrected.items())
            if items
        },
        structural_evidence_by_via={
            key: tuple(
                sorted(items, key=lambda item: item.target_layer.casefold())
            )
            for key, items in sorted(corrected_structural.items())
            if items
        },
        diagnostics=tuple(diagnostics),
        statistics=statistics,
    ))


def recover_spd_ground_reachability(
    path: str | Path,
    *,
    landings: Iterable[object],
    terminal_contact_landings: Iterable[object] | None = None,
    scenario_isolated_terminal_landings: Iterable[object] | None = None,
    retarget_destination_requests: Iterable[tuple[str, str, str]] | None = None,
    terminal_owned_via_ids: Iterable[str] | None = None,
    padstacks: Iterable[SpdPadStack] | None = None,
    stackup_layers: Iterable[StackupLayer] | None = None,
    target_layers_by_net: Mapping[str, Iterable[str]],
    target_node_predicate: Callable[[str, str, str, float, float], bool] | None = None,
    target_node_surface_resolver: Callable[
        [str, str, str, float, float], str | None
    ]
    | None = None,
    target_surface_island_ids: Mapping[
        tuple[str, str], Iterable[str]
    ]
    | None = None,
    expected_source: SpdSourceInfo | None = None,
    include_traces: bool = True,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> SpdGroundReachability:
    """Prove same-NET GND landing reachability to exact target layers.

    This is intentionally a separate batched graph pass from unique Via-path
    recovery: a branching Trace/Via graph is valid for return connectivity but
    cannot be condensed into one serial RL chain.  Only target-net records are
    retained. Node/Trace sections are scanned once; Via records are scanned
    twice so final full-component relevance can be classified without keeping
    millions of raw Via identities in memory.

    ``target_node_surface_resolver`` binds every accepted source Node coordinate
    to one deterministic exact-artwork island.  When paired with the complete
    ``target_surface_island_ids`` inventory, same-island Nodes and same-layer
    Trace paths form a separate equivalence graph. Via edges remain in the full
    reachability graph but are excluded from equipotential coalescing, so every
    layer/NET collapse receives a fail-closed same-layer proof.

    ``include_traces=False`` restricts the result to a directly joined local Via
    stack.  Distribution audits use that mode to distinguish unchanged-barrel
    reachability from the separate same-XY re-termination planning assumption.

    ``terminal_contact_landings`` adds compact landing-to-island evidence without
    multiplying each landing by every target layer in ``reachable_keys``.
    ``scenario_isolated_terminal_landings`` names editable decap landing Nodes
    whose same-layer artwork/Trace contacts must stay out of the permanent
    quotient.  Scenario compilation restores only the certified conditional
    pad/contact graph, so a moved or gap-removed capacitor cannot retain a raw
    TOP bypass through an earlier ideal union.
    ``retarget_destination_requests`` contains only the compact
    ``(NET, target layer, target Node)`` identities referenced by recovered
    decap path evidence.  The result binds those Nodes to retained quotient
    vertices without serializing the full raw Node inventory.
    ``terminal_owned_via_ids`` optionally classifies exact raw Via records into
    terminal-owned and substrate counts inside each island-pair aggregate.
    Supplying ``padstacks`` and ``stackup_layers`` together adds the same
    source-proven drill/material and conductor-centre segment lengths used by
    the finite Via R/L model; missing or ambiguous physical evidence is retained
    as an explicit incomplete aggregate rather than replaced by guessed values.
    """

    reporter = _Reporter(progress, is_cancelled)
    reporter.report(0, "Checking mixed-reference GND landing reachability")
    source_path = Path(path)
    if not source_path.is_file():
        raise SpdImportError(f"SPD source does not exist: {source_path}")
    # Keep the graph keys canonical while retaining a deterministic spelling
    # for the public exact-surface output.  Materialising each iterable once
    # also keeps generator-valued layer collections valid.
    target_layers: dict[str, set[str]] = {}
    target_net_display: dict[str, str] = {}
    target_layer_display: dict[tuple[str, str], str] = {}
    for raw_net, raw_layers in target_layers_by_net.items():
        net = str(raw_net).strip()
        layers = tuple(
            layer
            for raw_layer in raw_layers
            if (layer := str(raw_layer).strip())
        )
        if not net or not layers:
            continue
        net_key = net.casefold()
        previous_net = target_net_display.get(net_key)
        if previous_net is None or net < previous_net:
            target_net_display[net_key] = net
        layer_keys = target_layers.setdefault(net_key, set())
        for layer in layers:
            layer_key = layer.casefold()
            layer_keys.add(layer_key)
            display_key = (net_key, layer_key)
            previous_layer = target_layer_display.get(display_key)
            if previous_layer is None or layer < previous_layer:
                target_layer_display[display_key] = layer
    if (target_node_surface_resolver is None) != (
        target_surface_island_ids is None
    ):
        raise ValueError(
            "surface-island resolver and complete island inventory must be supplied together"
        )
    surface_island_inventory: dict[tuple[str, str], tuple[str, ...]] = {}
    seen_island_ids: set[str] = set()
    if target_surface_island_ids is not None:
        for raw_key, raw_ids in target_surface_island_ids.items():
            if len(raw_key) != 2:
                raise ValueError("surface-island inventory keys must contain NET and layer")
            net_key = str(raw_key[0]).strip().casefold()
            layer_key = str(raw_key[1]).strip().casefold()
            ids = tuple(sorted({str(item).strip() for item in raw_ids}))
            if (
                not net_key
                or not layer_key
                or not ids
                or any(not item for item in ids)
                or (net_key, layer_key) not in target_layer_display
            ):
                raise ValueError("surface-island inventory has an invalid surface row")
            if seen_island_ids.intersection(ids):
                raise ValueError("surface-island identities must be globally unique")
            seen_island_ids.update(ids)
            surface_island_inventory[(net_key, layer_key)] = ids
        expected_surface_keys = {
            (net_key, layer_key)
            for net_key, layers in target_layers.items()
            for layer_key in layers
        }
        if set(surface_island_inventory) != expected_surface_keys:
            raise ValueError(
                "surface-island inventory does not exactly cover every target layer/NET"
            )
    requested_by_key: dict[tuple[str, str, str], str] = {}
    contact_requested_by_key: dict[
        tuple[str, str], tuple[str, str, str, str, str]
    ] = {}
    contact_identity_by_via_key: dict[
        str, tuple[str, str, str, str]
    ] = {}
    terminal_ids_by_contact_key: dict[tuple[str, str], str] = {}

    def add_contact_landing(
        landing: object,
        *,
        request_reachability: bool,
        explicit_terminal_contact: bool,
        request_landing_contact: bool,
    ) -> None:
        try:
            net = str(getattr(landing, "net")).strip()
            via_id = str(getattr(landing, "via_id")).strip()
            node_id = str(getattr(landing, "endpoint_node_id")).strip()
        except AttributeError as exc:
            raise ValueError("GND landing lacks source graph identity") from exc
        if not net or not via_id or not node_id:
            raise ValueError("GND landing has a blank source graph identity")
        net_key = net.casefold()
        if net_key not in target_layers:
            return
        via_key = via_id.casefold()
        node_key = node_id.casefold()
        contact_key = (via_key, node_key)
        owner_kind = (
            "device"
            if str(getattr(landing, "pin_id", "") or "").strip()
            else "decap"
            if explicit_terminal_contact
            else "unknown"
        )
        owner_identity = (
            str(getattr(landing, "pin_id", "") or "").strip().casefold()
            if owner_kind == "device"
            else owner_kind
        )
        terminal_id = (
            str(getattr(landing, "pin_id", "") or "").strip()
            if owner_kind == "device"
            else f"decap-via:{via_key}:{node_key}"
            if owner_kind == "decap"
            else ""
        )
        if request_landing_contact and owner_kind == "device":
            via_contact_identity = (
                net_key,
                node_key,
                owner_kind,
                owner_identity,
            )
            previous_via_contact = contact_identity_by_via_key.get(via_key)
            if (
                previous_via_contact is not None
                and previous_via_contact != via_contact_identity
            ):
                raise ValueError(
                    "one physical terminal Via ID cannot be claimed by multiple "
                    "NETs, endpoint Nodes, or terminal owners"
                )
            contact_identity_by_via_key[via_key] = via_contact_identity
        candidate = (net_key, via_id, node_id, net, owner_kind)
        previous = contact_requested_by_key.get(contact_key)
        if previous is not None and previous[0] != net_key:
            raise ValueError("one terminal landing identity names multiple NETs")
        if (
            previous is not None
            and previous[4] != "unknown"
            and owner_kind != "unknown"
            and previous[4] != owner_kind
        ):
            raise ValueError("one terminal landing identity has conflicting owners")
        if request_landing_contact and (
            previous is None
            or (previous[4] == "unknown" and owner_kind != "unknown")
            or (previous[4] == owner_kind and (
            via_id.casefold(), node_id.casefold(), net.casefold(), via_id, node_id, net
        ) < (
            previous[1].casefold(),
            previous[2].casefold(),
            previous[3].casefold(),
            previous[1],
            previous[2],
            previous[3],
            ))
        ):
            contact_requested_by_key[contact_key] = candidate
            if terminal_id:
                terminal_ids_by_contact_key[contact_key] = terminal_id
        if request_reachability:
            for target_layer in target_layers[net_key]:
                requested_by_key[(via_key, node_key, target_layer)] = net_key

    for landing in landings:
        add_contact_landing(
            landing,
            request_reachability=True,
            explicit_terminal_contact=False,
            request_landing_contact=False,
        )
    if terminal_contact_landings is not None:
        for landing in terminal_contact_landings:
            add_contact_landing(
                landing,
                request_reachability=False,
                explicit_terminal_contact=True,
                request_landing_contact=True,
            )

    scenario_isolated_requested_by_key: dict[
        tuple[str, str], tuple[str, str, str]
    ] = {}
    scenario_isolated_nodes_by_net: dict[str, set[str]] = {}
    if scenario_isolated_terminal_landings is not None:
        for landing in scenario_isolated_terminal_landings:
            try:
                net = str(getattr(landing, "net")).strip()
                via_id = str(getattr(landing, "via_id")).strip()
                node_id = str(getattr(landing, "endpoint_node_id")).strip()
            except AttributeError as exc:
                raise ValueError(
                    "scenario-isolated landing lacks source graph identity"
                ) from exc
            if not net or not via_id or not node_id:
                raise ValueError(
                    "scenario-isolated landing has a blank source graph identity"
                )
            net_key = net.casefold()
            if net_key not in target_layers:
                continue
            landing_key = (via_id.casefold(), node_id.casefold())
            contact_row = contact_requested_by_key.get(landing_key)
            if contact_row is None or contact_row[4] != "decap":
                raise ValueError(
                    "scenario-isolated landing must be an explicit decap contact"
                )
            candidate = (net_key, via_id, node_id)
            previous = scenario_isolated_requested_by_key.get(landing_key)
            if previous is not None and previous[0] != net_key:
                raise ValueError(
                    "one scenario-isolated landing identity names multiple NETs"
                )
            scenario_isolated_requested_by_key[landing_key] = candidate
            scenario_isolated_nodes_by_net.setdefault(net_key, set()).add(
                node_id.casefold()
            )

    retarget_destination_requested_keys: set[tuple[str, str, str]] = set()
    if retarget_destination_requests is not None:
        for raw_request in retarget_destination_requests:
            if len(raw_request) != 3:
                raise ValueError(
                    "retarget-destination request must contain NET, layer, and Node"
                )
            net_key, layer_key, node_key = tuple(
                str(item).strip().casefold() for item in raw_request
            )
            if (
                not net_key
                or not layer_key
                or not node_key
                or layer_key not in target_layers.get(net_key, set())
            ):
                raise ValueError(
                    "retarget-destination request is outside the retained target inventory"
                )
            retarget_destination_requested_keys.add(
                (net_key, layer_key, node_key)
            )

    terminal_owned_keys = (
        None
        if terminal_owned_via_ids is None
        else {
            key
            for raw in terminal_owned_via_ids
            if (key := str(raw).strip().casefold())
        }
    )
    if (padstacks is None) != (stackup_layers is None):
        raise ValueError(
            "padstacks and stackup_layers must be supplied together for Via physics"
        )
    padstack_definitions_by_key: dict[str, list[SpdPadStack]] = {}
    physical_stackup = tuple(stackup_layers or ())
    stackup_positions_by_key: dict[str, list[int]] = {}
    stackup_display_by_key: dict[str, str] = {}
    stackup_center_um_by_key: dict[str, float] = {}
    if padstacks is not None:
        for padstack in padstacks:
            name = str(getattr(padstack, "name", "")).strip()
            if name:
                padstack_definitions_by_key.setdefault(
                    name.casefold(), []
                ).append(padstack)
        depth_um = 0.0
        for index, layer in enumerate(physical_stackup):
            name = str(getattr(layer, "name", "")).strip()
            try:
                thickness_um = float(getattr(layer, "thickness_um"))
            except (AttributeError, TypeError, ValueError):
                thickness_um = float("nan")
            if name:
                layer_key = name.casefold()
                stackup_positions_by_key.setdefault(layer_key, []).append(index)
                stackup_display_by_key.setdefault(layer_key, name)
                if isfinite(thickness_um) and thickness_um > 0.0:
                    stackup_center_um_by_key.setdefault(
                        layer_key, depth_um + thickness_um / 2.0
                    )
            if isfinite(thickness_um) and thickness_um > 0.0:
                depth_um += thickness_um

    def physical_model_for(
        padstack_key: str,
        start_layer_key: str,
        end_layer_key: str,
    ) -> tuple[
        float | None,
        str | None,
        tuple[SpdViaIslandPairSegment, ...],
        Literal["complete", "incomplete"],
        tuple[str, ...],
    ]:
        issues: set[str] = set()
        definitions = padstack_definitions_by_key.get(padstack_key, ())
        padstack = definitions[0] if len(definitions) == 1 else None
        if padstacks is None:
            issues.add("physical_model_source_not_supplied")
        elif not definitions:
            issues.add("padstack_definition_missing")
        elif len(definitions) > 1:
            issues.add("padstack_definition_ambiguous")
        drill_diameter_um: float | None = None
        material: str | None = None
        if padstack is not None:
            raw_drill = getattr(padstack, "drill_diameter_um", None)
            try:
                drill_diameter_um = float(raw_drill)
            except (TypeError, ValueError):
                drill_diameter_um = None
            if (
                drill_diameter_um is None
                or not isfinite(drill_diameter_um)
                or drill_diameter_um <= 0.0
            ):
                drill_diameter_um = None
                issues.add("drill_diameter_um_missing_or_invalid")
            material = str(getattr(padstack, "material", "") or "").strip() or None

        declared_by_key: dict[str, str] = {}
        duplicate_declared_layers = False
        for raw_layer in getattr(padstack, "layers", ()) if padstack is not None else ():
            layer = str(raw_layer).strip()
            if not layer:
                continue
            layer_key = layer.casefold()
            if layer_key in declared_by_key:
                duplicate_declared_layers = True
            else:
                declared_by_key[layer_key] = layer
        if duplicate_declared_layers:
            issues.add("padstack_conductor_layer_duplicated")
        if any(
            len(stackup_positions_by_key.get(layer_key, ())) != 1
            or layer_key not in stackup_center_um_by_key
            or not bool(
                getattr(
                    physical_stackup[
                        stackup_positions_by_key[layer_key][0]
                    ],
                    "is_conductor",
                    False,
                )
            )
            for layer_key in declared_by_key
        ):
            issues.add("padstack_conductor_layer_missing_or_ambiguous")
        declared_order = sorted(
            (
                stackup_positions_by_key[layer_key][0],
                layer_key,
            )
            for layer_key in declared_by_key
            if len(stackup_positions_by_key.get(layer_key, ())) == 1
            and layer_key in stackup_center_um_by_key
            and bool(
                getattr(
                    physical_stackup[
                        stackup_positions_by_key[layer_key][0]
                    ],
                    "is_conductor",
                    False,
                )
            )
        )
        ordered_layer_keys = [item[1] for item in declared_order]
        if (
            start_layer_key not in ordered_layer_keys
            or end_layer_key not in ordered_layer_keys
        ):
            issues.add("via_endpoint_layer_not_declared_by_padstack")
            path_layer_keys: list[str] = []
        else:
            start_index = ordered_layer_keys.index(start_layer_key)
            end_index = ordered_layer_keys.index(end_layer_key)
            if start_index == end_index:
                issues.add("via_endpoint_layers_are_identical")
                path_layer_keys = []
            elif start_index < end_index:
                path_layer_keys = ordered_layer_keys[start_index : end_index + 1]
            else:
                path_layer_keys = list(
                    reversed(ordered_layer_keys[end_index : start_index + 1])
                )
        segments: list[SpdViaIslandPairSegment] = []
        for ordinal, (first_key, second_key) in enumerate(
            zip(path_layer_keys, path_layer_keys[1:], strict=False)
        ):
            length_um = abs(
                stackup_center_um_by_key[second_key]
                - stackup_center_um_by_key[first_key]
            )
            if not isfinite(length_um) or length_um <= 0.0:
                issues.add("segment_length_not_positive_finite")
                continue
            segments.append(
                SpdViaIslandPairSegment(
                    ordinal=ordinal,
                    start_layer=stackup_display_by_key[first_key],
                    end_layer=stackup_display_by_key[second_key],
                    length_um=length_um,
                )
            )
        if not segments:
            issues.add("physical_segment_chain_missing")
        status: Literal["complete", "incomplete"] = (
            "complete" if not issues else "incomplete"
        )
        return (
            drill_diameter_um,
            material,
            tuple(segments),
            status,
            tuple(sorted(issues)),
        )
    contact_landing_keys_by_via: dict[
        str, list[tuple[str, str]]
    ] = {}
    for landing_key in contact_requested_by_key:
        contact_landing_keys_by_via.setdefault(landing_key[0], []).append(
            landing_key
        )
    for keys in contact_landing_keys_by_via.values():
        keys.sort()
    requested = sorted(requested_by_key)
    if not requested and not surface_island_inventory and not contact_requested_by_key:
        return SpdGroundReachability(frozenset(), frozenset(), {
            "requested": 0, "reachable": 0, "unreachable": 0,
            "node_section_passes": 0, "trace_section_passes": 0,
            "via_section_passes": 0, "components": 0,
        })

    try:
        observed = source_path.stat()
    except OSError as exc:
        raise SpdImportError(f"cannot stat SPD source {source_path}: {exc}") from exc
    if expected_source is not None:
        try:
            same_path = source_path.resolve() == expected_source.path.resolve()
        except OSError as exc:
            raise SpdImportError(f"cannot resolve SPD source identity {source_path}: {exc}") from exc
        if not same_path or (int(observed.st_size), int(observed.st_mtime_ns)) != (
            int(expected_source.size_bytes), int(expected_source.mtime_ns)
        ):
            raise SpdImportError(
                "SPD source identity changed after analysis before mixed-reference "
                "GND reachability recovery"
            )

    before_identity = (int(observed.st_size), int(observed.st_mtime_ns))

    def finish(result: SpdGroundReachability) -> SpdGroundReachability:
        try:
            after = source_path.stat()
        except OSError as exc:
            raise SpdImportError(
                f"cannot stat SPD source {source_path} after mixed-reference "
                f"GND reachability recovery: {exc}"
            ) from exc
        if (int(after.st_size), int(after.st_mtime_ns)) != before_identity:
            raise SpdImportError(
                "SPD source changed during mixed-reference GND reachability "
                "recovery; import aborted and no mixed-source witness was persisted"
            )
        return result

    # A set-valued adjacency graph holds two Python objects for virtually every
    # source edge.  Large SPD files can have millions of GND Trace/Via records,
    # making that representation several gigabytes even though reachability only
    # needs connected-component membership.  Keep one string-to-dense-index map
    # per NET and a compact disjoint-set forest instead.  This remains exact:
    # every accepted same-NET Trace/Via edge is unioned and target-layer bits are
    # reduced only after all components are complete.
    target_bit_by_key = {
        key: 1 << index
        for index, key in enumerate(
            sorted(
                (net, layer)
                for net, layers in target_layers.items()
                for layer in layers
            )
        )
    }
    target_nodes_by_net: dict[str, dict[str, int]] = {
        net: {} for net in target_layers
    }
    target_island_by_node: dict[tuple[str, str], str] = {}
    island_anchor_node: dict[tuple[str, str], str] = {}
    island_target_node_counts: dict[tuple[str, str], int] = {}
    node_index_by_net: dict[str, dict[str, int]] = {
        net: {} for net in target_layers
    }
    parents = array("I")
    ranks = bytearray()
    # A second compact forest proves only same-layer equipotential ownership.
    # It receives artwork-island and same-layer Trace unions, never Via unions;
    # otherwise a finite cross-layer detour could incorrectly justify an ideal
    # coalescing of disconnected islands on one physical surface.
    equivalence_parents = array("I")
    equivalence_ranks = bytearray()
    node_layer_codes = array("I")
    layer_code_by_key: dict[str, int] = {}
    layer_key_by_code: dict[int, str] = {}
    layer_display_by_key: dict[str, str] = {}
    components = 0
    graph_edges = 0
    via_graph_edges = 0
    artwork_island_unions = 0
    same_layer_trace_edges = 0
    scenario_isolated_suppressed_artwork_contacts = 0
    scenario_isolated_suppressed_trace_edges = 0
    retarget_destination_observed_keys: set[tuple[str, str, str]] = set()
    via_island_pair_counts: dict[
        tuple[str, str, str, str, str, str], int
    ] = {}
    via_island_pair_terminal_counts: dict[
        tuple[str, str, str, str, str, str], int
    ] = {}
    via_island_pair_hashes: dict[
        tuple[str, str, str, str, str, str], Any
    ] = {}
    via_island_pair_display: dict[
        tuple[str, str, str, str, str, str],
        tuple[
            str,
            str,
            str,
            str,
            str,
            str,
            tuple[str, ...],
            tuple[str, ...],
        ],
    ] = {}
    via_island_pair_records = 0
    via_island_pair_missing_endpoint_records = 0
    observed_terminal_owned_via_keys: set[str] = set()
    terminal_internal_endpoint_candidates: dict[
        tuple[str, str], dict[str, str]
    ] = {}
    terminal_via_padstack_candidates: dict[
        tuple[str, str], dict[str, str]
    ] = {}
    terminal_owned_unpaired_count = 0
    terminal_owned_unpaired_hash = hashlib.sha256()
    unsupported_missing_endpoint_count = 0
    unsupported_missing_endpoint_hash = hashlib.sha256()
    outside_retained_interface_scope_count = 0
    outside_retained_interface_scope_hash = hashlib.sha256()
    island_equivalence_root_by_key: dict[tuple[str, str], int] = {}
    equivalence_component_islands_by_key: dict[
        tuple[str, str, int], tuple[str, ...]
    ] = {}
    retained_component_keys_by_full_root: dict[
        int, set[tuple[str, str, int]]
    ] = {}
    # v4 finite network: retain only compact integer endpoint/PadStack codes
    # for raw Via edges.  Same-layer Trace/artwork equivalence is already in
    # ``equivalence_parents``; these arrays are sufficient to build its exact
    # quotient graph without Python adjacency objects per source record.
    finite_via_first_indices = array("I")
    finite_via_second_indices = array("I")
    finite_via_padstack_codes = array("I")
    finite_padstack_code_by_key: dict[str, int] = {}
    finite_padstack_key_by_code: list[str] = []
    finite_padstack_display_by_code: list[str] = []
    finite_first_edge_index_by_via_key: dict[str, int] = {}
    source_sha256_for_ids = (
        expected_source.sha256.casefold() if expected_source is not None else ""
    )
    finite_via_vertices: list[SpdFiniteViaQuotientVertex] = []
    finite_via_edges: list[SpdFiniteViaQuotientEdge] = []
    finite_via_vertex_id_by_landing: dict[tuple[str, str], str] = {}
    finite_via_edge_id_by_landing: dict[tuple[str, str], str] = {}
    finite_via_path_index_by_landing: dict[tuple[str, str], int] = {}
    finite_landing_root_by_key: dict[tuple[str, str], int] = {}
    finite_vertex_member_count: dict[int, int] = {}
    finite_retained_by_root: dict[int, dict[str, tuple[str, ...]]] = {}
    finite_vertex_id_by_root: dict[int, str] = {}
    finite_raw_via_hash = hashlib.sha256()
    finite_modeled_via_hash = hashlib.sha256()
    finite_modeled_owner_hash = hashlib.sha256()
    finite_outside_via_hash = hashlib.sha256()
    finite_modeled_via_count = 0
    finite_outside_via_count = 0
    finite_physical_complete_via_count = 0
    finite_physical_incomplete_via_count = 0
    finite_reduced_paths: list[dict[str, Any]] = []
    finite_raw_edge_path_index = array("i")
    finite_raw_edge_physical_complete = bytearray()

    def index_for(net_key: str, node_key: str) -> int:
        nonlocal components
        by_node = node_index_by_net[net_key]
        existing = by_node.get(node_key)
        if existing is not None:
            return existing
        index = len(parents)
        by_node[node_key] = index
        parents.append(index)
        ranks.append(0)
        equivalence_parents.append(index)
        equivalence_ranks.append(0)
        node_layer_codes.append(0)
        components += 1
        return index

    def find(index: int) -> int:
        root = index
        while parents[root] != root:
            root = parents[root]
        while parents[index] != index:
            parent = parents[index]
            parents[index] = root
            index = parent
        return root

    def union(net_key: str, first: str, second: str) -> None:
        nonlocal components
        first_root = find(index_for(net_key, first))
        second_root = find(index_for(net_key, second))
        if first_root == second_root:
            return
        if ranks[first_root] < ranks[second_root]:
            first_root, second_root = second_root, first_root
        parents[second_root] = first_root
        if ranks[first_root] == ranks[second_root]:
            ranks[first_root] += 1
        components -= 1

    def equivalence_find(index: int) -> int:
        root = index
        while equivalence_parents[root] != root:
            root = equivalence_parents[root]
        while equivalence_parents[index] != index:
            parent = equivalence_parents[index]
            equivalence_parents[index] = root
            index = parent
        return root

    def equivalence_union(first: int, second: int) -> None:
        first_root = equivalence_find(first)
        second_root = equivalence_find(second)
        if first_root == second_root:
            return
        if equivalence_ranks[first_root] < equivalence_ranks[second_root]:
            first_root, second_root = second_root, first_root
        equivalence_parents[second_root] = first_root
        if equivalence_ranks[first_root] == equivalence_ranks[second_root]:
            equivalence_ranks[first_root] += 1

    def layer_code(layer_key: str) -> int:
        code = layer_code_by_key.get(layer_key)
        if code is None:
            code = len(layer_code_by_key) + 1
            layer_code_by_key[layer_key] = code
            layer_key_by_code[code] = layer_key
        return code

    def node_identity(raw: bytes) -> tuple[str, str] | None:
        cuts = [
            value for value in (raw.find(b"!!"), raw.find(b"::"), raw.find(b" "))
            if value >= 0
        ]
        separator = raw.find(b"::")
        if not cuts or separator < 0:
            return None
        node_id = _decode(raw[: min(cuts)])
        net_token = raw[separator + 2 :].split(None, 1)[0]
        return (node_id, _decode(net_token)) if node_id and net_token else None

    def update_via_id_digest(digest: Any, via_key: str) -> None:
        encoded_via_id = via_key.encode("utf-8")
        digest.update(len(encoded_via_id).to_bytes(4, "big"))
        digest.update(encoded_via_id)

    try:
        with source_path.open("rb") as handle, mmap.mmap(
            handle.fileno(), 0, access=mmap.ACCESS_READ
        ) as data:
            if expected_source is not None:
                observed_sha256 = hashlib.sha256(data).hexdigest()
                if observed_sha256.casefold() != expected_source.sha256.casefold():
                    raise SpdImportError(
                        "SPD source SHA-256 mismatch after analysis before mixed-reference "
                        "GND reachability recovery; import aborted so connectivity "
                        "evidence cannot be mixed with replacement bytes"
                    )
                source_sha256_for_ids = observed_sha256.casefold()
            elif not source_sha256_for_ids:
                source_sha256_for_ids = hashlib.sha256(data).hexdigest().casefold()
            node_start = _find_line(data, b"* Node description lines")
            trace_start = _find_line(data, b"* Trace description lines")
            via_start = _find_line(data, b"* Via description lines")
            pad_start = _find_line(data, b"* PadStack collection description lines")
            if node_start < 0 or via_start < 0:
                return finish(SpdGroundReachability(
                    frozenset(), frozenset(requested), {
                        "requested": len(requested), "reachable": 0,
                        "unreachable": len(requested), "node_section_passes": 0,
                        "trace_section_passes": 0, "via_section_passes": 0,
                        "components": 0,
                    }
                ))
            node_end = trace_start if trace_start > node_start else via_start
            trace_end = via_start if via_start > trace_start else pad_start
            via_end = pad_start if pad_start > via_start else len(data)
            reporter.report(15, "Indexing exact mixed-reference GND target nodes")
            for index, (_offset, raw) in enumerate(_iter_lines(data, node_start, node_end)):
                if index % 16384 == 0:
                    reporter.check()
                if not raw.startswith(b"Node"):
                    continue
                identity = node_identity(raw)
                if identity is None:
                    continue
                node_id, net = identity
                net_key = net.casefold()
                if net_key not in target_layers:
                    continue
                layer_raw = _attribute(raw, b"Layer")
                if layer_raw is None:
                    continue
                layer_key = _decode(layer_raw).casefold()
                layer_display = _decode(layer_raw).strip()
                previous_layer_display = layer_display_by_key.get(layer_key)
                if (
                    layer_display
                    and (
                        previous_layer_display is None
                        or layer_display < previous_layer_display
                    )
                ):
                    layer_display_by_key[layer_key] = layer_display
                node_key = node_id.casefold()
                scenario_isolated_nodes = scenario_isolated_nodes_by_net.get(
                    net_key
                )
                is_scenario_isolated = (
                    scenario_isolated_nodes is not None
                    and node_key in scenario_isolated_nodes
                )
                node_index = index_for(net_key, node_key)
                observed_layer_code = layer_code(layer_key)
                previous_layer_code = node_layer_codes[node_index]
                if previous_layer_code not in (0, observed_layer_code):
                    raise SpdImportError(
                        "one same-NET source Node identity appears on multiple layers"
                    )
                node_layer_codes[node_index] = observed_layer_code
                retarget_destination_key = (net_key, layer_key, node_key)
                if (
                    retarget_destination_key
                    in retarget_destination_requested_keys
                ):
                    retarget_destination_observed_keys.add(
                        retarget_destination_key
                    )
                if layer_key in target_layers[net_key]:
                    x_um: float | None = None
                    y_um: float | None = None
                    if (
                        target_node_predicate is not None
                        or target_node_surface_resolver is not None
                    ):
                        attributes = _NODE_ATTR_RE.search(raw)
                        if attributes is None:
                            continue
                        try:
                            x_um = _length_um(attributes.group(1))
                            y_um = _length_um(attributes.group(2))
                        except ValueError:
                            continue
                        if target_node_predicate is not None and not target_node_predicate(
                            net, _decode(layer_raw), node_id, x_um, y_um
                        ):
                            continue
                    if target_node_surface_resolver is not None:
                        assert x_um is not None and y_um is not None
                        island_id = target_node_surface_resolver(
                            net, _decode(layer_raw), node_id, x_um, y_um
                        )
                        expected_ids = surface_island_inventory.get(
                            (net_key, layer_key), ()
                        )
                        if island_id is None:
                            continue
                        island_id = str(island_id).strip()
                        if island_id not in expected_ids:
                            raise SpdImportError(
                                "surface-island resolver returned an identity outside "
                                f"the exact inventory for {net!r} on {_decode(layer_raw)!r}"
                            )
                        if is_scenario_isolated:
                            # The retained artwork still proves where this pad
                            # Node originated, but it must not become a
                            # permanent ideal contact.  Scenario compilation
                            # restores the exact source pad/contact graph and
                            # can therefore remove a gap cell without a raw
                            # artwork bypass.
                            scenario_isolated_suppressed_artwork_contacts += 1
                            continue
                        previous_island = target_island_by_node.get(
                            (net_key, node_key)
                        )
                        if previous_island is not None and previous_island != island_id:
                            raise SpdImportError(
                                "one source Node resolved to multiple exact artwork islands"
                            )
                        target_island_by_node[(net_key, node_key)] = island_id
                        island_key = (net_key, island_id)
                        previous_anchor = island_anchor_node.get(island_key)
                        if previous_anchor is None:
                            island_anchor_node[island_key] = node_key
                        else:
                            before = components
                            union(net_key, node_key, previous_anchor)
                            equivalence_union(
                                node_index,
                                index_for(net_key, previous_anchor),
                            )
                            if components != before:
                                artwork_island_unions += 1
                        island_target_node_counts[island_key] = (
                            island_target_node_counts.get(island_key, 0) + 1
                        )
                    target_nodes = target_nodes_by_net[net_key]
                    target_nodes[node_key] = (
                        target_nodes.get(node_key, 0)
                        | target_bit_by_key[(net_key, layer_key)]
                    )
            if include_traces and trace_start >= 0 and trace_end > trace_start:
                reporter.report(40, "Indexing same-NET GND Trace connectivity")
                for index, match in enumerate(_TRACE_RE.finditer(data, trace_start, trace_end)):
                    if index % 8192 == 0:
                        reporter.check()
                    net_key = _decode(match.group(2)).casefold()
                    if net_key not in node_index_by_net:
                        continue
                    first, second = _decode(match.group(3)).casefold(), _decode(match.group(4)).casefold()
                    first_index = index_for(net_key, first)
                    second_index = index_for(net_key, second)
                    union(net_key, first, second)
                    if (
                        node_layer_codes[first_index] != 0
                        and node_layer_codes[first_index]
                        == node_layer_codes[second_index]
                    ):
                        isolated_nodes = scenario_isolated_nodes_by_net.get(net_key)
                        if isolated_nodes is not None and (
                            first in isolated_nodes or second in isolated_nodes
                        ):
                            scenario_isolated_suppressed_trace_edges += 1
                        else:
                            equivalence_union(first_index, second_index)
                            same_layer_trace_edges += 1
                    graph_edges += 1
            reporter.report(65, "Indexing same-NET GND Via connectivity")
            for index, match in enumerate(_VIA_RE.finditer(data, via_start, via_end)):
                if index % 8192 == 0:
                    reporter.check()
                net_key = _decode(match.group(2)).casefold()
                if net_key not in node_index_by_net:
                    continue
                first = _decode(match.group(3)).strip().casefold()
                second = _decode(match.group(4)).strip().casefold()
                first_index = index_for(net_key, first)
                second_index = index_for(net_key, second)
                padstack = _decode(match.group(5)).strip()
                padstack_key = padstack.casefold()
                padstack_code = finite_padstack_code_by_key.get(padstack_key)
                if padstack_code is None:
                    padstack_code = len(finite_padstack_key_by_code)
                    finite_padstack_code_by_key[padstack_key] = padstack_code
                    finite_padstack_key_by_code.append(padstack_key)
                    finite_padstack_display_by_code.append(padstack)
                finite_edge_index = len(finite_via_first_indices)
                finite_via_first_indices.append(first_index)
                finite_via_second_indices.append(second_index)
                finite_via_padstack_codes.append(padstack_code)
                via_key = _decode(match.group(1)).strip().casefold()
                if via_key in contact_landing_keys_by_via:
                    if via_key in finite_first_edge_index_by_via_key:
                        raise SpdImportError(
                            "one terminal Via ID names multiple raw Via records"
                        )
                    finite_first_edge_index_by_via_key[via_key] = finite_edge_index
                union(net_key, first, second)
                graph_edges += 1
                via_graph_edges += 1

            # Freeze the same-layer Trace/artwork partition before classifying
            # any Via endpoint.  A Via endpoint may sit outside a polygon yet
            # reach one certified surface component through exact same-layer
            # Trace records; direct coordinate coverage is only the fast path.
            island_equivalence_root_by_key = {
                island_key: equivalence_find(
                    index_for(island_key[0], anchor_node)
                )
                for island_key, anchor_node in island_anchor_node.items()
            }
            mutable_component_islands: dict[
                tuple[str, str, int], set[str]
            ] = {}
            for (net_key, layer_key), island_ids in sorted(
                surface_island_inventory.items()
            ):
                for island_id in island_ids:
                    equivalence_root = island_equivalence_root_by_key.get(
                        (net_key, island_id)
                    )
                    if equivalence_root is None:
                        continue
                    mutable_component_islands.setdefault(
                        (net_key, layer_key, equivalence_root), set()
                    ).add(island_id)
            equivalence_component_islands_by_key = {
                key: tuple(sorted(island_ids))
                for key, island_ids in mutable_component_islands.items()
            }
            for component_key in equivalence_component_islands_by_key:
                net_key, _layer_key, equivalence_root = component_key
                full_root = find(equivalence_root)
                retained_component_keys_by_full_root.setdefault(
                    full_root, set()
                ).add(component_key)

            def endpoint_component(
                net_key: str,
                node_index: int,
            ) -> tuple[str, tuple[str, ...]] | None:
                observed_layer_code = node_layer_codes[node_index]
                if observed_layer_code == 0:
                    return None
                layer_key = layer_key_by_code[observed_layer_code]
                island_ids = equivalence_component_islands_by_key.get(
                    (net_key, layer_key, equivalence_find(node_index))
                )
                return (
                    (layer_key, island_ids)
                    if island_ids is not None
                    else None
                )

            # Build the v4 finite-R/L quotient before the second Via scan.  A
            # quotient vertex is one exact same-layer Trace/artwork component;
            # every raw Via remains one finite edge until the bridge-safe
            # reducer below proves a degree-two series contraction.  This is
            # the compact-array equivalent of ``finite-route-reduction-v1``:
            # parallel/cycle edges and degree>2 junctions stay explicit.
            reporter.report(68, "Reducing the finite Via quotient graph")
            finite_node_count = len(parents)
            finite_root_layer_codes = array("I", [0]) * finite_node_count
            finite_net_key_by_root: dict[int, str] = {}
            for net_key, by_node in node_index_by_net.items():
                for node_index in by_node.values():
                    root = equivalence_find(node_index)
                    previous_net = finite_net_key_by_root.get(root)
                    if previous_net is not None and previous_net != net_key:
                        raise SpdImportError(
                            "same-layer quotient root spans multiple NETs"
                        )
                    finite_net_key_by_root[root] = net_key
                    observed_code = node_layer_codes[node_index]
                    if observed_code == 0:
                        continue
                    previous_code = finite_root_layer_codes[root]
                    if previous_code not in (0, observed_code):
                        raise SpdImportError(
                            "same-layer quotient root spans multiple conductor layers"
                        )
                    finite_root_layer_codes[root] = observed_code

            finite_retained_by_root: dict[
                int, dict[str, tuple[str, ...]]
            ] = {}
            for (
                net_key,
                layer_key,
                root,
            ), island_ids in equivalence_component_islands_by_key.items():
                display_layer = target_layer_display[(net_key, layer_key)]
                finite_retained_by_root.setdefault(root, {})[
                    display_layer
                ] = island_ids

            finite_terminal_ids_by_root: dict[int, set[str]] = {}
            finite_landing_root_by_key: dict[tuple[str, str], int] = {}
            for landing_key, contact_row in contact_requested_by_key.items():
                net_key = contact_row[0]
                source_index = index_for(net_key, landing_key[1])
                source_root = equivalence_find(source_index)
                finite_landing_root_by_key[landing_key] = source_root
                terminal_id = terminal_ids_by_contact_key.get(landing_key)
                if terminal_id:
                    finite_terminal_ids_by_root.setdefault(
                        source_root, set()
                    ).add(terminal_id)

            finite_retarget_cut_ids_by_root: dict[int, set[str]] = {}
            finite_first_edge_by_landing: dict[tuple[str, str], int] = {}
            for landing_key, contact_row in contact_requested_by_key.items():
                via_key = landing_key[0]
                edge_index = finite_first_edge_index_by_via_key.get(via_key)
                if edge_index is None:
                    continue
                source_root = finite_landing_root_by_key[landing_key]
                first_root = equivalence_find(
                    finite_via_first_indices[edge_index]
                )
                second_root = equivalence_find(
                    finite_via_second_indices[edge_index]
                )
                if source_root == first_root:
                    opposite_root = second_root
                elif source_root == second_root:
                    opposite_root = first_root
                else:
                    raise SpdImportError(
                        "terminal first Via is not incident to its exposed quotient vertex"
                    )
                finite_first_edge_by_landing[landing_key] = edge_index
                if contact_row[4] == "decap":
                    finite_retarget_cut_ids_by_root.setdefault(
                        opposite_root, set()
                    ).add(f"retarget-cut:{via_key}")

            finite_boundary_roots = (
                set(finite_retained_by_root)
                | set(finite_terminal_ids_by_root)
                | set(finite_retarget_cut_ids_by_root)
            )
            finite_boundaries_by_full_root: dict[int, set[int]] = {}
            for root in finite_boundary_roots:
                finite_boundaries_by_full_root.setdefault(find(root), set()).add(
                    root
                )
            finite_relevant_full_roots = {
                full_root
                for full_root, roots in finite_boundaries_by_full_root.items()
                if len(roots) >= 2
            }

            # Replace raw Node indices with exact same-layer quotient roots.
            # The original Node identities remain available in
            # ``node_index_by_net`` for deterministic vertex certificates.
            finite_candidate_edge = bytearray(len(finite_via_first_indices))
            finite_active_edge = bytearray(len(finite_via_first_indices))
            finite_degrees = array("I", [0]) * finite_node_count
            for edge_index in range(len(finite_via_first_indices)):
                first_root = equivalence_find(finite_via_first_indices[edge_index])
                second_root = equivalence_find(finite_via_second_indices[edge_index])
                finite_via_first_indices[edge_index] = first_root
                finite_via_second_indices[edge_index] = second_root
                if (
                    first_root == second_root
                    or find(first_root) not in finite_relevant_full_roots
                ):
                    continue
                finite_candidate_edge[edge_index] = 1
                finite_active_edge[edge_index] = 1
                finite_degrees[first_root] += 1
                finite_degrees[second_root] += 1

            finite_indptr = array("I", [0]) * (finite_node_count + 1)
            adjacency_slot_count = 0
            for node_index in range(finite_node_count):
                finite_indptr[node_index] = adjacency_slot_count
                adjacency_slot_count += finite_degrees[node_index]
            finite_indptr[finite_node_count] = adjacency_slot_count
            finite_adjacency_edges = array("I", [0]) * adjacency_slot_count
            finite_cursor = array("I", finite_indptr[:-1])
            for edge_index in range(len(finite_via_first_indices)):
                if not finite_candidate_edge[edge_index]:
                    continue
                first_root = finite_via_first_indices[edge_index]
                second_root = finite_via_second_indices[edge_index]
                first_slot = finite_cursor[first_root]
                finite_adjacency_edges[first_slot] = edge_index
                finite_cursor[first_root] += 1
                second_slot = finite_cursor[second_root]
                finite_adjacency_edges[second_slot] = edge_index
                finite_cursor[second_root] += 1
            del finite_cursor

            def finite_other(edge_index: int, node_index: int) -> int:
                first_root = finite_via_first_indices[edge_index]
                second_root = finite_via_second_indices[edge_index]
                if node_index == first_root:
                    return second_root
                if node_index == second_root:
                    return first_root
                raise SpdImportError("finite Via quotient incidence mismatch")

            # Prune only passive non-boundary dangling trees.  Their exact raw
            # owners are still retained in the coverage ledger as
            # ``pruned_dangling``; no edge is silently dropped.
            finite_active_degree = array("I", finite_degrees)
            finite_prune_queue = deque(
                node_index
                for node_index, degree in enumerate(finite_active_degree)
                if degree <= 1
                and degree > 0
                and node_index not in finite_boundary_roots
            )
            finite_queued = bytearray(finite_node_count)
            for node_index in finite_prune_queue:
                finite_queued[node_index] = 1
            while finite_prune_queue:
                node_index = finite_prune_queue.popleft()
                finite_queued[node_index] = 0
                if (
                    node_index in finite_boundary_roots
                    or finite_active_degree[node_index] > 1
                ):
                    continue
                active_incident = -1
                for slot in range(
                    finite_indptr[node_index], finite_indptr[node_index + 1]
                ):
                    edge_index = finite_adjacency_edges[slot]
                    if finite_active_edge[edge_index]:
                        active_incident = edge_index
                        break
                if active_incident >= 0:
                    finite_active_edge[active_incident] = 0
                    neighbor = finite_other(active_incident, node_index)
                    finite_active_degree[node_index] -= 1
                    finite_active_degree[neighbor] -= 1
                    if (
                        neighbor not in finite_boundary_roots
                        and finite_active_degree[neighbor] <= 1
                        and not finite_queued[neighbor]
                    ):
                        finite_prune_queue.append(neighbor)
                        finite_queued[neighbor] = 1

            # Exact iterative Tarjan bridge classification.  Parent-edge ID,
            # rather than parent vertex, makes parallel Via edges non-bridges.
            finite_discovery = array("i", [-1]) * finite_node_count
            finite_low = array("i", [0]) * finite_node_count
            finite_parent_edge = array("i", [-1]) * finite_node_count
            finite_parent_node = array("i", [-1]) * finite_node_count
            finite_bridges = bytearray(len(finite_via_first_indices))
            finite_timer = 0
            for start_node in range(finite_node_count):
                if (
                    finite_active_degree[start_node] == 0
                    or finite_discovery[start_node] >= 0
                ):
                    continue
                finite_discovery[start_node] = finite_timer
                finite_low[start_node] = finite_timer
                finite_timer += 1
                # Mutable [node, next CSR slot] frames avoid recursion on
                # production stacks containing tens of thousands of layers hops.
                finite_stack: list[list[int]] = [
                    [start_node, finite_indptr[start_node]]
                ]
                while finite_stack:
                    node_index, slot = finite_stack[-1]
                    end_slot = finite_indptr[node_index + 1]
                    while slot < end_slot and not finite_active_edge[
                        finite_adjacency_edges[slot]
                    ]:
                        slot += 1
                    if slot >= end_slot:
                        finite_stack.pop()
                        parent_node = finite_parent_node[node_index]
                        parent_edge = finite_parent_edge[node_index]
                        if parent_node >= 0:
                            finite_low[parent_node] = min(
                                finite_low[parent_node], finite_low[node_index]
                            )
                            if finite_low[node_index] > finite_discovery[parent_node]:
                                finite_bridges[parent_edge] = 1
                        continue
                    edge_index = finite_adjacency_edges[slot]
                    finite_stack[-1][1] = slot + 1
                    if edge_index == finite_parent_edge[node_index]:
                        continue
                    neighbor = finite_other(edge_index, node_index)
                    if finite_discovery[neighbor] < 0:
                        finite_parent_edge[neighbor] = edge_index
                        finite_parent_node[neighbor] = node_index
                        finite_discovery[neighbor] = finite_timer
                        finite_low[neighbor] = finite_timer
                        finite_timer += 1
                        finite_stack.append(
                            [neighbor, finite_indptr[neighbor]]
                        )
                    else:
                        finite_low[node_index] = min(
                            finite_low[node_index], finite_discovery[neighbor]
                        )

            finite_contractible = bytearray(finite_node_count)
            for node_index in range(finite_node_count):
                if (
                    finite_active_degree[node_index] != 2
                    or node_index in finite_boundary_roots
                ):
                    continue
                incident = []
                neighbors = set()
                for slot in range(
                    finite_indptr[node_index], finite_indptr[node_index + 1]
                ):
                    edge_index = finite_adjacency_edges[slot]
                    if finite_active_edge[edge_index]:
                        incident.append(edge_index)
                        neighbors.add(finite_other(edge_index, node_index))
                if (
                    len(incident) == 2
                    and len(neighbors) == 2
                    and all(finite_bridges[edge_index] for edge_index in incident)
                ):
                    finite_contractible[node_index] = 1

            finite_kept_roots = {
                node_index
                for node_index in range(finite_node_count)
                if finite_active_degree[node_index] > 0
                and not finite_contractible[node_index]
            }
            # A direct terminal/artwork ideal union may have no surviving Via
            # edge yet still needs one external MNA binding vertex.
            finite_kept_roots.update(finite_boundary_roots)

            finite_vertex_member_count: dict[int, int] = {
                root: 0 for root in finite_kept_roots
            }
            finite_vertex_member_hash: dict[int, Any] = {
                root: hashlib.sha256() for root in finite_kept_roots
            }
            finite_vertex_representative: dict[int, str] = {}
            for net_key, by_node in node_index_by_net.items():
                for node_key, node_index in by_node.items():
                    root = equivalence_find(node_index)
                    if root not in finite_kept_roots:
                        continue
                    finite_vertex_member_count[root] += 1
                    update_via_id_digest(
                        finite_vertex_member_hash[root], node_key
                    )
                    representative = finite_vertex_representative.get(root)
                    if representative is None or node_key < representative:
                        finite_vertex_representative[root] = node_key

            def finite_identity(prefix: str, *tokens: object) -> str:
                digest = hashlib.sha256()
                for token in (source_sha256_for_ids, *tokens):
                    encoded = str(token).strip().casefold().encode("utf-8")
                    digest.update(len(encoded).to_bytes(4, "big"))
                    digest.update(encoded)
                return f"{prefix}:{digest.hexdigest()[:24]}"

            finite_vertex_id_by_root: dict[int, str] = {}
            for root in sorted(finite_kept_roots):
                net_key = finite_net_key_by_root.get(root, "unknown")
                observed_layer_code = finite_root_layer_codes[root]
                layer_key = (
                    layer_key_by_code[observed_layer_code]
                    if observed_layer_code != 0
                    else "unknown"
                )
                layer_display = layer_display_by_key.get(
                    layer_key, "UNKNOWN"
                )
                representative = finite_vertex_representative.get(
                    root, f"quotient-root:{root}"
                )
                node_count = finite_vertex_member_count.get(root, 0)
                if node_count <= 0:
                    # ``index_for`` always records a by-NET identity; this is a
                    # defensive fail-closed guard for inconsistent compact state.
                    raise SpdImportError(
                        "finite Via quotient vertex has no source Node identity"
                    )
                node_digest = finite_vertex_member_hash[root].hexdigest()
                vertex_id = finite_identity(
                    "spd-finite-via-vertex",
                    net_key,
                    layer_key,
                    representative,
                    node_count,
                    node_digest,
                )
                finite_vertex_id_by_root[root] = vertex_id
                roles: set[str] = set()
                retained = finite_retained_by_root.get(root, {})
                terminal_ids = finite_terminal_ids_by_root.get(root, set())
                if retained:
                    roles.add("retained_surface")
                if terminal_ids:
                    roles.add("terminal")
                if finite_retarget_cut_ids_by_root.get(root):
                    roles.add("retarget_cut")
                if finite_active_degree[root] > 2:
                    roles.add("junction")
                elif finite_active_degree[root] == 2 and not roles:
                    roles.add("cycle_anchor")
                elif finite_active_degree[root] <= 1 and not roles:
                    roles.add("leaf")
                finite_via_vertices.append(
                    SpdFiniteViaQuotientVertex(
                        vertex_id=vertex_id,
                        net=target_net_display.get(net_key, net_key),
                        layer=layer_display,
                        representative_node_id=representative,
                        source_node_count=node_count,
                        source_node_ids_sha256=node_digest,
                        roles=tuple(roles),
                        retained_component_island_ids_by_layer=retained,
                        terminal_ids=tuple(terminal_ids),
                    )
                )
            for landing_key, root in finite_landing_root_by_key.items():
                vertex_id = finite_vertex_id_by_root.get(root)
                if vertex_id is not None:
                    finite_via_vertex_id_by_landing[landing_key] = vertex_id

            finite_physical_cache: dict[
                tuple[int, str, str],
                tuple[
                    float | None,
                    str | None,
                    tuple[SpdViaIslandPairSegment, ...],
                    str,
                    tuple[str, ...],
                    float | None,
                    float | None,
                    float | None,
                ],
            ] = {}

            def finite_physical_model(
                edge_index: int,
                start_root: int,
                end_root: int,
            ) -> tuple[
                int,
                str,
                str,
                float | None,
                str | None,
                tuple[SpdViaIslandPairSegment, ...],
                str,
                tuple[str, ...],
                float | None,
                float | None,
                float | None,
            ]:
                padstack_code = finite_via_padstack_codes[edge_index]
                start_code = finite_root_layer_codes[start_root]
                end_code = finite_root_layer_codes[end_root]
                start_key = (
                    layer_key_by_code[start_code] if start_code != 0 else "unknown"
                )
                end_key = (
                    layer_key_by_code[end_code] if end_code != 0 else "unknown"
                )
                cache_key = (padstack_code, start_key, end_key)
                cached = finite_physical_cache.get(cache_key)
                if cached is None:
                    if start_key == "unknown" or end_key == "unknown":
                        cached = (
                            None,
                            None,
                            (),
                            "incomplete",
                            ("via_endpoint_layer_missing",),
                            None,
                            None,
                            None,
                        )
                    else:
                        (
                            drill,
                            material,
                            segments,
                            status,
                            issues,
                        ) = physical_model_for(
                            finite_padstack_key_by_code[padstack_code],
                            start_key,
                            end_key,
                        )
                        resistance: float | None = None
                        inductance: float | None = None
                        length_um: float | None = None
                        electrical_issues = set(issues)
                        if status == "complete" and drill is not None:
                            try:
                                models = tuple(
                                    estimate_via_segment_rl(
                                        length_um=segment.length_um,
                                        drill_diameter_um=drill,
                                        padstack_material=material,
                                        start_layer=segment.start_layer,
                                        end_layer=segment.end_layer,
                                        stackup_layers=physical_stackup,
                                    )
                                    for segment in segments
                                )
                                resistance = sum(
                                    item.resistance_ohm for item in models
                                )
                                inductance = sum(
                                    item.inductance_h for item in models
                                )
                                length_um = sum(
                                    segment.length_um for segment in segments
                                )
                            except ViaModelError:
                                electrical_issues.add(
                                    "via_segment_rl_estimation_failed"
                                )
                        if electrical_issues:
                            status = "incomplete"
                            resistance = inductance = length_um = None
                        cached = (
                            drill,
                            material,
                            segments,
                            status,
                            tuple(sorted(electrical_issues)),
                            resistance,
                            inductance,
                            length_um,
                        )
                    finite_physical_cache[cache_key] = cached
                return (
                    padstack_code,
                    start_key,
                    end_key,
                    *cached,
                )

            finite_raw_edge_path_index = array(
                "i", [-2]
            ) * len(finite_via_first_indices)
            finite_raw_edge_physical_complete = bytearray(
                len(finite_via_first_indices)
            )
            finite_visited_edge = bytearray(len(finite_via_first_indices))
            for start_root in sorted(finite_kept_roots):
                if finite_active_degree[start_root] == 0:
                    continue
                for slot in range(
                    finite_indptr[start_root], finite_indptr[start_root + 1]
                ):
                    first_edge = finite_adjacency_edges[slot]
                    if (
                        not finite_active_edge[first_edge]
                        or finite_visited_edge[first_edge]
                    ):
                        continue
                    route_roots = [start_root]
                    route_edges: list[int] = []
                    current_root = start_root
                    edge_index = first_edge
                    while True:
                        if finite_visited_edge[edge_index]:
                            raise SpdImportError(
                                "finite Via bridge-chain traversal repeated an edge"
                            )
                        finite_visited_edge[edge_index] = 1
                        route_edges.append(edge_index)
                        following_root = finite_other(edge_index, current_root)
                        route_roots.append(following_root)
                        if following_root in finite_kept_roots:
                            break
                        if not finite_contractible[following_root]:
                            raise SpdImportError(
                                "finite Via route ended at an unclassified vertex"
                            )
                        candidates = []
                        for next_slot in range(
                            finite_indptr[following_root],
                            finite_indptr[following_root + 1],
                        ):
                            candidate = finite_adjacency_edges[next_slot]
                            if (
                                finite_active_edge[candidate]
                                and candidate != edge_index
                            ):
                                candidates.append(candidate)
                        if len(candidates) != 1:
                            raise SpdImportError(
                                "finite Via degree-two bridge chain lost continuity"
                            )
                        current_root = following_root
                        edge_index = candidates[0]

                    start_vertex_id = finite_vertex_id_by_root[route_roots[0]]
                    end_vertex_id = finite_vertex_id_by_root[route_roots[-1]]
                    forward_identity = (
                        start_vertex_id.casefold(),
                        end_vertex_id.casefold(),
                        tuple(route_edges),
                    )
                    reverse_identity = (
                        end_vertex_id.casefold(),
                        start_vertex_id.casefold(),
                        tuple(reversed(route_edges)),
                    )
                    if reverse_identity < forward_identity:
                        route_roots.reverse()
                        route_edges.reverse()
                        start_vertex_id, end_vertex_id = (
                            end_vertex_id,
                            start_vertex_id,
                        )
                    raw_models = [
                        finite_physical_model(
                            raw_edge,
                            route_roots[position],
                            route_roots[position + 1],
                        )
                        for position, raw_edge in enumerate(route_edges)
                    ]
                    terms: list[SpdFiniteViaSeriesTerm] = []
                    run_start = 0
                    while run_start < len(raw_models):
                        model = raw_models[run_start]
                        run_end = run_start + 1
                        while run_end < len(raw_models) and raw_models[run_end] == model:
                            run_end += 1
                        (
                            padstack_code,
                            start_key,
                            end_key,
                            drill,
                            material,
                            segments,
                            status,
                            issues,
                            resistance,
                            inductance,
                            length_um,
                        ) = model
                        repeat_count = run_end - run_start
                        terms.append(
                            SpdFiniteViaSeriesTerm(
                                ordinal=len(terms),
                                count=repeat_count,
                                padstack=finite_padstack_display_by_code[
                                    padstack_code
                                ],
                                start_layer=layer_display_by_key.get(
                                    start_key, "UNKNOWN"
                                ),
                                end_layer=layer_display_by_key.get(
                                    end_key, "UNKNOWN"
                                ),
                                drill_diameter_um=drill,
                                material=material,
                                segments=segments,
                                resistance_ohm=(
                                    None
                                    if resistance is None
                                    else resistance * repeat_count
                                ),
                                inductance_h=(
                                    None
                                    if inductance is None
                                    else inductance * repeat_count
                                ),
                                length_um=(
                                    None
                                    if length_um is None
                                    else length_um * repeat_count
                                ),
                                physical_model_status=status,
                                physical_model_issues=issues,
                            )
                        )
                        run_start = run_end
                    path_index = len(finite_reduced_paths)
                    path_complete = all(
                        item.physical_model_status == "complete" for item in terms
                    )
                    path_issues = tuple(
                        sorted(
                            {
                                issue
                                for item in terms
                                for issue in item.physical_model_issues
                            }
                        )
                    )
                    finite_reduced_paths.append(
                        {
                            "start_vertex_id": start_vertex_id,
                            "end_vertex_id": end_vertex_id,
                            "net": target_net_display.get(
                                finite_net_key_by_root[route_roots[0]],
                                finite_net_key_by_root[route_roots[0]],
                            ),
                            "raw_via_count": len(route_edges),
                            "series_terms": tuple(terms),
                            "mode": (
                                "retained_explicit"
                                if len(route_edges) == 1
                                else "contracted_series"
                            ),
                            "physical_model_status": (
                                "complete" if path_complete else "incomplete"
                            ),
                            "physical_model_issues": path_issues,
                            "resistance_ohm": (
                                sum(item.resistance_ohm or 0.0 for item in terms)
                                if path_complete
                                else None
                            ),
                            "inductance_h": (
                                sum(item.inductance_h or 0.0 for item in terms)
                                if path_complete
                                else None
                            ),
                            "length_um": (
                                sum(item.length_um or 0.0 for item in terms)
                                if path_complete
                                else None
                            ),
                            "via_ids_sha256": hashlib.sha256(),
                            "owner_ids": [],
                        }
                    )
                    for raw_edge, model in zip(
                        route_edges, raw_models, strict=True
                    ):
                        if finite_raw_edge_path_index[raw_edge] != -2:
                            raise SpdImportError(
                                "finite Via raw edge received multiple dispositions"
                            )
                        finite_raw_edge_path_index[raw_edge] = path_index
                        finite_raw_edge_physical_complete[raw_edge] = int(
                            model[6] == "complete"
                        )

            for landing_key, raw_edge in finite_first_edge_by_landing.items():
                path_index = finite_raw_edge_path_index[raw_edge]
                if path_index < 0:
                    continue
                finite_via_path_index_by_landing[landing_key] = path_index
                if (
                    contact_requested_by_key[landing_key][4] == "decap"
                    and (
                        finite_reduced_paths[path_index]["raw_via_count"] != 1
                        or finite_reduced_paths[path_index]["mode"]
                        != "retained_explicit"
                    )
                ):
                    raise SpdImportError(
                        "decap retarget cut failed to preserve its first Via as "
                        "one explicit global edge"
                    )

            if any(
                finite_active_edge[edge_index]
                and finite_raw_edge_path_index[edge_index] < 0
                for edge_index in range(len(finite_via_first_indices))
            ):
                raise SpdImportError(
                    "finite Via reducer did not retain every active cycle/branch edge"
                )

            # A second mmap scan avoids retaining millions of Via IDs/records.
            # The first pass has made full graph roots final; this pass can now
            # prove whether an unpaired Via belongs to a component capable of
            # joining two retained interfaces or is safely outside model scope.
            reporter.report(72, "Classifying retained-interface Via records")
            finite_edge_scan_index = 0
            for index, match in enumerate(_VIA_RE.finditer(data, via_start, via_end)):
                if index % 8192 == 0:
                    reporter.check()
                net_key = _decode(match.group(2)).casefold()
                if net_key not in node_index_by_net:
                    continue
                finite_edge_index = finite_edge_scan_index
                finite_edge_scan_index += 1
                via_id = _decode(match.group(1)).strip()
                via_key = via_id.casefold()
                update_via_id_digest(finite_raw_via_hash, via_key)
                finite_path_index = finite_raw_edge_path_index[finite_edge_index]
                if finite_path_index >= 0:
                    finite_modeled_via_count += 1
                    update_via_id_digest(finite_modeled_via_hash, via_key)
                    canonical_owner_id = f"via:{via_id}"
                    update_via_id_digest(
                        finite_modeled_owner_hash,
                        canonical_owner_id.casefold(),
                    )
                    finite_reduced_paths[finite_path_index][
                        "owner_ids"
                    ].append(canonical_owner_id)
                    update_via_id_digest(
                        finite_reduced_paths[finite_path_index][
                            "via_ids_sha256"
                        ],
                        via_key,
                    )
                    if finite_raw_edge_physical_complete[finite_edge_index]:
                        finite_physical_complete_via_count += 1
                    else:
                        finite_physical_incomplete_via_count += 1
                else:
                    finite_outside_via_count += 1
                    update_via_id_digest(finite_outside_via_hash, via_key)
                padstack = _decode(match.group(5)).strip()
                padstack_key = padstack.casefold()
                first_display = _decode(match.group(3)).strip()
                second_display = _decode(match.group(4)).strip()
                first = first_display.casefold()
                second = second_display.casefold()
                first_index = index_for(net_key, first)
                second_index = index_for(net_key, second)
                for landing_key in contact_landing_keys_by_via.get(
                    via_key, ()
                ):
                    if contact_requested_by_key[landing_key][0] != net_key:
                        continue
                    external_node = landing_key[1]
                    internal_node_key, internal_node_display = (
                        (second, second_display)
                        if first == external_node
                        else (first, first_display)
                        if second == external_node
                        else (None, None)
                    )
                    if internal_node_key is not None:
                        terminal_internal_endpoint_candidates.setdefault(
                            landing_key, {}
                        ).setdefault(internal_node_key, internal_node_display)
                        terminal_via_padstack_candidates.setdefault(
                            landing_key, {}
                        ).setdefault(padstack_key, padstack)
                terminal_owned = (
                    terminal_owned_keys is not None
                    and via_key in terminal_owned_keys
                )
                if terminal_owned:
                    observed_terminal_owned_via_keys.add(via_key)
                first_component = endpoint_component(net_key, first_index)
                second_component = endpoint_component(net_key, second_index)
                if (
                    via_id
                    and padstack
                    and first_component is not None
                    and second_component is not None
                ):
                    first_layer_key, first_component_islands = first_component
                    second_layer_key, second_component_islands = second_component
                    first_island = first_component_islands[0]
                    second_island = second_component_islands[0]
                    aggregate_key = (
                        net_key,
                        padstack_key,
                        first_layer_key,
                        second_layer_key,
                        first_island,
                        second_island,
                    )
                    via_island_pair_counts[aggregate_key] = (
                        via_island_pair_counts.get(aggregate_key, 0) + 1
                    )
                    if terminal_owned:
                        via_island_pair_terminal_counts[aggregate_key] = (
                            via_island_pair_terminal_counts.get(aggregate_key, 0)
                            + 1
                        )
                    digest = via_island_pair_hashes.get(aggregate_key)
                    if digest is None:
                        digest = hashlib.sha256()
                        via_island_pair_hashes[aggregate_key] = digest
                    update_via_id_digest(digest, via_key)
                    via_island_pair_display.setdefault(
                        aggregate_key,
                        (
                            target_net_display[net_key],
                            padstack,
                            target_layer_display[(net_key, first_layer_key)],
                            target_layer_display[(net_key, second_layer_key)],
                            first_island,
                            second_island,
                            first_component_islands,
                            second_component_islands,
                        ),
                    )
                    via_island_pair_records += 1
                    continue

                via_island_pair_missing_endpoint_records += 1
                if terminal_owned:
                    terminal_owned_unpaired_count += 1
                    update_via_id_digest(
                        terminal_owned_unpaired_hash, via_key
                    )
                    continue
                full_root = find(first_index)
                retained_components = retained_component_keys_by_full_root.get(
                    full_root, set()
                )
                if len(retained_components) >= 2:
                    unsupported_missing_endpoint_count += 1
                    update_via_id_digest(
                        unsupported_missing_endpoint_hash, via_key
                    )
                else:
                    outside_retained_interface_scope_count += 1
                    update_via_id_digest(
                        outside_retained_interface_scope_hash, via_key
                    )
            if finite_edge_scan_index != len(finite_via_first_indices):
                raise SpdImportError(
                    "finite Via quotient second pass changed raw edge cardinality"
                )
    except OSError as exc:
        raise SpdImportError(
            f"cannot recover mixed-reference GND graph from {source_path}: {exc}"
        ) from exc

    for path_row in finite_reduced_paths:
        via_digest = path_row["via_ids_sha256"].hexdigest()
        identity_digest = hashlib.sha256()
        for token in (
            source_sha256_for_ids,
            path_row["net"],
            path_row["start_vertex_id"],
            path_row["end_vertex_id"],
            path_row["mode"],
            path_row["raw_via_count"],
            via_digest,
        ):
            encoded = str(token).strip().casefold().encode("utf-8")
            identity_digest.update(len(encoded).to_bytes(4, "big"))
            identity_digest.update(encoded)
        finite_via_edges.append(
            SpdFiniteViaQuotientEdge(
                edge_id=(
                    "spd-finite-via-edge:"
                    + identity_digest.hexdigest()[:24]
                ),
                net=path_row["net"],
                start_vertex_id=path_row["start_vertex_id"],
                end_vertex_id=path_row["end_vertex_id"],
                parallel_path_count=1,
                per_path_via_count=path_row["raw_via_count"],
                raw_via_count=path_row["raw_via_count"],
                raw_via_ids_sha256=via_digest,
                owner_ids=tuple(path_row["owner_ids"]),
                series_terms=path_row["series_terms"],
                resistance_ohm=path_row["resistance_ohm"],
                inductance_h=path_row["inductance_h"],
                length_um=path_row["length_um"],
                mode=path_row["mode"],
                physical_model_status=path_row["physical_model_status"],
                physical_model_issues=path_row["physical_model_issues"],
            )
        )
    for landing_key, path_index in finite_via_path_index_by_landing.items():
        finite_via_edge_id_by_landing[landing_key] = finite_via_edges[
            path_index
        ].edge_id
    finite_modeled_owner_canonical_hash = hashlib.sha256()
    for owner_id in sorted(
        (
            owner_id
            for path_row in finite_reduced_paths
            for owner_id in path_row["owner_ids"]
        ),
        key=lambda value: (value.casefold(), value),
    ):
        update_via_id_digest(
            finite_modeled_owner_canonical_hash,
            owner_id.casefold(),
        )
    finite_via_coverage = SpdFiniteViaQuotientCoverage(
        raw_target_via_count=len(finite_via_first_indices),
        modeled_global_via_count=finite_modeled_via_count,
        outside_scope_via_count=finite_outside_via_count,
        pruned_dangling_via_count=finite_outside_via_count,
        physical_complete_via_count=finite_physical_complete_via_count,
        physical_incomplete_via_count=finite_physical_incomplete_via_count,
        raw_target_via_ids_sha256=finite_raw_via_hash.hexdigest(),
        modeled_global_via_ids_sha256=finite_modeled_via_hash.hexdigest(),
        modeled_owner_ledger_sha256=(
            finite_modeled_owner_hash.hexdigest()
        ),
        modeled_owner_canonical_sha256=(
            finite_modeled_owner_canonical_hash.hexdigest()
        ),
        outside_scope_via_ids_sha256=finite_outside_via_hash.hexdigest(),
        terminal_exclusive_via_count=0,
    )
    scenario_isolated_requested_hash = hashlib.sha256()
    scenario_isolated_resolved_hash = hashlib.sha256()
    finite_via_scenario_isolated_landing_keys: set[
        tuple[str, str]
    ] = set()
    scenario_isolated_node_identities: set[tuple[str, str]] = set()
    for landing_key in sorted(scenario_isolated_requested_by_key):
        net_key, via_id, node_id = scenario_isolated_requested_by_key[landing_key]
        for token in (net_key, via_id.casefold(), node_id.casefold()):
            update_via_id_digest(scenario_isolated_requested_hash, token)
        root = finite_landing_root_by_key.get(landing_key)
        vertex_id = finite_via_vertex_id_by_landing.get(landing_key)
        edge_id = finite_via_edge_id_by_landing.get(landing_key)
        if (
            root is None
            or vertex_id is None
            or edge_id is None
            or finite_vertex_member_count.get(root) != 1
            or root in finite_retained_by_root
        ):
            continue
        finite_via_scenario_isolated_landing_keys.add(landing_key)
        scenario_isolated_node_identities.add((net_key, node_id.casefold()))
        for token in (net_key, via_id.casefold(), node_id.casefold()):
            update_via_id_digest(scenario_isolated_resolved_hash, token)
    finite_via_scenario_isolation_coverage = (
        SpdFiniteViaScenarioIsolationCoverage(
            requested_landing_count=len(scenario_isolated_requested_by_key),
            isolated_landing_count=len(
                finite_via_scenario_isolated_landing_keys
            ),
            isolated_node_count=len(scenario_isolated_node_identities),
            suppressed_artwork_contact_count=(
                scenario_isolated_suppressed_artwork_contacts
            ),
            suppressed_trace_edge_count=(
                scenario_isolated_suppressed_trace_edges
            ),
            requested_landing_ids_sha256=(
                scenario_isolated_requested_hash.hexdigest()
            ),
            isolated_landing_ids_sha256=(
                scenario_isolated_resolved_hash.hexdigest()
            ),
        )
        if scenario_isolated_requested_by_key
        else None
    )
    retarget_destination_requested_hash = hashlib.sha256()
    retarget_destination_resolved_hash = hashlib.sha256()
    finite_via_vertex_id_by_retarget_destination: dict[
        tuple[str, str, str], str
    ] = {}
    for destination_key in sorted(retarget_destination_requested_keys):
        for token in destination_key:
            update_via_id_digest(retarget_destination_requested_hash, token)
        if destination_key not in retarget_destination_observed_keys:
            continue
        net_key, layer_key, node_key = destination_key
        node_index = node_index_by_net.get(net_key, {}).get(node_key)
        if node_index is None:
            continue
        root = equivalence_find(node_index)
        vertex_id = finite_vertex_id_by_root.get(root)
        retained_rows = finite_retained_by_root.get(root, {})
        if (
            vertex_id is None
            or not any(
                str(layer).casefold() == layer_key and bool(island_ids)
                for layer, island_ids in retained_rows.items()
            )
        ):
            continue
        finite_via_vertex_id_by_retarget_destination[
            destination_key
        ] = vertex_id
        for token in destination_key:
            update_via_id_digest(retarget_destination_resolved_hash, token)
    finite_via_retarget_destination_coverage = (
        SpdFiniteViaRetargetDestinationCoverage(
            requested_destination_count=len(
                retarget_destination_requested_keys
            ),
            resolved_destination_count=len(
                finite_via_vertex_id_by_retarget_destination
            ),
            requested_destination_ids_sha256=(
                retarget_destination_requested_hash.hexdigest()
            ),
            resolved_destination_ids_sha256=(
                retarget_destination_resolved_hash.hexdigest()
            ),
        )
    )

    reporter.report(85, "Reducing mixed-reference GND graph components")
    component_target_masks: dict[int, int] = {}
    component_net_by_root: dict[int, str] = {}
    for net_key, targets in target_nodes_by_net.items():
        for node_key, target_mask in targets.items():
            root = find(index_for(net_key, node_key))
            component_net_by_root[root] = net_key
            component_target_masks[root] = (
                component_target_masks.get(root, 0) | target_mask
            )
    island_root_by_key = {
        island_key: find(index_for(island_key[0], anchor_node))
        for island_key, anchor_node in island_anchor_node.items()
    }
    island_equivalence_root_by_key = {
        island_key: equivalence_find(index_for(island_key[0], anchor_node))
        for island_key, anchor_node in island_anchor_node.items()
    }
    surface_equivalence_proofs: list[SpdSurfaceEquivalenceProof] = []
    surface_equivalence_components: list[
        SpdSurfaceIslandEquivalenceComponent
    ] = []
    islands_by_root_layer: dict[tuple[str, int, str], set[str]] = {}
    for (net_key, layer_key), expected_ids in sorted(
        surface_island_inventory.items()
    ):
        contacted_ids = tuple(
            island_id
            for island_id in expected_ids
            if island_target_node_counts.get((net_key, island_id), 0) > 0
        )
        roots = {
            island_equivalence_root_by_key[(net_key, island_id)]
            for island_id in contacted_ids
        }
        if contacted_ids != expected_ids:
            status = "uncontacted_island"
        else:
            # Multiple same-layer components are a valid exact partition; they
            # remain independent network nodes rather than blocking the model.
            status = "complete"
        surface_equivalence_proofs.append(
            SpdSurfaceEquivalenceProof(
                net=target_net_display[net_key],
                layer=target_layer_display[(net_key, layer_key)],
                island_ids=expected_ids,
                contacted_island_ids=contacted_ids,
                graph_component_count=len(roots),
                status=status,
            )
        )
        partition_by_root: dict[tuple[str, int | str], list[str]] = {}
        for island_id in expected_ids:
            equivalence_root = island_equivalence_root_by_key.get(
                (net_key, island_id)
            )
            partition_key: tuple[str, int | str] = (
                ("root", equivalence_root)
                if equivalence_root is not None
                else ("uncontacted", island_id)
            )
            partition_by_root.setdefault(partition_key, []).append(island_id)
        for island_ids in partition_by_root.values():
            surface_equivalence_components.append(
                SpdSurfaceIslandEquivalenceComponent(
                    net=target_net_display[net_key],
                    layer=target_layer_display[(net_key, layer_key)],
                    island_ids=tuple(island_ids),
                )
            )
        for island_id in contacted_ids:
            root = island_root_by_key[(net_key, island_id)]
            islands_by_root_layer.setdefault(
                (net_key, root, layer_key), set()
            ).add(island_id)
    reachable: set[tuple[str, str, str]] = set()
    for via, node, target_layer in requested:
        net_key = requested_by_key[(via, node, target_layer)]
        root = find(index_for(net_key, node))
        if component_target_masks.get(root, 0) & target_bit_by_key[(net_key, target_layer)]:
            reachable.add((via, node, target_layer))
    unreachable = set(requested) - reachable

    landing_surface_contacts: list[SpdLandingSurfaceContact] = []
    for landing_key, (
        net_key,
        via_id,
        node_id,
        _net_display,
        terminal_owner_kind,
    ) in sorted(contact_requested_by_key.items()):
        island_ids_by_layer: dict[str, tuple[str, ...]] = {}
        internal_candidates = terminal_internal_endpoint_candidates.get(
            landing_key, {}
        )
        internal_endpoint_node_id = (
            next(iter(internal_candidates.values()))
            if len(internal_candidates) == 1
            else None
        )
        internal_endpoint_node_key = (
            next(iter(internal_candidates))
            if len(internal_candidates) == 1
            else None
        )
        internal_endpoint_index = (
            index_for(net_key, internal_endpoint_node_key)
            if internal_endpoint_node_key is not None
            else None
        )
        resolved_endpoint_component = (
            endpoint_component(net_key, internal_endpoint_index)
            if internal_endpoint_index is not None
            else None
        )
        endpoint_layer_code = (
            node_layer_codes[internal_endpoint_index]
            if internal_endpoint_index is not None
            else 0
        )
        external_layer_code = node_layer_codes[
            index_for(net_key, landing_key[1])
        ]
        external_layer_key = (
            layer_key_by_code[external_layer_code]
            if external_layer_code != 0
            else None
        )
        endpoint_layer_key = (
            layer_key_by_code[endpoint_layer_code]
            if endpoint_layer_code != 0
            else None
        )
        if resolved_endpoint_component is not None:
            component_layer_key, component_island_ids = (
                resolved_endpoint_component
            )
            island_ids_by_layer[
                target_layer_display[(net_key, component_layer_key)]
            ] = component_island_ids
        padstack_candidates = terminal_via_padstack_candidates.get(
            landing_key, {}
        )
        padstack_key = (
            next(iter(padstack_candidates))
            if len(padstack_candidates) == 1
            else None
        )
        padstack = (
            next(iter(padstack_candidates.values()))
            if len(padstack_candidates) == 1
            else None
        )
        if (
            padstack_key is not None
            and external_layer_key is not None
            and endpoint_layer_key is not None
        ):
            (
                drill_diameter_um,
                material,
                physical_segments,
                physical_model_status,
                physical_model_issues,
            ) = physical_model_for(
                padstack_key,
                external_layer_key,
                endpoint_layer_key,
            )
        else:
            drill_diameter_um = None
            material = None
            physical_segments = ()
            physical_model_status = "incomplete"
            missing_physical_issues: set[str] = set()
            if len(internal_candidates) != 1:
                missing_physical_issues.add(
                    "internal_via_endpoint_missing_or_ambiguous"
                )
            if len(padstack_candidates) != 1:
                missing_physical_issues.add(
                    "terminal_via_padstack_missing_or_ambiguous"
                )
            if external_layer_key is None:
                missing_physical_issues.add("external_endpoint_layer_missing")
            if endpoint_layer_key is None:
                missing_physical_issues.add("internal_endpoint_layer_missing")
            physical_model_issues = tuple(sorted(missing_physical_issues))
        physical_issue_set = set(physical_model_issues)
        if terminal_owner_kind == "unknown":
            physical_issue_set.add("terminal_owner_kind_unknown")
        if not island_ids_by_layer:
            physical_issue_set.add(
                "internal_endpoint_equivalence_component_missing"
            )
        if physical_issue_set:
            physical_model_status = "incomplete"
        contact = SpdLandingSurfaceContact(
            via_id=via_id,
            endpoint_node_id=node_id,
            net=target_net_display[net_key],
            contact_island_ids_by_layer=island_ids_by_layer,
            internal_endpoint_node_id=internal_endpoint_node_id,
            terminal_owner_kind=terminal_owner_kind,
            external_endpoint_layer=(
                layer_display_by_key.get(external_layer_key)
                if external_layer_key is not None
                else None
            ),
            padstack=padstack,
            drill_diameter_um=drill_diameter_um,
            material=material,
            segments=physical_segments,
            physical_model_status=physical_model_status,
            physical_model_issues=tuple(sorted(physical_issue_set)),
        )
        landing_surface_contacts.append(contact)

    via_island_pair_aggregates: list[SpdViaIslandPairAggregate] = []
    for aggregate_key in sorted(via_island_pair_counts):
        count = via_island_pair_counts[aggregate_key]
        owned = (
            None
            if terminal_owned_keys is None
            else via_island_pair_terminal_counts.get(aggregate_key, 0)
        )
        (
            net,
            padstack,
            start_layer,
            end_layer,
            start_island,
            end_island,
            start_component_island_ids,
            end_component_island_ids,
        ) = via_island_pair_display[aggregate_key]
        (
            drill_diameter_um,
            material,
            physical_segments,
            physical_model_status,
            physical_model_issues,
        ) = physical_model_for(
            aggregate_key[1],
            aggregate_key[2],
            aggregate_key[3],
        )
        via_island_pair_aggregates.append(
            SpdViaIslandPairAggregate(
                net=net,
                padstack=padstack,
                start_layer=start_layer,
                end_layer=end_layer,
                start_island_id=start_island,
                end_island_id=end_island,
                count=count,
                via_ids_sha256=via_island_pair_hashes[
                    aggregate_key
                ].hexdigest(),
                start_component_island_ids=start_component_island_ids,
                end_component_island_ids=end_component_island_ids,
                terminal_owned_count=owned,
                substrate_count=None if owned is None else count - owned,
                drill_diameter_um=drill_diameter_um,
                material=material,
                segments=physical_segments,
                physical_model_status=physical_model_status,
                physical_model_issues=physical_model_issues,
            )
        )
    paired_terminal_owned_count = (
        sum(via_island_pair_terminal_counts.values())
        if terminal_owned_keys is not None
        else None
    )
    paired_substrate_count = (
        via_island_pair_records - int(paired_terminal_owned_count or 0)
        if terminal_owned_keys is not None
        else None
    )
    via_island_pair_coverage = SpdViaIslandPairCoverage(
        raw_target_via_count=via_graph_edges,
        paired_via_count=via_island_pair_records,
        terminal_owned_unpaired_count=terminal_owned_unpaired_count,
        terminal_owned_unpaired_via_ids_sha256=(
            terminal_owned_unpaired_hash.hexdigest()
        ),
        unsupported_missing_endpoint_count=(
            unsupported_missing_endpoint_count
        ),
        unsupported_missing_endpoint_via_ids_sha256=(
            unsupported_missing_endpoint_hash.hexdigest()
        ),
        outside_retained_interface_scope_count=(
            outside_retained_interface_scope_count
        ),
        outside_retained_interface_scope_via_ids_sha256=(
            outside_retained_interface_scope_hash.hexdigest()
        ),
        model_relevant_via_count=(
            via_island_pair_records
            + terminal_owned_unpaired_count
            + unsupported_missing_endpoint_count
        ),
        terminal_owned_ids_supplied=terminal_owned_keys is not None,
        terminal_owned_declared_count=(
            len(terminal_owned_keys) if terminal_owned_keys is not None else 0
        ),
        terminal_owned_observed_count=len(observed_terminal_owned_via_keys),
        paired_terminal_owned_count=paired_terminal_owned_count,
        paired_substrate_count=paired_substrate_count,
    )

    surface_component_set: set[SpdSurfaceConnectivityComponent] = set()
    for root, target_mask in component_target_masks.items():
        net_key = component_net_by_root[root]
        layer_keys = tuple(
            layer_key
            for layer_key in sorted(target_layers[net_key])
            if target_mask & target_bit_by_key[(net_key, layer_key)]
        )
        if len(layer_keys) < 2:
            continue
        surface_component_set.add(
            SpdSurfaceConnectivityComponent(
                net=target_net_display[net_key],
                layers=tuple(
                    target_layer_display[(net_key, layer_key)]
                    for layer_key in layer_keys
                ),
            )
        )
    surface_components = tuple(
        sorted(
            surface_component_set,
            key=lambda item: (
                item.net.casefold(),
                tuple(layer.casefold() for layer in item.layers),
                item.net,
                item.layers,
            ),
        )
    )

    landing_layer_sets: dict[tuple[str, str], set[str]] = {
        (via, node): set() for via, node, _target_layer in requested
    }
    landing_island_sets: dict[tuple[str, str], set[str]] = {
        (via, node): set() for via, node, _target_layer in requested
    }
    for via, node, target_layer in reachable:
        net_key = requested_by_key[(via, node, target_layer)]
        root = find(index_for(net_key, node))
        landing_layer_sets[(via, node)].add(
            target_layer_display[(net_key, target_layer)]
        )
        landing_island_sets[(via, node)].update(
            islands_by_root_layer.get((net_key, root, target_layer), ())
        )
    surface_layers_by_landing = {
        landing_key: tuple(
            sorted(layers, key=lambda layer: (layer.casefold(), layer))
        )
        for landing_key, layers in sorted(landing_layer_sets.items())
    }
    surface_islands_by_landing = {
        landing_key: tuple(sorted(islands))
        for landing_key, islands in sorted(landing_island_sets.items())
    }
    result = SpdGroundReachability(
        frozenset(reachable), frozenset(unreachable), {
            "requested": len(requested), "reachable": len(reachable),
            "unreachable": len(unreachable), "node_section_passes": 1,
            "trace_section_passes": int(
                include_traces and trace_start >= 0 and trace_end > trace_start
            ),
            "via_section_passes": 2, "components": components,
            "graph_nodes": len(parents), "graph_edges": graph_edges,
            "artwork_island_count": len(seen_island_ids),
            "artwork_island_unions": artwork_island_unions,
            "same_layer_trace_equivalence_edges": same_layer_trace_edges,
            "via_edges_excluded_from_surface_equivalence": via_graph_edges,
            "surface_equivalence_component_count": len(
                surface_equivalence_components
            ),
            "surface_equivalence_complete": sum(
                item.status == "complete" for item in surface_equivalence_proofs
            ),
            "surface_equivalence_incomplete": sum(
                item.status != "complete" for item in surface_equivalence_proofs
            ),
            "terminal_landing_contact_count": len(landing_surface_contacts),
            "terminal_landing_contacted_count": sum(
                bool(item.contact_island_ids_by_layer)
                for item in landing_surface_contacts
            ),
            "via_island_pair_aggregate_count": len(
                via_island_pair_aggregates
            ),
            "via_island_pair_record_count": via_island_pair_records,
            "via_island_pair_missing_endpoint_record_count": (
                via_island_pair_missing_endpoint_records
            ),
            "via_terminal_owned_unpaired_record_count": (
                terminal_owned_unpaired_count
            ),
            "via_unsupported_missing_endpoint_record_count": (
                unsupported_missing_endpoint_count
            ),
            "via_outside_retained_interface_scope_record_count": (
                outside_retained_interface_scope_count
            ),
            "via_model_relevant_record_count": (
                via_island_pair_coverage.model_relevant_via_count
            ),
            "terminal_owned_via_ids_supplied": int(
                terminal_owned_keys is not None
            ),
            "terminal_owned_via_id_count": (
                len(terminal_owned_keys) if terminal_owned_keys is not None else 0
            ),
            "terminal_owned_via_observed_count": len(
                observed_terminal_owned_via_keys
            ),
            "via_island_pair_terminal_owned_record_count": sum(
                via_island_pair_terminal_counts.values()
            ),
            "via_island_pair_substrate_record_count": (
                int(paired_substrate_count or 0)
            ),
            "via_island_pair_physical_model_complete": sum(
                item.physical_model_status == "complete"
                for item in via_island_pair_aggregates
            ),
            "via_island_pair_physical_model_incomplete": sum(
                item.physical_model_status != "complete"
                for item in via_island_pair_aggregates
            ),
            "terminal_landing_physical_model_complete": sum(
                item.physical_model_status == "complete"
                for item in landing_surface_contacts
            ),
            "terminal_landing_physical_model_incomplete": sum(
                item.physical_model_status != "complete"
                for item in landing_surface_contacts
            ),
            "via_island_pair_coverage_complete": int(
                via_island_pair_coverage.status == "complete"
            ),
            "finite_via_quotient_vertex_count": len(finite_via_vertices),
            "finite_via_quotient_edge_count": len(finite_via_edges),
            "finite_via_raw_record_count": (
                finite_via_coverage.raw_target_via_count
            ),
            "finite_via_modeled_global_record_count": (
                finite_via_coverage.modeled_global_via_count
            ),
            "finite_via_pruned_dangling_record_count": (
                finite_via_coverage.pruned_dangling_via_count
            ),
            "finite_via_physical_model_complete_record_count": (
                finite_via_coverage.physical_complete_via_count
            ),
            "finite_via_physical_model_incomplete_record_count": (
                finite_via_coverage.physical_incomplete_via_count
            ),
            "finite_via_retained_explicit_edge_count": sum(
                item.mode == "retained_explicit" for item in finite_via_edges
            ),
            "finite_via_contracted_series_edge_count": sum(
                item.mode == "contracted_series" for item in finite_via_edges
            ),
            "finite_via_terminal_exclusive_record_count": 0,
            "finite_via_quotient_coverage_complete": int(
                finite_via_coverage.status == "complete"
            ),
            "finite_via_scenario_isolated_landing_count": len(
                finite_via_scenario_isolated_landing_keys
            ),
            "finite_via_scenario_suppressed_artwork_contact_count": (
                scenario_isolated_suppressed_artwork_contacts
            ),
            "finite_via_scenario_suppressed_trace_edge_count": (
                scenario_isolated_suppressed_trace_edges
            ),
            "finite_via_scenario_isolation_complete": int(
                finite_via_scenario_isolation_coverage is None
                or finite_via_scenario_isolation_coverage.status == "complete"
            ),
            "finite_via_retarget_destination_requested_count": (
                finite_via_retarget_destination_coverage.requested_destination_count
            ),
            "finite_via_retarget_destination_resolved_count": (
                finite_via_retarget_destination_coverage.resolved_destination_count
            ),
            "finite_via_retarget_destination_coverage_complete": int(
                finite_via_retarget_destination_coverage.status == "complete"
            ),
        },
        surface_components=surface_components,
        surface_layers_by_landing=surface_layers_by_landing,
        surface_islands_by_landing=surface_islands_by_landing,
        surface_equivalence_proofs=tuple(surface_equivalence_proofs),
        surface_equivalence_components=tuple(surface_equivalence_components),
        landing_surface_contacts=tuple(landing_surface_contacts),
        via_island_pair_aggregates=tuple(via_island_pair_aggregates),
        via_island_pair_coverage=via_island_pair_coverage,
        finite_via_vertices=tuple(finite_via_vertices),
        finite_via_edges=tuple(finite_via_edges),
        finite_via_vertex_id_by_landing=finite_via_vertex_id_by_landing,
        finite_via_edge_id_by_landing=finite_via_edge_id_by_landing,
        finite_via_coverage=finite_via_coverage,
        finite_via_scenario_isolated_landing_keys=frozenset(
            finite_via_scenario_isolated_landing_keys
        ),
        finite_via_scenario_isolation_coverage=(
            finite_via_scenario_isolation_coverage
        ),
        finite_via_vertex_id_by_retarget_destination=(
            finite_via_vertex_id_by_retarget_destination
        ),
        finite_via_retarget_destination_coverage=(
            finite_via_retarget_destination_coverage
        ),
    )
    result = finish(result)
    reporter.report(100, "Checked mixed-reference GND landing reachability")
    return result


def analyze_spd(
    path: str | Path,
    frequencies_hz: Iterable[float] = (1.0e3, 1.0e6, 1.0e9),
    gnd_aliases: Iterable[str] = ("DGND", "GND"),
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    scope: SpdAnalysisScope = "selected_pi",
) -> SpdAnalysis:
    """Analyze an SPD without materializing the source file in memory."""

    reporter = _Reporter(progress, is_cancelled)
    reporter.report(0, "Validating SPD source")
    source_path = Path(path)
    try:
        stat = source_path.stat()
    except OSError as exc:
        raise SpdImportError(f"cannot stat SPD source {source_path}: {exc}") from exc
    if not source_path.is_file():
        raise SpdImportError(f"SPD source is not a file: {source_path}")
    if stat.st_size <= 0:
        raise SpdImportError("SPD source is empty")
    try:
        frequencies = tuple(float(value) for value in frequencies_hz)
    except (TypeError, ValueError) as exc:
        raise SpdImportError("frequencies_hz must contain numeric values") from exc
    if not frequencies or any(not isfinite(value) or value <= 0 for value in frequencies):
        raise SpdImportError("frequencies_hz must contain at least one finite positive value")
    ground_aliases = _unique(alias.strip() for alias in gnd_aliases if alias.strip())
    if not ground_aliases:
        raise SpdImportError("at least one ground alias is required")
    if scope not in {"selected_pi", "decap_scenario"}:
        raise SpdImportError(f"unsupported SPD analysis scope: {scope!r}")
    gnd_keys = {item.casefold() for item in ground_aliases}
    diagnostics: list[SpdDiagnostic] = []

    try:
        with source_path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            reporter.report(3, "Hashing SPD source")
            digest = hashlib.sha256(data).hexdigest()
            first_end = _line_end(data, 0, min(len(data), 16_384))
            title = _decode(data[0:first_end]).strip()
            source = SpdSourceInfo(source_path.resolve(), source_path.name, stat.st_size, stat.st_mtime_ns, digest, title)

            reporter.report(9, "Reading selected power/ground nets")
            selected_power, selected_ground = _parse_netlist(data, gnd_keys, diagnostics)
            if scope == "decap_scenario" and not selected_power:
                diagnostics.append(
                    SpdDiagnostic(
                        "error",
                        "SPD_POWER_NET_CLASSIFICATION_MISSING",
                        (
                            "Editable decap assignment requires at least one explicit "
                            ".NetList PowerNets declaration; positive shapes and non-GND "
                            "IO ports are not promoted to PWR rails automatically."
                        ),
                    )
                )
            configured_ground_keys = {
                item.casefold() for item in (*ground_aliases, *selected_ground)
            }
            selected_keys = {
                item.casefold() for item in (*selected_power, *selected_ground)
            }

            layer_marker = data.find(b"* Layer description lines")
            node_marker = data.find(b"* Node description lines")
            trace_marker = data.find(b"* Trace description lines")
            via_marker = data.find(b"* Via description lines")
            pad_marker = data.find(b"* PadStack collection description lines")
            material_marker = data.find(b"* Material description lines")
            circuit_marker = data.find(b"* Circuit description lines")
            first_shape = _find_line(data, b".Shape")
            shape_start = first_shape if first_shape >= 0 else 0
            shape_end = layer_marker if layer_marker > shape_start else (node_marker if node_marker > shape_start else len(data))

            reporter.report(11, "Reading material models")
            material_start = material_marker if material_marker >= 0 else 0
            material_end_marker = _find_line(data, b".EndMaterial", material_start)
            material_end = (
                len(data)
                if material_end_marker < 0
                else _line_end(data, material_end_marker, len(data))
            )
            dielectrics, metals = _parse_materials(
                data, material_start, material_end, diagnostics
            )
            layer_start = layer_marker if layer_marker >= 0 else shape_end
            layer_end = (
                node_marker
                if node_marker > layer_start
                else min(
                    (
                        value
                        for value in (via_marker, pad_marker, len(data))
                        if value > layer_start
                    ),
                    default=len(data),
                )
            )
            # Scenario mode needs candidate rails beyond the .NetList selection,
            # but retaining every signal polygon in a production SPD would be
            # prohibitively expensive.  Index compact Part/Connect metadata first
            # and use it to constrain the single exact-geometry scan.
            prefetched: tuple[
                dict[str, PassiveSubcircuitModel],
                dict[str, str],
                dict[str, str],
                set[str],
                int,
                dict[str, _Part],
                dict[str, _Component],
                tuple[_Connection, ...],
            ] | None = None
            scenario_power_keys = {
                item.casefold() for item in selected_power
            }
            if scope == "decap_scenario":
                reporter.report(12, "Indexing decap and device candidate nets")
                quiet_reporter = _Reporter(None, is_cancelled)
                material_start_early = material_marker if material_marker >= 0 else 0
                material_end_marker_early = _find_line(
                    data, b".EndMaterial", material_start_early
                )
                material_end_early = (
                    len(data)
                    if material_end_marker_early < 0
                    else _line_end(data, material_end_marker_early, len(data))
                )
                circuit_start_early = (
                    circuit_marker if circuit_marker >= 0 else material_end_early
                )
                connect_start_early = _find_line(
                    data, b".Connect", circuit_start_early
                )
                circuit_end_early = (
                    connect_start_early
                    if connect_start_early > circuit_start_early
                    else len(data)
                )
                (
                    cap_models_early,
                    model_assets_early,
                    canonical_early,
                    empty_names_early,
                    partial_count_early,
                ) = _parse_partial_circuits(
                    data,
                    circuit_start_early,
                    circuit_end_early,
                    frequencies,
                    source_path.name,
                    quiet_reporter,
                    diagnostics,
                )
                metadata_start_early = (
                    connect_start_early
                    if connect_start_early >= 0
                    else circuit_start_early
                )
                comp_end_marker_early = _find_line(
                    data, b".EndCompCollection", metadata_start_early
                )
                metadata_end_early = (
                    len(data)
                    if comp_end_marker_early < 0
                    else _line_end(data, comp_end_marker_early, len(data))
                )
                parts_early, components_early, connections_early = _parse_metadata(
                    data,
                    metadata_start_early,
                    metadata_end_early,
                    diagnostics,
                )
                prefetched = (
                    cap_models_early,
                    model_assets_early,
                    canonical_early,
                    empty_names_early,
                    partial_count_early,
                    parts_early,
                    components_early,
                    connections_early,
                )

            reporter.report(14, "Scanning positive plane polygons")
            # Editable scenario rails are fail-closed to explicit .NetList
            # PowerNets declarations.  Exact configured-GND artwork is retained
            # on every layer for mixed-reference certification; it is never
            # promoted into the selected power-rail set.
            scenario_geometry_keys = scenario_power_keys
            shape_selected_keys = (
                selected_keys
                if scope == "selected_pi"
                else scenario_geometry_keys | configured_ground_keys
            )
            outline, layer_nets, positive_nets, plane_geometries = _parse_shapes(
                data,
                shape_start,
                shape_end,
                shape_selected_keys,
                (
                    {item.casefold() for item in selected_power}
                    if scope == "selected_pi" and selected_power
                    else scenario_geometry_keys | configured_ground_keys
                    if scope == "decap_scenario"
                    else None
                ),
                reporter,
                diagnostics,
            )
            positive_keys = {item.casefold() for item in positive_nets}
            usable_power = _unique(
                (
                    *(
                        item
                        for item in selected_power
                        if item.casefold() in positive_keys
                    ),
                    *(
                        item
                        for item in positive_nets
                        if scope == "decap_scenario"
                        and item.casefold() in scenario_geometry_keys
                        and item.casefold() not in configured_ground_keys
                    ),
                )
            )
            usable_ground = _unique(
                (
                    *(
                        item
                        for item in selected_ground
                        if item.casefold() in positive_keys
                    ),
                    *(
                        item
                        for item in positive_nets
                        if item.casefold() in configured_ground_keys
                    ),
                )
            )
            if scope == "decap_scenario" and not usable_ground:
                diagnostics.append(
                    SpdDiagnostic(
                        "error",
                        "SPD_GROUND_PLANE_CLASSIFICATION_MISSING",
                        (
                            "Editable decap assignment requires an explicit GroundNets "
                            "or configured ground-alias shape that forms a PWR/GND pair."
                        ),
                    )
                )
            ground_keys = configured_ground_keys | {
                item.casefold() for item in usable_ground
            }
            if selected_power:
                missing_shapes = [item for item in selected_power if item.casefold() not in positive_keys]
                if missing_shapes:
                    diagnostics.append(SpdDiagnostic("warning", "SELECTED_NET_WITHOUT_POSITIVE_SHAPE", f"{len(missing_shapes)} selected power net(s) have no positive Shape polygon and may not form a usable plane."))
            plane_keys = (
                {item.casefold() for item in (*usable_power, *usable_ground)}
                if selected_power or scope == "decap_scenario"
                else positive_keys
            )

            reporter.report(30, "Building stack-up")
            layers = _parse_layers(data, layer_start, layer_end, layer_nets, dielectrics, metals, plane_keys, diagnostics)
            top_layer = next((item.name for item in layers if item.is_conductor), None)

            reporter.report(35, "Reading padstack definitions")
            pad_start = _find_line(data, b".PadStackDef", pad_marker if pad_marker >= 0 else 0)
            if pad_start < 0:
                pad_start = 0
            pad_end = material_marker if material_marker > pad_start else (circuit_marker if circuit_marker > pad_start else len(data))
            padstacks = _parse_padstacks(data, pad_start, pad_end, diagnostics)

            reporter.report(38, "Reading passive PartialCkt models")
            circuit_start = circuit_marker if circuit_marker >= 0 else material_end
            connect_start = _find_line(data, b".Connect", circuit_start)
            circuit_end = connect_start if connect_start > circuit_start else len(data)
            if prefetched is None:
                cap_models, model_assets, canonical, empty_names, partial_count = _parse_partial_circuits(data, circuit_start, circuit_end, frequencies, source_path.name, reporter, diagnostics)
            else:
                (
                    cap_models,
                    model_assets,
                    canonical,
                    empty_names,
                    partial_count,
                    _prefetched_parts,
                    _prefetched_components,
                    _prefetched_connections,
                ) = prefetched

            reporter.report(47, "Reading Part, Component, and Connect records")
            metadata_start = connect_start if connect_start >= 0 else circuit_start
            comp_end_marker = _find_line(data, b".EndCompCollection", metadata_start)
            metadata_end = len(data) if comp_end_marker < 0 else _line_end(data, comp_end_marker, len(data))
            if prefetched is None:
                parts, components, connections = _parse_metadata(data, metadata_start, metadata_end, diagnostics)
            else:
                parts = _prefetched_parts
                components = _prefetched_components
                connections = _prefetched_connections
            device_candidates, cap_candidates, referenced, skipped_unselected, ambiguous = _select_candidates(
                parts,
                components,
                connections,
                canonical,
                empty_names,
                plane_keys,
                {item.casefold() for item in selected_power},
                ground_keys,
                diagnostics,
                include_unmodeled_caps=scope == "decap_scenario",
                include_unselected_caps=scope == "decap_scenario",
            )

            routing_extraction: SpdRoutingExtraction | None = None
            if (
                scope == "decap_scenario"
                and trace_marker >= 0
                and via_marker > trace_marker
            ):
                reporter.report(54, "Compiling immutable signal-routing evidence")
                routing_roles = merge_spd_routing_net_roles(
                    parse_spd_routing_net_roles(data),
                    power_nets=(
                        *selected_power,
                        *usable_power,
                        *(candidate.power.net for candidate in cap_candidates),
                    ),
                    ground_nets=(
                        *ground_aliases,
                        *selected_ground,
                        *usable_ground,
                        *(candidate.ground.net for candidate in cap_candidates),
                    ),
                )
                conductor_layers = tuple(
                    item.name for item in layers if item.is_conductor
                )
                try:
                    routing_extraction = extract_spd_routing_obstacles(
                        data,
                        trace_start=trace_marker,
                        trace_end=via_marker,
                        node_start=node_marker if node_marker >= 0 else 0,
                        node_end=(
                            trace_marker
                            if trace_marker > node_marker
                            else via_marker
                        ),
                        conductor_layers=conductor_layers,
                        net_roles=routing_roles,
                        check=reporter.check,
                    )
                except SpdImportError:
                    # Cancellation and other import-control failures are not
                    # optional research-asset compiler failures.
                    raise
                except (ValueError, OverflowError) as exc:
                    # This research evidence is optional for the legacy OFF
                    # workflow.  Isolate bounded-format/compiler rejection to
                    # the asset; protection ON will fail closed because no
                    # asset is attached to the imported scenario.
                    routing_extraction = None
                    diagnostics.append(
                        SpdDiagnostic(
                            "warning",
                            "SPD_SIGNAL_ROUTING_RESEARCH_ASSET_UNAVAILABLE",
                            f"Optional signal-routing evidence was not compiled: {exc}",
                        )
                    )
                else:
                    diagnostics.append(
                        SpdDiagnostic(
                            "info",
                            "SPD_SIGNAL_ROUTING_RESEARCH_ASSET",
                            (
                                "Compiled width-resolved SIGNAL-role Trace evidence "
                                "for optional Distribution protection. This initial "
                                "scope is research/provisional and does not certify "
                                "routed PWR/GND, signal vias, pins or fanout pads."
                            ),
                        )
                    )

            reporter.report(57, "Resolving referenced Node coordinates")
            node_start = node_marker if node_marker >= 0 else 0
            node_end = (
                trace_marker
                if trace_marker > node_start
                else via_marker
                if via_marker > node_start
                else pad_marker
                if pad_marker > node_start
                else len(data)
            )
            nodes = _parse_referenced_nodes(
                data,
                node_start,
                node_end,
                referenced,
                reporter,
                top_layer=top_layer,
            )
            pins, cap_instances, missing_nodes = _materialize_geometry(device_candidates, cap_candidates, nodes, diagnostics)
            if not any(pin.kind == PinKind.DEVICE_BUMP for pin in pins):
                diagnostics.append(SpdDiagnostic("warning", "DEVICE_PINS_NOT_FOUND", "No selected PWR/GND pins from a top-attached IO component were resolved."))

            reporter.report(82, "Counting selected-net vias")
            via_start = via_marker if via_marker >= 0 else node_end
            via_end = pad_marker if pad_marker > via_start else (material_marker if material_marker > via_start else len(data))
            connection_keys = {
                candidate.power.net.casefold()
                for candidate in cap_candidates
            } | {
                candidate.ground.net.casefold()
                for candidate in cap_candidates
            }
            (
                via_usage,
                total_vias,
                top_via_endpoints,
                uncertain_via_nets,
                device_terminal_via_endpoints,
            ) = _parse_vias(
                data,
                via_start,
                via_end,
                plane_keys,
                connection_keys,
                nodes,
                tuple(
                    pin for pin in pins if pin.kind == PinKind.DEVICE_BUMP
                ),
                reporter,
                top_layer=top_layer,
                ground_keys=ground_keys,
                padstacks=padstacks,
            )
            incomplete_device_endpoints = tuple(
                item
                for item in device_terminal_via_endpoints
                if item.status != "complete"
            )
            if incomplete_device_endpoints:
                status_counts = Counter(
                    item.status for item in incomplete_device_endpoints
                )
                diagnostics.append(
                    SpdDiagnostic(
                        "warning",
                        "SPD_DEVICE_TERMINAL_VIA_INCOMPLETE",
                        (
                            f"{len(incomplete_device_endpoints)} of "
                            f"{len(device_terminal_via_endpoints)} selected Device "
                            "terminal(s) lack one unique source-proven direct TOP "
                            "Via attachment: "
                            + ", ".join(
                                f"{status}={count}"
                                for status, count in sorted(status_counts.items())
                            )
                            + ". Layerwise terminal proof must fail closed for "
                            "those terminals."
                        ),
                    )
                )
            padstack_shapes = {
                item.name.casefold(): item.pad_shapes for item in padstacks
            }
            shared_pad = extract_shared_pad_connectivity(
                tuple(
                    DecapPadEvidence(
                        refdes=instance.refdes,
                        top_side=bool(
                            top_layer
                            and (
                                (instance.start_layer or "").casefold()
                                == top_layer.casefold()
                                or (instance.attach_layer or "")
                                .casefold()
                                .replace("_", "")
                                in {"topair", "airtop"}
                            )
                        ),
                        layer=instance.start_layer,
                        power_net=instance.power_net,
                        ground_net=instance.ground_net,
                        power_x_um=instance.power_x_um,
                        power_y_um=instance.power_y_um,
                        power_padstack=instance.power_padstack,
                        power_rotation_degrees=instance.power_pad_rotation_degrees,
                        ground_x_um=(
                            instance.ground_pad_x_um
                            if instance.ground_pad_x_um is not None
                            else instance.x_um
                        ),
                        ground_y_um=(
                            instance.ground_pad_y_um
                            if instance.ground_pad_y_um is not None
                            else instance.y_um
                        ),
                        ground_padstack=instance.ground_padstack,
                        ground_rotation_degrees=instance.ground_pad_rotation_degrees,
                        power_rotation_valid=instance.power_pad_rotation_valid,
                        ground_rotation_valid=instance.ground_pad_rotation_valid,
                    )
                    for instance in cap_instances
                ),
                top_via_endpoints,
                padstack_shapes,
                top_layer=top_layer,
                uncertain_via_nets=uncertain_via_nets,
                top_copper_geometries=tuple(
                    SpdTopCopperGeometry(
                        layer=geometry.layer,
                        net=geometry.net,
                        positive_polygons_um=geometry.positive_polygons_um,
                        negative_polygons_um=geometry.negative_polygons_um,
                        positive_circles_um=geometry.positive_circles_um,
                        negative_circles_um=geometry.negative_circles_um,
                        primitive_order=geometry.primitive_order,
                    )
                    for geometry in plane_geometries
                    if top_layer is not None
                    and geometry.layer.casefold() == top_layer.casefold()
                    and geometry.net.casefold() in connection_keys
                ),
            )
            diagnostics.extend(
                SpdDiagnostic("warning", "SPD_SHARED_PAD_UNRESOLVED", message)
                for message in shared_pad.warnings
            )
            if shared_pad.source_copper_power_edges:
                diagnostics.append(
                    SpdDiagnostic(
                        "info",
                        "SPD_SHARED_PAD_TOP_COPPER_LINKS",
                        (
                            "Used source-positive TOP copper geometry to add "
                            f"{shared_pad.source_copper_power_edges:,} PWR and "
                            f"{shared_pad.source_copper_ground_edges:,} GND shared-pad "
                            "edge(s) without a second SPD scan."
                        ),
                    )
                )

            persisted_plane_geometries = (
                tuple(
                    geometry
                    for geometry in plane_geometries
                    if geometry.net.casefold() in scenario_geometry_keys
                    or geometry.net.casefold() in ground_keys
                )
                if scope == "decap_scenario"
                else plane_geometries
            )

            counts = {
                "positive_plane_nets": len(positive_nets),
                "selected_plane_geometry_groups": len(persisted_plane_geometries),
                "selected_plane_boundary_polygons": sum(
                    len(item.positive_polygons_um)
                    for item in persisted_plane_geometries
                ),
                "selected_plane_boundary_vertices": sum(
                    len(polygon)
                    for item in persisted_plane_geometries
                    for polygon in item.positive_polygons_um
                ),
                "selected_plane_negative_polygons": sum(
                    len(item.negative_polygons_um)
                    for item in persisted_plane_geometries
                ),
                "selected_plane_positive_circles": sum(
                    len(item.positive_circles_um)
                    for item in persisted_plane_geometries
                ),
                "selected_plane_negative_circles": sum(
                    len(item.negative_circles_um)
                    for item in persisted_plane_geometries
                ),
                "selected_plane_polygon_traces": sum(
                    item.polygon_trace_count for item in persisted_plane_geometries
                ),
                "selected_plane_boxes": sum(
                    item.box_count for item in persisted_plane_geometries
                ),
                "selected_plane_subelements": sum(
                    item.positive_subelement_count
                    + item.negative_subelement_count
                    for item in persisted_plane_geometries
                ),
                "selected_power_nets": len(usable_power),
                "selected_ground_nets": len(usable_ground),
                "stackup_layers": len(layers),
                "partial_circuits": partial_count,
                "cap_models": len(cap_models),
                "connections": len(connections),
                "referenced_nodes": len(referenced),
                "resolved_nodes": len(referenced.intersection(nodes)),
                "retained_top_nodes": len(nodes) - len(referenced.intersection(nodes)),
                "unresolved_nodes": len(referenced - set(nodes)),
                "pins": len(pins),
                "device_pins": sum(pin.kind == PinKind.DEVICE_BUMP for pin in pins),
                "decap_pins": sum(pin.kind == PinKind.DECAP_PAD for pin in pins),
                "cap_instances": len(cap_instances),
                "mounted_cap_instances": sum(item.mounted for item in cap_instances),
                "empty_cap_instances": sum(not item.mounted for item in cap_instances),
                "skipped_unselected_cap_instances": skipped_unselected,
                "ambiguous_two_port_connections": ambiguous,
                "missing_referenced_pin_nodes": missing_nodes,
                "padstacks": len(padstacks),
                "vias": total_vias,
                "via_usage_groups": len(via_usage),
                "top_via_endpoints": len(top_via_endpoints),
                "device_terminal_via_endpoints": len(
                    device_terminal_via_endpoints
                ),
                "complete_device_terminal_via_endpoints": sum(
                    item.status == "complete"
                    for item in device_terminal_via_endpoints
                ),
                "shared_pad_clusters": len(shared_pad.clusters),
                "shared_pad_anchored_clusters": sum(
                    item.state == "ANCHORED" for item in shared_pad.clusters
                ),
                "shared_pad_floating_clusters": sum(
                    item.state == "FLOATING" for item in shared_pad.clusters
                ),
                "shared_pad_unresolved_decaps": sum(
                    item.kind == "UNRESOLVED" for item in shared_pad.connections
                ),
                "shared_pad_source_copper_power_edges": (
                    shared_pad.source_copper_power_edges
                ),
                "shared_pad_source_copper_ground_edges": (
                    shared_pad.source_copper_ground_edges
                ),
                "shared_pad_source_copper_members": (
                    shared_pad.source_copper_member_count
                ),
            }
            if routing_extraction is not None:
                counts.update(
                    {
                        f"routing_{key}": int(value)
                        for key, value in routing_extraction.statistics.items()
                    }
                )
            reporter.report(100, "SPD analysis complete")
            return SpdAnalysis(
                source=source,
                outline=outline,
                stackup_layers=layers,
                pins=pins,
                cap_models=cap_models,
                cap_instances=cap_instances,
                padstacks=padstacks,
                via_usage=via_usage,
                diagnostics=tuple(diagnostics),
                model_assets=model_assets,
                counts=counts,
                power_plane_nets=(
                    usable_power
                    if scope == "decap_scenario"
                    else usable_power
                    or tuple(
                        item
                        for item in positive_nets
                        if item.casefold() not in ground_keys
                    )
                ),
                ground_nets=usable_ground,
                plane_geometries=persisted_plane_geometries,
                decap_connections=shared_pad.connections,
                shared_pad_clusters=shared_pad.clusters,
                routing_extraction=routing_extraction,
                device_terminal_via_endpoints=device_terminal_via_endpoints,
            )
    except SpdImportError:
        raise
    except OSError as exc:
        raise SpdImportError(f"cannot read SPD source {source_path}: {exc}") from exc


__all__ = [
    "SpdAnalysis",
    "SpdAnalysisScope",
    "SpdCapInstance",
    "SpdDeviceTerminalViaEndpoint",
    "SpdDeviceTerminalViaStatus",
    "SpdDecapConnection",
    "SpdDiagnostic",
    "SpdFiniteViaQuotientCoverage",
    "SpdFiniteViaQuotientEdge",
    "SpdFiniteViaQuotientVertex",
    "SpdFiniteViaRetargetDestinationCoverage",
    "SpdFiniteViaScenarioIsolationCoverage",
    "SpdFiniteViaSeriesTerm",
    "SpdGroundReachability",
    "SpdImportError",
    "SpdLandingSurfaceContact",
    "SpdPadStack",
    "SpdPadShape",
    "SpdPlaneGeometry",
    "SpdSourceInfo",
    "SpdSurfaceConnectivityComponent",
    "SpdSurfaceEquivalenceProof",
    "SpdSurfaceIslandEquivalenceComponent",
    "SpdSharedPadCluster",
    "SpdViaPathEvidence",
    "SpdViaStructuralEvidence",
    "SpdViaPathRecovery",
    "SpdViaPathSegment",
    "SpdViaIslandPairAggregate",
    "SpdViaIslandPairCoverage",
    "SpdViaIslandPairSegment",
    "SpdViaUsage",
    "analyze_spd",
    "recover_spd_via_paths",
    "recover_spd_ground_reachability",
]
