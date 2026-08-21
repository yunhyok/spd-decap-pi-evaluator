"""Research-gated full multinet loaded hybrid assembly.

This module is deliberately outside the production evaluator.  It admits only
an *indefinite raw component Maxwell matrix*: all conductor components remain
nodes, and a reference is removed only by the global MNA gauge after
source-proven DGND connectivity.  It never performs capacitance-only floating
Schur reduction.  Decaps, shared pads, and PEEC branches are stamped into the
same frequency-domain MNA system before its internal nodes are eliminated.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, pi
from typing import Callable, Iterable, Mapping

import numpy as np
from numpy.typing import NDArray

from spd_decap_pi._core.solver.global_mna import (
    DifferentialPort,
    FilledMicroviaBranch,
    GlobalMnaError,
    GlobalMnaOperator,
    NodalAdmittanceBlock,
    SeriesBranchBlock,
    compile_global_mna,
    filled_microvia_branch_block,
)


class FullMultinetHybridError(ValueError):
    """Raised whenever source evidence or a raw physical gate is incomplete."""


def _readonly(matrix: object, dtype: np.dtype) -> NDArray[np.generic]:
    value = np.asarray(matrix, dtype=dtype).copy()
    value.setflags(write=False)
    return value


def _symmetric_and_passive(matrix: NDArray[np.float64], *, name: str) -> None:
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or not np.all(np.isfinite(matrix)):
        raise FullMultinetHybridError(f"{name} must be a finite square matrix")
    scale = max(float(np.max(np.abs(matrix))), 1.0e-30)
    if float(np.max(np.abs(matrix - matrix.T))) > scale * 1.0e-10:
        raise FullMultinetHybridError(f"{name} is not raw reciprocal (ordinary transpose)")
    if float(np.max(np.abs(matrix.sum(axis=1)))) > scale * 1.0e-10:
        raise FullMultinetHybridError(f"{name} is not an indefinite Maxwell matrix (row sums must vanish)")
    if float(np.linalg.eigvalsh(matrix).min()) < -scale * 1.0e-10:
        raise FullMultinetHybridError(f"{name} is non-passive")


@dataclass(frozen=True, slots=True)
class RawComponentMaxwellCapacitance:
    """Raw full component C, including every DGND and PWR component node."""

    node_ids: tuple[str, ...]
    capacitance_f: NDArray[np.float64]
    reference_node_ids: tuple[str, ...]
    unit_permittivity_partials_f: Mapping[str, NDArray[np.float64]] | None = None

    def __post_init__(self) -> None:
        ids = tuple(str(item).strip() for item in self.node_ids)
        if not ids or any(not item for item in ids) or len(set(ids)) != len(ids):
            raise FullMultinetHybridError("raw component matrix needs unique non-blank node IDs")
        matrix = _readonly(self.capacitance_f, np.float64)
        if matrix.shape != (len(ids), len(ids)):
            raise FullMultinetHybridError("component C shape does not match node IDs")
        _symmetric_and_passive(matrix, name="raw component Maxwell C")
        references = tuple(str(item).strip() for item in self.reference_node_ids)
        if not references or any(item not in ids for item in references):
            raise FullMultinetHybridError("every DGND reference component must be a raw C node")
        partials: dict[str, NDArray[np.float64]] = {}
        for name, partial in (self.unit_permittivity_partials_f or {}).items():
            key = str(name).strip()
            if not key or key in partials:
                raise FullMultinetHybridError("unit-permittivity partial names must be unique and non-blank")
            checked = _readonly(partial, np.float64)
            if checked.shape != matrix.shape:
                raise FullMultinetHybridError("unit-permittivity partial shape differs from raw C")
            _symmetric_and_passive(checked, name=f"unit-permittivity partial {key!r}")
            partials[key] = checked
        object.__setattr__(self, "node_ids", ids)
        object.__setattr__(self, "capacitance_f", matrix)
        object.__setattr__(self, "reference_node_ids", references)
        object.__setattr__(self, "unit_permittivity_partials_f", partials)

    def admittance_s(self, frequency_hz: float, relative_permittivity_by_partial: Mapping[str, float] | None = None) -> NDArray[np.complex128]:
        frequency = float(frequency_hz)
        if not isfinite(frequency) or frequency <= 0.0:
            raise FullMultinetHybridError("frequency must be finite and > 0")
        matrix = self.capacitance_f
        if relative_permittivity_by_partial is not None:
            if not self.unit_permittivity_partials_f:
                raise FullMultinetHybridError("no unit-permittivity partial matrices were retained")
            if set(relative_permittivity_by_partial) != set(self.unit_permittivity_partials_f):
                raise FullMultinetHybridError("every and only retained dielectric partial requires a supplied relative permittivity")
            matrix = np.zeros_like(self.capacitance_f)
            for name, partial in self.unit_permittivity_partials_f.items():
                epsilon_r = float(relative_permittivity_by_partial[name])
                if not isfinite(epsilon_r) or epsilon_r <= 0.0:
                    raise FullMultinetHybridError("relative permittivity must be finite and > 0")
                matrix += epsilon_r * partial
        return np.asarray(2j * pi * frequency * matrix, dtype=np.complex128)


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    """All provenance required before full-artwork component assembly."""

    dgnd_connectivity_proven: bool
    component_artwork_connectivity_proven: bool
    decap_network_complete: bool
    shared_pad_network_complete: bool
    microvia_solid_fill_proven: bool
    microvia_geometry_complete: bool

    def require_loaded_hybrid(self, *, require_peec: bool) -> None:
        missing = [name for name, present in (
            ("DGND_CONNECTIVITY_UNPROVEN", self.dgnd_connectivity_proven),
            ("COMPONENT_ARTWORK_CONNECTIVITY_UNPROVEN", self.component_artwork_connectivity_proven),
            ("DECAP_NETWORK_INCOMPLETE", self.decap_network_complete),
            ("SHARED_PAD_NETWORK_INCOMPLETE", self.shared_pad_network_complete),
        ) if not present]
        if require_peec:
            missing.extend(name for name, present in (
                ("MICROVIA_SOLID_FILL_UNPROVEN", self.microvia_solid_fill_proven),
                ("MICROVIA_GEOMETRY_INCOMPLETE", self.microvia_geometry_complete),
            ) if not present)
        if missing:
            raise FullMultinetHybridError("source evidence gate: " + ", ".join(missing))


@dataclass(frozen=True, slots=True)
class DecapRlcBranch:
    branch_id: str
    positive_node_id: str
    negative_node_id: str
    esr_ohm: float
    esl_h: float
    capacitance_f: float

    def impedance_ohm(self, frequency_hz: float) -> complex:
        frequency = float(frequency_hz)
        if frequency <= 0.0:
            raise FullMultinetHybridError("decap frequency must be > 0")
        if min(self.esr_ohm, self.esl_h, self.capacitance_f) < 0.0 or self.capacitance_f == 0.0:
            raise FullMultinetHybridError("decap ESR/ESL/C must be non-negative with C > 0")
        return self.esr_ohm + 2j * pi * frequency * self.esl_h + 1.0 / (2j * pi * frequency * self.capacitance_f)


def uniform_mode_replacement_admittance(
    raw_component_c: RawComponentMaxwellCapacitance,
    frequency_hz: float,
    nonuniform_admittance_s: NDArray[np.complex128] | None = None,
) -> NDArray[np.complex128]:
    """Replace, never add to, the old modal C00 uniform mode.

    ``nonuniform_admittance_s`` must contain only modes with at least one
    non-zero cosine index. It is added to raw full-C's uniform admittance;
    callers have no parameter through which legacy C00 can be double-counted.
    """

    uniform = raw_component_c.admittance_s(frequency_hz)
    if nonuniform_admittance_s is None:
        return uniform
    nonuniform = np.asarray(nonuniform_admittance_s, dtype=np.complex128)
    if nonuniform.shape != uniform.shape or not np.all(np.isfinite(nonuniform)):
        raise FullMultinetHybridError("nonuniform modal admittance has invalid component matrix shape")
    if float(np.max(np.abs(nonuniform - nonuniform.T))) > max(float(np.max(np.abs(nonuniform))), 1e-30) * 1e-10:
        raise FullMultinetHybridError("nonuniform modal correction must be complex symmetric")
    return uniform + nonuniform


def compile_loaded_hybrid(
    raw_component_c: RawComponentMaxwellCapacitance,
    evidence: SourceEvidence,
    *,
    decaps: Iterable[DecapRlcBranch],
    shared_pad_admittances: Iterable[NodalAdmittanceBlock] = (),
    microvias: Iterable[FilledMicroviaBranch] = (),
    ports: Iterable[DifferentialPort],
    nonuniform_admittance: Callable[[float], NDArray[np.complex128]] | None = None,
) -> GlobalMnaOperator:
    """Compile full loaded C/decap/shared-pad/PEEC MNA without pre-Schur C."""

    via_tuple = tuple(microvias)
    evidence.require_loaded_hybrid(require_peec=bool(via_tuple))
    decap_tuple = tuple(decaps)
    for item in decap_tuple:
        if item.positive_node_id not in raw_component_c.node_ids or item.negative_node_id not in raw_component_c.node_ids:
            raise FullMultinetHybridError("decap endpoint absent from raw component C")
    def capacitor_block(frequency: float) -> NDArray[np.complex128]:
        correction = nonuniform_admittance(frequency) if nonuniform_admittance is not None else None
        return uniform_mode_replacement_admittance(raw_component_c, frequency, correction)
    nodal = [NodalAdmittanceBlock(raw_component_c.node_ids, capacitor_block, "raw-full-component-maxwell-c")]
    nodal.extend(tuple(shared_pad_admittances))
    branches: list[SeriesBranchBlock] = []
    if decap_tuple:
        branches.append(SeriesBranchBlock(
            tuple(item.branch_id for item in decap_tuple),
            tuple(item.positive_node_id for item in decap_tuple),
            tuple(item.negative_node_id for item in decap_tuple),
            lambda frequency: np.diag([item.impedance_ohm(frequency) for item in decap_tuple]),
            "source-decap-esr-esl-c",
        ))
    if via_tuple:
        branches.append(filled_microvia_branch_block(via_tuple, block_id="source-solid-filled-microvia-peec"))
    try:
        return compile_global_mna(raw_component_c.node_ids, nodal_admittances=nodal, branch_blocks=branches, ports=tuple(ports))
    except GlobalMnaError as exc:
        raise FullMultinetHybridError(str(exc)) from exc


__all__ = [
    "DecapRlcBranch", "FullMultinetHybridError", "RawComponentMaxwellCapacitance",
    "SourceEvidence", "compile_loaded_hybrid", "uniform_mode_replacement_admittance",
]
