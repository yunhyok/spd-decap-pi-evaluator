"""Experimental PEEC model for source-dimensioned, solid filled microvias.

This module intentionally models *only* straight, vertical, circular copper
segments.  It is a building block for a source-proven via topology, not a
fallback for ambiguous SPD connectivity.  In particular, pads, antipads,
planes, bends, and an inferred return path are outside its scope.

The branch partial-inductance matrix is assembled from the Neumann integral
for finite, parallel z-directed filaments.  Direction is represented solely
in the terminal-current constraint matrix; the geometrical partial-L matrix
therefore remains symmetric positive semidefinite instead of acquiring
spurious negative mutual entries from a chosen return-current convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asinh, isfinite, pi, sqrt
from typing import Iterable, Literal

import numpy as np
from numpy.typing import NDArray
from scipy.special import ive


MU_0_H_PER_M = 4.0e-7 * pi
_TINY = float(np.finfo(np.float64).tiny)
# At this gate cond(A)*eps is still about 2e-4.  Independent backward-error,
# reciprocity, KCL, and equipotential-voltage gates remain mandatory; lowering
# it without real-problem conditioning evidence would reject valid via arrays.
_MAX_CONDITION = 1.0e12
_SOLVE_RESIDUAL_LIMIT = 1.0e-10
_RECIPROCITY_LIMIT = 1.0e-10


class ViaPeecError(ValueError):
    """Raised when a filled-via PEEC problem is physically incomplete."""


@dataclass(frozen=True, slots=True)
class ViaPeecOwnership:
    """Non-composable ownership note for an isolated PEEC reference solve.

    The finite-wire partial inductance and solid-cylinder internal impedance
    belong to this block exactly once.  Pads, antipads and plane spreading do
    not.  Therefore an ``isolated_reference`` result is diagnostic only and
    may not be directly added to any layer-domain operator.  A future global
    Merely naming a core block does not calculate ``A_exact - A_core`` and
    therefore cannot authorize global composition.  A subtraction *intent*
    may be recorded for a research comparison, but this record deliberately
    has no composable state.  A future correction type must carry the actual
    core and exact matrices (or evaluators), stable hashes, and a verified
    subtraction before it can cross the global-MNA boundary.
    """

    role: Literal[
        "isolated_reference", "subtraction_intent_diagnostic"
    ] = "isolated_reference"
    core_local_block_id: str | None = None
    source_evidence: str = "canonical filled-microvia geometry"

    def __post_init__(self) -> None:
        if self.role not in {
            "isolated_reference",
            "subtraction_intent_diagnostic",
        }:
            raise ViaPeecError(
                "PEEC ownership role is invalid; exact_minus_core is not a role claim "
                "without actual subtraction data"
            )
        if not isinstance(self.source_evidence, str) or not self.source_evidence.strip():
            raise ViaPeecError("PEEC ownership needs source evidence")
        core_id = None if self.core_local_block_id is None else str(self.core_local_block_id).strip()
        if self.role == "subtraction_intent_diagnostic" and not core_id:
            raise ViaPeecError(
                "subtraction-intent diagnostic needs a stable core local-block identifier"
            )
        if self.role == "isolated_reference" and core_id is not None:
            raise ViaPeecError("isolated PEEC ownership must not name a composited core block")
        object.__setattr__(self, "core_local_block_id", core_id)

    @property
    def global_mna_composable(self) -> bool:
        """Always false: this record carries no exact-minus-core matrix."""

        return False

    def require_global_mna_composable(self) -> None:
        """Fail closed instead of turning a role string into physical evidence."""

        detail = (
            f" for core {self.core_local_block_id!r}"
            if self.core_local_block_id is not None
            else ""
        )
        raise ViaPeecError(
            "isolated PEEC partial inductance is diagnostic-only and cannot be "
            f"stamped into global MNA{detail}; actual exact/core data, stable hashes, "
            "and verified subtraction are required"
        )


def _finite_positive(value: float, *, name: str) -> float:
    number = float(value)
    if not isfinite(number) or number <= 0.0:
        raise ViaPeecError(f"{name} must be finite and > 0")
    return number


def _readonly(values: NDArray[np.generic] | Iterable[object], *, dtype: np.dtype) -> NDArray[np.generic]:
    array = np.asarray(values, dtype=dtype).copy()
    array.setflags(write=False)
    return array


def _matrix_condition(values: NDArray[np.complex128], *, name: str) -> float:
    try:
        condition = float(np.linalg.cond(values))
    except np.linalg.LinAlgError as exc:
        raise ViaPeecError(f"{name} condition estimate failed") from exc
    if not isfinite(condition) or condition > _MAX_CONDITION:
        raise ViaPeecError(f"{name} is ill-conditioned ({condition:.3e})")
    return condition


@dataclass(frozen=True, slots=True)
class FilledMicroviaSegment:
    """One canonical +z, solid-copper vertical segment in metres.

    ``current_sign`` declares whether positive current for ``group_id`` flows
    in the canonical +z (``+1``) or -z (``-1``) direction.  It affects only
    current/voltage constraints, never the geometric partial-L matrix.
    Every group must map to exactly one external ``terminal_id``.
    """

    segment_id: str
    x_m: float
    y_m: float
    z0_m: float
    z1_m: float
    radius_m: float
    conductivity_s_per_m: float
    group_id: str
    terminal_id: str
    current_sign: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.segment_id, str) or not self.segment_id.strip():
            raise ViaPeecError("segment_id must be non-empty")
        if not isinstance(self.group_id, str) or not self.group_id.strip():
            raise ViaPeecError("group_id must be non-empty")
        if not isinstance(self.terminal_id, str) or not self.terminal_id.strip():
            raise ViaPeecError("terminal_id must be non-empty")
        for name in ("x_m", "y_m", "z0_m", "z1_m"):
            value = float(getattr(self, name))
            if not isfinite(value):
                raise ViaPeecError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        z0 = float(self.z0_m)
        z1 = float(self.z1_m)
        if z1 <= z0:
            raise ViaPeecError("z1_m must be greater than z0_m; use current_sign for direction")
        object.__setattr__(self, "radius_m", _finite_positive(self.radius_m, name="radius_m"))
        object.__setattr__(
            self,
            "conductivity_s_per_m",
            _finite_positive(self.conductivity_s_per_m, name="conductivity_s_per_m"),
        )
        if int(self.current_sign) not in (-1, 1):
            raise ViaPeecError("current_sign must be +1 or -1")
        object.__setattr__(self, "current_sign", int(self.current_sign))

    @property
    def length_m(self) -> float:
        return self.z1_m - self.z0_m


@dataclass(frozen=True, slots=True)
class ViaPeecDiagnostics:
    """Numerical/physical checks returned for one frequency solve."""

    partial_inductance_min_eigenvalue_h: float
    partial_inductance_max_eigenvalue_h: float
    branch_impedance_condition: float
    terminal_admittance_condition: float
    branch_solve_backward_error: float
    terminal_solve_backward_error: float
    kcl_relative_residual: float
    equipotential_voltage_relative_residual: float
    raw_terminal_admittance_reciprocity_relative_error: float
    raw_terminal_impedance_reciprocity_relative_error: float
    min_hermitian_impedance_eigenvalue_ohm: float
    passivity_tolerance_ohm: float


@dataclass(frozen=True, slots=True)
class ViaPeecSolveResult:
    """Reciprocal terminal impedance and exact minimum-energy sharing map."""

    frequency_hz: float
    terminal_ids: tuple[str, ...]
    group_ids: tuple[str, ...]
    impedance_ohm: NDArray[np.complex128]
    branch_currents_per_group_amp: NDArray[np.complex128]
    diagnostics: ViaPeecDiagnostics

    def __post_init__(self) -> None:
        impedance = _readonly(self.impedance_ohm, dtype=np.complex128)
        currents = _readonly(self.branch_currents_per_group_amp, dtype=np.complex128)
        count = len(self.group_ids)
        if impedance.shape != (count, count):
            raise ViaPeecError("terminal impedance shape does not match group_ids")
        if currents.ndim != 2 or currents.shape[1] != count:
            raise ViaPeecError("branch current sharing map shape does not match group_ids")
        object.__setattr__(self, "impedance_ohm", impedance)
        object.__setattr__(self, "branch_currents_per_group_amp", currents)


@dataclass(frozen=True, slots=True)
class ViaPeecOperator:
    """Frequency-independent geometry and group constraints for PEEC vias."""

    segments: tuple[FilledMicroviaSegment, ...]
    group_ids: tuple[str, ...]
    terminal_ids: tuple[str, ...]
    group_current_constraint: NDArray[np.float64]
    external_partial_inductance_h: NDArray[np.float64]
    partial_inductance_min_eigenvalue_h: float
    partial_inductance_max_eigenvalue_h: float
    ownership: ViaPeecOwnership = ViaPeecOwnership()

    def require_global_mna_composable(self) -> None:
        """Expose the ownership boundary on the compiled diagnostic operator."""

        self.ownership.require_global_mna_composable()

    def __post_init__(self) -> None:
        segments = tuple(self.segments)
        group_ids = tuple(str(item).strip() for item in self.group_ids)
        terminal_ids = tuple(str(item).strip() for item in self.terminal_ids)
        count = len(segments)
        groups = len(group_ids)
        constraint = _readonly(self.group_current_constraint, dtype=np.float64)
        inductance = _readonly(self.external_partial_inductance_h, dtype=np.float64)
        if not count or not groups:
            raise ViaPeecError("PEEC operator requires at least one segment and one group")
        if not all(isinstance(item, FilledMicroviaSegment) for item in segments):
            raise ViaPeecError("PEEC operator segments must be FilledMicroviaSegment instances")
        if not all(group_ids) or len(set(group_ids)) != len(group_ids):
            raise ViaPeecError("PEEC operator group_ids must be non-empty and unique")
        if len(terminal_ids) != groups or not all(terminal_ids) or len(set(terminal_ids)) != len(terminal_ids):
            raise ViaPeecError("PEEC operator terminal_ids must be unique and map one-to-one to groups")
        if tuple(segment.group_id for segment in segments if segment.group_id not in group_ids):
            raise ViaPeecError("PEEC operator group_ids do not cover every segment")
        expected_constraint = np.zeros((count, groups), dtype=np.float64)
        terminal_by_group = {group: terminal for group, terminal in zip(group_ids, terminal_ids, strict=True)}
        for index, segment in enumerate(segments):
            if terminal_by_group[segment.group_id] != segment.terminal_id:
                raise ViaPeecError("PEEC operator terminal mapping differs from source segment evidence")
            expected_constraint[index, group_ids.index(segment.group_id)] = float(segment.current_sign)
        if constraint.shape != (count, groups):
            raise ViaPeecError("group current constraint has invalid shape")
        if inductance.shape != (count, count):
            raise ViaPeecError("partial inductance matrix has invalid shape")
        if not np.all(np.isfinite(constraint)):
            raise ViaPeecError("group current constraint must be finite")
        if not np.array_equal(constraint, expected_constraint):
            raise ViaPeecError("PEEC group-current constraint must match source segment orientation exactly")
        if np.any(np.count_nonzero(constraint, axis=0) == 0):
            raise ViaPeecError("every PEEC group must constrain at least one branch")
        if not np.all(np.isfinite(inductance)):
            raise ViaPeecError("external partial-inductance matrix must be finite")
        # Do not repair an asymmetric caller-supplied matrix.  The raw-Y/raw-Z
        # reciprocity gates are meaningful only when this source invariant is
        # enforced before solving.
        if not np.array_equal(inductance, inductance.T):
            raise ViaPeecError("external partial-inductance matrix must be exactly symmetric")
        try:
            eigenvalues = np.linalg.eigvalsh(inductance)
        except np.linalg.LinAlgError as exc:
            raise ViaPeecError("external partial-inductance PSD check failed") from exc
        actual_min = float(np.min(eigenvalues))
        actual_max = float(np.max(eigenvalues))
        psd_tolerance = max(actual_max, _TINY) * 1.0e-12
        if actual_min < -psd_tolerance:
            raise ViaPeecError("external partial-inductance matrix must be positive semidefinite")
        recorded_min = float(self.partial_inductance_min_eigenvalue_h)
        recorded_max = float(self.partial_inductance_max_eigenvalue_h)
        if not isfinite(recorded_min) or not isfinite(recorded_max):
            raise ViaPeecError("partial-inductance eigenvalue diagnostics must be finite")
        diagnostic_tolerance = max(abs(actual_min), abs(actual_max), _TINY) * 1.0e-12
        if (
            abs(recorded_min - actual_min) > diagnostic_tolerance
            or abs(recorded_max - actual_max) > diagnostic_tolerance
        ):
            raise ViaPeecError("partial-inductance eigenvalue diagnostics do not match the matrix")
        if not isinstance(self.ownership, ViaPeecOwnership):
            raise ViaPeecError("PEEC ownership must be a ViaPeecOwnership record")
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "group_ids", group_ids)
        object.__setattr__(self, "terminal_ids", terminal_ids)
        object.__setattr__(self, "group_current_constraint", constraint)
        object.__setattr__(self, "external_partial_inductance_h", inductance)


def _neumann_antiderivative(value_m: float, separation_m: float) -> float:
    """H''(u)=1/sqrt(u²+rho²), used by the finite parallel-wire integral."""

    return value_m * asinh(value_m / separation_m) - sqrt(value_m * value_m + separation_m * separation_m)


def _finite_parallel_neumann_integral(
    z0_a_m: float,
    z1_a_m: float,
    z0_b_m: float,
    z1_b_m: float,
    separation_m: float,
) -> float:
    """Exact double Neumann integral for two non-coincident z intervals."""

    rho = _finite_positive(separation_m, name="separation_m")
    result = (
        _neumann_antiderivative(z1_a_m - z0_b_m, rho)
        - _neumann_antiderivative(z0_a_m - z0_b_m, rho)
        - _neumann_antiderivative(z1_a_m - z1_b_m, rho)
        + _neumann_antiderivative(z0_a_m - z1_b_m, rho)
    )
    if not isfinite(result) or result <= 0.0:
        raise ViaPeecError("finite Neumann integral is non-positive or non-finite")
    return result


def straight_wire_external_self_inductance(length_m: float, radius_m: float) -> float:
    """Finite external partial L of one round straight conductor.

    The physical radius is used in the finite line-integral expression.  Its
    long-wire limit is ``mu0*l/(2*pi)*(log(2*l/a)-1)``.  The separate
    solid-cylinder impedance supplies ``mu0*l/(8*pi)`` at low frequency, so
    the combined limit has the familiar ``-3/4`` constant without counting
    internal inductance twice.
    """

    length = _finite_positive(length_m, name="length_m")
    radius = _finite_positive(radius_m, name="radius_m")
    geometric_integral = 2.0 * (
        length * asinh(length / radius)
        - sqrt(length * length + radius * radius)
        + radius
    )
    inductance = MU_0_H_PER_M / (4.0 * pi) * geometric_integral
    if not isfinite(inductance) or inductance <= 0.0:
        raise ViaPeecError("external straight-wire self inductance is nonphysical")
    return inductance


def parallel_finite_wire_mutual_inductance(
    first: FilledMicroviaSegment,
    second: FilledMicroviaSegment,
) -> float:
    """Exact mutual partial L for two distinct, parallel finite z segments."""

    dx = first.x_m - second.x_m
    dy = first.y_m - second.y_m
    separation = sqrt(dx * dx + dy * dy)
    if separation <= first.radius_m + second.radius_m:
        raise ViaPeecError(
            f"segments {first.segment_id!r} and {second.segment_id!r} overlap or coincide"
        )
    integral = _finite_parallel_neumann_integral(
        first.z0_m, first.z1_m, second.z0_m, second.z1_m, separation
    )
    return MU_0_H_PER_M / (4.0 * pi) * integral


def solid_cylinder_internal_impedance(
    frequency_hz: float | NDArray[np.float64],
    *,
    length_m: float,
    radius_m: float,
    conductivity_s_per_m: float,
) -> complex | NDArray[np.complex128]:
    """Solid-cylinder internal impedance with DC and skin-effect limits.

    ``I0/I1`` is evaluated with exponentially scaled modified Bessel functions
    away from the origin.  At very small argument a power series preserves the
    exact DC resistance and the ``mu0*l/(8*pi)`` internal-inductance limit.
    For extreme arguments the stable asymptotic ratio is used instead of an
    overflow-prone unscaled Bessel call.
    """

    length = _finite_positive(length_m, name="length_m")
    radius = _finite_positive(radius_m, name="radius_m")
    conductivity = _finite_positive(conductivity_s_per_m, name="conductivity_s_per_m")
    frequencies = np.asarray(frequency_hz, dtype=np.float64)
    if np.any(~np.isfinite(frequencies)) or np.any(frequencies < 0.0):
        raise ViaPeecError("frequency_hz must be finite and >= 0")
    omega = 2.0 * pi * frequencies
    x = np.sqrt(1j * omega * MU_0_H_PER_M * conductivity) * radius
    ratio = np.empty(x.shape, dtype=np.complex128)
    small = np.abs(x) < 1.0e-3
    if np.any(small):
        xs = x[small]
        small_ratio = np.zeros(xs.shape, dtype=np.complex128)
        nonzero = xs != 0.0
        xn = xs[nonzero]
        small_ratio[nonzero] = 2.0 / xn + xn / 4.0 - xn**3 / 96.0
        ratio[small] = small_ratio
    ordinary = ~small
    if np.any(ordinary):
        xo = x[ordinary]
        # ive(0,x)/ive(1,x) has the same ratio as iv(0,x)/iv(1,x), but avoids
        # the exp(Re(x)) overflow of a copper via at high frequency.
        with np.errstate(all="ignore"):
            evaluated = ive(0, xo) / ive(1, xo)
        bad = ~np.isfinite(evaluated)
        if np.any(bad):
            xb = xo[bad]
            evaluated[bad] = 1.0 + 1.0 / (2.0 * xb) + 3.0 / (8.0 * xb**2)
        ratio[ordinary] = evaluated
    per_length = x * ratio / (2.0 * pi * radius * radius * conductivity)
    dc = 1.0 / (conductivity * pi * radius * radius)
    per_length = np.where(frequencies == 0.0, dc + 0.0j, per_length)
    result = np.asarray(length * per_length, dtype=np.complex128)
    if not np.all(np.isfinite(result)):
        raise ViaPeecError("solid-cylinder internal impedance is non-finite")
    if frequencies.ndim == 0:
        return complex(result)
    return result


def _validate_unique_segments(segments: tuple[FilledMicroviaSegment, ...]) -> None:
    ids = [segment.segment_id for segment in segments]
    if len(set(ids)) != len(ids):
        raise ViaPeecError("segment_id values must be unique")
    for first_index, first in enumerate(segments):
        for second in segments[first_index + 1 :]:
            dx = first.x_m - second.x_m
            dy = first.y_m - second.y_m
            if sqrt(dx * dx + dy * dy) <= first.radius_m + second.radius_m:
                raise ViaPeecError(
                    f"segments {first.segment_id!r} and {second.segment_id!r} overlap or coincide"
                )


def _validate_parallel_group_topology(segments: tuple[FilledMicroviaSegment, ...]) -> None:
    """Fail closed unless every equipotential group is a parallel via bundle."""

    endpoint_by_group: dict[str, tuple[float, float]] = {}
    for segment in segments:
        expected = endpoint_by_group.setdefault(segment.group_id, (segment.z0_m, segment.z1_m))
        scale = max(abs(expected[0]), abs(expected[1]), abs(segment.z0_m), abs(segment.z1_m), 1.0e-6)
        tolerance = max(1.0e-15, scale * 1.0e-12)
        if (
            abs(segment.z0_m - expected[0]) > tolerance
            or abs(segment.z1_m - expected[1]) > tolerance
        ):
            raise ViaPeecError(
                f"group {segment.group_id!r} has unsupported stacked/series via topology; "
                "all branches in one equipotential group must share z0/z1 endpoints"
            )


def compile_via_peec(
    segments: Iterable[FilledMicroviaSegment],
    *,
    ownership: ViaPeecOwnership | None = None,
) -> ViaPeecOperator:
    """Compile geometry and exact equipotential-group current constraints."""

    segment_tuple = tuple(segments)
    if not segment_tuple:
        raise ViaPeecError("at least one filled microvia segment is required")
    if not all(isinstance(segment, FilledMicroviaSegment) for segment in segment_tuple):
        raise ViaPeecError("segments must be FilledMicroviaSegment instances")
    _validate_parallel_group_topology(segment_tuple)
    _validate_unique_segments(segment_tuple)

    group_order: list[str] = []
    terminal_by_group: dict[str, str] = {}
    for segment in segment_tuple:
        if segment.group_id not in terminal_by_group:
            group_order.append(segment.group_id)
            terminal_by_group[segment.group_id] = segment.terminal_id
        elif terminal_by_group[segment.group_id] != segment.terminal_id:
            raise ViaPeecError(
                f"group {segment.group_id!r} maps to multiple terminals; explicit topology is required"
            )
    group_ids = tuple(group_order)
    terminal_ids = tuple(terminal_by_group[group] for group in group_ids)
    if len(set(terminal_ids)) != len(terminal_ids):
        raise ViaPeecError("each terminal_id must map to exactly one equipotential group")
    group_index = {group: index for index, group in enumerate(group_ids)}
    constraint = np.zeros((len(segment_tuple), len(group_ids)), dtype=np.float64)
    for index, segment in enumerate(segment_tuple):
        constraint[index, group_index[segment.group_id]] = float(segment.current_sign)

    partial = np.empty((len(segment_tuple), len(segment_tuple)), dtype=np.float64)
    for first_index, first in enumerate(segment_tuple):
        partial[first_index, first_index] = straight_wire_external_self_inductance(
            first.length_m, first.radius_m
        )
        for second_index in range(first_index + 1, len(segment_tuple)):
            mutual = parallel_finite_wire_mutual_inductance(first, segment_tuple[second_index])
            partial[first_index, second_index] = mutual
            partial[second_index, first_index] = mutual
    partial = (partial + partial.T) * 0.5
    eigenvalues = np.linalg.eigvalsh(partial)
    min_eigenvalue = float(np.min(eigenvalues))
    max_eigenvalue = float(np.max(eigenvalues))
    tolerance = max(max_eigenvalue, _TINY) * 1.0e-12
    if min_eigenvalue < -tolerance:
        raise ViaPeecError(
            "external partial-inductance matrix is not positive semidefinite; "
            "geometry needs a resolved non-overlapping PEEC discretization"
        )
    return ViaPeecOperator(
        segment_tuple,
        group_ids,
        terminal_ids,
        constraint,
        partial,
        min_eigenvalue,
        max_eigenvalue,
        ownership or ViaPeecOwnership(),
    )


def solve_via_peec(operator: ViaPeecOperator, frequency_hz: float) -> ViaPeecSolveResult:
    """Reduce coupled branches under exact group-current constraints.

    For branch impedance ``Z`` and signed group constraint ``B``, the
    equipotential minimum-energy reduction is
    ``Zgroup = (B.T @ inv(Z) @ B)^-1``.  ``B`` carries return orientation;
    the source-geometric partial-L matrix remains unsigned and PSD.
    """

    if not isinstance(operator, ViaPeecOperator):
        raise ViaPeecError("operator must be a ViaPeecOperator")
    frequency = float(frequency_hz)
    if not isfinite(frequency) or frequency < 0.0:
        raise ViaPeecError("frequency_hz must be finite and >= 0")
    segments = operator.segments
    internal = np.asarray(
        [
            solid_cylinder_internal_impedance(
                frequency,
                length_m=segment.length_m,
                radius_m=segment.radius_m,
                conductivity_s_per_m=segment.conductivity_s_per_m,
            )
            for segment in segments
        ],
        dtype=np.complex128,
    )
    branch_impedance = np.diag(internal) + 1j * 2.0 * pi * frequency * operator.external_partial_inductance_h
    if not np.all(np.isfinite(branch_impedance)):
        raise ViaPeecError("branch impedance is non-finite")
    constraint = operator.group_current_constraint
    branch_condition = _matrix_condition(
        branch_impedance, name="coupled via branch impedance"
    )
    try:
        branch_admittance_constraint = np.linalg.solve(branch_impedance, constraint)
    except np.linalg.LinAlgError as exc:
        raise ViaPeecError("coupled via branch impedance is singular") from exc
    branch_raw_residual = branch_impedance @ branch_admittance_constraint - constraint
    branch_backward_error = float(
        np.linalg.norm(branch_raw_residual)
        / max(
            float(np.linalg.norm(branch_impedance) * np.linalg.norm(branch_admittance_constraint))
            + float(np.linalg.norm(constraint)),
            _TINY,
        )
    )
    if not isfinite(branch_backward_error) or branch_backward_error > _SOLVE_RESIDUAL_LIMIT:
        raise ViaPeecError(
            f"coupled via branch solve has excessive backward error ({branch_backward_error:.3e})"
        )
    terminal_admittance = constraint.T @ branch_admittance_constraint
    raw_y_reciprocity = float(
        np.linalg.norm(terminal_admittance - terminal_admittance.T)
        / max(float(np.linalg.norm(terminal_admittance)), _TINY)
    )
    if not isfinite(raw_y_reciprocity) or raw_y_reciprocity > _RECIPROCITY_LIMIT:
        raise ViaPeecError(
            "raw PEEC terminal admittance violates reciprocity "
            f"({raw_y_reciprocity:.3e})"
        )
    terminal_condition = _matrix_condition(
        terminal_admittance, name="PEEC terminal admittance"
    )
    try:
        raw_impedance = np.linalg.solve(
            terminal_admittance, np.eye(len(operator.group_ids), dtype=np.complex128)
        )
    except np.linalg.LinAlgError as exc:
        raise ViaPeecError("equipotential group-current reduction is singular") from exc
    terminal_raw_residual = (
        terminal_admittance @ raw_impedance
        - np.eye(len(operator.group_ids), dtype=np.complex128)
    )
    terminal_backward_error = float(
        np.linalg.norm(terminal_raw_residual)
        / max(
            float(np.linalg.norm(terminal_admittance) * np.linalg.norm(raw_impedance))
            + sqrt(len(operator.group_ids)),
            _TINY,
        )
    )
    if not isfinite(terminal_backward_error) or terminal_backward_error > _SOLVE_RESIDUAL_LIMIT:
        raise ViaPeecError(
            f"PEEC terminal solve has excessive backward error ({terminal_backward_error:.3e})"
        )
    raw_z_reciprocity = float(
        np.linalg.norm(raw_impedance - raw_impedance.T)
        / max(float(np.linalg.norm(raw_impedance)), _TINY)
    )
    if not isfinite(raw_z_reciprocity) or raw_z_reciprocity > _RECIPROCITY_LIMIT:
        raise ViaPeecError(
            f"raw PEEC terminal impedance violates reciprocity ({raw_z_reciprocity:.3e})"
        )
    # Symmetrization is permitted only after the independent raw-Y/raw-Z gates.
    impedance = (raw_impedance + raw_impedance.T) * 0.5
    sharing = branch_admittance_constraint @ impedance
    kcl = constraint.T @ sharing - np.eye(len(operator.group_ids), dtype=np.complex128)
    kcl_relative = float(np.linalg.norm(kcl) / max(sqrt(len(operator.group_ids)), _TINY))
    if not isfinite(kcl_relative) or kcl_relative > _SOLVE_RESIDUAL_LIMIT:
        raise ViaPeecError(f"PEEC group-current KCL residual is too large ({kcl_relative:.3e})")
    voltage_residual = branch_impedance @ sharing - constraint @ impedance
    voltage_scale = float(np.linalg.norm(branch_impedance) * np.linalg.norm(sharing)) + float(
        np.linalg.norm(constraint @ impedance)
    )
    equipotential_residual = float(
        np.linalg.norm(voltage_residual) / max(voltage_scale, _TINY)
    )
    if not isfinite(equipotential_residual) or equipotential_residual > _SOLVE_RESIDUAL_LIMIT:
        raise ViaPeecError(
            "PEEC equipotential voltage residual is too large "
            f"({equipotential_residual:.3e})"
        )
    hermitian = (impedance + impedance.conj().T) * 0.5
    min_hermitian = float(np.min(np.linalg.eigvalsh(hermitian)))
    passivity_tolerance = max(float(np.linalg.norm(impedance, ord=2)), 1.0e-18) * 1.0e-10
    if min_hermitian < -passivity_tolerance:
        raise ViaPeecError(
            "PEEC terminal impedance is non-passive; source geometry/topology is incomplete"
        )
    diagnostics = ViaPeecDiagnostics(
        partial_inductance_min_eigenvalue_h=operator.partial_inductance_min_eigenvalue_h,
        partial_inductance_max_eigenvalue_h=operator.partial_inductance_max_eigenvalue_h,
        branch_impedance_condition=branch_condition,
        terminal_admittance_condition=terminal_condition,
        branch_solve_backward_error=branch_backward_error,
        terminal_solve_backward_error=terminal_backward_error,
        kcl_relative_residual=kcl_relative,
        equipotential_voltage_relative_residual=equipotential_residual,
        raw_terminal_admittance_reciprocity_relative_error=raw_y_reciprocity,
        raw_terminal_impedance_reciprocity_relative_error=raw_z_reciprocity,
        min_hermitian_impedance_eigenvalue_ohm=min_hermitian,
        passivity_tolerance_ohm=passivity_tolerance,
    )
    return ViaPeecSolveResult(
        frequency,
        operator.terminal_ids,
        operator.group_ids,
        impedance,
        sharing,
        diagnostics,
    )


__all__ = [
    "FilledMicroviaSegment",
    "MU_0_H_PER_M",
    "ViaPeecDiagnostics",
    "ViaPeecError",
    "ViaPeecOwnership",
    "ViaPeecOperator",
    "ViaPeecSolveResult",
    "compile_via_peec",
    "parallel_finite_wire_mutual_inductance",
    "solid_cylinder_internal_impedance",
    "solve_via_peec",
    "straight_wire_external_self_inductance",
]
