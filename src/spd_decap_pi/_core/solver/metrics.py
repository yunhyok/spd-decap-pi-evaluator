"""Target, peak, phase, and model-confidence metrics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import ArrayLike, NDArray

from spd_decap_pi._core.models.impedance import frequency_array
from .modal import RectangularPlane, SolverDiagnostics


class MetricsError(ValueError):
    """Raised when a target or evaluation metric input is invalid."""


@dataclass(frozen=True, slots=True)
class TargetMask:
    frequencies_hz: NDArray[np.float64]
    impedance_ohm: NDArray[np.float64]

    def __post_init__(self) -> None:
        frequencies = frequency_array(self.frequencies_hz).copy()
        values = np.asarray(self.impedance_ohm, dtype=np.float64).copy()
        if values.shape != frequencies.shape:
            raise MetricsError("target impedance must match target frequencies")
        if not np.all(np.diff(frequencies) > 0.0):
            raise MetricsError("target frequencies must be strictly increasing")
        if not np.all(np.isfinite(values)) or np.any(values <= 0.0):
            raise MetricsError("target impedance values must be finite and > 0")
        frequencies.setflags(write=False)
        values.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "impedance_ohm", values)

    @classmethod
    def constant(
        cls, impedance_ohm: float, *, start_hz: float = 1e3, stop_hz: float = 1e9
    ) -> "TargetMask":
        if impedance_ohm <= 0.0 or not np.isfinite(impedance_ohm):
            raise MetricsError("constant target impedance must be finite and > 0")
        return cls(
            np.asarray([start_hz, stop_hz], dtype=np.float64),
            np.asarray([impedance_ohm, impedance_ohm], dtype=np.float64),
        )

    def values_at(self, frequencies_hz: ArrayLike) -> NDArray[np.float64]:
        frequencies = frequency_array(frequencies_hz)
        if frequencies.min() < self.frequencies_hz[0] or frequencies.max() > self.frequencies_hz[-1]:
            raise MetricsError(
                "target mask does not cover the complete evaluation frequency range"
            )
        return np.exp(
            np.interp(
                np.log(frequencies),
                np.log(self.frequencies_hz),
                np.log(self.impedance_ohm),
            )
        )


@dataclass(frozen=True, slots=True)
class Peak:
    peak_id: str
    index: int
    frequency_hz: float
    impedance_ohm: float
    prominence_db: float


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    magnitude_ohm: NDArray[np.float64]
    phase_deg: NDArray[np.float64]
    target_ohm: NDArray[np.float64]
    violation_db: NDArray[np.float64]
    max_violation_db: float
    rms_violation_db: float
    max_peak_prominence_db: float
    target_met: bool
    peaks: tuple[Peak, ...]


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


@dataclass(frozen=True, slots=True)
class ConfidenceAssessment:
    category: ConfidenceCategory
    start_hz: float
    stop_hz: float
    level: ConfidenceLevel
    reason: str


@dataclass(frozen=True, slots=True)
class ConfidenceInputs:
    geometry_confirmed: bool = False
    templates_calibrated: bool = False
    coupling_modeled: bool = False
    model_valid_min_hz: float | None = None
    model_valid_max_hz: float | None = None
    model_validity_known: bool = False
    modal_converged: bool | None = None
    cavity_cutoff_safety_factor: float = 0.1

    def __post_init__(self) -> None:
        if not 0.0 < self.cavity_cutoff_safety_factor <= 1.0:
            raise MetricsError("cavity_cutoff_safety_factor must be in (0, 1]")
        if self.model_valid_min_hz is not None and self.model_valid_min_hz <= 0.0:
            raise MetricsError("model_valid_min_hz must be > 0")
        if self.model_valid_max_hz is not None and self.model_valid_max_hz <= 0.0:
            raise MetricsError("model_valid_max_hz must be > 0")
        if self.model_validity_known and (
            self.model_valid_min_hz is None or self.model_valid_max_hz is None
        ):
            raise MetricsError(
                "known model validity requires both model_valid_min_hz and model_valid_max_hz"
            )


def compute_evaluation_metrics(
    frequencies_hz: ArrayLike,
    impedance_ohm: ArrayLike,
    target: TargetMask,
    *,
    critical_band_hz: tuple[float, float] = (1e5, 1e8),
    minimum_peak_prominence_db: float = 0.05,
) -> EvaluationMetrics:
    frequencies = frequency_array(frequencies_hz)
    impedance = np.asarray(impedance_ohm, dtype=np.complex128)
    if impedance.shape != frequencies.shape:
        raise MetricsError("impedance_ohm must match frequencies_hz")
    if not np.all(np.diff(frequencies) > 0.0):
        raise MetricsError("evaluation frequencies must be strictly increasing")
    if not np.all(np.isfinite(impedance.real)) or not np.all(np.isfinite(impedance.imag)):
        raise MetricsError("impedance values must be finite")
    critical_start, critical_stop = critical_band_hz
    if critical_start <= 0.0 or critical_stop <= critical_start:
        raise MetricsError("critical band must satisfy 0 < start < stop")

    target_values = target.values_at(frequencies)
    magnitude = np.abs(impedance)
    safe_magnitude = np.maximum(magnitude, np.finfo(float).tiny)
    ratio = safe_magnitude / target_values
    violation = np.where(
        ratio <= 1.0 + 1e-12,
        0.0,
        np.maximum(0.0, 20.0 * np.log10(ratio)),
    )
    critical = (frequencies >= critical_start) & (frequencies <= critical_stop)
    if not np.any(critical):
        raise MetricsError("the evaluation grid has no points inside the critical band")
    critical_frequencies = frequencies[critical]
    critical_violation = violation[critical]
    max_violation = float(np.max(critical_violation))
    if critical_frequencies.size == 1:
        rms_violation = float(critical_violation[0])
    else:
        log_frequency = np.log(critical_frequencies)
        span = float(log_frequency[-1] - log_frequency[0])
        rms_violation = float(
            np.sqrt(np.trapezoid(critical_violation**2, log_frequency) / span)
        )

    peaks = extract_local_peaks(
        frequencies,
        magnitude,
        band_hz=critical_band_hz,
        minimum_prominence_db=minimum_peak_prominence_db,
    )
    max_prominence = max((peak.prominence_db for peak in peaks), default=0.0)
    return EvaluationMetrics(
        magnitude_ohm=magnitude,
        phase_deg=np.angle(impedance, deg=True),
        target_ohm=target_values,
        violation_db=violation,
        max_violation_db=max_violation,
        rms_violation_db=rms_violation,
        max_peak_prominence_db=float(max_prominence),
        target_met=bool(max_violation <= 1e-12),
        peaks=peaks,
    )


def extract_local_peaks(
    frequencies_hz: ArrayLike,
    magnitude_ohm: ArrayLike,
    *,
    band_hz: tuple[float, float] | None = None,
    minimum_prominence_db: float = 0.05,
) -> tuple[Peak, ...]:
    """Extract local maxima with the smaller available one-sided drop.

    On each side, the base search ends at the boundary or the first point above
    the candidate peak.  Prominence is the smaller of the two one-sided drops;
    band-edge points are intentionally not classified as local peaks.
    """

    frequencies = frequency_array(frequencies_hz)
    magnitude = np.asarray(magnitude_ohm, dtype=np.float64)
    if magnitude.shape != frequencies.shape or np.any(magnitude < 0.0):
        raise MetricsError("magnitude_ohm must be non-negative and match frequency")
    if minimum_prominence_db < 0.0:
        raise MetricsError("minimum_prominence_db must be >= 0")
    db = 20.0 * np.log10(np.maximum(magnitude, np.finfo(float).tiny))
    if band_hz is None:
        selected_indices = np.arange(frequencies.size)
    else:
        selected_indices = np.flatnonzero(
            (frequencies >= band_hz[0]) & (frequencies <= band_hz[1])
        )
    if selected_indices.size < 3:
        return ()
    start = int(selected_indices[0])
    stop = int(selected_indices[-1])
    candidates: list[tuple[int, float]] = []
    for index in range(start + 1, stop):
        if db[index] > db[index - 1] and db[index] >= db[index + 1]:
            left_limit = start
            for cursor in range(index - 1, start - 1, -1):
                if db[cursor] > db[index]:
                    left_limit = cursor + 1
                    break
            right_limit = stop
            for cursor in range(index + 1, stop + 1):
                if db[cursor] > db[index]:
                    right_limit = cursor - 1
                    break
            left_drop = db[index] - float(np.min(db[left_limit : index + 1]))
            right_drop = db[index] - float(np.min(db[index : right_limit + 1]))
            prominence = max(0.0, min(left_drop, right_drop))
            if prominence >= minimum_prominence_db:
                candidates.append((index, prominence))
    return tuple(
        Peak(
            peak_id=f"PEAK_{order:03d}",
            index=index,
            frequency_hz=float(frequencies[index]),
            impedance_ohm=float(magnitude[index]),
            prominence_db=float(prominence),
        )
        for order, (index, prominence) in enumerate(candidates, start=1)
    )


def assess_confidence(
    frequencies_hz: ArrayLike,
    diagnostics: SolverDiagnostics,
    plane: RectangularPlane,
    inputs: ConfidenceInputs = ConfidenceInputs(),
) -> tuple[ConfidenceAssessment, ...]:
    frequencies = frequency_array(frequencies_hz)
    bands = (
        (float(frequencies[0]), min(1e5, float(frequencies[-1]))),
        (max(1e5, float(frequencies[0])), min(1e8, float(frequencies[-1]))),
        (max(1e8, float(frequencies[0])), float(frequencies[-1])),
    )
    bands = tuple((start, stop) for start, stop in bands if stop > start)

    if diagnostics.max_relative_residual <= 1e-10 and diagnostics.max_condition_number <= 1e12:
        numerical_level = ConfidenceLevel.HIGH
        numerical_reason = "factorized solve residual and matrix condition are within limits"
    elif diagnostics.max_relative_residual <= 1e-7 and diagnostics.max_condition_number <= 1e15:
        numerical_level = ConfidenceLevel.MEDIUM
        numerical_reason = "solver is usable but matrix conditioning or residual needs attention"
    else:
        numerical_level = ConfidenceLevel.LOW
        numerical_reason = "matrix conditioning or residual exceeds the numerical confidence limit"
    if inputs.modal_converged is False:
        numerical_level = ConfidenceLevel.LOW
        numerical_reason = "frequency-grid or modal convergence check failed"
    elif inputs.modal_converged is None and numerical_level == ConfidenceLevel.HIGH:
        numerical_level = ConfidenceLevel.MEDIUM
        numerical_reason = (
            "linear solve is stable but frequency-grid and modal convergence were not "
            "independently checked"
        )

    geometry_level = ConfidenceLevel.HIGH if inputs.geometry_confirmed else ConfidenceLevel.MEDIUM
    geometry_reason = (
        "plane partition and finite ports were user-confirmed"
        if inputs.geometry_confirmed
        else "geometry passed bounds checks but partition/ports were not user-confirmed"
    )
    template_level = ConfidenceLevel.HIGH if inputs.templates_calibrated else ConfidenceLevel.MEDIUM
    template_reason = (
        "via and local topology templates are calibrated"
        if inputs.templates_calibrated
        else "via/local topology templates are analytical or uncalibrated"
    )
    coupling_level = ConfidenceLevel.HIGH if inputs.coupling_modeled else ConfidenceLevel.LOW
    coupling_reason = (
        "requested coupling terms are included"
        if inputs.coupling_modeled
        else "inter-rail/site coupling is not modeled in the MVP evaluator"
    )

    assessments: list[ConfidenceAssessment] = []
    for start, stop in bands:
        assessments.extend(
            (
                ConfidenceAssessment(
                    ConfidenceCategory.NUMERICAL, start, stop, numerical_level, numerical_reason
                ),
                ConfidenceAssessment(
                    ConfidenceCategory.GEOMETRY, start, stop, geometry_level, geometry_reason
                ),
                ConfidenceAssessment(
                    ConfidenceCategory.TEMPLATE, start, stop, template_level, template_reason
                ),
                ConfidenceAssessment(
                    ConfidenceCategory.COUPLING, start, stop, coupling_level, coupling_reason
                ),
            )
        )
        coverage_limit = plane.vertical_cutoff_hz * inputs.cavity_cutoff_safety_factor
        valid_min = inputs.model_valid_min_hz or float(frequencies[0])
        valid_max = min(inputs.model_valid_max_hz or float("inf"), coverage_limit)
        inside_limits = start >= valid_min and stop <= valid_max
        overlaps_limits = stop >= valid_min and start <= valid_max
        if inside_limits and inputs.model_validity_known:
            coverage_level = ConfidenceLevel.HIGH
            coverage_reason = "band is inside characterized cavity and local-model validity limits"
        elif inside_limits:
            coverage_level = ConfidenceLevel.MEDIUM
            coverage_reason = (
                "band is inside the cavity limit, but one or more lumped local models "
                "have no characterized frequency range"
            )
        elif overlaps_limits:
            coverage_level = ConfidenceLevel.MEDIUM
            coverage_reason = "band partially exceeds a cavity or local-model validity limit"
        else:
            coverage_level = ConfidenceLevel.LOW
            coverage_reason = "band is outside a cavity or local-model validity limit"
        assessments.append(
            ConfidenceAssessment(
                ConfidenceCategory.MODEL_COVERAGE,
                start,
                stop,
                coverage_level,
                coverage_reason,
            )
        )
    return tuple(assessments)
