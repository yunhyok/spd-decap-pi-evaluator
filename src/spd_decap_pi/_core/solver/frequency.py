"""Deterministic logarithmic sweep construction and refinement."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from spd_decap_pi._core.models.impedance import ImpedanceModelError, frequency_array


DEFAULT_CURVATURE_THRESHOLD_DB = 0.75


@dataclass(frozen=True, slots=True)
class FrequencyGrid:
    """A validated, strictly increasing frequency sweep."""

    frequencies_hz: NDArray[np.float64]

    def __post_init__(self) -> None:
        values = frequency_array(self.frequencies_hz).copy()
        if values.size < 2 or not np.all(np.diff(values) > 0.0):
            raise ImpedanceModelError("a frequency grid needs at least two increasing points")
        values.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", values)

    @classmethod
    def logarithmic(
        cls,
        start_hz: float = 1e3,
        stop_hz: float = 1e9,
        *,
        points_per_decade: int = 20,
    ) -> "FrequencyGrid":
        if not np.isfinite(start_hz) or not np.isfinite(stop_hz):
            raise ImpedanceModelError("frequency limits must be finite")
        if start_hz <= 0.0 or stop_hz <= start_hz:
            raise ImpedanceModelError("frequency limits must satisfy 0 < start < stop")
        if points_per_decade < 1:
            raise ImpedanceModelError("points_per_decade must be >= 1")
        decades = np.log10(stop_hz / start_hz)
        raw_intervals = decades * points_per_decade
        rounded = round(raw_intervals)
        intervals = int(rounded if abs(raw_intervals - rounded) < 1e-12 else np.ceil(raw_intervals))
        return cls(np.geomspace(start_hz, stop_hz, intervals + 1, dtype=np.float64))

    def __len__(self) -> int:
        return int(self.frequencies_hz.size)


def refine_log_grid(
    frequencies_hz: ArrayLike,
    values: ArrayLike,
    *,
    curvature_threshold_db: float = DEFAULT_CURVATURE_THRESHOLD_DB,
    max_new_points: int = 64,
) -> FrequencyGrid:
    """Insert geometric midpoints around rapidly curving magnitude intervals.

    This helper is intentionally stateless: the evaluator can repeatedly solve,
    call it, and stop when the returned grid is unchanged or its point budget is
    exhausted.
    """

    frequencies = frequency_array(frequencies_hz)
    complex_values = np.asarray(values, dtype=np.complex128)
    if complex_values.shape != frequencies.shape:
        raise ImpedanceModelError("values must match frequencies_hz")
    if frequencies.size < 3 or not np.all(np.diff(frequencies) > 0.0):
        raise ImpedanceModelError("refinement requires at least three increasing frequencies")
    if curvature_threshold_db <= 0.0 or max_new_points < 0:
        raise ImpedanceModelError("refinement thresholds must be positive")
    if not np.all(np.isfinite(complex_values.real)) or not np.all(
        np.isfinite(complex_values.imag)
    ):
        raise ImpedanceModelError("values must be finite")

    magnitude_db = 20.0 * np.log10(np.maximum(np.abs(complex_values), np.finfo(float).tiny))
    log_frequency = np.log10(frequencies)
    slopes = np.diff(magnitude_db) / np.diff(log_frequency)
    curvature = np.abs(np.diff(slopes))
    candidates: set[int] = set()
    for center in np.flatnonzero(curvature >= curvature_threshold_db):
        candidates.add(int(center))
        candidates.add(int(center + 1))

    if not candidates or max_new_points == 0:
        return FrequencyGrid(frequencies.copy())
    ranked = sorted(
        candidates,
        key=lambda index: (
            -max(
                curvature[index - 1] if index > 0 else -np.inf,
                curvature[index] if index < curvature.size else -np.inf,
            ),
            index,
        ),
    )[:max_new_points]
    midpoints = [np.sqrt(frequencies[index] * frequencies[index + 1]) for index in ranked]
    refined = np.unique(np.concatenate((frequencies, np.asarray(midpoints, dtype=np.float64))))
    return FrequencyGrid(refined)
