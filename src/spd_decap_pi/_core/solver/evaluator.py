"""Deterministic single-rail Zii evaluator and domain-model adapter."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from spd_decap_pi._core.models import (
    DirectBranchModel,
    ImpedanceModel,
    SampledImpedanceModel,
    ScaledImpedanceModel,
    SeriesRLCModel,
    SeriesRLModel,
    SharedPairModel,
)
from spd_decap_pi._core.models.circuit import SharedPadClusterModel
from spd_decap_pi._core.domain import (
    MIXED_REFERENCE_MIN_COVERAGE,
    MIXED_REFERENCE_MIN_DOMINANT_COMPONENT,
)
from .frequency import refine_log_grid
from .metrics import (
    ConfidenceAssessment,
    ConfidenceInputs,
    EvaluationMetrics,
    TargetMask,
    assess_confidence,
    compute_evaluation_metrics,
)
from .modal import (
    CoupledShuntGroup,
    DielectricDispersion,
    DielectricLayer,
    DeviceBranch,
    DeviceConnection,
    FinitePort,
    ModalSolveResult,
    PreparedDeviceSystem,
    RectangularCavitySolver,
    RectangularPlane,
    ShuntGroup,
)


# Cache identity: source-derived terminal branches and explicit multi-ground
# shared-pad reduction and shared-PWR return treatment changed the calculated
# transfer function in v0.14.0.
SOLVER_VERSION = "modal-mvp-0.7.0"
COUPLING_ASSUMPTION = "inter-rail/site coupling not modeled"


class EvaluationError(ValueError):
    """Raised when a project cannot be mapped to the single-rail MVP model."""


def _validated_parallel_planes(
    plane: RectangularPlane, parallel_planes: tuple[RectangularPlane, ...]
) -> tuple[RectangularPlane, ...]:
    components = tuple(parallel_planes)
    if any(
        component.width_m != plane.width_m or component.height_m != plane.height_m
        for component in components
    ):
        raise EvaluationError(
            "parallel rectangular plane components must share the primary plane width "
            "and height"
        )
    return components


def sensitivity_port_id(
    topology_kind: str,
    member_slot_ids: tuple[str, ...],
) -> str:
    """Return an injective internal ID for one atomic Sensitivity unit.

    Slot names are user/import data and may contain any separator.  Length
    prefixes plus the topology tag keep a DIRECT slot such as ``A+B`` distinct
    from the SHARED_PAIR made from slots ``A`` and ``B``.
    """

    kind = str(topology_kind).strip().upper()
    members = tuple(str(slot_id) for slot_id in member_slot_ids)
    expected_count = {"DIRECT": 1, "SHARED_PAIR": 2}.get(kind)
    valid_cluster = kind == "SHARED_PAD_CLUSTER" and bool(members)
    if not valid_cluster and (
        expected_count is None or len(members) != expected_count
    ):
        raise EvaluationError(
            "Sensitivity IDs require one DIRECT member, two SHARED_PAIR members, "
            "or at least one SHARED_PAD_CLUSTER member"
        )
    encoded_members = "".join(f"{len(slot_id)}:{slot_id}" for slot_id in members)
    return f"{kind}|{encoded_members}"


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    rail_id: str
    frequencies_hz: NDArray[np.float64]
    plane: RectangularPlane
    device: DeviceConnection
    shunts: tuple[ShuntGroup | CoupledShuntGroup, ...]
    target: TargetMask
    parallel_planes: tuple[RectangularPlane, ...] = ()
    critical_band_hz: tuple[float, float] = (1e5, 1e8)
    max_mode_x: int = 6
    max_mode_y: int = 6
    mode_count: int | None = None
    worker_count: int = 1
    confidence_inputs: ConfidenceInputs = ConfidenceInputs()
    assumptions: tuple[str, ...] = (COUPLING_ASSUMPTION,)

    def __post_init__(self) -> None:
        if not self.rail_id.strip():
            raise EvaluationError("rail_id must not be empty")
        if self.worker_count < 1:
            raise EvaluationError("worker_count must be >= 1")
        object.__setattr__(
            self,
            "parallel_planes",
            _validated_parallel_planes(self.plane, self.parallel_planes),
        )


@dataclass(frozen=True, slots=True)
class ProjectEvaluationTemplate:
    """Placement-independent objects compiled once for repeated rail solves."""

    source_project: Any = field(repr=False, compare=False)
    rail_id: str
    plane: RectangularPlane
    parallel_planes: tuple[RectangularPlane, ...]
    origin_um: tuple[float, float]
    partition_confirmed: bool
    cap_models: dict[str, ImpedanceModel]
    via_templates: dict[str, Any]
    via_models: dict[str, ImpedanceModel]
    topology_maps: dict[str, Any]
    shared_pad_clusters: dict[str, Any]
    device: DeviceConnection
    plane_assumptions: tuple[str, ...]
    pairing_assumptions: tuple[str, ...]
    pairing_confident: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parallel_planes",
            _validated_parallel_planes(self.plane, self.parallel_planes),
        )


@dataclass(frozen=True, slots=True)
class EvaluationKernel:
    """Prepared numerical kernel for repeated placement-only evaluations."""

    rail_id: str
    plane: RectangularPlane
    parallel_planes: tuple[RectangularPlane, ...]
    max_mode_x: int
    max_mode_y: int
    mode_count: int | None
    frequencies_hz: NDArray[np.float64]
    device: DeviceConnection = field(repr=False, compare=False)
    solver: RectangularCavitySolver
    prepared_device: PreparedDeviceSystem

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parallel_planes",
            _validated_parallel_planes(self.plane, self.parallel_planes),
        )


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    rail_id: str
    solve: ModalSolveResult
    metrics: EvaluationMetrics
    confidence: tuple[ConfidenceAssessment, ...]
    assumptions: tuple[str, ...]
    solver_version: str = SOLVER_VERSION
    convergence: "ConvergenceReport | None" = None


@dataclass(frozen=True, slots=True)
class ShuntSensitivityOutcome:
    """Compact max-violation results for every one-shunt-removed case."""

    port_ids: tuple[str, ...]
    baseline_max_violation_db: float
    without_max_violation_db: tuple[float, ...]
    worker_count: int

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return self.port_ids

    def __post_init__(self) -> None:
        if len(self.port_ids) != len(self.without_max_violation_db):
            raise EvaluationError("sensitivity port and metric counts must match")
        values = (self.baseline_max_violation_db, *self.without_max_violation_db)
        if not all(np.isfinite(value) and value >= 0.0 for value in values):
            raise EvaluationError("sensitivity violation metrics must be finite and >= 0")
        if self.worker_count < 1:
            raise EvaluationError("sensitivity worker_count must be >= 1")


@dataclass(frozen=True, slots=True)
class ConvergenceReport:
    initial_frequency_points: int
    final_frequency_points: int
    refinement_iterations: int
    lower_mode_x: int
    lower_mode_y: int
    final_mode_x: int
    final_mode_y: int
    critical_rms_delta_db: float
    critical_max_delta_db: float
    dominant_peak_shift_percent: float
    frequency_rms_delta_db: float
    frequency_max_delta_db: float
    frequency_peak_shift_percent: float
    frequency_converged: bool
    frequency_budget_exhausted: bool
    modal_rms_delta_db: float
    modal_max_delta_db: float
    modal_peak_shift_percent: float
    modal_converged: bool
    converged: bool


@dataclass(frozen=True, slots=True)
class _FrequencyRefinementResult:
    request: EvaluationRequest
    outcome: EvaluationOutcome
    iterations: int
    rms_delta_db: float
    max_delta_db: float
    peak_shift_percent: float
    converged: bool
    budget_exhausted: bool


def compile_evaluation_kernel(request: EvaluationRequest) -> EvaluationKernel:
    """Prepare placement-independent modal and Device terms for one request shape."""

    solver = RectangularCavitySolver(
        request.plane,
        parallel_planes=request.parallel_planes,
        max_mode_x=request.max_mode_x,
        max_mode_y=request.max_mode_y,
        mode_count=request.mode_count,
    )
    return EvaluationKernel(
        rail_id=request.rail_id,
        plane=request.plane,
        parallel_planes=request.parallel_planes,
        max_mode_x=request.max_mode_x,
        max_mode_y=request.max_mode_y,
        mode_count=request.mode_count,
        frequencies_hz=request.frequencies_hz.copy(),
        device=request.device,
        solver=solver,
        prepared_device=solver.prepare_device(
            request.frequencies_hz,
            request.device,
        ),
    )


def evaluate_rail(
    request: EvaluationRequest,
    *,
    kernel: EvaluationKernel | None = None,
) -> EvaluationOutcome:
    if kernel is None:
        kernel = compile_evaluation_kernel(request)
    elif (
        kernel.rail_id != request.rail_id
        or kernel.plane != request.plane
        or kernel.parallel_planes != request.parallel_planes
        or kernel.max_mode_x != request.max_mode_x
        or kernel.max_mode_y != request.max_mode_y
        or kernel.mode_count != request.mode_count
        or not np.array_equal(kernel.frequencies_hz, request.frequencies_hz)
        or kernel.device is not request.device
    ):
        raise EvaluationError("prepared evaluation kernel does not match request shape")
    solve = kernel.solver.solve_prepared_device(
        kernel.prepared_device,
        shunts=request.shunts,
        max_workers=request.worker_count,
    )
    metrics = compute_evaluation_metrics(
        solve.frequencies_hz,
        solve.impedance_ohm,
        request.target,
        critical_band_hz=request.critical_band_hz,
    )
    confidence = assess_confidence(
        solve.frequencies_hz,
        solve.diagnostics,
        request.plane,
        request.confidence_inputs,
        parallel_planes=request.parallel_planes,
    )
    assumptions = tuple(dict.fromkeys((*request.assumptions, COUPLING_ASSUMPTION)))
    return EvaluationOutcome(
        rail_id=request.rail_id,
        solve=solve,
        metrics=metrics,
        confidence=confidence,
        assumptions=assumptions,
    )


def evaluate_shunt_sensitivity(
    request: EvaluationRequest,
    *,
    max_workers: int = 1,
    progress: Callable[[int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> ShuntSensitivityOutcome:
    """Evaluate every physical shunt removal with shared batched factorizations."""

    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    if cancelled():
        raise RuntimeError("sensitivity calculation cancelled")
    solver = RectangularCavitySolver(
        request.plane,
        parallel_planes=request.parallel_planes,
        max_mode_x=request.max_mode_x,
        max_mode_y=request.max_mode_y,
        mode_count=request.mode_count,
    )
    worker_count = min(max_workers, int(request.frequencies_hz.size))

    def numerical_progress(completed: int, total: int) -> None:
        report(
            5 + int(85 * completed / max(total, 1)),
            f"Batched sensitivity {completed}/{total} frequencies "
            f"({worker_count} parallel worker{'s' if worker_count != 1 else ''})",
        )

    solve = solver.solve_device_shunt_leave_one_out(
        request.frequencies_hz,
        request.device,
        shunts=request.shunts,
        max_workers=worker_count,
        progress=numerical_progress,
        is_cancelled=cancelled,
    )
    if cancelled():
        raise RuntimeError("sensitivity calculation cancelled")
    report(94, "Reducing leave-one-out curves to critical-band target metrics")
    target_values = request.target.values_at(solve.frequencies_hz)
    critical = (
        (solve.frequencies_hz >= request.critical_band_hz[0])
        & (solve.frequencies_hz <= request.critical_band_hz[1])
    )
    if not np.any(critical):
        raise EvaluationError(
            "the evaluation grid has no points inside the critical sensitivity band"
        )

    def max_violation(values: NDArray[np.complex128]) -> NDArray[np.float64]:
        magnitude = np.maximum(np.abs(values[..., critical]), np.finfo(float).tiny)
        ratio = magnitude / target_values[critical]
        violation = np.where(
            ratio <= 1.0 + 1.0e-12,
            0.0,
            np.maximum(0.0, 20.0 * np.log10(ratio)),
        )
        return np.asarray(np.max(violation, axis=-1), dtype=np.float64)

    baseline_max = float(max_violation(solve.baseline_impedance_ohm))
    without_max = max_violation(solve.without_impedance_ohm)
    report(100, f"Batched sensitivity metrics complete for {len(solve.port_ids)} units")
    return ShuntSensitivityOutcome(
        port_ids=solve.port_ids,
        baseline_max_violation_db=baseline_max,
        without_max_violation_db=tuple(float(value) for value in without_max),
        worker_count=worker_count,
    )


def evaluate_rail_converged(
    request: EvaluationRequest,
    *,
    max_refinement_iterations: int = 2,
    max_new_frequency_points: int = 64,
    max_mode_x: int = 10,
    max_mode_y: int = 10,
    rms_tolerance_db: float = 0.2,
    max_tolerance_db: float = 0.5,
    peak_shift_tolerance_percent: float = 2.0,
) -> EvaluationOutcome:
    """Adapt the log grid and modal order until both documented gates settle."""

    if max_refinement_iterations < 0:
        raise EvaluationError("max_refinement_iterations must be non-negative")
    if max_new_frequency_points < 0:
        raise EvaluationError("max_new_frequency_points must be non-negative")
    if min(rms_tolerance_db, max_tolerance_db, peak_shift_tolerance_percent) <= 0.0:
        raise EvaluationError("convergence tolerances must be positive")

    initial_count = int(request.frequencies_hz.size)
    working = request
    high_x = min(request.max_mode_x, max_mode_x)
    high_y = min(request.max_mode_y, max_mode_y)
    frequency_result = _refine_frequency_for_modes(
        working,
        mode_x=high_x,
        mode_y=high_y,
        max_refinement_iterations=max_refinement_iterations,
        max_new_frequency_points=max_new_frequency_points,
        rms_tolerance_db=rms_tolerance_db,
        max_tolerance_db=max_tolerance_db,
        peak_shift_tolerance_percent=peak_shift_tolerance_percent,
    )
    working = frequency_result.request
    high = frequency_result.outcome
    refinements = frequency_result.iterations

    lower_x = max(0, high_x - 2)
    lower_y = max(0, high_y - 2)
    low = evaluate_rail(replace(working, max_mode_x=lower_x, max_mode_y=lower_y))
    modal_values = _modal_convergence_delta(
        request,
        low,
        high,
        rms_tolerance_db=rms_tolerance_db,
        max_tolerance_db=max_tolerance_db,
        peak_shift_tolerance_percent=peak_shift_tolerance_percent,
    )
    while not modal_values[3] and (high_x < max_mode_x or high_y < max_mode_y):
        lower_x, lower_y = high_x, high_y
        high_x = min(max_mode_x, high_x + 2)
        high_y = min(max_mode_y, high_y + 2)
        # Newly admitted modes can reveal resonances missing from the previous
        # curve, so run frequency refinement again after every mode increase.
        frequency_result = _refine_frequency_for_modes(
            working,
            mode_x=high_x,
            mode_y=high_y,
            max_refinement_iterations=max_refinement_iterations,
            max_new_frequency_points=max_new_frequency_points,
            rms_tolerance_db=rms_tolerance_db,
            max_tolerance_db=max_tolerance_db,
            peak_shift_tolerance_percent=peak_shift_tolerance_percent,
        )
        working = frequency_result.request
        high = frequency_result.outcome
        refinements += frequency_result.iterations
        low = evaluate_rail(replace(working, max_mode_x=lower_x, max_mode_y=lower_y))
        modal_values = _modal_convergence_delta(
            request,
            low,
            high,
            rms_tolerance_db=rms_tolerance_db,
            max_tolerance_db=max_tolerance_db,
            peak_shift_tolerance_percent=peak_shift_tolerance_percent,
        )

    modal_rms, modal_max, modal_peak_shift, modal_converged = modal_values
    frequency_converged = frequency_result.converged
    combined_rms = max(frequency_result.rms_delta_db, modal_rms)
    combined_max = max(frequency_result.max_delta_db, modal_max)
    combined_peak_shift = max(frequency_result.peak_shift_percent, modal_peak_shift)
    converged = frequency_converged and modal_converged
    report = ConvergenceReport(
        initial_frequency_points=initial_count,
        final_frequency_points=int(working.frequencies_hz.size),
        refinement_iterations=refinements,
        lower_mode_x=lower_x,
        lower_mode_y=lower_y,
        final_mode_x=high_x,
        final_mode_y=high_y,
        critical_rms_delta_db=combined_rms,
        critical_max_delta_db=combined_max,
        dominant_peak_shift_percent=combined_peak_shift,
        frequency_rms_delta_db=frequency_result.rms_delta_db,
        frequency_max_delta_db=frequency_result.max_delta_db,
        frequency_peak_shift_percent=frequency_result.peak_shift_percent,
        frequency_converged=frequency_converged,
        frequency_budget_exhausted=frequency_result.budget_exhausted,
        modal_rms_delta_db=modal_rms,
        modal_max_delta_db=modal_max,
        modal_peak_shift_percent=modal_peak_shift,
        modal_converged=modal_converged,
        converged=converged,
    )
    final_request = replace(
        working,
        max_mode_x=high_x,
        max_mode_y=high_y,
        confidence_inputs=replace(working.confidence_inputs, modal_converged=converged),
        assumptions=(
            *working.assumptions,
            (
                f"adaptive frequency refinement: {initial_count} -> "
                f"{working.frequencies_hz.size} points; converged={frequency_converged}"
            ),
            (
                f"modal convergence: {lower_x}x{lower_y} -> {high_x}x{high_y}, "
                f"RMS {modal_rms:.3f} dB, max {modal_max:.3f} dB, "
                f"peak shift {modal_peak_shift:.3f}%"
            ),
        ),
    )
    # ``high`` is already the exact solve for ``final_request``'s frequency
    # grid and modal order.  Recomputing it only to refresh confidence and
    # assumptions used to duplicate the most expensive high-order solve.
    final_confidence = assess_confidence(
        high.solve.frequencies_hz,
        high.solve.diagnostics,
        final_request.plane,
        final_request.confidence_inputs,
        parallel_planes=final_request.parallel_planes,
    )
    final_assumptions = tuple(
        dict.fromkeys((*final_request.assumptions, COUPLING_ASSUMPTION))
    )
    return replace(
        high,
        confidence=final_confidence,
        assumptions=final_assumptions,
        convergence=report,
    )


def _refine_frequency_for_modes(
    request: EvaluationRequest,
    *,
    mode_x: int,
    mode_y: int,
    max_refinement_iterations: int,
    max_new_frequency_points: int,
    rms_tolerance_db: float,
    max_tolerance_db: float,
    peak_shift_tolerance_percent: float,
) -> _FrequencyRefinementResult:
    working = replace(request, max_mode_x=mode_x, max_mode_y=mode_y)
    current = evaluate_rail(working)
    iterations = 0
    last_rms = rms_tolerance_db
    last_max = max_tolerance_db
    last_peak_shift = peak_shift_tolerance_percent
    converged = False
    budget_exhausted = False

    if current.solve.frequencies_hz.size < 3:
        return _FrequencyRefinementResult(
            working,
            current,
            iterations,
            last_rms,
            last_max,
            last_peak_shift,
            False,
            True,
        )

    while True:
        # Probe with one point even when the budget is zero: lack of budget is
        # not evidence that the current grid is stable.
        probe_points = max(1, max_new_frequency_points)
        refined = refine_log_grid(
            current.solve.frequencies_hz,
            current.solve.impedance_ohm,
            max_new_points=probe_points,
        ).frequencies_hz
        if refined.size == current.solve.frequencies_hz.size:
            converged = True
            budget_exhausted = False
            last_rms = last_max = last_peak_shift = 0.0
            break
        if iterations >= max_refinement_iterations or max_new_frequency_points == 0:
            budget_exhausted = True
            break

        previous = current
        working = replace(working, frequencies_hz=refined)
        current = evaluate_rail(working)
        iterations += 1
        last_rms, last_max, last_peak_shift, converged = _frequency_grid_delta(
            request,
            previous,
            current,
            rms_tolerance_db=rms_tolerance_db,
            max_tolerance_db=max_tolerance_db,
            peak_shift_tolerance_percent=peak_shift_tolerance_percent,
        )
        if converged:
            budget_exhausted = False
            break

    return _FrequencyRefinementResult(
        working,
        current,
        iterations,
        last_rms,
        last_max,
        last_peak_shift,
        converged,
        budget_exhausted,
    )


def _frequency_grid_delta(
    request: EvaluationRequest,
    lower: EvaluationOutcome,
    higher: EvaluationOutcome,
    *,
    rms_tolerance_db: float,
    max_tolerance_db: float,
    peak_shift_tolerance_percent: float,
) -> tuple[float, float, float, bool]:
    lower_frequency = lower.solve.frequencies_hz
    higher_frequency = higher.solve.frequencies_hz
    floor = np.finfo(float).tiny
    lower_db = 20.0 * np.log10(np.maximum(np.abs(lower.solve.impedance_ohm), floor))
    higher_db = 20.0 * np.log10(np.maximum(np.abs(higher.solve.impedance_ohm), floor))
    interpolated_lower_db = np.interp(
        np.log(higher_frequency), np.log(lower_frequency), lower_db
    )
    critical = (
        (higher_frequency >= request.critical_band_hz[0])
        & (higher_frequency <= request.critical_band_hz[1])
    )
    delta = higher_db[critical] - interpolated_lower_db[critical]
    rms_delta = float(np.sqrt(np.mean(delta**2)))
    max_delta = float(np.max(np.abs(delta)))
    lower_peak = _dominant_local_peak_frequency(lower)
    higher_peak = _dominant_local_peak_frequency(higher)
    if lower_peak is None and higher_peak is None:
        peak_shift = 0.0
    elif lower_peak is None or higher_peak is None:
        peak_shift = peak_shift_tolerance_percent
    else:
        peak_shift = abs(higher_peak - lower_peak) / higher_peak * 100.0
    converged = (
        rms_delta < rms_tolerance_db
        and max_delta < max_tolerance_db
        and peak_shift < peak_shift_tolerance_percent
    )
    return rms_delta, max_delta, peak_shift, converged


def _dominant_local_peak_frequency(outcome: EvaluationOutcome) -> float | None:
    if not outcome.metrics.peaks:
        return None
    peak = max(
        outcome.metrics.peaks,
        key=lambda item: (item.prominence_db, item.impedance_ohm, -item.frequency_hz),
    )
    return float(peak.frequency_hz)


def _modal_convergence_delta(
    request: EvaluationRequest,
    lower: EvaluationOutcome,
    higher: EvaluationOutcome,
    *,
    rms_tolerance_db: float,
    max_tolerance_db: float,
    peak_shift_tolerance_percent: float,
) -> tuple[float, float, float, bool]:
    frequencies = higher.solve.frequencies_hz
    if not np.array_equal(lower.solve.frequencies_hz, frequencies):
        raise EvaluationError("modal convergence outcomes must share one frequency grid")
    floor = np.finfo(float).tiny
    lower_db = 20.0 * np.log10(np.maximum(np.abs(lower.solve.impedance_ohm), floor))
    higher_db = 20.0 * np.log10(np.maximum(np.abs(higher.solve.impedance_ohm), floor))
    critical = (
        (frequencies >= request.critical_band_hz[0])
        & (frequencies <= request.critical_band_hz[1])
    )
    delta = higher_db[critical] - lower_db[critical]
    rms_delta = float(np.sqrt(np.mean(delta**2)))
    max_delta = float(np.max(np.abs(delta)))
    selected_frequencies = frequencies[critical]
    lower_peak = float(selected_frequencies[int(np.argmax(lower_db[critical]))])
    higher_peak = float(selected_frequencies[int(np.argmax(higher_db[critical]))])
    peak_shift = abs(higher_peak - lower_peak) / higher_peak * 100.0
    converged = (
        rms_delta < rms_tolerance_db
        and max_delta < max_tolerance_db
        and peak_shift < peak_shift_tolerance_percent
    )
    return rms_delta, max_delta, peak_shift, converged


def compile_project_evaluation_template(
    project: Any,
    rail_id: str,
) -> ProjectEvaluationTemplate:
    """Compile plane, Device pairing, models, and topology for repeated states."""

    rails = {item.rail_id: item for item in project.rails}
    if rail_id not in rails:
        raise EvaluationError(f"unknown rail_id {rail_id!r}")
    rail = rails[rail_id]
    if str(getattr(rail.state, "value", rail.state)) != "ACTIVE":
        raise EvaluationError(f"rail {rail_id!r} is not ACTIVE")
    (
        plane,
        parallel_planes,
        origin_um,
        partition_confirmed,
        plane_assumptions,
    ) = _planes_from_project(project, rail)
    cap_models = {item.model_id: _cap_model(item) for item in project.cap_models}
    via_templates = {item.template_id: item for item in project.via_templates}
    via_models = {key: _via_model(value) for key, value in via_templates.items()}
    topology_maps = {item.slot_id: item for item in project.topology_maps}
    shared_pad_clusters = {
        item.cluster_id: item
        for item in getattr(project, "shared_pad_clusters", ())
    }
    device, pairing_assumptions, pairing_confident = _device_connection(
        project,
        rail,
        plane,
        origin_um,
        via_templates,
        via_models,
    )
    return ProjectEvaluationTemplate(
        source_project=project,
        rail_id=rail_id,
        plane=plane,
        parallel_planes=parallel_planes,
        origin_um=origin_um,
        partition_confirmed=partition_confirmed,
        cap_models=cap_models,
        via_templates=via_templates,
        via_models=via_models,
        topology_maps=topology_maps,
        shared_pad_clusters=shared_pad_clusters,
        device=device,
        plane_assumptions=plane_assumptions,
        pairing_assumptions=pairing_assumptions,
        pairing_confident=pairing_confident,
    )


def build_project_evaluation_request(
    project: Any,
    rail_id: str,
    *,
    max_mode_x: int = 6,
    max_mode_y: int = 6,
    mode_count: int | None = None,
    worker_count: int = 1,
    geometry_confirmed: bool | None = None,
    templates_calibrated: bool = False,
    template: ProjectEvaluationTemplate | None = None,
    assume_static_template_compatible: bool = False,
) -> EvaluationRequest:
    """Adapt ``spd_decap_pi._core.domain.ProjectSpec`` into the numerical request.

    The adapter intentionally uses the public project attributes rather than
    embedding Pydantic types in the numerical kernel.  It is therefore also
    usable with validated project-like objects in tests and batch tooling.
    """

    rails = {item.rail_id: item for item in project.rails}
    if rail_id not in rails:
        raise EvaluationError(f"unknown rail_id {rail_id!r}")
    rail = rails[rail_id]
    if str(getattr(rail.state, "value", rail.state)) != "ACTIVE":
        raise EvaluationError(f"rail {rail_id!r} is not ACTIVE")

    compiled = template or compile_project_evaluation_template(project, rail_id)
    if compiled.rail_id != rail_id:
        raise EvaluationError(
            f"evaluation template rail {compiled.rail_id!r} does not match {rail_id!r}"
        )
    if (
        template is not None
        and compiled.source_project is not project
        and not assume_static_template_compatible
    ):
        raise EvaluationError(
            "evaluation template belongs to a different project object; pass "
            "assume_static_template_compatible=True only when plane, pins, models, "
            "topology, and via data are unchanged"
        )

    frequencies = _project_frequencies(project.frequency)
    target = _target_from_rail(rail, frequencies)
    placements = {
        item.slot_id: item
        for item in project.placements
        if item.rail_id == rail_id and str(getattr(item.topology, "value", item.topology)) != "EMPTY"
    }

    shunts = _placement_shunts(
        rail_id,
        rail.pwr_layer,
        rail.gnd_layer,
        compiled.plane,
        compiled.origin_um,
        placements,
        compiled.topology_maps,
        compiled.cap_models,
        compiled.via_templates,
        compiled.via_models,
        compiled.shared_pad_clusters,
    )
    model_min, model_max, model_validity_known = _shared_model_range(
        tuple(branch.series_path for branch in compiled.device.branches)
        + tuple(group.network for group in shunts)
    )
    confirmed = (
        compiled.partition_confirmed and compiled.pairing_confident
        if geometry_confirmed is None
        else geometry_confirmed
    )
    assumptions = (
        tuple(getattr(project, "assumptions", ()))
        + compiled.plane_assumptions
        + compiled.pairing_assumptions
    )
    return EvaluationRequest(
        rail_id=rail_id,
        frequencies_hz=frequencies,
        plane=compiled.plane,
        parallel_planes=compiled.parallel_planes,
        device=compiled.device,
        shunts=shunts,
        target=target,
        critical_band_hz=(
            float(project.frequency.critical_start_hz),
            float(project.frequency.critical_stop_hz),
        ),
        max_mode_x=max_mode_x,
        max_mode_y=max_mode_y,
        mode_count=mode_count,
        worker_count=worker_count,
        confidence_inputs=ConfidenceInputs(
            geometry_confirmed=confirmed,
            templates_calibrated=templates_calibrated,
            coupling_modeled=False,
            model_valid_min_hz=model_min,
            model_valid_max_hz=model_max,
            model_validity_known=model_validity_known,
            modal_converged=None,
            mixed_reference_rectangular_approximation=(
                getattr(rail, "mixed_reference_certificate", None) is not None
            ),
        ),
        assumptions=assumptions,
    )


def evaluate_project_rail(project: Any, rail_id: str, **kwargs: Any) -> EvaluationOutcome:
    return evaluate_rail(build_project_evaluation_request(project, rail_id, **kwargs))


def evaluate_project_rail_converged(
    project: Any,
    rail_id: str,
    *,
    request_options: dict[str, Any] | None = None,
    **convergence_options: Any,
) -> EvaluationOutcome:
    request = build_project_evaluation_request(project, rail_id, **(request_options or {}))
    return evaluate_rail_converged(request, **convergence_options)


def to_domain_evaluation_result(outcome: EvaluationOutcome) -> Any:
    """Convert an outcome to the JSON-safe ``domain.EvaluationResult``."""

    from spd_decap_pi._core.domain import (
        ConfidenceBand,
        ConfidenceCategory as DomainConfidenceCategory,
        ConfidenceLevel as DomainConfidenceLevel,
        EvaluationMetrics as DomainEvaluationMetrics,
        EvaluationResult,
        PeakMetric,
    )

    return EvaluationResult(
        solver_version=outcome.solver_version,
        rail_id=outcome.rail_id,
        frequencies_hz=outcome.solve.frequencies_hz.tolist(),
        z_real_ohm=outcome.solve.impedance_ohm.real.tolist(),
        z_imag_ohm=outcome.solve.impedance_ohm.imag.tolist(),
        metrics=DomainEvaluationMetrics(
            max_violation_db=outcome.metrics.max_violation_db,
            rms_violation_db=outcome.metrics.rms_violation_db,
            max_peak_prominence_db=outcome.metrics.max_peak_prominence_db,
            target_met=outcome.metrics.target_met,
        ),
        peaks=[
            PeakMetric(
                peak_id=peak.peak_id,
                frequency_hz=peak.frequency_hz,
                impedance_ohm=peak.impedance_ohm,
                prominence_db=peak.prominence_db,
            )
            for peak in outcome.metrics.peaks
        ],
        assumptions=list(outcome.assumptions),
        confidence=[
            ConfidenceBand(
                category=DomainConfidenceCategory(item.category.value),
                start_hz=item.start_hz,
                stop_hz=item.stop_hz,
                level=DomainConfidenceLevel(item.level.value),
                reason=item.reason,
            )
            for item in outcome.confidence
        ],
    )


def _project_frequencies(settings: Any) -> NDArray[np.float64]:
    if settings.spacing == "log":
        return np.geomspace(settings.start_hz, settings.stop_hz, settings.points)
    return np.linspace(settings.start_hz, settings.stop_hz, settings.points)


def _target_from_rail(rail: Any, frequencies: NDArray[np.float64]) -> TargetMask:
    points = list(rail.target_mask)
    if not points:
        raise EvaluationError(f"rail {rail.rail_id!r} has no target mask")
    if len(points) == 1:
        return TargetMask.constant(
            points[0].impedance_ohm,
            start_hz=float(frequencies[0]),
            stop_hz=float(frequencies[-1]),
        )
    target_frequencies = [float(point.frequency_hz) for point in points]
    target_values = [float(point.impedance_ohm) for point in points]
    if target_frequencies[0] > frequencies[0]:
        target_frequencies.insert(0, float(frequencies[0]))
        target_values.insert(0, target_values[0])
    if target_frequencies[-1] < frequencies[-1]:
        target_frequencies.append(float(frequencies[-1]))
        target_values.append(target_values[-1])
    return TargetMask(np.asarray(target_frequencies), np.asarray(target_values))


def _plane_from_project(
    project: Any, rail: Any
) -> tuple[RectangularPlane, tuple[float, float], bool]:
    """Compatibility view of the primary component of a project plane pair."""

    plane, _parallel, origin, confirmed, _assumptions = _planes_from_project(
        project, rail
    )
    return plane, origin, confirmed


def _planes_from_project(
    project: Any, rail: Any
) -> tuple[
    RectangularPlane,
    tuple[RectangularPlane, ...],
    tuple[float, float],
    bool,
    tuple[str, ...],
]:
    """Build the selected pair and an immediately adjacent common-DGND return.

    A second component is deliberately limited to the first conductor on the
    opposite side of the selected PWR layer.  This permits the disclosed
    shared-PWR, ideal-common-reference equivalent without crossing another
    conductor or treating a PWR neighbor as a return path.
    """

    layers = list(project.stackup_layers)
    layer_index = {layer.name: index for index, layer in enumerate(layers)}
    if rail.pwr_layer not in layer_index or rail.gnd_layer not in layer_index:
        raise EvaluationError("selected PWR/DGND layer is absent from the stack-up")
    pwr_index = layer_index[rail.pwr_layer]
    gnd_index = layer_index[rail.gnd_layer]
    pwr_layer = layers[pwr_index]
    gnd_layer = layers[gnd_index]
    if not pwr_layer.is_conductor or not gnd_layer.is_conductor:
        raise EvaluationError("selected PWR/DGND layers must be conductor rows")
    gnd_aliases = {str(item).casefold() for item in project.gnd_aliases}
    certificate = getattr(rail, "mixed_reference_certificate", None)
    witness = getattr(rail, "mixed_reference_ground_witness", None)
    if certificate is not None and witness is None:
        raise EvaluationError(
            "mixed-reference DGND evaluation requires a source-ground reachability witness"
        )
    if certificate is not None and getattr(witness, "gnd_asset_sha256", None) != certificate.gnd_asset_sha256:
        raise EvaluationError(
            "mixed-reference DGND witness does not match the certificate DGND artwork"
        )
    if not _is_configured_ground_layer(gnd_layer, gnd_aliases, certificate, rail):
        raise EvaluationError(
            "selected DGND layer must contain only configured GND aliases"
        )

    partition = next(
        (
            item
            for item in project.partitions
            if item.layer == rail.pwr_layer and rail.domain in item.domain_to_cell
        ),
        None,
    )
    if partition is None:
        width_um = float(project.outline.width_um)
        height_um = float(project.outline.height_um)
        origin = (float(project.outline.origin_x_um), float(project.outline.origin_y_um))
        domains_on_layer = {
            item.domain for item in project.rails if item.pwr_layer == rail.pwr_layer
        }
        confirmed = bool(
            len(domains_on_layer) == 1
            and (getattr(project, "metadata", {}) or {}).get(
                "plane_pair_confirmed", False
            )
        )
    else:
        cell_id = partition.domain_to_cell[rail.domain]
        cell = next(item for item in partition.cells if item.cell_id == cell_id)
        width_um = float(cell.x_max_um - cell.x_min_um)
        height_um = float(cell.y_max_um - cell.y_min_um)
        origin = (float(cell.x_min_um), float(cell.y_min_um))
        confirmed = bool(partition.confirmed)
    primary = _plane_component(
        pwr_layer,
        gnd_layer,
        layers,
        pwr_index,
        gnd_index,
        width_um,
        height_um,
        selected=True,
    )
    parallel: list[RectangularPlane] = []
    primary_direction = 1 if gnd_index > pwr_index else -1
    opposite_direction = -primary_direction
    candidate_index = _first_conductor_index(layers, pwr_index, opposite_direction)
    if candidate_index is not None:
        candidate = layers[candidate_index]
        if _is_configured_ground_layer(candidate, gnd_aliases) and _has_valid_dielectric_rows(
            layers, pwr_index, candidate_index
        ):
            parallel.append(
                _plane_component(
                    pwr_layer,
                    candidate,
                    layers,
                    pwr_index,
                    candidate_index,
                    width_um,
                    height_um,
                    selected=False,
                )
            )
    assumptions: tuple[str, ...] = ()
    if certificate is not None:
        assumptions = (
            "LOW confidence: mixed-reference rectangular approximation; "
            f"DGND overlap {certificate.overlap_fraction:.2%}, dominant overlap "
            f"component {certificate.dominant_overlap_component_fraction:.2%}; "
            "coverage does not scale plane electrical parameters",
        )
    if parallel:
        component_names = (f"{rail.pwr_layer}/{rail.gnd_layer}",)
        component_names += (f"{rail.pwr_layer}/{layers[candidate_index].name}",)
        assumptions += (
            "shared-PWR ideal-common-reference components: " + "; ".join(component_names),
            "DGND component layers are treated as an ideal common reference",
        )
    if any(
        not layer.is_conductor and bool(getattr(layer, "dielectric_properties", ()))
        for layer in layers
    ):
        assumptions += (
            "frequency-dependent SPD dielectric tables use clamped log-frequency-linear Dk/Df interpolation; no material curve fitting is applied",
            "resonance metadata retains the nominal series Dk while the modal shunt admittance uses the source dielectric table",
        )
    return primary, tuple(parallel), origin, confirmed, assumptions


def _is_configured_ground_layer(
    layer: Any,
    gnd_aliases: set[str],
    certificate: Any | None = None,
    rail: Any | None = None,
) -> bool:
    if not layer.is_conductor:
        return False
    keys = {str(item).casefold() for item in layer.pwr_nets}
    if bool(keys) and keys.issubset(gnd_aliases):
        return certificate is None
    if certificate is None or rail is None:
        return False
    return (
        str(certificate.rail_net).casefold() == str(rail.net).casefold()
        and str(certificate.pwr_layer).casefold() == str(rail.pwr_layer).casefold()
        and str(certificate.gnd_layer).casefold() == str(rail.gnd_layer).casefold()
        and str(rail.net).casefold() not in keys
        and any(alias in str(layer.name).casefold() for alias in gnd_aliases)
        and float(certificate.overlap_fraction) >= MIXED_REFERENCE_MIN_COVERAGE
        and float(certificate.dominant_overlap_component_fraction)
        >= MIXED_REFERENCE_MIN_DOMINANT_COMPONENT
    )


def _first_conductor_index(
    layers: list[Any], start: int, direction: int
) -> int | None:
    index = start + direction
    while 0 <= index < len(layers):
        if layers[index].is_conductor:
            return index
        index += direction
    return None


def _has_valid_dielectric_rows(
    layers: list[Any], first: int, second: int
) -> bool:
    lower, upper = sorted((first, second))
    between = layers[lower + 1 : upper]
    return bool(between) and all(
        not layer.is_conductor and layer.dk is not None for layer in between
    )


def _plane_component(
    pwr_layer: Any,
    gnd_layer: Any,
    layers: list[Any],
    pwr_index: int,
    gnd_index: int,
    width_um: float,
    height_um: float,
    *,
    selected: bool,
) -> RectangularPlane:
    lower, upper = sorted((pwr_index, gnd_index))
    between = layers[lower + 1 : upper]
    intervening_conductors = [layer.name for layer in between if layer.is_conductor]
    if intervening_conductors:
        label = "selected" if selected else "parallel"
        raise EvaluationError(
            f"{label} plane pair crosses intervening conductor layers: "
            + ", ".join(intervening_conductors)
        )
    dielectrics = [layer for layer in between if not layer.is_conductor]
    if not dielectrics or any(layer.dk is None for layer in dielectrics):
        label = "selected" if selected else "parallel"
        raise EvaluationError(f"{label} plane pair requires dielectric rows with Dk")
    separation_um = sum(float(layer.thickness_um) for layer in dielectrics)
    series_weight = sum(float(layer.thickness_um) / float(layer.dk) for layer in dielectrics)
    effective_dk = separation_um / series_weight
    effective_df = sum(
        (float(layer.thickness_um) / float(layer.dk)) * float(layer.df or 0.0)
        for layer in dielectrics
    ) / series_weight
    has_dispersion = any(
        bool(getattr(layer, "dielectric_properties", ())) for layer in dielectrics
    )
    dielectric_layers = ()
    if has_dispersion:
        rows: list[DielectricLayer] = []
        for layer in dielectrics:
            properties = tuple(getattr(layer, "dielectric_properties", ()))
            if properties:
                frequencies = tuple(float(item.frequency_hz) for item in properties)
                dk_values = tuple(float(item.dk) for item in properties)
                df_values = tuple(float(item.df) for item in properties)
            else:
                # An un-tabulated row remains its documented legacy scalar
                # while its neighboring source-tabulated rows are evaluated at
                # frequency.  It must still participate in the series complex
                # dielectric impedance rather than being averaged separately.
                frequencies = (1.0e9,)
                dk_values = (float(layer.dk),)
                df_values = (float(layer.df or 0.0),)
            rows.append(
                DielectricLayer(
                    thickness_m=float(layer.thickness_um) * 1e-6,
                    dispersion=DielectricDispersion(
                        frequencies_hz=frequencies,
                        relative_permittivities=dk_values,
                        loss_tangents=df_values,
                    ),
                    material=getattr(layer, "material", None),
                )
            )
        dielectric_layers = tuple(rows)
    return RectangularPlane(
        width_m=width_um * 1e-6,
        height_m=height_um * 1e-6,
        separation_m=separation_um * 1e-6,
        relative_permittivity=effective_dk,
        loss_tangent=effective_df,
        conductivity_s_per_m=min(
            float(pwr_layer.conductivity_s_m), float(gnd_layer.conductivity_s_m)
        ),
        power_thickness_m=float(pwr_layer.thickness_um) * 1e-6,
        ground_thickness_m=float(gnd_layer.thickness_um) * 1e-6,
        power_conductivity_s_per_m=float(pwr_layer.conductivity_s_m),
        ground_conductivity_s_per_m=float(gnd_layer.conductivity_s_m),
        dielectric_layers=dielectric_layers,
    )


def _sampled_model(model_id: str, samples: list[Any]) -> SampledImpedanceModel:
    return SampledImpedanceModel(
        model_id=model_id,
        frequencies_hz=np.asarray([item.frequency_hz for item in samples], dtype=np.float64),
        values_ohm=np.asarray(
            [complex(item.real_ohm, item.imag_ohm) for item in samples],
            dtype=np.complex128,
        ),
    )


def _cap_model(item: Any) -> ImpedanceModel:
    if item.impedance:
        return _sampled_model(item.model_id, item.impedance)
    return SeriesRLCModel(
        item.model_id,
        capacitance_f=float(item.capacitance_f),
        esr_ohm=float(item.esr_ohm or 0.0),
        esl_h=float(item.esl_h or 0.0),
    )


def _via_model(item: Any) -> ImpedanceModel:
    if item.impedance:
        return _sampled_model(item.template_id, item.impedance)
    if float(item.loop_resistance_ohm) == 0.0 and float(item.loop_inductance_h) == 0.0:
        raise EvaluationError(f"via template {item.template_id!r} has no R/L or sampled impedance")
    return SeriesRLModel(
        item.template_id,
        resistance_ohm=float(item.loop_resistance_ohm),
        inductance_h=float(item.loop_inductance_h),
    )


def _finite_port(
    x_um: float,
    y_um: float,
    origin_um: tuple[float, float],
    template: Any,
    port_id: str,
    *,
    width_um: float | None = None,
    height_um: float | None = None,
) -> FinitePort:
    return FinitePort(
        x_m=(float(x_um) - origin_um[0]) * 1e-6,
        y_m=(float(y_um) - origin_um[1]) * 1e-6,
        width_m=float(
            template.finite_port_width_um if width_um is None else width_um
        )
        * 1e-6,
        height_m=float(
            template.finite_port_height_um if height_um is None else height_um
        )
        * 1e-6,
        port_id=port_id,
    )


def _shared_pad_terminal_model(
    path: Any,
    fallback: ImpedanceModel,
) -> ImpedanceModel:
    """Use source-terminal R/L unless a sampled differential template owns it.

    SharedPadClusterModel stamps every terminal branch at ``2 / Zmodel``.
    A physical source terminal impedance therefore needs a transient model of
    ``2 * Zterminal`` so the stamped branch remains exactly ``1 / Zterminal``.
    Returning the original fallback object verbatim keeps legacy clusters
    eligible for the homogeneous shared-pad batch path.
    """

    if not getattr(path, "has_source_terminal_rl", False):
        return fallback
    if isinstance(fallback, SampledImpedanceModel):
        return fallback
    return SeriesRLModel(
        f"{path.path_id}:SOURCE_TERMINAL",
        resistance_ohm=2.0 * float(path.terminal_resistance_ohm),
        inductance_h=2.0 * float(path.terminal_inductance_h),
    )


def _device_connection(
    project: Any,
    rail: Any,
    plane: RectangularPlane,
    origin_um: tuple[float, float],
    templates: dict[str, Any],
    via_models: dict[str, ImpedanceModel],
) -> tuple[DeviceConnection, tuple[str, ...], bool]:
    from spd_decap_pi._core.geometry.pairing import BumpPairingError, pair_device_bumps

    power_pins = [
        pin
        for pin in project.pins
        if str(getattr(pin.kind, "value", pin.kind)) == "DEVICE_BUMP"
        and str(getattr(pin.terminal, "value", pin.terminal)) == "PWR"
        and pin.net.casefold() == rail.net.casefold()
        and (pin.domain is None or pin.domain == rail.domain)
        and (pin.site is None or pin.site == rail.site)
    ]
    if not power_pins:
        raise EvaluationError(f"rail {rail.rail_id!r} has no matching Device PWR bump")
    ground_pins = []
    for pin in project.pins:
        if (
            str(getattr(pin.kind, "value", pin.kind)) != "DEVICE_BUMP"
            or str(getattr(pin.terminal, "value", pin.terminal)) != "GND"
            or (pin.site is not None and pin.site != rail.site)
        ):
            continue
        if pin.via_template_id:
            if pin.via_template_id not in templates:
                raise EvaluationError(
                    f"Device DGND bump {pin.pin_id!r} references unknown via template"
                )
            ground_template = templates[pin.via_template_id]
            # A shared top-side DGND population may contain returns terminating
            # on several internal ground layers.  Only terminals whose explicit
            # path reaches this rail's selected plane pair participate in its
            # local supernode.  Unspecified terminals remain eligible and are
            # resolved below against the unique matching template.
            if (
                ground_template.pwr_reference_layer != rail.pwr_layer
                or ground_template.gnd_reference_layer != rail.gnd_layer
            ):
                continue
        ground_pins.append(pin)
    if not ground_pins:
        raise EvaluationError(
            f"rail {rail.rail_id!r} has no Device DGND bump for its return path"
        )
    pairing_candidates = [*power_pins, *ground_pins]
    try:
        pairing = pair_device_bumps(
            pairing_candidates,
            rail_net=rail.net,
            gnd_aliases=project.gnd_aliases,
            # Candidates were already restricted to the selected site.  Treat
            # an unspecified GND site as belonging to that selection rather
            # than creating an unpairable UNSPECIFIED_SITE bucket.
            group_by_site=False,
        )
    except BumpPairingError as exc:
        raise EvaluationError(f"Device PWR/DGND pairing failed: {exc}") from exc
    power_lookup = {pin.pin_id: pin for pin in power_pins}
    ground_lookup = {pin.pin_id: pin for pin in ground_pins}
    if pairing.unmatched_power_pin_ids or pairing.unmatched_ground_pin_ids:
        raise EvaluationError(
            "every Device PWR/DGND bump must belong to a coordinate cluster; "
            + pairing.confidence_reason
        )
    matching_templates = [
        item
        for item in templates.values()
        if item.pwr_reference_layer == rail.pwr_layer
        and item.gnd_reference_layer == rail.gnd_layer
    ]
    branches: list[DeviceBranch] = []
    unequal_cluster = False
    for cluster in sorted(pairing.clusters, key=lambda item: item.cluster_id.casefold()):
        cluster_power = [power_lookup[pin_id] for pin_id in cluster.power_pin_ids]
        cluster_ground = [ground_lookup[pin_id] for pin_id in cluster.ground_pin_ids]
        if not cluster_power or not cluster_ground:
            raise EvaluationError(
                f"Device cluster {cluster.cluster_id!r} must contain PWR and DGND terminals"
            )
        unequal_cluster |= len(cluster_power) != len(cluster_ground)
        explicit_templates = {
            pin.via_template_id
            for pin in (*cluster_power, *cluster_ground)
            if pin.via_template_id
        }
        if len(explicit_templates) > 1:
            raise EvaluationError(
                f"Device cluster {cluster.cluster_id!r} has conflicting via templates"
            )
        if explicit_templates:
            template_id = next(iter(explicit_templates))
            if template_id not in templates:
                raise EvaluationError(
                    f"Device cluster {cluster.cluster_id!r} references unknown via template"
                )
            template = templates[template_id]
        elif len(matching_templates) == 1:
            template = matching_templates[0]
        else:
            raise EvaluationError(
                f"Device cluster {cluster.cluster_id!r} needs an explicit via_template_id"
            )
        if (
            template.pwr_reference_layer != rail.pwr_layer
            or template.gnd_reference_layer != rail.gnd_layer
        ):
            raise EvaluationError(
                f"Device cluster {cluster.cluster_id!r} uses a via template "
                "with reference layers different from the selected plane pair"
            )
        anchor_pairs = [
            pair
            for pair in pairing.pairs
            if pair.group_key == cluster.group_key
            and pair.power_pin_id in cluster.power_pin_ids
            and pair.ground_pin_id in cluster.ground_pin_ids
        ]
        if len(anchor_pairs) != 1:
            raise EvaluationError(
                f"Device cluster {cluster.cluster_id!r} must have exactly one "
                "coordinate-pairing anchor"
            )
        anchor_power = power_lookup[anchor_pairs[0].power_pin_id]
        # The modal cavity is the selected PWR artwork with a continuous DGND
        # reference.  Locate the excitation at the original paired PWR terminal,
        # just as decap ports use their PWR-pad coordinate.  A PWR/DGND midpoint
        # can legitimately fall outside a narrow PWR polygon when the return bump
        # is beside it.  A full PWR centroid can likewise be displaced by surplus
        # terminals attached to this conservative shared cluster.
        port = _finite_port(
            float(anchor_power.x_um),
            float(anchor_power.y_um),
            origin_um,
            template,
            cluster.cluster_id,
        )
        port.validate_inside(plane)
        branch_id = "|".join(
            [cluster.cluster_id, *cluster.power_pin_ids, *cluster.ground_pin_ids]
        )
        branches.append(
            DeviceBranch(
                branch_id,
                port,
                via_models[template.template_id],
            )
        )
    pairing_level = str(getattr(pairing.confidence, "value", pairing.confidence))
    assumptions = [
        "Device PWR/DGND finite ports use the coordinate-pairing algorithm's anchor PWR terminal on the selected PWR cavity; surplus terminals retain cluster branch membership",
        f"Device bump pairing: {pairing.confidence_reason}",
    ]
    if unequal_cluster:
        assumptions.append(
            "Unequal PWR/DGND cluster uses one conservative shared differential loop template; "
            "its finite port remains at the pairing anchor PWR terminal and "
            "terminal-level mutual/current-crowding is not modeled"
        )
    metadata = getattr(project, "metadata", {}) or {}
    pairing_user_confirmed = bool(metadata.get("device_pairing_confirmed", False))
    geometry_confirmed = pairing_user_confirmed and pairing_level in {"HIGH", "MEDIUM"}
    return DeviceConnection(tuple(branches)), tuple(assumptions), geometry_confirmed


def _placement_shunts(
    rail_id: str,
    pwr_layer: str,
    gnd_layer: str,
    plane: RectangularPlane,
    origin_um: tuple[float, float],
    placements: dict[str, Any],
    topologies: dict[str, Any],
    cap_models: dict[str, ImpedanceModel],
    via_templates: dict[str, Any],
    via_models: dict[str, ImpedanceModel],
    shared_pad_clusters: dict[str, Any],
) -> tuple[ShuntGroup | CoupledShuntGroup, ...]:
    direct_ports: dict[tuple[str, str], list[FinitePort]] = defaultdict(list)
    shared_groups: list[ShuntGroup] = []
    cluster_groups: list[CoupledShuntGroup] = []
    consumed_shared: set[str] = set()
    consumed_cluster: set[str] = set()

    for cluster_id in sorted(shared_pad_clusters):
        cluster = shared_pad_clusters[cluster_id]
        if cluster.rail_id != rail_id:
            continue
        member_ids = tuple(str(item) for item in cluster.member_slot_ids)
        member_capacitors: dict[str, ImpedanceModel] = {}
        for member_id in member_ids:
            topology = topologies.get(member_id)
            if topology is None:
                raise EvaluationError(
                    f"shared-pad cluster {cluster_id!r} references unknown member "
                    f"slot {member_id!r}"
                )
            mapped_kind = str(
                getattr(topology.topology, "value", topology.topology)
            )
            if mapped_kind != "SHARED_PAD_CLUSTER" or topology.cluster_id != cluster_id:
                raise EvaluationError(
                    f"shared-pad cluster {cluster_id!r} member {member_id!r} has "
                    "an incompatible topology mapping"
                )
            if rail_id not in topology.allowed_rail_ids:
                raise EvaluationError(
                    f"shared-pad cluster member {member_id!r} does not allow rail "
                    f"{rail_id!r}"
                )
            placement = placements.get(member_id)
            if placement is None:
                continue
            placement_kind = str(
                getattr(placement.topology, "value", placement.topology)
            )
            if placement_kind != "SHARED_PAD_CLUSTER":
                raise EvaluationError(
                    f"shared-pad member {member_id!r} has incompatible placement "
                    f"topology {placement_kind!r}"
                )
            if placement.rail_id != rail_id:
                raise EvaluationError(
                    f"shared-pad member {member_id!r} is assigned to a different rail"
                )
            if placement.cap_model_id not in cap_models:
                raise EvaluationError(
                    f"unknown cap model {placement.cap_model_id!r} for shared-pad "
                    f"member {member_id!r}"
                )
            member_capacitors[member_id] = cap_models[placement.cap_model_id]

        consumed_cluster.update(member_ids)
        if not cluster.via_paths:
            # An isolated top-pad cluster has no plane stamp.  Keeping its
            # topology members consumed prevents a fake DIRECT via path.
            continue
        path_runtime: dict[str, tuple[str, FinitePort, ImpedanceModel]] = {}
        for path in cluster.via_paths:
            if path.via_template_id not in via_models:
                raise EvaluationError(
                    f"shared-pad via path {path.path_id!r} references an unknown "
                    "via template"
                )
            template = via_templates[path.via_template_id]
            _validate_template_reference_layers(
                template,
                pwr_layer=pwr_layer,
                gnd_layer=gnd_layer,
                context=f"shared-pad via path {path.path_id!r}",
            )
            port = _finite_port(
                path.x_um,
                path.y_um,
                origin_um,
                template,
                str(path.path_id),
                width_um=(
                    float(path.landing_pad_width_um)
                    if getattr(path, "has_source_landing_geometry", False)
                    else None
                ),
                height_um=(
                    float(path.landing_pad_height_um)
                    if getattr(path, "has_source_landing_geometry", False)
                    else None
                ),
            )
            port.validate_inside(plane)
            terminal = str(getattr(path.terminal, "value", path.terminal))
            if terminal not in {"PWR", "GND"}:
                raise EvaluationError(
                    f"shared-pad via path {path.path_id!r} has unsupported "
                    f"terminal {terminal!r}"
                )
            path_runtime[str(path.path_id)] = (
                terminal,
                port,
                _shared_pad_terminal_model(
                    path,
                    via_models[path.via_template_id],
                ),
            )

        power_ports: list[FinitePort] = []
        ground_ports: list[FinitePort] = []
        power_loops: list[ImpedanceModel] = []
        ground_loops: list[ImpedanceModel] = []
        power_component_indices: list[int] = []
        ground_component_indices: list[int] = []
        cluster_capacitors: list[ImpedanceModel] = []
        capacitor_component_indices: list[int] = []
        capacitor_ground_component_indices: list[int] = []
        consumed_power_paths: set[str] = set()
        consumed_ground_paths: set[str] = set()
        consumed_component_members: set[str] = set()
        explicit_ground_components = tuple(
            getattr(cluster, "ground_components", ())
        )
        ground_component_by_member: dict[str, int] = {}
        if explicit_ground_components:
            for component_index, component in enumerate(explicit_ground_components):
                for member_id in component.member_slot_ids:
                    if member_id in ground_component_by_member:
                        raise EvaluationError(
                            f"shared-pad member {member_id!r} belongs to multiple "
                            "GND components"
                        )
                    ground_component_by_member[member_id] = component_index
                for path_id in component.ground_path_ids:
                    if path_id in consumed_ground_paths or path_id not in path_runtime:
                        raise EvaluationError(
                            f"shared-pad GND component references invalid path "
                            f"{path_id!r}"
                        )
                    terminal, port, loop = path_runtime[path_id]
                    if terminal != "GND":
                        raise EvaluationError(
                            f"shared-pad GND component path {path_id!r} is not GND"
                        )
                    consumed_ground_paths.add(path_id)
                    ground_ports.append(port)
                    ground_loops.append(loop)
                    ground_component_indices.append(component_index)
            if set(ground_component_by_member) != set(member_ids):
                raise EvaluationError(
                    f"shared-pad cluster {cluster_id!r} GND components do not cover "
                    "every member"
                )
        else:
            # V4/V3 transient projects have an implicit single continuous GND
            # supernode.  Keep path order and algebra unchanged for exact legacy
            # solver/batch equivalence.
            ground_component_by_member = {member_id: 0 for member_id in member_ids}
        for component_index, component in enumerate(cluster.power_components):
            for member_id in component.member_slot_ids:
                if member_id in consumed_component_members:
                    raise EvaluationError(
                        f"shared-pad member {member_id!r} belongs to multiple "
                        "PWR components"
                    )
                consumed_component_members.add(member_id)
                capacitor = member_capacitors.get(member_id)
                if capacitor is not None:
                    cluster_capacitors.append(capacitor)
                    capacitor_component_indices.append(component_index)
                    capacitor_ground_component_indices.append(
                        ground_component_by_member[member_id]
                    )
            for path_id in component.power_path_ids:
                if path_id in consumed_power_paths or path_id not in path_runtime:
                    raise EvaluationError(
                        f"shared-pad PWR component references invalid path "
                        f"{path_id!r}"
                    )
                terminal, port, loop = path_runtime[path_id]
                if terminal != "PWR":
                    raise EvaluationError(
                        f"shared-pad PWR component path {path_id!r} is not PWR"
                    )
                consumed_power_paths.add(path_id)
                power_ports.append(port)
                power_loops.append(loop)
                power_component_indices.append(component_index)
        if consumed_component_members != set(member_ids):
            raise EvaluationError(
                f"shared-pad cluster {cluster_id!r} PWR components do not cover "
                "every member"
            )
        for path_id, (terminal, port, loop) in path_runtime.items():
            if terminal == "PWR":
                if path_id not in consumed_power_paths:
                    raise EvaluationError(
                        f"shared-pad PWR path {path_id!r} has no component"
                    )
                continue
            if explicit_ground_components:
                if path_id not in consumed_ground_paths:
                    raise EvaluationError(
                        f"shared-pad GND path {path_id!r} has no component"
                    )
                continue
            ground_ports.append(port)
            ground_loops.append(loop)
            ground_component_indices.append(0)
        if not power_ports or not ground_ports:
            raise EvaluationError(
                f"shared-pad cluster {cluster_id!r} requires both PWR and GND "
                "via paths"
            )
        ordered_members = tuple(sorted(member_ids, key=str.casefold))
        network = SharedPadClusterModel(
            model_id=f"SHARED_PAD_CLUSTER:{cluster_id}",
            power_via_loops=tuple(power_loops),
            ground_via_loops=tuple(ground_loops),
            capacitors=tuple(cluster_capacitors),
            power_component_indices=tuple(power_component_indices),
            capacitor_component_indices=tuple(capacitor_component_indices),
            ground_component_indices=tuple(ground_component_indices),
            capacitor_ground_component_indices=tuple(
                capacitor_ground_component_indices
            ),
        )
        physical_network = SharedPadClusterModel(
            model_id=f"SHARED_PAD_CLUSTER_PHYSICAL:{cluster_id}",
            power_via_loops=tuple(power_loops),
            ground_via_loops=tuple(ground_loops),
            capacitors=(),
            power_component_indices=tuple(power_component_indices),
            ground_component_indices=tuple(ground_component_indices),
        )
        cluster_groups.append(
            CoupledShuntGroup(
                group_id=network.model_id,
                ports=tuple((*power_ports, *ground_ports)),
                network=network,
                sensitivity_id=(
                    sensitivity_port_id(
                        "SHARED_PAD_CLUSTER", ordered_members
                    )
                    if cluster_capacitors
                    else None
                ),
                sensitivity_without_network=physical_network,
            )
        )

    for slot_id in sorted(placements):
        placement = placements[slot_id]
        if slot_id in consumed_cluster:
            continue
        if slot_id not in topologies:
            raise EvaluationError(f"placement references unknown topology slot {slot_id!r}")
        topology = topologies[slot_id]
        if rail_id not in topology.allowed_rail_ids:
            raise EvaluationError(f"slot {slot_id!r} does not allow rail {rail_id!r}")
        if placement.cap_model_id not in cap_models:
            raise EvaluationError(f"unknown cap model {placement.cap_model_id!r}")
        if topology.via_template_id not in via_models:
            raise EvaluationError(f"slot {slot_id!r} references unknown via template")
        template = _topology_template(topology, via_templates, via_models)
        _validate_template_reference_layers(
            template[0],
            pwr_layer=pwr_layer,
            gnd_layer=gnd_layer,
            context=f"slot {slot_id!r} via template",
        )
        topology_kind = str(getattr(placement.topology, "value", placement.topology))
        mapped_kind = str(getattr(topology.topology, "value", topology.topology))
        if topology_kind != mapped_kind:
            raise EvaluationError(
                f"placement/topology mismatch for slot {slot_id!r}: "
                f"{topology_kind} != {mapped_kind}"
            )
        if topology_kind == "DIRECT":
            port = _finite_port(
                topology.x_um,
                topology.y_um,
                origin_um,
                template[0],
                sensitivity_port_id("DIRECT", (slot_id,)),
            )
            port.validate_inside(plane)
            direct_ports[(placement.cap_model_id, topology.via_template_id)].append(port)
        elif topology_kind == "SHARED_PAIR":
            if slot_id in consumed_shared:
                continue
            anchor_id, satellite_id = _shared_pair_ids(slot_id, topology)
            if anchor_id not in topologies or satellite_id not in topologies:
                raise EvaluationError(
                    f"SHARED_PAIR {anchor_id!r}/{satellite_id!r} references an "
                    "unknown topology slot"
                )
            if anchor_id not in placements or satellite_id not in placements:
                raise EvaluationError(
                    f"SHARED_PAIR {anchor_id!r}/{satellite_id!r} requires both assignments"
                )
            anchor_topology = topologies[anchor_id]
            anchor_placement = placements[anchor_id]
            satellite_placement = placements[satellite_id]
            if anchor_placement.rail_id != satellite_placement.rail_id:
                raise EvaluationError("SHARED_PAIR members must be assigned to the same rail")
            if anchor_topology.via_template_id not in via_models:
                raise EvaluationError("SHARED_PAIR anchor has no valid shared via template")
            if not anchor_topology.horizontal_template_id:
                raise EvaluationError("SHARED_PAIR anchor requires horizontal_template_id")
            if anchor_topology.horizontal_template_id not in via_models:
                raise EvaluationError("SHARED_PAIR horizontal template is unknown")
            template_object = _topology_template(anchor_topology, via_templates, via_models)[0]
            _validate_template_reference_layers(
                template_object,
                pwr_layer=pwr_layer,
                gnd_layer=gnd_layer,
                context=f"SHARED_PAIR anchor {anchor_id!r} via template",
            )
            horizontal_template = via_templates[anchor_topology.horizontal_template_id]
            _validate_template_reference_layers(
                horizontal_template,
                pwr_layer=pwr_layer,
                gnd_layer=gnd_layer,
                context=f"SHARED_PAIR {anchor_id!r}/{satellite_id!r} horizontal template",
            )
            port = _finite_port(
                anchor_topology.x_um,
                anchor_topology.y_um,
                origin_um,
                template_object,
                sensitivity_port_id("SHARED_PAIR", (anchor_id, satellite_id)),
            )
            port.validate_inside(plane)
            horizontal = via_models[anchor_topology.horizontal_template_id]
            pair = SharedPairModel(
                model_id=f"PAIR:{anchor_id}:{satellite_id}",
                shared_via_loop=via_models[anchor_topology.via_template_id],
                anchor_capacitor=cap_models[anchor_placement.cap_model_id],
                satellite_capacitor=cap_models[satellite_placement.cap_model_id],
                horizontal_power_path=ScaledImpedanceModel(
                    f"{anchor_topology.horizontal_template_id}:P",
                    horizontal,
                    0.5,
                ),
                horizontal_ground_path=ScaledImpedanceModel(
                    f"{anchor_topology.horizontal_template_id}:G",
                    horizontal,
                    0.5,
                ),
            )
            shared_groups.append(ShuntGroup(pair.model_id, (port,), pair))
            consumed_shared.update((anchor_id, satellite_id))
        else:
            raise EvaluationError(f"unsupported placement topology {topology_kind!r}")

    groups: list[ShuntGroup | CoupledShuntGroup] = []
    for (cap_id, via_id), ports in sorted(direct_ports.items()):
        network = DirectBranchModel(
            f"DIRECT:{cap_id}:{via_id}", cap_models[cap_id], via_models[via_id]
        )
        groups.append(ShuntGroup(network.model_id, tuple(ports), network))
    groups.extend(shared_groups)
    groups.extend(cluster_groups)
    return tuple(groups)


def _topology_template(
    topology: Any,
    via_templates: dict[str, Any],
    via_models: dict[str, ImpedanceModel],
) -> tuple[Any, ImpedanceModel]:
    if topology.via_template_id not in via_templates:
        raise EvaluationError("topology references an unknown domain via template")
    return via_templates[topology.via_template_id], via_models[topology.via_template_id]


def _validate_template_reference_layers(
    template: Any,
    *,
    pwr_layer: str,
    gnd_layer: str,
    context: str,
) -> None:
    if (
        template.pwr_reference_layer != pwr_layer
        or template.gnd_reference_layer != gnd_layer
    ):
        raise EvaluationError(
            f"{context} reference layers "
            f"{template.pwr_reference_layer!r}/{template.gnd_reference_layer!r} "
            f"do not match selected PWR/DGND pair {pwr_layer!r}/{gnd_layer!r}"
        )


def _shared_pair_ids(slot_id: str, topology: Any) -> tuple[str, str]:
    if topology.anchor_slot_id:
        return str(topology.anchor_slot_id), slot_id
    if topology.satellite_slot_id:
        return slot_id, str(topology.satellite_slot_id)
    raise EvaluationError(f"SHARED_PAIR slot {slot_id!r} has no partner mapping")


def _shared_model_range(
    models: tuple[object, ...],
) -> tuple[float | None, float | None, bool]:
    flattened: list[ImpedanceModel] = []

    def visit(model: object) -> None:
        if isinstance(model, DirectBranchModel):
            visit(model.capacitor)
            visit(model.via_loop)
        elif isinstance(model, SharedPairModel):
            visit(model.shared_via_loop)
            visit(model.anchor_capacitor)
            visit(model.satellite_capacitor)
            visit(model.horizontal_power_path)
            visit(model.horizontal_ground_path)
        elif isinstance(model, SharedPadClusterModel):
            for via_loop in (*model.power_via_loops, *model.ground_via_loops):
                visit(via_loop)
            for capacitor in model.capacitors:
                visit(capacitor)
        elif isinstance(model, ScaledImpedanceModel):
            visit(model.source)
        else:
            flattened.append(model)  # type: ignore[arg-type]

    for model in models:
        visit(model)
    minima = [
        float(item.valid_min_hz)
        for item in flattened
        if getattr(item, "valid_min_hz", None) is not None
    ]
    maxima = [
        float(item.valid_max_hz)
        for item in flattened
        if getattr(item, "valid_max_hz", None) is not None
    ]
    validity_known = bool(flattened) and all(
        getattr(item, "valid_min_hz", None) is not None
        and getattr(item, "valid_max_hz", None) is not None
        for item in flattened
    )
    return (
        max(minima) if minima else None,
        min(maxima) if maxima else None,
        validity_known,
    )
