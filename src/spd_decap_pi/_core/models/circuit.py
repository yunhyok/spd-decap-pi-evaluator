"""Local decoupling connection networks and Kron reduction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .impedance import ImpedanceModel, ImpedanceModelError, frequency_array


class CircuitModelError(ImpedanceModelError):
    """Raised when a local topology cannot be reduced safely."""


@runtime_checkable
class MultiportAdmittanceModel(Protocol):
    """A frequency-dependent, complex-symmetric multiport admittance."""

    @property
    def port_count(self) -> int:
        """Number of retained differential plane ports."""

    def admittance_matrix(
        self, frequencies_hz: ArrayLike
    ) -> NDArray[np.complex128]:
        """Return shape ``(frequency, port, port)`` in siemens."""


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


@dataclass(frozen=True, slots=True)
class SharedPadClusterModel:
    """Explicit terminal Vias joined by PWR-component and shared-GND buses.

    The source project currently calibrates one *differential* PWR/GND
    via-loop impedance rather than separate terminal impedances.  Each
    physical PWR or GND Via therefore receives a symmetric ``Z_loop / 2``
    branch.  This makes a colocated 1-PWR/1-GND cluster reproduce the calibrated
    loop exactly while preserving unequal PWR/GND counts without pairing or
    duplicating physical Via evidence.

    Each post-edit same-rail PWR component owns one ideal top-PWR supernode.
    The original source cluster retains one continuous ideal top-GND supernode,
    with every physical GND Via represented once.  Capacitors bridge their PWR
    component to that common GND node.  ``power_component_indices`` and
    ``capacitor_component_indices`` map flattened paths/caps to those PWR
    nodes; omitted maps mean the legacy one-PWR-component case.

    All internal nodes are Kron-reduced.  Finally the raw terminal matrix is
    transformed with ``+1/2`` for PWR and ``-1/2`` for GND; this is the
    symmetric plane-pair incidence used by the differential modal solver.
    Ordinary transposes are intentional because the passive network and solver
    use complex-symmetric MNA equations.
    """

    model_id: str
    power_via_loops: tuple[ImpedanceModel, ...]
    ground_via_loops: tuple[ImpedanceModel, ...]
    capacitors: tuple[ImpedanceModel, ...] = ()
    power_component_indices: tuple[int, ...] = ()
    capacitor_component_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise CircuitModelError("model_id must not be empty")
        if not self.power_via_loops or not self.ground_via_loops:
            raise CircuitModelError(
                "a shared-pad cluster model requires PWR and GND via paths"
            )
        power_indices = self.power_component_indices or tuple(
            0 for _item in self.power_via_loops
        )
        if len(power_indices) != len(self.power_via_loops) or any(
            index < 0 for index in power_indices
        ):
            raise CircuitModelError(
                "shared-pad PWR path component mapping is invalid"
            )
        component_ids = sorted(set(power_indices))
        if component_ids != list(range(len(component_ids))):
            raise CircuitModelError(
                "shared-pad PWR component indices must be contiguous from zero"
            )
        capacitor_indices = self.capacitor_component_indices or tuple(
            0 for _item in self.capacitors
        )
        if len(capacitor_indices) != len(self.capacitors) or any(
            index < 0 or index >= len(component_ids)
            for index in capacitor_indices
        ):
            raise CircuitModelError(
                "shared-pad capacitor component mapping is invalid"
            )
        object.__setattr__(self, "power_component_indices", power_indices)
        object.__setattr__(
            self, "capacitor_component_indices", capacitor_indices
        )

    @property
    def port_count(self) -> int:
        return len(self.power_via_loops) + len(self.ground_via_loops)

    @property
    def power_component_count(self) -> int:
        return max(self.power_component_indices) + 1

    def homogeneous_one_component_via_model(self) -> ImpedanceModel | None:
        """Return the one exact terminal Via model for the batch fast path.

        The modal solver can avoid materializing this cluster's dense terminal
        admittance only for the special arrowhead topology with one PWR
        supernode and an *identical object* on every PWR and GND terminal.
        Identity, rather than impedance equality, is intentional: it keeps the
        optimization a purely algebraic regrouping of the existing model and
        prevents an accidental approximation of distinct calibrated paths.
        """

        if self.power_component_count != 1:
            return None
        candidate = self.power_via_loops[0]
        if any(model is not candidate for model in self.power_via_loops):
            return None
        if any(model is not candidate for model in self.ground_via_loops):
            return None
        return candidate

    def admittance_matrix(
        self, frequencies_hz: ArrayLike
    ) -> NDArray[np.complex128]:
        frequencies = frequency_array(frequencies_hz)
        power_admittance = np.column_stack(
            tuple(
                2.0 / _checked_impedance(model, frequencies)
                for model in self.power_via_loops
            )
        )
        ground_admittance = np.column_stack(
            tuple(
                2.0 / _checked_impedance(model, frequencies)
                for model in self.ground_via_loops
            )
        )
        component_count = self.power_component_count
        cap_admittance = np.zeros(
            (frequencies.size, component_count), dtype=np.complex128
        )
        for capacitor, component_index in zip(
            self.capacitors,
            self.capacitor_component_indices,
            strict=True,
        ):
            cap_admittance[:, component_index] += 1.0 / _checked_impedance(
                capacitor, frequencies
            )

        power_sum = np.zeros_like(cap_admittance)
        for path_index, component_index in enumerate(self.power_component_indices):
            power_sum[:, component_index] += power_admittance[:, path_index]
        ground_sum = np.sum(ground_admittance, axis=1)
        power_node = power_sum + cap_admittance
        power_scale = np.abs(power_sum) + np.abs(cap_admittance)
        singular_power = np.abs(power_node) <= (
            64.0
            * np.finfo(np.float64).eps
            * np.maximum(power_scale, np.finfo(float).tiny)
        )
        if np.any(singular_power):
            frequency_index = int(np.argwhere(singular_power)[0, 0])
            raise CircuitModelError(
                "shared-pad PWR-supernode admittance is singular at "
                f"{float(frequencies[frequency_index]):g} Hz"
            )

        # Arrowhead Schur complement for one shared GND node.  Writing
        # c-c**2/(P+c) as c*P/(P+c) avoids large-c cancellation.
        component_transfer = cap_admittance / power_node
        ground_schur_terms = cap_admittance * power_sum / power_node
        ground_schur = ground_sum + np.sum(ground_schur_terms, axis=1)
        ground_scale = np.abs(ground_sum) + np.sum(
            np.abs(ground_schur_terms), axis=1
        )
        singular_ground = np.abs(ground_schur) <= (
            64.0
            * np.finfo(np.float64).eps
            * np.maximum(ground_scale, np.finfo(float).tiny)
        )
        if np.any(singular_ground):
            frequency = float(
                frequencies[int(np.flatnonzero(singular_ground)[0])]
            )
            raise CircuitModelError(
                "shared-pad common-GND admittance is singular at "
                f"{frequency:g} Hz"
            )

        power_count = len(self.power_via_loops)
        ground_count = len(self.ground_via_loops)
        count = power_count + ground_count
        output = np.zeros(
            (frequencies.size, count, count), dtype=np.complex128
        )
        power_block = np.zeros(
            (frequencies.size, power_count, power_count), dtype=np.complex128
        )
        power_diagonal = np.arange(power_count)
        power_block[:, power_diagonal, power_diagonal] = power_admittance
        component_index = np.asarray(
            self.power_component_indices, dtype=np.int64
        )
        inverse_power = 1.0 / power_node
        inverse_ground = 1.0 / ground_schur
        mapped_inverse = inverse_power[:, component_index]
        mapped_transfer = component_transfer[:, component_index]
        same_component = (
            component_index[:, None] == component_index[None, :]
        ).astype(np.float64)
        internal_power_inverse = (
            same_component[None, :, :]
            * mapped_inverse[:, :, None]
            + mapped_transfer[:, :, None]
            * mapped_transfer[:, None, :]
            * inverse_ground[:, None, None]
        )
        power_block -= (
            power_admittance[:, :, None]
            * power_admittance[:, None, :]
            * internal_power_inverse
        )
        ground_block = np.zeros(
            (frequencies.size, ground_count, ground_count), dtype=np.complex128
        )
        ground_diagonal = np.arange(ground_count)
        ground_block[:, ground_diagonal, ground_diagonal] = ground_admittance
        ground_block -= (
            ground_admittance[:, :, None]
            * ground_admittance[:, None, :]
            * inverse_ground[:, None, None]
        )
        cross_block = -(
            power_admittance[:, :, None]
            * ground_admittance[:, None, :]
            * (mapped_transfer * inverse_ground[:, None])[:, :, None]
        )
        # Symmetric plane-pair terminal transform D=diag(+1/2 PWR, -1/2 GND).
        output[:, :power_count, :power_count] = 0.25 * power_block
        output[:, power_count:, power_count:] = 0.25 * ground_block
        output[:, :power_count, power_count:] = -0.25 * cross_block
        output[:, power_count:, :power_count] = -0.25 * np.swapaxes(
            cross_block, 1, 2
        )
        if not np.all(np.isfinite(output.real)) or not np.all(
            np.isfinite(output.imag)
        ):
            raise CircuitModelError(
                "shared-pad reduction produced non-finite admittance"
            )
        return output
