"""Validated, JSON-safe domain contracts for the MLO PDN application.

The numerical core is intentionally kept out of this module.  These models are
the stable interchange format shared by importers, the CLI, the GUI, the
solver, and project persistence.
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .version import __version__


SCHEMA_VERSION = "0.4"
MIXED_REFERENCE_MIN_COVERAGE = 0.90
MIXED_REFERENCE_MIN_DOMINANT_COMPONENT = 0.99


class DomainModel(BaseModel):
    """Common strict configuration for persisted domain objects."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class CoordinateUnit(StrEnum):
    UM = "um"
    MM = "mm"
    MIL = "mil"
    M = "m"


class RailState(StrEnum):
    ACTIVE = "ACTIVE"
    RESERVE = "RESERVE"
    IGNORE = "IGNORE"


class PinKind(StrEnum):
    DEVICE_BUMP = "DEVICE_BUMP"
    DECAP_PAD = "DECAP_PAD"


class TerminalKind(StrEnum):
    PWR = "PWR"
    GND = "GND"


class TopologyKind(StrEnum):
    EMPTY = "EMPTY"
    DIRECT = "DIRECT"
    SHARED_PAIR = "SHARED_PAIR"
    SHARED_PAD_CLUSTER = "SHARED_PAD_CLUSTER"


class ViaPathKind(StrEnum):
    DIRECT = "DIRECT"
    STACKED = "STACKED"
    STAGGERED = "STAGGERED"


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ConfidenceCategory(StrEnum):
    NUMERICAL = "Numerical"
    GEOMETRY = "Geometry"
    TEMPLATE = "Template"
    COUPLING = "Coupling"
    MODEL_COVERAGE = "Model Coverage"


class Point2D(DomainModel):
    x_um: float
    y_um: float

    @field_validator("x_um", "y_um")
    @classmethod
    def finite_coordinates(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("coordinates must be finite")
        return value


class MLOOutline(DomainModel):
    width_um: float = Field(gt=0)
    height_um: float = Field(gt=0)
    origin_x_um: float = 0.0
    origin_y_um: float = 0.0

    @field_validator("width_um", "height_um", "origin_x_um", "origin_y_um")
    @classmethod
    def finite_dimensions(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("outline values must be finite")
        return value

    @property
    def x_max_um(self) -> float:
        return self.origin_x_um + self.width_um

    @property
    def y_max_um(self) -> float:
        return self.origin_y_um + self.height_um


class CoordinateTransform(DomainModel):
    """Transform from a source report into the project coordinate system."""

    source_unit: CoordinateUnit = CoordinateUnit.UM
    origin_x_um: float = 0.0
    origin_y_um: float = 0.0
    rotation_deg: Literal[0, 90, 180, 270] = 0
    mirror_x: bool = False
    mirror_y: bool = False

    @field_validator("origin_x_um", "origin_y_um")
    @classmethod
    def finite_origin(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("coordinate origin must be finite")
        return value


class FrequencySettings(DomainModel):
    start_hz: float = Field(default=1.0e3, gt=0)
    stop_hz: float = Field(default=1.0e9, gt=0)
    points: int = Field(default=401, ge=2)
    spacing: Literal["log", "linear"] = "log"
    critical_start_hz: float = Field(default=1.0e5, gt=0)
    critical_stop_hz: float = Field(default=1.0e8, gt=0)

    @model_validator(mode="after")
    def ordered_bands(self) -> "FrequencySettings":
        if self.stop_hz <= self.start_hz:
            raise ValueError("stop_hz must be greater than start_hz")
        if self.critical_stop_hz <= self.critical_start_hz:
            raise ValueError(
                "critical_stop_hz must be greater than critical_start_hz"
            )
        if not (
            self.start_hz <= self.critical_start_hz
            and self.critical_stop_hz <= self.stop_hz
        ):
            raise ValueError("critical band must lie inside the full sweep")
        return self


class TargetPoint(DomainModel):
    frequency_hz: float = Field(gt=0)
    impedance_ohm: float = Field(gt=0)


class MixedReferenceCertificate(DomainModel):
    """Geometry evidence allowing a selected PWR rail to use a mixed layer.

    This is intentionally attached to the rail rather than inferred from a
    layer name at solve time.  The hashes bind the result to the exact retained
    PowerSI artwork used for the boolean intersection.
    """

    version: Literal["mixed-reference-v1"] = "mixed-reference-v1"
    method: Literal["ordered-shapely-boolean-v1"] = "ordered-shapely-boolean-v1"
    rail_net: str = Field(min_length=1)
    gnd_net: str = Field(min_length=1)
    pwr_layer: str = Field(min_length=1)
    gnd_layer: str = Field(min_length=1)
    pwr_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gnd_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    overlap_fraction: float = Field(ge=0, le=1)
    dominant_overlap_component_fraction: float = Field(ge=0, le=1)


class MixedReferenceGroundWitness(DomainModel):
    """Source-graph evidence for GND landings on a mixed return layer.

    A mixed-reference geometry certificate proves that the selected rail and
    the named DGND artwork overlap.  It cannot, by itself, prove that a decap
    GND landing reaches that particular artwork rather than another net on the
    same physical layer.  This compact witness records the result of a
    same-net raw SPD Via+Trace graph reachability check for every covered
    landing.  It deliberately records reachability, not a unique RL path.
    """

    version: Literal["mixed-reference-ground-reachability-v1"] = (
        "mixed-reference-ground-reachability-v1"
    )
    method: Literal["same-net-via-trace-reachability-v1"] = (
        "same-net-via-trace-reachability-v1"
    )
    rail_net: str = Field(min_length=1)
    gnd_net: str = Field(min_length=1)
    pwr_layer: str = Field(min_length=1)
    gnd_layer: str = Field(min_length=1)
    gnd_asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    landing_identities: tuple[str, ...] = ()
    landing_count: int = Field(ge=0)
    landing_identities_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def canonical_landing_identities(self) -> "MixedReferenceGroundWitness":
        identities = tuple(item.strip().casefold() for item in self.landing_identities)
        if any(not item for item in identities):
            raise ValueError("mixed-reference ground witness identities must be nonblank")
        if identities != tuple(sorted(identities)) or len(identities) != len(set(identities)):
            raise ValueError(
                "mixed-reference ground witness identities must be unique and canonically sorted"
            )
        if self.landing_count != len(identities):
            raise ValueError("mixed-reference ground witness landing count disagrees")
        digest = sha256(
            (json.dumps(list(identities), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        ).hexdigest()
        if self.landing_identities_sha256 != digest:
            raise ValueError("mixed-reference ground witness landing hash disagrees")
        return self


class RailSpec(DomainModel):
    rail_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    net: str = Field(min_length=1)
    site: str = Field(min_length=1)
    pwr_layer: str = Field(min_length=1)
    gnd_layer: str = Field(min_length=1)
    priority: int = Field(default=2, ge=1, le=3)
    state: RailState = RailState.ACTIVE
    target_mask: list[TargetPoint] = Field(default_factory=list)
    min_near_slots: int = Field(default=0, ge=0)
    reserved_slot_ids: list[str] = Field(default_factory=list)
    mixed_reference_certificate: MixedReferenceCertificate | None = None
    mixed_reference_ground_witness: MixedReferenceGroundWitness | None = None

    @field_validator("target_mask")
    @classmethod
    def ordered_target_mask(cls, value: list[TargetPoint]) -> list[TargetPoint]:
        frequencies = [point.frequency_hz for point in value]
        if frequencies != sorted(frequencies) or len(frequencies) != len(
            set(frequencies)
        ):
            raise ValueError(
                "target_mask frequencies must be strictly increasing and unique"
            )
        return value

    @field_validator("reserved_slot_ids")
    @classmethod
    def unique_reserved_slots(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("reserved_slot_ids must be unique")
        return value


class StackupLayer(DomainModel):
    """One physical row of a top-to-bottom stack-up table."""

    name: str = Field(min_length=1)
    thickness_um: float = Field(gt=0)
    conductivity_s_m: float | None = Field(default=None, gt=0)
    dk: float | None = Field(default=None, gt=0)
    df: float | None = Field(default=None, ge=0)
    pwr_nets: list[str] = Field(default_factory=list)

    @property
    def is_conductor(self) -> bool:
        return self.conductivity_s_m is not None

    @field_validator("pwr_nets")
    @classmethod
    def normalize_nets(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for net in value:
            normalized = net.strip()
            if normalized and normalized.casefold() not in seen:
                result.append(normalized)
                seen.add(normalized.casefold())
        return result


class PlanePairSuggestion(DomainModel):
    rail_net: str
    pwr_layer: str
    gnd_layer: str
    pwr_index: int = Field(ge=0)
    gnd_index: int = Field(ge=0)
    separation_um: float = Field(ge=0)
    mixed_reference_certificate: MixedReferenceCertificate | None = None


class PinRecord(DomainModel):
    refdes: str = Field(min_length=1)
    pin: str = Field(min_length=1)
    net: str = Field(min_length=1)
    x_um: float
    y_um: float
    kind: PinKind
    terminal: TerminalKind
    domain: str | None = None
    site: str | None = None
    bump_group: str | None = None
    via_template_id: str | None = None

    @field_validator("x_um", "y_um")
    @classmethod
    def finite_pin_coordinates(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("pin coordinates must be finite")
        return value

    @property
    def pin_id(self) -> str:
        return f"{self.refdes}:{self.pin}"


class PlaneCell(DomainModel):
    cell_id: str = Field(min_length=1)
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    x_min_um: float
    x_max_um: float
    y_min_um: float
    y_max_um: float
    nominal_x_min_um: float | None = None
    nominal_x_max_um: float | None = None
    nominal_y_min_um: float | None = None
    nominal_y_max_um: float | None = None
    source_net: str | None = None
    source_geometry_asset: str | None = None
    source_geometry_sha256: str | None = None
    source_positive_polygons_um: list[list[tuple[float, float]]] = Field(
        default_factory=list
    )
    source_negative_polygons_um: list[list[tuple[float, float]]] = Field(
        default_factory=list
    )
    source_positive_circles_um: list[tuple[float, float, float]] = Field(
        default_factory=list
    )
    source_negative_circles_um: list[tuple[float, float, float]] = Field(
        default_factory=list
    )
    source_primitive_order: list[
        tuple[
            Literal[
                "positive_polygon",
                "negative_polygon",
                "positive_circle",
                "negative_circle",
            ],
            int,
        ]
    ] = Field(default_factory=list)
    source_positive_subelement_count: int = Field(default=0, ge=0)
    source_negative_subelement_count: int = Field(default=0, ge=0)
    solver_geometry: Literal[
        "active_bounds", "spd_axis_aligned_rectangle", "spd_bounding_box"
    ] = "active_bounds"

    @model_validator(mode="after")
    def ordered_bounds(self) -> "PlaneCell":
        values = (
            self.x_min_um,
            self.x_max_um,
            self.y_min_um,
            self.y_max_um,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("plane cell bounds must be finite")
        if self.x_max_um <= self.x_min_um or self.y_max_um <= self.y_min_um:
            raise ValueError("plane cell active area must be positive")
        nominal = (
            self.nominal_x_min_um,
            self.nominal_x_max_um,
            self.nominal_y_min_um,
            self.nominal_y_max_um,
        )
        if all(value is not None for value in nominal):
            nx0, nx1, ny0, ny1 = nominal
            assert nx0 is not None and nx1 is not None
            assert ny0 is not None and ny1 is not None
            if nx1 <= nx0 or ny1 <= ny0:
                raise ValueError("plane cell nominal area must be positive")
            if not (
                nx0 <= self.x_min_um <= self.x_max_um <= nx1
                and ny0 <= self.y_min_um <= self.y_max_um <= ny1
            ):
                raise ValueError("active cell must lie inside its nominal cell")
        elif any(value is not None for value in nominal):
            raise ValueError("all nominal bounds must be supplied together")
        for polygon in (
            *self.source_positive_polygons_um,
            *self.source_negative_polygons_um,
        ):
            if len(polygon) < 3:
                raise ValueError("source plane polygons require at least three vertices")
            if not all(
                len(point) == 2 and all(isfinite(float(value)) for value in point)
                for point in polygon
            ):
                raise ValueError("source plane polygon vertices must be finite X/Y pairs")
        for circle in (
            *self.source_positive_circles_um,
            *self.source_negative_circles_um,
        ):
            if (
                len(circle) != 3
                or not all(isfinite(float(value)) for value in circle)
                or float(circle[2]) <= 0
            ):
                raise ValueError(
                    "source plane circles require finite center X/Y and positive radius"
                )
        primitive_counts = {
            "positive_polygon": len(self.source_positive_polygons_um),
            "negative_polygon": len(self.source_negative_polygons_um),
            "positive_circle": len(self.source_positive_circles_um),
            "negative_circle": len(self.source_negative_circles_um),
        }
        ordered_refs = [(kind, int(index)) for kind, index in self.source_primitive_order]
        if any(
            index < 0 or index >= primitive_counts[kind]
            for kind, index in ordered_refs
        ):
            raise ValueError("source primitive order references an unknown primitive")
        if len(ordered_refs) != len(set(ordered_refs)):
            raise ValueError("source primitive order references a primitive more than once")
        expected_refs = {
            (kind, index)
            for kind, count in primitive_counts.items()
            for index in range(count)
        }
        if set(ordered_refs) != expected_refs:
            raise ValueError("source primitive order must reference every primitive exactly once")
        if self.solver_geometry.startswith("spd_"):
            if not self.source_net or not (
                self.source_positive_polygons_um
                or self.source_positive_circles_um
                or self.source_geometry_asset
            ):
                raise ValueError(
                    "SPD solver geometry requires a source net and positive primitive"
                )
        elif any(
            (
                self.source_positive_polygons_um,
                self.source_negative_polygons_um,
                self.source_positive_circles_um,
                self.source_negative_circles_um,
            )
        ):
            raise ValueError("source primitives require an SPD solver geometry mode")
        if bool(self.source_geometry_asset) != bool(self.source_geometry_sha256):
            raise ValueError(
                "source geometry asset and SHA-256 must be supplied together"
            )
        if self.source_geometry_asset and not self.solver_geometry.startswith("spd_"):
            raise ValueError("source geometry assets require an SPD solver geometry mode")
        if self.source_geometry_sha256 is not None and (
            len(self.source_geometry_sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in self.source_geometry_sha256)
        ):
            raise ValueError("source geometry SHA-256 must be a 64-character hex digest")
        return self

    def contains_point(self, x_um: float, y_um: float, *, nominal: bool = False) -> bool:
        if nominal and self.nominal_x_min_um is not None:
            assert self.nominal_x_max_um is not None
            assert self.nominal_y_min_um is not None
            assert self.nominal_y_max_um is not None
            return (
                self.nominal_x_min_um <= x_um <= self.nominal_x_max_um
                and self.nominal_y_min_um <= y_um <= self.nominal_y_max_um
            )
        return (
            self.x_min_um <= x_um <= self.x_max_um
            and self.y_min_um <= y_um <= self.y_max_um
        )


class PlanePartitionSpec(DomainModel):
    layer: str = Field(min_length=1)
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    domain_to_cell: dict[str, str]
    cells: list[PlaneCell]
    split_gap_um: float = Field(ge=0)
    confidence: ConfidenceLevel
    confidence_reason: str = ""
    confirmed: bool = False
    method: Literal["equal_grid", "recursive_bisection", "spd_actual"] = "equal_grid"
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_grid(self) -> "PlanePartitionSpec":
        expected = self.rows * self.columns
        if len(self.cells) != expected:
            raise ValueError(f"partition requires {expected} cells")
        cell_ids = [cell.cell_id for cell in self.cells]
        if len(cell_ids) != len(set(cell_ids)):
            raise ValueError("plane cell IDs must be unique")
        unknown = set(self.domain_to_cell.values()) - set(cell_ids)
        if unknown:
            raise ValueError(f"domain mapping references unknown cells: {unknown}")
        if len(self.domain_to_cell.values()) != len(set(self.domain_to_cell.values())):
            raise ValueError("each domain must map to a different cell")
        return self


class ImpedanceSample(DomainModel):
    frequency_hz: float = Field(gt=0)
    real_ohm: float
    imag_ohm: float

    @field_validator("real_ohm", "imag_ohm")
    @classmethod
    def finite_impedance(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("impedance values must be finite")
        return value


class CapModel(DomainModel):
    model_id: str = Field(min_length=1)
    impedance: list[ImpedanceSample] = Field(default_factory=list)
    capacitance_f: float | None = Field(default=None, gt=0)
    esr_ohm: float | None = Field(default=None, ge=0)
    esl_h: float | None = Field(default=None, ge=0)
    footprint: str = Field(min_length=1)
    inventory: int = Field(ge=0)
    source_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def normalized_model_available(self) -> "CapModel":
        if not self.impedance and self.capacitance_f is None:
            raise ValueError(
                "cap model requires normalized impedance samples or capacitance_f"
            )
        frequencies = [sample.frequency_hz for sample in self.impedance]
        if frequencies != sorted(frequencies) or len(frequencies) != len(
            set(frequencies)
        ):
            raise ValueError(
                "cap impedance frequencies must be strictly increasing and unique"
            )
        return self


class ViaLoopTemplate(DomainModel):
    template_id: str = Field(min_length=1)
    pwr_reference_layer: str = Field(min_length=1)
    gnd_reference_layer: str = Field(min_length=1)
    path_kind: ViaPathKind
    finite_port_width_um: float = Field(gt=0)
    finite_port_height_um: float = Field(gt=0)
    loop_resistance_ohm: float = Field(default=0.0, ge=0)
    loop_inductance_h: float = Field(default=0.0, ge=0)
    impedance: list[ImpedanceSample] = Field(default_factory=list)

    @field_validator("impedance")
    @classmethod
    def ordered_impedance(cls, value: list[ImpedanceSample]) -> list[ImpedanceSample]:
        frequencies = [sample.frequency_hz for sample in value]
        if frequencies != sorted(frequencies) or len(frequencies) != len(
            set(frequencies)
        ):
            raise ValueError(
                "via impedance frequencies must be strictly increasing and unique"
            )
        return value


class SharedPadViaPath(DomainModel):
    """One physical terminal-via path into a shared top-pad bus.

    ``via_template_id`` still names the calibrated *differential* PWR/GND loop
    available to the solver.  The shared-pad network splits that impedance
    symmetrically between explicit PWR and GND terminal branches, so every
    physical Via is retained once without inventing a nearest PWR/GND pair.
    """

    path_id: str = Field(min_length=1)
    terminal: TerminalKind
    x_um: float
    y_um: float
    via_template_id: str = Field(min_length=1)
    source_via_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("source_via_id", "source_power_via_id"),
    )
    # Raw-SPD imports may carry a source-proven landing on the selected plane.
    # The calibrated rail template remains the compatibility fallback whenever
    # this compact evidence is absent.  These are deliberately scalar values:
    # the transient evaluation project must not persist the raw Via graph.
    landing_layer: str | None = None
    landing_padstack: str | None = None
    landing_pad_width_um: float | None = Field(default=None, gt=0)
    landing_pad_height_um: float | None = Field(default=None, gt=0)
    terminal_resistance_ohm: float | None = Field(default=None, ge=0)
    terminal_inductance_h: float | None = Field(default=None, ge=0)
    terminal_provenance: str = "LEGACY_RAIL_TEMPLATE"
    conductor_model: str | None = None
    fill_provenance: str | None = None
    classification_basis: str | None = None
    effective_area_m2: float | None = Field(default=None, gt=0)

    @field_validator("x_um", "y_um")
    @classmethod
    def finite_path_coordinates(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("shared-pad via coordinates must be finite")
        return value

    @field_validator("source_via_id")
    @classmethod
    def nonblank_source_via_id(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("source via ID must not be blank")
        return value

    @model_validator(mode="after")
    def coherent_landing_evidence(self) -> "SharedPadViaPath":
        geometry = (self.landing_pad_width_um, self.landing_pad_height_um)
        if any(value is not None for value in geometry) and any(
            value is None for value in geometry
        ):
            raise ValueError("source landing width and height must be provided together")
        if any(value is not None for value in geometry) and not (
            self.landing_layer and self.landing_padstack
        ):
            raise ValueError(
                "source landing geometry requires its source layer and padstack"
            )
        terminal_rl = (self.terminal_resistance_ohm, self.terminal_inductance_h)
        if any(value is not None for value in terminal_rl) and any(
            value is None for value in terminal_rl
        ):
            raise ValueError("terminal Via resistance and inductance must be provided together")
        if not self.terminal_provenance.strip():
            raise ValueError("terminal Via provenance must not be blank")
        conductor_evidence = (
            self.conductor_model,
            self.fill_provenance,
            self.classification_basis,
            self.effective_area_m2,
        )
        if any(value is not None for value in conductor_evidence) and any(
            value is None for value in conductor_evidence
        ):
            raise ValueError("terminal Via conductor evidence must be provided together")
        return self

    @property
    def has_source_landing_geometry(self) -> bool:
        return (
            self.landing_pad_width_um is not None
            and self.landing_pad_height_um is not None
        )

    @property
    def has_source_terminal_rl(self) -> bool:
        return (
            self.terminal_resistance_ohm is not None
            and self.terminal_inductance_h is not None
        )


class SharedPadPowerComponentSpec(DomainModel):
    """One post-edit top-PWR supernode inside a source shared-pad cluster."""

    component_id: str = Field(min_length=1)
    member_slot_ids: list[str] = Field(min_length=1)
    power_path_ids: list[str] = Field(min_length=1)

    @field_validator("member_slot_ids", "power_path_ids")
    @classmethod
    def unique_nonblank_ids(cls, value: list[str]) -> list[str]:
        keys = [item.strip().casefold() for item in value]
        if any(not item for item in keys) or len(keys) != len(set(keys)):
            raise ValueError("shared-pad component IDs must be nonblank and unique")
        return value


class SharedPadGroundComponentSpec(DomainModel):
    """One post-edit top-GND supernode inside a source shared-pad cluster."""

    component_id: str = Field(min_length=1)
    member_slot_ids: list[str] = Field(min_length=1)
    ground_path_ids: list[str] = Field(min_length=1)

    @field_validator("member_slot_ids", "ground_path_ids")
    @classmethod
    def unique_nonblank_ids(cls, value: list[str]) -> list[str]:
        keys = [item.strip().casefold() for item in value]
        if any(not item for item in keys) or len(keys) != len(set(keys)):
            raise ValueError("shared-pad component IDs must be nonblank and unique")
        return value


class SharedPadCapacitorComponentSpec(DomainModel):
    """The physical PWR/GND top-bus pair for one shared-pad capacitor slot."""

    member_slot_id: str = Field(min_length=1)
    power_component_id: str = Field(min_length=1)
    ground_component_id: str = Field(min_length=1)


class SharedPadClusterSpec(DomainModel):
    """One rail's source-derived PWR/GND shared-pad component topology.

    ``ground_components`` and ``capacitor_component_mappings`` were added after
    the original one-GND representation.  Empty collections retain that legacy
    implicit common-GND meaning so normalized projects written by older releases
    remain readable and numerically identical.
    """

    cluster_id: str = Field(min_length=1)
    rail_id: str = Field(min_length=1)
    member_slot_ids: list[str] = Field(min_length=1)
    via_paths: list[SharedPadViaPath] = Field(default_factory=list)
    power_components: list[SharedPadPowerComponentSpec] = Field(min_length=1)
    ground_components: list[SharedPadGroundComponentSpec] = Field(default_factory=list)
    capacitor_component_mappings: list[SharedPadCapacitorComponentSpec] = Field(
        default_factory=list
    )

    @field_validator("member_slot_ids")
    @classmethod
    def unique_member_slots(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("shared-pad member slot IDs must not be blank")
        folded = [item.casefold() for item in value]
        if len(folded) != len(set(folded)):
            raise ValueError("shared-pad member slot IDs must be unique")
        return value

    @field_validator("via_paths")
    @classmethod
    def unique_via_paths(
        cls, value: list[SharedPadViaPath]
    ) -> list[SharedPadViaPath]:
        folded = [item.path_id.casefold() for item in value]
        if len(folded) != len(set(folded)):
            raise ValueError("shared-pad via path IDs must be unique")
        terminals = {item.terminal for item in value}
        if terminals != {TerminalKind.PWR, TerminalKind.GND}:
            raise ValueError(
                "a connected shared-pad cluster requires both PWR and GND via paths"
            )
        return value

    @model_validator(mode="after")
    def complete_power_component_partition(self) -> "SharedPadClusterSpec":
        component_keys = [item.component_id.casefold() for item in self.power_components]
        if len(component_keys) != len(set(component_keys)):
            raise ValueError("shared-pad power component IDs must be unique")

        member_keys = [item.casefold() for item in self.member_slot_ids]
        component_members = [
            slot_id.casefold()
            for component in self.power_components
            for slot_id in component.member_slot_ids
        ]
        if (
            len(component_members) != len(set(component_members))
            or set(component_members) != set(member_keys)
        ):
            raise ValueError(
                "shared-pad power components must partition every member slot"
            )

        path_by_key = {item.path_id.casefold(): item for item in self.via_paths}
        power_path_keys = {
            key
            for key, path in path_by_key.items()
            if path.terminal == TerminalKind.PWR
        }
        component_paths = [
            path_id.casefold()
            for component in self.power_components
            for path_id in component.power_path_ids
        ]
        unknown_paths = set(component_paths) - set(path_by_key)
        if unknown_paths:
            raise ValueError(
                "shared-pad power component references an unknown via path"
            )
        if (
            len(component_paths) != len(set(component_paths))
            or set(component_paths) != power_path_keys
        ):
            raise ValueError(
                "shared-pad power components must partition every PWR via path"
            )
        if any(
            path_by_key[path_key].terminal != TerminalKind.PWR
            for path_key in component_paths
        ):
            raise ValueError("shared-pad components can reference only PWR via paths")

        # V4 persisted projects have no explicit ground partition.  Preserve the
        # former single common-GND topology verbatim in that case.
        if not self.ground_components and not self.capacitor_component_mappings:
            return self
        if not self.ground_components:
            raise ValueError(
                "shared-pad capacitor mappings require explicit GND components"
            )
        ground_component_keys = [
            item.component_id.casefold() for item in self.ground_components
        ]
        if len(ground_component_keys) != len(set(ground_component_keys)):
            raise ValueError("shared-pad GND component IDs must be unique")
        ground_members = [
            slot_id.casefold()
            for component in self.ground_components
            for slot_id in component.member_slot_ids
        ]
        if len(ground_members) != len(set(ground_members)) or set(ground_members) != set(
            member_keys
        ):
            raise ValueError(
                "shared-pad GND components must partition every member slot"
            )
        ground_path_keys = {
            key
            for key, path in path_by_key.items()
            if path.terminal == TerminalKind.GND
        }
        component_ground_paths = [
            path_id.casefold()
            for component in self.ground_components
            for path_id in component.ground_path_ids
        ]
        unknown_ground_paths = set(component_ground_paths) - set(path_by_key)
        if unknown_ground_paths:
            raise ValueError(
                "shared-pad GND component references an unknown via path"
            )
        if (
            len(component_ground_paths) != len(set(component_ground_paths))
            or set(component_ground_paths) != ground_path_keys
        ):
            raise ValueError(
                "shared-pad GND components must partition every GND via path"
            )
        if any(
            path_by_key[path_key].terminal != TerminalKind.GND
            for path_key in component_ground_paths
        ):
            raise ValueError("shared-pad GND components can reference only GND via paths")

        mappings = self.capacitor_component_mappings
        if not mappings:
            raise ValueError(
                "explicit shared-pad GND components require capacitor mappings"
            )
        mapping_by_member = {
            item.member_slot_id.casefold(): item for item in mappings
        }
        if len(mapping_by_member) != len(mappings) or set(mapping_by_member) != set(
            member_keys
        ):
            raise ValueError(
                "shared-pad capacitor mappings must cover every member exactly once"
            )
        power_component_by_key = {
            item.component_id.casefold(): item for item in self.power_components
        }
        ground_component_by_key = {
            item.component_id.casefold(): item for item in self.ground_components
        }
        for member_key, mapping in mapping_by_member.items():
            power = power_component_by_key.get(mapping.power_component_id.casefold())
            ground = ground_component_by_key.get(mapping.ground_component_id.casefold())
            if power is None or ground is None:
                raise ValueError(
                    "shared-pad capacitor mapping references an unknown component"
                )
            if member_key not in {
                item.casefold() for item in power.member_slot_ids
            } or member_key not in {
                item.casefold() for item in ground.member_slot_ids
            }:
                raise ValueError(
                    "shared-pad capacitor mapping must use its member's PWR/GND components"
                )
        return self


class TopologyMap(DomainModel):
    slot_id: str = Field(min_length=1)
    x_um: float
    y_um: float
    allowed_rail_ids: list[str]
    allowed_footprints: list[str] = Field(default_factory=list)
    topology: TopologyKind
    zone: str = "MID"
    via_template_id: str | None = None
    horizontal_template_id: str | None = None
    anchor_slot_id: str | None = None
    satellite_slot_id: str | None = None
    cluster_id: str | None = None

    @field_validator("x_um", "y_um")
    @classmethod
    def finite_slot_coordinates(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("slot coordinates must be finite")
        return value

    @field_validator("allowed_rail_ids")
    @classmethod
    def allowed_rails_are_unique(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a slot must allow at least one rail")
        if len(value) != len(set(value)):
            raise ValueError("allowed_rail_ids must be unique")
        return value

    @field_validator("allowed_footprints")
    @classmethod
    def allowed_footprints_are_normalized(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("allowed_footprints cannot contain blank values")
        folded = [item.casefold() for item in normalized]
        if len(folded) != len(set(folded)):
            raise ValueError("allowed_footprints must be unique")
        return normalized

    @model_validator(mode="after")
    def supported_topology(self) -> "TopologyMap":
        if self.topology == TopologyKind.EMPTY:
            raise ValueError("TopologyMap describes usable slots, not EMPTY assignments")
        if self.topology == TopologyKind.SHARED_PAD_CLUSTER:
            if self.cluster_id is None:
                raise ValueError(
                    "SHARED_PAD_CLUSTER topology requires cluster_id"
                )
            if any(
                value is not None
                for value in (
                    self.via_template_id,
                    self.horizontal_template_id,
                    self.anchor_slot_id,
                    self.satellite_slot_id,
                )
            ):
                raise ValueError(
                    "SHARED_PAD_CLUSTER member topology cannot own via, horizontal, "
                    "anchor, or satellite mappings"
                )
        elif self.topology == TopologyKind.SHARED_PAIR:
            if self.via_template_id is None:
                raise ValueError("usable topology requires via_template_id")
            partner_fields = int(self.anchor_slot_id is not None) + int(
                self.satellite_slot_id is not None
            )
            if partner_fields != 1:
                raise ValueError(
                    "SHARED_PAIR topology requires exactly one of anchor_slot_id "
                    "or satellite_slot_id"
                )
            if self.satellite_slot_id is not None and self.horizontal_template_id is None:
                raise ValueError(
                    "SHARED_PAIR anchor requires horizontal_template_id"
                )
            if self.cluster_id is not None:
                raise ValueError("cluster_id is only valid for SHARED_PAD_CLUSTER")
        else:
            if self.via_template_id is None:
                raise ValueError("usable topology requires via_template_id")
            if self.anchor_slot_id is not None or self.satellite_slot_id is not None:
                raise ValueError(
                    "anchor_slot_id/satellite_slot_id are only valid for "
                    "SHARED_PAIR topology"
                )
            if self.cluster_id is not None:
                raise ValueError("cluster_id is only valid for SHARED_PAD_CLUSTER")
        return self


class PlacementAssignment(DomainModel):
    slot_id: str = Field(min_length=1)
    topology: TopologyKind = TopologyKind.EMPTY
    rail_id: str | None = None
    cap_model_id: str | None = None

    @model_validator(mode="after")
    def complete_or_empty(self) -> "PlacementAssignment":
        if self.topology == TopologyKind.EMPTY:
            if self.rail_id is not None or self.cap_model_id is not None:
                raise ValueError("EMPTY assignment cannot specify rail or cap model")
        elif self.rail_id is None or self.cap_model_id is None:
            raise ValueError("non-empty assignment requires rail_id and cap_model_id")
        return self


class ConfidenceBand(DomainModel):
    category: ConfidenceCategory
    start_hz: float = Field(gt=0)
    stop_hz: float = Field(gt=0)
    level: ConfidenceLevel
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_band(self) -> "ConfidenceBand":
        if self.stop_hz <= self.start_hz:
            raise ValueError("confidence stop_hz must exceed start_hz")
        return self


class PeakMetric(DomainModel):
    peak_id: str = Field(min_length=1)
    frequency_hz: float = Field(gt=0)
    impedance_ohm: float = Field(ge=0)
    prominence_db: float = Field(ge=0)


class EvaluationMetrics(DomainModel):
    max_violation_db: float = Field(ge=0)
    rms_violation_db: float = Field(ge=0)
    max_peak_prominence_db: float = Field(ge=0)
    target_met: bool


class EvaluationResult(DomainModel):
    schema_version: str = SCHEMA_VERSION
    solver_version: str
    rail_id: str
    frequencies_hz: list[float]
    z_real_ohm: list[float]
    z_imag_ohm: list[float]
    metrics: EvaluationMetrics
    peaks: list[PeakMetric] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: list[ConfidenceBand] = Field(default_factory=list)

    @model_validator(mode="after")
    def aligned_samples(self) -> "EvaluationResult":
        length = len(self.frequencies_hz)
        if length < 2:
            raise ValueError("evaluation requires at least two frequency points")
        if len(self.z_real_ohm) != length or len(self.z_imag_ohm) != length:
            raise ValueError("frequency and impedance arrays must have equal lengths")
        if self.frequencies_hz != sorted(self.frequencies_hz) or len(
            self.frequencies_hz
        ) != len(set(self.frequencies_hz)):
            raise ValueError("evaluation frequencies must be increasing and unique")
        if not all(value > 0 and isfinite(value) for value in self.frequencies_hz):
            raise ValueError("evaluation frequencies must be positive and finite")
        if not all(isfinite(value) for value in self.z_real_ohm + self.z_imag_ohm):
            raise ValueError("evaluation impedance arrays must be finite")
        return self


class ProjectSpec(DomainModel):
    schema_version: str = SCHEMA_VERSION
    app_version: str = __version__
    name: str = Field(default="Untitled MLO PDN Project", min_length=1)
    outline: MLOOutline
    coordinate_transform: CoordinateTransform = Field(
        default_factory=CoordinateTransform
    )
    gnd_aliases: list[str] = Field(default_factory=lambda: ["DGND", "GND"])
    split_gap_um: float = Field(ge=0)
    frequency: FrequencySettings = Field(default_factory=FrequencySettings)
    stackup_layers: list[StackupLayer] = Field(default_factory=list)
    rails: list[RailSpec] = Field(default_factory=list)
    pins: list[PinRecord] = Field(default_factory=list)
    partitions: list[PlanePartitionSpec] = Field(default_factory=list)
    cap_models: list[CapModel] = Field(default_factory=list)
    via_templates: list[ViaLoopTemplate] = Field(default_factory=list)
    topology_maps: list[TopologyMap] = Field(default_factory=list)
    shared_pad_clusters: list[SharedPadClusterSpec] = Field(default_factory=list)
    placements: list[PlacementAssignment] = Field(default_factory=list)
    assumptions: list[str] = Field(
        default_factory=lambda: ["inter-rail/site coupling not modeled"]
    )
    attachment_names: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def supported_schema_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported project schema version {value!r}; expected {SCHEMA_VERSION!r}"
            )
        return value

    @field_validator("gnd_aliases")
    @classmethod
    def usable_gnd_aliases(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for alias in value:
            key = alias.strip().casefold()
            if key and key not in seen:
                normalized.append(alias.strip())
                seen.add(key)
        if not normalized:
            raise ValueError("at least one GND alias is required")
        return normalized

    @model_validator(mode="after")
    def unique_object_ids(self) -> "ProjectSpec":
        collections: tuple[tuple[str, list[str]], ...] = (
            ("stackup layer", [item.name for item in self.stackup_layers]),
            ("rail", [item.rail_id for item in self.rails]),
            ("pin", [item.pin_id for item in self.pins]),
            ("cap model", [item.model_id for item in self.cap_models]),
            ("via template", [item.template_id for item in self.via_templates]),
            ("topology slot", [item.slot_id for item in self.topology_maps]),
            (
                "shared-pad cluster",
                [item.cluster_id for item in self.shared_pad_clusters],
            ),
            ("placement slot", [item.slot_id for item in self.placements]),
            ("attachment", self.attachment_names),
        )
        for label, identifiers in collections:
            folded = [identifier.casefold() for identifier in identifiers]
            if len(folded) != len(set(folded)):
                raise ValueError(f"duplicate {label} identifier")
        layer_by_name = {item.name: item for item in self.stackup_layers}
        rail_by_id = {item.rail_id: item for item in self.rails}
        cap_by_id = {item.model_id: item for item in self.cap_models}
        via_by_id = {item.template_id: item for item in self.via_templates}
        topology_by_slot = {item.slot_id: item for item in self.topology_maps}
        cluster_by_id = {
            item.cluster_id: item for item in self.shared_pad_clusters
        }

        for rail in self.rails:
            for role, layer_name in (("PWR", rail.pwr_layer), ("DGND", rail.gnd_layer)):
                layer = layer_by_name.get(layer_name)
                if layer is None:
                    raise ValueError(
                        f"rail {rail.rail_id!r} references unknown {role} layer {layer_name!r}"
                    )
                if not layer.is_conductor:
                    raise ValueError(
                        f"rail {rail.rail_id!r} {role} layer {layer_name!r} is not a conductor"
                    )
            pwr_layer = layer_by_name[rail.pwr_layer]
            pwr_keys = {item.casefold() for item in pwr_layer.pwr_nets}
            if pwr_keys and rail.net.casefold() not in pwr_keys:
                raise ValueError(
                    f"rail {rail.rail_id!r} net {rail.net!r} is absent from "
                    f"PWR layer {rail.pwr_layer!r} PWR_NET"
                )
            gnd_layer = layer_by_name[rail.gnd_layer]
            gnd_keys = {item.casefold() for item in gnd_layer.pwr_nets}
            aliases = {item.casefold() for item in self.gnd_aliases}
            pure_ground = bool(gnd_keys) and gnd_keys.issubset(aliases)
            certificate = rail.mixed_reference_certificate
            witness = rail.mixed_reference_ground_witness
            if pure_ground and certificate is not None:
                raise ValueError(
                    f"rail {rail.rail_id!r} has an unnecessary mixed-reference certificate"
                )
            if not pure_ground and certificate is None:
                raise ValueError(
                    f"rail {rail.rail_id!r} DGND layer {rail.gnd_layer!r} "
                    "must contain only configured GND aliases or a valid mixed-reference certificate"
                )
            if certificate is not None:
                if (
                    certificate.rail_net.casefold() != rail.net.casefold()
                    or certificate.pwr_layer.casefold() != rail.pwr_layer.casefold()
                    or certificate.gnd_layer.casefold() != rail.gnd_layer.casefold()
                ):
                    raise ValueError(
                        f"rail {rail.rail_id!r} mixed-reference certificate does not bind its selected pair"
                    )
                if certificate.gnd_net.casefold() not in aliases:
                    raise ValueError(
                        f"rail {rail.rail_id!r} certificate ground net is not a configured alias"
                    )
                if certificate.gnd_net.casefold() not in gnd_keys:
                    raise ValueError(
                        f"rail {rail.rail_id!r} certificate ground net is absent from its DGND layer"
                    )
                if rail.net.casefold() in gnd_keys:
                    raise ValueError(
                        f"rail {rail.rail_id!r} target rail cannot appear on its mixed DGND layer"
                    )
                if not any(alias in gnd_layer.name.casefold() for alias in aliases):
                    raise ValueError(
                        f"rail {rail.rail_id!r} mixed DGND layer must have an explicit ground-like name"
                    )
                if (
                    certificate.overlap_fraction < MIXED_REFERENCE_MIN_COVERAGE
                    or certificate.dominant_overlap_component_fraction
                    < MIXED_REFERENCE_MIN_DOMINANT_COMPONENT
                ):
                    raise ValueError(
                        f"rail {rail.rail_id!r} mixed-reference certificate is below fail-closed coverage thresholds"
                    )
                spd_import = self.metadata.get("spd_import")
                records = (
                    spd_import.get("plane_geometries")
                    if isinstance(spd_import, dict)
                    else None
                )
                if not isinstance(records, list):
                    raise ValueError(
                        f"rail {rail.rail_id!r} certificate requires retained SPD geometry index"
                    )
                def asset_match(net: str, layer_name: str, digest: str) -> list[dict[str, Any]]:
                    return [
                        item for item in records if isinstance(item, dict)
                        and str(item.get("net", "")).casefold() == net.casefold()
                        and str(item.get("layer", "")).casefold() == layer_name.casefold()
                        and str(item.get("asset_sha256", "")) == digest
                    ]
                pwr_assets = asset_match(rail.net, rail.pwr_layer, certificate.pwr_asset_sha256)
                gnd_assets = asset_match(certificate.gnd_net, rail.gnd_layer, certificate.gnd_asset_sha256)
                if len(pwr_assets) != 1 or len(gnd_assets) != 1:
                    raise ValueError(
                        f"rail {rail.rail_id!r} certificate asset binding is missing or ambiguous"
                    )
                attachment_keys = {name.casefold() for name in self.attachment_names}
                for item in (*pwr_assets, *gnd_assets):
                    asset = str(item.get("asset", ""))
                    if asset.casefold() not in attachment_keys:
                        raise ValueError(
                            f"rail {rail.rail_id!r} certificate geometry asset is not attached"
                        )
            if witness is not None:
                if certificate is None:
                    raise ValueError(
                        f"rail {rail.rail_id!r} has a mixed-reference ground witness without a certificate"
                    )
                if (
                    witness.rail_net.casefold() != rail.net.casefold()
                    or witness.gnd_net.casefold() != certificate.gnd_net.casefold()
                    or witness.pwr_layer.casefold() != rail.pwr_layer.casefold()
                    or witness.gnd_layer.casefold() != rail.gnd_layer.casefold()
                    or witness.gnd_asset_sha256 != certificate.gnd_asset_sha256
                ):
                    raise ValueError(
                        f"rail {rail.rail_id!r} mixed-reference ground witness does not bind its selected pair"
                    )

        for template in self.via_templates:
            if template.pwr_reference_layer not in layer_by_name:
                raise ValueError(
                    f"via template {template.template_id!r} references unknown PWR layer "
                    f"{template.pwr_reference_layer!r}"
                )
            if template.gnd_reference_layer not in layer_by_name:
                raise ValueError(
                    f"via template {template.template_id!r} references unknown DGND layer "
                    f"{template.gnd_reference_layer!r}"
                )

        rail_domains_by_layer: dict[str, set[str]] = {}
        for rail in self.rails:
            rail_domains_by_layer.setdefault(rail.pwr_layer, set()).add(rail.domain)
        for partition in self.partitions:
            if partition.layer not in layer_by_name:
                raise ValueError(
                    f"partition references unknown conductor layer {partition.layer!r}"
                )
            if not layer_by_name[partition.layer].is_conductor:
                raise ValueError(f"partition layer {partition.layer!r} is not a conductor")
            if abs(partition.split_gap_um - self.split_gap_um) > 1e-9:
                raise ValueError(
                    f"partition {partition.layer!r} split gap disagrees with project split gap"
                )
            unknown_domains = set(partition.domain_to_cell) - rail_domains_by_layer.get(
                partition.layer, set()
            )
            if unknown_domains:
                raise ValueError(
                    f"partition {partition.layer!r} maps unknown rail domains: "
                    f"{sorted(unknown_domains)}"
                )

        for topology in self.topology_maps:
            unknown_rails = set(topology.allowed_rail_ids) - set(rail_by_id)
            if unknown_rails:
                raise ValueError(
                    f"slot {topology.slot_id!r} allows unknown rails: {sorted(unknown_rails)}"
                )
            if (
                topology.via_template_id is not None
                and topology.via_template_id not in via_by_id
            ):
                raise ValueError(
                    f"slot {topology.slot_id!r} references unknown via template "
                    f"{topology.via_template_id!r}"
                )
            if (
                topology.horizontal_template_id is not None
                and topology.horizontal_template_id not in via_by_id
            ):
                raise ValueError(
                    f"slot {topology.slot_id!r} references unknown horizontal template "
                    f"{topology.horizontal_template_id!r}"
                )
            partner_id = topology.anchor_slot_id or topology.satellite_slot_id
            if topology.topology == TopologyKind.SHARED_PAIR:
                partner = topology_by_slot.get(str(partner_id))
                if partner is None:
                    raise ValueError(
                        f"SHARED_PAIR slot {topology.slot_id!r} references unknown partner "
                        f"{partner_id!r}"
                    )
                if partner.topology != TopologyKind.SHARED_PAIR:
                    raise ValueError(
                        f"SHARED_PAIR partner {partner.slot_id!r} has incompatible topology"
                    )
                reciprocal = partner.anchor_slot_id or partner.satellite_slot_id
                if reciprocal != topology.slot_id:
                    raise ValueError(
                        f"SHARED_PAIR slots {topology.slot_id!r}/{partner.slot_id!r} "
                        "must reference each other"
                    )
                if topology.anchor_slot_id is not None:
                    complementary = partner.satellite_slot_id == topology.slot_id
                else:
                    complementary = partner.anchor_slot_id == topology.slot_id
                if not complementary:
                    raise ValueError(
                        f"SHARED_PAIR slots {topology.slot_id!r}/{partner.slot_id!r} "
                        "must define complementary anchor and satellite roles"
                    )

        clustered_slots: set[str] = set()
        clustered_path_ids: set[str] = set()
        clustered_source_via_ids: set[str] = set()
        for cluster in self.shared_pad_clusters:
            rail = rail_by_id.get(cluster.rail_id)
            if rail is None:
                raise ValueError(
                    f"shared-pad cluster {cluster.cluster_id!r} references unknown "
                    f"rail {cluster.rail_id!r}"
                )
            for slot_id in cluster.member_slot_ids:
                if slot_id in clustered_slots:
                    raise ValueError(
                        f"shared-pad slot {slot_id!r} belongs to more than one cluster"
                    )
                topology = topology_by_slot.get(slot_id)
                if topology is None:
                    raise ValueError(
                        f"shared-pad cluster {cluster.cluster_id!r} references "
                        f"unknown member slot {slot_id!r}"
                    )
                if topology.topology != TopologyKind.SHARED_PAD_CLUSTER:
                    raise ValueError(
                        f"shared-pad member {slot_id!r} has incompatible topology"
                    )
                if topology.cluster_id != cluster.cluster_id:
                    raise ValueError(
                        f"shared-pad member {slot_id!r} does not reference cluster "
                        f"{cluster.cluster_id!r}"
                    )
                if cluster.rail_id not in topology.allowed_rail_ids:
                    raise ValueError(
                        f"shared-pad cluster rail {cluster.rail_id!r} is not allowed "
                        f"at member slot {slot_id!r}"
                    )
                clustered_slots.add(slot_id)
            for path in cluster.via_paths:
                path_key = path.path_id.casefold()
                if path_key in clustered_path_ids:
                    raise ValueError(
                        f"shared-pad via path {path.path_id!r} is reused across clusters"
                    )
                clustered_path_ids.add(path_key)
                if path.source_via_id is not None:
                    source_key = path.source_via_id.casefold()
                    if source_key in clustered_source_via_ids:
                        raise ValueError(
                            f"source via {path.source_via_id!r} is reused "
                            "across shared-pad paths"
                        )
                    clustered_source_via_ids.add(source_key)
                template = via_by_id.get(path.via_template_id)
                if template is None:
                    raise ValueError(
                        f"shared-pad via path {path.path_id!r} references unknown "
                        f"via template {path.via_template_id!r}"
                    )
                if (
                    template.pwr_reference_layer != rail.pwr_layer
                    or template.gnd_reference_layer != rail.gnd_layer
                ):
                    raise ValueError(
                        f"shared-pad via path {path.path_id!r} reference layers do "
                        f"not match cluster rail {cluster.rail_id!r}"
                    )

        for topology in self.topology_maps:
            if topology.topology != TopologyKind.SHARED_PAD_CLUSTER:
                continue
            if topology.cluster_id not in cluster_by_id:
                raise ValueError(
                    f"shared-pad slot {topology.slot_id!r} references unknown cluster "
                    f"{topology.cluster_id!r}"
                )
            if topology.slot_id not in clustered_slots:
                raise ValueError(
                    f"shared-pad slot {topology.slot_id!r} is absent from its cluster "
                    "member list"
                )

        for placement in self.placements:
            topology = topology_by_slot.get(placement.slot_id)
            if topology is None:
                raise ValueError(
                    f"placement references unknown slot {placement.slot_id!r}"
                )
            if placement.topology != topology.topology:
                raise ValueError(
                    f"placement {placement.slot_id!r} topology {placement.topology.value} "
                    f"does not match slot topology {topology.topology.value}"
                )
            if placement.topology == TopologyKind.EMPTY:
                continue
            if placement.rail_id not in rail_by_id:
                raise ValueError(
                    f"placement {placement.slot_id!r} references unknown rail "
                    f"{placement.rail_id!r}"
                )
            if placement.rail_id not in topology.allowed_rail_ids:
                raise ValueError(
                    f"placement rail {placement.rail_id!r} is not allowed at "
                    f"slot {placement.slot_id!r}"
                )
            if placement.cap_model_id not in cap_by_id:
                raise ValueError(
                    f"placement {placement.slot_id!r} references unknown cap model "
                    f"{placement.cap_model_id!r}"
                )
            cap = cap_by_id[placement.cap_model_id]
            if (
                topology.allowed_footprints
                and cap.footprint.casefold()
                not in {item.casefold() for item in topology.allowed_footprints}
            ):
                raise ValueError(
                    f"placement {placement.slot_id!r} footprint {cap.footprint!r} "
                    "is not allowed by the slot topology"
                )

        inventory_usage = Counter(
            str(item.cap_model_id)
            for item in self.placements
            if item.topology != TopologyKind.EMPTY and item.cap_model_id is not None
        )
        for model_id, used in inventory_usage.items():
            if used > cap_by_id[model_id].inventory:
                raise ValueError(
                    f"placements use {used} of cap model {model_id!r}, exceeding "
                    f"inventory {cap_by_id[model_id].inventory}"
                )

        placement_by_slot = {item.slot_id: item for item in self.placements}
        for placement in self.placements:
            if placement.topology != TopologyKind.SHARED_PAIR:
                continue
            topology = topology_by_slot[placement.slot_id]
            partner_id = str(topology.anchor_slot_id or topology.satellite_slot_id)
            partner = placement_by_slot.get(partner_id)
            if partner is None:
                raise ValueError(
                    f"SHARED_PAIR placement {placement.slot_id!r} requires partner "
                    f"assignment {partner_id!r}"
                )
            if partner.rail_id != placement.rail_id:
                raise ValueError("SHARED_PAIR members must be assigned to the same rail")

        for cluster in self.shared_pad_clusters:
            for slot_id in cluster.member_slot_ids:
                placement = placement_by_slot.get(slot_id)
                if placement is not None and placement.rail_id != cluster.rail_id:
                    raise ValueError(
                        f"shared-pad member {slot_id!r} must be assigned to cluster "
                        f"rail {cluster.rail_id!r}"
                    )

        for pin in self.pins:
            if pin.via_template_id is not None and pin.via_template_id not in via_by_id:
                raise ValueError(
                    f"pin {pin.pin_id!r} references unknown via template "
                    f"{pin.via_template_id!r}"
                )
        return self


__all__ = [
    "SCHEMA_VERSION",
    "CapModel",
    "ConfidenceBand",
    "ConfidenceCategory",
    "ConfidenceLevel",
    "CoordinateTransform",
    "CoordinateUnit",
    "DomainModel",
    "EvaluationMetrics",
    "EvaluationResult",
    "FrequencySettings",
    "ImpedanceSample",
    "MLOOutline",
    "MixedReferenceCertificate",
    "MixedReferenceGroundWitness",
    "PeakMetric",
    "PinKind",
    "PinRecord",
    "PlacementAssignment",
    "PlaneCell",
    "PlanePairSuggestion",
    "PlanePartitionSpec",
    "Point2D",
    "ProjectSpec",
    "RailSpec",
    "RailState",
    "SharedPadCapacitorComponentSpec",
    "SharedPadClusterSpec",
    "SharedPadGroundComponentSpec",
    "SharedPadPowerComponentSpec",
    "SharedPadViaPath",
    "StackupLayer",
    "TargetPoint",
    "TerminalKind",
    "TopologyKind",
    "TopologyMap",
    "ViaLoopTemplate",
    "ViaPathKind",
]
