"""Reusable deterministic impedance models.

The numerical solver works with the small :class:`ImpedanceModel` protocol so
domain/import code can provide either analytic or sampled models without a
dependency on the GUI or project schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
from numpy.typing import ArrayLike, NDArray


class ImpedanceModelError(ValueError):
    """Raised when an impedance model is invalid or used out of range."""


def frequency_array(frequencies_hz: ArrayLike) -> NDArray[np.float64]:
    """Return a validated, one-dimensional positive frequency array."""

    values = np.asarray(frequencies_hz, dtype=np.float64)
    if values.ndim != 1 or values.size == 0:
        raise ImpedanceModelError("frequencies_hz must be a non-empty 1-D array")
    if not np.all(np.isfinite(values)) or np.any(values <= 0.0):
        raise ImpedanceModelError("frequencies_hz must contain finite values > 0")
    return values


@runtime_checkable
class ImpedanceModel(Protocol):
    """A frequency-dependent two-terminal impedance."""

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        """Return complex impedance in ohms for each requested frequency."""


@dataclass(frozen=True, slots=True)
class SeriesRLCModel:
    """A passive series R-L-C capacitor model."""

    model_id: str
    capacitance_f: float
    esr_ohm: float = 0.0
    esl_h: float = 0.0

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ImpedanceModelError("model_id must not be empty")
        if not np.isfinite(self.capacitance_f) or self.capacitance_f <= 0.0:
            raise ImpedanceModelError("capacitance_f must be finite and > 0")
        if not np.isfinite(self.esr_ohm) or self.esr_ohm < 0.0:
            raise ImpedanceModelError("esr_ohm must be finite and >= 0")
        if not np.isfinite(self.esl_h) or self.esl_h < 0.0:
            raise ImpedanceModelError("esl_h must be finite and >= 0")

    @property
    def self_resonant_frequency_hz(self) -> float:
        if self.esl_h == 0.0:
            return np.inf
        return float(1.0 / (2.0 * np.pi * np.sqrt(self.esl_h * self.capacitance_f)))

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        omega = 2.0 * np.pi * frequencies
        return np.asarray(
            self.esr_ohm
            + 1j * omega * self.esl_h
            + 1.0 / (1j * omega * self.capacitance_f),
            dtype=np.complex128,
        )


@dataclass(frozen=True, slots=True)
class SeriesRLModel:
    """A passive series R-L path, typically a calibrated via loop."""

    model_id: str
    resistance_ohm: float = 0.0
    inductance_h: float = 0.0
    valid_min_hz: float | None = None
    valid_max_hz: float | None = None

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ImpedanceModelError("model_id must not be empty")
        if not np.isfinite(self.resistance_ohm) or self.resistance_ohm < 0.0:
            raise ImpedanceModelError("resistance_ohm must be finite and >= 0")
        if not np.isfinite(self.inductance_h) or self.inductance_h < 0.0:
            raise ImpedanceModelError("inductance_h must be finite and >= 0")
        if self.resistance_ohm == 0.0 and self.inductance_h == 0.0:
            raise ImpedanceModelError("an R-L path cannot have both R and L equal to zero")
        if self.valid_min_hz is not None and self.valid_min_hz <= 0.0:
            raise ImpedanceModelError("valid_min_hz must be > 0")
        if self.valid_max_hz is not None and self.valid_max_hz <= 0.0:
            raise ImpedanceModelError("valid_max_hz must be > 0")
        if (
            self.valid_min_hz is not None
            and self.valid_max_hz is not None
            and self.valid_min_hz >= self.valid_max_hz
        ):
            raise ImpedanceModelError("valid_min_hz must be less than valid_max_hz")

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        return np.asarray(
            self.resistance_ohm + 1j * 2.0 * np.pi * frequencies * self.inductance_h,
            dtype=np.complex128,
        )


@dataclass(frozen=True, slots=True)
class ConstantImpedanceModel:
    """A constant non-zero impedance, useful for calibrated short paths."""

    model_id: str
    impedance_ohm: complex

    def __post_init__(self) -> None:
        value = complex(self.impedance_ohm)
        if not self.model_id.strip():
            raise ImpedanceModelError("model_id must not be empty")
        if not np.isfinite(value.real) or not np.isfinite(value.imag):
            raise ImpedanceModelError("impedance_ohm must be finite")
        if value == 0.0:
            raise ImpedanceModelError("impedance_ohm must be non-zero")
        if value.real < 0.0:
            raise ImpedanceModelError("a passive constant impedance cannot have negative resistance")

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        return np.full(frequencies.shape, complex(self.impedance_ohm), dtype=np.complex128)


@dataclass(frozen=True, slots=True)
class SampledImpedanceModel:
    """Complex impedance sampled on a strictly increasing frequency grid.

    Real and imaginary parts are interpolated on log-frequency. Extrapolation
    is rejected by default so an imported model cannot silently be used beyond
    its characterized range.
    """

    model_id: str
    frequencies_hz: NDArray[np.float64]
    values_ohm: NDArray[np.complex128]
    allow_extrapolation: bool = False

    def __post_init__(self) -> None:
        frequencies = frequency_array(self.frequencies_hz).copy()
        values = np.asarray(self.values_ohm, dtype=np.complex128).copy()
        if not self.model_id.strip():
            raise ImpedanceModelError("model_id must not be empty")
        if values.ndim != 1 or values.shape != frequencies.shape:
            raise ImpedanceModelError("values_ohm must match frequencies_hz")
        if not np.all(np.diff(frequencies) > 0.0):
            raise ImpedanceModelError("sample frequencies must be strictly increasing")
        if not np.all(np.isfinite(values.real)) or not np.all(np.isfinite(values.imag)):
            raise ImpedanceModelError("sampled impedance values must be finite")
        if np.any(values.real < -1e-12):
            raise ImpedanceModelError("sampled passive impedance cannot have negative resistance")
        frequencies.setflags(write=False)
        values.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "values_ohm", values)

    @property
    def valid_min_hz(self) -> float:
        return float(self.frequencies_hz[0])

    @property
    def valid_max_hz(self) -> float:
        return float(self.frequencies_hz[-1])

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        if not self.allow_extrapolation and (
            frequencies.min() < self.frequencies_hz[0]
            or frequencies.max() > self.frequencies_hz[-1]
        ):
            raise ImpedanceModelError(
                f"{self.model_id!r} is characterized only from "
                f"{self.frequencies_hz[0]:g} Hz to {self.frequencies_hz[-1]:g} Hz"
            )
        source_x = np.log(self.frequencies_hz)
        target_x = np.log(frequencies)
        real = np.interp(target_x, source_x, self.values_ohm.real)
        imag = np.interp(target_x, source_x, self.values_ohm.imag)
        return np.asarray(real + 1j * imag, dtype=np.complex128)


@dataclass(frozen=True, slots=True)
class SeriesCombinationModel:
    """A deterministic series combination of two-terminal models."""

    model_id: str
    parts: tuple[ImpedanceModel, ...]

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ImpedanceModelError("model_id must not be empty")
        if not self.parts:
            raise ImpedanceModelError("a series combination requires at least one part")

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        result = np.zeros(frequencies.shape, dtype=np.complex128)
        for part in self.parts:
            result += np.asarray(part.impedance(frequencies), dtype=np.complex128)
        return result


@dataclass(frozen=True, slots=True)
class ScaledImpedanceModel:
    """A scalar multiple of another model (for split differential paths)."""

    model_id: str
    source: ImpedanceModel
    scale: float

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ImpedanceModelError("model_id must not be empty")
        if not np.isfinite(self.scale) or self.scale <= 0.0:
            raise ImpedanceModelError("scale must be finite and > 0")

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        return np.asarray(self.scale * self.source.impedance(frequencies), dtype=np.complex128)
