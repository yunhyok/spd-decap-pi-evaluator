"""Exact, sampled multiport admittance cascades for adjacent board layers.

This module deliberately contains only network algebra.  It does not infer a
network from SPD geometry, does not split existing scalar impedance templates,
and is not part of the evaluation solver yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from numpy.typing import ArrayLike, NDArray


class LayerPairNetworkError(ValueError):
    """Raised when a layer-pair network cannot be represented or cascaded safely."""


_RECIPROCITY_RTOL = 1.0e-11
_RECIPROCITY_ATOL = 1.0e-13
_MAX_RELATIVE_FORWARD_ERROR_BOUND = 1.0e-8
_MAX_SOLVE_RESIDUAL = 1.0e-10


def _readonly_array(values: ArrayLike, dtype: np.dtype[np.generic]) -> NDArray[np.generic]:
    """Copy ``values`` to immutable bytes so callers cannot reopen it for writes."""

    validated = np.array(values, dtype=dtype, copy=True, order="C")
    # A writeable ndarray's WRITEABLE flag can be reopened by its owner.  A
    # bytes-backed view cannot: NumPy rejects setflags(write=True), retaining
    # frozen dataclass semantics even when a caller keeps the original array.
    array = np.frombuffer(validated.tobytes(order="C"), dtype=dtype).reshape(
        validated.shape
    )
    return array


def _port_ids(values: Sequence[str], *, side: str) -> tuple[str, ...]:
    """Validate the ordered interface IDs for one side of a layer pair."""

    ids = tuple(values)
    if not ids:
        raise LayerPairNetworkError(f"{side}_port_ids must not be empty")
    if any(not isinstance(port_id, str) or not port_id.strip() for port_id in ids):
        raise LayerPairNetworkError(
            f"{side}_port_ids must contain only non-empty string IDs"
        )
    if len(set(ids)) != len(ids):
        raise LayerPairNetworkError(f"{side}_port_ids must be unique")
    return ids


@dataclass(frozen=True, slots=True)
class SampledPassivityDiagnostic:
    """Conservative passivity evidence evaluated only on the supplied samples.

    ``sampled_positive_semidefinite`` says whether the Hermitian conductance
    matrix is positive semidefinite to the reported numerical tolerance at all
    supplied frequencies.  The ``worst_frequency_*`` values deliberately come
    from the same sample: the one with the smallest eigenvalue-plus-tolerance
    margin.  This is *not* a proof of continuous-frequency passivity,
    causality, or physical realizability between samples.
    """

    sampled_positive_semidefinite: bool
    worst_frequency_index: int
    worst_frequency_hz: float
    worst_frequency_minimum_conductance_eigenvalue_siemens: float
    worst_frequency_tolerance_siemens: float
    worst_frequency_margin_siemens: float
    evaluated_frequency_count: int


@dataclass(frozen=True, slots=True)
class LayerPairNetwork:
    """A sampled reciprocal multiport between two adjacent layer interfaces.

    Currents are defined positive *into* the network at both interfaces.  The
    admittance ordering is ``top_port_ids + bottom_port_ids`` and has shape
    ``(frequency_count, port_count, port_count)``.  IDs are ordered and are
    intentionally preserved by :func:`cascade_layer_pair_networks`.
    """

    frequencies_hz: ArrayLike
    top_port_ids: Sequence[str]
    bottom_port_ids: Sequence[str]
    admittance_siemens: ArrayLike

    def __post_init__(self) -> None:
        frequencies = _readonly_array(self.frequencies_hz, np.dtype(np.float64))
        if frequencies.ndim != 1 or frequencies.size == 0:
            raise LayerPairNetworkError("frequencies_hz must be a non-empty one-dimensional array")
        if not np.all(np.isfinite(frequencies)) or np.any(frequencies <= 0.0):
            raise LayerPairNetworkError("frequencies_hz must be finite and strictly positive")
        if np.any(np.diff(frequencies) <= 0.0):
            raise LayerPairNetworkError(
                "frequencies_hz must be strictly increasing and unique"
            )

        top_ids = _port_ids(self.top_port_ids, side="top")
        bottom_ids = _port_ids(self.bottom_port_ids, side="bottom")
        overlap = set(top_ids).intersection(bottom_ids)
        if overlap:
            raise LayerPairNetworkError(
                "top_port_ids and bottom_port_ids must be disjoint; "
                f"duplicate ID {sorted(overlap)[0]!r}"
            )

        port_count = len(top_ids) + len(bottom_ids)
        admittance = _readonly_array(self.admittance_siemens, np.dtype(np.complex128))
        if admittance.shape != (frequencies.size, port_count, port_count):
            raise LayerPairNetworkError(
                "admittance_siemens must have shape "
                "(len(frequencies_hz), top_port_count + bottom_port_count, "
                "top_port_count + bottom_port_count)"
            )
        if not np.all(np.isfinite(admittance.real)) or not np.all(np.isfinite(admittance.imag)):
            raise LayerPairNetworkError("admittance_siemens must be finite")
        if not np.allclose(
            admittance,
            np.swapaxes(admittance, 1, 2),
            rtol=_RECIPROCITY_RTOL,
            atol=_RECIPROCITY_ATOL,
        ):
            raise LayerPairNetworkError(
                "admittance_siemens must be reciprocal under ordinary transpose"
            )

        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "top_port_ids", top_ids)
        object.__setattr__(self, "bottom_port_ids", bottom_ids)
        object.__setattr__(self, "admittance_siemens", admittance)

    @property
    def port_count(self) -> int:
        """Total number of retained interface ports."""

        return len(self.top_port_ids) + len(self.bottom_port_ids)

    @property
    def top_port_count(self) -> int:
        """Number of ports at the upper interface."""

        return len(self.top_port_ids)

    @property
    def bottom_port_count(self) -> int:
        """Number of ports at the lower interface."""

        return len(self.bottom_port_ids)

    def __reduce__(
        self,
    ) -> tuple[object, tuple[ArrayLike, Sequence[str], Sequence[str], ArrayLike]]:
        """Reconstruct through validation so pickle cannot reopen array storage."""

        return (
            type(self),
            (
                np.array(self.frequencies_hz, copy=True),
                self.top_port_ids,
                self.bottom_port_ids,
                np.array(self.admittance_siemens, copy=True),
            ),
        )

    def sampled_passivity_diagnostic(
        self,
        *,
        relative_tolerance: float = 1.0e-12,
        absolute_tolerance_siemens: float = 0.0,
    ) -> SampledPassivityDiagnostic:
        """Return sampled PSD evidence for the Hermitian conductance matrix.

        This deliberately reports limited numerical evidence, rather than
        labelling a finite list of samples as a proven passive network.
        """

        if not np.isfinite(relative_tolerance) or relative_tolerance < 0.0:
            raise LayerPairNetworkError(
                "relative_tolerance must be finite and non-negative"
            )
        if (
            not np.isfinite(absolute_tolerance_siemens)
            or absolute_tolerance_siemens < 0.0
        ):
            raise LayerPairNetworkError(
                "absolute_tolerance_siemens must be finite and non-negative"
            )
        hermitian_conductance = 0.5 * (
            self.admittance_siemens + np.swapaxes(self.admittance_siemens.conj(), 1, 2)
        )
        eigenvalues = np.linalg.eigvalsh(hermitian_conductance)
        minimum_per_frequency = np.min(eigenvalues, axis=1)
        matrix_norm_per_frequency = np.linalg.norm(
            hermitian_conductance, ord=np.inf, axis=(1, 2)
        )
        tolerance_per_frequency = (
            absolute_tolerance_siemens
            + relative_tolerance * matrix_norm_per_frequency
        )
        passivity_margin = minimum_per_frequency + tolerance_per_frequency
        worst_index = int(np.argmin(passivity_margin))
        return SampledPassivityDiagnostic(
            sampled_positive_semidefinite=bool(
                np.all(minimum_per_frequency >= -tolerance_per_frequency)
            ),
            worst_frequency_index=worst_index,
            worst_frequency_hz=float(self.frequencies_hz[worst_index]),
            worst_frequency_minimum_conductance_eigenvalue_siemens=float(
                minimum_per_frequency[worst_index]
            ),
            worst_frequency_tolerance_siemens=float(
                tolerance_per_frequency[worst_index]
            ),
            worst_frequency_margin_siemens=float(passivity_margin[worst_index]),
            evaluated_frequency_count=int(self.frequencies_hz.size),
        )


def _require_same_grid(first: LayerPairNetwork, second: LayerPairNetwork) -> None:
    if not np.array_equal(first.frequencies_hz, second.frequencies_hz):
        raise LayerPairNetworkError(
            "layer-pair networks must use exactly the same frequency grid"
        )


def _solve_interface(
    matrix: NDArray[np.complex128],
    right_hand_side: NDArray[np.complex128],
    *,
    frequency_hz: float,
) -> NDArray[np.complex128]:
    """Solve one interface block while rejecting singular or unstable systems."""

    try:
        condition = float(np.linalg.cond(matrix))
    except np.linalg.LinAlgError as exc:
        raise LayerPairNetworkError(
            f"interface admittance is singular at {frequency_hz:g} Hz"
        ) from exc
    forward_error_bound = condition * np.finfo(np.float64).eps
    if (
        not np.isfinite(condition)
        or forward_error_bound > _MAX_RELATIVE_FORWARD_ERROR_BOUND
    ):
        raise LayerPairNetworkError(
            "interface admittance is numerically unstable at "
            f"{frequency_hz:g} Hz (condition {condition:.3g}, "
            f"condition*epsilon {forward_error_bound:.3g} exceeds "
            f"{_MAX_RELATIVE_FORWARD_ERROR_BOUND:g} forward-error budget)"
        )
    try:
        solution = np.linalg.solve(matrix, right_hand_side)
    except np.linalg.LinAlgError as exc:
        raise LayerPairNetworkError(
            f"interface admittance is singular at {frequency_hz:g} Hz"
        ) from exc
    residual_norm = np.linalg.norm(matrix @ solution - right_hand_side, ord=np.inf, axis=0)
    solution_norm = np.linalg.norm(solution, ord=np.inf, axis=0)
    right_hand_side_norm = np.linalg.norm(right_hand_side, ord=np.inf, axis=0)
    scale_per_column = np.maximum(
        float(np.linalg.norm(matrix, ord=np.inf)) * solution_norm
        + right_hand_side_norm,
        np.finfo(np.float64).tiny,
    )
    if np.any(residual_norm / scale_per_column > _MAX_SOLVE_RESIDUAL):
        raise LayerPairNetworkError(
            f"interface admittance solve is numerically unstable at {frequency_hz:g} Hz"
        )
    return np.asarray(solution, dtype=np.complex128)


def cascade_layer_pair_networks(
    first: LayerPairNetwork, second: LayerPairNetwork
) -> LayerPairNetwork:
    """Exactly eliminate the common interface between two adjacent networks.

    ``first.bottom_port_ids`` and ``second.top_port_ids`` must describe the
    same ID set.  Their order may differ.  Interface voltages are continuous
    and interface currents obey outward-current KCL.  The implementation uses
    a solve of ``Q = Y1_bb + Y2_tt`` for every sampled frequency; it never
    forms ``Q``'s inverse.
    """

    if not isinstance(first, LayerPairNetwork) or not isinstance(second, LayerPairNetwork):
        raise LayerPairNetworkError("both cascade operands must be LayerPairNetwork instances")
    _require_same_grid(first, second)
    first_interface = set(first.bottom_port_ids)
    second_interface = set(second.top_port_ids)
    if first_interface != second_interface:
        raise LayerPairNetworkError(
            "adjacent interface port IDs must match exactly before cascade"
        )
    boundary_overlap = set(first.top_port_ids).intersection(second.bottom_port_ids)
    if boundary_overlap:
        raise LayerPairNetworkError(
            "cascade boundary port IDs must be disjoint; "
            f"duplicate ID {sorted(boundary_overlap)[0]!r}"
        )

    first_top_count = first.top_port_count
    interface_count = first.bottom_port_count
    second_bottom_count = second.bottom_port_count
    second_order = np.asarray(
        [second.top_port_ids.index(port_id) for port_id in first.bottom_port_ids],
        dtype=np.intp,
    )
    first_matrix = first.admittance_siemens
    second_matrix = second.admittance_siemens
    output = np.empty(
        (
            first.frequencies_hz.size,
            first_top_count + second_bottom_count,
            first_top_count + second_bottom_count,
        ),
        dtype=np.complex128,
    )

    for frequency_index, frequency in enumerate(first.frequencies_hz):
        y1 = first_matrix[frequency_index]
        y2 = second_matrix[frequency_index]
        y1_aa = y1[:first_top_count, :first_top_count]
        y1_ab = y1[:first_top_count, first_top_count:]
        y1_ba = y1[first_top_count:, :first_top_count]
        y1_bb = y1[first_top_count:, first_top_count:]

        y2_tt = y2[:interface_count, :interface_count][np.ix_(second_order, second_order)]
        y2_tb = y2[:interface_count, interface_count:][second_order, :]
        y2_bt = y2[interface_count:, :interface_count][:, second_order]
        y2_bb = y2[interface_count:, interface_count:]
        interface = y1_bb + y2_tt
        solved = _solve_interface(
            interface,
            np.concatenate((y1_ba, y2_tb), axis=1),
            frequency_hz=float(frequency),
        )
        transfer_left = solved[:, :first_top_count]
        transfer_right = solved[:, first_top_count:]
        y_aa = y1_aa - y1_ab @ transfer_left
        y_ab = -y1_ab @ transfer_right
        y_ba = -y2_bt @ transfer_left
        y_bb = y2_bb - y2_bt @ transfer_right
        reduced = np.block([[y_aa, y_ab], [y_ba, y_bb]])
        if not np.allclose(
            reduced,
            reduced.T,
            rtol=_RECIPROCITY_RTOL,
            atol=_RECIPROCITY_ATOL,
        ):
            raise LayerPairNetworkError(
                "cascade produced non-reciprocal admittance at "
                f"{float(frequency):g} Hz"
            )
        # Remove only sub-tolerance floating-point solve asymmetry after it is
        # checked; never use this average to conceal a non-reciprocal result.
        output[frequency_index] = 0.5 * (reduced + reduced.T)

    if not np.all(np.isfinite(output.real)) or not np.all(np.isfinite(output.imag)):
        raise LayerPairNetworkError("cascade produced non-finite admittance")
    return LayerPairNetwork(
        frequencies_hz=first.frequencies_hz,
        top_port_ids=first.top_port_ids,
        bottom_port_ids=second.bottom_port_ids,
        admittance_siemens=output,
    )


def cascade_layer_pair_stack(
    networks: Sequence[LayerPairNetwork],
) -> LayerPairNetwork:
    """Cascade an ordered adjacent-layer stack deterministically from top down."""

    ordered = tuple(networks)
    if len(ordered) < 2:
        raise LayerPairNetworkError("a layer-pair stack requires at least two networks")
    result = ordered[0]
    for next_network in ordered[1:]:
        result = cascade_layer_pair_networks(result, next_network)
    return result


__all__ = [
    "LayerPairNetwork",
    "LayerPairNetworkError",
    "SampledPassivityDiagnostic",
    "cascade_layer_pair_networks",
    "cascade_layer_pair_stack",
]
