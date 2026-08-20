"""Fail-closed uniform-mode replacement from exact-artwork adjacent bulk C.

This companion deliberately does one narrow thing: replace the legacy modal
(0,0) shunt with a source-proven multi-net uniform admittance.  It is not a
new parallel shunt, and it must not be enabled unless the SPD evidence ties
the selected source net and reference plane component unambiguously.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Callable, Sequence

import numpy as np
from numpy.typing import NDArray

from .modal import DielectricDispersion, PreparedDeviceSystem, RectangularCavitySolver
from .multilayer_capacitance import AdjacentGapMaxwellPartial


class UniformC00Error(ValueError):
    """Raised for an unsafe uniform C00 replacement request."""


@dataclass(frozen=True, slots=True)
class DispersiveAdjacentGap:
    """One immutable nominal-C adjacent gap with source-tabulated Dk/Df."""

    partial: AdjacentGapMaxwellPartial
    dispersion: DielectricDispersion


@dataclass(frozen=True, slots=True)
class UniformPortConnectivityEvidence:
    """Source proof that each launch reaches its own conductor component.

    This never asserts that PWR and reference are in the same electrical
    component (which would be a short).  Each boolean is independently bound
    to a topology/compiler certificate supplied by the caller.
    """

    reference_net: str
    source_net: str
    source_terminal_component_proven: bool
    reference_terminal_component_proven: bool
    evidence: str

    def __post_init__(self) -> None:
        if not self.reference_net.strip() or not self.source_net.strip() or not self.evidence.strip():
            raise UniformC00Error("reference/source tie evidence must contain names and source evidence")


@dataclass(frozen=True, slots=True)
class UniformLoadBlock:
    """Frequency-domain load block stamped on named non-reference nodes."""

    net_names: tuple[str, ...]
    admittance_at_hz: Callable[[float], NDArray[np.complex128]]
    evidence: str

    def __post_init__(self) -> None:
        keys = tuple(_key(item) for item in self.net_names)
        if not keys or len(set(keys)) != len(keys) or not self.evidence.strip():
            raise UniformC00Error("uniform load needs unique named nodes and source evidence")


@dataclass(frozen=True, slots=True)
class UniformC00Assembly:
    """Frequency-local effective uniform admittance, or a fail-closed block."""

    status: str
    reason: str | None
    selected_net_names: tuple[str, ...]
    frequencies_hz: NDArray[np.float64]
    effective_admittance_s: NDArray[np.complex128] | None
    raw_admittance_s: NDArray[np.complex128] | None
    reference_evidence: UniformPortConnectivityEvidence | None

    def __post_init__(self) -> None:
        if self.status not in {"ok", "blocked_fail_closed"}:
            raise UniformC00Error("uniform assembly status is invalid")
        selected = tuple(self.selected_net_names)
        selected_keys = tuple(_key(item) for item in selected)
        if not selected or len(set(selected_keys)) != len(selected_keys):
            raise UniformC00Error("uniform assembly selected nets must be non-empty and unique")
        frequencies = np.asarray(self.frequencies_hz, dtype=np.float64)
        if (
            frequencies.ndim != 1
            or not frequencies.size
            or not np.all(np.isfinite(frequencies))
            or np.any(frequencies <= 0.0)
        ):
            raise UniformC00Error("uniform assembly frequencies must be finite positive values")
        if self.status == "blocked_fail_closed":
            if self.effective_admittance_s is not None or self.raw_admittance_s is not None:
                raise UniformC00Error("blocked uniform assembly must not expose usable admittance data")
            if not self.reason:
                raise UniformC00Error("blocked uniform assembly needs an actionable reason")
        elif self.effective_admittance_s is None or self.raw_admittance_s is None:
            raise UniformC00Error("successful uniform assembly requires raw and reduced admittance data")
        for matrix in (self.effective_admittance_s, self.raw_admittance_s):
            if matrix is not None:
                if (
                    matrix.ndim != 3
                    or matrix.shape[0] != frequencies.size
                    or matrix.shape[1] != matrix.shape[2]
                    or not np.all(np.isfinite(matrix))
                ):
                    raise UniformC00Error("uniform assembly matrix must be finite (frequency,node,node)")
                frozen = _readonly(matrix, np.complex128)
                if matrix is self.effective_admittance_s:
                    object.__setattr__(self, "effective_admittance_s", frozen)
                else:
                    object.__setattr__(self, "raw_admittance_s", frozen)
        if self.effective_admittance_s is not None and self.effective_admittance_s.shape[1:] != (len(selected), len(selected)):
            raise UniformC00Error("effective uniform admittance shape does not match selected nets")
        object.__setattr__(self, "selected_net_names", selected)
        object.__setattr__(self, "frequencies_hz", _readonly(frequencies, np.float64))


def _key(value: str) -> str:
    result = value.strip().casefold()
    if not result:
        raise UniformC00Error("net name must not be blank")
    return result


def _readonly(values: NDArray[np.generic], dtype: np.dtype | type) -> NDArray:
    result = np.array(values, dtype=dtype, copy=True)
    result.setflags(write=False)
    return result


def _validate_partials(partials: Sequence[DispersiveAdjacentGap]) -> tuple[str, ...]:
    if not partials:
        raise UniformC00Error("at least one adjacent physical-gap partial is required")
    names = partials[0].partial.net_names
    for item in partials:
        if item.partial.net_names != names:
            raise UniformC00Error("all adjacent partials must preserve the same global net ordering")
    return names


def assemble_adjacent_bulk_admittance(
    partials: Sequence[DispersiveAdjacentGap], frequencies_hz: Sequence[float] | NDArray[np.float64]
) -> NDArray[np.complex128]:
    """Assemble ``j*w*sum(eps_r*(1-j*Df)/eps_r_nom*C_gap_nom)``.

    The result remains ordinary complex-symmetric (not Hermitian) and uses
    source-table clamped log-frequency interpolation supplied by
    :class:`DielectricDispersion`.
    """

    names = _validate_partials(partials)
    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    if frequencies.ndim != 1 or not frequencies.size or not np.all(np.isfinite(frequencies)) or np.any(frequencies <= 0.0):
        raise UniformC00Error("frequencies must be finite positive values")
    result = np.zeros((frequencies.size, len(names), len(names)), dtype=np.complex128)
    for item in partials:
        dk, df = item.dispersion.interpolate(frequencies)
        ratio = dk * (1.0 - 1j * df) / item.partial.nominal_relative_permittivity
        result += (1j * 2.0 * np.pi * frequencies * ratio)[:, None, None] * item.partial.maxwell_capacitance_f[None, :, :]
    _gate_admittance(result, "adjacent-bulk admittance")
    for matrix in result:
        scale = max(float(np.max(np.abs(matrix))), 1.0e-30)
        if float(np.max(np.abs(matrix.sum(axis=1)))) > scale * 1.0e-10:
            raise UniformC00Error("adjacent-bulk admittance row-sum check failed")
    return _readonly(result, np.complex128)


def _gate_admittance(values: NDArray[np.complex128], name: str) -> None:
    if not np.all(np.isfinite(values)) or not np.allclose(values, np.swapaxes(values, 1, 2), rtol=1.0e-10, atol=1.0e-18):
        raise UniformC00Error(f"{name} must be finite and ordinary complex-symmetric")
    for matrix in values:
        scale = max(float(np.max(np.abs(matrix))), 1.0e-30)
        # Capacitive Maxwell terms preserve row sum before the reference is
        # grounded.  Loads are exempt from this check at their stamp level.
        hermitian = 0.5 * (matrix.real + matrix.real.T)
        if float(np.linalg.eigvalsh(hermitian).min()) < -scale * 1.0e-10:
            raise UniformC00Error(f"{name} has non-passive Hermitian real part")


def _load_matrix(
    loads: Sequence[UniformLoadBlock], names: tuple[str, ...], reference_key: str, frequency_hz: float
) -> NDArray[np.complex128]:
    nonref = tuple(name for name in names if _key(name) != reference_key)
    positions = {_key(name): index for index, name in enumerate(nonref)}
    stamp = np.zeros((len(nonref), len(nonref)), dtype=np.complex128)
    for load in loads:
        keys = tuple(_key(name) for name in load.net_names)
        if reference_key in keys or any(key not in positions for key in keys):
            raise UniformC00Error("uniform load must stamp only retained non-reference global net nodes")
        values = np.asarray(load.admittance_at_hz(float(frequency_hz)), dtype=np.complex128)
        if values.shape != (len(keys), len(keys)):
            raise UniformC00Error("uniform load returned a matrix with the wrong named-node shape")
        if not np.all(np.isfinite(values)) or not np.allclose(values, values.T, rtol=1.0e-10, atol=1.0e-18):
            raise UniformC00Error("uniform load must be finite and ordinary complex-symmetric")
        hermitian = 0.5 * (values.real + values.real.T)
        scale = max(float(np.max(np.abs(values))), 1.0e-30)
        if float(np.linalg.eigvalsh(hermitian).min()) < -scale * 1.0e-10:
            raise UniformC00Error("uniform load has non-passive Hermitian real part")
        indices = np.asarray([positions[key] for key in keys], dtype=int)
        stamp[np.ix_(indices, indices)] += values
    return stamp


def assemble_uniform_effective_admittance(
    partials: Sequence[DispersiveAdjacentGap],
    frequencies_hz: Sequence[float] | NDArray[np.float64],
    *,
    reference_net: str,
    selected_nets: Sequence[str],
    port_connectivity: UniformPortConnectivityEvidence | None,
    loads: Sequence[UniformLoadBlock] = (),
) -> UniformC00Assembly:
    """Ground reference, stamp loads, then Schur eliminate *at every f*.

    No capacitance-only reduction is used once a load is present.  Missing or
    disconnected source/reference evidence yields an explicit fail-closed
    result rather than a geometry-only prediction pretending to be usable.
    """

    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    names = _validate_partials(partials)
    reference_key = _key(reference_net)
    keys = tuple(_key(name) for name in names)
    selected_keys = tuple(_key(item) for item in selected_nets)
    if not selected_keys or len(set(selected_keys)) != len(selected_keys) or reference_key not in keys:
        raise UniformC00Error("selected nets must be unique/nonempty and reference net must be in raw Maxwell C")
    if reference_key in selected_keys or any(key not in keys for key in selected_keys):
        raise UniformC00Error("selected uniform ports must be retained non-reference global nets")
    if (
        port_connectivity is None
        or not port_connectivity.source_terminal_component_proven
        or not port_connectivity.reference_terminal_component_proven
        or _key(port_connectivity.reference_net) != reference_key
        or _key(port_connectivity.source_net) not in selected_keys
    ):
        return UniformC00Assembly(
            "blocked_fail_closed",
            "source and reference terminal landings are not independently source-proven",
            tuple(selected_nets),
            _readonly(frequencies, np.float64),
            None,
            None,
            port_connectivity,
        )

    raw = assemble_adjacent_bulk_admittance(partials, frequencies)
    nonref_indices = np.asarray([index for index, key in enumerate(keys) if key != reference_key], dtype=int)
    nonref_keys = tuple(key for key in keys if key != reference_key)
    selected_indices = np.asarray([nonref_keys.index(key) for key in selected_keys], dtype=int)
    floating_indices = np.asarray([index for index in range(len(nonref_keys)) if index not in set(selected_indices)], dtype=int)
    effective = np.empty((frequencies.size, len(selected_keys), len(selected_keys)), dtype=np.complex128)
    grounded_raw = raw[:, nonref_indices][:, :, nonref_indices]
    for f_index, frequency in enumerate(frequencies):
        matrix = np.array(grounded_raw[f_index], copy=True)
        matrix += _load_matrix(loads, names, reference_key, float(frequency))
        _gate_admittance(matrix[None, :, :], "uniform grounded multi-net admittance")
        yss = matrix[np.ix_(selected_indices, selected_indices)]
        if floating_indices.size:
            yff = matrix[np.ix_(floating_indices, floating_indices)]
            try:
                cond = float(np.linalg.cond(yff))
                if not isfinite(cond) or cond > 1.0e12:
                    raise np.linalg.LinAlgError("ill-conditioned")
                correction = matrix[np.ix_(selected_indices, floating_indices)] @ np.linalg.solve(yff, matrix[np.ix_(floating_indices, selected_indices)])
            except np.linalg.LinAlgError as exc:
                raise UniformC00Error("frequency-dependent uniform-node Schur block is singular/ill-conditioned") from exc
            yss = yss - correction
        effective[f_index] = 0.5 * (yss + yss.T)
    _gate_admittance(effective, "effective uniform multi-net admittance")
    return UniformC00Assembly("ok", None, tuple(selected_nets), _readonly(frequencies, np.float64), _readonly(effective, np.complex128), raw, port_connectivity)


def replace_modal_c00_admittance(
    modal_admittance: NDArray[np.complex128],
    *,
    modes: Sequence[tuple[int, int]],
    modal_uniform_projection: Sequence[float],
    legacy_c00_admittance: Sequence[complex] | NDArray[np.complex128],
    replacement_uniform_admittance: Sequence[complex] | NDArray[np.complex128],
) -> NDArray[np.complex128]:
    """Replace only the scalar legacy (0,0) modal shunt; never add it.

    A retained multi-rail uniform block cannot be represented by this scalar
    modal interface and is therefore intentionally rejected by callers before
    invoking this function.
    """

    matrix = np.asarray(modal_admittance, dtype=np.complex128)
    vector_form = matrix.ndim == 2
    matrix_form = matrix.ndim == 3
    if (
        not (vector_form or matrix_form)
        or matrix.shape[-1] != len(modes)
        or (matrix_form and matrix.shape[1] != len(modes))
        or not np.all(np.isfinite(matrix))
    ):
        raise UniformC00Error("modal admittance must be (frequency,mode) diagonal data or (frequency,mode,mode) K")
    if matrix_form and not np.allclose(matrix, np.swapaxes(matrix, 1, 2), rtol=1.0e-10, atol=1.0e-18):
        raise UniformC00Error("full modal K must be ordinary complex-symmetric")
    try:
        zero_index = tuple(modes).index((0, 0))
    except ValueError as exc:
        raise UniformC00Error("modal basis has no (0,0) entry") from exc
    phi = np.asarray(modal_uniform_projection, dtype=np.float64)
    if phi.shape != (len(modes),) or not np.all(np.isfinite(phi)) or phi[zero_index] != 1.0 or not np.allclose(np.delete(phi, zero_index), 0.0, atol=1.0e-14):
        raise UniformC00Error("uniform projection must be exactly phi00=1 and zero for every nonuniform mode")
    legacy = np.asarray(legacy_c00_admittance, dtype=np.complex128)
    replacement = np.asarray(replacement_uniform_admittance, dtype=np.complex128)
    if legacy.shape != (matrix.shape[0],) or replacement.shape != legacy.shape or not np.all(np.isfinite(legacy)) or not np.all(np.isfinite(replacement)):
        raise UniformC00Error("legacy and replacement C00 admittances must match modal frequency count")
    # This local substitution cannot prove the whole modal network passive,
    # but it must never make the legacy C00 shunt active through malformed
    # source/material data.
    scale = max(float(np.max(np.abs(replacement))), 1.0e-30)
    if float(np.min(replacement.real)) < -scale * 1.0e-10:
        raise UniformC00Error("replacement C00 admittance has a non-passive real part")
    old_c00 = matrix[:, zero_index] if vector_form else matrix[:, zero_index, zero_index]
    if not np.allclose(old_c00, legacy, rtol=1.0e-10, atol=1.0e-18):
        raise UniformC00Error("legacy C00 does not match Kold(0,0); replacement would double-count or overwrite another term")
    result = np.array(matrix, copy=True)
    if vector_form:
        result[:, zero_index] = replacement
    else:
        # Knew=Kold+e0*(Yeff-Kold00)*e0.T.  Cross terms are deliberately
        # untouched; a non-e0 uniform projection was rejected above.
        result[:, zero_index, zero_index] = replacement
    return _readonly(result, np.complex128)


def replace_modal_c00_from_assembly(
    modal_admittance: NDArray[np.complex128], *, modes: Sequence[tuple[int, int]], modal_uniform_projection: Sequence[float], legacy_c00_admittance: Sequence[complex] | NDArray[np.complex128], assembly: UniformC00Assembly
) -> NDArray[np.complex128]:
    """Scalar-only convenience wrapper that blocks multi-rail reductions."""

    if assembly.status != "ok" or assembly.effective_admittance_s is None:
        raise UniformC00Error(f"uniform C00 replacement is blocked: {assembly.reason or assembly.status}")
    if len(assembly.selected_net_names) != 1 or assembly.effective_admittance_s.shape[1:] != (1, 1):
        raise UniformC00Error("multi-rail uniform block needs an expanded modal/MNA interface; scalar C00 replacement is forbidden")
    return replace_modal_c00_admittance(modal_admittance, modes=modes, modal_uniform_projection=modal_uniform_projection, legacy_c00_admittance=legacy_c00_admittance, replacement_uniform_admittance=assembly.effective_admittance_s[:, 0, 0])


def replace_prepared_uniform_c00_from_assembly(
    solver: RectangularCavitySolver,
    prepared: PreparedDeviceSystem,
    assembly: UniformC00Assembly,
) -> PreparedDeviceSystem:
    """Use the solver-owned C00 API for a scalar source-only replacement.

    Unlike :func:`replace_modal_c00_admittance`, this production path accepts
    neither a caller-selected matrix entry nor a caller-supplied ``legacy``
    value.  The rectangular solver owns the original uniform term and changes
    only that term, retaining Device/Via/decap branch data and higher modes.
    """

    if assembly.status != "ok" or assembly.effective_admittance_s is None:
        raise UniformC00Error(
            f"uniform C00 replacement is blocked: {assembly.reason or assembly.status}"
        )
    if (
        len(assembly.selected_net_names) != 1
        or assembly.effective_admittance_s.shape[1:] != (1, 1)
    ):
        raise UniformC00Error(
            "multi-rail uniform block needs an expanded modal/MNA interface; scalar C00 replacement is forbidden"
        )
    if not np.array_equal(assembly.frequencies_hz, prepared.frequencies_hz):
        raise UniformC00Error(
            "uniform C00 assembly was evaluated on a different frequency grid than "
            "the prepared Device system"
        )
    try:
        return solver.replace_uniform_c00_term(
            prepared,
            assembly.effective_admittance_s[:, 0, 0],
        )
    except ValueError as exc:
        raise UniformC00Error(str(exc)) from exc


__all__ = [
    "DispersiveAdjacentGap", "UniformC00Assembly", "UniformC00Error", "UniformLoadBlock", "UniformPortConnectivityEvidence",
    "assemble_adjacent_bulk_admittance", "assemble_uniform_effective_admittance", "replace_modal_c00_admittance", "replace_modal_c00_from_assembly", "replace_prepared_uniform_c00_from_assembly",
]
