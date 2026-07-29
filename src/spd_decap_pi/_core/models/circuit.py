"""Local decoupling connection networks and Kron reduction."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .impedance import ImpedanceModel, ImpedanceModelError, frequency_array


class CircuitModelError(ImpedanceModelError):
    """Raised when a local topology cannot be reduced safely."""


def _checked_impedance(
    model: ImpedanceModel, frequencies_hz: NDArray[np.float64]
) -> NDArray[np.complex128]:
    values = np.asarray(model.impedance(frequencies_hz), dtype=np.complex128)
    if values.shape != frequencies_hz.shape:
        raise CircuitModelError("impedance model returned a shape that does not match frequency")
    if not np.all(np.isfinite(values.real)) or not np.all(np.isfinite(values.imag)):
        raise CircuitModelError("impedance model returned non-finite values")
    if np.any(np.abs(values) < np.finfo(np.float64).tiny):
        raise CircuitModelError("zero impedance cannot be converted to a finite shunt admittance")
    return values


def kron_reduce_admittance(
    admittance: ArrayLike,
    retained_indices: tuple[int, ...] | list[int],
) -> NDArray[np.complex128]:
    """Kron-reduce a nodal admittance matrix without forming an inverse."""

    matrix = np.asarray(admittance, dtype=np.complex128)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or matrix.size == 0:
        raise CircuitModelError("admittance must be a non-empty square matrix")
    retained = tuple(int(index) for index in retained_indices)
    if not retained or len(set(retained)) != len(retained):
        raise CircuitModelError("retained_indices must be unique and non-empty")
    if min(retained) < 0 or max(retained) >= matrix.shape[0]:
        raise CircuitModelError("retained index is outside the admittance matrix")
    internal = tuple(index for index in range(matrix.shape[0]) if index not in retained)
    retained_array = np.asarray(retained, dtype=np.int64)
    if not internal:
        return matrix[np.ix_(retained_array, retained_array)].copy()
    internal_array = np.asarray(internal, dtype=np.int64)
    y_rr = matrix[np.ix_(retained_array, retained_array)]
    y_ri = matrix[np.ix_(retained_array, internal_array)]
    y_ir = matrix[np.ix_(internal_array, retained_array)]
    y_ii = matrix[np.ix_(internal_array, internal_array)]
    try:
        transferred = np.linalg.solve(y_ii, y_ir)
    except np.linalg.LinAlgError as exc:
        raise CircuitModelError("internal admittance block is singular during Kron reduction") from exc
    return np.asarray(y_rr - y_ri @ transferred, dtype=np.complex128)


@dataclass(frozen=True, slots=True)
class DirectBranchModel:
    """Capacitor and differential via loop connected in series."""

    model_id: str
    capacitor: ImpedanceModel
    via_loop: ImpedanceModel

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise CircuitModelError("model_id must not be empty")

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        return _checked_impedance(self.capacitor, frequencies) + _checked_impedance(
            self.via_loop, frequencies
        )

    def admittance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        return 1.0 / self.impedance(frequencies)


@dataclass(frozen=True, slots=True)
class SharedPairModel:
    """Anchor and satellite decaps sharing one differential via path.

    The plane port is node 0 and the common top-pad node is node 1.  The shared
    via connects those nodes.  The anchor capacitor and the satellite's
    P-horizontal/cap/G-horizontal series path shunt node 1.  Node 1 is then
    Kron-reduced, preserving the shared common-path impedance.
    """

    model_id: str
    shared_via_loop: ImpedanceModel
    anchor_capacitor: ImpedanceModel
    satellite_capacitor: ImpedanceModel
    horizontal_power_path: ImpedanceModel
    horizontal_ground_path: ImpedanceModel

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise CircuitModelError("model_id must not be empty")

    def admittance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        z_shared = _checked_impedance(self.shared_via_loop, frequencies)
        z_anchor = _checked_impedance(self.anchor_capacitor, frequencies)
        z_satellite = (
            _checked_impedance(self.horizontal_power_path, frequencies)
            + _checked_impedance(self.satellite_capacitor, frequencies)
            + _checked_impedance(self.horizontal_ground_path, frequencies)
        )
        output = np.empty(frequencies.shape, dtype=np.complex128)
        for index in range(frequencies.size):
            y_shared = 1.0 / z_shared[index]
            y_local = 1.0 / z_anchor[index] + 1.0 / z_satellite[index]
            full = np.asarray(
                [
                    [y_shared, -y_shared],
                    [-y_shared, y_shared + y_local],
                ],
                dtype=np.complex128,
            )
            output[index] = kron_reduce_admittance(full, (0,))[0, 0]
        return output

    def impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        admittance = self.admittance(frequencies_hz)
        if np.any(np.abs(admittance) < np.finfo(np.float64).tiny):
            raise CircuitModelError("shared-pair reduction produced zero admittance")
        return 1.0 / admittance
