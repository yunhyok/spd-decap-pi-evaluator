"""Standalone SPD import, decap-model, evaluation, and plot-analysis services."""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
import zlib
import numpy as np
from .ai import AssistantSource, EvidenceKind, FeatureEvidence, LocalLLMClient, LocalLLMConfig, PlotFeatures, local_llm_endpoint_requires_remote_access
from .domain import CapModel, CoordinateTransform, CoordinateUnit, FrequencySettings, ImpedanceSample, MLOOutline, MIXED_REFERENCE_MIN_COVERAGE, MIXED_REFERENCE_MIN_DOMINANT_COMPONENT, MixedReferenceCertificate, PinKind, PlaneCell, PlacementAssignment as DomainPlacement, PlanePartitionSpec, ProjectSpec, RailSpec, RailState, StackupLayer, TargetPoint, TerminalKind, TopologyKind, TopologyMap, ViaLoopTemplate, ViaPathKind
from .models import PassiveSubcircuitModel, parse_passive_subcircuit, passive_subcircuit_names
from .plane_pairs import suggest_effective_plane_pairs
from .solver.evaluator import EvaluationOutcome, evaluate_project_rail_converged
from .via_model import (
    COPPER_CONDUCTIVITY_S_PER_M,
    ViaModelError,
    ViaSegmentElectricalModel,
    estimate_via_segment_rl,
)
from .version import __version__

ProgressCallback = Callable[[int, str], None]

CancelCallback = Callable[[], bool]

_CAP_MODEL_SOURCES_KEY = "cap_model_sources"

_SPD_PLANE_GEOMETRIES_KEY = "plane_geometries"

_SPD_GEOMETRY_MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024

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
    """One supported square modal-basis internal-convergence/runtime trade-off."""

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
            "49 rectangular-cavity modes for quicker exploratory evaluation; "
            "compares against the 25-mode maximum-index (4,4) basis."
        ),
    ),
    EvaluationModalPreset(
        key="balanced",
        label="Balanced",
        max_index=8,
        mode_count=81,
        description=(
            "81 rectangular-cavity modes for the default internal-convergence/"
            "runtime balance; compares against the 49-mode (6,6) basis."
        ),
    ),
    EvaluationModalPreset(
        key="high",
        label="High",
        max_index=10,
        mode_count=121,
        description=(
            "121 rectangular-cavity modes for a stricter internal truncation "
            "check; compares against the 81-mode (8,8) basis. More modes do not "
            "correct polygon, shared-DGND, or via-model approximations and can "
            "take several minutes on a large SPD."
        ),
    ),
    EvaluationModalPreset(
        key="maximum",
        label="Maximum",
        max_index=12,
        mode_count=169,
        description=(
            "169 rectangular-cavity modes for an opt-in final truncation check; "
            "compares against the 121-mode (10,10) basis. A representative m10→m12 "
            "check observed up to 0.105 dB change and can take substantially longer. "
            "It is not PowerSI calibration."
        ),
    ),
)

DEFAULT_EVALUATION_MODAL_MAX_INDEX = 8

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

def _decode_spd_geometry_asset(
    expected_sha256: str, compressed: bytes
) -> dict[str, Any]:
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
                if not all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in point
                ):
                    raise ValueError(
                        "PowerSI geometry asset Polygon vertices must be finite"
                    )
    for name in circle_names:
        for circle in payload[name]:
            if not isinstance(circle, list) or len(circle) != 3:
                raise ValueError("PowerSI geometry asset contains an invalid Circle")
            if not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in circle
            ) or float(circle[2]) <= 0:
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

def _cell_source_geometry(
    attachments: Mapping[str, bytes],
    cell: PlaneCell,
    *,
    expected_layer: str | None = None,
) -> Mapping[str, Any]:
    if cell.source_geometry_asset:
        compressed = attachments.get(cell.source_geometry_asset)
        if compressed is None:
            raise ValueError(
                f"PowerSI geometry asset is missing: {cell.source_geometry_asset}"
            )
        assert cell.source_geometry_sha256 is not None
        payload = _decode_spd_geometry_asset(
            cell.source_geometry_sha256, bytes(compressed)
        )
        _validate_spd_geometry_payload(
            payload,
            expected_layer=expected_layer,
            expected_net=cell.source_net,
        )
        return payload
    return {
        "positive_polygons_um": cell.source_positive_polygons_um,
        "negative_polygons_um": cell.source_negative_polygons_um,
        "positive_circles_um": cell.source_positive_circles_um,
        "negative_circles_um": cell.source_negative_circles_um,
        "primitive_order": cell.source_primitive_order,
    }

def plane_cell_source_geometry(
    cell: PlaneCell,
    attachments: Mapping[str, bytes],
    *,
    expected_layer: str | None = None,
) -> Mapping[str, Any]:
    """Return validated read-only PowerSI artwork for one normalized cell."""

    return _cell_source_geometry(attachments, cell, expected_layer=expected_layer)

def evaluate_workspace(
    state: WorkspaceState,
    rail_id: str,
    target_ohm: float | None = None,
    modal_max_index: int = DEFAULT_EVALUATION_MODAL_MAX_INDEX,
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> EvaluationView:
    modal_preset = evaluation_modal_preset(modal_max_index)
    progress = progress or _noop_progress
    is_cancelled = is_cancelled or _never_cancelled
    if is_cancelled():
        raise RuntimeError("evaluation cancelled")
    project = state.project
    if target_ohm is not None:
        if not math.isfinite(target_ohm) or target_ohm <= 0:
            raise ValueError("target impedance must be greater than zero")
        project = _project_with_target(project, rail_id, target_ohm)
    progress(
        10,
        f"Validating geometry and templates · {modal_preset.label} · "
        f"{modal_preset.mode_count} modes…",
    )
    _require_evaluable(project, rail_id)
    if is_cancelled():
        raise RuntimeError("evaluation cancelled")
    progress(
        25,
        f"Solving finite-port rectangular cavity · {modal_preset.label} · max "
        f"index ({modal_preset.max_index},{modal_preset.max_index}) · "
        f"{modal_preset.mode_count} modes…",
    )
    outcome = evaluate_project_rail_converged(
        project,
        rail_id,
        request_options={
            "max_mode_x": modal_preset.max_index,
            "max_mode_y": modal_preset.max_index,
            "worker_count": numerical_worker_count(project.frequency.points),
        },
        max_mode_x=modal_preset.max_index,
        max_mode_y=modal_preset.max_index,
        max_refinement_iterations=1,
        max_new_frequency_points=32,
    )
    if is_cancelled():
        raise RuntimeError("evaluation cancelled")
    progress(90, "Extracting target and resonance metrics…")
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

    compressor = zlib.compressobj(level=9)
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
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    """Compress exact Polygon/Circle primitives and return a compact project index."""

    index: list[dict[str, Any]] = []
    assets: dict[str, bytes] = {}
    for item in getattr(analysis, "plane_geometries", ()):
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
    return index, assets


def _ordered_spd_geometry(record: Mapping[str, Any]) -> Any | None:
    """Return the exact ordered PowerSI boolean geometry, or fail closed."""

    try:
        from shapely.errors import GEOSException
        from shapely.geometry import GeometryCollection, Point, Polygon
        from shapely.ops import unary_union
    except ImportError:  # packaging must provide Shapely; do not guess geometry.
        return None
    positive_polygons = record.get("positive_polygons_um", ())
    negative_polygons = record.get("negative_polygons_um", ())
    positive_circles = record.get("positive_circles_um", ())
    negative_circles = record.get("negative_circles_um", ())
    collections = {
        "positive_polygon": positive_polygons,
        "negative_polygon": negative_polygons,
        "positive_circle": positive_circles,
        "negative_circle": negative_circles,
    }
    shape = GeometryCollection()
    order = record.get("primitive_order", ())
    if not isinstance(order, Sequence) or not order:
        return None
    try:
        runs: list[tuple[bool, list[Any]]] = []
        for step in order:
            if not isinstance(step, Sequence) or len(step) != 2:
                return None
            kind, offset = str(step[0]), int(step[1])
            source = collections.get(kind)
            if source is None or offset < 0 or offset >= len(source):
                return None
            raw = source[offset]
            if kind.endswith("polygon"):
                primitive = Polygon(raw)
            else:
                x_um, y_um, radius_um = map(float, raw)
                if not math.isfinite(radius_um) or radius_um <= 0:
                    return None
                # The only curved primitive PowerSI emits is a circle.  This fixed
                # resolution is part of the versioned method, never a solver scale.
                primitive = Point(x_um, y_um).buffer(radius_um, quad_segs=64)
            if primitive.is_empty or not primitive.is_valid or primitive.area <= 0:
                return None
            positive = kind.startswith("positive_")
            if runs and runs[-1][0] == positive:
                runs[-1][1].append(primitive)
            else:
                runs.append((positive, [primitive]))
        # Preserve exact PowerSI ordering at polarity boundaries while avoiding
        # one expensive GEOS boolean per primitive.  Union is associative, and
        # consecutive differences A\B\C are exactly A\(B union C).
        for positive, primitives in runs:
            batch = unary_union(primitives)
            if batch.is_empty or not batch.is_valid or batch.area <= 0:
                return None
            shape = shape.union(batch) if positive else shape.difference(batch)
    except (TypeError, ValueError, IndexError, ArithmeticError, GEOSException):
        return None
    return None if shape.is_empty or not shape.is_valid or shape.area <= 0 else shape


def _mixed_reference_certificates(
    records: Sequence[Mapping[str, Any]],
    assets: Mapping[str, bytes],
    layers: Sequence[StackupLayer],
    *,
    power_keys: set[str],
    ground_keys: set[str],
    failures: list[dict[str, Any]] | None = None,
) -> tuple[MixedReferenceCertificate, ...]:
    """Certify mixed GND layers from retained source artwork, never net names alone."""

    layer_by_key = {item.name.casefold(): item for item in layers}
    layer_index = {item.name.casefold(): index for index, item in enumerate(layers)}
    result: list[MixedReferenceCertificate] = []
    decoded: list[dict[str, Any]] = []
    geometry_by_digest: dict[str, Any | None] = {}
    for record in records:
        asset = str(record.get("asset", ""))
        digest = str(record.get("asset_sha256", ""))
        try:
            payload = _decode_spd_geometry_asset(digest, assets[asset])
            _validate_spd_geometry_payload(
                payload,
                expected_layer=str(record.get("layer", "")),
                expected_net=str(record.get("net", "")),
            )
        except (KeyError, ValueError):
            continue
        payload["asset_sha256"] = digest
        decoded.append(payload)

    def geometry_for(payload: Mapping[str, Any]) -> Any | None:
        digest = str(payload.get("asset_sha256", ""))
        if digest not in geometry_by_digest:
            geometry_by_digest[digest] = _ordered_spd_geometry(payload)
        return geometry_by_digest[digest]

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

    for pwr in decoded:
        net = str(pwr.get("net", ""))
        pwr_layer = str(pwr.get("layer", ""))
        if net.casefold() not in power_keys or not pwr_layer:
            continue
        pwr_shape = geometry_for(pwr) if shapely_available else None
        for gnd in decoded:
            gnd_layer = str(gnd.get("layer", ""))
            if gnd_layer.casefold() == pwr_layer.casefold() or str(gnd.get("net", "")).casefold() not in ground_keys:
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
            gnd_net = str(gnd.get("net", ""))
            if net.casefold() in layer_keys:
                record_failure(
                    rail_net=net, pwr_layer=pwr_layer, gnd_layer=gnd_layer,
                    gnd_net=gnd_net, reason="target rail is present on proposed DGND layer",
                )
                continue
            # A certificate binds one exact DGND artwork asset.  Multiple ground
            # assets on a mixed return layer are intentionally not merged silently.
            matching_ground = [
                item for item in records
                if str(item.get("layer", "")).casefold() == gnd_layer.casefold()
                and str(item.get("net", "")).casefold() in ground_keys
            ]
            if len(matching_ground) != 1:
                record_failure(
                    rail_net=net, pwr_layer=pwr_layer, gnd_layer=gnd_layer,
                    gnd_net=gnd_net, reason="ground artwork asset is missing or ambiguous",
                )
                continue
            gnd_shape = geometry_for(gnd) if shapely_available else None
            if pwr_shape is None or gnd_shape is None:
                record_failure(
                    rail_net=net, pwr_layer=pwr_layer, gnd_layer=gnd_layer,
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
                overlap = pwr_shape.intersection(gnd_shape)
            except GEOSException:
                record_failure(
                    rail_net=net, pwr_layer=pwr_layer, gnd_layer=gnd_layer,
                    gnd_net=gnd_net, reason="GEOS intersection failed",
                    code="SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE",
                    blocking=True,
                )
                continue
            if overlap.is_empty or overlap.area <= 0:
                record_failure(
                    rail_net=net, pwr_layer=pwr_layer, gnd_layer=gnd_layer,
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
                    rail_net=net, pwr_layer=pwr_layer, gnd_layer=gnd_layer,
                    gnd_net=gnd_net,
                    reason=(
                        f"coverage {coverage:.6f} or dominant component "
                        f"{dominant:.6f} is below v1 thresholds"
                    ),
                )
                continue
            pwr_hash = str(pwr.get("asset_sha256", ""))
            gnd_hash = str(gnd.get("asset_sha256", ""))
            if len(pwr_hash) != 64 or len(gnd_hash) != 64:
                continue
            result.append(MixedReferenceCertificate(
                rail_net=net, gnd_net=str(gnd.get("net", "")),
                pwr_layer=pwr_layer, gnd_layer=gnd_layer,
                pwr_asset_sha256=pwr_hash, gnd_asset_sha256=gnd_hash,
                overlap_fraction=coverage,
                dominant_overlap_component_fraction=dominant,
            ))
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

def build_spd_import_plan(
    current: ProjectSpec,
    analysis: Any,
    source_path: Path,
) -> SpdImportPlan:
    diagnostics: list[Any] = list(analysis.diagnostics)
    ground_nets = _unique_strings([*current.gnd_aliases, *analysis.ground_nets])
    ground_keys = {item.casefold() for item in ground_nets}
    selected_power_nets = _unique_strings(analysis.power_plane_nets)
    selected_power_keys = {item.casefold() for item in selected_power_nets}
    plane_geometry_payload, plane_geometry_assets = _spd_plane_geometry_assets(
        analysis, selected_power_keys | ground_keys, diagnostics
    )
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
    mixed_reference_certificates = _mixed_reference_certificates(
        plane_geometry_payload,
        plane_geometry_assets,
        layers,
        power_keys=selected_power_keys,
        ground_keys=ground_keys,
        failures=mixed_reference_failures,
    )
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
        _derive_rails(base, mixed_reference_certificates), current.rails
    )
    project = _validated_project_copy(base, rails=rails)

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
    for model_id, parsed in sorted(
        analysis.cap_models.items(), key=lambda item: str(item[0]).casefold()
    ):
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
    for instance in analysis.cap_instances:
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
                        f"Geometry review, "
                        f"but the rectangular modal solver uses the separate per-net "
                        f"bounding box for {len(approximation_cells)} non-rectangular, "
                        "cutout, or disjoint rail domain(s). Validated imported solver "
                        "geometry is confirmed by default; review the approximation as needed."
                    ),
                )
            )
        diagnostics.append(
            _SpdServiceDiagnostic(
                severity="warning",
                code="SPD_GROUND_CONTINUOUS_REFERENCE_ASSUMPTION",
                message=(
                    "Mixed-reference DGND artwork and cutouts are retained for certificate "
                    "validation. The modal solver still assumes a continuous DGND reference "
                    "across each displayed solver rectangle, so certificate-backed results "
                    "remain LOW geometry confidence."
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
                    "available; continuous DGND reference assumed"
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
            "use disclosed solver bounding boxes; normalized source primitives "
            "retained; continuous DGND reference assumed"
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
    can_apply = not any(
        str(getattr(item, "severity", "")).casefold() == "error"
        for item in diagnostics
    )
    return SpdImportPlan(
        project=project,
        attachments=attachments,
        diagnostics=tuple(diagnostics),
        source_name=source_name,
        source_size_bytes=source_size,
        source_sha256=source_hash,
        summary_lines=summary_lines,
        can_apply=can_apply,
    )

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
    confidence_note = " | ".join(f"{key} {value}" for key, value in category_summary.items())
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
        disclosure = (
            "Mixed reference "
            f"{certificate.pwr_layer}/{certificate.gnd_layer}: overlap "
            f"{certificate.overlap_fraction:.2%}, dominant "
            f"{certificate.dominant_overlap_component_fraction:.2%}; "
            "continuous rectangular-return approximation (LOW geometry confidence)"
        )
        confidence_note = (
            f"{confidence_note} | {disclosure}" if confidence_note else disclosure
        )
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
    )

def _derive_rails(
    project: ProjectSpec,
    mixed_reference_certificates: Sequence[MixedReferenceCertificate] = (),
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
        suggestion = suggestions[0]
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
    'import_cap_spice',
    'plane_cell_source_geometry',
]
