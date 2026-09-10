"""Memory-mapped Cadence PowerSI SPD import.

The format is line-oriented but production files are commonly hundreds of
megabytes.  This module therefore indexes byte ranges and only decodes records
that can contribute to the PI model.  In particular, multi-port circuit bodies
are skipped and the Node section is parsed only for IDs referenced by selected
device/capacitor connections.
"""

from __future__ import annotations

from array import array
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
import hashlib
import heapq
import json
from decimal import Decimal, DecimalException, InvalidOperation
from math import fsum, isfinite, log10, nextafter, sqrt
import mmap
from pathlib import Path
import re
from tempfile import TemporaryFile
from types import MappingProxyType
from typing import Any, BinaryIO, Literal

from scipy.spatial import cKDTree

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
from ...source_plane_ownership_ir import MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS


class SpdImportError(ValueError):
    """Raised when an SPD source cannot be opened or analysis is cancelled."""


def _append_ownership_provenance(
    provenance: dict[str, Any], section: str, row: Mapping[str, Any]
) -> None:
    total = sum(len(value) for value in provenance.values() if isinstance(value, list))
    if total >= MAX_SOURCE_PLANE_OWNERSHIP_IR_ROWS:
        raise SpdImportError(
            "SOURCE_PLANE_OWNERSHIP_IR_BOUND_EXCEEDED: provenance row bound exceeded"
        )
    provenance.setdefault(section, []).append(dict(row))


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
    """Exact source attachment from a Device pin to its first Via."""

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
    source_node_id: str | None = None


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
    def endpoint_layer(self) -> str | None:
        """Canonical internal endpoint layer (compatibility convenience)."""
        return next(iter(self.contact_island_ids_by_layer), None)

    @property
    def endpoint_island_id(self) -> str | None:
        """Canonical internal endpoint island (compatibility convenience)."""
        values = tuple(self.contact_island_ids_by_layer.values())
        return values[0][0] if values and values[0] else None

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
    target_contacts_by_key: Mapping[
        tuple[str, str, str], tuple[tuple[str, float, float], ...]
    ] = field(default_factory=dict)
    target_contact_count_by_key: Mapping[tuple[str, str, str], int] = field(
        default_factory=dict
    )
    target_contact_hash_by_key: Mapping[tuple[str, str, str], str] = field(
        default_factory=dict
    )
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
        normalized_layers_by_identity: dict[
            int, tuple[object, tuple[str, ...]]
        ] = {}
        for raw_key, raw_layers in self.surface_layers_by_landing.items():
            if len(raw_key) != 2:
                raise ValueError(
                    "surface landing keys must contain Via and endpoint Node IDs"
                )
            key = (str(raw_key[0]).casefold(), str(raw_key[1]).casefold())
            identity = id(raw_layers)
            cacheable = type(raw_layers) is tuple
            cached = (
                normalized_layers_by_identity.get(identity)
                if cacheable
                else None
            )
            if cached is None or cached[0] is not raw_layers:
                layer_by_key: dict[str, str] = {}
                for raw_layer in raw_layers:
                    layer = str(raw_layer).strip()
                    if not layer:
                        raise ValueError("surface landing layers must not be blank")
                    layer_key = layer.casefold()
                    previous = layer_by_key.get(layer_key)
                    if previous is None or layer < previous:
                        layer_by_key[layer_key] = layer
                layers = tuple(
                    layer_by_key[layer_key] for layer_key in sorted(layer_by_key)
                )
                if cacheable:
                    normalized_layers_by_identity[identity] = raw_layers, layers
            else:
                layers = cached[1]
            layers_by_landing[key] = layers
        object.__setattr__(self, "surface_components", components)
        object.__setattr__(
            self,
            "surface_layers_by_landing",
            ordered_mapping_proxy(layers_by_landing),
        )
        islands_by_landing: dict[tuple[str, str], tuple[str, ...]] = {}
        normalized_islands_by_identity: dict[
            int, tuple[object, tuple[str, ...]]
        ] = {}
        for raw_key, raw_islands in self.surface_islands_by_landing.items():
            if len(raw_key) != 2:
                raise ValueError(
                    "surface landing island keys must contain Via and endpoint Node IDs"
                )
            key = (str(raw_key[0]).casefold(), str(raw_key[1]).casefold())
            identity = id(raw_islands)
            cacheable = type(raw_islands) is tuple
            cached = (
                normalized_islands_by_identity.get(identity)
                if cacheable
                else None
            )
            if cached is None or cached[0] is not raw_islands:
                islands = tuple(
                    sorted(
                        {
                            str(item).strip()
                            for item in raw_islands
                            if str(item).strip()
                        }
                    )
                )
                if cacheable:
                    normalized_islands_by_identity[identity] = raw_islands, islands
            else:
                islands = cached[1]
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
        previous_aggregate_key: tuple[str, str, str, str, str, str] | None = None
        for item in via_aggregates:
            if not isinstance(item, SpdViaIslandPairAggregate):
                raise ValueError("Via island-pair aggregates have an invalid record")
            aggregate_key = (
                item.net.casefold(),
                item.padstack.casefold(),
                item.start_layer.casefold(),
                item.end_layer.casefold(),
                item.start_island_id,
                item.end_island_id,
            )
            if aggregate_key == previous_aggregate_key:
                raise ValueError(
                    "Via island-pair aggregates duplicate a physical pair"
                )
            previous_aggregate_key = aggregate_key
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
    device_terminal_via_endpoints: tuple[SpdDeviceTerminalViaEndpoint, ...] = ()
    # Compact source identities gathered during the existing mmap scans.  This
    # is intentionally metadata-only (no source bytes or geometry objects).
    source_plane_ownership_draft: Mapping[str, Any] | None = None

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
_SHAPE_EVENT_RE = re.compile(
    rb"(?m)^(?:\.Shape[ \t]+(?P<shape_name>\S+)"
    rb"|(?P<primitive_kind>[A-Za-z_]+)[^\r\n\s]*::"
    rb"(?P<net>\S+?)(?P<polarity>[+-])(?:\s+|$))"
)
_SHAPE_INDEX_CHUNK_BYTES = 8 * 1024 * 1024
# A tree is worthwhile only for components large enough that repeated nearest
# scans dominate its build cost.  Smaller components retain the exact legacy
# scan, including its deterministic tie-break.
_NEAREST_CONTACT_TREE_THRESHOLD = 64
_VIA_RE = re.compile(
    rb"(?m)^(Via[^\r\n:]*)::([^\s]+)\s+"
    rb"UpperNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?\s+"
    rb"LowerNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?\s+"
    rb"PadStack\s*=\s*(\S+)([^\r\n]*)"
)

# A batch callback returning ``None`` is an authoritative empty result.  Keep
# that distinct from an omitted callback (which selects the scalar fallback),
# otherwise a valid batch miss can trigger a second per-node query.
_BATCH_UNSET = object()
# The optional ``Thermal`` keyword mirrors spd_routing._TRACE_HEADER_RE: PowerSI
# writes thermal-relief copper as an ordinary Trace record with that keyword
# between the net and StartingNode.  It stays non-capturing so the group numbers
# consumed by every reachability pass below are unchanged.
_TRACE_RE = re.compile(
    rb"(?m)^(Trace[^\r\n:]*)::([^\s]+)\s+"
    rb"(?:Thermal\s+)?"
    rb"StartingNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?\s+"
    rb"EndingNode\s*=\s*(Node[^\s:!]+)(?:!![^\s:]+)?(?:::[^\s]+)?"
)
_TRACE_WIDTH_RE = re.compile(rb"\bWidth\s*=\s*(\S+)")
_TRACE_PRIMARY_RE = re.compile(
    rb"^(Trace[^\r\n:]*)::([^\s]+)\s+"
    rb"(?:Thermal\s+)?"
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
# The PadStack scan must live inside the optional group; a bare lazy ``.*?`` in
# front of it always matches empty and makes the fallback unreachable.
_NODE_ATTR_RE = re.compile(
    rb"\bX\s*=\s*(\S+)\s+Y\s*=\s*(\S+)(?:.*?\bPadStack\s*=\s*(\S+))?"
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


def _canonical_casefolded_ids_digest(
    values: Iterable[str],
    *,
    chunk_size: int = 16384,
    check: Callable[[], None] | None = None,
) -> Any:
    """Hash case-folded IDs in canonical order without retaining them all."""

    if chunk_size <= 0:
        raise ValueError("canonical digest chunk size must be positive")
    runs: list[BinaryIO] = []

    def spill(chunk: list[str]) -> None:
        chunk.sort()
        run = TemporaryFile(mode="w+b")
        try:
            for value in chunk:
                encoded = value.encode("utf-8")
                run.write(len(encoded).to_bytes(4, "big"))
                run.write(encoded)
            run.seek(0)
        except Exception:
            run.close()
            raise
        runs.append(run)

    def read_run(run: BinaryIO) -> Iterator[str]:
        while header := run.read(4):
            if len(header) != 4:
                raise SpdImportError("Canonical via ID digest run is truncated")
            size = int.from_bytes(header, "big")
            encoded = run.read(size)
            if len(encoded) != size:
                raise SpdImportError("Canonical via ID digest run is truncated")
            try:
                yield encoded.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SpdImportError("Canonical via ID digest run is invalid") from exc

    try:
        if check is not None:
            check()
        chunk: list[str] = []
        for value in values:
            chunk.append(value.casefold())
            if len(chunk) == chunk_size:
                spill(chunk)
                chunk = []
                if check is not None:
                    check()
        if chunk:
            spill(chunk)
            if check is not None:
                check()

        digest = hashlib.sha256()
        for index, value in enumerate(
            heapq.merge(*(read_run(run) for run in runs)), start=1
        ):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
            if check is not None and index % chunk_size == 0:
                check()
        if check is not None:
            check()
        return digest
    finally:
        for run in runs:
            run.close()


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


def _iter_shape_events(
    data: mmap.mmap,
    start: int,
    end: int,
    reporter: _Reporter,
):
    """Yield Shape headers and primitive headers in mmap byte order."""

    for chunk_start, chunk_end in _iter_line_bounded_chunks(data, start, end):
        reporter.check()
        yield from _SHAPE_EVENT_RE.finditer(data, chunk_start, chunk_end)


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
    scale_by_unit = _LENGTH_SCALE_BY_UNIT
    values: list[float] = []
    append = values.append
    finite = isfinite
    for match in _LENGTH_RE.finditer(raw):
        value = float(match.group(1))
        unit = match.group(2).lower()
        result = value * scale_by_unit[unit]
        if not finite(result):
            raise ValueError("SPD length is not finite")
        append(result)
    return values


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
    *,
    include_unselected_power: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    start = _find_line(data, b".NetList")
    if start < 0:
        diagnostics.append(
            SpdDiagnostic(
                "info",
                "NETLIST_MISSING",
                "No .NetList selection was found; positive plane polygons will "
                "be used as the rail filter.",
            )
        )
        return (), ()
    end = _find_line(data, b".EndNetList", start)
    if end < 0:
        end = len(data)
        diagnostics.append(
            SpdDiagnostic(
                "warning",
                "NETLIST_UNTERMINATED",
                ".NetList has no .EndNetList; parsed selections through end of file.",
            )
        )
    # PowerSI writes .NetList as two ordered groups. Only the first row in a
    # group is guaranteed to contain ``-> GroundNets`` or ``-> PowerNets``;
    # later rows inherit that classification. Lines before the first group
    # marker are document properties or ordinary signal nets and must not be
    # promoted to editable power rails.
    _ = gnd_keys  # Kept in the signature for compatibility with older callers.
    power: list[str] = []
    ground: list[str] = []
    selected_power_rows: list[str] = []
    selected_ground_rows: list[str] = []
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

        if active_group is None:
            continue

        row_unselected = b"::unselected" in token.lower()
        name = _decode(token.split(b"::", 1)[0])
        if active_group == "ground":
            ground.append(name)
            if not row_unselected:
                selected_ground_rows.append(name)
        else:
            power.append(name)
            if not row_unselected:
                selected_power_rows.append(name)
    inventory_power = _unique(power)
    selected_power = _unique(selected_power_rows)
    selected_ground = _unique(selected_ground_rows)
    result_power = inventory_power if include_unselected_power else selected_power
    if result_power or selected_ground:
        message = (
            "Using full PowerNets inventory "
            f"({len(result_power)} row(s)) and selected GroundNets "
            f"({len(selected_ground)} rail(s)) from .NetList."
            if include_unselected_power
            else f"Using {len(result_power)} selected power net(s) and "
            f"{len(selected_ground)} selected ground net(s) from .NetList."
        )
        diagnostics.append(
            SpdDiagnostic("info", "NETLIST_SELECTION_USED", message)
        )
    return result_power, selected_ground


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
    # Stream Shape headers and primitive headers once in byte order.  The
    # event iterator scans each newline-bounded mmap chunk in C, avoiding the
    # old header materialization plus second full-section traversal.
    primitive_index = 0
    layer: str | None = None
    for event in _iter_shape_events(data, start, end, reporter):
        shape_name = event.group("shape_name")
        if shape_name is not None:
            layer = _decode(shape_name)
            if layer.casefold().endswith("pkgshape"):
                layer = layer[: -len("pkgshape")]
            continue

        primitive_kind = event.group("primitive_kind")
        net_raw = event.group("net")
        polarity = event.group("polarity")
        assert primitive_kind is not None
        assert net_raw is not None
        assert polarity is not None
        if primitive_index % 128 == 0:
            reporter.report(
                _span_percent(event.start(), start, end, 15, 29),
                "Scanning selected plane geometry",
            )
        primitive_index += 1
        line_end = _line_end(data, event.start(), end)
        first_line = data[event.start():line_end]
        net = _decode(net_raw)
        is_sub_element = b"Sub-element" in first_line
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
                malformed_reason = "Box requires start-corner X/Y and positive width/height"
            else:
                x_um, y_um, width_um, height_um = values
                values = [
                    x_um,
                    y_um,
                    x_um + width_um,
                    y_um,
                    x_um + width_um,
                    y_um + height_um,
                    x_um,
                    y_um + height_um,
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
                "SPD_BOX_START_CORNER_SIZE_INTERPRETATION",
                f"Normalized {box_count:,} selected Box record(s) as rectangles, "
                "interpreting the four lengths as start-corner X/Y and width/height.",
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
    data: mmap.mmap, start: int, end: int, diagnostics: list[SpdDiagnostic],
    source_provenance: dict[str, Any] | None = None,
) -> tuple[dict[str, _DielectricMaterial], dict[str, float]]:
    dielectrics: dict[str, _DielectricMaterial] = {}
    metals: dict[str, float] = {}
    kind: str | None = None
    name = ""
    rows: list[tuple[float, ...]] = []
    scale: float | None = None
    block_start: int | None = None
    block_end: int | None = None

    def physical_end(offset: int) -> int:
        newline = data.find(b"\n", offset, end)
        return end if newline < 0 else newline + 1

    def flush(end_offset: int | None = None) -> None:
        nonlocal kind, name, rows, scale, block_start, block_end
        if not kind or not name or not rows:
            kind, name, rows, scale, block_start, block_end = None, "", [], None, None, None
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
        if source_provenance is not None and block_start is not None:
            stop = end_offset if end_offset is not None else block_end
            if stop is None or stop <= block_start:
                stop = min(end, block_start + len(name.encode("utf-8")) + 1)
            record_id = f"material:{name}"
            _append_ownership_provenance(source_provenance, "source_records", {
                "ordinal": len(source_provenance.get("source_records", ())),
                "record_id": record_id,
                "kind": "Material",
                "name": name,
                "logical_net": None,
                "layer": None,
                "source_offset": block_start,
                "source_end": min(end, stop),
                "source_record_sha256": hashlib.sha256(bytes(data[block_start:min(end, stop)])).hexdigest(),
                "raw_ordinal": len(source_provenance.get("source_records", ())),
            })
        kind, name, rows, scale = None, "", [], None
        block_start = block_end = None

    for offset, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        folded = stripped.lower()
        if folded.startswith(b".dielectricmodel "):
            flush(offset)
            kind, name = "dielectric", _decode(stripped.split(None, 1)[1])
            block_start = offset
        elif folded.startswith(b".metalmodel "):
            flush(offset)
            kind, name = "metal", _decode(stripped.split(None, 1)[1])
            block_start = offset
        elif folded.startswith((b".enddielectricmodel", b".endmetalmodel")):
            block_end = physical_end(offset)
            flush(block_end)
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
    flush(end)
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
    source_provenance: dict[str, Any] | None = None,
) -> tuple[StackupLayer, ...]:
    result: list[StackupLayer] = []
    seen: set[str] = set()
    material_record_ids = {
        str(row.get("name", "")).casefold(): str(row.get("record_id"))
        for row in (source_provenance or {}).get("source_records", ())
        if isinstance(row, Mapping) and row.get("kind") == "Material"
        and isinstance(row.get("name"), str) and isinstance(row.get("record_id"), str)
    }
    for offset, raw in _iter_lines(data, start, end):
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
            # StackupLayer.is_conductor is derived from conductivity_s_m, so a
            # conductor row without one would be silently demoted to a
            # dielectric and shift the effective TOP layer.  Substituting a
            # default conductivity would invent source data, so the import is
            # blocked instead.
            diagnostics.append(SpdDiagnostic("error", "LAYER_CONDUCTIVITY_MISSING", f"Conductor layer {name!r} references material {material!r} without a usable conductivity table."))
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
            if source_provenance is not None:
                layer_record_id = f"layer:{name}"
                has_material_record = material_model is not None or material_key in metals
                if has_material_record:
                    material_record_id = material_record_ids.get(material_key)
                    if material_record_id is None:
                        raise SpdImportError(
                            f"SOURCE_PLANE_OWNERSHIP_IR_INCOMPLETE: material source record is absent for {material!r}"
                        )
                else:
                    material_record_id = layer_record_id
                material_origin = "material_model" if has_material_record else "source"
                conductivity_origin = "source" if conductivity_raw else ("material_model" if conductivity is not None and material_key in metals else "unavailable")
                _append_ownership_provenance(source_provenance, "stackup_layers", {
                    "ordinal": len(source_provenance.get("stackup_layers", ())),
                    "layer_name": name,
                    "layer_kind": "conductor" if conductor else "dielectric",
                    "thickness_um": thickness,
                    "thickness_origin": "source",
                    "thickness_source_record_id": layer_record_id,
                    "conductivity_s_per_m": conductivity,
                    "conductivity_origin": conductivity_origin,
                    "conductivity_source_record_id": layer_record_id if conductivity is not None and conductivity_origin == "source" else (material_record_id if conductivity is not None else None),
                    "material_name": material or name,
                    "material_origin": material_origin,
                    "material_source_record_id": material_record_id,
                })
                if not conductor and material_model is not None:
                    for point_ordinal, item in enumerate(material_model.properties):
                        _append_ownership_provenance(source_provenance, "dielectric_points", {
                            "ordinal": len(source_provenance.get("dielectric_points", ())),
                            "layer_name": name,
                            "point_ordinal": point_ordinal,
                            "frequency_hz": item.frequency_hz,
                            "frequency_origin": "material_model",
                            "frequency_source_record_id": material_record_id,
                            "epsilon_r": float(dk) if permittivity_raw else item.dk,
                            "epsilon_origin": "layer_override" if permittivity_raw else "material_model",
                            "epsilon_source_record_id": layer_record_id if permittivity_raw else material_record_id,
                            "loss_tangent": float(df) if loss_raw else item.df,
                            "loss_tangent_origin": "layer_override" if loss_raw else "material_model",
                            "loss_tangent_source_record_id": layer_record_id if loss_raw else material_record_id,
                        })
            seen.add(key)
        except ValueError as exc:
            diagnostics.append(SpdDiagnostic("warning", "LAYER_INVALID", f"Layer {name!r} was skipped: {exc}."))
    if not result:
        diagnostics.append(SpdDiagnostic("error", "STACKUP_NOT_FOUND", "No valid Thickness layer rows were found in the SPD layer section."))
    return tuple(result)


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
        # Record-introducing keywords are tested before the port fall-through:
        # a missing .EndC must only warn about the unterminated block, never
        # swallow the .Part/.Component records that follow it.
        elif folded.startswith(b".part ") and not folded.startswith(b".partialckt"):
            flush_connection(active_header is not None)
            tokens = stripped.split()
            if len(tokens) >= 2:
                part_name = _decode(tokens[1])
                tag_match = re.search(rb"\bTags\s*=\s*\"([^\"]*)\"", stripped, re.IGNORECASE)
                tags = () if tag_match is None else tuple(item.strip() for item in _decode(tag_match.group(1)).split(",") if item.strip())
                parts[part_name.casefold()] = _Part(part_name, tags)
        elif folded.startswith(b".component "):
            flush_connection(active_header is not None)
            tokens = stripped.split()
            if len(tokens) >= 2:
                refdes = _decode(tokens[1])
                start_layer = _attribute(stripped, b"StartLayer")
                attach_layer = _attribute(stripped, b"AttachLayer")
                components[refdes.casefold()] = _Component(refdes, _decode(start_layer) if start_layer else None, _decode(attach_layer) if attach_layer else None)
        elif active_header is not None:
            port = _parse_port(stripped)
            if port is not None:
                active_ports.append(port)
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
            source_padstack=node.padstack,
            source_layer=node.layer,
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
    reporter: _Reporter,
    *,
    top_layer: str | None,
    padstacks: tuple[SpdPadStack, ...],
    device_pins: tuple[PinRecord, ...] = (),
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
    incident_by_node: dict[str, list[tuple[str, str, str, str]]] = {}
    for index, match in enumerate(_VIA_RE.finditer(data, start, end)):
        total += 1
        via_id_display = _decode(match.group(1))
        net_display = _decode(match.group(2))
        padstack_display = _decode(match.group(5))
        upper_display = _decode(match.group(3))
        lower_display = _decode(match.group(4))
        incident_by_node.setdefault(upper_display.casefold(), []).append(
            (via_id_display, net_display, padstack_display, lower_display)
        )
        incident_by_node.setdefault(lower_display.casefold(), []).append(
            (via_id_display, net_display, padstack_display, upper_display)
        )
        if index % 8192 == 0:
            reporter.report(
                _span_percent(match.start(), start, end, 83, 96),
                "Counting vias and resolving TOP landing evidence",
            )
        net_raw = match.group(2)
        padstack_raw = match.group(5)
        net_key_raw = net_raw.lower()
        padstack_key_raw = padstack_raw.lower()
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
        via_id = _decode(match.group(1))
        net = net or _decode(net_raw)
        upper_node_id = _decode(match.group(3))
        lower_node_id = _decode(match.group(4))
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
    terminal_endpoints: list[SpdDeviceTerminalViaEndpoint] = []
    for pin in device_pins:
        source_node_id = getattr(pin, "source_node_id", None)
        source_layer = getattr(pin, "source_layer", None)
        source_padstack = getattr(pin, "source_padstack", None)
        pin_net = str(getattr(pin, "net", ""))
        candidates = tuple(
            item
            for item in incident_by_node.get(str(source_node_id or "").casefold(), ())
            if item[1].casefold() == pin_net.casefold()
        )
        issues: list[str] = []
        if not source_node_id:
            status: SpdDeviceTerminalViaStatus = "source_node_missing"
            issues.append("source_node_missing")
        elif not source_layer:
            status = "source_layer_missing"
            issues.append("source_layer_missing")
        elif top_key is not None and str(source_layer).casefold() != top_key:
            status = "source_pin_not_top"
            issues.append("source_pin_not_top")
        elif not candidates:
            status = "missing_incident_via"
            issues.append("missing_incident_via")
        elif len(candidates) > 1:
            status = "ambiguous_incident_via"
            issues.append("ambiguous_incident_via")
        else:
            status = "complete"
        incident = candidates[0] if len(candidates) == 1 else None
        terminal_endpoints.append(
            SpdDeviceTerminalViaEndpoint(
                pin_id=str(getattr(pin, "pin_id", "")),
                refdes=str(getattr(pin, "refdes", "")),
                pin=str(getattr(pin, "pin", "")),
                terminal=str(getattr(getattr(pin, "terminal", ""), "value", getattr(pin, "terminal", ""))),
                net=pin_net,
                source_node_id=source_node_id,
                source_layer=source_layer,
                source_padstack=source_padstack,
                source_x_um=float(getattr(pin, "x_um", 0.0)),
                source_y_um=float(getattr(pin, "y_um", 0.0)),
                status=status,
                issues=tuple(issues),
                candidate_count=len(candidates),
                candidate_via_ids=tuple(item[0] for item in candidates),
                incident_via_id=incident[0] if incident else None,
                incident_net=incident[1] if incident else None,
                incident_padstack=incident[2] if incident else None,
                incident_opposite_node_id=incident[3] if incident else None,
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
        tuple(sorted(terminal_endpoints, key=lambda item: (item.refdes.casefold(), item.pin.casefold()))),
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
                "source_node_id": endpoint_node_id,
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
                            source_node_id=str(state["source_node_id"]),
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
            # Only the segment spans are corrected here; replace() keeps every
            # other field (source_node_id in particular) from being dropped.
            corrected[via_key].append(replace(item, segments=segments))
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


def _index_reachable_layers_by_landing(
    reachable_rows: Iterable[tuple[str, str, str]],
    needed_landing_keys: set[tuple[str, str]],
) -> dict[tuple[str, str], set[str]]:
    layers_by_landing: dict[tuple[str, str], set[str]] = {}
    for via_key, node_key, target_layer in reachable_rows:
        landing_key = (via_key, node_key)
        if landing_key in needed_landing_keys:
            layers_by_landing.setdefault(landing_key, set()).add(target_layer)
    return layers_by_landing


def recover_spd_ground_reachability(
    path: str | Path,
    *,
    landings: Iterable[object],
    requested_target_layers_by_landing: Mapping[
        tuple[str, str, str], Iterable[str]
    ] | None = None,
    terminal_contact_landings: Iterable[object] | None = None,
    scenario_isolated_terminal_landings: Iterable[object] | None = None,
    retarget_destination_requests: Iterable[tuple[str, str, str]] | None = None,
    terminal_owned_via_ids: Iterable[str] | None = None,
    padstacks: Iterable[SpdPadStack] | None = None,
    stackup_layers: Iterable[StackupLayer] | None = None,
    target_layers_by_net: Mapping[str, Iterable[str]],
    target_node_predicate: Callable[[str, str, str, float, float], bool] | None = None,
    target_node_predicate_batch: Callable[
        [str, str, Sequence[tuple[float, float]]], Sequence[bool]
    ] | None = None,
    target_node_predicates_by_key: Mapping[
        tuple[str, str], Callable[[str, str, str, float, float], bool]
    ] | None = None,
    same_layer_artwork_layers_by_net: Mapping[str, Iterable[str]] | None = None,
    same_layer_artwork_component: Callable[[str, str, float, float], object | None] | None = None,
    same_layer_artwork_components_batch: Callable[
        [str, str, Sequence[tuple[float, float]]], Sequence[object | None]
    ] | None = None,
    same_layer_artwork_release: Callable[[str, str], None] | None = None,
    target_trace_contact_predicate: Callable[..., tuple[object, ...] | None] | None = None,
    target_node_surface_resolver: Callable[
        [str, str, str, float, float], str | None
    ] | None = None,
    target_node_surface_resolver_batch: Callable[
        [str, str, Sequence[str], Sequence[tuple[float, float]]], Sequence[str | None]
    ] | None = None,
    target_surface_island_ids: Mapping[tuple[str, str], Iterable[str]] | None = None,
    expected_source: SpdSourceInfo | None = None,
    include_traces: bool = True,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> SpdGroundReachability:
    """Prove same-NET GND landing reachability to exact target layers.

    This is intentionally a separate batched graph pass from unique Via-path
    recovery: a branching Trace/Via graph is valid for return connectivity but
    cannot be condensed into one serial RL chain. Trace/Via connectivity is
    indexed before the single Node/artwork pass; Trace records receive one
    conditional unresolved-component seam pass when finite-width evidence is
    required.

    ``include_traces=False`` restricts the result to a directly joined local Via
    stack.  Distribution audits use that mode to distinguish unchanged-barrel
    reachability from the separate same-XY re-termination planning assumption.
    """

    reporter = _Reporter(progress, is_cancelled)
    reporter.report(0, "Checking mixed-reference GND landing reachability")
    source_path = Path(path)
    if not source_path.is_file():
        raise SpdImportError(f"SPD source does not exist: {source_path}")
    target_layer_display: dict[tuple[str, str], str] = {}
    for raw_net, raw_layers in target_layers_by_net.items():
        for raw_layer in raw_layers:
            target_layer_display.setdefault(
                (str(raw_net).casefold(), str(raw_layer).casefold()), str(raw_layer)
            )
    target_layers = {
        str(net).casefold(): {str(layer).casefold() for layer in layers}
        for net, layers in target_layers_by_net.items()
        if str(net).strip() and any(str(layer).strip() for layer in layers)
    }
    artwork_layers = {
        str(net).casefold(): {str(layer).casefold() for layer in layers}
        for net, layers in (same_layer_artwork_layers_by_net or {}).items()
        if str(net).strip()
    }
    # The dense graph universe includes artwork-only nets as well as regular
    # target nets.  This lets same-NET artwork connectivity be indexed without
    # ever admitting cross-NET Trace/Via edges into a target component.
    graph_layers = {
        net: set(target_layers.get(net, ())) | set(artwork_layers.get(net, ()))
        for net in (set(target_layers) | set(artwork_layers))
    }
    # Surface classification is an independent, boundary-inclusive channel
    # from strict target acceptance.  Require the resolver/inventory pair when
    # callers request a surface certificate; fail closed on malformed IDs.
    if (
        target_node_surface_resolver is None
        and target_node_surface_resolver_batch is None
    ) != (target_surface_island_ids is None):
        raise ValueError(
            "target_node_surface_resolver and target_surface_island_ids must be supplied together"
        )
    surface_inventory: dict[tuple[str, str], tuple[str, ...]] = {}
    seen_surface_ids: set[str] = set()
    if target_surface_island_ids is not None:
        for raw_key, raw_ids in target_surface_island_ids.items():
            if not isinstance(raw_key, tuple) or len(raw_key) != 2:
                raise ValueError("surface island inventory key is invalid")
            net_key, layer_key = (str(raw_key[0]).casefold(), str(raw_key[1]).casefold())
            raw_id_values = tuple(str(item).strip() for item in raw_ids if str(item).strip())
            # Preserve the established source-contract normalization: exact
            # duplicate IDs within one layer are harmless and IDs are
            # case-sensitive for cross-layer uniqueness.
            ids = tuple(sorted(set(raw_id_values)))
            if (
                not net_key
                or not layer_key
                or not ids
                or (net_key, layer_key) not in {
                    (target_net, target_layer)
                    for target_net, target_layers_for_net in target_layers.items()
                    for target_layer in target_layers_for_net
                }
                or seen_surface_ids.intersection(ids)
            ):
                raise ValueError("surface island inventory is invalid")
            seen_surface_ids.update(ids)
            surface_inventory[(net_key, layer_key)] = ids
        expected_surface_keys = {
            (target_net, target_layer)
            for target_net, target_layers_for_net in target_layers.items()
            for target_layer in target_layers_for_net
        }
        if set(surface_inventory) != expected_surface_keys:
            raise ValueError("surface island inventory does not exactly cover target layers")
        seen_surface_ids.clear()
    surface_layers_by_landing: dict[tuple[str, str], tuple[str, ...]] = {}
    surface_islands_by_landing: dict[tuple[str, str], tuple[str, ...]] = {}
    landing_surface_contacts: list[SpdLandingSurfaceContact] = []
    via_island_pair_aggregates: list[SpdViaIslandPairAggregate] = []
    via_island_pair_coverage: SpdViaIslandPairCoverage | None = None
    surface_equivalence_components: set[SpdSurfaceIslandEquivalenceComponent] = set()
    surface_equivalence_proofs: list[SpdSurfaceEquivalenceProof] = []
    surface_code_by_key: dict[tuple[str, str, str], int] = {}
    surface_key_by_code: list[tuple[str, str, str]] = []
    surface_code_by_index = array("I")
    surface_code_representative: list[int] = []
    surface_code_count = array("I")
    surface_trace_net_codes = array("I")
    surface_trace_first_indices = array("I")
    surface_trace_second_indices = array("I")
    surface_trace_net_by_code: list[str] = []
    surface_trace_net_code_by_key: dict[str, int] = {}
    # Keep raw Via records as fixed-width source offsets and dense codes only.
    # Decoding a Via identity is deferred to the single mmap replay below (and
    # to final finite-edge owner emission), avoiding one Python tuple/string per
    # source record on production-sized SPD files.
    via_source_net_codes = array("I")
    via_source_net_key_by_code: list[str] = []
    via_source_net_code_by_key: dict[str, int] = {}
    via_source_first_indices = array("I")
    via_source_second_indices = array("I")
    via_source_padstack_codes = array("I")
    via_source_padstack_names: list[str] = []
    via_padstack_code_by_key: dict[str, int] = {}
    via_terminal_display_endpoints: dict[tuple[str, str], tuple[str, str]] = {}
    via_terminal_padstack_by_key: dict[tuple[str, str], str] = {}
    via_seen_keys: set[tuple[str, str]] = set()
    terminal_contact_records = tuple(terminal_contact_landings or ())
    terminal_contact_owner_by_key: dict[
        tuple[str, str], tuple[str, str, str]
    ] = {}
    terminal_contact_identity_by_via_kind: dict[
        tuple[str, str], tuple[str, str, str]
    ] = {}
    for item in terminal_contact_records:
        via_id = str(getattr(item, "via_id", "")).strip()
        node_id = str(getattr(item, "endpoint_node_id", "")).strip()
        net = str(getattr(item, "net", "")).strip()
        if not via_id or not node_id or not net:
            raise ValueError("terminal landing has a blank source graph identity")
        via_key = via_id.casefold()
        node_key = node_id.casefold()
        net_key = net.casefold()
        if net_key not in target_layers:
            continue
        pin_id = str(getattr(item, "pin_id", "") or "").strip()
        owner_kind = "device" if pin_id else "decap"
        terminal_id = (
            pin_id
            if owner_kind == "device"
            else f"decap-via:{via_key}:{node_key}"
        )
        contact_key = (via_key, node_key)
        contact_identity = (net_key, owner_kind, terminal_id.casefold())
        previous_contact = terminal_contact_owner_by_key.get(contact_key)
        if previous_contact is not None and (
            previous_contact[0],
            previous_contact[1],
            previous_contact[2].casefold(),
        ) != contact_identity:
            raise ValueError(
                "one terminal landing identity has conflicting NET or owner"
            )
        terminal_contact_owner_by_key.setdefault(
            contact_key, (net_key, owner_kind, terminal_id)
        )
        via_kind_key = (via_key, owner_kind)
        via_kind_identity = (net_key, node_key, terminal_id.casefold())
        previous_via_kind = terminal_contact_identity_by_via_kind.get(
            via_kind_key
        )
        if (
            previous_via_kind is not None
            and previous_via_kind != via_kind_identity
        ):
            raise ValueError(
                "one physical terminal Via ID cannot be claimed by conflicting "
                "NETs, endpoint Nodes, or same-kind terminal owners"
            )
        terminal_contact_identity_by_via_kind.setdefault(
            via_kind_key, via_kind_identity
        )
    terminal_contact_identity_by_via_kind.clear()
    terminal_contact_via_ids = {
        via_key for via_key, _node_key in terminal_contact_owner_by_key
    }
    isolated_landing_keys = {
        (str(getattr(item, "via_id", "")).casefold(), str(getattr(item, "endpoint_node_id", "")).casefold())
        for item in (scenario_isolated_terminal_landings or ())
    }
    isolated_node_ids = {
        str(getattr(item, "endpoint_node_id", "")).casefold()
        for item in (scenario_isolated_terminal_landings or ())
        if str(getattr(item, "endpoint_node_id", "")).strip()
    }
    isolated_node_keys = {
        (
            str(getattr(item, "net", "")).casefold(),
            str(getattr(item, "endpoint_node_id", "")).casefold(),
        )
        for item in (scenario_isolated_terminal_landings or ())
        if str(getattr(item, "endpoint_node_id", "")).strip()
    }
    landing_records = tuple(landings)
    landing_keys: list[tuple[str, str, str]] = []
    for landing in landing_records:
        try:
            landing_keys.append(
                (
                    str(getattr(landing, "via_id")).casefold(),
                    str(getattr(landing, "endpoint_node_id")).casefold(),
                    str(getattr(landing, "net")).casefold(),
                )
            )
        except AttributeError as exc:
            raise ValueError("GND landing lacks source graph identity") from exc
    requested_layers_by_landing: dict[
        tuple[str, str, str], set[str]
    ] | None = None
    if requested_target_layers_by_landing is not None:
        requested_layers_by_landing = {}
        for raw_key, raw_layers in requested_target_layers_by_landing.items():
            if not isinstance(raw_key, tuple) or len(raw_key) != 3:
                raise ValueError(
                    "requested target layer keys must contain Via, endpoint Node, and NET"
                )
            if any(not str(item).strip() for item in raw_key):
                raise ValueError("requested target layer keys must not be blank")
            key = (
                str(raw_key[0]).casefold(),
                str(raw_key[1]).casefold(),
                str(raw_key[2]).casefold(),
            )
            if isinstance(raw_layers, (str, bytes)):
                raise ValueError("requested target layers must be an iterable of layers")
            try:
                layers = {str(layer).casefold() for layer in raw_layers}
            except TypeError as exc:
                raise ValueError(
                    "requested target layers must be an iterable of layers"
                ) from exc
            if not layers.issubset(target_layers.get(key[2], set())):
                raise ValueError(
                    "requested target layer is outside target_layers_by_net"
                )
            requested_layers_by_landing.setdefault(key, set()).update(layers)
        if set(requested_layers_by_landing) != set(landing_keys):
            raise ValueError(
                "requested target layer coverage must exactly match landings"
            )
    requested_by_key: dict[tuple[str, str, str], str] = {}
    requested_coordinates: dict[tuple[str, str, str], tuple[float, float]] = {}
    for landing, (via_key, node_key, net_key) in zip(
        landing_records, landing_keys, strict=True
    ):
        target_layers_for_landing = (
            requested_layers_by_landing[(via_key, node_key, net_key)]
            if requested_layers_by_landing is not None
            else target_layers.get(net_key, ())
        )
        for target_layer in target_layers_for_landing:
            request_key = (via_key, node_key, target_layer)
            requested_by_key[request_key] = net_key
            requested_coordinates[request_key] = (
                float(getattr(landing, "x_um", 0.0)),
                float(getattr(landing, "y_um", 0.0)),
            )
    requested = sorted(requested_by_key)
    landing_keys.clear()
    if requested_layers_by_landing is not None:
        requested_layers_by_landing.clear()
    if not requested and not surface_inventory and not terminal_contact_records:
        return SpdGroundReachability(frozenset(), frozenset(), {
            "requested": 0, "reachable": 0, "unreachable": 0,
            "node_section_passes": 0, "trace_section_passes": 0,
            "via_section_passes": 0, "components": 0,
            "node_release_index_passes": 0,
            "deferred_artwork_passes": 0, "deferred_artwork_keys": 0,
            "max_live_artwork_shapes": 0,
            "target_nodes_considered": 0, "target_nodes_filtered": 0,
            "conditional_target_component_passes": 0,
            "conditional_target_components_scanned": 0,
            "conditional_target_components_recovered": 0,
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
    target_records_by_net: dict[
        str, dict[str, tuple[int, str, float, float, str, int]]
    ] = {net: {} for net in target_layers}
    node_positions_by_net: dict[str, dict[str, tuple[str, float, float]]] = {
        net: {} for net in graph_layers
    }
    node_net_key_by_index: list[str] = []
    node_id_by_index: list[str] = []
    layer_display_by_key: dict[tuple[str, str], str] = {}
    node_index_by_net: dict[str, dict[str, int]] = {
        net: {} for net in graph_layers
    }
    parents = array("I")
    ranks = bytearray()
    # Dense auxiliary DSUs share the regular node index space.  They avoid a
    # second million-entry string-key parent map for surface/equivalence
    # certificates while retaining the legacy maps for contact provenance.
    surface_parents = array("I")
    surface_ranks = bytearray()
    equivalence_parents = array("I")
    equivalence_ranks = bytearray()
    node_layer_codes = array("I")
    layer_code_by_key: dict[str, int] = {}
    layer_key_by_code: dict[int, str] = {}
    layer_display_by_code: list[str] = []
    via_source_offsets = array("Q")
    components = 0
    graph_edges = 0
    trace_edges = 0
    via_edges = 0
    artwork_edges = 0
    artwork_nodes = 0
    artwork_components: set[tuple[str, str, str]] = set()
    artwork_representatives: dict[tuple[str, str, str], int] = {}
    artwork_trace_contacts = 0
    artwork_component_members_registered: set[tuple[str, str, str]] = set()
    trace_artwork_conditional_passes = 0
    conditional_node_position_passes = 0
    trace_artwork_conditional_checks = 0
    trace_artwork_conditional_successes = 0
    target_nodes_considered = 0
    target_nodes_filtered = 0
    filtered_target_nets: set[str] = set()
    conditional_target_component_passes = 0
    conditional_target_components_scanned = 0
    conditional_target_components_recovered = 0
    artwork_batch_points: dict[tuple[str, str], list[tuple[str, int | None, float, float]]] = {}
    target_batch_points: dict[tuple[str, str], list[tuple[int, str, float, float, str]]] = {}
    deferred_artwork_offsets: dict[tuple[str, str], array] = {}
    deferred_artwork_key_order: list[tuple[str, str]] = []
    artwork_batch_total = 0
    target_batch_total = 0
    batch_progress = 15
    node_release_index_passes = 0
    deferred_artwork_passes = 0
    deferred_artwork_keys = 0
    max_live_artwork_shapes = 0

    def report_batch_progress(message: str) -> None:
        nonlocal batch_progress
        # Keep the graph's bounded sub-phase monotonic while leaving room for
        # the subsequent Trace/Via and certificate phases.
        batch_progress = min(40, batch_progress + 1)
        reporter.report(batch_progress, message)

    def flush_artwork_batches() -> None:
        nonlocal artwork_nodes, artwork_edges, artwork_batch_total
        if same_layer_artwork_components_batch is None:
            return
        for (batch_net, batch_layer), batch in tuple(artwork_batch_points.items()):
            if not batch:
                continue
            reporter.check()
            components_batch = same_layer_artwork_components_batch(
                batch_net,
                batch_layer,
                tuple((x_um, y_um) for _node_id, _node_index, x_um, y_um in batch),
            )
            if len(components_batch) != len(batch):
                raise SpdImportError("artwork batch resolver returned an invalid result length")
            for (node_id, node_index, _x_um, _y_um), component in zip(batch, components_batch, strict=True):
                if component is None:
                    continue
                artwork_nodes += 1
                component_key = (batch_net.casefold(), batch_layer.casefold(), str(component))
                artwork_components.add(component_key)
                if node_index is None:
                    node_index = index_for(batch_net.casefold(), node_id)
                representative = artwork_representatives.setdefault(component_key, node_index)
                artwork_component_members_registered.add(component_key)
                if representative != node_index:
                    artwork_edges += 1
                    union_indices(node_index, representative)
            batch.clear()
            report_batch_progress("Resolving same-layer artwork batches")
        artwork_batch_total = 0

    def flush_target_batches() -> None:
        nonlocal target_batch_total
        if target_node_predicate_batch is None:
            return
        all_decisions: list[tuple[int, str, str, str, float, float, str]] = []
        for (batch_net, batch_layer), batch in tuple(target_batch_points.items()):
            if not batch:
                continue
            reporter.check()
            accepted = target_node_predicate_batch(
                batch_net,
                batch_layer,
                tuple((x_um, y_um) for _seq, _node_id, x_um, y_um, _display in batch),
            )
            if accepted is None:
                accepted = (False,) * len(batch)
            if len(accepted) != len(batch):
                raise SpdImportError("target batch resolver returned an invalid result length")
            all_decisions.extend(
                (seq, batch_net, batch_layer, display_node_id, x_um, y_um, layer_display)
                for (seq, display_node_id, x_um, y_um, layer_display), is_target
                in zip(batch, accepted, strict=True)
                if is_target
            )
            batch.clear()
            report_batch_progress("Resolving target-node batches")
        target_batch_total = 0
        for _seq, batch_net, batch_layer, display_node_id, x_um, y_um, layer_display in sorted(all_decisions):
            register_target(
                batch_net.casefold(),
                batch_layer.casefold(),
                display_node_id,
                x_um,
                y_um,
                _seq,
                layer_display=layer_display,
            )

    def index_for(net_key: str, node_key: str) -> int:
        nonlocal components
        by_node = node_index_by_net[net_key]
        existing = by_node.get(node_key)
        if existing is not None:
            return existing
        index = len(parents)
        by_node[node_key] = index
        node_net_key_by_index.append(net_key)
        node_id_by_index.append(node_key)
        parents.append(index)
        ranks.append(0)
        surface_parents.append(index)
        surface_ranks.append(0)
        equivalence_parents.append(index)
        equivalence_ranks.append(0)
        node_layer_codes.append(0)
        surface_code_by_index.append(0)
        components += 1
        return index

    def layer_code(layer_key: str, display: str | None = None) -> int:
        code = layer_code_by_key.get(layer_key)
        if code is None:
            code = len(layer_code_by_key) + 1
            layer_code_by_key[layer_key] = code
            layer_key_by_code[code] = layer_key
            layer_display_by_code.append(display or layer_key)
        elif display and layer_display_by_code[code - 1] == layer_key:
            layer_display_by_code[code - 1] = display
        return code

    def node_id_for(net_key: str, index: int) -> str:
        if 0 <= index < len(node_id_by_index) and node_net_key_by_index[index] == net_key:
            return node_id_by_index[index]
        return ""

    def node_layer_at(net_key: str, index: int | None) -> str:
        if (
            index is None
            or not 0 <= index < len(node_layer_codes)
            or node_net_key_by_index[index] != net_key
        ):
            return "UNKNOWN"
        code = int(node_layer_codes[index])
        if code <= 0:
            return "UNKNOWN"
        return layer_display_by_code[code - 1]

    late_node_indices: dict[tuple[str, str], int] = {}

    def node_layer_for(net_key: str, node_key: str) -> str:
        return node_layer_at(
            net_key, late_node_indices.get((net_key, node_key.casefold()))
        )

    def find(index: int) -> int:
        root = index
        while parents[root] != root:
            root = parents[root]
        while parents[index] != index:
            parent = parents[index]
            parents[index] = root
            index = parent
        return root

    def union_indices(first_index: int, second_index: int) -> None:
        nonlocal components
        first_root = find(first_index)
        second_root = find(second_index)
        if first_root == second_root:
            return
        if ranks[first_root] < ranks[second_root]:
            first_root, second_root = second_root, first_root
        parents[second_root] = first_root
        if ranks[first_root] == ranks[second_root]:
            ranks[first_root] += 1
        components -= 1

    def _dense_find(parent: array, index: int) -> int:
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != index:
            previous = parent[index]
            parent[index] = root
            index = previous
        return root

    def surface_union_indices(first_index: int, second_index: int) -> None:
        first_root = _dense_find(surface_parents, first_index)
        second_root = _dense_find(surface_parents, second_index)
        if first_root == second_root:
            return
        if surface_ranks[first_root] < surface_ranks[second_root]:
            first_root, second_root = second_root, first_root
        surface_parents[second_root] = first_root
        if surface_ranks[first_root] == surface_ranks[second_root]:
            surface_ranks[first_root] += 1

    def equivalence_union_indices(first_index: int, second_index: int) -> None:
        if (
            (node_net_key_by_index[first_index], node_id_by_index[first_index])
            in isolated_node_keys
            or (node_net_key_by_index[second_index], node_id_by_index[second_index])
            in isolated_node_keys
        ):
            return
        first_root = _dense_find(equivalence_parents, first_index)
        second_root = _dense_find(equivalence_parents, second_index)
        if first_root == second_root:
            return
        if equivalence_ranks[first_root] < equivalence_ranks[second_root]:
            first_root, second_root = second_root, first_root
        equivalence_parents[second_root] = first_root
        if equivalence_ranks[first_root] == equivalence_ranks[second_root]:
            equivalence_ranks[first_root] += 1

    def union(net_key: str, first: str, second: str) -> None:
        union_indices(index_for(net_key, first), index_for(net_key, second))

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

    def register_target(
        net_key: str,
        layer_key: str,
        node_id: str,
        x_um: float,
        y_um: float,
        source_offset: int,
        *,
        layer_display: str,
    ) -> None:
        """Apply target mask/coordinates with source-order last-write parity."""

        node_key = node_id.casefold()
        target_records = target_records_by_net[net_key]
        previous = target_records.get(node_key)
        target_mask = (previous[0] if previous is not None else 0) | target_bit_by_key[
            (net_key, layer_key)
        ]
        if previous is None or source_offset >= previous[5]:
            target_records[node_key] = (
                target_mask,
                node_id,
                float(x_um),
                float(y_um),
                layer_key,
                source_offset,
            )
        elif target_mask != previous[0]:
            target_records[node_key] = (target_mask, *previous[1:])

    def record_surface_node(
        net: str,
        layer: str,
        node_id: str,
        x_um: float,
        y_um: float,
        surface_id_override: str | None | object = _BATCH_UNSET,
    ) -> None:
        if target_node_surface_resolver is None and surface_id_override is _BATCH_UNSET:
            return
        net_key = net.casefold()
        layer_key = layer.casefold()
        if surface_id_override is _BATCH_UNSET:
            try:
                surface_id = target_node_surface_resolver(
                    net, layer, node_id, float(x_um), float(y_um)
                )
            except Exception as exc:
                raise SpdImportError("surface island resolver failed closed") from exc
        else:
            surface_id = surface_id_override
        if surface_id is None:
            return
        if (net_key, node_id.casefold()) in isolated_node_keys:
            return
        token = str(surface_id).strip()
        if token not in surface_inventory.get((net_key, layer_key), ()):
            raise SpdImportError("surface island resolver returned an unknown island identity")
        node_index = index_for(net_key, node_id.casefold())
        resolved_layer_code = layer_code(layer_key, layer)
        if node_layer_codes[node_index] not in (0, resolved_layer_code):
            raise SpdImportError(
                "one source Node resolved to a surface on a conflicting conductor layer"
            )
        # Artwork-only Nodes are first indexed here, after the caller's dense
        # layer lookup.  Retain their exact layer for the finite quotient.
        node_layer_codes[node_index] = resolved_layer_code
        surface_key = (net_key, layer_key, token)
        code = surface_code_by_key.get(surface_key)
        if code is None:
            code = len(surface_key_by_code) + 1
            surface_code_by_key[surface_key] = code
            surface_key_by_code.append(surface_key)
            surface_code_representative.append(node_index)
            surface_code_count.append(0)
        existing_code = surface_code_by_index[node_index]
        if existing_code and existing_code != code:
            raise SpdImportError("one source Node resolved to multiple exact artwork islands")
        if existing_code == 0:
            surface_code_count[code - 1] += 1
            representative = surface_code_representative[code - 1]
            if representative != node_index:
                surface_union_indices(node_index, representative)
                equivalence_union_indices(node_index, representative)
        surface_code_by_index[node_index] = code

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
                        "components": 0, "node_release_index_passes": 0,
                        "deferred_artwork_passes": 0, "deferred_artwork_keys": 0,
                        "max_live_artwork_shapes": 0,
                        "target_nodes_considered": 0, "target_nodes_filtered": 0,
                        "conditional_target_component_passes": 0,
                        "conditional_target_components_scanned": 0,
                        "conditional_target_components_recovered": 0,
                    }
                ))
            node_end = trace_start if trace_start > node_start else via_start
            trace_end = via_start if via_start > trace_start else pad_start
            via_end = pad_start if pad_start > via_start else len(data)
            if include_traces and trace_start >= 0 and trace_end > trace_start:
                reporter.report(5, "Indexing same-NET GND Trace connectivity")
                for index, match in enumerate(_TRACE_RE.finditer(data, trace_start, trace_end)):
                    if index % 8192 == 0:
                        reporter.check()
                    trace_net = _decode(match.group(2))
                    net_key = trace_net.casefold()
                    if net_key not in node_index_by_net:
                        continue
                    first = _decode(match.group(3)).casefold()
                    second = _decode(match.group(4)).casefold()
                    union(net_key, first, second)
                    first_index = index_for(net_key, first)
                    second_index = index_for(net_key, second)
                    surface_union_indices(first_index, second_index)
                    trace_net_code = surface_trace_net_code_by_key.setdefault(
                        net_key, len(surface_trace_net_by_code)
                    )
                    if trace_net_code == len(surface_trace_net_by_code):
                        surface_trace_net_by_code.append(net_key)
                    surface_trace_net_codes.append(trace_net_code)
                    surface_trace_first_indices.append(first_index)
                    surface_trace_second_indices.append(second_index)
                    graph_edges += 1
                    trace_edges += 1
            reporter.report(10, "Indexing same-NET GND Via connectivity")
            for index, match in enumerate(_VIA_RE.finditer(data, via_start, via_end)):
                if index % 8192 == 0:
                    reporter.check()
                net_key = _decode(match.group(2)).casefold()
                if net_key not in node_index_by_net:
                    continue
                first = _decode(match.group(3)).casefold()
                second = _decode(match.group(4)).casefold()
                via_key_display = _decode(match.group(1))
                via_key = via_key_display.casefold()
                if (net_key, via_key) in via_seen_keys:
                    raise ValueError("duplicate physical Via ID in SPD source")
                via_seen_keys.add((net_key, via_key))
                padstack_name = _decode(match.group(5))
                padstack_code = via_padstack_code_by_key.setdefault(
                    padstack_name.casefold(), len(via_source_padstack_names)
                )
                if padstack_code == len(via_source_padstack_names):
                    via_source_padstack_names.append(padstack_name)
                net_code = via_source_net_code_by_key.setdefault(
                    net_key, len(via_source_net_key_by_code)
                )
                if net_code == len(via_source_net_key_by_code):
                    via_source_net_key_by_code.append(net_key)
                via_source_net_codes.append(net_code)
                via_source_first_indices.append(index_for(net_key, first))
                via_source_second_indices.append(index_for(net_key, second))
                via_source_padstack_codes.append(padstack_code)
                if via_key in terminal_contact_via_ids:
                    via_terminal_display_endpoints[(net_key, via_key)] = (
                        _decode(match.group(3)), _decode(match.group(4))
                    )
                    via_terminal_padstack_by_key[(net_key, via_key)] = padstack_name
                union(net_key, first, second)
                first_index = index_for(net_key, first)
                second_index = index_for(net_key, second)
                via_source_offsets.append(int(match.start()))
                surface_union_indices(first_index, second_index)
                graph_edges += 1
                via_edges += 1
            # Duplicate detection is complete; do not retain every Via identity
            # through the expensive Node/surface phases.
            via_seen_keys.clear()
            terminal_contact_via_ids.clear()
            # For nets without same-layer artwork, a target can only affect a
            # requested result when its Trace/Via DSU component contains one
            # of that net's requested landing endpoints.  Seed these roots
            # before the Node pass so unrelated target Nodes never enter the
            # target contact/provenance dictionaries. Artwork-bearing nets
            # intentionally skip this filter because artwork may join roots
            # while Nodes are scanned.
            requested_roots_by_net: dict[str, set[int]] = {
                net_key: set() for net_key in target_layers
            }
            for (_via_key, node_key, _target_layer), net_key in requested_by_key.items():
                requested_roots_by_net.setdefault(net_key, set()).add(
                    find(index_for(net_key, node_key))
                )
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
                if net_key not in graph_layers:
                    continue
                layer_raw = _attribute(raw, b"Layer")
                if layer_raw is None:
                    continue
                layer_key = _decode(layer_raw).casefold()
                layer_display = layer_display_by_key.get((net_key, layer_key))
                if layer_display is None:
                    layer_display = _decode(layer_raw)
                    layer_display_by_key[(net_key, layer_key)] = layer_display
                node_key = node_id.casefold()
                dense_node_index = node_index_by_net[net_key].get(node_key)
                if dense_node_index is not None:
                    observed_code = layer_code(layer_key, layer_display)
                    if node_layer_codes[dense_node_index] in (0, observed_code):
                        node_layer_codes[dense_node_index] = observed_code
                if (
                    layer_key in artwork_layers.get(net_key, ())
                    and (
                        same_layer_artwork_components_batch is not None
                        or same_layer_artwork_component is not None
                    )
                ):
                    deferred_key = (net_key, layer_key)
                    offsets = deferred_artwork_offsets.get(deferred_key)
                    if offsets is None:
                        offsets = array("Q")
                        deferred_artwork_offsets[deferred_key] = offsets
                        deferred_artwork_key_order.append(deferred_key)
                    offsets.append(int(_offset))
                    continue
                attributes = _NODE_ATTR_RE.search(raw)
                if attributes is None:
                    continue
                try:
                    x_um = _length_um(attributes.group(1))
                    y_um = _length_um(attributes.group(2))
                except ValueError:
                    continue
                if layer_key in target_layers.get(net_key, ()) or layer_key in artwork_layers.get(net_key, ()):
                    record_surface_node(net, layer_display, node_id, x_um, y_um)
                # An omitted artwork-layer map means the caller may still be
                # supplying an artwork callback (legacy/tests); fail safe and
                # retain all target Nodes in that case.  Production provides
                # an explicit ground artwork map, so no-artwork nets can use
                # the requested-root filter; any deferred Trace seam on such
                # a net is recovered in the bounded conditional Node pass.
                if (
                    layer_key in target_layers.get(net_key, ())
                    and (
                        bool(artwork_layers)
                        or (
                            same_layer_artwork_component is None
                            and same_layer_artwork_components_batch is None
                        )
                    )
                    and (
                        same_layer_artwork_component is not None
                        or same_layer_artwork_components_batch is None
                    )
                    and (
                        target_trace_contact_predicate is None
                        or same_layer_artwork_component is not None
                    )
                    and not artwork_layers.get(net_key)
                ):
                    target_nodes_considered += 1
                    requested_roots = requested_roots_by_net.get(net_key, set())
                    if (
                        dense_node_index is None
                        or find(dense_node_index) not in requested_roots
                    ):
                        target_nodes_filtered += 1
                        filtered_target_nets.add(net_key)
                        continue
                if (
                    layer_key in target_layers.get(net_key, ())
                    or layer_key in artwork_layers.get(net_key, ())
                ):
                    if (
                        (
                            same_layer_artwork_component is not None
                            or same_layer_artwork_components_batch is not None
                        )
                        and layer_key in artwork_layers.get(net_key, ())
                    ):
                        if same_layer_artwork_components_batch is not None:
                            artwork_batch_total += 1
                            batch_key = (net, layer_display)
                            artwork_batch_points.setdefault(batch_key, []).append(
                                (node_key, dense_node_index, float(x_um), float(y_um))
                            )
                            if artwork_batch_total >= 32768:
                                reporter.check()
                                flush_artwork_batches()
                            component = None
                        else:
                            component = same_layer_artwork_component(
                                net, layer_display, x_um, y_um
                            )
                        if component is not None:
                            artwork_nodes += 1
                            component_key = (net_key, layer_key, str(component))
                            if component_key not in artwork_component_members_registered:
                                artwork_components.add(component_key)
                                artwork_component_members_registered.add(component_key)
                            if dense_node_index is None:
                                dense_node_index = index_for(net_key, node_key)
                            representative = artwork_representatives.setdefault(
                                component_key, dense_node_index
                            )
                            if representative != dense_node_index:
                                artwork_edges += 1
                                union_indices(dense_node_index, representative)
                                surface_union_indices(dense_node_index, representative)
                                equivalence_union_indices(dense_node_index, representative)
                    if layer_key not in target_layers.get(net_key, ()):
                        continue
                    node_predicate = target_node_predicate
                    if target_node_predicates_by_key is not None:
                        node_predicate = target_node_predicates_by_key.get(
                            (net_key, layer_key), node_predicate
                        )
                    strict_target_predicate = target_node_predicates_by_key is not None and (
                        net_key, layer_key
                    ) in target_node_predicates_by_key
                    if target_node_predicate_batch is not None and not strict_target_predicate:
                        target_batch_total += 1
                        target_batch_points.setdefault((net, layer_display), []).append(
                            (int(_offset), node_id, float(x_um), float(y_um), layer_display)
                        )
                        if target_batch_total >= 32768:
                            reporter.check()
                            # Flush same-layer results first so target nodes
                            # consume the shared artwork cache in this source
                            # window; otherwise a later artwork flush could
                            # repopulate keys that the target flush already
                            # consumed.
                            flush_artwork_batches()
                            flush_target_batches()
                        continue
                    if node_predicate is not None:
                        if not node_predicate(
                            net, layer_display, node_id, x_um, y_um
                        ):
                            continue
                    register_target(
                        net_key,
                        layer_key,
                        node_id,
                        x_um,
                        y_um,
                        int(_offset),
                        layer_display=layer_display,
                    )
            if deferred_artwork_offsets:
                deferred_artwork_passes = 1
                reporter.report(16, "Resolving deferred artwork layers")
                live_artwork_shapes = 0

                def process_deferred_artwork_batch(
                    batch_key: tuple[str, str],
                    records: list[tuple[int, str, str, str, float, float, str]],
                ) -> None:
                    nonlocal artwork_nodes, artwork_edges, live_artwork_shapes
                    if not records:
                        return
                    net_key, layer_key = batch_key
                    # The folded key is only for queueing/release.  Preserve
                    # each Node's original net/layer spelling when invoking
                    # callbacks, matching the source-order main pass.
                    display_groups: dict[tuple[str, str], list[tuple[int, str, str, str, float, float, str]]] = {}
                    display_order: list[tuple[str, str]] = []
                    for record in records:
                        display_key = (record[2], record[3])
                        group = display_groups.get(display_key)
                        if group is None:
                            group = []
                            display_groups[display_key] = group
                            display_order.append(display_key)
                        group.append(record)
                    if len(display_order) > 1:
                        for display_key in display_order:
                            process_deferred_artwork_batch(batch_key, display_groups[display_key])
                        return
                    display_net = records[0][2]
                    display_layer = records[0][3]
                    points = tuple((record[4], record[5]) for record in records)
                    if same_layer_artwork_components_batch is not None:
                        components_batch = same_layer_artwork_components_batch(
                            display_net,
                            display_layer,
                            points,
                        )
                    elif same_layer_artwork_component is not None:
                        components_batch = tuple(
                            same_layer_artwork_component(
                                display_net,
                                display_layer,
                                record[4],
                                record[5],
                            )
                            for record in records
                        )
                    else:
                        components_batch = (None,) * len(records)
                    if len(components_batch) != len(records):
                        raise SpdImportError("deferred artwork resolver returned an invalid result length")
                    surface_ids_batch: Sequence[str | None] | object = _BATCH_UNSET
                    if target_node_surface_resolver_batch is not None:
                        try:
                            surface_ids_batch = target_node_surface_resolver_batch(
                                display_net,
                                display_layer,
                                tuple(record[1] for record in records),
                                tuple((record[4], record[5]) for record in records),
                            )
                        except Exception as exc:
                            raise SpdImportError("surface island batch resolver failed closed") from exc
                        if surface_ids_batch is None:
                            surface_ids_batch = (None,) * len(records)
                        if len(surface_ids_batch) != len(records):
                            raise SpdImportError("surface island batch resolver returned an invalid result length")
                    for record_index, (record, component) in enumerate(zip(records, components_batch, strict=True)):
                        record_surface_node(
                            record[2], record[3], record[1], record[4], record[5],
                            _BATCH_UNSET
                            if surface_ids_batch is _BATCH_UNSET
                            else surface_ids_batch[record_index],
                        )
                        if component is None:
                            continue
                        artwork_nodes += 1
                        component_key = (net_key, layer_key, str(component))
                        artwork_components.add(component_key)
                        node_key = record[6]
                        node_index = node_index_by_net[net_key].get(node_key)
                        if node_index is None:
                            node_index = index_for(net_key, node_key)
                        representative = artwork_representatives.setdefault(
                            component_key,
                            node_index,
                        )
                        artwork_component_members_registered.add(component_key)
                        if representative != node_index:
                            artwork_edges += 1
                            union_indices(node_index, representative)
                            surface_union_indices(node_index, representative)
                            equivalence_union_indices(node_index, representative)

                    target_records = [
                        record
                        for record in records
                        if layer_key in target_layers.get(net_key, ())
                    ]
                    if not target_records:
                        report_batch_progress("Resolving deferred artwork batches")
                        return
                    strict = (
                        target_node_predicates_by_key.get((net_key, layer_key))
                        if target_node_predicates_by_key is not None
                        else None
                    )
                    if strict is not None:
                        accepted = tuple(
                            strict(
                                record[2],
                                record[3],
                                record[1],
                                record[4],
                                record[5],
                            )
                            for record in target_records
                        )
                    elif target_node_predicate_batch is not None:
                        accepted = target_node_predicate_batch(
                            display_net,
                            display_layer,
                            tuple((record[4], record[5]) for record in target_records),
                        )
                        if accepted is None:
                            accepted = (False,) * len(target_records)
                    elif target_node_predicate is not None:
                        accepted = tuple(
                            target_node_predicate(
                                record[2],
                                record[3],
                                record[1],
                                record[4],
                                record[5],
                            )
                            for record in target_records
                        )
                    else:
                        accepted = (True,) * len(target_records)
                    if len(accepted) != len(target_records):
                        raise SpdImportError("deferred target resolver returned an invalid result length")
                    for record, is_target in zip(target_records, accepted, strict=True):
                        if not is_target:
                            continue
                        register_target(
                            net_key,
                            layer_key,
                            record[1],
                            record[4],
                            record[5],
                            record[0],
                            layer_display=record[3],
                        )
                    report_batch_progress("Resolving deferred artwork batches")

                for deferred_key in deferred_artwork_key_order:
                    offsets = deferred_artwork_offsets[deferred_key]
                    deferred_artwork_keys += 1
                    live_artwork_shapes += 1
                    max_live_artwork_shapes = max(max_live_artwork_shapes, live_artwork_shapes)
                    records: list[tuple[int, str, str, str, float, float, str]] = []
                    try:
                        for offset in offsets:
                            line_end = _line_end(data, int(offset), node_end)
                            raw = data[int(offset):line_end]
                            if raw.endswith(b"\r"):
                                raw = raw[:-1]
                            identity = node_identity(raw)
                            if identity is None:
                                continue
                            node_id, node_net = identity
                            node_net_key = node_net.casefold()
                            if (node_net_key, deferred_key[1]) != deferred_key:
                                continue
                            layer_raw = _attribute(raw, b"Layer")
                            attributes = _NODE_ATTR_RE.search(raw)
                            if layer_raw is None or attributes is None:
                                continue
                            layer_display = _decode(layer_raw)
                            layer_display_by_key.setdefault(deferred_key, layer_display)
                            try:
                                x_um = _length_um(attributes.group(1))
                                y_um = _length_um(attributes.group(2))
                            except ValueError:
                                continue
                            records.append(
                                (
                                    int(offset),
                                    node_id,
                                    node_net,
                                    layer_display,
                                    float(x_um),
                                    float(y_um),
                                    node_id.casefold(),
                                )
                            )
                            if len(records) >= 1024:
                                reporter.check()
                                process_deferred_artwork_batch(deferred_key, records)
                                records.clear()
                        process_deferred_artwork_batch(deferred_key, records)
                    finally:
                        if same_layer_artwork_release is not None:
                            same_layer_artwork_release(*deferred_key)
                        del offsets[:]
                        live_artwork_shapes -= 1
            flush_artwork_batches()
            flush_target_batches()
            # The first Trace pass above is deliberately union-only.  Resolve
            # the handful of components that still lack a real strict target
            # before considering any finite Trace-to-artwork seam.  This keeps
            # the expensive geometry callback out of the common case and out
            # of unrelated boundary traces.
            pre_contact_masks: dict[int, int] = {}
            for net_key, targets in target_records_by_net.items():
                for node_key, target_record in targets.items():
                    target_mask = target_record[0]
                    root = find(index_for(net_key, node_key))
                    pre_contact_masks[root] = (
                        pre_contact_masks.get(root, 0) | target_mask
                    )
            unresolved_root_bits: set[tuple[str, int, int]] = set()
            if target_trace_contact_predicate is not None:
                for via, node, target_layer in requested:
                    net_key = requested_by_key[(via, node, target_layer)]
                    target_bit = target_bit_by_key[(net_key, target_layer)]
                    root = find(index_for(net_key, node))
                    if not (pre_contact_masks.get(root, 0) & target_bit):
                        unresolved_root_bits.add((net_key, root, target_bit))

            if (
                include_traces
                and target_trace_contact_predicate is not None
                and unresolved_root_bits
                and trace_start >= 0
                and trace_end > trace_start
            ):
                # Revisit only the Node endpoints belonging to unresolved
                # components.  Boundary/outside endpoints rejected by the
                # strict target predicate remain available for finite-width
                # Trace seam checks, while unrelated target nodes do not stay
                # resident for the whole import.
                conditional_node_position_passes = 1
                reporter.report(72, "Indexing unresolved Trace endpoint coordinates")
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
                    target_bit = target_bit_by_key.get((net_key, layer_key))
                    if target_bit is None:
                        continue
                    node_key = node_id.casefold()
                    node_index = node_index_by_net[net_key].get(node_key)
                    if node_index is None:
                        continue
                    if (net_key, find(node_index), target_bit) not in unresolved_root_bits:
                        continue
                    attributes = _NODE_ATTR_RE.search(raw)
                    if attributes is None:
                        continue
                    try:
                        x_um = _length_um(attributes.group(1))
                        y_um = _length_um(attributes.group(2))
                    except ValueError:
                        continue
                    layer_display = layer_display_by_key.get((net_key, layer_key))
                    if layer_display is None:
                        layer_display = _decode(layer_raw)
                        layer_display_by_key[(net_key, layer_key)] = layer_display
                    node_positions_by_net[net_key][node_key] = (
                        layer_display,
                        float(x_um),
                        float(y_um),
                    )
                trace_artwork_conditional_passes = 1
                reporter.report(75, "Checking conditional Trace-to-artwork contacts")
                pending_seams: dict[
                    tuple[str, int, int, str, str], tuple[set[str], str, str]
                ] = {}
                for index, match in enumerate(_TRACE_RE.finditer(data, trace_start, trace_end)):
                    if index % 8192 == 0:
                        reporter.check()
                    trace_net = _decode(match.group(2))
                    net_key = trace_net.casefold()
                    if net_key not in target_layers:
                        continue
                    first = _decode(match.group(3)).casefold()
                    second = _decode(match.group(4)).casefold()
                    first_position = node_positions_by_net[net_key].get(first)
                    second_position = node_positions_by_net[net_key].get(second)
                    if first_position is None or second_position is None:
                        continue
                    first_layer_key = first_position[0].casefold()
                    if (
                        first_layer_key != second_position[0].casefold()
                        or first_layer_key not in target_layers[net_key]
                    ):
                        continue
                    target_bit = target_bit_by_key.get((net_key, first_layer_key))
                    if target_bit is None:
                        continue
                    root = find(index_for(net_key, first))
                    root_bit = (net_key, root, target_bit)
                    if root_bit not in unresolved_root_bits:
                        continue
                    # The graph is already fully unioned, so both endpoints
                    # should have the same root.  Keep the guard fail-closed
                    # if a malformed source violates that invariant.
                    if find(index_for(net_key, second)) != root:
                        continue
                    width_match = _TRACE_WIDTH_RE.search(
                        data,
                        match.start(),
                        _line_end(data, match.start(), trace_end),
                    )
                    try:
                        width_um = (
                            _length_um(width_match.group(1))
                            if width_match is not None
                            else 0.0
                        )
                    except ValueError:
                        width_um = 0.0
                    trace_artwork_conditional_checks += 1
                    try:
                        contact = target_trace_contact_predicate(
                            trace_net,
                            _decode(match.group(1)),
                            first,
                            second,
                            first_position[0],
                            first_position[1],
                            first_position[2],
                            second_position[0],
                            second_position[1],
                            second_position[2],
                            float(width_um),
                        )
                    except Exception:
                        contact = None
                    if contact is None or len(contact) != 2:
                        continue
                    contact_layer, component = contact
                    contact_layer_key = str(contact_layer).casefold()
                    contact_bit = target_bit_by_key.get((net_key, contact_layer_key))
                    if contact_bit is None or contact_bit != target_bit or component is None:
                        continue
                    component_key = (net_key, contact_layer_key, str(component))
                    seam_key = (
                        net_key,
                        root,
                        target_bit,
                        contact_layer_key,
                        str(component),
                    )
                    pending = pending_seams.get(seam_key)
                    if pending is not None:
                        pending[0].add(first)
                        continue
                    pending_seams[seam_key] = ({first}, trace_net, str(contact_layer))

                # A seam-capable net may have target Nodes that are not in a
                # requested DSU root: the finite Trace can contact their
                # ordered artwork component and make them witnesses.  Those
                # Nodes were intentionally omitted by the root prefilter, so
                # recover only the pending (net, layer, component) tokens in
                # one bounded source-order pass before reducing components.
                deferred_component_tokens: dict[tuple[str, str], set[str]] = {}
                for (
                    pending_net,
                    _pending_root,
                    _pending_bit,
                    pending_layer,
                    pending_component,
                ), (_nodes, _display_net, display_layer) in pending_seams.items():
                    if pending_net not in filtered_target_nets:
                        continue
                    key = (pending_net, pending_layer)
                    deferred_component_tokens.setdefault(key, set()).add(
                        pending_component
                    )
                def classify_deferred_batch(
                    batch_key: tuple[str, str, str],
                    batch: list[tuple[int, str, str, str, float, float, str, str]],
                ) -> list[tuple[int, str, str, str, float, float, str]]:
                    if not batch:
                        return []
                    batch_net, batch_layer_display, batch_layer = batch_key
                    batch_net_key = batch_net.casefold()
                    strict = (
                        target_node_predicates_by_key.get((batch_net_key, batch_layer))
                        if target_node_predicates_by_key is not None
                        else None
                    )
                    if strict is not None:
                        accepted = tuple(
                            strict(batch_net, layer_display, node_id, x_um, y_um)
                            for _seq, node_id, _net, layer_display, x_um, y_um, _layer_key, _component in batch
                        )
                    elif target_node_predicate_batch is not None:
                        accepted = target_node_predicate_batch(
                            batch_net,
                            batch_layer_display,
                            tuple((x_um, y_um) for _seq, _node_id, _net, _layer_display, x_um, y_um, _layer_key, _component in batch),
                        )
                        if accepted is None:
                            accepted = (False,) * len(batch)
                    elif target_node_predicate is not None:
                        accepted = tuple(
                            target_node_predicate(batch_net, layer_display, node_id, x_um, y_um)
                            for _seq, node_id, _net, layer_display, x_um, y_um, _layer_key, _component in batch
                        )
                    else:
                        accepted = (True,) * len(batch)
                    if len(accepted) != len(batch):
                        raise SpdImportError("deferred target resolver returned an invalid result length")
                    decisions = [
                        (seq, batch_net, batch_layer, node_id, x_um, y_um, layer_display)
                        for (seq, node_id, _net, layer_display, x_um, y_um, _layer_key, _component), is_target
                        in zip(batch, accepted, strict=True)
                        if is_target
                    ]
                    return decisions

                def register_deferred_window(
                    window: list[tuple[int, str, str, str, float, float, str, str]],
                ) -> None:
                    nonlocal conditional_target_components_recovered
                    if not window:
                        return
                    grouped: dict[
                        tuple[str, str, str],
                        list[tuple[int, str, str, str, float, float, str, str]],
                    ] = {}
                    for record in window:
                        grouped.setdefault((record[2], record[3], record[6]), []).append(record)
                    decisions: list[tuple[int, str, str, str, float, float, str]] = []
                    for batch_key, batch in grouped.items():
                        decisions.extend(classify_deferred_batch(batch_key, batch))
                    for (
                        _seq,
                        batch_net,
                        batch_layer,
                        display_node_id,
                        x_um,
                        y_um,
                        _layer_display,
                    ) in sorted(decisions):
                        register_target(
                            batch_net.casefold(),
                            batch_layer.casefold(),
                            display_node_id,
                            x_um,
                            y_um,
                            _seq,
                            layer_display=_layer_display,
                        )
                    conditional_target_components_recovered += len(decisions)
                    window.clear()

                if deferred_component_tokens and same_layer_artwork_component is not None:
                    conditional_target_component_passes = 1
                    reporter.report(78, "Recovering filtered Trace-artwork target contacts")
                    deferred_window: list[
                        tuple[int, str, str, str, float, float, str, str]
                    ] = []
                    for index, (_offset, raw) in enumerate(_iter_lines(data, node_start, node_end)):
                        if index % 16384 == 0:
                            reporter.check()
                        if not raw.startswith(b"Node"):
                            continue
                        identity = node_identity(raw)
                        if identity is None:
                            continue
                        node_id, node_net = identity
                        node_net_key = node_net.casefold()
                        if node_net_key not in filtered_target_nets:
                            continue
                        layer_raw = _attribute(raw, b"Layer")
                        attributes = _NODE_ATTR_RE.search(raw)
                        if layer_raw is None or attributes is None:
                            continue
                        layer_key = _decode(layer_raw).casefold()
                        candidate_key = (node_net_key, layer_key)
                        tokens = deferred_component_tokens.get(candidate_key)
                        if not tokens or layer_key not in target_layers.get(node_net_key, ()):
                            continue
                        try:
                            x_um = _length_um(attributes.group(1))
                            y_um = _length_um(attributes.group(2))
                        except ValueError:
                            continue
                        layer_display = layer_display_by_key.get(candidate_key)
                        if layer_display is None:
                            layer_display = _decode(layer_raw)
                            layer_display_by_key[candidate_key] = layer_display
                        conditional_target_components_scanned += 1
                        component = same_layer_artwork_component(
                            node_net,
                            layer_display,
                            x_um,
                            y_um,
                        )
                        if component is None or str(component) not in tokens:
                            continue
                        deferred_window.append(
                            (
                                int(_offset),
                                node_id,
                                node_net,
                                layer_display,
                                float(x_um),
                                float(y_um),
                                layer_key,
                                str(component),
                            )
                        )
                        if len(deferred_window) >= 32768:
                            reporter.check()
                            register_deferred_window(deferred_window)
                    register_deferred_window(deferred_window)

                # Keep all exact components contacted by an immutable original
                # root/target bit.  Applying unions only after the scan avoids
                # DSU-root mutation changing which pending traces are visited.
                component_members_cache: dict[tuple[str, str, str], tuple[str, ...]] = {}
                for (
                    net_key,
                    original_root,
                    target_bit,
                    contact_layer_key,
                    component_token,
                ), (first_nodes, display_net, display_layer) in sorted(pending_seams.items()):
                    component_key = (net_key, contact_layer_key, component_token)
                    members = component_members_cache.get(component_key)
                    if members is None:
                        members_list: list[str] = []
                        if same_layer_artwork_component is not None:
                            for candidate_node, candidate_record in target_records_by_net[net_key].items():
                                if candidate_record[4] != contact_layer_key:
                                    continue
                                candidate_component = same_layer_artwork_component(
                                    display_net,
                                    display_layer,
                                    candidate_record[2],
                                    candidate_record[3],
                                )
                                if (
                                    candidate_component is not None
                                    and str(candidate_component) == component_token
                                ):
                                    members_list.append(candidate_node)
                        members = tuple(sorted(set(members_list)))
                        component_members_cache[component_key] = members
                    if not members:
                        continue
                    representative = members[0]
                    representative_index = index_for(net_key, representative)
                    artwork_representatives[component_key] = representative_index
                    if component_key not in artwork_component_members_registered:
                        artwork_component_members_registered.add(component_key)
                        artwork_nodes += len(members)
                        artwork_edges += max(0, len(members) - 1)
                        artwork_components.add(component_key)
                    for member in members[1:]:
                        union_indices(index_for(net_key, member), representative_index)
                    for first in sorted(first_nodes):
                        union_indices(index_for(net_key, first), representative_index)
                    trace_artwork_conditional_successes += 1
                    artwork_trace_contacts += 1
    except OSError as exc:
        raise SpdImportError(
            f"cannot recover mixed-reference GND graph from {source_path}: {exc}"
        ) from exc

    reporter.report(85, "Reducing mixed-reference GND graph components")
    component_target_masks: dict[int, int] = {}
    component_contacts: dict[tuple[str, int], list[tuple[str, float, float, int]]] = {}
    required_target_masks: dict[tuple[str, int], int] = {}
    for via, node, target_layer in requested:
        net_key = requested_by_key[(via, node, target_layer)]
        root = find(index_for(net_key, node))
        key = (net_key, root)
        required_target_masks[key] = (
            required_target_masks.get(key, 0)
            | target_bit_by_key[(net_key, target_layer)]
        )
    component_contact_records_retained = 0
    component_contact_records_filtered = 0
    for net_key, targets in target_records_by_net.items():
        for node_key, target_record in targets.items():
            target_mask = target_record[0]
            root = find(index_for(net_key, node_key))
            retained_mask = target_mask & required_target_masks.get(
                (net_key, root), 0
            )
            if not retained_mask:
                component_contact_records_filtered += 1
                continue
            component_target_masks[root] = (
                component_target_masks.get(root, 0) | retained_mask
            )
            component_contacts.setdefault((net_key, root), []).append(
                (
                    target_record[1],
                    target_record[2],
                    target_record[3],
                    retained_mask,
                )
            )
            component_contact_records_retained += 1
    # Component contacts are the only remaining consumers of these large
    # source-node maps.  Release them before building the surface certificate.
    target_records_by_net.clear()
    node_positions_by_net.clear()
    requested_roots_by_net.clear()
    filtered_target_nets.clear()
    deferred_artwork_offsets.clear()
    deferred_artwork_key_order.clear()
    artwork_batch_points.clear()
    target_batch_points.clear()
    artwork_representatives.clear()
    artwork_component_members_registered.clear()
    surface_code_by_key.clear()
    surface_code_representative.clear()
    del surface_code_count[:]
    reachable: set[tuple[str, str, str]] = set()
    target_contacts_by_key: dict[tuple[str, str, str], tuple[tuple[str, float, float], ...]] = {}
    target_contact_count_by_key: dict[tuple[str, str, str], int] = {}
    target_contact_hash_by_key: dict[tuple[str, str, str], str] = {}
    requests_by_component: dict[
        tuple[str, int], dict[int, list[tuple[str, str, str]]]
    ] = {}

    def legacy_selected_contact(
        contacts: Iterable[tuple[str, float, float]],
        source_xy: tuple[float, float],
    ) -> tuple[str, float, float]:
        return min(
            contacts,
            key=lambda item: (
                (item[1] - source_xy[0]) ** 2
                + (item[2] - source_xy[1]) ** 2,
                item[0].casefold(),
                item[0],
                item[1],
                item[2],
            ),
        )

    for request in requested:
        via, node, target_layer = request
        net_key = requested_by_key[(via, node, target_layer)]
        root = find(index_for(net_key, node))
        target_bit = target_bit_by_key[(net_key, target_layer)]
        if component_target_masks.get(root, 0) & target_bit:
            requests_by_component.setdefault((net_key, root), {}).setdefault(
                target_bit, []
            ).append(request)
    resolved_contacts_by_key: dict[
        tuple[str, str, str], tuple[tuple[str, float, float], int, str]
    ] = {}
    while requests_by_component:
        (net_key, root), requests_by_target_bit = requests_by_component.popitem()
        raw_component_contacts = component_contacts.pop((net_key, root), ())
        while requests_by_target_bit:
            target_bit, grouped_requests = requests_by_target_bit.popitem()
            ordered_contacts = sorted(
                (
                    (node_id, float(x_um), float(y_um))
                    for node_id, x_um, y_um, mask in raw_component_contacts
                    if mask & target_bit
                ),
                key=lambda item: (item[0].casefold(), item[0], item[1], item[2]),
            )
            contact_digest = hashlib.sha256()
            contact_digest.update(b"(")
            for contact_index, contact_item in enumerate(ordered_contacts):
                if contact_index:
                    contact_digest.update(b", ")
                contact_digest.update(repr(contact_item).encode("utf-8"))
            if len(ordered_contacts) == 1:
                contact_digest.update(b",")
            contact_digest.update(b")")
            contact_count = len(ordered_contacts)
            contact_hash = contact_digest.hexdigest()
            nearest_tree: cKDTree | None = None
            if (
                len(ordered_contacts) >= _NEAREST_CONTACT_TREE_THRESHOLD
                and all(
                    isfinite(item[1]) and isfinite(item[2])
                    for item in ordered_contacts
                )
            ):
                coordinate_buffer = array(
                    "d",
                    (
                        coordinate
                        for item in ordered_contacts
                        for coordinate in item[1:]
                    ),
                )
                coordinate_view = memoryview(coordinate_buffer).cast("B").cast(
                    "d", shape=[len(ordered_contacts), 2]
                )
                nearest_tree = cKDTree(coordinate_view)
                del coordinate_view, coordinate_buffer
            for via, node, target_layer in grouped_requests:
                key = (via, node, target_layer)
                source_xy = requested_coordinates.get(key)
                if (
                    source_xy is None
                    or not all(isfinite(float(value)) for value in source_xy)
                    or not ordered_contacts
                ):
                    # A graph component without a finite source origin cannot be
                    # reduced to a deterministic target contact.
                    continue
                if nearest_tree is None:
                    selected_contact = legacy_selected_contact(
                        ordered_contacts, source_xy
                    )
                else:
                    candidate_indices = None
                    try:
                        _tree_distance, nearest_index = nearest_tree.query(
                            (source_xy[0], source_xy[1]), k=1, workers=1
                        )
                        nearest_index = int(nearest_index)
                        if not 0 <= nearest_index < len(ordered_contacts):
                            raise ValueError("nearest contact index is outside the tree")
                        nearest_candidate = ordered_contacts[nearest_index]
                        nearest_d2 = (
                            (nearest_candidate[1] - source_xy[0]) ** 2
                            + (nearest_candidate[2] - source_xy[1]) ** 2
                        )
                        d2_for_radius = nextafter(nearest_d2, float("inf"))
                        if not isfinite(nearest_d2) or not isfinite(d2_for_radius):
                            raise ValueError("nearest contact distance is not finite")
                        radius = nextafter(sqrt(d2_for_radius), float("inf"))
                        if not isfinite(radius):
                            raise ValueError("nearest contact radius is not finite")
                        candidate_indices = nearest_tree.query_ball_point(
                            (source_xy[0], source_xy[1]), radius
                        )
                        if not candidate_indices:
                            candidate_indices = [nearest_index]
                        selected_contact = legacy_selected_contact(
                            (
                                ordered_contacts[int(index)]
                                for index in candidate_indices
                            ),
                            source_xy,
                        )
                    except (OverflowError, TypeError, ValueError):
                        selected_contact = legacy_selected_contact(
                            ordered_contacts, source_xy
                        )
                    candidate_indices = None
                resolved_contacts_by_key[key] = (
                    selected_contact,
                    contact_count,
                    contact_hash,
                )
            del ordered_contacts, nearest_tree, grouped_requests
        del raw_component_contacts
    requests_by_component.clear()
    for key in requested:
        resolved = resolved_contacts_by_key.pop(key, None)
        if resolved is None:
            continue
        selected_contact, contact_count, contact_hash = resolved
        reachable.add(key)
        target_contact_count_by_key[key] = contact_count
        target_contact_hash_by_key[key] = contact_hash
        target_contacts_by_key[key] = (selected_contact,) if selected_contact else ()
    resolved_contacts_by_key.clear()
    unreachable = set(requested) - reachable
    requested_count = len(requested)
    graph_node_count = len(parents)
    for node_index in range(graph_node_count):
        parents[node_index] = find(node_index)
    full_root_by_node = parents
    artwork_component_count = len(artwork_components)
    artwork_island_count = sum(len(ids) for ids in surface_inventory.values())
    requested.clear()
    requested_by_key.clear()
    requested_coordinates.clear()
    target_bit_by_key.clear()
    ranks.clear()
    artwork_components.clear()
    component_contacts.clear()
    component_target_masks.clear()
    required_target_masks.clear()
    reporter.report(72, "Building surface connectivity certificates")
    reporter.check()
    surface_components: set[SpdSurfaceConnectivityComponent] = set()
    surface_token_layers: dict[tuple[str, str], set[str]] = {}
    for net_key, layer_key, token in surface_key_by_code:
        surface_token_layers.setdefault((net_key, token), set()).add(layer_key)
    for (net_key, _token), layers in sorted(surface_token_layers.items()):
        if len(layers) >= 2:
            surface_components.add(
                SpdSurfaceConnectivityComponent(net_key, tuple(sorted(layers)))
            )
    surface_token_layers.clear()
    # Surface equivalence excludes Via edges: only same-island artwork and
    # same-layer Trace edges coalesce equipotential islands.
    def eq_find(net_key: str, node_index: int) -> tuple[str, int]:
        return (net_key, _dense_find(equivalence_parents, node_index))
    for _trace_index, (trace_net_code, first_index, second_index) in enumerate(
        zip(surface_trace_net_codes, surface_trace_first_indices, surface_trace_second_indices, strict=True)
    ):
        if _trace_index % 16384 == 0:
            reporter.check()
        if (
            node_layer_codes[first_index]
            and node_layer_codes[first_index] == node_layer_codes[second_index]
        ):
            equivalence_union_indices(first_index, second_index)
    trace_terminal_node_index_by_landing: dict[tuple[str, str], int] = {}
    trace_terminal_landings_by_root: dict[
        tuple[str, int], list[tuple[str, str]]
    ] = {}
    for landing_key, (net_key, _owner_kind, _terminal_id) in (
        terminal_contact_owner_by_key.items()
    ):
        if not landing_key[0].startswith("source-node:"):
            continue
        node_index = node_index_by_net.get(net_key, {}).get(landing_key[1])
        if node_index is None:
            continue
        trace_terminal_landings_by_root.setdefault(
            eq_find(net_key, node_index), []
        ).append(landing_key)
    if trace_terminal_landings_by_root:
        via_nodes_by_root: dict[tuple[str, int], set[int]] = {}
        for via_index, (first_index, second_index) in enumerate(
            zip(via_source_first_indices, via_source_second_indices, strict=True)
        ):
            if via_index % 16384 == 0:
                reporter.check()
            net_key = via_source_net_key_by_code[
                via_source_net_codes[via_index]
            ]
            for node_index in (first_index, second_index):
                root = eq_find(net_key, node_index)
                if root in trace_terminal_landings_by_root:
                    via_nodes_by_root.setdefault(root, set()).add(node_index)
        for root, landing_keys_for_root in trace_terminal_landings_by_root.items():
            candidates = via_nodes_by_root.get(root, set())
            if len(candidates) != 1:
                continue
            node_index = next(iter(candidates))
            for landing_key in landing_keys_for_root:
                trace_terminal_node_index_by_landing[landing_key] = node_index
        via_nodes_by_root.clear()
    trace_terminal_landings_by_root.clear()
    grouped_equivalence: dict[tuple[str, str, tuple[str, int]], set[str]] = {}
    for node_index, code_value in enumerate(surface_code_by_index):
        if not code_value:
            continue
        net_key, layer_key, token = surface_key_by_code[int(code_value) - 1]
        root = eq_find(net_key, node_index)
        grouped_equivalence.setdefault((net_key, layer_key, root), set()).add(token)
    equivalence_ranks.clear()
    # Preserve uncontacted inventory islands as explicit singleton partitions;
    # proofs must cover the complete declared surface universe.
    for (net_key, layer_key), island_ids in surface_inventory.items():
        observed = {
            token
            for (candidate_net, candidate_layer, _root), tokens in grouped_equivalence.items()
            if candidate_net == net_key and candidate_layer == layer_key
            for token in tokens
        }
        for index, token in enumerate(island_ids):
            if token not in observed:
                grouped_equivalence.setdefault((net_key, layer_key, ("inventory", str(index))), set()).add(token)
    for (net_key, layer_key, _root), tokens in grouped_equivalence.items():
        display_net = next(
            (str(raw_net) for raw_net in target_layers_by_net if str(raw_net).casefold() == net_key),
            net_key,
        )
        display_layer = target_layer_display.get((net_key, layer_key), layer_key)
        surface_equivalence_components.add(
            SpdSurfaceIslandEquivalenceComponent(display_net, display_layer, tuple(sorted(tokens)))
        )
    for node_index in range(len(equivalence_parents)):
        equivalence_parents[node_index] = _dense_find(
            equivalence_parents, node_index
        )
    equivalence_root_by_node = equivalence_parents
    # A single source-offset replay supplies Via surface unions, canonical pair
    # aggregates, ownership counts, and compact finite-route arrays.  The
    # initial graph pass above retains only fixed-width offsets/codes.
    owned_ids = {str(item).casefold() for item in (terminal_owned_via_ids or ())}
    raw_via_count = len(via_source_offsets)
    invalid_owned_count = 0
    invalid_outside_count = 0
    invalid_unsupported_count = 0
    invalid_owned_offsets = array("Q")
    invalid_outside_offsets = array("Q")
    invalid_unsupported_offsets = array("Q")
    invalid_owned_digest = hashlib.sha256()
    invalid_outside_digest = hashlib.sha256()
    invalid_unsupported_digest = hashlib.sha256()
    paired_owned = 0
    paired_substrate = 0
    aggregate_states: dict[tuple[str, str, str, str, str, str], dict[str, object]] = {}
    finite_requested = bool(
        terminal_contact_records
        or scenario_isolated_terminal_landings
        or retarget_destination_requests
        or terminal_owned_via_ids is not None
    )
    requested_destinations = tuple(
        retarget_destination_requests
        if retarget_destination_requests is not None
        else ()
    )
    finite_via_id_bytes = bytearray()
    finite_raw_via_ids_digest = hashlib.sha256()
    finite_owner_ledger_digest = hashlib.sha256()
    # Values transition from raw edge indexes to exact quotient edge IDs as
    # the CSR paths are emitted; the key count is bounded by terminal Vias.
    finite_edge_binding_by_terminal_via: dict[
        tuple[str, str], int | str
    ] = {}

    def surface_row_at(
        net_key: str, node_index: int | None
    ) -> tuple[str, str] | None:
        if (
            node_index is None
            or not 0 <= node_index < len(surface_code_by_index)
            or node_net_key_by_index[node_index] != net_key
        ):
            return None
        code = surface_code_by_index[node_index]
        if not code:
            return None
        surface_net, surface_layer, surface_token = surface_key_by_code[code - 1]
        if surface_net != net_key:
            return None
        return surface_layer, surface_token

    def surface_row(net_key: str, node_key: str) -> tuple[str, str] | None:
        node_index = late_node_indices.get((net_key, node_key.casefold()))
        row = surface_row_at(net_key, node_index)
        if row is None or node_index is None:
            return None
        code = surface_code_by_index[node_index]
        return row[0], surface_component_islands_by_code.get(code, (row[1],))[0]

    # grouped_equivalence is complete before this replay; cache its component
    # island lookup so each Via performs O(1) provenance work.
    component_islands_by_surface: dict[tuple[str, str, str], tuple[str, ...]] = {}
    component_count_by_surface: dict[tuple[str, str], int] = {}
    artwork_island_union_count = 0
    for (eq_net, eq_layer, _root), tokens in grouped_equivalence.items():
        members = tuple(sorted(tokens))
        component_count_by_surface[(eq_net, eq_layer)] = (
            component_count_by_surface.get((eq_net, eq_layer), 0) + 1
        )
        artwork_island_union_count += max(0, len(members) - 1)
        for token in members:
            component_islands_by_surface[(eq_net, eq_layer, token)] = members
    grouped_equivalence.clear()
    tokens = set()
    members = ()

    all_landings = landing_records + terminal_contact_records
    late_node_keys = {
        (
            str(getattr(landing, "net", "")).casefold(),
            str(getattr(landing, "endpoint_node_id", "")).casefold(),
        )
        for landing in all_landings
    }
    late_node_keys.update(
        (net_key, node_key)
        for (_via_key, node_key), (
            net_key,
            _owner_kind,
            _terminal_id,
        ) in terminal_contact_owner_by_key.items()
    )
    late_node_keys.update(
        (net_key, str(node_id).casefold())
        for (net_key, _via_key), endpoints in via_terminal_display_endpoints.items()
        for node_id in endpoints
    )
    late_node_keys.update(isolated_node_keys)
    late_node_keys.update(
        (str(net).casefold(), str(node).casefold())
        for net, _layer, node in requested_destinations
    )
    for net_key, node_key in late_node_keys:
        node_index = node_index_by_net.get(net_key, {}).get(node_key)
        if node_index is not None:
            late_node_indices[(net_key, node_key)] = node_index
    node_index_by_net.clear()
    late_node_keys.clear()
    terminal_contact_via_ids.update(
        via_key for via_key, _node_key in terminal_contact_owner_by_key
    )
    with source_path.open("rb") as replay_handle, mmap.mmap(
        replay_handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as replay_data:
        via_end = _find_line(replay_data, b"* PadStack collection description lines")
        if via_end < 0:
            via_end = len(replay_data)
        for via_index, offset in enumerate(via_source_offsets):
            if via_index % 16384 == 0:
                reporter.check()
            match = _VIA_RE.match(replay_data, int(offset), via_end)
            if match is None:
                raise SpdImportError("Via source offset replay no longer matches the SPD source")
            net_code = via_source_net_codes[via_index]
            padstack_code = via_source_padstack_codes[via_index]
            net_key = via_source_net_key_by_code[net_code]
            via_key = _decode(match.group(1)).casefold()
            first_index = via_source_first_indices[via_index]
            second_index = via_source_second_indices[via_index]
            padstack_name = via_source_padstack_names[padstack_code]
            if finite_requested and via_key in terminal_contact_via_ids:
                finite_edge_binding_by_terminal_via[(net_key, via_key)] = via_index
            surface_union_indices(first_index, second_index)
            first_surface = surface_row_at(net_key, first_index)
            second_surface = surface_row_at(net_key, second_index)
            if first_surface is None or second_surface is None or first_surface[0] == second_surface[0]:
                if via_key in owned_ids:
                    invalid_owned_count += 1
                    invalid_owned_offsets.append(int(offset))
                elif via_key in terminal_contact_via_ids:
                    invalid_outside_count += 1
                    invalid_outside_offsets.append(int(offset))
                else:
                    invalid_unsupported_count += 1
                    invalid_unsupported_offsets.append(int(offset))
            else:
                start_component = component_islands_by_surface.get(
                    (net_key, first_surface[0], first_surface[1]),
                    (first_surface[1],),
                )
                end_component = component_islands_by_surface.get(
                    (net_key, second_surface[0], second_surface[1]),
                    (second_surface[1],),
                )
                aggregate_key = (
                    net_key,
                    padstack_name.casefold(),
                    first_surface[0].casefold(),
                    second_surface[0].casefold(),
                    start_component[0],
                    end_component[0],
                )
                state = aggregate_states.get(aggregate_key)
                if state is None:
                    state = {
                        "net": net_key,
                        "padstack": padstack_name,
                        "start_layer": first_surface[0],
                        "end_layer": second_surface[0],
                        "start_island": start_component[0],
                        "end_island": end_component[0],
                        "start_component": start_component,
                        "end_component": end_component,
                        "count": 0,
                        "digest": hashlib.sha256(),
                        "owned": 0,
                        "substrate": 0,
                    }
                    aggregate_states[aggregate_key] = state
                state["count"] = int(state["count"]) + 1
                encoded = via_key.encode("utf-8")
                cast_digest = state["digest"]
                cast_digest.update(len(encoded).to_bytes(4, "big"))
                cast_digest.update(encoded)
                if via_key in owned_ids:
                    state["owned"] = int(state["owned"]) + 1
                    paired_owned += 1
                else:
                    state["substrate"] = int(state["substrate"]) + 1
                    paired_substrate += 1
            if finite_requested:
                # Replay order is already the finite source order.  Transfer
                # the compact source arrays after this loop instead of copying
                # one fixed-width row per Via.
                encoded = via_key.encode("utf-8")
                finite_via_id_bytes.extend(encoded)
                # This source offset has completed its final replay use.
                # Reuse its Q slot as the compact UTF-8 ledger end offset.
                via_source_offsets[via_index] = len(finite_via_id_bytes)
                finite_raw_via_ids_digest.update(len(encoded).to_bytes(4, "big"))
                finite_raw_via_ids_digest.update(encoded)
        def digest_invalid_offsets(offsets: array):
            def values() -> Iterator[str]:
                for offset in offsets:
                    match = _VIA_RE.match(replay_data, int(offset), via_end)
                    if match is not None:
                        yield _decode(match.group(1))

            return _canonical_casefolded_ids_digest(values(), check=reporter.check)
        invalid_owned_digest = digest_invalid_offsets(invalid_owned_offsets)
        invalid_outside_digest = digest_invalid_offsets(invalid_outside_offsets)
        invalid_unsupported_digest = digest_invalid_offsets(invalid_unsupported_offsets)
    terminal_contact_via_ids.clear()
    del invalid_owned_offsets[:]
    del invalid_outside_offsets[:]
    del invalid_unsupported_offsets[:]
    if not finite_requested:
        del via_source_offsets[:]
    finite_via_id_end_offsets = via_source_offsets
    finite_edge_count = len(finite_via_id_end_offsets)
    del via_source_net_codes[finite_edge_count:]
    del via_source_first_indices[finite_edge_count:]
    del via_source_second_indices[finite_edge_count:]
    del via_source_padstack_codes[finite_edge_count:]
    finite_net_codes = via_source_net_codes
    finite_first_indices = via_source_first_indices
    finite_second_indices = via_source_second_indices
    finite_padstack_codes = via_source_padstack_codes
    surface_ranks.clear()
    # One post-replay metadata pass turns surface codes into direct root/layer
    # lookups.  Contacts and finite vertices use these tables instead of
    # repeatedly scanning every surface group.
    surface_root_by_code: dict[int, int] = {}
    surface_codes_by_root: dict[tuple[str, int], tuple[int, ...]] = {}
    surface_component_islands_by_code: dict[int, tuple[str, ...]] = {}
    global_surface_layers: dict[tuple[str, int], set[str]] = {}
    codes_by_root_mutable: dict[tuple[str, int], list[int]] = {}
    for node_index, code_value in enumerate(surface_code_by_index):
        code = int(code_value)
        if not code or code in surface_root_by_code:
            continue
        if node_index % 16384 == 0:
            reporter.check()
        candidate_net, candidate_layer, token = surface_key_by_code[code - 1]
        root = _dense_find(surface_parents, node_index)
        surface_root_by_code[code] = root
        codes_by_root_mutable.setdefault((candidate_net, root), []).append(code)
        global_surface_layers.setdefault((candidate_net, root), set()).add(candidate_layer)
        surface_component_islands_by_code[code] = component_islands_by_surface.get(
            (candidate_net, candidate_layer, token), (token,)
        )
    component_islands_by_surface.clear()
    surface_codes_by_root = {
        key: tuple(values) for key, values in codes_by_root_mutable.items()
    }
    surface_root_by_code.clear()
    codes_by_root_mutable.clear()
    surface_summary_by_root: dict[
        tuple[str, int], tuple[int, tuple[str, ...], tuple[str, ...]]
    ] = {}
    for root_key, codes in surface_codes_by_root.items():
        candidate_net, _root = root_key
        layers = global_surface_layers[root_key]
        display_layers = tuple(
            sorted(
                {
                    target_layer_display.get((candidate_net, layer), layer)
                    for layer in layers
                },
                key=lambda item: (item.casefold(), item),
            )
        )
        surface_summary_by_root[root_key] = (
            len(layers),
            display_layers,
            tuple(sorted({surface_key_by_code[code - 1][2] for code in codes})),
        )
    surface_codes_by_root.clear()
    for root_key, layers in global_surface_layers.items():
        candidate_net, _root = root_key
        if len(layers) < 2:
            continue
        display_net = next(
            (str(raw_net) for raw_net in target_layers_by_net if str(raw_net).casefold() == candidate_net),
            candidate_net,
        )
        display_layers = surface_summary_by_root[root_key][1]
        surface_components.add(SpdSurfaceConnectivityComponent(display_net, display_layers))
    global_surface_layers.clear()
    needed_landing_keys = {
        (
            str(getattr(landing, "via_id", "")).casefold(),
            str(getattr(landing, "endpoint_node_id", "")).casefold(),
        )
        for landing in all_landings
    }
    reachable_layers_by_landing = _index_reachable_layers_by_landing(
        reachable, needed_landing_keys
    )
    needed_landing_keys.clear()
    contact_seen: set[tuple[str, str]] = set()
    for landing in all_landings:
        landing_key = (
            str(getattr(landing, "via_id", "")).casefold(),
            str(getattr(landing, "endpoint_node_id", "")).casefold(),
        )
        net_key = str(getattr(landing, "net", "")).casefold()
        landing_node_key = landing_key[1]
        landing_index = late_node_indices.get((net_key, landing_node_key))
        landing_root = (
            _dense_find(surface_parents, landing_index)
            if landing_index is not None
            else None
        )
        matched_layer_count = 0
        matched_layers: tuple[str, ...] = ()
        matched_islands: tuple[str, ...] = ()
        reachable_target_layers = reachable_layers_by_landing.get(landing_key, ())
        if len(reachable_target_layers) >= 2:
            display_layers = tuple(
                sorted(
                    {
                        target_layer_display.get((net_key, layer), layer)
                        for layer in reachable_target_layers
                    },
                    key=lambda item: (item.casefold(), item),
                )
            )
            display_net = str(getattr(landing, "net", net_key))
            surface_components.add(SpdSurfaceConnectivityComponent(display_net, display_layers))
            surface_layers_by_landing[landing_key] = display_layers
        if landing_key not in isolated_landing_keys:
            matched_layer_count, matched_layers, matched_islands = (
                surface_summary_by_root.get((net_key, landing_root), (0, (), ()))
            )
        if matched_layer_count:
            surface_layers_by_landing[landing_key] = matched_layers
            surface_islands_by_landing[landing_key] = matched_islands
            if matched_layer_count >= 2:
                display_net = str(getattr(landing, "net", net_key))
                surface_components.add(
                    SpdSurfaceConnectivityComponent(display_net, matched_layers)
                )
        if (
            landing_key in terminal_contact_owner_by_key
            and landing_key not in contact_seen
        ):
            terminal_net_key, terminal_owner_kind, _terminal_id = (
                terminal_contact_owner_by_key[landing_key]
            )
            if net_key != terminal_net_key:
                continue
            contact_seen.add(landing_key)
            via_key = str(getattr(landing, "via_id", "")).casefold()
            endpoint_node = landing_key[1]
            display_endpoints = via_terminal_display_endpoints.get((net_key, via_key), ())
            endpoints = tuple(item.casefold() for item in display_endpoints)
            internal_node_display = (
                display_endpoints[1]
                if len(display_endpoints) == 2 and endpoints[0] == endpoint_node
                else display_endpoints[0]
                if len(display_endpoints) == 2 and endpoints[1] == endpoint_node
                else None
            )
            internal_node = (
                internal_node_display.casefold()
                if internal_node_display is not None
                else None
            )
            contact_rows: dict[str, tuple[str, ...]] = {}
            if internal_node is not None:
                internal_index = late_node_indices.get(
                    (net_key, str(internal_node).casefold())
                )
                internal_code = (
                    int(surface_code_by_index[internal_index])
                    if internal_index is not None
                    else 0
                )
                if internal_code:
                    _candidate_net, candidate_layer, token = surface_key_by_code[internal_code - 1]
                    contact_rows[
                        target_layer_display.get(
                            (net_key, candidate_layer), candidate_layer
                        )
                    ] = surface_component_islands_by_code.get(
                        internal_code, (token,)
                    )
            contact_issues = (
                ()
                if contact_rows
                else (
                    (
                        "internal_endpoint_equivalence_component_missing"
                        if internal_node is not None
                        else "internal_via_endpoint_missing_or_ambiguous"
                    ),
                )
            )
            landing_surface_contacts.append(
                SpdLandingSurfaceContact(
                    via_id=str(getattr(landing, "via_id", "")),
                    endpoint_node_id=str(getattr(landing, "endpoint_node_id", "")),
                    net=str(getattr(landing, "net", net_key)),
                    contact_island_ids_by_layer=contact_rows,
                    internal_endpoint_node_id=internal_node_display,
                    terminal_owner_kind=terminal_owner_kind,
                    physical_model_issues=contact_issues,
                )
            )
    surface_dense_node_count = len(surface_parents)
    del surface_parents[:]
    surface_summary_by_root.clear()
    reachable_layers_by_landing.clear()
    contact_seen.clear()
    via_terminal_display_endpoints.clear()
    del all_landings, landing_records, terminal_contact_records

    physical_model_cache: dict[
        tuple[str, str, str],
        tuple[
            float | None,
            str | None,
            tuple[SpdViaIslandPairSegment, ...],
            str,
            tuple[str, ...],
        ],
    ] = {}

    def physical_model_for_canonical(
        padstack_name: str,
        start_layer: str,
        end_layer: str,
    ) -> tuple[float | None, str | None, tuple[SpdViaIslandPairSegment, ...], str, tuple[str, ...]]:
        cache_key = (padstack_name, start_layer, end_layer)
        cached = physical_model_cache.get(cache_key)
        if cached is not None:
            return cached
        issues: set[str] = set()
        definitions = tuple(
            item for item in (padstacks or ())
            if str(getattr(item, "name", "")).strip().casefold() == padstack_name.casefold()
        )
        padstack = definitions[0] if len(definitions) == 1 else None
        if padstacks is None:
            issues.add("physical_model_source_not_supplied")
        elif not definitions:
            issues.add("padstack_definition_missing")
        elif len(definitions) > 1:
            issues.add("padstack_definition_ambiguous")
        drill = None
        material = None
        if padstack is not None:
            try:
                drill = float(getattr(padstack, "drill_diameter_um"))
            except (TypeError, ValueError, AttributeError):
                drill = None
            if drill is None or not isfinite(drill) or drill <= 0.0:
                drill = None
                issues.add("drill_diameter_um_missing_or_invalid")
            material = str(getattr(padstack, "material", "") or "").strip() or None
        declared: list[str] = []
        seen_declared: set[str] = set()
        if padstack is not None:
            for raw_layer in getattr(padstack, "layers", ()):
                layer = str(raw_layer).strip()
                if not layer:
                    continue
                key = layer.casefold()
                if key in seen_declared:
                    issues.add("padstack_conductor_layer_duplicated")
                else:
                    seen_declared.add(key)
                    declared.append(layer)
        start_key, end_key = start_layer.casefold(), end_layer.casefold()
        if start_key not in seen_declared or end_key not in seen_declared:
            issues.add("via_endpoint_layer_not_declared_by_padstack")
        stackup = tuple(stackup_layers or ())
        centers: dict[str, float] = {}
        stack_seen: set[str] = set()
        depth = 0.0
        for layer in stackup:
            layer_name = str(getattr(layer, "name", "")).strip()
            key = layer_name.casefold()
            try:
                thickness = float(getattr(layer, "thickness_um"))
            except (TypeError, ValueError, AttributeError):
                thickness = float("nan")
            if not layer_name or key in stack_seen or not isfinite(thickness) or thickness <= 0.0:
                issues.add("stackup_layer_missing_or_ambiguous")
                continue
            stack_seen.add(key)
            centers[key] = depth + thickness / 2.0
            depth += thickness
        if start_key not in centers or end_key not in centers:
            issues.add("via_endpoint_layer_missing_from_stackup")
        if issues:
            result = drill, material, (), "incomplete", tuple(sorted(issues))
            physical_model_cache[cache_key] = result
            return result
        start_index = declared.index(next(layer for layer in declared if layer.casefold() == start_key))
        end_index = declared.index(next(layer for layer in declared if layer.casefold() == end_key))
        if start_index == end_index:
            result = drill, material, (), "incomplete", ("via_endpoint_layers_identical",)
            physical_model_cache[cache_key] = result
            return result
        step = 1 if end_index > start_index else -1
        segments = tuple(
            SpdViaIslandPairSegment(
                ordinal,
                declared[index],
                declared[index + step],
                abs(centers[declared[index + step].casefold()] - centers[declared[index].casefold()]) or 1.0,
            )
            for ordinal, index in enumerate(range(start_index, end_index, step))
        )
        result = drill, material, segments, "complete", ()
        physical_model_cache[cache_key] = result
        return result

    aggregate_key_by_contact: list[
        tuple[str, str, str, str, str, str] | None
    ] = []
    required_aggregate_keys: set[tuple[str, str, str, str, str, str]] = set()
    for contact in landing_surface_contacts:
        net_key = contact.net.casefold()
        endpoint_surface = surface_row(net_key, contact.endpoint_node_id.casefold())
        internal_surface = surface_row(
            net_key, (contact.internal_endpoint_node_id or "").casefold()
        )
        padstack_name = via_terminal_padstack_by_key.get(
            (net_key, contact.via_id.casefold())
        )
        aggregate_key = (
            (
                net_key,
                padstack_name.casefold(),
                endpoint_surface[0].casefold(),
                internal_surface[0].casefold(),
                endpoint_surface[1],
                internal_surface[1],
            )
            if padstack_name is not None
            and endpoint_surface is not None
            and internal_surface is not None
            else None
        )
        aggregate_key_by_contact.append(aggregate_key)
        if aggregate_key is not None:
            required_aggregate_keys.add(aggregate_key)
    aggregate_by_key: dict[
        tuple[str, str, str, str, str, str], SpdViaIslandPairAggregate
    ] = {}

    # Materialize one canonical row per replay accumulator.  The old path built
    # one dataclass and one Via-ID list per source edge; the replay keeps only a
    # framed digest/count/ownership counters until this bounded output stage.
    via_island_pair_aggregates = []
    while aggregate_states:
        source_aggregate_key, state = aggregate_states.popitem()
        net_key = str(state["net"])
        padstack_name = str(state["padstack"])
        start_layer = target_layer_display.get(
            (net_key, str(state["start_layer"]).casefold()), str(state["start_layer"])
        )
        end_layer = target_layer_display.get(
            (net_key, str(state["end_layer"]).casefold()), str(state["end_layer"])
        )
        drill, material, segments, physical_status, physical_issues = physical_model_for_canonical(
            padstack_name, start_layer, end_layer
        )
        start_island = str(state["start_island"])
        end_island = str(state["end_island"])
        start_component = state["start_component"]
        end_component = state["end_component"]
        aggregate = SpdViaIslandPairAggregate(
            net=next(
                (str(raw_net) for raw_net in target_layers_by_net
                 if str(raw_net).casefold() == net_key),
                net_key,
            ),
            padstack=padstack_name,
            start_layer=start_layer,
            end_layer=end_layer,
            start_island_id=start_island,
            end_island_id=end_island,
            count=int(state["count"]),
            via_ids_sha256=state["digest"].hexdigest(),
            start_component_island_ids=tuple(sorted(start_component)),
            end_component_island_ids=tuple(sorted(end_component)),
            terminal_owned_count=(int(state["owned"]) if terminal_owned_via_ids is not None else None),
            substrate_count=(int(state["substrate"]) if terminal_owned_via_ids is not None else None),
            drill_diameter_um=drill,
            material=material,
            segments=segments,
            physical_model_status=physical_status,
            physical_model_issues=physical_issues,
        )
        via_island_pair_aggregates.append(aggregate)
        if source_aggregate_key in required_aggregate_keys:
            aggregate_by_key[source_aggregate_key] = aggregate
    via_island_pair_aggregates.sort(
        key=lambda item: (
            item.net.casefold(), item.padstack.casefold(),
            item.start_layer.casefold(), item.end_layer.casefold(),
            item.start_island_id, item.end_island_id,
        )
    )
    state = {}
    # Enrich terminal contact rows from the canonical physical pair.  Surface
    # contact classification is independent from strict target acceptance, but
    # a terminal landing that has a resolved pair must carry the same physical
    # metadata as its aggregate.
    if landing_surface_contacts:
        for contact_index, (contact, aggregate_key) in enumerate(
            zip(landing_surface_contacts, aggregate_key_by_contact, strict=True)
        ):
            net_key = contact.net.casefold()
            endpoint_key = contact.endpoint_node_id.casefold()
            internal_key = (contact.internal_endpoint_node_id or "").casefold()
            aggregate = (
                aggregate_by_key.get(aggregate_key)
                if aggregate_key is not None
                else None
            )
            if aggregate is None:
                # Owned one-sided terminal Vias have no paired aggregate, but
                # their retained internal endpoint still carries complete
                # physical evidence and must be serialized as an unpaired
                # terminal contact rather than dropped.
                via_key = contact.via_id.casefold()
                endpoint_layer = node_layer_for(net_key, endpoint_key)
                internal_layer = node_layer_for(net_key, internal_key)
                padstack_name = via_terminal_padstack_by_key.get((net_key, via_key))
                drill = None
                material = None
                if padstack_name and padstacks is not None:
                    for padstack in padstacks:
                        if str(padstack.name).casefold() == padstack_name.casefold():
                            drill = padstack.drill_diameter_um
                            material = padstack.material
                            break
                segments: tuple[SpdViaIslandPairSegment, ...] = ()
                physical_status = "incomplete"
                physical_issues = contact.physical_model_issues
                is_owned_contact = contact.via_id.casefold() in owned_ids
                if is_owned_contact and contact.contact_island_ids_by_layer and endpoint_layer and internal_layer and drill is not None and stackup_layers is not None:
                    depth = 0.0
                    centres: dict[str, float] = {}
                    for stack_layer in stackup_layers:
                        thickness = float(stack_layer.thickness_um)
                        centres[str(stack_layer.name).casefold()] = depth + thickness / 2.0
                        depth += thickness
                    if endpoint_layer.casefold() in centres and internal_layer.casefold() in centres:
                        segments = (
                            SpdViaIslandPairSegment(
                                0,
                                endpoint_layer,
                                internal_layer,
                                abs(centres[internal_layer.casefold()] - centres[endpoint_layer.casefold()]) or 1.0,
                            ),
                        )
                        physical_status = "complete"
                        physical_issues = ()
                landing_surface_contacts[contact_index] = replace(
                    contact,
                    terminal_owner_kind=contact.terminal_owner_kind,
                    external_endpoint_layer=endpoint_layer,
                    padstack=padstack_name,
                    drill_diameter_um=drill,
                    material=material,
                    segments=segments,
                    physical_model_status=physical_status,
                    physical_model_issues=physical_issues,
                )
                continue
            landing_surface_contacts[contact_index] = replace(
                contact,
                terminal_owner_kind=contact.terminal_owner_kind,
                external_endpoint_layer=aggregate.start_layer,
                padstack=aggregate.padstack,
                drill_diameter_um=aggregate.drill_diameter_um,
                material=aggregate.material,
                segments=aggregate.segments,
                physical_model_status=aggregate.physical_model_status,
                physical_model_issues=aggregate.physical_model_issues,
            )
    aggregate_by_key.clear()
    required_aggregate_keys.clear()
    aggregate_key_by_contact.clear()
    via_terminal_padstack_by_key.clear()
    if raw_via_count:
        paired_count = sum(item.count for item in via_island_pair_aggregates)
        # An observed terminal-owned Via whose endpoint cannot be bound to two
        # retained surface islands is an explicit unpaired-owned record.  A
        # nonterminal missing endpoint is unsupported unless it was supplied as
        # an external terminal contact, in which case it remains outside the
        # retained interface scope.
        unpaired_owned = invalid_owned_count
        unsupported = invalid_unsupported_count
        outside = invalid_outside_count

        via_island_pair_coverage = SpdViaIslandPairCoverage(
            raw_target_via_count=raw_via_count,
            paired_via_count=paired_count,
            terminal_owned_unpaired_count=unpaired_owned,
            terminal_owned_unpaired_via_ids_sha256=invalid_owned_digest.hexdigest(),
            unsupported_missing_endpoint_count=unsupported,
            unsupported_missing_endpoint_via_ids_sha256=invalid_unsupported_digest.hexdigest(),
            outside_retained_interface_scope_count=outside,
            outside_retained_interface_scope_via_ids_sha256=invalid_outside_digest.hexdigest(),
            model_relevant_via_count=paired_count + unpaired_owned + unsupported,
            terminal_owned_ids_supplied=bool(terminal_owned_via_ids is not None),
            terminal_owned_declared_count=len(owned_ids),
            terminal_owned_observed_count=paired_owned + unpaired_owned,
            paired_terminal_owned_count=paired_owned if terminal_owned_via_ids is not None else None,
            paired_substrate_count=paired_substrate if terminal_owned_via_ids is not None else None,
        )
    for (net_key, layer_key), island_ids in sorted(surface_inventory.items()):
        contacted = tuple(
            sorted(
                token
                for candidate_net, candidate_layer, token in surface_key_by_code
                if candidate_net == net_key and candidate_layer == layer_key
            )
        )
        component_count = component_count_by_surface.get((net_key, layer_key), 0)
        status = "complete" if set(contacted) == set(island_ids) and component_count else "uncontacted_island"
        display_net = next(
            (str(raw_net) for raw_net in target_layers_by_net if str(raw_net).casefold() == net_key),
            net_key,
        )
        display_layer = target_layer_display.get((net_key, layer_key), layer_key)
        surface_equivalence_proofs.append(
            SpdSurfaceEquivalenceProof(
                display_net,
                display_layer,
                tuple(island_ids),
                contacted,
                component_count,
                status,
            )
        )
    component_count_by_surface.clear()
    surface_inventory.clear()
    # Build the finite Via quotient from the already-retained endpoint graph.
    # This is intentionally post-replay: no second Via section scan or eager
    # raw geometry is needed, and every edge keeps its source Via owner.
    finite_via_vertices: list[SpdFiniteViaQuotientVertex] = []
    finite_via_edges: list[SpdFiniteViaQuotientEdge] = []
    finite_via_vertex_id_by_landing: dict[tuple[str, str], str] = {}
    finite_via_edge_id_by_landing: dict[tuple[str, str], str] = {}
    finite_via_coverage: SpdFiniteViaQuotientCoverage | None = None
    finite_via_scenario_isolated_landing_keys: frozenset[tuple[str, str]] = frozenset()
    finite_via_scenario_isolation_coverage: SpdFiniteViaScenarioIsolationCoverage | None = None
    finite_via_vertex_id_by_retarget_destination: dict[tuple[str, str, str], str] = {}
    finite_via_retarget_destination_coverage: SpdFiniteViaRetargetDestinationCoverage | None = None
    if finite_via_id_end_offsets and finite_requested:
        # Restore the canonical finite scope before reducing the dense graph:
        # same-layer Trace/artwork components are ideal nodes, while Via-only
        # components and passive dangling trees remain outside global MNA.
        edge_count = len(finite_via_id_end_offsets)
        node_count = graph_node_count

        def edge_net(edge_index: int) -> str:
            return via_source_net_key_by_code[finite_net_codes[edge_index]]

        def edge_owner(edge_index: int) -> str:
            start = (
                finite_via_id_end_offsets[edge_index - 1]
                if edge_index
                else 0
            )
            end = finite_via_id_end_offsets[edge_index]
            return finite_via_id_bytes[start:end].decode("utf-8")

        def edge_padstack(edge_index: int) -> str:
            return via_source_padstack_names[finite_padstack_codes[edge_index]]

        finite_root_layer_codes = array("I", [0]) * node_count
        for node_index, root in enumerate(equivalence_root_by_node):
            if node_net_key_by_index[node_index] != node_net_key_by_index[root]:
                raise SpdImportError("same-layer quotient root spans multiple NETs")
            observed_code = int(node_layer_codes[node_index])
            if not observed_code:
                continue
            previous_code = finite_root_layer_codes[root]
            if previous_code not in (0, observed_code):
                raise SpdImportError(
                    "same-layer quotient root spans multiple conductor layers"
                )
            finite_root_layer_codes[root] = observed_code
        for edge_index in range(edge_count):
            finite_first_indices[edge_index] = equivalence_root_by_node[
                finite_first_indices[edge_index]
            ]
            finite_second_indices[edge_index] = equivalence_root_by_node[
                finite_second_indices[edge_index]
            ]

        boundary_nodes = bytearray(node_count)
        required_terminal_edge_indices = bytearray(edge_count)
        terminal_ids_by_node: dict[int, list[str]] = {}
        retarget_cut_nodes: set[int] = set()
        for (via_key, node_key), (
            net_key,
            _owner_kind,
            terminal_id,
        ) in terminal_contact_owner_by_key.items():
            landing_key = (via_key, node_key)
            node_index = trace_terminal_node_index_by_landing.get(
                landing_key,
                late_node_indices.get((net_key, node_key)),
            )
            if node_index is None:
                continue
            source_root = equivalence_root_by_node[node_index]
            boundary_nodes[source_root] = 1
            terminal_ids_by_node.setdefault(source_root, []).append(terminal_id)
            edge_index = finite_edge_binding_by_terminal_via.get((net_key, via_key))
            if not isinstance(edge_index, int):
                continue
            first = finite_first_indices[edge_index]
            second = finite_second_indices[edge_index]
            if source_root == first:
                opposite = second
            elif source_root == second:
                opposite = first
            else:
                raise SpdImportError(
                    "terminal first Via is not incident to its exposed quotient vertex"
                )
            # Keep the physical first Via in the finite quotient even when its
            # opposite endpoint is an otherwise dangling package node.  The
            # terminal binding must expose that edge to the global MNA graph;
            # pruning it as a non-boundary leaf leaves the landing with no
            # first_via_quotient_edge_id.  Protect the exact edge instead of
            # making the opposite node a boundary, so ordinary bridge chains
            # still contract and scenario isolation remains the only path cut.
            required_terminal_edge_indices[edge_index] = 1
            if (net_key, node_key) in isolated_node_keys:
                boundary_nodes[opposite] = 1
                retarget_cut_nodes.add(opposite)
        for node_index, code_value in enumerate(surface_code_by_index):
            if code_value:
                boundary_nodes[equivalence_root_by_node[node_index]] = 1
        for raw_net, _raw_layer, raw_node in requested_destinations:
            node_index = late_node_indices.get(
                (str(raw_net).casefold(), str(raw_node).casefold())
            )
            if node_index is not None:
                boundary_nodes[equivalence_root_by_node[node_index]] = 1

        boundaries_by_full_root: dict[int, set[int]] = {}
        for node_index, is_boundary in enumerate(boundary_nodes):
            if is_boundary:
                boundaries_by_full_root.setdefault(
                    full_root_by_node[node_index], set()
                ).add(node_index)
        relevant_full_roots = {
            root
            for root, boundaries in boundaries_by_full_root.items()
            if len(boundaries) >= 2
        }
        boundaries_by_full_root.clear()

        active_edges = bytearray(edge_count)
        degree = array("I", [0]) * node_count
        for edge_index in range(edge_count):
            first = finite_first_indices[edge_index]
            second = finite_second_indices[edge_index]
            if (
                first == second
                or full_root_by_node[first] != full_root_by_node[second]
                or (
                    full_root_by_node[first] not in relevant_full_roots
                    and not required_terminal_edge_indices[edge_index]
                )
            ):
                continue
            active_edges[edge_index] = 1
            degree[first] += 1
            degree[second] += 1
        relevant_full_roots.clear()

        adjacency_offsets = array("I", [0]) * (node_count + 1)
        for node_index in range(node_count):
            adjacency_offsets[node_index + 1] = adjacency_offsets[node_index] + degree[node_index]
        adjacency_incidence = array("I", [0]) * adjacency_offsets[-1]
        adjacency_cursor = array("I", adjacency_offsets[:-1])
        for edge_index in range(edge_count):
            if not active_edges[edge_index]:
                continue
            first = finite_first_indices[edge_index]
            second = finite_second_indices[edge_index]
            adjacency_incidence[adjacency_cursor[first]] = edge_index
            adjacency_cursor[first] += 1
            adjacency_incidence[adjacency_cursor[second]] = edge_index
            adjacency_cursor[second] += 1
        del adjacency_cursor[:]

        def edge_other(edge_index: int, node: int) -> int:
            first = finite_first_indices[edge_index]
            second = finite_second_indices[edge_index]
            return second if first == node else first

        prune_queue = deque(
            node_index
            for node_index, active_degree in enumerate(degree)
            if 0 < active_degree <= 1 and not boundary_nodes[node_index]
        )
        queued = bytearray(node_count)
        for node_index in prune_queue:
            queued[node_index] = 1
        while prune_queue:
            node_index = prune_queue.popleft()
            queued[node_index] = 0
            if boundary_nodes[node_index] or degree[node_index] > 1:
                continue
            active_incident = -1
            for slot in range(
                adjacency_offsets[node_index], adjacency_offsets[node_index + 1]
            ):
                candidate = adjacency_incidence[slot]
                if active_edges[candidate]:
                    active_incident = candidate
                    break
            if active_incident < 0:
                continue
            if required_terminal_edge_indices[active_incident]:
                continue
            active_edges[active_incident] = 0
            neighbor = edge_other(active_incident, node_index)
            degree[node_index] -= 1
            degree[neighbor] -= 1
            if (
                not boundary_nodes[neighbor]
                and degree[neighbor] <= 1
                and not queued[neighbor]
            ):
                prune_queue.append(neighbor)
                queued[neighbor] = 1
        queued.clear()

        del adjacency_offsets[:]
        del adjacency_incidence[:]
        adjacency_offsets = array("I", [0]) * (node_count + 1)
        for node_index in range(node_count):
            adjacency_offsets[node_index + 1] = (
                adjacency_offsets[node_index] + degree[node_index]
            )
        adjacency_incidence = array("I", [0]) * adjacency_offsets[-1]
        adjacency_cursor = array("I", adjacency_offsets[:-1])
        for edge_index in range(edge_count):
            if not active_edges[edge_index]:
                continue
            first = finite_first_indices[edge_index]
            second = finite_second_indices[edge_index]
            adjacency_incidence[adjacency_cursor[first]] = edge_index
            adjacency_cursor[first] += 1
            adjacency_incidence[adjacency_cursor[second]] = edge_index
            adjacency_cursor[second] += 1
        del adjacency_cursor[:]

        finite_modeled_via_ids_digest = hashlib.sha256()
        finite_outside_via_ids_digest = hashlib.sha256()
        for edge_index in range(edge_count):
            encoded = edge_owner(edge_index).encode("utf-8")
            target_digest = (
                finite_modeled_via_ids_digest
                if active_edges[edge_index]
                else finite_outside_via_ids_digest
            )
            target_digest.update(len(encoded).to_bytes(4, "big"))
            target_digest.update(encoded)
            if active_edges[edge_index]:
                finite_owner_ledger_digest.update(
                    (len(encoded) + 4).to_bytes(4, "big")
                )
                finite_owner_ledger_digest.update(b"via:")
                finite_owner_ledger_digest.update(encoded)

        def incident_start(node: int) -> int:
            return int(adjacency_offsets[node])

        def incident_end(node: int) -> int:
            return int(adjacency_offsets[node + 1])

        def incident_degree(node: int) -> int:
            return incident_end(node) - incident_start(node)

        # Tarjan bridges classify cycle/parallel topology in O(V+E).  A
        # non-boundary degree-two node is contractible iff both incident edges
        # are bridges; the previous per-node BFS made middle-first chains
        # quadratic and could oscillate on cycles.
        discovery = array("i", [-1]) * node_count
        low = array("i", [0]) * node_count
        parent_edge = array("i", [-1]) * node_count
        parent_node = array("i", [-1]) * node_count
        bridges = bytearray(edge_count)
        timer = 0
        for start in range(node_count):
            if not degree[start] or discovery[start] >= 0:
                continue
            discovery[start] = low[start] = timer
            timer += 1
            stack_nodes = array("I", [start])
            stack_cursors = array("I", [incident_start(start)])
            while stack_nodes:
                node = stack_nodes[-1]
                cursor = stack_cursors[-1]
                end = incident_end(node)
                if cursor >= end:
                    stack_nodes.pop()
                    stack_cursors.pop()
                    parent = parent_node[node]
                    parent_edge_index = parent_edge[node]
                    if parent >= 0 and parent_edge_index >= 0:
                        low[parent] = min(low[parent], low[node])
                        if low[node] > discovery[parent]:
                            bridges[parent_edge_index] = 1
                    continue
                edge_index = adjacency_incidence[cursor]
                stack_cursors[-1] = cursor + 1
                if edge_index == parent_edge[node]:
                    continue
                neighbor = edge_other(edge_index, node)
                if discovery[neighbor] < 0:
                    parent_node[neighbor] = node
                    parent_edge[neighbor] = edge_index
                    discovery[neighbor] = low[neighbor] = timer
                    timer += 1
                    stack_nodes.append(neighbor)
                    stack_cursors.append(incident_start(neighbor))
                else:
                    low[node] = min(low[node], discovery[neighbor])

        contracted = bytearray(node_count)
        for node in range(node_count):
            if boundary_nodes[node] or degree[node] != 2:
                continue
            first_edge = adjacency_incidence[incident_start(node)]
            second_edge = adjacency_incidence[incident_start(node) + 1]
            if bridges[first_edge] and bridges[second_edge] and edge_other(first_edge, node) != edge_other(second_edge, node):
                contracted[node] = 1
        del discovery[:]
        del low[:]
        del parent_edge[:]
        del parent_node[:]
        bridges.clear()

        vertex_for_node = array("I", range(node_count))
        visited_edges = bytearray(edge_count)
        quotient_path_starts = array("I")
        quotient_path_ends = array("I")
        quotient_path_offsets = array("Q", [0])
        quotient_path_edge_ids = array("I")
        # Walk each bridge chain once from a kept endpoint.  Every contracted
        # interior node is assigned during that walk, so source ordering no
        # longer depends on which middle node happens to be visited first.
        for start in range(node_count):
            if not degree[start] or contracted[start]:
                continue
            for incidence_index in range(incident_start(start), incident_end(start)):
                first_edge = adjacency_incidence[incidence_index]
                if visited_edges[first_edge]:
                    continue
                current = start
                previous_edge = -1
                path_offset = len(quotient_path_edge_ids)
                path_committed = False
                while True:
                    current_edge = -1
                    for candidate_offset in range(incident_start(current), incident_end(current)):
                        candidate = adjacency_incidence[candidate_offset]
                        if candidate != previous_edge and not visited_edges[candidate]:
                            current_edge = candidate
                            break
                    if current_edge < 0:
                        break
                    visited_edges[current_edge] = 1
                    quotient_path_edge_ids.append(current_edge)
                    nxt = edge_other(current_edge, current)
                    if not contracted[nxt]:
                        quotient_path_starts.append(start)
                        quotient_path_ends.append(nxt)
                        quotient_path_offsets.append(len(quotient_path_edge_ids))
                        path_committed = True
                        break
                    vertex_for_node[nxt] = start
                    previous_edge = current_edge
                    current = nxt
                if path_committed:
                    continue
                del quotient_path_edge_ids[path_offset:]
        # Any remaining edge is cyclic/parallel (or a defensive malformed
        # component); retain it explicitly rather than forcing contraction.
        for edge_index in range(edge_count):
            if not active_edges[edge_index] or visited_edges[edge_index]:
                continue
            visited_edges[edge_index] = 1
            first = finite_first_indices[edge_index]
            second = finite_second_indices[edge_index]
            quotient_path_starts.append(vertex_for_node[first])
            quotient_path_ends.append(vertex_for_node[second])
            quotient_path_edge_ids.append(edge_index)
            quotient_path_offsets.append(len(quotient_path_edge_ids))
        visited_edges.clear()
        contracted.clear()
        del adjacency_incidence[:]

        def finite_digest(values: Iterable[str]) -> str:
            digest = hashlib.sha256()
            for value in values:
                encoded = str(value).casefold().encode("utf-8")
                digest.update(len(encoded).to_bytes(4, "big"))
                digest.update(encoded)
            return digest.hexdigest()

        vertex_member_count = array("I", [0]) * node_count
        for node_index in range(node_count):
            root = equivalence_root_by_node[node_index]
            if degree[root] or boundary_nodes[root]:
                vertex_member_count[vertex_for_node[root]] += 1
        vertex_member_offsets = array("I", [0]) * (node_count + 1)
        for node_index in range(node_count):
            vertex_member_offsets[node_index + 1] = (
                vertex_member_offsets[node_index] + vertex_member_count[node_index]
            )
        vertex_members = array("I", [0]) * vertex_member_offsets[-1]
        vertex_member_cursor = array("I", vertex_member_offsets[:-1])
        for node_index in range(node_count):
            root = equivalence_root_by_node[node_index]
            if not degree[root] and not boundary_nodes[root]:
                continue
            representative = vertex_for_node[root]
            vertex_members[vertex_member_cursor[representative]] = node_index
            vertex_member_cursor[representative] += 1
        del degree[:]
        del vertex_for_node[:]
        del vertex_member_cursor[:]
        vertex_id_by_node: list[str | None] = [None] * node_count
        for representative in range(node_count):
            if not vertex_member_count[representative]:
                continue
            members = vertex_members[
                vertex_member_offsets[representative] : vertex_member_offsets[representative + 1]
            ]
            net_for_vertex = node_net_key_by_index[members[0]]
            representative_node = min(node_id_by_index[member] for member in members)
            layer_code = finite_root_layer_codes[representative]
            layer = (
                layer_display_by_code[layer_code - 1]
                if layer_code
                else "UNKNOWN"
            )
            node_ids_hash = finite_digest(
                sorted(node_id_by_index[member] for member in members)
            )
            vertex_id = f"spd-finite-via-vertex:{finite_digest((net_for_vertex, layer, representative_node, str(len(members)), node_ids_hash))[:24]}"
            retained_mutable: dict[str, set[str]] = {}
            roles: set[str] = set()
            for member in members:
                if node_net_key_by_index[member] != net_for_vertex:
                    continue
                surface_code = int(surface_code_by_index[member])
                if not surface_code:
                    continue
                _surface_net, candidate_layer, token = surface_key_by_code[surface_code - 1]
                display_layer = target_layer_display.get((net_for_vertex, candidate_layer), candidate_layer)
                retained_mutable.setdefault(display_layer, set()).update(
                    surface_component_islands_by_code.get(surface_code, (token,))
                )
            retained = {
                layer_key: tuple(sorted(tokens))
                for layer_key, tokens in retained_mutable.items()
            }
            if retained:
                roles.add("retained_surface")
            member_roots = {
                equivalence_root_by_node[member] for member in members
            }
            terminal_ids = tuple(
                sorted(
                    {
                        terminal_id
                        for root in member_roots
                        for terminal_id in terminal_ids_by_node.get(root, ())
                    }
                )
            )
            if terminal_ids:
                roles.add("terminal")
            if member_roots & retarget_cut_nodes:
                roles.add("retarget_cut")
            if incident_degree(representative) > 2:
                roles.add("junction")
            elif not roles:
                roles.add("cycle_anchor" if incident_degree(representative) == 2 else "leaf")
            finite_via_vertices.append(
                SpdFiniteViaQuotientVertex(
                    vertex_id=vertex_id,
                    net=next((str(raw_net) for raw_net in target_layers_by_net if str(raw_net).casefold() == net_for_vertex), net_for_vertex),
                    layer=layer,
                    representative_node_id=representative_node,
                    source_node_count=len(members),
                    source_node_ids_sha256=node_ids_hash,
                    roles=tuple(roles),
                    retained_component_island_ids_by_layer=retained,
                    terminal_ids=terminal_ids,
                )
            )
            for member in members:
                vertex_id_by_node[member] = vertex_id
        members = array("I")
        del adjacency_offsets[:]
        del vertex_member_count[:]
        del vertex_member_offsets[:]
        del vertex_members[:]
        required_terminal_edge_indices.clear()
        terminal_ids_by_node.clear()
        boundary_nodes.clear()
        retarget_cut_nodes.clear()
        del equivalence_root_by_node[:]
        del full_root_by_node[:]
        del surface_code_by_index[:]
        surface_key_by_code.clear()
        surface_component_islands_by_code.clear()
        def finite_physical(padstack_name: str, start_layer: str, end_layer: str):
            drill, material, segments, status, issues = physical_model_for_canonical(
                padstack_name, start_layer, end_layer
            )
            if status != "complete" or not segments:
                return drill, material, segments, status, issues, None, None, None
            try:
                models = tuple(
                    estimate_via_segment_rl(
                        length_um=segment.length_um,
                        drill_diameter_um=drill,
                        padstack_material=material,
                        start_layer=segment.start_layer,
                        end_layer=segment.end_layer,
                        stackup_layers=stackup_layers,
                    )
                    for segment in segments
                )
                return (
                    drill,
                    material,
                    segments,
                    "complete",
                    (),
                    fsum(model.resistance_ohm for model in models),
                    fsum(model.inductance_h for model in models),
                    fsum(segment.length_um for segment in segments),
                )
            except Exception:
                return drill, material, segments, "incomplete", ("physical_model_unavailable",), None, None, None

        for path_index, (start_node, end_node) in enumerate(
            zip(quotient_path_starts, quotient_path_ends, strict=True)
        ):
            path_start = quotient_path_offsets[path_index]
            path_end = quotient_path_offsets[path_index + 1]
            path_length = path_end - path_start
            start_vertex = vertex_id_by_node[start_node]
            end_vertex = vertex_id_by_node[end_node]
            owner_ids: list[str] = []
            terminal_via_keys: list[tuple[str, str]] = []
            edge_digest_builder = hashlib.sha256()
            terms = []
            total_r = total_l = total_length = 0.0
            status = "complete"
            issues: set[str] = set()
            for ordinal, offset in enumerate(range(path_start, path_end)):
                edge_index = quotient_path_edge_ids[offset]
                raw_owner_id = edge_owner(edge_index)
                net_for_edge = edge_net(edge_index)
                terminal_via_key = (net_for_edge, raw_owner_id)
                if terminal_via_key in finite_edge_binding_by_terminal_via:
                    terminal_via_keys.append(terminal_via_key)
                encoded_owner_id = raw_owner_id.encode("utf-8")
                edge_digest_builder.update(
                    len(encoded_owner_id).to_bytes(4, "big")
                )
                edge_digest_builder.update(encoded_owner_id)
                owner_ids.append(f"via:{raw_owner_id}")
                first_index = finite_first_indices[edge_index]
                second_index = finite_second_indices[edge_index]
                first_code = finite_root_layer_codes[first_index]
                second_code = finite_root_layer_codes[second_index]
                first_layer = (
                    layer_display_by_code[first_code - 1]
                    if first_code
                    else "UNKNOWN"
                )
                second_layer = (
                    layer_display_by_code[second_code - 1]
                    if second_code
                    else "UNKNOWN"
                )
                padstack_name = edge_padstack(edge_index)
                drill, material, segments, term_status, term_issues, resistance, inductance, length_um = finite_physical(padstack_name, first_layer, second_layer)
                if term_status != "complete":
                    status = "incomplete"
                    issues.update(term_issues)
                else:
                    total_r += resistance or 0.0
                    total_l += inductance or 0.0
                    total_length += length_um or 0.0
                terms.append(SpdFiniteViaSeriesTerm(ordinal, 1, padstack_name, first_layer, second_layer, drill, material, segments, resistance, inductance, length_um, term_status, term_issues))
            edge_digest = edge_digest_builder.hexdigest()
            edge_id = f"spd-finite-via-edge:{finite_digest((start_vertex, end_vertex, edge_digest))[:24]}"
            finite_via_edges.append(SpdFiniteViaQuotientEdge(edge_id, edge_net(quotient_path_edge_ids[path_start]), start_vertex, end_vertex, 1, path_length, path_length, edge_digest, tuple(owner_ids), tuple(terms), total_r if status == "complete" else None, total_l if status == "complete" else None, total_length if status == "complete" else None, "contracted_series" if path_length > 1 else "retained_explicit", status, tuple(sorted(issues))))
            for terminal_via_key in terminal_via_keys:
                finite_edge_binding_by_terminal_via[terminal_via_key] = edge_id
        del quotient_path_starts[:]
        del quotient_path_ends[:]
        del quotient_path_offsets[:]
        del quotient_path_edge_ids[:]
        del finite_net_codes[:]
        del finite_first_indices[:]
        del finite_second_indices[:]
        del finite_padstack_codes[:]
        del finite_root_layer_codes[:]
        via_source_net_key_by_code.clear()
        via_source_padstack_names.clear()
        del node_layer_codes[:]
        layer_code_by_key.clear()
        layer_key_by_code.clear()
        layer_display_by_code.clear()
        for key, (net_key, _owner_kind, _terminal_id) in (
            terminal_contact_owner_by_key.items()
        ):
            node_index = trace_terminal_node_index_by_landing.get(
                key,
                late_node_indices.get((net_key, key[1])),
            )
            vertex_id = vertex_id_by_node[node_index] if node_index is not None else None
            if vertex_id:
                finite_via_vertex_id_by_landing[key] = vertex_id
                if not key[0].startswith("source-node:"):
                    edge_id = finite_edge_binding_by_terminal_via.get(
                        (net_key, key[0])
                    )
                    if isinstance(edge_id, str):
                        finite_via_edge_id_by_landing[key] = edge_id
        finite_edge_binding_by_terminal_via.clear()
        trace_terminal_node_index_by_landing.clear()
        terminal_contact_owner_by_key.clear()
        scenario_requested = tuple(scenario_isolated_terminal_landings or ())
        finite_via_scenario_isolated_landing_keys = frozenset(
            (
                str(getattr(item, "via_id", "")).casefold(),
                str(getattr(item, "endpoint_node_id", "")).casefold(),
            )
            for item in scenario_requested
        )
        scenario_ids_hash = finite_digest(
            f"{via}:{node}" for via, node in sorted(finite_via_scenario_isolated_landing_keys)
        )
        finite_via_scenario_isolation_coverage = (
            SpdFiniteViaScenarioIsolationCoverage(
                requested_landing_count=len(scenario_requested),
                isolated_landing_count=len(finite_via_scenario_isolated_landing_keys),
                isolated_node_count=len({node for _via, node in finite_via_scenario_isolated_landing_keys}),
                suppressed_artwork_contact_count=len(isolated_node_ids),
                suppressed_trace_edge_count=sum(
                    1
                    for trace_net_code, first_index, second_index in zip(surface_trace_net_codes, surface_trace_first_indices, surface_trace_second_indices, strict=True)
                    for _net in (surface_trace_net_by_code[trace_net_code],)
                    for first, second in ((node_id_for(_net, first_index), node_id_for(_net, second_index)),)
                    if (_net, first) in isolated_node_keys or (_net, second) in isolated_node_keys
                ),
                requested_landing_ids_sha256=scenario_ids_hash,
                isolated_landing_ids_sha256=scenario_ids_hash,
            )
            if scenario_requested
            else None
        )
        for raw_net, raw_layer, raw_node in requested_destinations:
            key = (str(raw_net).casefold(), str(raw_layer).casefold(), str(raw_node).casefold())
            node_index = late_node_indices.get((key[0], key[2]))
            vertex_id = vertex_id_by_node[node_index] if node_index is not None else None
            if vertex_id is not None:
                finite_via_vertex_id_by_retarget_destination[key] = vertex_id
        vertex_id_by_node.clear()
        late_node_indices.clear()
        destination_digest = finite_digest(
            f"{net}:{layer}:{node}" for net, layer, node in requested_destinations
        )
        finite_via_retarget_destination_coverage = (
            SpdFiniteViaRetargetDestinationCoverage(
                requested_destination_count=len(requested_destinations),
                resolved_destination_count=len(finite_via_vertex_id_by_retarget_destination),
                requested_destination_ids_sha256=destination_digest,
                resolved_destination_ids_sha256=finite_digest(
                    f"{net}:{layer}:{node}"
                    for net, layer, node in finite_via_vertex_id_by_retarget_destination
                ),
            )
            if requested_destinations
            else None
        )
        canonical_owner_digest = _canonical_casefolded_ids_digest(
            (
                f"via:{edge_owner(index)}"
                for index in range(edge_count)
                if active_edges[index]
            ),
            check=reporter.check,
        ).hexdigest()
        modeled_count = sum(active_edges)
        complete_count = sum(
            term.count
            for edge in finite_via_edges
            for term in edge.series_terms
            if term.physical_model_status == "complete"
        )
        incomplete_count = modeled_count - complete_count
        outside_count = edge_count - modeled_count
        finite_via_coverage = SpdFiniteViaQuotientCoverage(
            raw_target_via_count=edge_count, modeled_global_via_count=modeled_count, outside_scope_via_count=outside_count,
            pruned_dangling_via_count=outside_count, physical_complete_via_count=complete_count,
            physical_incomplete_via_count=incomplete_count,
            raw_target_via_ids_sha256=finite_raw_via_ids_digest.hexdigest(), modeled_global_via_ids_sha256=finite_modeled_via_ids_digest.hexdigest(),
            modeled_owner_ledger_sha256=finite_owner_ledger_digest.hexdigest(), modeled_owner_canonical_sha256=canonical_owner_digest, outside_scope_via_ids_sha256=finite_outside_via_ids_digest.hexdigest(),
        )
        active_edges.clear()
        finite_via_id_bytes.clear()
        del finite_via_id_end_offsets[:]
    del full_root_by_node[:]
    del equivalence_root_by_node[:]
    finite_edge_binding_by_terminal_via.clear()
    physical_model_cache.clear()
    surface_dense_trace_edges = len(surface_trace_first_indices)
    del surface_trace_net_codes[:]
    del surface_trace_first_indices[:]
    del surface_trace_second_indices[:]
    surface_trace_net_by_code.clear()
    del finite_net_codes[:]
    del finite_first_indices[:]
    del finite_second_indices[:]
    del finite_padstack_codes[:]
    finite_via_id_bytes.clear()
    del finite_via_id_end_offsets[:]
    via_source_net_key_by_code.clear()
    via_source_padstack_names.clear()
    terminal_contact_owner_by_key.clear()
    trace_terminal_node_index_by_landing.clear()
    del surface_code_by_index[:]
    surface_key_by_code.clear()
    surface_component_islands_by_code.clear()
    late_node_indices.clear()
    node_id_by_index.clear()
    node_net_key_by_index.clear()
    del node_layer_codes[:]
    layer_code_by_key.clear()
    layer_key_by_code.clear()
    layer_display_by_code.clear()
    reporter.report(100, "Checked mixed-reference GND landing reachability")
    return finish(SpdGroundReachability(
        frozenset(reachable), frozenset(unreachable), {
            "requested": requested_count, "reachable": len(reachable),
            "unreachable": len(unreachable), "node_section_passes": 1,
            "trace_section_passes": int(
                include_traces and trace_start >= 0 and trace_end > trace_start
            ),
            "via_section_passes": 1,
            "components": components,
            "graph_nodes": graph_node_count,
            "graph_edges": graph_edges,
            "artwork_nodes": artwork_nodes,
            "artwork_edges": artwork_edges,
            "artwork_components": artwork_component_count,
            "artwork_island_count": artwork_island_count,
            "artwork_island_unions": artwork_island_union_count,
            "surface_equivalence_complete": sum(1 for proof in surface_equivalence_proofs if proof.status == "complete"),
            "surface_equivalence_incomplete": sum(1 for proof in surface_equivalence_proofs if proof.status != "complete"),
            "via_edges_excluded_from_surface_equivalence": via_edges,
            "terminal_owned_via_id_count": len(owned_ids),
            "terminal_owned_via_observed_count": paired_owned + (invalid_owned_count if raw_via_count else 0),
            "via_island_pair_terminal_owned_record_count": paired_owned,
            "via_island_pair_substrate_record_count": paired_substrate,
            "artwork_trace_contacts": artwork_trace_contacts,
            "trace_artwork_contact_count": artwork_trace_contacts,
            "trace_artwork_conditional_passes": trace_artwork_conditional_passes,
            "conditional_node_position_passes": conditional_node_position_passes,
            "node_release_index_passes": node_release_index_passes,
            "deferred_artwork_passes": deferred_artwork_passes,
            "deferred_artwork_keys": deferred_artwork_keys,
            "max_live_artwork_shapes": max_live_artwork_shapes,
            "trace_artwork_conditional_checks": trace_artwork_conditional_checks,
            "trace_artwork_conditional_successes": trace_artwork_conditional_successes,
            "target_nodes_considered": target_nodes_considered,
            "target_nodes_filtered": target_nodes_filtered,
            "conditional_target_component_passes": conditional_target_component_passes,
            "conditional_target_components_scanned": conditional_target_components_scanned,
            "conditional_target_components_recovered": conditional_target_components_recovered,
            "component_contact_records_retained": component_contact_records_retained,
            "component_contact_records_filtered": component_contact_records_filtered,
            "artwork_algorithm": "same_net_via_trace_artwork_reachability_v3_layerwise_deferred",
            "trace_edges": trace_edges,
            "via_edges": via_edges,
            "via_source_record_replay_passes": int(raw_via_count > 0),
            "surface_dense_node_count": surface_dense_node_count,
            "surface_dense_trace_edges": surface_dense_trace_edges,
        },
        target_contacts_by_key=target_contacts_by_key,
        target_contact_count_by_key=target_contact_count_by_key,
        target_contact_hash_by_key=target_contact_hash_by_key,
        surface_components=tuple(sorted(surface_components, key=lambda item: (item.net.casefold(), item.layers))),
        surface_layers_by_landing=surface_layers_by_landing,
        surface_islands_by_landing=surface_islands_by_landing,
        surface_equivalence_proofs=tuple(surface_equivalence_proofs),
        surface_equivalence_components=tuple(sorted(surface_equivalence_components, key=lambda item: (item.net.casefold(), item.layer.casefold(), item.island_ids))),
        landing_surface_contacts=tuple(landing_surface_contacts),
        via_island_pair_aggregates=tuple(via_island_pair_aggregates),
        via_island_pair_coverage=via_island_pair_coverage,
        finite_via_vertices=tuple(finite_via_vertices),
        finite_via_edges=tuple(finite_via_edges),
        finite_via_vertex_id_by_landing=finite_via_vertex_id_by_landing,
        finite_via_edge_id_by_landing=finite_via_edge_id_by_landing,
        finite_via_coverage=finite_via_coverage,
        finite_via_scenario_isolated_landing_keys=finite_via_scenario_isolated_landing_keys,
        finite_via_scenario_isolation_coverage=finite_via_scenario_isolation_coverage,
        finite_via_vertex_id_by_retarget_destination=finite_via_vertex_id_by_retarget_destination,
        finite_via_retarget_destination_coverage=finite_via_retarget_destination_coverage,
    ))


def analyze_spd(
    path: str | Path,
    frequencies_hz: Iterable[float] = (1.0e3, 1.0e6, 1.0e9),
    gnd_aliases: Iterable[str] = ("DGND", "GND"),
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    scope: SpdAnalysisScope = "selected_pi",
    source_plane_ownership: bool = False,
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
    source_provenance: dict[str, Any] | None = {"source_records": []} if source_plane_ownership else None

    try:
        with source_path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            reporter.report(3, "Hashing SPD source")
            digest = hashlib.sha256(data).hexdigest()
            first_end = _line_end(data, 0, min(len(data), 16_384))
            title = _decode(data[0:first_end]).strip()
            source = SpdSourceInfo(source_path.resolve(), source_path.name, stat.st_size, stat.st_mtime_ns, digest, title)
            # Test and migration fixtures may explicitly declare that the
            # retained source does not provide a usable Trace/Via graph.  This
            # capability marker is source-authored; it is intentionally not
            # inferred from file size or an empty recovery result.
            capability_match = re.search(
                rb"(?im)^\s*\*\s*SourceGraphCapability\s*=\s*([A-Z0-9_]+)\s*$",
                data,
            )
            explicit_source_graph_capability = (
                capability_match.group(1).decode("ascii", "ignore").upper()
                if capability_match is not None
                else None
            )

            reporter.report(9, "Reading selected power/ground nets")
            selected_power, selected_ground = _parse_netlist(
                data,
                gnd_keys,
                diagnostics,
                include_unselected_power=scope == "decap_scenario",
            )
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
                data, material_start, material_end, diagnostics, source_provenance
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
            layers = _parse_layers(data, layer_start, layer_end, layer_nets, dielectrics, metals, plane_keys, diagnostics, source_provenance)
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
                reporter,
                top_layer=top_layer,
                padstacks=padstacks,
                device_pins=tuple(pins),
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
                "source_graph_capability": explicit_source_graph_capability,
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
                source_plane_ownership_draft=source_provenance,
            )
    except SpdImportError:
        raise
    except OSError as exc:
        raise SpdImportError(f"cannot read SPD source {source_path}: {exc}") from exc


__all__ = [
    "SpdAnalysis",
    "SpdAnalysisScope",
    "SpdCapInstance",
    "SpdDecapConnection",
    "SpdDiagnostic",
    "SpdGroundReachability",
    "SpdImportError",
    "SpdPadStack",
    "SpdPadShape",
    "SpdPlaneGeometry",
    "SpdSourceInfo",
    "SpdSharedPadCluster",
    "SpdViaPathEvidence",
    "SpdViaStructuralEvidence",
    "SpdViaPathRecovery",
    "SpdViaPathSegment",
    "SpdViaUsage",
    "analyze_spd",
    "recover_spd_via_paths",
    "recover_spd_ground_reachability",
]
