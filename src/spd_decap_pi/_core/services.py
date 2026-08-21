"""Standalone SPD import, decap-model, evaluation, and plot-analysis services."""

from __future__ import annotations

import json
import math
import os
import re
from copy import deepcopy
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable, Mapping, Sequence
import zlib
import numpy as np
from ..canonical_json import iter_canonical_json_bytes
from .ai import AssistantSource, EvidenceKind, FeatureEvidence, LocalLLMClient, LocalLLMConfig, PlotFeatures, local_llm_endpoint_requires_remote_access
from .geometry.ordered_boolean import ordered_spd_geometry
from .domain import CapModel, CoordinateTransform, CoordinateUnit, FrequencySettings, ImpedanceSample, MLOOutline, MIXED_REFERENCE_MIN_COVERAGE, MIXED_REFERENCE_MIN_DOMINANT_COMPONENT, MixedReferenceCertificate, PinKind, PlaneCell, PlanePairSuggestion, PlacementAssignment as DomainPlacement, PlanePartitionSpec, ProjectSpec, RailSpec, RailState, StackupLayer, TargetPoint, TerminalKind, TopologyKind, TopologyMap, ViaLoopTemplate, ViaPathKind
from .models import PassiveSubcircuitModel, parse_passive_subcircuit, passive_subcircuit_names
from .plane_pairs import suggest_effective_plane_pairs
from .solver.evaluator import (
    DEFAULT_MODAL_CEILING_INDEX,
    DEFAULT_MAX_NEW_FREQUENCY_POINTS,
    DEFAULT_MAX_REFINEMENT_ITERATIONS,
    EvaluationOutcome,
    compile_project_evaluation_template,
    evaluate_project_rail_converged,
)
from .solver.profiles import (
    DEFAULT_SOLVER_PROFILE_KEY,
    LAYERWISE_ADMITTANCE_PROFILE,
    RESEARCH_UNIFORM_ADMITTANCE_PROFILE,
    solver_profile as resolve_solver_profile,
)
from .via_model import (
    COPPER_CONDUCTIVITY_S_PER_M,
    ViaModelError,
    ViaSegmentElectricalModel,
    estimate_via_segment_rl,
)
from .version import __version__

try:
    from threadpoolctl import threadpool_limits
except ImportError:  # pragma: no cover - packaging declares the dependency
    threadpool_limits: Any = None

ProgressCallback = Callable[[int, str], None]

CancelCallback = Callable[[], bool]

_CAP_MODEL_SOURCES_KEY = "cap_model_sources"

_BLAS_THREAD_OVERRIDE = "SPD_DECAP_PI_BLAS_THREADS"
_BLAS_THREADPOOL_LOCK = RLock()


@contextmanager
def scoped_blas_threads() -> Iterator[None]:
    """Limit unmanaged BLAS pools for one numerical operation.

    The desktop evaluator runs one background job at a time.  A library pool
    that consumes every core can starve the Qt event loop, while a single BLAS
    thread is faster for this application's repeated small dense solves.
    Default to one thread even when a generic backend environment variable is
    present.  ``SPD_DECAP_PI_BLAS_THREADS`` may set a positive limit, while
    ``auto``/``inherit`` explicitly opts out.  The process-wide lock prevents
    overlapping threadpoolctl contexts from restoring another job's setting.
    """

    requested = _requested_blas_thread_limit()
    if requested is None or threadpool_limits is None:
        yield None
        return
    with _BLAS_THREADPOOL_LOCK:
        with threadpool_limits(limits=requested, user_api="blas"):
            yield None


def _requested_blas_thread_limit() -> int | None:
    override = os.environ.get(_BLAS_THREAD_OVERRIDE)
    if override is not None:
        if override.strip().casefold() in {"auto", "inherit"}:
            return None
        try:
            value = int(override)
        except ValueError:
            return 1
        return value if value > 0 else 1
    return 1

_SPD_PLANE_GEOMETRIES_KEY = "plane_geometries"

_SPD_LAYERWISE_VIA_GROUPS_KEY = "layerwise_via_group_certificate"
_SPD_LAYERWISE_VIA_GROUP_SCHEMA = "spd-layerwise-via-groups-v2"
_SPD_LAYERWISE_VIA_GROUP_COMPILER = (
    "powersi-via-usage-padstack-terminal-ownership-v2"
)

_SPD_LAYERWISE_DEVICE_TERMINAL_VIAS_KEY = (
    "layerwise_device_terminal_via_certificate"
)
_SPD_LAYERWISE_DEVICE_TERMINAL_VIA_SCHEMA = (
    "spd-layerwise-device-terminal-vias-v1"
)
_SPD_LAYERWISE_DEVICE_TERMINAL_VIA_COMPILER = (
    "powersi-direct-device-top-via-v1"
)

_SPD_GEOMETRY_MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
# Level 6 preserves nearly all of level 9's storage saving for real SPD artwork,
# while materially reducing time on the background import path.  The compressed
# bytes remain the versioned asset identity (SHA-256), rather than treating a
# decoded JSON equivalence as interchangeable content.
_SPD_GEOMETRY_COMPRESSION_LEVEL = 6

class EvaluationReadinessError(ValueError):
    """Actionable project prerequisite failure raised before a solver starts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _SpdGeometryAssetTooLarge(ValueError):
    """Raised before an import creates artwork that its decoder rejects."""

@dataclass(frozen=True, slots=True)
class LocalAIAnalysisView:
    """Display text plus the validated source of one optional AI request."""

    text: str
    source: AssistantSource
    fallback_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.source is AssistantSource.LOCAL_LLM
            and self.fallback_reason is None
        )

@dataclass(frozen=True, slots=True)
class EvaluationModalPreset:
    """Shared run preset; modal order applies only to Legacy/Research paths."""

    key: str
    label: str
    max_index: int
    mode_count: int
    description: str

EVALUATION_MODAL_PRESETS: tuple[EvaluationModalPreset, ...] = (
    EvaluationModalPreset(
        key="fast",
        label="Fast",
        max_index=6,
        mode_count=49,
        description=(
            "Starts at 49 rectangular-cavity modes and adaptively escalates by "
            "two-index steps through the m14 ceiling until adjacent-order "
            "convergence passes. Terminal-complete Layerwise does not stamp "
            "this modal basis into its external Device-port result."
        ),
    ),
    EvaluationModalPreset(
        key="balanced",
        label="Balanced",
        max_index=8,
        mode_count=81,
        description=(
            "Starts at 81 rectangular-cavity modes and adaptively escalates by "
            "two-index steps through the m14 ceiling. Terminal-complete Layerwise "
            "records the shared policy identity but uses only its global-Y Device-port result."
        ),
    ),
    EvaluationModalPreset(
        key="high",
        label="High",
        max_index=10,
        mode_count=121,
        description=(
            "Starts at 121 rectangular-cavity modes for a stricter truncation "
            "check and adaptively escalates through the m14 ceiling. More modes do not "
            "correct polygon, shared-DGND, or via-model approximations and can "
            "take several minutes on a large SPD. Terminal-complete Layerwise does not add "
            "a rectangular one-port difference to its global-Y result."
        ),
    ),
    EvaluationModalPreset(
        key="maximum",
        label="Experimental m12 check",
        max_index=12,
        mode_count=169,
        description=(
            "Starts at 169 rectangular-cavity modes after the m10(121)↔m12(169) "
            "comparison and may perform the final m12(169)↔m14(225) check. "
            "PowerSI evidence is comparison-only; it can take more than an hour "
            "on the 2026-07-29 benchmark, and must never be used to choose a "
            "lower modal order. Terminal-complete Layerwise remains a sole global-Y "
            "Device-port solve. PowerSI never selects an order."
        ),
    ),
)

DEFAULT_EVALUATION_MODAL_MAX_INDEX = 8
DEFAULT_LAYERWISE_MODAL_CEILING_INDEX = 12

_EVALUATION_MODAL_PRESETS_BY_INDEX = {
    preset.max_index: preset for preset in EVALUATION_MODAL_PRESETS
}

def evaluation_modal_preset(max_index: int) -> EvaluationModalPreset:
    """Return the supported preset for ``max_index`` or raise an actionable error."""

    if isinstance(max_index, bool) or not isinstance(max_index, int):
        raise ValueError("evaluation modal maximum index must be an integer")
    try:
        return _EVALUATION_MODAL_PRESETS_BY_INDEX[max_index]
    except KeyError as exc:
        choices = ", ".join(
            str(preset.max_index) for preset in EVALUATION_MODAL_PRESETS
        )
        raise ValueError(
            f"evaluation modal maximum index must be one of {choices}; got {max_index}"
        ) from exc


def evaluation_modal_convergence_ceiling(
    max_index: int,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
) -> int:
    """Return the fail-closed modal ceiling for one requested start preset.

    The shared adaptive schema retains the established m12 ceiling for a
    Layerwise request identity.  A terminal-complete external Device-port
    kernel is invariant to that rectangular order and does not stamp any modal
    correction; Legacy and Research retain their actual modal-order behavior.
    """

    preset = evaluation_modal_preset(max_index)
    profile = resolve_solver_profile(solver_profile)
    if (
        profile == LAYERWISE_ADMITTANCE_PROFILE
        and preset.max_index in (8, 10)
    ):
        return DEFAULT_LAYERWISE_MODAL_CEILING_INDEX
    # Legacy/Research preserve the established adaptive modal contract.  The
    # requested start preset must not silently lower the fail-closed ceiling.
    return DEFAULT_MODAL_CEILING_INDEX


def evaluation_model_boundary_disclosure(solver_profile: str) -> str:
    """Return the user-facing modeling boundary for one solver profile."""

    profile = resolve_solver_profile(solver_profile)
    if profile.key == LAYERWISE_ADMITTANCE_PROFILE.key:
        return (
            "Layerwise model boundary: terminal-complete exact retained-surface "
            "Maxwell-Y "
            "blocks, source-proven finite Via links and exact same-layer Trace "
            "connectivity, all mounted decap terminations, and one global "
            "Schur/Kron reduction at the external Device port; this global-Y Zii "
            "is the sole driving-point result and no legacy rectangular higher-mode "
            "one-port difference is added; topology-only surfaces "
            "receive zero synthesized adjacent-gap capacitance and no synthesized "
            "fringing; the output is one external Zii rather than a full multiport "
            "Z matrix, with no package/connector coupling and no full-wave "
            "claim; absolute "
            "sub-milliohm accuracy is not certified."
        )
    if profile.key == RESEARCH_UNIFORM_ADMITTANCE_PROFILE.key:
        return (
            "Research model boundary: source-only exact-artwork uniform C00 "
            "replacement; nonuniform modes retain the rectangular-envelope "
            "continuous-DGND correction; single-rail Zii only, with no inter-rail/"
            "site coupling and no full-wave claim; absolute sub-milliohm accuracy "
            "not certified."
        )
    return (
        "Legacy model boundary: rectangular PWR bounding-box modal cavity with a "
        "continuous DGND return; single-rail Zii only, with no inter-rail/site "
        "coupling and no full-wave claim; absolute sub-milliohm accuracy not "
        "certified."
    )


@dataclass(slots=True)
class EvaluationView:
    rail_id: str
    frequency_hz: list[float]
    magnitude_ohm: list[float]
    phase_deg: list[float]
    target_ohm: float
    target_curve_ohm: list[float]
    max_violation_db: float
    max_violation_frequency_hz: float
    rms_violation_db: float
    peak_magnitude_ohm: float
    peak_frequency_hz: float
    peak_prominence_db: float
    peaks: list[dict[str, float | str]]
    cap_count: int
    model_count: int
    confidence: str
    confidence_note: str
    confidence_bands: list[dict[str, Any]]
    assumptions: list[str]
    solver_version: str
    solver_diagnostics: dict[str, Any]
    convergence: dict[str, Any] | None
    z_real_ohm: list[float]
    z_imag_ohm: list[float]
    solver_profile_key: str = DEFAULT_SOLVER_PROFILE_KEY
    solver_profile_label: str = "Legacy modal"
    solver_profile_badge: str = "LEGACY"
    solver_provenance: dict[str, Any] = field(default_factory=dict)
    # Evaluation geometry policy is carried with every result so an
    # approximation can never be confused with the strict default.
    evaluation_policy: str = "STRICT_EXACT"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True, slots=True)
class SpdImportPlan:
    """A fully validated, non-mutating preview of one PowerSI SPD import."""

    project: ProjectSpec
    attachments: dict[str, bytes]
    diagnostics: tuple[Any, ...]
    source_name: str
    source_size_bytes: int
    source_sha256: str
    summary_lines: tuple[str, ...]
    mixed_reference_certificates: tuple[MixedReferenceCertificate, ...]
    can_apply: bool

@dataclass(frozen=True, slots=True)
class _SpdServiceDiagnostic:
    severity: str
    code: str
    message: str

@dataclass(slots=True)
class WorkspaceState:
    project: ProjectSpec
    attachments: dict[str, bytes] = field(default_factory=dict)
    # Scenario-owned, solve-scoped adapter.  It is intentionally transient:
    # ProjectSpec and saved SPDPI payloads remain JSON-only, while the
    # production layerwise path must bind the exact Original/Tuned mounted
    # state before any numerical solve can start.
    layerwise_termination_factory: Callable[[Any, Any], Any] | None = None
    last_evaluation: EvaluationView | None = None
    evaluation_history: list[EvaluationView] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.project.name

    @property
    def rail_ids(self) -> list[str]:
        active = [
            item.rail_id
            for item in self.project.rails
            if item.state == RailState.ACTIVE
        ]
        return active or [item.rail_id for item in self.project.rails]

    @property
    def input_rows(self) -> list[list[str]]:
        rows: list[list[str]] = []
        for layer in self.project.stackup_layers:
            layer_type = "Conductor" if layer.is_conductor else "Dielectric"
            net = ", ".join(layer.pwr_nets) or "—"
            dk = "—" if layer.dk is None else f"{layer.dk:g}"
            status = "VALID" if layer.thickness_um > 0 else "CHECK"
            rows.append(
                [
                    layer.name,
                    layer_type,
                    net,
                    f"{layer.thickness_um:g} µm",
                    dk,
                    status,
                ]
            )
        for rail in self.project.rails:
            matching_partition = next(
                (
                    partition
                    for partition in self.project.partitions
                    if partition.layer == rail.pwr_layer
                    and rail.domain in partition.domain_to_cell
                ),
                None,
            )
            pair_confirmed = bool(
                self.project.metadata.get("plane_pair_confirmed", False)
            )
            domains_on_layer = {
                item.domain
                for item in self.project.rails
                if item.pwr_layer == rail.pwr_layer
            }
            whole_outline_fallback = _uses_spd_whole_outline_fallback(
                self.project
            )
            confirmed = pair_confirmed and (
                matching_partition.confirmed
                if matching_partition is not None
                else not self.project.partitions
                and (len(domains_on_layer) == 1 or whole_outline_fallback)
            )
            rows.append(
                [
                    rail.rail_id,
                    f"Rail P{rail.priority}",
                    rail.net,
                    f"{rail.pwr_layer} → {rail.gnd_layer}",
                    rail.domain,
                    "VALID" if confirmed else "CONFIRM",
                ]
            )
        bump_count = sum(item.kind == PinKind.DEVICE_BUMP for item in self.project.pins)
        cap_pad_count = sum(item.kind == PinKind.DECAP_PAD for item in self.project.pins)
        if self.project.pins:
            rows.append(
                [
                    (
                        "PowerSI normalized pins"
                        if isinstance(self.project.metadata.get("spd_import"), Mapping)
                        else "Allegro normalized pins"
                    ),
                    "Pin report",
                    f"{bump_count} bump / {cap_pad_count} cap pad",
                    "—",
                    "—",
                    "VALID",
                ]
            )
        spd_metadata = self.project.metadata.get("spd_import")
        if isinstance(spd_metadata, dict):
            source_name = str(spd_metadata.get("source_name", "PowerSI SPD"))
            source_size = int(spd_metadata.get("source_size_bytes", 0) or 0)
            digest = str(spd_metadata.get("source_sha256", ""))
            confirmed_partitions = sum(
                item.confirmed for item in self.project.partitions
            )
            if not self.project.partitions:
                source_status = (
                    "VALID"
                    if bool(
                        self.project.metadata.get("plane_pair_confirmed", False)
                    )
                    else "CONFIRM"
                )
            elif confirmed_partitions == len(self.project.partitions):
                source_status = "VALID"
            elif confirmed_partitions:
                source_status = "PARTIAL"
            else:
                source_status = "CONFIRM"
            rows.append(
                [
                    source_name,
                    "PowerSI SPD",
                    f"{len(self.project.rails)} rails / {len(self.project.placements):,} mounted caps",
                    f"{source_size / (1024 * 1024):,.1f} MiB (referenced)",
                    digest[:12] if digest else "—",
                    source_status,
                ]
            )
        for cap in self.project.cap_models:
            rows.append(
                [
                    cap.model_id,
                    "Cap model",
                    cap.footprint,
                    f"inventory {cap.inventory}",
                    "sampled Z" if cap.impedance else f"{cap.capacitance_f:g} F",
                    "VALID",
                ]
            )
        via_provenance = self.project.metadata.get("spd_via_template_provenance")
        if isinstance(via_provenance, Mapping):
            for template_id, raw in sorted(
                via_provenance.items(), key=lambda item: str(item[0]).casefold()
            ):
                details = raw if isinstance(raw, Mapping) else {}
                padstack = str(details.get("padstack") or "not resolved")
                pad_diameter = details.get("pad_diameter_um")
                size_text = "??" if pad_diameter is None else f"{float(pad_diameter):g} µm pad"
                rows.append(
                    [
                        str(template_id),
                        "SPD via template",
                        padstack,
                        size_text,
                        "analytical R/L",
                        "ESTIMATE",
                    ]
                )
        return rows

def create_workspace_state() -> WorkspaceState:
    return WorkspaceState(
        project=ProjectSpec(
            name="Untitled SPD Decap PI Scenario",
            outline=MLOOutline(width_um=40_000.0, height_um=40_000.0),
            split_gap_um=100.0,
        )
    )

def _decode_spd_geometry_asset_with_size(
    expected_sha256: str, compressed: bytes
) -> tuple[dict[str, Any], int]:
    if sha256(compressed).hexdigest().casefold() != expected_sha256.casefold():
        raise ValueError("PowerSI geometry asset SHA-256 mismatch")
    limit = _SPD_GEOMETRY_MAX_UNCOMPRESSED_BYTES
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(compressed, limit + 1)
    if (
        len(raw) > limit
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or not decompressor.eof
    ):
        raise ValueError("PowerSI geometry asset exceeds the 64 MiB safety limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("PowerSI geometry asset is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("format") != (
        "powersi-spd-plane-primitives-v1"
    ):
        raise ValueError("unsupported PowerSI geometry asset format")
    return payload, len(raw)


def _decode_spd_geometry_asset(
    expected_sha256: str, compressed: bytes
) -> dict[str, Any]:
    payload, _decoded_bytes = _decode_spd_geometry_asset_with_size(
        expected_sha256, compressed
    )
    return payload

def _validate_spd_geometry_payload(
    payload: Mapping[str, Any],
    *,
    expected_layer: str | None = None,
    expected_net: str | None = None,
) -> None:
    """Validate decoded geometry before it reaches preview or project state."""

    layer = payload.get("layer")
    net = payload.get("net")
    if not isinstance(layer, str) or not layer:
        raise ValueError("PowerSI geometry asset has no valid layer")
    if not isinstance(net, str) or not net:
        raise ValueError("PowerSI geometry asset has no valid NET")
    if expected_layer is not None and layer.casefold() != expected_layer.casefold():
        raise ValueError("PowerSI geometry asset layer does not match its partition")
    if expected_net is not None and net.casefold() != expected_net.casefold():
        raise ValueError("PowerSI geometry asset NET does not match its plane cell")

    polygon_names = ("positive_polygons_um", "negative_polygons_um")
    circle_names = ("positive_circles_um", "negative_circles_um")
    for name in (*polygon_names, *circle_names, "primitive_order"):
        if not isinstance(payload.get(name), list):
            raise ValueError(f"PowerSI geometry asset field {name!r} must be a list")

    for name in polygon_names:
        for polygon in payload[name]:
            if not isinstance(polygon, list) or len(polygon) < 3:
                raise ValueError("PowerSI geometry asset contains an invalid Polygon")
            for point in polygon:
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError(
                        "PowerSI geometry asset Polygon vertices must be X/Y pairs"
                    )
                x, y = point
                # Geometry payloads can contain millions of JSON coordinate
                # values.  Keep the exact type/bool/finite contract, but avoid
                # creating one generator and calling ``all`` for every vertex.
                # This is on the background loading path, before any payload is
                # admitted to the preview or project state.
                if (
                    not isinstance(x, (int, float))
                    or isinstance(x, bool)
                    or not math.isfinite(float(x))
                    or not isinstance(y, (int, float))
                    or isinstance(y, bool)
                    or not math.isfinite(float(y))
                ):
                    raise ValueError(
                        "PowerSI geometry asset Polygon vertices must be finite"
                    )
    for name in circle_names:
        for circle in payload[name]:
            if not isinstance(circle, list) or len(circle) != 3:
                raise ValueError("PowerSI geometry asset contains an invalid Circle")
            center_x, center_y, radius = circle
            if (
                not isinstance(center_x, (int, float))
                or isinstance(center_x, bool)
                or not math.isfinite(float(center_x))
                or not isinstance(center_y, (int, float))
                or isinstance(center_y, bool)
                or not math.isfinite(float(center_y))
                or not isinstance(radius, (int, float))
                or isinstance(radius, bool)
                or not math.isfinite(float(radius))
                or float(radius) <= 0
            ):
                raise ValueError(
                    "PowerSI geometry asset Circle requires finite X/Y and positive radius"
                )

    primitive_counts = {
        "positive_polygon": len(payload["positive_polygons_um"]),
        "negative_polygon": len(payload["negative_polygons_um"]),
        "positive_circle": len(payload["positive_circles_um"]),
        "negative_circle": len(payload["negative_circles_um"]),
    }
    order = payload["primitive_order"]
    if len(order) != sum(primitive_counts.values()):
        raise ValueError(
            "PowerSI geometry asset order must reference every primitive exactly once"
        )
    seen = {kind: bytearray(count) for kind, count in primitive_counts.items()}
    for reference in order:
        if not isinstance(reference, list) or len(reference) != 2:
            raise ValueError("PowerSI geometry asset has an invalid primitive-order entry")
        kind, raw_index = reference
        if (
            kind not in primitive_counts
            or not isinstance(raw_index, int)
            or isinstance(raw_index, bool)
        ):
            raise ValueError("PowerSI geometry asset references an unknown primitive")
        index = raw_index
        if index < 0 or index >= primitive_counts[kind]:
            raise ValueError("PowerSI geometry asset references an unknown primitive")
        if seen[kind][index]:
            raise ValueError(
                "PowerSI geometry asset references a primitive more than once"
            )
        seen[kind][index] = 1
    if not payload["positive_polygons_um"] and not payload["positive_circles_um"]:
        raise ValueError("PowerSI geometry asset has no positive PWR boundary")

def _cell_source_geometry_with_size(
    attachments: Mapping[str, bytes],
    cell: PlaneCell,
    *,
    expected_layer: str | None = None,
) -> tuple[Mapping[str, Any], int]:
    if cell.source_geometry_asset:
        compressed = attachments.get(cell.source_geometry_asset)
        if compressed is None:
            raise ValueError(
                f"PowerSI geometry asset is missing: {cell.source_geometry_asset}"
            )
        assert cell.source_geometry_sha256 is not None
        payload, decoded_bytes = _decode_spd_geometry_asset_with_size(
            cell.source_geometry_sha256, bytes(compressed)
        )
        _validate_spd_geometry_payload(
            payload,
            expected_layer=expected_layer,
            expected_net=cell.source_net,
        )
        return payload, decoded_bytes
    payload = {
        "positive_polygons_um": cell.source_positive_polygons_um,
        "negative_polygons_um": cell.source_negative_polygons_um,
        "positive_circles_um": cell.source_positive_circles_um,
        "negative_circles_um": cell.source_negative_circles_um,
        "primitive_order": cell.source_primitive_order,
    }
    decoded_bytes = len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    return payload, decoded_bytes


def _cell_source_geometry(
    attachments: Mapping[str, bytes],
    cell: PlaneCell,
    *,
    expected_layer: str | None = None,
) -> Mapping[str, Any]:
    payload, _decoded_bytes = _cell_source_geometry_with_size(
        attachments, cell, expected_layer=expected_layer
    )
    return payload

def plane_cell_source_geometry(
    cell: PlaneCell,
    attachments: Mapping[str, bytes],
    *,
    expected_layer: str | None = None,
) -> Mapping[str, Any]:
    """Return validated read-only PowerSI artwork for one normalized cell."""

    return _cell_source_geometry(attachments, cell, expected_layer=expected_layer)


def plane_cell_source_geometry_with_size(
    cell: PlaneCell,
    attachments: Mapping[str, bytes],
    *,
    expected_layer: str | None = None,
) -> tuple[Mapping[str, Any], int]:
    """Return validated artwork and its decoded byte size for bounded preview."""

    return _cell_source_geometry_with_size(
        attachments, cell, expected_layer=expected_layer
    )


def spd_plane_geometry_record_payload(
    record: Mapping[str, Any],
    attachments: Mapping[str, bytes],
) -> Mapping[str, Any]:
    """Return one validated PowerSI artwork asset from the compact SPD index."""

    layer = record.get("layer")
    net = record.get("net")
    asset = record.get("asset")
    digest = record.get("asset_sha256")
    declared_uncompressed_bytes = record.get("uncompressed_bytes")
    if not isinstance(layer, str) or not layer:
        raise ValueError("PowerSI geometry index record has no valid layer")
    if not isinstance(net, str) or not net:
        raise ValueError("PowerSI geometry index record has no valid NET")
    if not isinstance(asset, str) or not asset:
        raise ValueError("PowerSI geometry index record has no valid asset")
    if not isinstance(digest, str) or not digest:
        raise ValueError("PowerSI geometry index record has no valid SHA-256")
    if (
        not isinstance(declared_uncompressed_bytes, int)
        or isinstance(declared_uncompressed_bytes, bool)
        or declared_uncompressed_bytes <= 0
    ):
        raise ValueError(
            "PowerSI geometry index record has no valid decoded-byte count"
        )
    compressed = attachments.get(asset)
    if compressed is None:
        raise ValueError(f"PowerSI geometry asset is missing: {asset}")
    payload, decoded_bytes = _decode_spd_geometry_asset_with_size(
        digest, bytes(compressed)
    )
    if decoded_bytes != declared_uncompressed_bytes:
        raise ValueError(
            "PowerSI geometry asset decoded size does not match its index"
        )
    _validate_spd_geometry_payload(
        payload,
        expected_layer=layer,
        expected_net=net,
    )
    return payload


def evaluate_workspace(
    state: WorkspaceState,
    rail_id: str,
    target_ohm: float | None = None,
    modal_max_index: int = DEFAULT_EVALUATION_MODAL_MAX_INDEX,
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
) -> EvaluationView:
    profile = resolve_solver_profile(solver_profile)
    modal_preset = evaluation_modal_preset(modal_max_index)
    modal_ceiling_index = evaluation_modal_convergence_ceiling(
        modal_preset.max_index, profile.key
    )
    progress = progress or _noop_progress
    is_cancelled = is_cancelled or _never_cancelled
    if is_cancelled():
        raise RuntimeError("evaluation cancelled")
    project = state.project
    if target_ohm is not None:
        if not math.isfinite(target_ohm) or target_ohm <= 0:
            raise ValueError("target impedance must be greater than zero")
        project = _project_with_target(project, rail_id, target_ohm)
    profile_numerics = (
        "terminal-complete external Device-port global Y; no modal add-on"
        if profile == LAYERWISE_ADMITTANCE_PROFILE
        else f"{modal_preset.mode_count} rectangular modes"
    )
    progress(
        8,
        f"Profile audit · {profile.label} [{profile.badge}] · {profile_numerics}",
    )
    _require_evaluable(project, rail_id)
    if is_cancelled():
        raise RuntimeError("evaluation cancelled")
    request_options: dict[str, Any] = {
        "max_mode_x": modal_preset.max_index,
        "max_mode_y": modal_preset.max_index,
        "worker_count": numerical_worker_count(project.frequency.points),
        "solver_profile_key": profile.key,
    }
    if profile in (
        LAYERWISE_ADMITTANCE_PROFILE,
        RESEARCH_UNIFORM_ADMITTANCE_PROFILE,
    ):
        # Keep the default legacy import graph independent of the optional
        # exact-artwork bridges and their geometry dependencies.

        progress(
            15,
            "Auditing source artwork, adjacent layers, reference, and Device evidence…",
        )
        template = compile_project_evaluation_template(project, rail_id)
        request_options["template"] = template
        if profile == LAYERWISE_ADMITTANCE_PROFILE:
            from .solver.layerwise_network import (
                build_layerwise_uniform_source_model,
            )

            source_model = build_layerwise_uniform_source_model(
                project,
                state.attachments,
                rail_id,
                template,
                progress=lambda value, message: progress(
                    15 + round(min(max(value, 0), 70) / 7), message
                ),
                is_cancelled=is_cancelled,
            )
            termination_factory = state.layerwise_termination_factory
            if termination_factory is None:
                raise EvaluationReadinessError(
                    "LAYERWISE_TERMINATION_MANIFEST_REQUIRED",
                    "production layerwise evaluation requires the exact "
                    "Original/Tuned mounted-decap termination state",
                )
            try:
                termination_manifest = termination_factory(
                    source_model.substrate, template
                )
                source_model = source_model.with_termination_manifest(
                    termination_manifest, required=True
                )
            except ValueError as exc:
                code = str(
                    getattr(exc, "code", "LAYERWISE_TERMINATION_BUILD_FAILED")
                )
                raise EvaluationReadinessError(code, str(exc)) from exc
            request_options["uniform_c00_source"] = source_model
            progress(
                25,
                "Layer-surface Maxwell Y, finite Via links, exact same-layer Trace connectivity, and all mounted decap terminations assembled; terminal-complete external Device-port Schur/Kron ready with no legacy modal add-on",
            )
        else:
            from .solver.research_uniform_profile import (
                build_uniform_c00_source_model,
            )

            request_options["uniform_c00_source"] = build_uniform_c00_source_model(
                project,
                state.attachments,
                rail_id,
                template,
            )
        if is_cancelled():
            raise RuntimeError("evaluation cancelled")
        if profile == RESEARCH_UNIFORM_ADMITTANCE_PROFILE:
            progress(22, "Source-only uniform C00 evidence gate passed")
    solve_boundary = (
        "terminal-complete global-Y Device-port solve; shared preset recorded "
        "without rectangular modal stamping"
        if profile == LAYERWISE_ADMITTANCE_PROFILE
        else (
            f"{modal_preset.label} · start index "
            f"({modal_preset.max_index},{modal_preset.max_index}) · adaptive ceiling "
            f"({modal_ceiling_index},{modal_ceiling_index})"
        )
    )
    progress(25, f"Solving {profile.label} [{profile.badge}] · {solve_boundary}")
    outcome = evaluate_project_rail_converged(
        project,
        rail_id,
        request_options=request_options,
        max_mode_x=modal_ceiling_index,
        max_mode_y=modal_ceiling_index,
        max_refinement_iterations=DEFAULT_MAX_REFINEMENT_ITERATIONS,
        max_new_frequency_points=DEFAULT_MAX_NEW_FREQUENCY_POINTS,
        progress=lambda value, message: progress(
            25 + round(min(max(value, 0), 100) * 0.63), message
        ),
        is_cancelled=is_cancelled,
    )
    if is_cancelled():
        raise RuntimeError("evaluation cancelled")
    progress(90, f"Extracting metrics and {profile.badge} provenance…")
    view = _evaluation_view(project, outcome)
    state.last_evaluation = view
    state.evaluation_history.append(view)
    state.evaluation_history = state.evaluation_history[-8:]
    progress(100, f"Evaluation complete: {rail_id}")
    return view

def numerical_worker_count(
    frequency_points: int,
    *,
    environment_variable: str = "SPD_DECAP_PI_NUMERICAL_WORKERS",
) -> int:
    """Choose bounded outer frequency parallelism for Evaluation."""

    if frequency_points < 1:
        raise ValueError("frequency_points must be >= 1")
    logical_cpus = max(1, os.cpu_count() or 1)
    configured = os.environ.get(environment_variable, "").strip()
    if configured:
        try:
            requested = int(configured)
        except ValueError as exc:
            raise ValueError(
                f"{environment_variable} must be an integer >= 1"
            ) from exc
        if requested < 1:
            raise ValueError(f"{environment_variable} must be >= 1")
        return min(requested, logical_cpus, frequency_points)
    # Independent frequency solves are already dominated by native BLAS/LAPACK
    # work.  Actual maximum-index 3, 8, and 10 SPD benchmarks showed no reliable
    # gain from an outer thread pool and severe oversubscription at index 10. Keep the
    # exact prepared-kernel path single-worker by default; the explicit
    # environment override remains available for differently configured BLAS.
    return 1

def analyze_with_local_llm(
    state: WorkspaceState,
    endpoint: str,
    model: str,
    mode: str = "Plot Analyst",
    allow_remote: bool = False,
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> LocalAIAnalysisView:
    if mode != "Plot Analyst":
        raise ValueError(
            "SPD Decap PI Evaluator supports Plot Analyst mode only"
        )
    progress = progress or _noop_progress
    is_cancelled = is_cancelled or _never_cancelled
    result = state.last_evaluation
    if result is None:
        raise ValueError("run an evaluation before AI analysis")
    evidence = (
        FeatureEvidence(
            evidence_id="metric.max_violation",
            kind=EvidenceKind.MAX_VIOLATION,
            summary="Maximum target-mask exceedance in the critical band",
            frequency_hz=result.max_violation_frequency_hz,
            value=result.max_violation_db,
            unit="dB",
        ),
        FeatureEvidence(
            evidence_id="peak.dominant",
            kind=EvidenceKind.PEAK,
            summary="Highest extracted impedance peak in the critical band",
            frequency_hz=result.peak_frequency_hz,
            value=result.peak_magnitude_ohm,
            unit="ohm",
            metadata={"prominence_db": result.peak_prominence_db},
        ),
    )
    features = PlotFeatures(
        analysis_id=f"{result.rail_id}-{sha256(np.asarray(result.magnitude_ohm).tobytes()).hexdigest()[:12]}",
        rail_id=result.rail_id,
        critical_band_hz=(1.0e5, 1.0e8),
        evidence=evidence,
    )
    if not model:
        reason = "local_model_not_configured"
        return LocalAIAnalysisView(
            text=_deterministic_analysis_text(result, reason),
            source=AssistantSource.DETERMINISTIC_FALLBACK,
            fallback_reason=reason,
        )
    if is_cancelled():
        raise RuntimeError("AI analysis cancelled")
    requires_remote = local_llm_endpoint_requires_remote_access(endpoint)
    if requires_remote and not allow_remote:
        raise ValueError(
            "LAN/remote AI endpoint is blocked. Enable 'Allow LAN/remote endpoint "
            "for this session' in AI Assistant, then retry."
        )
    api_kind = "openai" if endpoint.rstrip("/").endswith("/v1") else "ollama"
    config = LocalLLMConfig(
        base_url=endpoint,
        model=model,
        api_kind=api_kind,
        allow_remote=allow_remote,
    )
    destination = "LAN/remote" if requires_remote else "loopback"
    progress(
        25,
        f"Sending solver-derived feature JSON to {destination} endpoint…",
    )
    with LocalLLMClient(config) as client:
        report = client.analyze_plot(features)
    lines = [_solver_evidence_text(result)]
    if report.fallback_reason:
        lines.append("")
        lines.append(
            f"Local AI unavailable ({report.fallback_reason}); solver results and "
            "the scenario remain unchanged."
        )
        progress(
            100,
            f"AI unavailable; deterministic fallback ({report.fallback_reason})",
        )
    else:
        lines.extend(("", "Local AI interpretation (advisory only):"))
        if report.findings:
            for finding in report.findings:
                lines.append(
                    f"- {finding.statement}  "
                    f"[evidence: {', '.join(finding.evidence_ids)}]"
                )
        else:
            lines.append("- No additional cited findings were returned.")
        progress(100, "Structured analysis validated")
    return LocalAIAnalysisView(
        text="\n".join(lines),
        source=report.source,
        fallback_reason=report.fallback_reason,
    )

def _compress_spd_geometry_payload(
    *,
    layer: str,
    net: str,
    positive_polygons: Sequence[Any],
    negative_polygons: Sequence[Any],
    positive_circles: Sequence[Any],
    negative_circles: Sequence[Any],
    primitive_order: Sequence[Any],
    positive_subelement_count: int,
    negative_subelement_count: int,
    polygon_trace_count: int,
    box_count: int,
) -> tuple[bytes, int]:
    """Serialize one geometry asset incrementally without duplicating all vertices.

    ``json.dumps`` of a fully materialized list graph briefly retained the parser's
    tuple graph, a second list graph, the full UTF-8 JSON buffer, and zlib output at
    once.  A production SPD can contain millions of vertices, so emit one primitive
    at a time into a compressor instead.
    """

    compressor = zlib.compressobj(level=_SPD_GEOMETRY_COMPRESSION_LEVEL)
    compressed = bytearray()
    uncompressed_bytes = 0

    def emit(payload: bytes) -> None:
        nonlocal uncompressed_bytes
        if (
            uncompressed_bytes + len(payload)
            > _SPD_GEOMETRY_MAX_UNCOMPRESSED_BYTES
        ):
            raise _SpdGeometryAssetTooLarge(
                "PowerSI geometry asset exceeds the 64 MiB safety limit"
            )
        uncompressed_bytes += len(payload)
        chunk = compressor.compress(payload)
        if chunk:
            compressed.extend(chunk)

    def emit_json(value: Any) -> None:
        emit(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )

    def emit_sequence(
        name: str, values: Sequence[Any], *, compact_polygon: bool = False
    ) -> None:
        emit(b',"' + name.encode("ascii") + b'":[')
        for index, value in enumerate(values):
            if index:
                emit(b",")
            # The SPD parser stores vertices in a compact array-backed Sequence.
            # Materialize only this one polygon for the C JSON encoder, then release it.
            emit_json(
                list(value)
                if compact_polygon and not isinstance(value, (list, tuple))
                else value
            )
        emit(b"]")

    emit(b'{"format":"powersi-spd-plane-primitives-v1","layer":')
    emit_json(layer)
    emit(b',"net":')
    emit_json(net)
    emit_sequence("positive_polygons_um", positive_polygons, compact_polygon=True)
    emit_sequence("negative_polygons_um", negative_polygons, compact_polygon=True)
    emit_sequence("positive_circles_um", positive_circles)
    emit_sequence("negative_circles_um", negative_circles)
    emit_sequence("primitive_order", primitive_order)
    for name, value in (
        ("positive_subelement_count", positive_subelement_count),
        ("negative_subelement_count", negative_subelement_count),
        ("polygon_trace_count", polygon_trace_count),
        ("box_count", box_count),
    ):
        emit(b',"' + name.encode("ascii") + b'":')
        emit_json(int(value))
    emit(b"}")
    compressed.extend(compressor.flush())
    return bytes(compressed), uncompressed_bytes

def _spd_plane_geometry_assets(
    analysis: Any,
    retained_net_keys: set[str],
    diagnostics: list[Any],
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    """Compress exact Polygon/Circle primitives and return a compact project index."""

    report = progress or _noop_progress
    cancelled = is_cancelled or _never_cancelled
    records = getattr(analysis, "plane_geometries", ())
    total = len(records)

    def check_cancelled() -> None:
        if cancelled():
            raise RuntimeError("SPD plane-geometry asset compilation cancelled")

    check_cancelled()
    report(0, "Compressing retained PowerSI plane geometry assets")
    index: list[dict[str, Any]] = []
    assets: dict[str, bytes] = {}
    for position, item in enumerate(records):
        check_cancelled()
        if position:
            report(
                round(position * 99 / max(1, total)),
                "Compressing retained PowerSI plane geometry assets "
                f"({position}/{total})",
            )
        net = str(getattr(item, "net", ""))
        layer = str(getattr(item, "layer", ""))
        if not net or not layer or net.casefold() not in retained_net_keys:
            continue
        positive_polygons = getattr(item, "positive_polygons_um", ())
        if not positive_polygons and not getattr(item, "positive_circles_um", ()):
            continue
        negative_polygons = getattr(item, "negative_polygons_um", ())
        positive_circles = getattr(item, "positive_circles_um", ())
        negative_circles = getattr(item, "negative_circles_um", ())
        primitive_order = getattr(item, "primitive_order", ())
        positive_subelement_count = int(
            getattr(item, "positive_subelement_count", 0)
        )
        negative_subelement_count = int(
            getattr(item, "negative_subelement_count", 0)
        )
        polygon_trace_count = int(getattr(item, "polygon_trace_count", 0))
        box_count = int(getattr(item, "box_count", 0))
        geometry_payload = {
            "positive_polygons_um": positive_polygons,
            "negative_polygons_um": negative_polygons,
            "positive_circles_um": positive_circles,
            "negative_circles_um": negative_circles,
        }
        bounds = _spd_geometry_bounds(geometry_payload)
        if bounds is None:
            continue
        exact_rectangle = bool(
            len(positive_polygons) == 1
            and _axis_aligned_rectangle(positive_polygons[0])
            and not negative_polygons
            and not positive_circles
            and not negative_circles
        )
        try:
            check_cancelled()
            compressed, uncompressed_bytes = _compress_spd_geometry_payload(
                layer=layer,
                net=net,
                positive_polygons=positive_polygons,
                negative_polygons=negative_polygons,
                positive_circles=positive_circles,
                negative_circles=negative_circles,
                primitive_order=primitive_order,
                positive_subelement_count=positive_subelement_count,
                negative_subelement_count=negative_subelement_count,
                polygon_trace_count=polygon_trace_count,
                box_count=box_count,
            )
            check_cancelled()
        except _SpdGeometryAssetTooLarge:
            diagnostics.append(
                _SpdServiceDiagnostic(
                    severity="error",
                    code="SPD_PLANE_GEOMETRY_ASSET_TOO_LARGE",
                    message=(
                        f"PowerSI artwork for {net!r} on {layer!r} exceeds the "
                        "64 MiB decoded safety limit. The scenario was not created; "
                        "reduce or partition the source artwork before retrying."
                    ),
                )
            )
            continue
        digest = sha256(compressed).hexdigest()
        asset_name = f"geometry/{len(index):04d}-{digest[:16]}.spdgeom.zlib"
        assets[asset_name] = compressed
        index.append(
            {
                "layer": layer,
                "net": net,
                "asset": asset_name,
                "asset_sha256": digest,
                "bbox_um": list(bounds),
                "solver_exact_rectangle": exact_rectangle,
                "positive_polygon_count": len(positive_polygons),
                "negative_polygon_count": len(negative_polygons),
                "positive_circle_count": len(positive_circles),
                "negative_circle_count": len(negative_circles),
                "positive_subelement_count": positive_subelement_count,
                "negative_subelement_count": negative_subelement_count,
                "polygon_trace_count": polygon_trace_count,
                "box_count": box_count,
                "uncompressed_bytes": uncompressed_bytes,
                "compressed_bytes": len(compressed),
            }
        )
    check_cancelled()
    report(100, f"Compressed {len(index)} retained plane geometry asset(s)")
    return index, assets

def _reuse_spd_plane_geometry_assets(
    donor: SpdImportPlan,
    *,
    source_name: str,
    source_size: int,
    source_hash: str,
    retained_net_keys: set[str],
    selected_power_keys: set[str],
    ground_nets: set[str],
) -> tuple[list[dict[str, Any]], dict[str, bytes]] | None:
    """Reuse only source-identity-validated geometry metadata/assets."""

    if (
        donor.source_name != source_name
        or int(donor.source_size_bytes) != int(source_size)
        or donor.source_sha256.casefold() != source_hash.casefold()
        or any(
            str(getattr(item, "code", "")) == "SPD_PLANE_GEOMETRY_ASSET_TOO_LARGE"
            for item in donor.diagnostics
        )
    ):
        return None
    metadata = getattr(donor.project, "metadata", {})
    spd_import = metadata.get("spd_import", {}) if isinstance(metadata, Mapping) else {}
    donor_power = {
        str(item).casefold()
        for item in (spd_import.get("selected_power_nets", ()) if isinstance(spd_import, Mapping) else ())
    }
    donor_ground = {str(item).casefold() for item in getattr(donor.project, "gnd_aliases", ())}
    if donor_power != selected_power_keys or donor_ground != ground_nets:
        return None
    records = spd_import.get(_SPD_PLANE_GEOMETRIES_KEY) if isinstance(spd_import, Mapping) else None
    if not isinstance(records, list):
        return None
    copied_records = deepcopy(records)
    copied_assets: dict[str, bytes] = {}
    referenced: set[str] = set()
    referenced_casefold: set[str] = set()
    for ordinal, record in enumerate(copied_records):
        if not isinstance(record, Mapping):
            return None
        net = str(record.get("net", ""))
        layer = str(record.get("layer", ""))
        asset_name = str(record.get("asset", ""))
        digest = str(record.get("asset_sha256", "")).casefold()
        if (
            not net
            or not layer
            or net.casefold() not in retained_net_keys
            or not asset_name
            or asset_name in referenced
            or asset_name.casefold() in referenced_casefold
            or len(digest) != 64
            or asset_name.casefold()
            != f"geometry/{ordinal:04d}-{digest[:16]}.spdgeom.zlib".casefold()
        ):
            return None
        source_asset = donor.attachments.get(asset_name)
        if not isinstance(source_asset, (bytes, bytearray, memoryview)):
            return None
        payload = bytes(source_asset)
        if sha256(payload).hexdigest().casefold() != digest:
            return None
        referenced.add(asset_name)
        referenced_casefold.add(asset_name.casefold())
        copied_assets[asset_name] = payload
    donor_geometry_assets = {
        str(name)
        for name in donor.attachments
        if str(name).casefold().startswith("geometry/")
    }
    if {name.casefold() for name in donor_geometry_assets} != referenced_casefold:
        return None
    return copied_records, copied_assets


def _ordered_spd_geometry(
    record: Mapping[str, Any],
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> Any | None:
    """Return the exact ordered PowerSI boolean geometry, or fail closed."""

    return ordered_spd_geometry(
        record,
        progress=progress,
        is_cancelled=is_cancelled,
    )


def _clip_spd_geometry_payload(
    record: Mapping[str, Any],
    clip_bounds: Sequence[float],
    *,
    max_primitives: int,
    is_cancelled: CancelCallback | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Keep ordered primitives whose bounds can meet the clipping rectangle."""

    cancelled = is_cancelled or _never_cancelled

    def check_cancelled() -> None:
        if cancelled():
            raise RuntimeError("SPD geometry clipping cancelled")

    check_cancelled()
    if len(clip_bounds) != 4:
        return None, False
    try:
        clip_min_x, clip_min_y, clip_max_x, clip_max_y = map(float, clip_bounds)
    except (TypeError, ValueError):
        return None, False
    if not (
        math.isfinite(clip_min_x)
        and math.isfinite(clip_max_x)
        and math.isfinite(clip_min_y)
        and math.isfinite(clip_max_y)
        and clip_max_x > clip_min_x
        and clip_max_y > clip_min_y
    ):
        return None, False
    collections = {
        "positive_polygon": record.get("positive_polygons_um", ()),
        "negative_polygon": record.get("negative_polygons_um", ()),
        "positive_circle": record.get("positive_circles_um", ()),
        "negative_circle": record.get("negative_circles_um", ()),
    }
    order = record.get("primitive_order", ())
    if not isinstance(order, Sequence) or not order:
        return None, False
    selected: list[Any] = []
    for position, step in enumerate(order):
        if position % 256 == 0:
            check_cancelled()
        if not isinstance(step, Sequence) or len(step) != 2:
            return None, False
        try:
            kind, offset = str(step[0]), int(step[1])
        except (TypeError, ValueError, IndexError):
            return None, False
        source = collections.get(kind)
        if source is None or offset < 0 or offset >= len(source):
            return None, False
        raw = source[offset]
        if kind.endswith("polygon"):
            try:
                points = [(float(point[0]), float(point[1])) for point in raw]
            except (TypeError, ValueError, IndexError):
                return None, False
            if not points or not all(
                math.isfinite(value) for point in points for value in point
            ):
                return None, False
            min_x = min(point[0] for point in points)
            max_x = max(point[0] for point in points)
            min_y = min(point[1] for point in points)
            max_y = max(point[1] for point in points)
        else:
            try:
                x_um, y_um, radius_um = map(float, raw)
            except (TypeError, ValueError):
                return None, False
            if not (
                math.isfinite(x_um)
                and math.isfinite(y_um)
                and math.isfinite(radius_um)
                and radius_um > 0
            ):
                return None, False
            min_x, max_x = x_um - radius_um, x_um + radius_um
            min_y, max_y = y_um - radius_um, y_um + radius_um
        if (
            max_x < clip_min_x
            or min_x > clip_max_x
            or max_y < clip_min_y
            or min_y > clip_max_y
        ):
            continue
        selected.append(step)
        if len(selected) > max_primitives:
            return None, True
    check_cancelled()
    clipped = dict(record)
    clipped["primitive_order"] = tuple(selected)
    return clipped, False


def _ordered_spd_geometry_is_valid_empty(
    record: Mapping[str, Any],
    geometry_builder: Callable[[Mapping[str, Any]], Any | None],
    *,
    is_cancelled: CancelCallback | None = None,
) -> bool:
    """Distinguish an exact empty replay from rejected local geometry."""

    cancelled = is_cancelled or _never_cancelled
    collections = {
        "positive_polygon": record.get("positive_polygons_um", ()),
        "negative_polygon": record.get("negative_polygons_um", ()),
        "positive_circle": record.get("positive_circles_um", ()),
        "negative_circle": record.get("negative_circles_um", ()),
    }
    order = record.get("primitive_order", ())
    if not isinstance(order, Sequence):
        return False
    if not order:
        return True
    compact_collections: dict[str, list[Any]] = {
        kind: [] for kind in collections
    }
    compact_order: list[tuple[str, int]] = []
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for position, step in enumerate(order):
        if position % 256 == 0 and cancelled():
            raise RuntimeError("SPD geometry empty-replay check cancelled")
        if not isinstance(step, Sequence) or len(step) != 2:
            return False
        try:
            kind, offset = str(step[0]), int(step[1])
            source = collections[kind]
            if offset < 0 or offset >= len(source):
                return False
            raw = source[offset]
            compact_order.append((kind, len(compact_collections[kind])))
            compact_collections[kind].append(raw)
            if kind.endswith("polygon"):
                xs = [float(point[0]) for point in raw]
                ys = [float(point[1]) for point in raw]
                bounds = min(xs), min(ys), max(xs), max(ys)
            else:
                x_um, y_um, radius_um = map(float, raw)
                bounds = (
                    x_um - radius_um,
                    y_um - radius_um,
                    x_um + radius_um,
                    y_um + radius_um,
                )
        except (KeyError, TypeError, ValueError, IndexError):
            return False
        if not all(math.isfinite(value) for value in bounds):
            return False
        min_x, min_y = min(min_x, bounds[0]), min(min_y, bounds[1])
        max_x, max_y = max(max_x, bounds[2]), max(max_y, bounds[3])
    span = max(max_x - min_x, max_y - min_y, 1.0)
    probe_min_x, probe_min_y = max_x + span, max_y + span
    probe_max_x, probe_max_y = probe_min_x + span, probe_min_y + span
    if not all(
        math.isfinite(value)
        for value in (probe_min_x, probe_min_y, probe_max_x, probe_max_y)
    ):
        return False
    probe = (
        (probe_min_x, probe_min_y),
        (probe_max_x, probe_min_y),
        (probe_max_x, probe_max_y),
        (probe_min_x, probe_max_y),
    )
    probe_index = len(compact_collections["positive_polygon"])
    compact_collections["positive_polygon"].append(probe)
    replay = {
        "positive_polygons_um": tuple(compact_collections["positive_polygon"]),
        "negative_polygons_um": tuple(compact_collections["negative_polygon"]),
        "positive_circles_um": tuple(compact_collections["positive_circle"]),
        "negative_circles_um": tuple(compact_collections["negative_circle"]),
        "primitive_order": (*compact_order, ("positive_polygon", probe_index)),
    }
    replay_shape = geometry_builder(replay)
    if replay_shape is None:
        return False
    try:
        from shapely.errors import GEOSException
        from shapely.geometry import Polygon

        return bool(replay_shape.equals(Polygon(probe)))
    except (ImportError, AttributeError, TypeError, ValueError, GEOSException):
        return False


def _spd_surface_islands(
    *,
    layer: str,
    net: str,
    asset_sha256: str,
    shape: Any,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> tuple[tuple[str, Any], ...]:
    """Return deterministic connected-polygon identities for one artwork asset.

    A layer/NET asset may be a ``MultiPolygon``.  Treating that geometry as one
    equipotential surface silently shorts physically disconnected copper.  The
    import-side raw graph compiler and the production layerwise gate share these
    source-asset-bound IDs so a Node coordinate can prove which exact island it
    contacts.  IDs use normalized WKB and therefore do not depend on GEOS's
    ``MultiPolygon`` member ordering.
    """

    report = progress or _noop_progress
    cancelled = is_cancelled or _never_cancelled
    last_progress = -1

    def check_cancelled() -> None:
        if cancelled():
            raise RuntimeError("SPD surface-island compilation cancelled")

    def emit(value: int, message: str) -> None:
        nonlocal last_progress
        bounded = max(0, min(100, int(value)))
        if bounded <= last_progress:
            return
        last_progress = bounded
        report(bounded, message)

    check_cancelled()
    emit(0, f"Identifying connected surface islands for {net} on {layer}")
    layer_text = str(layer).strip()
    net_text = str(net).strip()
    digest = str(asset_sha256).strip().casefold()
    if (
        not layer_text
        or not net_text
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
    ):
        raise ValueError("surface-island identity needs layer, NET, and asset SHA-256")
    geometry_type = str(getattr(shape, "geom_type", ""))
    if geometry_type == "Polygon":
        parts = (shape,)
    elif geometry_type == "MultiPolygon":
        parts = tuple(getattr(shape, "geoms", ()))
    else:
        raise ValueError(
            "surface-island identity requires Polygon or MultiPolygon artwork"
        )
    if not parts:
        raise ValueError("surface artwork has no connected polygon island")

    identified: list[tuple[str, Any]] = []
    for index, part in enumerate(parts, start=1):
        check_cancelled()
        if (
            str(getattr(part, "geom_type", "")) != "Polygon"
            or bool(getattr(part, "is_empty", True))
            or not bool(getattr(part, "is_valid", False))
            or float(getattr(part, "area", 0.0)) <= 0.0
        ):
            raise ValueError("surface artwork contains a nonphysical polygon island")
        check_cancelled()
        normalized = part.normalize()
        check_cancelled()
        polygon_sha256 = sha256(bytes(normalized.wkb)).hexdigest()
        identity = _canonical_metadata_sha256(
            {
                "asset_sha256": digest,
                "layer": layer_text.casefold(),
                "net": net_text.casefold(),
                "normalized_polygon_wkb_sha256": polygon_sha256,
            }
        )
        identified.append((f"spd-surface-island:{identity[:24]}", part))
        emit(
            round(index * 99 / len(parts)),
            f"Identifying connected surface islands for {net} on {layer} "
            f"({index}/{len(parts)})",
        )
    identified.sort(key=lambda item: item[0])
    if len({item[0] for item in identified}) != len(identified):
        raise ValueError("surface artwork has duplicate deterministic island identities")
    check_cancelled()
    emit(100, f"Identified {len(identified)} surface island(s) for {net} on {layer}")
    return tuple(identified)


def _mixed_reference_certificates(
    records: Sequence[Mapping[str, Any]],
    assets: Mapping[str, bytes],
    layers: Sequence[StackupLayer],
    *,
    power_keys: set[str],
    ground_keys: set[str],
    failures: list[dict[str, Any]] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> tuple[MixedReferenceCertificate, ...]:
    """Certify mixed GND layers from retained source artwork, never net names alone."""

    report = progress or _noop_progress
    cancelled = is_cancelled or _never_cancelled
    callbacks_requested = progress is not None or is_cancelled is not None
    last_progress = -1

    def check_cancelled() -> None:
        if cancelled():
            raise RuntimeError("SPD mixed-reference certification cancelled")

    def emit(value: int, message: str) -> None:
        nonlocal last_progress
        bounded = max(0, min(100, int(value)))
        if bounded <= last_progress:
            return
        last_progress = bounded
        report(bounded, message)

    check_cancelled()
    emit(0, "Certifying ordered mixed-reference artwork")
    layer_by_key = {item.name.casefold(): item for item in layers}
    layer_index = {item.name.casefold(): index for index, item in enumerate(layers)}
    result: list[MixedReferenceCertificate] = []
    ground_records_by_layer: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        layer = str(record.get("layer", ""))
        net = str(record.get("net", ""))
        if layer and net.casefold() in ground_keys:
            ground_records_by_layer.setdefault(layer.casefold(), []).append(record)

    def load_valid_payload(record: Mapping[str, Any]) -> dict[str, Any] | None:
        check_cancelled()
        asset = str(record.get("asset", ""))
        digest = str(record.get("asset_sha256", ""))
        try:
            payload = _decode_spd_geometry_asset(digest, assets[asset])
            check_cancelled()
            _validate_spd_geometry_payload(
                payload,
                expected_layer=str(record.get("layer", "")),
                expected_net=str(record.get("net", "")),
            )
        except (KeyError, ValueError):
            return None
        payload["asset_sha256"] = digest
        check_cancelled()
        return payload

    # GND artwork is reused by many PWR candidates.  Keep only those few
    # payloads/GEOS shapes cached; a PWR payload and shape live only for its
    # current outer record so an import does not retain every plane at once.
    ground_payload_by_identity: dict[
        tuple[str, str, str], dict[str, Any] | None
    ] = {}
    ground_shape_by_digest: dict[
        tuple[str, tuple[float, float, float, float]], Any | None
    ] = {}
    ground_valid_empty_by_shape_key: dict[
        tuple[str, tuple[float, float, float, float]], bool
    ] = {}
    ground_full_shape_valid_by_digest: dict[str, bool] = {}

    def ground_payload_for(record: Mapping[str, Any]) -> dict[str, Any] | None:
        digest = str(record.get("asset_sha256", ""))
        identity = (
            digest,
            str(record.get("layer", "")).casefold(),
            str(record.get("net", "")).casefold(),
        )
        if identity not in ground_payload_by_identity:
            ground_payload_by_identity[identity] = load_valid_payload(record)
        return ground_payload_by_identity[identity]

    try:
        from shapely.errors import GEOSException
        shapely_available = True
    except ImportError:
        class GEOSException(Exception):
            pass
        shapely_available = False

    def record_failure(
        *,
        rail_net: str,
        pwr_layer: str,
        gnd_layer: str,
        gnd_net: str,
        reason: str,
        code: str = "SPD_MIXED_REFERENCE_CERTIFICATE_REJECTED",
        blocking: bool = False,
    ) -> None:
        if failures is None:
            return
        item = {
            "rail_net": rail_net,
            "pwr_layer": pwr_layer,
            "gnd_layer": gnd_layer,
            "gnd_net": gnd_net,
            "reason": reason,
            "code": code,
            "blocking": blocking,
        }
        if item not in failures:
            failures.append(item)

    total_records = len(records)
    for pwr_position, pwr_record in enumerate(records):
        check_cancelled()
        outer_low = round(pwr_position * 99 / max(1, total_records))
        outer_high = round((pwr_position + 1) * 99 / max(1, total_records))
        emit(
            outer_low,
            "Certifying ordered mixed-reference artwork "
            f"({pwr_position}/{total_records})",
        )

        def ordered_geometry(payload: Mapping[str, Any]) -> Any | None:
            if not callbacks_requested:
                # Preserve one-positional-argument compatibility for callers
                # and tests that replace the legacy facade.
                return _ordered_spd_geometry(payload)

            def geometry_progress(value: int, message: str) -> None:
                span = max(0, outer_high - outer_low)
                emit(outer_low + round(span * value / 100), message)

            return _ordered_spd_geometry(
                payload,
                progress=geometry_progress,
                is_cancelled=cancelled,
            )

        net = str(pwr_record.get("net", ""))
        pwr_layer = str(pwr_record.get("layer", ""))
        if net.casefold() not in power_keys or not pwr_layer:
            continue
        pwr_payload: dict[str, Any] | None = None
        pwr_payload_checked = False
        pwr_shape: Any | None = None
        pwr_shape_checked = False
        for gnd_record in records:
            check_cancelled()
            gnd_layer = str(gnd_record.get("layer", ""))
            gnd_record_net = str(gnd_record.get("net", ""))
            if (
                gnd_layer.casefold() == pwr_layer.casefold()
                or gnd_record_net.casefold() not in ground_keys
            ):
                continue
            stack_layer = layer_by_key.get(gnd_layer.casefold())
            if stack_layer is None or not stack_layer.is_conductor:
                continue
            layer_keys = {value.casefold() for value in stack_layer.pwr_nets}
            if not layer_keys or layer_keys.issubset(ground_keys):
                continue
            if not any(alias in stack_layer.name.casefold() for alias in ground_keys):
                continue
            pwr_index = layer_index.get(pwr_layer.casefold())
            gnd_index = layer_index.get(gnd_layer.casefold())
            if pwr_index is None or gnd_index is None:
                continue
            lower, upper = sorted((pwr_index, gnd_index))
            between = layers[lower + 1 : upper]
            if (
                not between
                or any(item.is_conductor for item in between)
                or any(item.dk is None for item in between)
            ):
                continue
            # The former eager pass excluded invalid PWR/GND assets before any
            # structural failure could be recorded.  Retain that behavior, but
            # only validate assets after this compact structural candidate gate.
            if not pwr_payload_checked:
                pwr_payload = load_valid_payload(pwr_record)
                pwr_payload_checked = True
            if pwr_payload is None:
                break
            gnd_payload = ground_payload_for(gnd_record)
            if gnd_payload is None:
                continue
            pwr_net = str(pwr_payload.get("net", ""))
            pwr_payload_layer = str(pwr_payload.get("layer", ""))
            gnd_net = str(gnd_payload.get("net", ""))
            gnd_payload_layer = str(gnd_payload.get("layer", ""))
            if net.casefold() in layer_keys:
                record_failure(
                    rail_net=pwr_net, pwr_layer=pwr_payload_layer,
                    gnd_layer=gnd_payload_layer,
                    gnd_net=gnd_net, reason="target rail is present on proposed DGND layer",
                )
                continue
            # A certificate binds one exact DGND artwork asset.  Multiple ground
            # assets on a mixed return layer are intentionally not merged silently.
            matching_ground = ground_records_by_layer.get(gnd_layer.casefold(), [])
            if len(matching_ground) != 1:
                record_failure(
                    rail_net=pwr_net, pwr_layer=pwr_payload_layer,
                    gnd_layer=gnd_payload_layer,
                    gnd_net=gnd_net, reason="ground artwork asset is missing or ambiguous",
                )
                continue
            # Mixed-reference certification computes whole-plane overlap
            # fractions.  Replaying a retained asset with tens of thousands
            # of primitives through a global unary_union is unbounded in
            # memory/time and can stall raw import.  Keep the certificate
            # fail-closed when PWR or locally relevant DGND artwork exceeds the
            # bounded exact certificate budget; strict plane-pair artwork
            # validation uses the indexed local predicate elsewhere.
            max_certificate_primitives = 2048
            if len(pwr_payload.get("primitive_order", ())) > max_certificate_primitives:
                record_failure(
                    rail_net=net,
                    pwr_layer=pwr_layer,
                    gnd_layer=gnd_layer,
                    gnd_net=gnd_record_net,
                    reason=(
                        "retained mixed-reference artwork exceeds bounded "
                        "certificate geometry budget"
                    ),
                    code="SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE",
                    # This certificate candidate is ineligible, but an
                    # unrelated rail must not make the whole raw import fail.
                    # Rail selection/evaluation remains fail-closed.
                    blocking=False,
                )
                continue
            # Most retained PWR artwork never has a structurally eligible mixed
            # DGND candidate.  Do the inexpensive stackup/identity gates first,
            # then construct the costly ordered Boolean shape only when this
            # branch can actually issue a certificate or a geometry failure.
            if not pwr_shape_checked:
                pwr_shape = (
                    ordered_geometry(pwr_payload)
                    if shapely_available
                    else None
                )
                pwr_shape_checked = True
            if pwr_shape is None:
                record_failure(
                    rail_net=pwr_net, pwr_layer=pwr_payload_layer,
                    gnd_layer=gnd_payload_layer,
                    gnd_net=gnd_net,
                    reason=(
                        "Shapely geometry engine is unavailable"
                        if not shapely_available
                        else "ordered PWR/DGND artwork geometry is invalid or unsupported"
                    ),
                    code="SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE",
                    blocking=True,
                )
                continue
            pwr_bounds = tuple(float(value) for value in pwr_shape.bounds)
            local_gnd_payload, local_budget_exceeded = _clip_spd_geometry_payload(
                gnd_payload,
                pwr_bounds,
                max_primitives=max_certificate_primitives,
                is_cancelled=cancelled,
            )
            if local_budget_exceeded:
                record_failure(
                    rail_net=net,
                    pwr_layer=pwr_layer,
                    gnd_layer=gnd_layer,
                    gnd_net=gnd_record_net,
                    reason=(
                        "retained mixed-reference artwork exceeds bounded "
                        "certificate geometry budget"
                    ),
                    code="SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE",
                    blocking=False,
                )
                continue
            gnd_digest = str(gnd_payload.get("asset_sha256", ""))
            shape_key = (gnd_digest, pwr_bounds)
            if shape_key not in ground_shape_by_digest:
                for stale_key in tuple(ground_shape_by_digest):
                    if stale_key[0] == gnd_digest:
                        del ground_shape_by_digest[stale_key]
                        ground_valid_empty_by_shape_key.pop(stale_key, None)
                ground_shape_by_digest[shape_key] = (
                    ordered_geometry(local_gnd_payload)
                    if shapely_available and local_gnd_payload is not None
                    else None
                )
            gnd_shape = ground_shape_by_digest[shape_key]
            if gnd_shape is None:
                if shape_key not in ground_valid_empty_by_shape_key:
                    valid_empty = bool(
                        shapely_available
                        and local_gnd_payload is not None
                        and _ordered_spd_geometry_is_valid_empty(
                            local_gnd_payload,
                            ordered_geometry,
                            is_cancelled=cancelled,
                        )
                    )
                    if (
                        valid_empty
                        and len(gnd_payload.get("primitive_order", ()))
                        <= max_certificate_primitives
                    ):
                        if gnd_digest not in ground_full_shape_valid_by_digest:
                            ground_full_shape_valid_by_digest[gnd_digest] = (
                                ordered_geometry(gnd_payload) is not None
                            )
                        valid_empty = ground_full_shape_valid_by_digest[gnd_digest]
                    ground_valid_empty_by_shape_key[shape_key] = valid_empty
                if ground_valid_empty_by_shape_key[shape_key]:
                    record_failure(
                        rail_net=pwr_net,
                        pwr_layer=pwr_payload_layer,
                        gnd_layer=gnd_payload_layer,
                        gnd_net=gnd_net,
                        reason="PWR and DGND artwork do not overlap",
                    )
                    continue
                record_failure(
                    rail_net=pwr_net, pwr_layer=pwr_payload_layer,
                    gnd_layer=gnd_payload_layer,
                    gnd_net=gnd_net,
                    reason=(
                        "Shapely geometry engine is unavailable"
                        if not shapely_available
                        else "ordered PWR/DGND artwork geometry is invalid or unsupported"
                    ),
                    code="SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE",
                    blocking=True,
                )
                continue
            try:
                check_cancelled()
                overlap = pwr_shape.intersection(gnd_shape)
                check_cancelled()
            except GEOSException:
                record_failure(
                    rail_net=pwr_net, pwr_layer=pwr_payload_layer,
                    gnd_layer=gnd_payload_layer,
                    gnd_net=gnd_net, reason="GEOS intersection failed",
                    code="SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE",
                    blocking=True,
                )
                continue
            if overlap.is_empty or overlap.area <= 0:
                record_failure(
                    rail_net=pwr_net, pwr_layer=pwr_payload_layer,
                    gnd_layer=gnd_payload_layer,
                    gnd_net=gnd_net, reason="PWR and DGND artwork do not overlap",
                )
                continue
            coverage = float(overlap.area / pwr_shape.area)
            components = getattr(overlap, "geoms", (overlap,))
            dominant = float(max(part.area for part in components) / overlap.area)
            if (
                coverage < MIXED_REFERENCE_MIN_COVERAGE
                or dominant < MIXED_REFERENCE_MIN_DOMINANT_COMPONENT
            ):
                record_failure(
                    rail_net=pwr_net, pwr_layer=pwr_payload_layer,
                    gnd_layer=gnd_payload_layer,
                    gnd_net=gnd_net,
                    reason=(
                        f"coverage {coverage:.6f} or dominant component "
                        f"{dominant:.6f} is below v1 thresholds"
                    ),
                )
                continue
            pwr_hash = str(pwr_payload.get("asset_sha256", ""))
            gnd_hash = str(gnd_payload.get("asset_sha256", ""))
            if len(pwr_hash) != 64 or len(gnd_hash) != 64:
                continue
            result.append(MixedReferenceCertificate(
                rail_net=pwr_net, gnd_net=gnd_net,
                pwr_layer=pwr_payload_layer, gnd_layer=gnd_payload_layer,
                pwr_asset_sha256=pwr_hash, gnd_asset_sha256=gnd_hash,
                overlap_fraction=coverage,
                dominant_overlap_component_fraction=dominant,
            ))
    check_cancelled()
    emit(100, f"Certified {len(result)} mixed-reference artwork pair(s)")
    return tuple(result)

def _axis_aligned_rectangle(
    polygon: Sequence[Sequence[float]], *, tolerance_um: float = 1.0e-6
) -> bool:
    points = [(float(point[0]), float(point[1])) for point in polygon]
    if len(points) > 1 and all(
        abs(left - right) <= tolerance_um
        for left, right in zip(points[0], points[-1], strict=True)
    ):
        points.pop()
    if len(points) != 4:
        return False
    xs = sorted({round(point[0] / tolerance_um) for point in points})
    ys = sorted({round(point[1] / tolerance_um) for point in points})
    if len(xs) != 2 or len(ys) != 2:
        return False
    return {(round(x / tolerance_um), round(y / tolerance_um)) for x, y in points} == {
        (x, y) for x in xs for y in ys
    }

def _spd_geometry_bounds(
    record: Mapping[str, Any]
) -> tuple[float, float, float, float] | None:
    raw_bbox = record.get("bbox_um")
    if isinstance(raw_bbox, Sequence) and not isinstance(raw_bbox, (str, bytes)):
        if len(raw_bbox) == 4:
            bounds = tuple(float(value) for value in raw_bbox)
            if (
                all(math.isfinite(value) for value in bounds)
                and bounds[1] > bounds[0]
                and bounds[3] > bounds[2]
            ):
                return bounds  # type: ignore[return-value]
    xs: list[float] = []
    ys: list[float] = []
    for polygon in record.get("positive_polygons_um", []):
        for point in polygon:
            if len(point) == 2:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
    for circle in record.get("positive_circles_um", []):
        if len(circle) == 3:
            x_um, y_um, radius_um = map(float, circle)
            xs.extend((x_um - radius_um, x_um + radius_um))
            ys.extend((y_um - radius_um, y_um + radius_um))
    if not xs or not ys:
        return None
    bounds = min(xs), max(xs), min(ys), max(ys)
    if bounds[1] <= bounds[0] or bounds[3] <= bounds[2]:
        return None
    return bounds

def _spd_plane_partitions(project: ProjectSpec) -> list[PlanePartitionSpec]:
    """Map each rail to exact SPD primitives and a disclosed modal-solver bbox."""

    spd_metadata = project.metadata.get("spd_import")
    if not isinstance(spd_metadata, Mapping):
        return []
    raw_records = spd_metadata.get(_SPD_PLANE_GEOMETRIES_KEY, [])
    if not isinstance(raw_records, list):
        return []
    records: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            continue
        layer = str(raw.get("layer", ""))
        net = str(raw.get("net", ""))
        if layer and net:
            records[(layer.casefold(), net.casefold())] = raw

    partitions: list[PlanePartitionSpec] = []
    rails_by_layer: dict[str, list[RailSpec]] = {}
    for rail in project.rails:
        if (rail.pwr_layer.casefold(), rail.net.casefold()) in records:
            rails_by_layer.setdefault(rail.pwr_layer, []).append(rail)
    for layer, rails in sorted(rails_by_layer.items(), key=lambda item: item[0].casefold()):
        cells: list[PlaneCell] = []
        mapping: dict[str, str] = {}
        warnings: list[str] = []
        for column, rail in enumerate(rails):
            record = records[(layer.casefold(), rail.net.casefold())]
            bounds = _spd_geometry_bounds(record)
            if bounds is None:
                continue
            positive_polygons = list(record.get("positive_polygons_um", []))
            negative_polygons = list(record.get("negative_polygons_um", []))
            positive_circles = list(record.get("positive_circles_um", []))
            negative_circles = list(record.get("negative_circles_um", []))
            geometry_asset = str(record.get("asset", "")) or None
            geometry_sha256 = str(record.get("asset_sha256", "")) or None
            exact_rectangle = bool(
                record.get("solver_exact_rectangle", False)
                or (
                    len(positive_polygons) == 1
                    and _axis_aligned_rectangle(positive_polygons[0])
                    and not negative_polygons
                    and not positive_circles
                    and not negative_circles
                )
            )
            solver_geometry = (
                "spd_axis_aligned_rectangle"
                if exact_rectangle
                else "spd_bounding_box"
            )
            cell_id = f"spd-{column}-{_safe_id(rail.domain)}"
            x_min, x_max, y_min, y_max = bounds
            cells.append(
                PlaneCell(
                    cell_id=cell_id,
                    row=0,
                    column=column,
                    x_min_um=x_min,
                    x_max_um=x_max,
                    y_min_um=y_min,
                    y_max_um=y_max,
                    source_net=rail.net,
                    source_geometry_asset=geometry_asset,
                    source_geometry_sha256=geometry_sha256,
                    source_positive_polygons_um=positive_polygons,
                    source_negative_polygons_um=negative_polygons,
                    source_positive_circles_um=positive_circles,
                    source_negative_circles_um=negative_circles,
                    source_primitive_order=list(record.get("primitive_order", [])),
                    source_positive_subelement_count=int(
                        record.get("positive_subelement_count", 0)
                    ),
                    source_negative_subelement_count=int(
                        record.get("negative_subelement_count", 0)
                    ),
                    solver_geometry=solver_geometry,
                )
            )
            mapping[rail.domain] = cell_id
            if not exact_rectangle:
                warnings.append(
                    f"{rail.domain}: exact SPD primitives are retained; the current "
                    "rectangular modal solver uses their per-net bounding box"
                )
        if cells:
            partitions.append(
                PlanePartitionSpec(
                    layer=layer,
                    rows=1,
                    columns=len(cells),
                    domain_to_cell=mapping,
                    cells=cells,
                    split_gap_um=project.split_gap_um,
                    confidence="HIGH",
                    confidence_reason=(
                        "direct PowerSI .Shape layer/net primitives; solver geometry "
                        "is declared independently per cell"
                    ),
                    confirmed=False,
                    method="spd_actual",
                    warnings=warnings,
                )
            )
    return partitions


def _canonical_metadata_sha256(payload: Any) -> str:
    digest = sha256()
    for chunk in iter_canonical_json_bytes(payload):
        digest.update(chunk)
    return digest.hexdigest()


def _spd_layerwise_device_terminal_via_certificate(
    analysis: Any,
    *,
    device_pins: Sequence[Any],
    selected_power_nets: Sequence[str],
    ground_nets: Sequence[str],
    source_sha256: str,
) -> tuple[dict[str, Any], tuple[_SpdServiceDiagnostic, ...]]:
    """Persist exact Device source-Node to first-Via attachment evidence.

    The certificate is intentionally narrower than a terminal Via path: one
    complete row proves that a selected Device Connect pin is directly
    incident to exactly one same-NET Via on TOP.  That exact Via ID and its
    PadStackDef may be subtracted from the matching aggregate source population
    so the terminal-owned barrel is not stamped again as substrate.  The row
    does not by itself invent a terminal branch or finite Trace model.
    """

    source_digest = str(source_sha256).strip().casefold()
    power_by_key = {
        item.casefold(): item for item in _unique_strings(selected_power_nets)
    }
    ground_by_key = {
        item.casefold(): item for item in _unique_strings(ground_nets)
    }

    def optional_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def ground_key(net: str) -> str | None:
        key = net.casefold()
        if key in ground_by_key:
            return key
        match = re.fullmatch(r"(.+)/(\d+)", key)
        if match and match.group(1) in ground_by_key:
            return match.group(1)
        return None

    selected_pins = [
        pin
        for pin in device_pins
        if getattr(pin, "kind", None) == PinKind.DEVICE_BUMP
        and (
            (
                getattr(pin, "terminal", None) == TerminalKind.PWR
                and str(getattr(pin, "net", "")).strip().casefold()
                in power_by_key
            )
            or (
                getattr(pin, "terminal", None) == TerminalKind.GND
                and ground_key(str(getattr(pin, "net", "")).strip())
                is not None
            )
        )
    ]
    selected_pins.sort(
        key=lambda pin: (
            str(getattr(pin, "pin_id", "")).casefold(),
            str(getattr(pin, "pin_id", "")),
        )
    )

    evidence_by_pin: dict[str, list[Any]] = {}
    for endpoint in getattr(analysis, "device_terminal_via_endpoints", ()):
        pin_id = str(getattr(endpoint, "pin_id", "")).strip()
        if pin_id:
            evidence_by_pin.setdefault(pin_id.casefold(), []).append(endpoint)
    for items in evidence_by_pin.values():
        items.sort(
            key=lambda endpoint: (
                str(getattr(endpoint, "incident_via_id", "")).casefold(),
                str(getattr(endpoint, "incident_padstack", "")).casefold(),
                str(getattr(endpoint, "status", "")).casefold(),
            )
        )

    pin_key_counts = Counter(
        str(getattr(pin, "pin_id", "")).strip().casefold()
        for pin in selected_pins
    )
    terminals: list[dict[str, Any]] = []
    for pin in selected_pins:
        pin_id = str(getattr(pin, "pin_id", "")).strip()
        pin_key = pin_id.casefold()
        candidates = evidence_by_pin.get(pin_key, ())
        endpoint = candidates[0] if len(candidates) == 1 else None
        source_node_id = optional_text(getattr(pin, "source_node_id", None))
        source_layer = optional_text(getattr(pin, "source_layer", None))
        source_padstack = optional_text(
            getattr(pin, "source_padstack", None)
        )
        issues: list[str] = []
        if pin_key_counts[pin_key] != 1:
            status = "duplicate_selected_pin_id"
            issues.append(status)
        elif not candidates:
            status = "endpoint_evidence_missing"
            issues.append(status)
        elif len(candidates) != 1:
            status = "endpoint_evidence_ambiguous"
            issues.append(status)
        else:
            status = str(getattr(endpoint, "status", "")).strip()
            issues.extend(
                str(item).strip()
                for item in tuple(getattr(endpoint, "issues", ()) or ())
                if str(item).strip()
            )
            evidence_identity = (
                optional_text(getattr(endpoint, "source_node_id", None)),
                optional_text(getattr(endpoint, "source_layer", None)),
                optional_text(getattr(endpoint, "source_padstack", None)),
                str(getattr(endpoint, "net", "")).strip().casefold(),
                float(getattr(endpoint, "source_x_um")),
                float(getattr(endpoint, "source_y_um")),
            )
            pin_identity = (
                source_node_id,
                source_layer,
                source_padstack,
                str(getattr(pin, "net", "")).strip().casefold(),
                float(getattr(pin, "x_um")),
                float(getattr(pin, "y_um")),
            )
            if evidence_identity != pin_identity:
                status = "pin_record_evidence_mismatch"
                issues.append(status)
        if not status:
            status = "endpoint_status_missing"
            issues.append(status)

        candidate_via_ids = tuple(
            str(item)
            for item in (
                tuple(getattr(endpoint, "candidate_via_ids", ()) or ())
                if endpoint is not None
                else ()
            )
        )
        endpoint_identity = _canonical_metadata_sha256(
            {
                "source_sha256": source_digest,
                "pin_id_key": pin_key,
                "source_node_id_key": (
                    source_node_id.casefold() if source_node_id else None
                ),
            }
        )
        terminals.append(
            {
                "endpoint_id": (
                    f"spd-device-terminal-via:{endpoint_identity[:24]}"
                ),
                "pin_id": pin_id,
                "refdes": str(getattr(pin, "refdes", "")).strip(),
                "pin": str(getattr(pin, "pin", "")).strip(),
                "terminal": str(getattr(pin, "terminal", "")),
                "net": str(getattr(pin, "net", "")).strip(),
                "source_node_id": source_node_id,
                "source_layer": source_layer,
                "source_padstack": source_padstack,
                "source_x_um": float(getattr(pin, "x_um")),
                "source_y_um": float(getattr(pin, "y_um")),
                "incident_via_id": (
                    optional_text(getattr(endpoint, "incident_via_id", None))
                    if endpoint is not None
                    else None
                ),
                "incident_net": (
                    optional_text(getattr(endpoint, "incident_net", None))
                    if endpoint is not None
                    else None
                ),
                "incident_padstack": (
                    optional_text(getattr(endpoint, "incident_padstack", None))
                    if endpoint is not None
                    else None
                ),
                "incident_opposite_node_id": (
                    optional_text(
                        getattr(endpoint, "incident_opposite_node_id", None)
                    )
                    if endpoint is not None
                    else None
                ),
                "candidate_count": (
                    int(getattr(endpoint, "candidate_count", 0))
                    if endpoint is not None
                    else 0
                ),
                "candidate_via_ids_sha256": _canonical_metadata_sha256(
                    candidate_via_ids
                ),
                "status": status,
                "issues": sorted(set(issues)),
            }
        )

    incomplete = [item for item in terminals if item["status"] != "complete"]
    source_hash_valid = bool(re.fullmatch(r"[0-9a-f]{64}", source_digest))
    payload: dict[str, Any] = {
        "schema_version": _SPD_LAYERWISE_DEVICE_TERMINAL_VIA_SCHEMA,
        "compiler_id": _SPD_LAYERWISE_DEVICE_TERMINAL_VIA_COMPILER,
        "source_sha256": source_digest,
        "raw_spd_embedded": False,
        "scope": {
            "selected_power_nets": sorted(
                power_by_key.values(), key=str.casefold
            ),
            "ground_nets": sorted(ground_by_key.values(), key=str.casefold),
        },
        "terminals": terminals,
        "terminal_count": len(terminals),
        "complete_terminal_count": len(terminals) - len(incomplete),
        "incomplete_terminal_count": len(incomplete),
        "status": (
            "complete"
            if source_hash_valid and bool(terminals) and not incomplete
            else "incomplete"
        ),
    }
    certificate = {
        **payload,
        "evidence_sha256": _canonical_metadata_sha256(payload),
    }

    diagnostics: list[_SpdServiceDiagnostic] = []
    if not source_hash_valid:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_DEVICE_TERMINAL_VIA_SOURCE_HASH_INVALID",
                message=(
                    "Device terminal first-Via certificate has no valid "
                    "64-digit source SHA-256; layerwise terminal proof must "
                    "reject it."
                ),
            )
        )
    if not terminals:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_DEVICE_TERMINAL_VIAS_MISSING",
                message=(
                    "No selected Device PWR/GND terminal was available for "
                    "source first-Via attachment proof."
                ),
            )
        )
    if incomplete:
        counts = Counter(str(item["status"]) for item in incomplete)
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_DEVICE_TERMINAL_VIA_INCOMPLETE",
                message=(
                    f"{len(incomplete)} of {len(terminals)} selected Device "
                    "terminal first-Via attachment(s) are incomplete: "
                    + ", ".join(
                        f"{status}={count}"
                        for status, count in sorted(counts.items())
                    )
                    + ". No endpoint is inferred by distance or envelope."
                ),
            )
        )
    return certificate, tuple(diagnostics)


def _spd_layerwise_via_group_certificate(
    analysis: Any,
    stackup_layers: Sequence[StackupLayer],
    *,
    selected_power_nets: Sequence[str],
    ground_nets: Sequence[str],
    source_sha256: str,
    device_terminal_via_certificate: Mapping[str, Any],
    terminal_owned_refdes: Iterable[str] | None = None,
) -> tuple[dict[str, Any], tuple[_SpdServiceDiagnostic, ...]]:
    """Persist compact source Via populations for a later layerwise compiler.

    ``SpdViaUsage`` is the parser's exact aggregate count for one NET and
    PadStackDef.  This certificate binds those counts to deterministic physical
    conductor endpoints without retaining, reopening, or rescanning the raw
    SPD.  Complete Device-terminal first-Via certificate rows contribute their
    exact incident Via IDs to the same ``(NET, PadStackDef)`` owner set as exact
    decap landings.  An incomplete endpoint blocks only the exact group named by
    its observed incident NET/padstack; Device-pin presence alone never widens
    that uncertainty.  Missing PadStackDef, layer, drill, and ownership evidence
    remains explicit; no fallback dimensions or invented endpoints are written.
    """

    source_digest = str(source_sha256).strip().casefold()
    power_by_key = {
        item.casefold(): item
        for item in _unique_strings(selected_power_nets)
    }
    ground_by_key = {
        item.casefold(): item
        for item in _unique_strings(ground_nets)
    }
    selected_keys = set(power_by_key) | set(ground_by_key)

    conductor_rows = [item for item in stackup_layers if item.is_conductor]
    conductor_positions: dict[str, list[int]] = {}
    for index, layer in enumerate(conductor_rows):
        conductor_positions.setdefault(layer.name.casefold(), []).append(index)
    layer_centers = _spd_layer_center_depths(stackup_layers)

    padstacks_by_key: dict[str, list[Any]] = {}
    for padstack in getattr(analysis, "padstacks", ()):
        key = str(getattr(padstack, "name", "")).strip().casefold()
        if key:
            padstacks_by_key.setdefault(key, []).append(padstack)

    # Decap connection classification preserves exact source landing Via IDs.
    # The separately hashed Device-terminal certificate preserves exact source
    # first-Via IDs.  Only complete rows become owners; incomplete rows can
    # affect a group only when their observed incident NET and padstack name it.
    terminal_vias: dict[tuple[str, str], set[str]] = {}
    owned_refdes_keys = (
        {str(item).strip().casefold() for item in terminal_owned_refdes}
        if terminal_owned_refdes is not None
        else None
    )
    for connection in getattr(analysis, "decap_connections", ()):
        if owned_refdes_keys is not None and (
            str(getattr(connection, "refdes", "")).strip().casefold()
            not in owned_refdes_keys
        ):
            continue
        landings = (
            *tuple(getattr(connection, "power_vias", ()) or ()),
            *tuple(getattr(connection, "ground_vias", ()) or ()),
        )
        for landing in landings:
            net_key = str(getattr(landing, "net", "")).strip().casefold()
            padstack_key = (
                str(getattr(landing, "padstack", "")).strip().casefold()
            )
            via_id = str(getattr(landing, "via_id", "")).strip().casefold()
            if net_key in selected_keys and padstack_key and via_id:
                terminal_vias.setdefault((net_key, padstack_key), set()).add(via_id)

    terminal_certificate = device_terminal_via_certificate
    if (
        not isinstance(terminal_certificate, Mapping)
        or terminal_certificate.get("schema_version")
        != _SPD_LAYERWISE_DEVICE_TERMINAL_VIA_SCHEMA
        or terminal_certificate.get("compiler_id")
        != _SPD_LAYERWISE_DEVICE_TERMINAL_VIA_COMPILER
    ):
        raise ValueError(
            "layerwise Device-terminal Via certificate schema/compiler is invalid"
        )
    terminal_source_sha256 = str(
        terminal_certificate.get("source_sha256", "")
    ).strip().casefold()
    if terminal_source_sha256 != source_digest:
        raise ValueError(
            "layerwise Device-terminal Via certificate source SHA-256 mismatches"
        )
    terminal_evidence_sha256 = str(
        terminal_certificate.get("evidence_sha256", "")
    ).strip().casefold()
    terminal_unsigned = {
        key: terminal_certificate[key]
        for key in terminal_certificate
        if key != "evidence_sha256"
    }
    if (
        not re.fullmatch(r"[0-9a-f]{64}", terminal_evidence_sha256)
        or _canonical_metadata_sha256(terminal_unsigned)
        != terminal_evidence_sha256
    ):
        raise ValueError(
            "layerwise Device-terminal Via certificate evidence SHA-256 mismatches"
        )
    terminal_rows_raw = terminal_certificate.get("terminals")
    if not isinstance(terminal_rows_raw, Sequence) or isinstance(
        terminal_rows_raw, (str, bytes)
    ):
        raise ValueError(
            "layerwise Device-terminal Via certificate terminal rows are invalid"
        )
    terminal_rows = tuple(terminal_rows_raw)
    if not all(isinstance(item, Mapping) for item in terminal_rows):
        raise ValueError(
            "layerwise Device-terminal Via certificate has a non-mapping row"
        )

    incomplete_device_endpoints_by_group: dict[
        tuple[str, str], set[str]
    ] = {}
    unmapped_incomplete_device_endpoint_ids: set[str] = set()
    complete_device_owned_via_ids: set[str] = set()
    for row in terminal_rows:
        endpoint_id = str(row.get("endpoint_id", "")).strip()
        status = str(row.get("status", "")).strip()
        incident_net_key = str(row.get("incident_net", "") or "").strip().casefold()
        incident_padstack_key = (
            str(row.get("incident_padstack", "") or "").strip().casefold()
        )
        incident_via_id = str(
            row.get("incident_via_id", "") or ""
        ).strip().casefold()
        if status == "complete":
            if (
                not endpoint_id
                or incident_net_key not in selected_keys
                or not incident_padstack_key
                or not incident_via_id
            ):
                raise ValueError(
                    "complete Device-terminal Via row has incomplete incident identity"
                )
            terminal_vias.setdefault(
                (incident_net_key, incident_padstack_key), set()
            ).add(incident_via_id)
            complete_device_owned_via_ids.add(incident_via_id)
            continue
        if (
            endpoint_id
            and incident_net_key in selected_keys
            and incident_padstack_key
        ):
            incomplete_device_endpoints_by_group.setdefault(
                (incident_net_key, incident_padstack_key), set()
            ).add(endpoint_id)
        elif endpoint_id:
            unmapped_incomplete_device_endpoint_ids.add(endpoint_id)

    raw_usages = [
        usage
        for usage in getattr(analysis, "via_usage", ())
        if str(getattr(usage, "net", "")).strip().casefold() in selected_keys
    ]
    raw_usages.sort(
        key=lambda item: (
            str(getattr(item, "net", "")).strip().casefold(),
            str(getattr(item, "padstack", "")).strip().casefold(),
            str(getattr(item, "net", "")).strip(),
            str(getattr(item, "padstack", "")).strip(),
        )
    )

    seen_usage_keys: set[tuple[str, str]] = set()
    groups: list[dict[str, Any]] = []
    for usage in raw_usages:
        net = str(getattr(usage, "net", "")).strip()
        padstack_name = str(getattr(usage, "padstack", "")).strip()
        net_key = net.casefold()
        padstack_key = padstack_name.casefold()
        usage_key = (net_key, padstack_key)
        issues: list[str] = []

        if usage_key in seen_usage_keys:
            issues.append("duplicate_net_padstack_usage_record")
        seen_usage_keys.add(usage_key)

        try:
            count = int(getattr(usage, "count"))
        except (TypeError, ValueError):
            count = None
            issues.append("via_count_not_an_integer")
        if count is not None and count <= 0:
            issues.append("via_count_not_positive")

        definitions = padstacks_by_key.get(padstack_key, [])
        padstack = definitions[0] if len(definitions) == 1 else None
        if not definitions:
            issues.append("padstack_definition_missing")
        elif len(definitions) > 1:
            issues.append("padstack_definition_ambiguous")

        declared_layers = _unique_strings(
            getattr(padstack, "layers", ()) if padstack is not None else ()
        )
        unknown_layers = [
            name
            for name in declared_layers
            if len(conductor_positions.get(name.casefold(), ())) != 1
        ]
        if unknown_layers:
            issues.append("padstack_conductor_layer_missing_or_ambiguous")
        known_layers = [
            name
            for name in declared_layers
            if len(conductor_positions.get(name.casefold(), ())) == 1
        ]
        conductor_layers = sorted(
            known_layers,
            key=lambda name: conductor_positions[name.casefold()][0],
        )
        if len(conductor_layers) < 2:
            issues.append("fewer_than_two_physical_conductor_layers")

        start_layer = conductor_layers[0] if conductor_layers else None
        end_layer = conductor_layers[-1] if conductor_layers else None
        physical_layer_span: list[str] = []
        if start_layer is not None and end_layer is not None:
            start_index = conductor_positions[start_layer.casefold()][0]
            end_index = conductor_positions[end_layer.casefold()][0]
            physical_layer_span = [
                item.name for item in conductor_rows[start_index : end_index + 1]
            ]

        def source_dimension(name: str) -> float | None:
            if padstack is None:
                return None
            value = getattr(padstack, name, None)
            if value is None:
                return None
            try:
                number = float(value)
            except (TypeError, ValueError):
                issues.append(f"{name}_not_positive_finite")
                return None
            if not math.isfinite(number) or number <= 0.0:
                issues.append(f"{name}_not_positive_finite")
                return None
            return number

        drill_diameter_um = source_dimension("drill_diameter_um")
        pad_width_um = source_dimension("pad_width_um")
        pad_height_um = source_dimension("pad_height_um")
        if drill_diameter_um is None:
            issues.append("drill_diameter_um_missing")
        raw_material = (
            getattr(padstack, "material", None)
            if padstack is not None
            else None
        )
        material = str(raw_material).strip() if raw_material is not None else ""
        material = material or None
        optional_missing = [
            name
            for name, value in (
                ("pad_width_um", pad_width_um),
                ("pad_height_um", pad_height_um),
                ("material", material),
            )
            if value in (None, "")
        ]

        identity_digest = _canonical_metadata_sha256(
            {
                "source_sha256": source_digest,
                "net_key": net_key,
                "padstack_key": padstack_key,
            }
        )
        group_id = f"spd-via-group:{identity_digest[:24]}"
        owner_id = f"source-via-population:{identity_digest}"
        segments: list[dict[str, Any]] = []
        if count is not None and count > 0 and len(conductor_layers) >= 2:
            for index, (upper, lower) in enumerate(
                zip(conductor_layers, conductor_layers[1:], strict=False)
            ):
                length_um = abs(
                    float(layer_centers[lower]) - float(layer_centers[upper])
                )
                if not math.isfinite(length_um) or length_um <= 0.0:
                    issues.append("segment_length_not_positive_finite")
                    continue
                segment_digest = _canonical_metadata_sha256(
                    {
                        "group_id": group_id,
                        "ordinal": index,
                        "start_layer": upper.casefold(),
                        "end_layer": lower.casefold(),
                    }
                )
                segments.append(
                    {
                        "segment_id": f"spd-via-segment:{segment_digest[:24]}",
                        "owner_id": owner_id,
                        "ordinal": index,
                        "start_layer": upper,
                        "end_layer": lower,
                        "length_um": length_um,
                        "count": count,
                    }
                )

        owned_ids = sorted(terminal_vias.get(usage_key, ()))
        terminal_owned_count = len(owned_ids)
        incomplete_device_endpoint_ids = sorted(
            incomplete_device_endpoints_by_group.get(usage_key, ())
        )
        ownership_issues: list[str] = []
        if count is not None and terminal_owned_count > count:
            ownership_issues.append("terminal_owned_count_exceeds_via_count")
        if incomplete_device_endpoint_ids:
            ownership_issues.append(
                "incomplete_device_terminal_endpoint_affects_group"
            )
        ownership_complete = not ownership_issues
        substrate_count = (
            count - terminal_owned_count
            if ownership_complete and count is not None and count > 0
            else None
        )

        groups.append(
            {
                "group_id": group_id,
                "owner_id": owner_id,
                "net": net,
                "role": "power" if net_key in power_by_key else "ground",
                "padstack": padstack_name,
                "count": count,
                "declared_layers": declared_layers,
                "conductor_layers": conductor_layers,
                "start_layer": start_layer,
                "end_layer": end_layer,
                "physical_layer_span": physical_layer_span,
                "drill_diameter_um": drill_diameter_um,
                "pad_width_um": pad_width_um,
                "pad_height_um": pad_height_um,
                "material": material,
                "segments": segments,
                "status": "complete" if not issues else "incomplete",
                "issues": sorted(set(issues)),
                "missing_optional_fields": sorted(set(optional_missing)),
                "terminal_owned_count": terminal_owned_count,
                "terminal_owned_via_ids_sha256": _canonical_metadata_sha256(
                    owned_ids
                ),
                "terminal_ownership_method": (
                    "exact-decap-landing-and-device-incident-via-id-v2"
                ),
                "incomplete_device_terminal_endpoint_count": len(
                    incomplete_device_endpoint_ids
                ),
                "incomplete_device_terminal_endpoint_ids_sha256": (
                    _canonical_metadata_sha256(incomplete_device_endpoint_ids)
                ),
                "ownership_status": (
                    "complete" if ownership_complete else "unresolved"
                ),
                "ownership_issues": sorted(set(ownership_issues)),
                "substrate_count": substrate_count,
            }
        )

    covered_power_keys = {
        str(item["net"]).casefold()
        for item in groups
        if item["role"] == "power"
    }
    covered_ground_keys = {
        str(item["net"]).casefold()
        for item in groups
        if item["role"] == "ground"
    }
    missing_power_nets = sorted(
        (power_by_key[key] for key in set(power_by_key) - covered_power_keys),
        key=str.casefold,
    )
    missing_ground_nets = sorted(
        (ground_by_key[key] for key in set(ground_by_key) - covered_ground_keys),
        key=str.casefold,
    )
    incomplete_groups = [item for item in groups if item["status"] != "complete"]
    unresolved_ownership = [
        item for item in groups if item["ownership_status"] != "complete"
    ]
    compiled_usage_keys = {
        (str(item["net"]).casefold(), str(item["padstack"]).casefold())
        for item in groups
    }
    affecting_incomplete_device_endpoint_ids = {
        endpoint_id
        for group_key, endpoint_ids in incomplete_device_endpoints_by_group.items()
        if group_key in compiled_usage_keys
        for endpoint_id in endpoint_ids
    }
    nonaffecting_incomplete_device_endpoint_ids = {
        *unmapped_incomplete_device_endpoint_ids,
        *(
            endpoint_id
            for group_key, endpoint_ids in incomplete_device_endpoints_by_group.items()
            if group_key not in compiled_usage_keys
            for endpoint_id in endpoint_ids
        ),
    }
    source_hash_valid = bool(re.fullmatch(r"[0-9a-f]{64}", source_digest))
    payload: dict[str, Any] = {
        "schema_version": _SPD_LAYERWISE_VIA_GROUP_SCHEMA,
        "compiler_id": _SPD_LAYERWISE_VIA_GROUP_COMPILER,
        "source_sha256": source_digest,
        "raw_spd_embedded": False,
        "scope": {
            "selected_power_nets": sorted(power_by_key.values(), key=str.casefold),
            "ground_nets": sorted(ground_by_key.values(), key=str.casefold),
        },
        "groups": groups,
        "group_count": len(groups),
        "complete_group_count": len(groups) - len(incomplete_groups),
        "ownership_resolved_group_count": len(groups) - len(unresolved_ownership),
        "device_terminal_via_evidence_sha256": terminal_evidence_sha256,
        "device_terminal_certificate_status": str(
            terminal_certificate.get("status", "")
        ),
        "complete_device_terminal_owned_via_count": len(
            complete_device_owned_via_ids
        ),
        "affecting_incomplete_device_terminal_endpoint_count": len(
            affecting_incomplete_device_endpoint_ids
        ),
        "nonaffecting_incomplete_device_terminal_endpoint_count": len(
            nonaffecting_incomplete_device_endpoint_ids
        ),
        "nonaffecting_incomplete_device_terminal_endpoint_ids_sha256": (
            _canonical_metadata_sha256(
                sorted(nonaffecting_incomplete_device_endpoint_ids)
            )
        ),
        "missing_power_nets": missing_power_nets,
        "missing_ground_nets": missing_ground_nets,
        "status": (
            "complete"
            if source_hash_valid
            and not incomplete_groups
            and not unresolved_ownership
            and not missing_power_nets
            and bool(groups)
            else "incomplete"
        ),
    }
    certificate = {
        **payload,
        "evidence_sha256": _canonical_metadata_sha256(payload),
    }

    diagnostics: list[_SpdServiceDiagnostic] = []
    if not source_hash_valid:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_VIA_SOURCE_HASH_INVALID",
                message=(
                    "Layerwise Via-group certificate has no valid 64-digit source "
                    "SHA-256; the layerwise compiler must reject it."
                ),
            )
        )
    if not groups:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_VIA_GROUPS_MISSING",
                message=(
                    "No selected-PWR/configured-GND source Via usage group was "
                    "available; no layerwise Via branch was fabricated."
                ),
            )
        )
    if incomplete_groups:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_VIA_GROUP_INCOMPLETE",
                message=(
                    f"{len(incomplete_groups)} layerwise Via group(s) have "
                    "incomplete source padstack/count/layer/drill evidence; those "
                    "groups are retained as incomplete and cannot be stamped."
                ),
            )
        )
    if unresolved_ownership:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_VIA_OWNERSHIP_UNRESOLVED",
                message=(
                    f"{len(unresolved_ownership)} layerwise Via group(s) have "
                    "an incomplete Device-terminal endpoint whose observed "
                    "incident NET/padstack maps to that group, or another exact "
                    "ownership-count conflict. substrate_count remains null only "
                    "for those affected groups to prevent double stamping."
                ),
            )
        )
    if nonaffecting_incomplete_device_endpoint_ids:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_DEVICE_TERMINAL_ENDPOINT_NONAFFECTING",
                message=(
                    f"{len(nonaffecting_incomplete_device_endpoint_ids)} incomplete "
                    "Device-terminal first-Via endpoint(s) do not name a retained "
                    "selected-NET/padstack Via group. They are disclosed and hash-"
                    "bound but do not widen uncertainty to unrelated groups."
                ),
            )
        )
    if missing_power_nets:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_POWER_VIA_GROUP_MISSING",
                message=(
                    "No source Via usage group was found for selected PWR NET(s): "
                    + ", ".join(missing_power_nets)
                ),
            )
        )
    if groups and not covered_ground_keys:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_LAYERWISE_GROUND_VIA_GROUP_MISSING",
                message=(
                    "No configured-GND source Via usage group was found; no ground "
                    "Via population was fabricated."
                ),
            )
        )
    return certificate, tuple(diagnostics)

def build_spd_import_plan(
    current: ProjectSpec,
    analysis: Any,
    source_path: Path,
    *,
    selected_pairs: Mapping[str, PlanePairSuggestion] | None = None,
    selected_pair_provenance: Mapping[str, Mapping[str, Any]] | None = None,
    _geometry_from_plan: SpdImportPlan | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> SpdImportPlan:
    report = progress or _noop_progress
    cancelled = is_cancelled or _never_cancelled

    def check_cancelled() -> None:
        if cancelled():
            raise RuntimeError("SPD import-plan construction cancelled")

    check_cancelled()
    report(0, "Preparing retained PowerSI plane geometry")
    diagnostics: list[Any] = list(analysis.diagnostics)
    ground_nets = _unique_strings([*current.gnd_aliases, *analysis.ground_nets])
    ground_keys = {item.casefold() for item in ground_nets}
    selected_power_nets = _unique_strings(analysis.power_plane_nets)
    selected_power_keys = {item.casefold() for item in selected_power_nets}
    source_name, source_size, source_hash = _spd_source_identity(
        analysis.source, source_path
    )
    reused_geometry = (
        _reuse_spd_plane_geometry_assets(
            _geometry_from_plan,
            source_name=source_name,
            source_size=source_size,
            source_hash=source_hash,
            retained_net_keys=selected_power_keys | ground_keys,
            selected_power_keys=selected_power_keys,
            ground_nets=ground_keys,
        )
        if _geometry_from_plan is not None
        else None
    )
    if reused_geometry is None:
        geometry_asset_kwargs: dict[str, Any] = {}
        if progress is not None:
            geometry_asset_kwargs["progress"] = lambda value, message: report(
                round(max(0, min(100, value)) * 0.30), message
            )
        if is_cancelled is not None:
            geometry_asset_kwargs["is_cancelled"] = cancelled
        plane_geometry_payload, plane_geometry_assets = _spd_plane_geometry_assets(
            analysis, selected_power_keys | ground_keys, diagnostics,
            **geometry_asset_kwargs,
        )
        check_cancelled()
        report(30, "Retained plane geometry assets compressed")
    else:
        plane_geometry_payload, plane_geometry_assets = reused_geometry
    incomplete_plane_primitives = any(
        str(getattr(item, "code", ""))
        in {
            "SPD_PLANE_PRIMITIVE_UNSUPPORTED",
            "SPD_PLANE_PRIMITIVE_MALFORMED",
        }
        for item in analysis.diagnostics
    )
    active_cap_refdes = {
        item.refdes.casefold()
        for item in analysis.cap_instances
        if item.mounted
        and bool(getattr(item, "geometry_present", True))
        and item.power_net.casefold() in selected_power_keys
        and item.model_id in analysis.cap_models
    }

    # Keep the parser's full positive-NET occupancy on every conductor.  This
    # is deliberately broader than selected_power_nets: rail creation remains
    # selected-PWR-only, while ground-purity checks must see incidental/signal
    # copper that shares a proposed reference layer.
    layers = list(analysis.stackup_layers)
    mixed_reference_failures: list[dict[str, Any]] = []
    mixed_reference_kwargs: dict[str, Any] = {}
    if progress is not None:
        mixed_reference_kwargs["progress"] = lambda value, message: report(
            30 + round(max(0, min(100, value)) * 0.40), message
        )
    if is_cancelled is not None:
        mixed_reference_kwargs["is_cancelled"] = cancelled
    mixed_reference_certificates = _mixed_reference_certificates(
        plane_geometry_payload,
        plane_geometry_assets,
        layers,
        power_keys=selected_power_keys,
        ground_keys=ground_keys,
        failures=mixed_reference_failures,
        **mixed_reference_kwargs,
    )
    check_cancelled()
    report(70, "Mixed-reference artwork certification complete")
    diagnostics.extend(
        _SpdServiceDiagnostic(
            severity="error" if item.get("blocking") else "warning",
            code=str(
                item.get("code", "SPD_MIXED_REFERENCE_CERTIFICATE_REJECTED")
            ),
            message=(
                f"Rejected mixed-reference candidate {item['rail_net']} "
                f"{item['pwr_layer']}/{item['gnd_layer']}: {item['reason']}. "
                "The candidate is not eligible for rail selection."
            ),
        )
        for item in mixed_reference_failures
    )
    pins = [
        item
        for item in analysis.pins
        if (
            item.kind == PinKind.DEVICE_BUMP
            and (
                (
                    item.terminal == TerminalKind.PWR
                    and item.net.casefold() in selected_power_keys
                )
                or (
                    item.terminal == TerminalKind.GND
                    and item.net.casefold() in ground_keys
                )
            )
        )
        or (
            item.kind == PinKind.DECAP_PAD
            and item.refdes.casefold() in active_cap_refdes
        )
    ]
    source_name, source_size, source_hash = _spd_source_identity(
        analysis.source, source_path
    )
    device_terminal_via_certificate, device_terminal_via_diagnostics = (
        _spd_layerwise_device_terminal_via_certificate(
            analysis,
            device_pins=pins,
            selected_power_nets=selected_power_nets,
            ground_nets=ground_nets,
            source_sha256=source_hash,
        )
    )
    diagnostics.extend(device_terminal_via_diagnostics)
    layerwise_via_certificate, layerwise_via_diagnostics = (
        _spd_layerwise_via_group_certificate(
            analysis,
            layers,
            selected_power_nets=selected_power_nets,
            ground_nets=ground_nets,
            source_sha256=source_hash,
            device_terminal_via_certificate=device_terminal_via_certificate,
            terminal_owned_refdes=active_cap_refdes,
        )
    )
    diagnostics.extend(layerwise_via_diagnostics)
    check_cancelled()
    report(75, "Source Via ownership certificates compiled")
    counts = {
        str(key): int(value)
        for key, value in dict(analysis.counts).items()
        if isinstance(value, (int, np.integer))
    }
    assumptions = [
        "independent rail/domain Zii; inter-rail/site coupling not modeled",
        (
            "Selected-PWR PowerSI Polygon/PolygonTrace/Circle/Box source geometry "
            "is retained by layer/net; "
            "non-rectangular rails use a disclosed per-net bounding box in the "
            "current rectangular modal solver"
        ),
        (
            "Mixed-reference DGND artwork is retained and must pass a versioned "
            "ordered-boolean coverage certificate; the rectangular solver still "
            "assumes a continuous return across the selected rail rectangle"
        ),
        (
            "SPD via-loop R/L values are uncalibrated analytical estimates; "
            "no PowerSI fit is applied, so PowerSI or measurement comparison is "
            "required for validation"
        ),
        "PowerSI plane-pair and solver geometry require user confirmation",
    ]
    normalized_pair_provenance: dict[str, Mapping[str, Any]] = {}
    for raw_key, raw_value in (selected_pair_provenance or {}).items():
        folded_key = str(raw_key).casefold()
        if folded_key in normalized_pair_provenance:
            raise ValueError(
                "selected plane-pair provenance contains duplicate "
                f"case-insensitive rail key {raw_key!r}"
            )
        normalized_pair_provenance[folded_key] = raw_value
    display_net_by_key = {
        str(item.net).casefold(): str(item.net)
        for item in current.rails
    }
    display_net_by_key.update(
        {
            str(item).casefold(): str(item)
            for item in selected_power_nets
            if str(item).casefold() not in display_net_by_key
        }
    )
    metadata: dict[str, Any] = {
        "plane_pair_confirmed": False,
        "device_pairing_confirmed": False,
        "templates_calibrated": False,
        "spd_import": {
            "source_name": source_name,
            "source_size_bytes": source_size,
            "source_sha256": source_hash,
            "raw_spd_embedded": False,
            "selected_power_nets": selected_power_nets,
            "selected_power_net_count": len(selected_power_nets),
            "ground_nets": list(analysis.ground_nets),
            "counts": counts,
            _SPD_LAYERWISE_DEVICE_TERMINAL_VIAS_KEY: (
                device_terminal_via_certificate
            ),
            _SPD_LAYERWISE_VIA_GROUPS_KEY: layerwise_via_certificate,
            _SPD_PLANE_GEOMETRIES_KEY: plane_geometry_payload,
            "mixed_reference_certificate_failures": mixed_reference_failures,
            "source_geometry_fidelity": (
                "selected_pwr_incomplete_blocked"
                if incomplete_plane_primitives
                else "selected_pwr_exact_normalized_primitives"
                if plane_geometry_payload
                else "bounding_box_only"
            ),
            "source_geometry_scope": "selected_pwr_and_configured_ground_nets",
            "normalized_primitive_types": [
                "Polygon",
                "PolygonTrace",
                "Circle",
                "Box",
            ],
            "ground_reference_geometry": "exact_artwork_retained_for_mixed_reference_certification",
            "ground_source_primitives_retained": True,
            "selected_plane_pair_provenance": {
                display_net_by_key.get(
                    str(key).casefold(),
                    str(value.get("rail_net", key))
                    if isinstance(value, Mapping)
                    else str(key),
                )
                if isinstance(value, Mapping)
                else display_net_by_key.get(str(key).casefold(), str(key)): dict(value)
                for key, value in normalized_pair_provenance.items()
            },
        },
    }
    project_name = (
        f"{source_path.stem} PI Project"
        if current.name == "Untitled SPD Decap PI Scenario"
        or bool(current.metadata.get("demo", False))
        else current.name
    )
    outline = analysis.outline
    if outline is None:
        # The parser could not derive an envelope from any positive Shape
        # primitive.  Keep the current outline so the review dialog still
        # receives a plan with every diagnostic, and record the blocking error
        # that keeps ``can_apply`` false.
        outline = current.outline
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="error",
                code="SPD_OUTLINE_NOT_FOUND",
                message=(
                    "No usable PowerSI outline bounding box was extracted; the "
                    "current project outline is shown for review only and this "
                    "import cannot be applied."
                ),
            )
        )
    base = ProjectSpec(
        name=project_name,
        outline=outline,
        coordinate_transform=CoordinateTransform(source_unit=CoordinateUnit.MM),
        gnd_aliases=ground_nets,
        split_gap_um=current.split_gap_um,
        frequency=current.frequency,
        stackup_layers=layers,
        pins=pins,
        assumptions=assumptions,
        attachment_names=sorted(plane_geometry_assets, key=str.casefold),
        metadata=metadata,
    )
    rails = _preserve_spd_rail_preferences(
        _derive_rails(
            base,
            mixed_reference_certificates,
            selected_pairs=selected_pairs,
        ),
        current.rails,
    )
    project = _validated_project_copy(base, rails=rails)
    check_cancelled()
    report(80, "Power rails and exact geometry metadata normalized")

    if not selected_power_nets:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="error",
                code="SPD_NO_SELECTED_POWER_NETS",
                message="No selected PowerSI power-plane nets were found.",
            )
        )
    if not project.rails:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="error",
                code="SPD_NO_RAILS",
                message=(
                    "No rail could be formed from both a selected plane net and "
                    "device power-pin coordinates."
                ),
            )
        )

    via_templates, via_provenance = _spd_via_templates(project, analysis)
    missing_via_layer_matches = [
        details
        for details in via_provenance.values()
        if not details.get("power_layer_matched")
        or not details.get("ground_layer_matched")
    ]
    if missing_via_layer_matches:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_VIA_LAYER_MATCH_MISSING",
                message=(
                    f"{len(missing_via_layer_matches)} rail(s) have no observed "
                    "padstack spanning the selected PWR and/or GND reference "
                    "layer; conservative default via dimensions are used for "
                    "the missing path."
                ),
            )
        )
    template_by_rail = {
        str(details["rail_id"]): template_id
        for template_id, details in via_provenance.items()
    }
    rail_by_net = {item.net.casefold(): item for item in project.rails}
    pin_by_id = {item.pin_id.casefold(): item for item in pins}
    pins = [
        item.model_copy(
            update={
                "via_template_id": template_by_rail.get(
                    rail_by_net[item.net.casefold()].rail_id
                )
            }
        )
        if item.terminal == TerminalKind.PWR
        and item.net.casefold() in rail_by_net
        else item
        for item in pins
    ]

    frequencies = _frequency_array(current.frequency)
    model_instances = Counter(
        item.model_id
        for item in analysis.cap_instances
        if item.mounted
        and item.geometry_present
        and item.power_net.casefold() in rail_by_net
        and item.model_id in analysis.cap_models
    )
    footprint_by_model: dict[str, str] = {}
    for instance in analysis.cap_instances:
        footprint_by_model.setdefault(instance.model_id, instance.footprint or "GENERIC")
    cap_models: list[CapModel] = []
    for model_index, (model_id, parsed) in enumerate(
        sorted(
            analysis.cap_models.items(),
            key=lambda item: str(item[0]).casefold(),
        )
    ):
        if model_index % 32 == 0:
            check_cancelled()
        impedance = parsed.impedance(frequencies)
        cap_models.append(
            CapModel(
                model_id=str(model_id),
                impedance=[
                    ImpedanceSample(
                        frequency_hz=float(frequency),
                        real_ohm=float(value.real),
                        imag_ohm=float(value.imag),
                    )
                    for frequency, value in zip(frequencies, impedance, strict=True)
                ],
                footprint=footprint_by_model.get(str(model_id), "GENERIC"),
                inventory=model_instances[str(model_id)],
                source_hash=parsed.source_hash,
            )
        )

    topology_maps: list[TopologyMap] = []
    placements: list[DomainPlacement] = []
    skipped_inactive = 0
    skipped_outside_geometry = 0
    skipped_without_model = 0
    skipped_without_rail = 0
    used_slot_ids: set[str] = set()
    for instance_index, instance in enumerate(analysis.cap_instances):
        if instance_index % 128 == 0:
            check_cancelled()
        if not instance.mounted:
            skipped_inactive += 1
            continue
        if not instance.geometry_present:
            skipped_outside_geometry += 1
            continue
        if instance.model_id not in analysis.cap_models:
            skipped_without_model += 1
            continue
        rail = rail_by_net.get(instance.power_net.casefold())
        if rail is None:
            skipped_without_rail += 1
            continue
        template_id = template_by_rail.get(rail.rail_id)
        if template_id is None:
            skipped_without_rail += 1
            continue
        slot_id = instance.refdes
        if slot_id.casefold() in used_slot_ids:
            diagnostics.append(
                _SpdServiceDiagnostic(
                    severity="warning",
                    code="SPD_DUPLICATE_REFDES",
                    message=f"Duplicate capacitor reference {slot_id!r} was skipped.",
                )
            )
            continue
        used_slot_ids.add(slot_id.casefold())
        power_pin_id = f"{instance.refdes}:{instance.power_pin}"
        power_pin = pin_by_id.get(power_pin_id.casefold())
        x_um = power_pin.x_um if power_pin is not None else instance.power_x_um
        y_um = power_pin.y_um if power_pin is not None else instance.power_y_um
        footprint = instance.footprint or footprint_by_model.get(instance.model_id, "GENERIC")
        topology_maps.append(
            TopologyMap(
                slot_id=slot_id,
                x_um=x_um,
                y_um=y_um,
                allowed_rail_ids=[rail.rail_id],
                allowed_footprints=[footprint],
                topology=TopologyKind.DIRECT,
                zone="MID",
                via_template_id=template_id,
            )
        )
        placements.append(
            DomainPlacement(
                slot_id=slot_id,
                topology=TopologyKind.DIRECT,
                rail_id=rail.rail_id,
                cap_model_id=instance.model_id,
            )
        )

    check_cancelled()
    report(90, "Capacitor models and source placements normalized")
    metadata["spd_via_template_provenance"] = via_provenance
    metadata["spd_import"].update(
        {
            "mounted_cap_placements": len(placements),
            "inactive_cap_instances_excluded": skipped_inactive,
            "cap_instances_outside_selected_geometry_excluded": (
                skipped_outside_geometry
            ),
            "cap_instances_without_model_excluded": skipped_without_model,
            "cap_instances_outside_selected_rails_excluded": skipped_without_rail,
            "geometry_model": (
                "selected-PWR and configured-DGND PowerSI Polygon/PolygonTrace/"
                "Circle/Box add/subtract primitives retained by layer/net; mixed "
                "references require ordered-boolean coverage certification, while "
                "the modal solver uses a disclosed continuous rectangular return; "
                "each non-rectangular rail is evaluated with its disclosed per-net "
                "rectangular modal-solver bounding box"
            ),
        }
    )
    project = _validated_project_copy(
        project,
        pins=pins,
        cap_models=cap_models,
        via_templates=via_templates,
        topology_maps=topology_maps,
        placements=placements,
        metadata=metadata,
    )
    actual_partitions = _spd_plane_partitions(project)
    if actual_partitions:
        project = _validated_project_copy(project, partitions=actual_partitions)
        mapped_domains = {
            domain
            for partition in actual_partitions
            for domain in partition.domain_to_cell
        }
        approximation_cells = [
            cell
            for partition in actual_partitions
            for cell in partition.cells
            if cell.solver_geometry == "spd_bounding_box"
        ]
        metadata = {
            **project.metadata,
            "spd_import": {
                **project.metadata["spd_import"],
                "geometry_model": (
                    "exact normalized PowerSI Polygon/PolygonTrace/Circle/Box source "
                    "primitives by selected PWR and configured DGND layer/net; mixed "
                    "DGND source artwork is intersected for certificate coverage, and "
                    "the modal solver then uses a disclosed continuous rectangular return; "
                    "solver geometry is exact only for void-free axis-aligned rectangles, "
                    "otherwise a per-net bounding box"
                ),
                "actual_geometry_domain_count": len(mapped_domains),
                "solver_bbox_domain_count": len(approximation_cells),
            },
        }
        project = _validated_project_copy(project, metadata=metadata)
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="info",
                code="SPD_PLANE_GEOMETRY_APPLIED",
                message=(
                    f"PowerSI selected-PWR source primitives were assigned by layer/net to "
                    f"{len(mapped_domains)} rail domain(s); no equal-grid inference was used."
                ),
            )
        )
        if approximation_cells:
            diagnostics.append(
                _SpdServiceDiagnostic(
                    severity="warning",
                    code="SPD_PLANE_SOLVER_BBOX_APPROXIMATION",
                    message=(
                        f"Exact normalized PowerSI selected-PWR drawings are retained for "
                        "the Layerwise exact retained-surface uniform Maxwell-Y/global "
                        "Kron network and the Research exact-artwork uniform C00 term. "
                        "Research nonuniform modes and the Legacy modal path use a "
                        f"separate per-net bounding box for "
                        f"{len(approximation_cells)} non-rectangular, cutout, or disjoint "
                        "rail domain(s)."
                    ),
                )
            )
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_GROUND_CONTINUOUS_REFERENCE_ASSUMPTION",
                    message=(
                        "Mixed-reference DGND artwork and cutouts are retained for certificate "
                        "validation and the Layerwise exact retained-surface uniform "
                        "Maxwell-Y/global Kron network. Research nonuniform modes and "
                        "the Legacy modal path assume "
                        "a continuous DGND reference, so certificate-backed results remain "
                        "LOW geometry confidence."
                    ),
                )
            )
        missing_domains = [
            rail.domain for rail in project.rails if rail.domain not in mapped_domains
        ]
        if missing_domains:
            diagnostics.append(
                _SpdServiceDiagnostic(
                    severity="warning",
                    code="SPD_PLANE_GEOMETRY_MISSING",
                    message=(
                        "No matching PowerSI source primitive was found for the selected "
                        "PWR layer/net of: " + ", ".join(missing_domains)
                    ),
                )
            )
    else:
        metadata = {
            **project.metadata,
            "spd_import": {
                **project.metadata["spd_import"],
                "geometry_model": (
                    "whole selected-net bounding box fallback using the extracted "
                    "outline for each rail; selected PWR source primitives were not "
                    "available; the Layerwise exact retained-surface uniform Maxwell-Y/"
                    "global Kron network is unavailable; Research nonuniform and Legacy "
                    "modal paths assume a continuous DGND reference"
                ),
            },
        }
        project = _validated_project_copy(project, metadata=metadata)
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_POLYGON_BBOX_APPROXIMATION",
                message=(
                    "No selected layer/net source primitives were available; each rail "
                    "falls back to the whole extracted outline bounding box. The validated "
                    "fallback is confirmed by import default and remains clearly marked "
                    "for Geometry review."
                ),
            )
        )

    check_cancelled()
    report(95, "Exact plane partitions and geometry disclosures finalized")
    if not cap_models:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_NO_USABLE_CAP_MODELS",
                message="No populated passive capacitor subcircuit could be normalized.",
            )
        )
    if not topology_maps:
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_NO_DECAP_SLOTS",
                message="No mounted decap slot matched a selected PowerSI power-plane net.",
            )
        )

    geometry_domain_count = sum(
        len(item.domain_to_cell) for item in project.partitions
    )
    solver_bbox_count = sum(
        cell.solver_geometry == "spd_bounding_box"
        for partition in project.partitions
        for cell in partition.cells
    )
    summary_lines = (
        f"Stack-up: {len(project.stackup_layers)} layers",
        (
            "MLO outline (bounding box): "
            f"{project.outline.width_um / 1000:g} mm × "
            f"{project.outline.height_um / 1000:g} mm"
        ),
        (
            f"Selected power rails: {len(project.rails)} "
            f"({', '.join(item.net for item in project.rails)})"
        ),
        (
            "Normalized device/decap pins: "
            f"{sum(item.kind == PinKind.DEVICE_BUMP for item in project.pins):,} / "
            f"{sum(item.kind == PinKind.DECAP_PAD for item in project.pins):,}"
        ),
        (
            f"Mounted decaps: {len(project.placements):,} across "
            f"{len(project.cap_models)} usable SPICE models"
        ),
        (
            f"Via-loop templates: {len(project.via_templates)} "
            "(uncalibrated; physical padstack provenance retained)"
        ),
        (
            f"PowerSI PWR geometry: {geometry_domain_count} rail domain(s) across "
            f"{len(project.partitions)} PWR layer(s); {solver_bbox_count} domain(s) "
            "use disclosed Research-nonuniform/Legacy bounding boxes; normalized "
            "source primitives retained for the Layerwise exact retained-surface "
            "terminal-complete Maxwell-Y/global Kron network; no legacy modal "
            "one-port difference, synthesized topology-only surface capacitance, "
            "or synthesized fringing is added; Research/Legacy modal terms assume "
            "a continuous DGND reference"
            if project.partitions
            else "PowerSI plane geometry: unavailable; whole-outline fallback"
        ),
        "Original SPD: referenced by name/size/SHA-256 only; raw bytes are not embedded",
    )
    summary_payload = {
        "schema": "mlo-pdn-spd-import-summary-v1",
        "app_version": __version__,
        "source": {
            "name": source_name,
            "size_bytes": source_size,
            "sha256": source_hash,
            "raw_spd_embedded": False,
        },
        "extracted": {
            "stackup_layers": len(project.stackup_layers),
            "outline": project.outline.model_dump(mode="json"),
            "selected_power_nets": selected_power_nets,
            "rails": [item.model_dump(mode="json") for item in project.rails],
            "pins": len(project.pins),
            "cap_models": [item.model_id for item in project.cap_models],
            "mounted_decaps": len(project.placements),
            "via_templates": via_provenance,
            "plane_partitions": [
                {
                    "layer": item.layer,
                    "method": item.method,
                    "domains": sorted(item.domain_to_cell),
                    "solver_bbox_domains": sum(
                        cell.solver_geometry == "spd_bounding_box"
                        for cell in item.cells
                    ),
                }
                for item in project.partitions
            ],
            "source_counts": counts,
        },
        "diagnostics": [
            {
                "severity": str(getattr(item, "severity", "warning")),
                "code": str(getattr(item, "code", "SPD")),
                "message": str(getattr(item, "message", item)),
            }
            for item in diagnostics
        ],
    }
    attachments: dict[str, bytes] = {
        **plane_geometry_assets,
        f"inputs/{_safe_id(source_path.stem)}-spd-import.json": json.dumps(
            summary_payload, ensure_ascii=False, indent=2
        ).encode("utf-8")
    }
    cap_by_key = {item.model_id.casefold(): item for item in project.cap_models}
    cap_sources: dict[str, dict[str, Any]] = {
        item.model_id: {
            "origin": "powersi_spd",
            "source_name": f"PowerSI SPD embedded model {item.model_id}",
            "subckt_name": item.model_id,
            "source_hash": item.source_hash,
        }
        for item in project.cap_models
    }
    for asset_name, text in sorted(
        analysis.model_assets.items(), key=lambda item: str(item[0]).casefold()
    ):
        model_id = Path(str(asset_name)).stem
        cap = cap_by_key.get(model_id.casefold())
        if cap is None:
            continue
        source_bytes = str(text).encode("utf-8")
        asset_path = (
            f"cap_models/{_safe_id(cap.model_id)}-{cap.source_hash[:16]}.lib"
        )
        existing_payload = attachments.get(asset_path)
        if existing_payload is not None and existing_payload != source_bytes:
            diagnostics.append(
                _SpdServiceDiagnostic(
                    severity="error",
                    code="SPD_CAP_MODEL_ASSET_COLLISION",
                    message=(
                        f"Embedded cap model {cap.model_id!r} collides with another "
                        "source attachment; import was blocked."
                    ),
                )
            )
            continue
        attachments[asset_path] = source_bytes
        cap_sources[cap.model_id] = {
            "origin": "powersi_spd",
            "source_name": str(asset_name),
            "source_asset": asset_path,
            "subckt_name": cap.model_id,
            "source_hash": cap.source_hash,
        }
    project = _validated_project_copy(
        project,
        metadata={
            **project.metadata,
            _CAP_MODEL_SOURCES_KEY: cap_sources,
        },
        attachment_names=sorted(attachments, key=str.casefold),
    )
    check_cancelled()
    can_apply = not any(
        str(getattr(item, "severity", "")).casefold() == "error"
        for item in diagnostics
    )
    result = SpdImportPlan(
        project=project,
        attachments=attachments,
        diagnostics=tuple(diagnostics),
        source_name=source_name,
        source_size_bytes=source_size,
        source_sha256=source_hash,
        summary_lines=summary_lines,
        mixed_reference_certificates=tuple(mixed_reference_certificates),
        can_apply=can_apply,
    )
    check_cancelled()
    report(100, "SPD import plan complete")
    return result

def _spd_source_identity(source: Any, path: Path) -> tuple[str, int, str]:
    name = str(
        getattr(source, "name", None)
        or getattr(source, "source_name", None)
        or path.name
    )
    size = int(
        getattr(source, "size_bytes", None)
        or getattr(source, "source_size_bytes", None)
        or path.stat().st_size
    )
    digest = str(
        getattr(source, "sha256", None)
        or getattr(source, "source_sha256", None)
        or ""
    )
    if not digest:
        hasher = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    return name, size, digest

def _preserve_spd_rail_preferences(
    rails: Sequence[RailSpec], previous: Sequence[RailSpec]
) -> list[RailSpec]:
    del previous
    # This standalone evaluator has no optimization rail-state editor.  Every
    # rail backed by the imported SPD plane artwork is directly evaluable.
    return [rail.model_copy(update={"state": RailState.ACTIVE}) for rail in rails]

def _spd_via_templates(
    project: ProjectSpec, analysis: Any
) -> tuple[list[ViaLoopTemplate], dict[str, dict[str, Any]]]:
    padstack_by_name = {item.name.casefold(): item for item in analysis.padstacks}
    usages = list(analysis.via_usage)
    layer_centers = _spd_layer_center_depths(project.stackup_layers)
    top_conductor_layer = next(
        (layer.name for layer in project.stackup_layers if layer.is_conductor), None
    )
    ground_keys = {item.casefold() for item in project.gnd_aliases}
    templates: list[ViaLoopTemplate] = []
    provenance: dict[str, dict[str, Any]] = {}

    def matching_usage(
        *, net_keys: set[str], required_layer: str
    ) -> tuple[Any | None, Any | None]:
        required_key = required_layer.casefold()
        matches: list[tuple[Any, Any]] = []
        for usage in usages:
            if usage.net.casefold() not in net_keys:
                continue
            padstack = padstack_by_name.get(usage.padstack.casefold())
            if padstack is None:
                continue
            layer_keys = {str(name).casefold() for name in padstack.layers}
            if required_key in layer_keys:
                matches.append((usage, padstack))
        if not matches:
            return None, None
        return max(
            matches,
            key=lambda item: (item[0].count, item[0].padstack.casefold()),
        )

    for rail in project.rails:
        power_usage, power_padstack = matching_usage(
            net_keys={rail.net.casefold()}, required_layer=rail.pwr_layer
        )
        ground_usage, ground_padstack = matching_usage(
            net_keys=ground_keys, required_layer=rail.gnd_layer
        )
        matched_padstacks = [
            item for item in (power_padstack, ground_padstack) if item is not None
        ]
        pad_diameters = [
            float(item.pad_diameter_um)
            for item in matched_padstacks
            if item.pad_diameter_um is not None
        ]
        drill_diameters = [
            float(item.drill_diameter_um)
            for item in matched_padstacks
            if item.drill_diameter_um is not None
        ]
        port_width = max(pad_diameters, default=250.0)
        port_height = port_width
        # Use the smaller matched drill for a conservative uncalibrated loop-R
        # estimate when the power and ground structures differ.
        drill_diameter = min(drill_diameters, default=None)
        pwr_depth = layer_centers.get(rail.pwr_layer, 125.0)
        gnd_depth = layer_centers.get(rail.gnd_layer, 125.0)
        power_leg = _spd_via_leg_estimate(
            padstack=power_padstack,
            length_um=pwr_depth,
            start_layer=top_conductor_layer,
            end_layer=rail.pwr_layer,
            stackup_layers=project.stackup_layers,
        )
        ground_leg = _spd_via_leg_estimate(
            padstack=ground_padstack,
            length_um=gnd_depth,
            start_layer=top_conductor_layer,
            end_layer=rail.gnd_layer,
            stackup_layers=project.stackup_layers,
        )
        resistance, inductance = _spd_uncalibrated_loop_estimate(
            pwr_depth_um=pwr_depth,
            gnd_depth_um=gnd_depth,
            drill_diameter_um=drill_diameter,
            power_leg=power_leg,
            ground_leg=ground_leg,
        )
        template_id = f"SPD_{_safe_id(rail.rail_id)}_VIA"
        templates.append(
            ViaLoopTemplate(
                template_id=template_id,
                pwr_reference_layer=rail.pwr_layer,
                gnd_reference_layer=rail.gnd_layer,
                path_kind=ViaPathKind.STACKED,
                finite_port_width_um=max(port_width, 1.0),
                finite_port_height_um=max(port_height, 1.0),
                loop_resistance_ohm=resistance,
                loop_inductance_h=inductance,
            )
        )
        provenance[template_id] = {
            "rail_id": rail.rail_id,
            "power_net": rail.net,
            "padstack": power_padstack.name if power_padstack is not None else None,
            "drill_diameter_um": drill_diameter,
            "pad_diameter_um": max(pad_diameters) if pad_diameters else None,
            "pad_width_um": port_width if pad_diameters else None,
            "pad_height_um": port_height if pad_diameters else None,
            "padstack_layers": (
                list(power_padstack.layers) if power_padstack is not None else []
            ),
            "power_layer_matched": power_padstack is not None,
            "power_usage_count": int(power_usage.count) if power_usage is not None else 0,
            "ground_padstack": ground_usage.padstack if ground_usage is not None else None,
            "ground_padstack_layers": (
                list(ground_padstack.layers) if ground_padstack is not None else []
            ),
            "ground_layer_matched": ground_padstack is not None,
            "ground_usage_count": int(ground_usage.count) if ground_usage is not None else 0,
            "pwr_depth_um": pwr_depth,
            "gnd_depth_um": gnd_depth,
            "calibration": "uncalibrated analytical estimate; no PowerSI fit applied",
            "barrel_plating_assumption_um": (
                min(20.0, drill_diameter / 4.0) if drill_diameter is not None else None
            ),
            "power_leg": _via_leg_provenance(power_leg),
            "ground_leg": _via_leg_provenance(ground_leg),
        }
    return templates, provenance

def _spd_layer_center_depths(layers: Sequence[StackupLayer]) -> dict[str, float]:
    depth = 0.0
    result: dict[str, float] = {}
    for layer in layers:
        result[layer.name] = depth + layer.thickness_um / 2.0
        depth += layer.thickness_um
    return result

def _spd_uncalibrated_loop_estimate(
    *,
    pwr_depth_um: float,
    gnd_depth_um: float,
    drill_diameter_um: float | None,
    power_leg: ViaSegmentElectricalModel | None = None,
    ground_leg: ViaSegmentElectricalModel | None = None,
) -> tuple[float, float]:
    if power_leg is not None and ground_leg is not None:
        return (
            max(power_leg.resistance_ohm + ground_leg.resistance_ohm, 0.0),
            max(power_leg.inductance_h + ground_leg.inductance_h, 0.0),
        )
    total_length_um = max(float(pwr_depth_um), 1.0) + max(float(gnd_depth_um), 1.0)
    if drill_diameter_um is None or drill_diameter_um <= 0:
        scale = max(total_length_um / 500.0, 0.25)
        return 0.003 * scale, 0.50e-9 * scale
    diameter_um = float(drill_diameter_um)
    plating_um = min(20.0, diameter_um / 4.0)
    barrel_area_m2 = math.pi * diameter_um * plating_um * 1.0e-12
    resistance = total_length_um * 1.0e-6 / (
        COPPER_CONDUCTIVITY_S_PER_M * barrel_area_m2
    )

    def straight_via_inductance(length_um: float) -> float:
        length_mm = max(length_um, 1.0) / 1000.0
        ratio = max(4.0 * max(length_um, 1.0) / diameter_um, 1.0)
        return 0.2 * length_mm * (math.log(ratio) + 1.0) * 1.0e-9

    inductance = straight_via_inductance(pwr_depth_um) + straight_via_inductance(
        gnd_depth_um
    )
    return max(float(resistance), 0.0), max(float(inductance), 0.0)


def _spd_via_leg_estimate(
    *,
    padstack: Any | None,
    length_um: float,
    start_layer: str | None,
    end_layer: str,
    stackup_layers: Sequence[StackupLayer],
) -> ViaSegmentElectricalModel | None:
    """Return one source-backed rail-template leg, or retain legacy fallback."""

    if padstack is None or padstack.drill_diameter_um is None:
        return None
    try:
        return estimate_via_segment_rl(
            length_um=length_um,
            drill_diameter_um=padstack.drill_diameter_um,
            padstack_material=padstack.material,
            start_layer=start_layer,
            end_layer=end_layer,
            stackup_layers=stackup_layers,
        )
    except ViaModelError as exc:
        raise ValueError(f"source Via template leg is nonphysical: {exc}") from exc


def _via_leg_provenance(leg: ViaSegmentElectricalModel | None) -> dict[str, Any]:
    if leg is None:
        return {
            "conductor_model": "LEGACY_RAIL_TEMPLATE_NO_SOURCE_LEG",
            "fill_provenance": "LEGACY_PLATED_BARREL_FALLBACK",
            "classification_basis": "no matched source padstack/drill for this leg",
            "effective_area_m2": None,
        }
    classification = leg.classification
    return {
        "conductor_model": classification.conductor_model,
        "fill_provenance": classification.fill_provenance,
        "classification_basis": classification.classification_basis,
        "effective_area_m2": classification.effective_area_m2,
    }

def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text.casefold() not in seen:
            result.append(text)
            seen.add(text.casefold())
    return result

def _read_passive_spice_source(
    path: Path, *, subckt_name: str | None = None
) -> tuple[bytes, PassiveSubcircuitModel]:
    source_path = Path(path)
    source_bytes = source_path.read_bytes()
    try:
        text = source_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{source_path}: passive SPICE source must be UTF-8 or ASCII text"
        ) from exc
    parsed = parse_passive_subcircuit(
        text,
        subckt_name=subckt_name,
        source_name=str(source_path),
    )
    return source_bytes, parsed

def cap_spice_subcircuit_names(path: Path) -> tuple[str, ...]:
    """List selectable two-terminal ``.SUBCKT`` declarations in one source file."""

    source_path = Path(path)
    source_bytes = source_path.read_bytes()
    try:
        text = source_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{source_path}: passive SPICE source must be UTF-8 or ASCII text"
        ) from exc
    return passive_subcircuit_names(text, source_name=str(source_path))

def _cap_model_sources(project: ProjectSpec) -> dict[str, dict[str, Any]]:
    raw_sources = project.metadata.get(_CAP_MODEL_SOURCES_KEY)
    if not isinstance(raw_sources, Mapping):
        return {}
    sources: dict[str, dict[str, Any]] = {}
    for raw_model_id, raw_source in raw_sources.items():
        if isinstance(raw_model_id, str) and isinstance(raw_source, Mapping):
            sources[raw_model_id] = dict(raw_source)
    return sources

def _validate_cap_source_attachments(
    project: ProjectSpec, attachments: Mapping[str, bytes]
) -> None:
    """Require stored cap sources to parse and match their normalized provenance."""

    cap_by_key = {item.model_id.casefold(): item for item in project.cap_models}
    for source_id, source in _cap_model_sources(project).items():
        asset = source.get("source_asset")
        if not isinstance(asset, str) or not asset:
            continue
        cap = cap_by_key.get(source_id.casefold())
        if cap is None:
            raise ValueError(
                f"cap source provenance references unknown model {source_id!r}"
            )
        payload = attachments.get(asset)
        if payload is None:
            raise ValueError(f"cap source attachment is missing: {asset}")
        try:
            source_text = bytes(payload).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"cap source attachment is not UTF-8 text: {asset}") from exc

        subckt_name = source.get("subckt_name")
        selected_subckt = (
            subckt_name.strip()
            if isinstance(subckt_name, str) and subckt_name.strip()
            else _default_cap_subckt_name(cap.model_id)
        )
        candidate_texts = [source_text]
        if str(source.get("origin", "")).casefold() == "legacy_user_spice":
            normalized = source_text.replace("\r\n", "\n").replace("\r", "\n")
            if normalized != source_text:
                candidate_texts.append(normalized)

        parsed_models: list[PassiveSubcircuitModel] = []
        parse_error: ValueError | None = None
        for candidate_text in candidate_texts:
            try:
                parsed_models.append(
                    parse_passive_subcircuit(
                        candidate_text,
                        subckt_name=selected_subckt,
                        source_name=asset,
                    )
                )
            except ValueError as exc:
                parse_error = exc
        if not parsed_models:
            raise ValueError(
                f"cap source attachment failed passive two-terminal validation: "
                f"{asset}: {parse_error}"
            ) from parse_error

        declared_hash = source.get("source_hash")
        expected_hashes = {cap.source_hash.casefold()}
        if isinstance(declared_hash, str) and declared_hash:
            expected_hashes.add(declared_hash.casefold())
        matching = [
            parsed
            for parsed in parsed_models
            if all(parsed.source_hash.casefold() == value for value in expected_hashes)
        ]
        if not matching:
            raise ValueError(
                f"cap source attachment hash mismatch: {asset} "
                f"(model {cap.model_id!r})"
            )

def _default_cap_subckt_name(model_id: str) -> str:
    return _safe_id(model_id) or "CAP_MODEL"

def _find_cap_model(project: ProjectSpec, model_id: str) -> CapModel | None:
    key = model_id.casefold()
    return next(
        (item for item in project.cap_models if item.model_id.casefold() == key),
        None,
    )

def _pop_cap_model_source(
    sources: dict[str, dict[str, Any]], model_id: str
) -> dict[str, Any] | None:
    key = model_id.casefold()
    for source_id in tuple(sources):
        if source_id.casefold() == key:
            return sources.pop(source_id)
    return None

def _normalize_cap_inventory(value: int) -> int:
    if isinstance(value, bool):
        raise ValueError("cap inventory must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("cap inventory must be an integer") from exc
    if normalized != value:
        raise ValueError("cap inventory must be an integer")
    if normalized < 0:
        raise ValueError("cap inventory cannot be negative")
    return normalized

def _validate_cap_metadata_for_placements(
    project: ProjectSpec,
    model_id: str,
    *,
    footprint: str,
    inventory: int,
) -> None:
    placements = [
        item
        for item in project.placements
        if item.cap_model_id is not None
        and item.cap_model_id.casefold() == model_id.casefold()
    ]
    if inventory < len(placements):
        raise ValueError(
            f"cap model {model_id!r} is used by {len(placements)} placement(s); "
            f"inventory cannot be reduced to {inventory}"
        )
    topology_by_slot = {item.slot_id: item for item in project.topology_maps}
    incompatible = [
        item.slot_id
        for item in placements
        if (
            topology_by_slot[item.slot_id].allowed_footprints
            and footprint.casefold()
            not in {
                allowed.casefold()
                for allowed in topology_by_slot[item.slot_id].allowed_footprints
            }
        )
    ]
    if incompatible:
        preview = ", ".join(incompatible[:5])
        suffix = "" if len(incompatible) <= 5 else f" and {len(incompatible) - 5} more"
        raise ValueError(
            f"footprint {footprint!r} is incompatible with placed slot(s) "
            f"{preview}{suffix}; update topology compatibility before changing the model"
        )

def _remove_exclusive_cap_asset(
    attachments: dict[str, bytes],
    sources: Mapping[str, Mapping[str, Any]],
    source: Mapping[str, Any] | None,
) -> None:
    if source is None:
        return
    asset = source.get("source_asset")
    if not isinstance(asset, str) or not asset:
        return
    referenced = {
        str(item.get("source_asset"))
        for item in sources.values()
        if isinstance(item.get("source_asset"), str)
    }
    if asset not in referenced:
        attachments.pop(asset, None)

def _commit_cap_library_update(
    state: WorkspaceState,
    *,
    cap_models: Sequence[CapModel],
    attachments: Mapping[str, bytes],
    sources: Mapping[str, Mapping[str, Any]],
) -> WorkspaceState:
    normalized_attachments = {
        str(name): bytes(payload) for name, payload in attachments.items()
    }
    metadata = {
        **state.project.metadata,
        _CAP_MODEL_SOURCES_KEY: {
            str(model_id): dict(source) for model_id, source in sources.items()
        },
    }
    candidate = _validated_project_copy(
        state.project,
        cap_models=list(cap_models),
        metadata=metadata,
        attachment_names=sorted(normalized_attachments, key=str.casefold),
    )
    _validate_cap_source_attachments(candidate, normalized_attachments)

    # Candidate construction and all byte handling complete before observable state changes.
    state.project = candidate
    state.attachments = normalized_attachments
    state.last_evaluation = None
    state.evaluation_history.clear()
    return state

def import_cap_spice(
    state: WorkspaceState,
    path: Path,
    *,
    model_id: str | None = None,
    footprint: str = "GENERIC",
    inventory: int = 100_000,
    subckt_name: str | None = None,
    replace_existing: bool = False,
) -> WorkspaceState:
    """Add one normalized passive model, rejecting accidental ID replacement."""

    source_path = Path(path)
    source_bytes, parsed = _read_passive_spice_source(
        source_path, subckt_name=subckt_name
    )
    requested_id = (model_id or parsed.model_id).strip()
    if not requested_id:
        raise ValueError("cap model ID cannot be blank")
    normalized_footprint = footprint.strip()
    if not normalized_footprint:
        raise ValueError("cap footprint cannot be blank")
    normalized_inventory = _normalize_cap_inventory(inventory)

    existing = _find_cap_model(state.project, requested_id)
    if existing is not None and not replace_existing:
        raise ValueError(
            f"cap model ID {requested_id!r} already exists; explicitly replace it or "
            "choose a different ID"
        )
    canonical_id = existing.model_id if existing is not None else requested_id
    _validate_cap_metadata_for_placements(
        state.project,
        canonical_id,
        footprint=normalized_footprint,
        inventory=normalized_inventory,
    )

    frequencies = _frequency_array(state.project.frequency)
    impedance = parsed.impedance(frequencies)
    cap = CapModel(
        model_id=canonical_id,
        impedance=[
            ImpedanceSample(
                frequency_hz=float(f_hz),
                real_ohm=float(value.real),
                imag_ohm=float(value.imag),
            )
            for f_hz, value in zip(frequencies, impedance, strict=True)
        ],
        footprint=normalized_footprint,
        inventory=normalized_inventory,
        source_hash=parsed.source_hash,
    )
    models = [
        item
        for item in state.project.cap_models
        if item.model_id.casefold() != canonical_id.casefold()
    ]
    models.append(cap)

    attachments = dict(state.attachments)
    sources = _cap_model_sources(state.project)
    old_source = None
    if existing is not None:
        old_source = _pop_cap_model_source(sources, existing.model_id)
    asset_id = _safe_id(canonical_id) or "CAP_MODEL"
    asset_name = f"cap_models/{asset_id}-{parsed.source_hash[:12]}.lib"
    attachments[asset_name] = source_bytes
    sources[canonical_id] = {
        "origin": "user_spice",
        "source_name": source_path.name,
        "source_asset": asset_name,
        "subckt_name": parsed.model_id,
        "source_hash": parsed.source_hash,
    }
    _remove_exclusive_cap_asset(attachments, sources, old_source)
    return _commit_cap_library_update(
        state,
        cap_models=models,
        attachments=attachments,
        sources=sources,
    )

def _constant_target(impedance_ohm: float) -> list[TargetPoint]:
    return [
        TargetPoint(frequency_hz=1.0e3, impedance_ohm=impedance_ohm),
        TargetPoint(frequency_hz=1.0e9, impedance_ohm=impedance_ohm),
    ]

def _validated_project_copy(project: ProjectSpec, **updates: Any) -> ProjectSpec:
    candidate = project.model_copy(update=updates)
    return ProjectSpec.model_validate(candidate.model_dump(mode="python"))

def _project_with_target(project: ProjectSpec, rail_id: str, target: float) -> ProjectSpec:
    if rail_id not in {item.rail_id for item in project.rails}:
        raise ValueError(f"unknown rail/domain {rail_id!r}")
    rails = [
        item.model_copy(update={"target_mask": _constant_target(target)})
        if item.rail_id == rail_id
        else item
        for item in project.rails
    ]
    return _validated_project_copy(project, rails=rails)

def _uses_spd_whole_outline_fallback(project: ProjectSpec) -> bool:
    spd_import = project.metadata.get("spd_import")
    return bool(
        not project.partitions
        and isinstance(spd_import, dict)
        and str(spd_import.get("geometry_model", "")).startswith(
            "whole selected-net bounding box fallback"
        )
    )

def _require_evaluable(project: ProjectSpec, rail_id: str) -> None:
    rail = next((item for item in project.rails if item.rail_id == rail_id), None)
    if rail is None:
        raise EvaluationReadinessError(
            "UNKNOWN_RAIL", f"unknown rail/domain {rail_id!r}"
        )
    if not bool(project.metadata.get("plane_pair_confirmed", False)):
        raise EvaluationReadinessError(
            "PLANE_PAIR_UNCONFIRMED",
            "effective PWR/DGND plane-pair selection must be confirmed",
        )
    matching_partition = next(
        (
            item
            for item in project.partitions
            if item.layer == rail.pwr_layer and rail.domain in item.domain_to_cell
        ),
        None,
    )
    domains_on_layer = {
        item.domain for item in project.rails if item.pwr_layer == rail.pwr_layer
    }
    spd_whole_outline = _uses_spd_whole_outline_fallback(project)
    if project.partitions and matching_partition is None:
        raise EvaluationReadinessError(
            "PARTITION_MISSING",
            "selected PWR layer requires a confirmed partition containing the rail domain",
        )
    if (
        not project.partitions
        and len(domains_on_layer) > 1
        and not spd_whole_outline
    ):
        raise EvaluationReadinessError(
            "PARTITION_MISSING",
            "shared PWR layer requires a confirmed partition containing the rail domain",
        )
    if matching_partition is not None and not matching_partition.confirmed:
        raise EvaluationReadinessError(
            "PARTITION_UNCONFIRMED",
            "plane partition preview must be confirmed before calculation",
        )
    power_bumps = [
        item
        for item in project.pins
        if item.kind == PinKind.DEVICE_BUMP
        and item.terminal == TerminalKind.PWR
        and item.net.casefold() == rail.net.casefold()
        and (item.domain is None or item.domain == rail.domain)
        and (item.site is None or item.site == rail.site)
    ]
    ground_bumps = [
        item
        for item in project.pins
        if item.kind == PinKind.DEVICE_BUMP
        and item.terminal == TerminalKind.GND
        and (item.site is None or item.site == rail.site)
    ]
    if not power_bumps or not ground_bumps:
        raise EvaluationReadinessError(
            "DEVICE_PINS_MISSING",
            "Device PWR and DGND bump coordinates are both required",
        )

def _evaluation_view(project: ProjectSpec, outcome: EvaluationOutcome) -> EvaluationView:
    # Import/legacy compatibility: a few callers construct lightweight outcome
    # views rather than the current EvaluationOutcome dataclass.  Those results
    # predate solver profiles and must continue to mean the default legacy
    # backend, never the research backend.
    profile = resolve_solver_profile(
        getattr(outcome, "solver_profile_key", DEFAULT_SOLVER_PROFILE_KEY)
    )
    frequencies = outcome.solve.frequencies_hz
    magnitude = outcome.metrics.magnitude_ohm
    phase = outcome.metrics.phase_deg
    critical = (
        (frequencies >= project.frequency.critical_start_hz)
        & (frequencies <= project.frequency.critical_stop_hz)
    )
    critical_indices = np.flatnonzero(critical)
    violation_index = int(critical_indices[np.argmax(outcome.metrics.violation_db[critical])])
    if outcome.metrics.peaks:
        dominant_peak = max(outcome.metrics.peaks, key=lambda item: item.impedance_ohm)
        peak_frequency = dominant_peak.frequency_hz
        peak_magnitude = dominant_peak.impedance_ohm
        peak_prominence = dominant_peak.prominence_db
    else:
        peak_index = int(critical_indices[np.argmax(magnitude[critical])])
        peak_frequency = float(frequencies[peak_index])
        peak_magnitude = float(magnitude[peak_index])
        peak_prominence = 0.0
    confidence_bands = [
        {
            "category": item.category.value,
            "start_hz": item.start_hz,
            "stop_hz": item.stop_hz,
            "level": item.level.value,
            "reason": item.reason,
        }
        for item in outcome.confidence
    ]
    level_order = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    overall = min(
        (item.level.value for item in outcome.confidence),
        key=lambda level: level_order[level],
        default="LOW",
    )
    category_summary: dict[str, str] = {}
    for item in outcome.confidence:
        existing = category_summary.get(item.category.value)
        if existing is None or level_order[item.level.value] < level_order[existing]:
            category_summary[item.category.value] = item.level.value
    confidence_note_parts = [
        *(f"{key} {value}" for key, value in category_summary.items()),
        evaluation_model_boundary_disclosure(profile.key),
    ]
    confidence_note = " | ".join(confidence_note_parts)
    evaluated_rail = next(
        (
            item
            for item in project.rails
            if item.rail_id.casefold() == outcome.rail_id.casefold()
        ),
        None,
    )
    certificate = (
        evaluated_rail.mixed_reference_certificate
        if evaluated_rail is not None
        else None
    )
    if certificate is not None:
        mixed_reference = (
            "Mixed reference "
            f"{certificate.pwr_layer}/{certificate.gnd_layer}: overlap "
            f"{certificate.overlap_fraction:.2%}, dominant "
            f"{certificate.dominant_overlap_component_fraction:.2%}"
        )
        if profile.key == LAYERWISE_ADMITTANCE_PROFILE.key:
            disclosure = (
                f"{mixed_reference}; exact retained artwork is used by the "
                "terminal-complete Maxwell-Y/global Kron network and its external "
                "Device-port Zii is used alone; no legacy rectangular higher-mode "
                "one-port difference is added (LOW geometry confidence)"
            )
        elif profile.key == RESEARCH_UNIFORM_ADMITTANCE_PROFILE.key:
            disclosure = (
                f"{mixed_reference}; exact artwork is used by the uniform C00 term, "
                "while nonuniform modes retain the continuous rectangular-return "
                "approximation (LOW geometry confidence)"
            )
        else:
            disclosure = (
                f"{mixed_reference}; continuous rectangular-return approximation "
                "(LOW geometry confidence)"
            )
        confidence_note = f"{confidence_note} | {disclosure}"
    placements = [item for item in project.placements if item.rail_id == outcome.rail_id]
    model_count = len({item.cap_model_id for item in placements})
    return EvaluationView(
        rail_id=outcome.rail_id,
        frequency_hz=[float(value) for value in frequencies],
        magnitude_ohm=[float(value) for value in magnitude],
        phase_deg=[float(value) for value in phase],
        target_ohm=float(np.median(outcome.metrics.target_ohm[critical])),
        target_curve_ohm=[float(value) for value in outcome.metrics.target_ohm],
        max_violation_db=outcome.metrics.max_violation_db,
        max_violation_frequency_hz=float(frequencies[violation_index]),
        rms_violation_db=outcome.metrics.rms_violation_db,
        peak_magnitude_ohm=peak_magnitude,
        peak_frequency_hz=peak_frequency,
        peak_prominence_db=peak_prominence,
        peaks=[
            {
                "peak_id": item.peak_id,
                "frequency_hz": item.frequency_hz,
                "impedance_ohm": item.impedance_ohm,
                "prominence_db": item.prominence_db,
                "classification": "anti-resonance candidate",
            }
            for item in outcome.metrics.peaks
        ],
        cap_count=len(placements),
        model_count=model_count,
        confidence=overall,
        confidence_note=confidence_note,
        confidence_bands=confidence_bands,
        assumptions=list(outcome.assumptions),
        solver_version=outcome.solver_version,
        solver_diagnostics={
            "mode_count": outcome.solve.diagnostics.mode_count,
            "max_condition_number": outcome.solve.diagnostics.max_condition_number,
            "max_relative_residual": outcome.solve.diagnostics.max_relative_residual,
        },
        convergence=(asdict(outcome.convergence) if outcome.convergence else None),
        z_real_ohm=[float(value) for value in outcome.solve.impedance_ohm.real],
        z_imag_ohm=[float(value) for value in outcome.solve.impedance_ohm.imag],
        solver_profile_key=profile.key,
        solver_profile_label=profile.label,
        solver_profile_badge=profile.badge,
        solver_provenance=dict(getattr(outcome, "solver_provenance", {})),
    )

def _derive_rails(
    project: ProjectSpec,
    mixed_reference_certificates: Sequence[MixedReferenceCertificate] = (),
    *,
    selected_pairs: Mapping[str, PlanePairSuggestion] | None = None,
) -> list[RailSpec]:
    if not project.stackup_layers or not project.pins:
        return project.rails
    existing_by_net = {item.net.casefold(): item for item in project.rails}
    gnd_keys = {item.casefold() for item in project.gnd_aliases}
    power_pins = [
        item
        for item in project.pins
        if item.kind == PinKind.DEVICE_BUMP
        and item.terminal == TerminalKind.PWR
        and item.net.casefold() not in gnd_keys
    ]
    nets: list[str] = []
    for pin in power_pins:
        if pin.net.casefold() not in {item.casefold() for item in nets}:
            nets.append(pin.net)
    rails: list[RailSpec] = []
    for index, net in enumerate(nets):
        suggestions = suggest_effective_plane_pairs(
            project.stackup_layers,
            rail_net=net,
            gnd_aliases=project.gnd_aliases,
            mixed_reference_certificates=mixed_reference_certificates,
        )
        if not suggestions:
            continue
        existing = existing_by_net.get(net.casefold())
        matching = [item for item in power_pins if item.net.casefold() == net.casefold()]
        domain = next((item.domain for item in matching if item.domain), net)
        site = next((item.site for item in matching if item.site), "SITE0")
        requested = (
            selected_pairs.get(net.casefold())
            if selected_pairs is not None
            else None
        )
        if selected_pairs and requested is None:
            raise ValueError(
                f"source-proven plane-pair selection is missing for rail {net!r}"
            )
        if requested is None:
            # Prefer source-terminal/artwork coverage before physical-layer
            # separation when no explicit source-proven pair was supplied.
            suggestion = min(
                suggestions,
                key=lambda item: _plane_pair_source_rank(project, net, item),
            )
        else:
            matching_suggestions = [
                item
                for item in suggestions
                if item.pwr_layer.casefold() == requested.pwr_layer.casefold()
                and item.gnd_layer.casefold() == requested.gnd_layer.casefold()
            ]
            if not matching_suggestions:
                raise ValueError(
                    f"source-proven plane pair {requested.pwr_layer}/{requested.gnd_layer} "
                    f"is not a valid adjacent pure-GND pair for rail {net!r}"
                )
            suggestion = matching_suggestions[0]
        rails.append(
            RailSpec(
                rail_id=existing.rail_id if existing else str(domain),
                family=existing.family if existing else _rail_family(net),
                domain=str(domain),
                net=net,
                site=str(site),
                pwr_layer=suggestion.pwr_layer,
                gnd_layer=suggestion.gnd_layer,
                priority=existing.priority if existing else min(index + 1, 3),
                state=existing.state if existing else (RailState.ACTIVE if index < 3 else RailState.RESERVE),
                target_mask=existing.target_mask if existing and existing.target_mask else _constant_target(0.020),
                min_near_slots=existing.min_near_slots if existing else 0,
                reserved_slot_ids=existing.reserved_slot_ids if existing else [],
                mixed_reference_certificate=suggestion.mixed_reference_certificate,
            )
        )
    return rails


def _plane_pair_source_rank(
    project: ProjectSpec,
    rail_net: str,
    suggestion: Any,
) -> tuple[float, ...]:
    """Return a deterministic source-geometry rank for one eligible pair.

    Plane-pair selection ranks the selected PWR artwork envelope against the
    imported Device bumps and mounted decap pads, so center/footprint-envelope
    misses are a much stronger primary criterion than physical layer separation.
    Research/Legacy later use the selected bounding box as their finite modal
    domain; terminal-complete Layerwise instead retains the exact source artwork.
    No Touchstone result or fitted coefficient is used here.
    """

    metadata = project.metadata.get("spd_import")
    records = (
        metadata.get(_SPD_PLANE_GEOMETRIES_KEY, ())
        if isinstance(metadata, Mapping)
        else ()
    )
    record = next(
        (
            item
            for item in records
            if isinstance(item, Mapping)
            and str(item.get("layer", "")).casefold()
            == str(suggestion.pwr_layer).casefold()
            and str(item.get("net", "")).casefold() == rail_net.casefold()
        ),
        None,
    )
    bounds = _spd_geometry_bounds(record) if record is not None else None
    if bounds is None:
        return (
            float("inf"),
            float("inf"),
            float(suggestion.separation_um),
            0.0,
            float(suggestion.pwr_index),
            float(suggestion.gnd_index),
        )

    power_pins = [
        pin
        for pin in project.pins
        if pin.terminal == TerminalKind.PWR
        and pin.net.casefold() == rail_net.casefold()
    ]
    power_refdes = {pin.refdes.casefold() for pin in power_pins}
    power_groups = {
        (str(pin.site or "").casefold(), str(pin.bump_group or "").casefold())
        for pin in power_pins
        if pin.kind == PinKind.DEVICE_BUMP and pin.bump_group
    }
    related_ground = [
        pin
        for pin in project.pins
        if pin.terminal == TerminalKind.GND
        and (
            pin.refdes.casefold() in power_refdes
            or (
                pin.kind == PinKind.DEVICE_BUMP
                and pin.bump_group
                and (
                    str(pin.site or "").casefold(),
                    str(pin.bump_group or "").casefold(),
                )
                in power_groups
            )
        )
    ]
    terminals = (*power_pins, *related_ground)
    # Import has not compiled rail-specific via templates yet.  Fifty microns
    # is the conservative half-extent of the 100 um fallback finite port used
    # by the same source adapter; the production preflight later rechecks the
    # exact recovered landing dimensions.
    half_extent_um = 50.0
    x_min, x_max, y_min, y_max = bounds
    outside_count = 0
    total_overrun_um = 0.0
    for pin in terminals:
        overruns = (
            max(0.0, x_min - (float(pin.x_um) - half_extent_um)),
            max(0.0, (float(pin.x_um) + half_extent_um) - x_max),
            max(0.0, y_min - (float(pin.y_um) - half_extent_um)),
            max(0.0, (float(pin.y_um) + half_extent_um) - y_max),
        )
        if max(overruns) > 0.0:
            outside_count += 1
            total_overrun_um += sum(overruns)
    bbox_area_um2 = (x_max - x_min) * (y_max - y_min)
    return (
        float(outside_count),
        total_overrun_um,
        float(suggestion.separation_um),
        -bbox_area_um2,
        float(suggestion.pwr_index),
        float(suggestion.gnd_index),
    )

def _rail_family(net: str) -> str:
    value = re.sub(r"/\d+$", "", net)
    value = re.sub(r"[_-](?:NE|NW|SE|SW|N|S|E|W)$", "", value, flags=re.I)
    return value or net

def _frequency_array(settings: FrequencySettings) -> np.ndarray:
    if settings.spacing == "log":
        return np.geomspace(settings.start_hz, settings.stop_hz, settings.points)
    return np.linspace(settings.start_hz, settings.stop_hz, settings.points)

def _solver_evidence_text(result: EvaluationView) -> str:
    status = "exceeds" if result.max_violation_db > 0 else "meets"
    return (
        f"Solver evidence: {result.rail_id} {status} the target by a "
        f"maximum of {result.max_violation_db:.2f} dB at "
        f"{result.max_violation_frequency_hz/1e6:.3f} MHz. The dominant extracted "
        f"peak is {result.peak_magnitude_ohm*1e3:.2f} mΩ at "
        f"{result.peak_frequency_hz/1e6:.3f} MHz."
    )

def _deterministic_analysis_text(result: EvaluationView, reason: str) -> str:
    return (
        f"Deterministic fallback ({reason}). {_solver_evidence_text(result)} "
        "Numerical impedance is unchanged "
        "whether AI is enabled or disabled."
    )

def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()

def _noop_progress(_value: int, _message: str) -> None:
    return None

def _never_cancelled() -> bool:
    return False

__all__ = [
    'DEFAULT_EVALUATION_MODAL_MAX_INDEX',
    'DEFAULT_LAYERWISE_MODAL_CEILING_INDEX',
    'EVALUATION_MODAL_PRESETS',
    'EvaluationModalPreset',
    'EvaluationReadinessError',
    'EvaluationView',
    'LocalAIAnalysisView',
    'SpdImportPlan',
    'WorkspaceState',
    'analyze_with_local_llm',
    'build_spd_import_plan',
    'cap_spice_subcircuit_names',
    'create_workspace_state',
    'evaluate_workspace',
    'evaluation_modal_preset',
    'evaluation_modal_convergence_ceiling',
    'import_cap_spice',
    'plane_cell_source_geometry',
    'plane_cell_source_geometry_with_size',
    'spd_plane_geometry_record_payload',
]
