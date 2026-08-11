"""Research-only axisymmetric r-z electrostatic local-feature solver.

The finite-volume equation is ``div(r*eps*grad(phi)) = 0``.  It returns an
ordinary-symmetric, indefinite Maxwell capacitance matrix for named conductor
terminals, including an explicit top/bottom outer conductor.  The radial crop
is either the default closed conductor or a disclosed open/natural boundary
that is valid only after an explicit R/2R convergence hard gate; it is never a
hidden zero-Neumann approximation.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite, pi
import json
from time import perf_counter
from typing import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csc_matrix, lil_matrix
from scipy.sparse.linalg import spsolve


EPSILON_0_F_PER_M = 8.854_187_812_8e-12


class AxisymmetricElectrostaticError(ValueError):
    """Invalid source geometry or a strict numerical/resource gate."""


@dataclass(frozen=True, slots=True)
class AxisymmetricConductor:
    terminal: str
    r_min_m: float
    r_max_m: float
    z_min_m: float
    z_max_m: float

    def __post_init__(self) -> None:
        if not self.terminal.strip() or self.r_min_m < 0.0 or self.r_max_m <= self.r_min_m or self.z_max_m <= self.z_min_m:
            raise AxisymmetricElectrostaticError("conductor needs a non-blank terminal and positive r/z extent")


@dataclass(frozen=True, slots=True)
class DielectricLayer:
    z_min_m: float
    z_max_m: float
    relative_permittivity: float
    name: str = "dielectric"

    def __post_init__(self) -> None:
        if self.z_max_m <= self.z_min_m or not isfinite(self.relative_permittivity) or self.relative_permittivity <= 0.0:
            raise AxisymmetricElectrostaticError("dielectric layer extent and relative permittivity must be positive")


@dataclass(frozen=True, slots=True)
class AxisymmetricProblem:
    r_max_m: float
    z_min_m: float
    z_max_m: float
    dr_m: float
    dz_m: float
    relative_permittivity: NDArray[np.float64]
    conductor_labels: NDArray[np.object_]
    outer_terminal: str
    boundary_semantics: str = "closed_outer_conductor_rmax_top_bottom"

    def __post_init__(self) -> None:
        eps = np.asarray(self.relative_permittivity, dtype=float).copy()
        labels = np.asarray(self.conductor_labels, dtype=object).copy()
        if eps.ndim != 2 or labels.shape != eps.shape or not np.all(np.isfinite(eps)) or np.any(eps <= 0.0):
            raise AxisymmetricElectrostaticError("epsilon and conductor labels must be same-shape positive two-dimensional arrays")
        if min(self.r_max_m, self.dr_m, self.dz_m) <= 0.0 or self.z_max_m <= self.z_min_m:
            raise AxisymmetricElectrostaticError("domain dimensions and mesh spacing must be positive")
        allowed_boundaries = {
            "closed_outer_conductor_rmax_top_bottom",
            "open_r_explicit_convergence_top_bottom_outer_conductor",
        }
        if self.boundary_semantics not in allowed_boundaries or not self.outer_terminal.strip():
            raise AxisymmetricElectrostaticError("unsupported or implicit outer-boundary semantics")
        # Top/bottom are always the same physical cage.  The optional open-r
        # boundary is a disclosed natural zero-flux truncation and may be used
        # only with an explicit R/2R convergence gate; it is never silently
        # substituted for the default closed cage.
        if not np.all(labels[0, :] == self.outer_terminal) or not np.all(labels[-1, :] == self.outer_terminal):
            raise AxisymmetricElectrostaticError("top/bottom must be the declared outer conductor")
        if self.boundary_semantics == "closed_outer_conductor_rmax_top_bottom" and not np.all(labels[:, -1] == self.outer_terminal):
            raise AxisymmetricElectrostaticError("closed-cage r=R must be the declared outer conductor")
        eps.setflags(write=False); labels.setflags(write=False)
        object.__setattr__(self, "relative_permittivity", eps)
        object.__setattr__(self, "conductor_labels", labels)

    @property
    def shape(self) -> tuple[int, int]:
        return tuple(int(value) for value in self.relative_permittivity.shape)

    @property
    def terminals(self) -> tuple[str, ...]:
        found = {str(value) for value in self.conductor_labels.ravel() if value is not None}
        return tuple(sorted(found, key=lambda item: (item != self.outer_terminal, item)))

    @property
    def interface_signature(self) -> str:
        payload = {"r_max_m": self.r_max_m, "z_min_m": self.z_min_m, "z_max_m": self.z_max_m, "dr_m": self.dr_m, "dz_m": self.dz_m, "outer": self.outer_terminal, "boundary": self.boundary_semantics, "eps": sha256(self.relative_permittivity.tobytes()).hexdigest()}
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def cache_signature(self) -> str:
        labels = "\x1e".join("" if item is None else str(item) for item in self.conductor_labels.ravel())
        payload = {"interface": self.interface_signature, "labels": sha256(labels.encode()).hexdigest(), "shape": self.shape}
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AxisymmetricDiagnostics:
    status: str
    reason: str | None
    cache_signature: str
    cells: int
    free_cells: int
    runtime_seconds: float
    max_relative_residual: float | None
    reciprocity_error_f: float | None
    row_sum_error_f: float | None
    min_eigenvalue_f: float | None
    max_charge_energy_relative_error: float | None
    coupling_model: str = "full_rz"
    omitted_radial_columns: int = 0


@dataclass(frozen=True, slots=True)
class AxisymmetricCapacitanceResult:
    terminals: tuple[str, ...]
    maxwell_capacitance_f: NDArray[np.float64]
    diagnostics: AxisymmetricDiagnostics


@dataclass(frozen=True, slots=True)
class DetailCorrectionResult:
    terminals: tuple[str, ...]
    correction_maxwell_capacitance_f: NDArray[np.float64]
    interface_signature: str
    detail_signature: str
    baseline_signature: str


def build_axisymmetric_problem(
    *, r_max_m: float, z_min_m: float, z_max_m: float, dr_m: float, dz_m: float,
    dielectrics: Sequence[DielectricLayer], conductors: Sequence[AxisymmetricConductor],
    outer_terminal: str = "OUTER",
    boundary_semantics: str = "closed_outer_conductor_rmax_top_bottom",
) -> AxisymmetricProblem:
    """Build a deterministic cell-centre mesh with a closed outer conductor."""
    nr = int(round(r_max_m / dr_m)); nz = int(round((z_max_m - z_min_m) / dz_m))
    if nr < 3 or nz < 3 or not np.isclose(nr * dr_m, r_max_m, rtol=1e-12, atol=dr_m*1e-9) or not np.isclose(nz * dz_m, z_max_m-z_min_m, rtol=1e-12, atol=dz_m*1e-9):
        raise AxisymmetricElectrostaticError("domain extents must be exactly divisible by mesh spacing with at least three cells")
    r = (np.arange(nr) + 0.5) * dr_m; z = z_min_m + (np.arange(nz) + 0.5) * dz_m
    eps = np.full((nz, nr), np.nan, dtype=float)
    for layer in dielectrics:
        rows = (z >= layer.z_min_m) & (z < layer.z_max_m)
        if np.any(np.isfinite(eps[rows, :])):
            raise AxisymmetricElectrostaticError("dielectric layers overlap")
        eps[rows, :] = layer.relative_permittivity
    if not np.all(np.isfinite(eps)):
        raise AxisymmetricElectrostaticError("dielectric layers must cover the full z domain")
    labels = np.full((nz, nr), None, dtype=object)
    for item in conductors:
        rows = (z >= item.z_min_m) & (z < item.z_max_m); cols = (r >= item.r_min_m) & (r < item.r_max_m)
        prior = labels[np.ix_(rows, cols)]
        if np.any((prior != None) & (prior != item.terminal)):  # noqa: E711
            raise AxisymmetricElectrostaticError("different conductor terminals overlap")
        labels[np.ix_(rows, cols)] = item.terminal
    if boundary_semantics not in {
        "closed_outer_conductor_rmax_top_bottom",
        "open_r_explicit_convergence_top_bottom_outer_conductor",
    }:
        raise AxisymmetricElectrostaticError("unsupported boundary_semantics")
    # Top/bottom are a physical cage. The default also closes r=R; the open-r
    # option leaves that face natural and explicitly requires R/2R validation.
    boundaries = [labels[0, :], labels[-1, :]]
    if boundary_semantics == "closed_outer_conductor_rmax_top_bottom":
        boundaries.append(labels[:, -1])
    for boundary in boundaries:
        if np.any((boundary != None) & (boundary != outer_terminal)):  # noqa: E711
            raise AxisymmetricElectrostaticError("a physical conductor touches the crop cage; increase the domain")
        boundary[:] = outer_terminal
    return AxisymmetricProblem(r_max_m, z_min_m, z_max_m, dr_m, dz_m, eps, labels, outer_terminal, boundary_semantics)


def _edges(
    problem: AxisymmetricProblem,
    *,
    include_radial: bool = True,
    active_columns: NDArray[np.bool_] | None = None,
):
    nz, nr = problem.shape
    eps = problem.relative_permittivity
    active = np.ones(nr, dtype=bool) if active_columns is None else np.asarray(active_columns, dtype=bool)
    if active.shape != (nr,):
        raise AxisymmetricElectrostaticError("active radial-column mask has invalid shape")
    if include_radial:
        for iz in range(nz):
            for ir in range(nr - 1):
                # radial face at r=(ir+1)dr; harmonic epsilon protects layer steps.
                er = 2.0 * eps[iz, ir] * eps[iz, ir + 1] / (eps[iz, ir] + eps[iz, ir + 1])
                yield iz * nr + ir, iz * nr + ir + 1, EPSILON_0_F_PER_M * er * 2.0 * pi * (ir + 1) * problem.dr_m * problem.dz_m / problem.dr_m
    for iz in range(nz - 1):
        for ir in range(nr):
            if not active[ir]:
                continue
            ez = 2.0 * eps[iz, ir] * eps[iz + 1, ir] / (eps[iz, ir] + eps[iz + 1, ir])
            radius = (ir + 0.5) * problem.dr_m
            yield iz * nr + ir, (iz + 1) * nr + ir, EPSILON_0_F_PER_M * ez * 2.0 * pi * radius * problem.dr_m / problem.dz_m


def _solve_signature(
    problem: AxisymmetricProblem,
    *,
    coupling_model: str,
    max_cells: int,
    max_runtime_seconds: float,
    tolerance: float,
) -> str:
    payload = {
        "problem": problem.cache_signature,
        "coupling_model": coupling_model,
        "max_cells": int(max_cells),
        "max_runtime_seconds": float(max_runtime_seconds),
        "tolerance": float(tolerance),
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def solve_axisymmetric(
    problem: AxisymmetricProblem,
    *,
    max_cells: int = 120_000,
    max_runtime_seconds: float = 60.0,
    tolerance: float = 1.0e-10,
    coupling_model: str = "full_rz",
) -> AxisymmetricCapacitanceResult:
    """Solve raw full Maxwell C, returning an explicit fail-closed diagnostic."""
    started = perf_counter(); terms = problem.terminals; cells = int(np.prod(problem.shape))
    signature = _solve_signature(
        problem,
        coupling_model=coupling_model,
        max_cells=max_cells,
        max_runtime_seconds=max_runtime_seconds,
        tolerance=tolerance,
    )
    omitted_columns = 0
    try:
        if coupling_model not in {"full_rz", "vertical_columns_only"}:
            raise AxisymmetricElectrostaticError("coupling_model must be full_rz or vertical_columns_only")
        if cells > max_cells:
            raise AxisymmetricElectrostaticError(f"resource gate: {cells} cells exceed {max_cells}")
        term_index = {name: index for index, name in enumerate(terms)}
        label_grid = problem.conductor_labels
        if coupling_model == "vertical_columns_only":
            active_columns = np.asarray(
                [
                    any(
                        label is not None and str(label) != problem.outer_terminal
                        for label in label_grid[:, ir]
                    )
                    for ir in range(problem.shape[1])
                ],
                dtype=bool,
            )
            omitted_columns = int(np.count_nonzero(~active_columns))
            if not np.any(active_columns):
                raise AxisymmetricElectrostaticError(
                    "vertical-column baseline has no column connected to a non-outer terminal"
                )
        else:
            active_columns = np.ones(problem.shape[1], dtype=bool)
        labels = label_grid.ravel()
        active_cells = np.tile(active_columns, problem.shape[0])
        free = np.flatnonzero((labels == None) & active_cells)  # noqa: E711
        free_index = np.full(cells, -1, dtype=int); free_index[free] = np.arange(free.size)
        edges = tuple(
            _edges(
                problem,
                include_radial=coupling_model == "full_rz",
                active_columns=active_columns,
            )
        )
        matrix = lil_matrix((free.size, free.size), dtype=float); rhs_by_term = np.zeros((free.size, len(terms)))
        for left, right, conductance in edges:
            left_free, right_free = free_index[left], free_index[right]
            left_label, right_label = labels[left], labels[right]
            if left_label is not None and right_label is not None:
                if left_label != right_label:
                    raise AxisymmetricElectrostaticError("different terminals are in direct contact")
                continue
            if left_free >= 0 and right_free >= 0:
                matrix[left_free, left_free] += conductance; matrix[right_free, right_free] += conductance
                matrix[left_free, right_free] -= conductance; matrix[right_free, left_free] -= conductance
            else:
                free_cell = left_free if left_free >= 0 else right_free
                fixed_label = right_label if left_free >= 0 else left_label
                matrix[free_cell, free_cell] += conductance
                rhs_by_term[free_cell, term_index[str(fixed_label)]] += conductance
        if free.size == 0:
            raise AxisymmetricElectrostaticError("no dielectric/free cells remain")
        system = csc_matrix(matrix)
        potentials_free = np.asarray(spsolve(system, rhs_by_term), dtype=float)
        if potentials_free.ndim == 1: potentials_free = potentials_free[:, None]
        if not np.all(np.isfinite(potentials_free)):
            raise AxisymmetricElectrostaticError("finite-volume solve is singular/non-finite")
        residual = system @ potentials_free - rhs_by_term
        residuals = np.linalg.norm(residual, axis=0) / np.maximum(np.linalg.norm(rhs_by_term, axis=0), np.finfo(float).tiny)
        if float(np.max(residuals)) > tolerance:
            raise AxisymmetricElectrostaticError(f"residual gate: {float(np.max(residuals)):.3e} exceeds {tolerance:.3e}")
        c = np.zeros((len(terms), len(terms)), dtype=float); energies = np.zeros(len(terms), dtype=float)
        for column in range(len(terms)):
            values = np.zeros(cells, dtype=float); values[free] = potentials_free[:, column]
            for cell, label in enumerate(labels):
                if label is not None: values[cell] = 1.0 if term_index[str(label)] == column else 0.0
            for left, right, conductance in edges:
                delta = values[left] - values[right]
                energies[column] += 0.5 * conductance * delta * delta
                left_label, right_label = labels[left], labels[right]
                if left_label is not None and right_label is None: c[term_index[str(left_label)], column] += conductance * delta
                elif right_label is not None and left_label is None: c[term_index[str(right_label)], column] -= conductance * delta
        scale = max(float(np.max(np.abs(c))), 1.0e-30)
        reciprocity = float(np.max(np.abs(c-c.T))); row_sum = float(np.max(np.abs(c.sum(axis=0))) )
        energy_error = float(np.max(np.abs(np.diag(c)-2.0*energies) / np.maximum(np.abs(np.diag(c)), 1.0e-30)))
        minimum = float(np.linalg.eigvalsh(0.5*(c+c.T)).min())
        if reciprocity > scale*1e-8 or row_sum > scale*1e-8 or minimum < -scale*1e-9 or energy_error > 1e-8:
            raise AxisymmetricElectrostaticError(f"Maxwell gate: reciprocal={reciprocity:.3e}, row={row_sum:.3e}, minEig={minimum:.3e}, energy={energy_error:.3e}")
        elapsed = perf_counter()-started
        if elapsed > max_runtime_seconds:
            raise AxisymmetricElectrostaticError(f"runtime gate: {elapsed:.3f}s exceeds {max_runtime_seconds:.3f}s")
        c.setflags(write=False)
        return AxisymmetricCapacitanceResult(terms, c, AxisymmetricDiagnostics("computed", None, signature, cells, int(free.size), elapsed, float(np.max(residuals)), reciprocity, row_sum, minimum, energy_error, coupling_model, omitted_columns))
    except AxisymmetricElectrostaticError as exc:
        return AxisymmetricCapacitanceResult((), np.empty((0,0), dtype=float), AxisymmetricDiagnostics("blocked_fail_closed", str(exc), signature, cells, int(np.count_nonzero(problem.conductor_labels == None)), perf_counter()-started, None, None, None, None, None, coupling_model, omitted_columns))  # noqa: E711


def solve_vertical_column_baseline(
    problem: AxisymmetricProblem,
    *,
    max_cells: int = 120_000,
    max_runtime_seconds: float = 60.0,
    tolerance: float = 1.0e-10,
) -> AxisymmetricCapacitanceResult:
    """Solve the same geometry with z-face admittances only.

    Each radial annulus is independent. Columns containing only the closed
    outer terminal are omitted and reported; a column with a non-outer
    terminal remains a complete one-dimensional Dirichlet problem between the
    explicit top/bottom cage faces. No radial field, geometry substitution, or
    capacitance fit is introduced.
    """

    return solve_axisymmetric(
        problem,
        max_cells=max_cells,
        max_runtime_seconds=max_runtime_seconds,
        tolerance=tolerance,
        coupling_model="vertical_columns_only",
    )


def detail_minus_baseline(detail: AxisymmetricCapacitanceResult, baseline: AxisymmetricCapacitanceResult, *, detail_problem: AxisymmetricProblem, baseline_problem: AxisymmetricProblem) -> DetailCorrectionResult:
    if detail.diagnostics.status != "computed" or baseline.diagnostics.status != "computed":
        raise AxisymmetricElectrostaticError("detail-minus-baseline requires two gate-passing raw Maxwell solves")
    if detail_problem.interface_signature != baseline_problem.interface_signature or detail.terminals != baseline.terminals:
        raise AxisymmetricElectrostaticError("detail and baseline must have identical crop/interface and terminal ordering")
    correction = detail.maxwell_capacitance_f - baseline.maxwell_capacitance_f
    if float(np.max(np.abs(correction.sum(axis=0)))) > max(float(np.max(np.abs(correction))), 1e-30)*1e-8:
        raise AxisymmetricElectrostaticError("detail correction lost Maxwell row-sum consistency")
    correction.setflags(write=False)
    return DetailCorrectionResult(detail.terminals, correction, detail_problem.interface_signature, detail_problem.cache_signature, baseline_problem.cache_signature)


def radial_coupling_correction(
    detail: AxisymmetricCapacitanceResult,
    vertical_baseline: AxisymmetricCapacitanceResult,
    *,
    problem: AxisymmetricProblem,
) -> DetailCorrectionResult:
    """Return full-rz minus same-geometry vertical-column Maxwell C.

    The geometry, epsilon grid, terminal labels, crop, and outer-conductor
    semantics are necessarily identical because both results are tied to one
    ``problem``.  The only removed physics is radial face admittance, so the
    correction cannot double-count the vertical overlap already represented by
    a global plane model.
    """

    if detail.diagnostics.status != "computed" or vertical_baseline.diagnostics.status != "computed":
        raise AxisymmetricElectrostaticError(
            "radial correction requires gate-passing full-rz and vertical-column solves"
        )
    if detail.diagnostics.coupling_model != "full_rz" or vertical_baseline.diagnostics.coupling_model != "vertical_columns_only":
        raise AxisymmetricElectrostaticError(
            "radial correction received the wrong coupling-model pair"
        )
    if detail.terminals != problem.terminals or vertical_baseline.terminals != problem.terminals:
        raise AxisymmetricElectrostaticError(
            "radial correction terminal ordering differs from the shared geometry"
        )
    correction = detail.maxwell_capacitance_f - vertical_baseline.maxwell_capacitance_f
    raw_scale = max(
        float(np.max(np.abs(detail.maxwell_capacitance_f))),
        float(np.max(np.abs(vertical_baseline.maxwell_capacitance_f))),
        1.0e-30,
    )
    if float(np.max(np.abs(correction-correction.T))) > raw_scale*1e-8:
        raise AxisymmetricElectrostaticError("radial correction lost ordinary-transpose symmetry")
    if float(np.max(np.abs(correction.sum(axis=0)))) > raw_scale*1e-8:
        raise AxisymmetricElectrostaticError("radial correction lost Maxwell row-sum consistency")
    correction.setflags(write=False)
    return DetailCorrectionResult(
        detail.terminals,
        correction,
        problem.interface_signature,
        problem.cache_signature,
        sha256((problem.cache_signature + "\x1fvertical_columns_only").encode()).hexdigest(),
    )


def map_correction_to_global(correction: DetailCorrectionResult, terminal_to_global_node: Mapping[str, str]) -> tuple[tuple[str, ...], NDArray[np.float64]]:
    if set(terminal_to_global_node) != set(correction.terminals):
        raise AxisymmetricElectrostaticError("every and only local terminal requires a global incidence mapping")
    nodes = tuple(dict.fromkeys(terminal_to_global_node[item] for item in correction.terminals))
    incidence = np.zeros((len(correction.terminals), len(nodes)))
    position = {name: index for index, name in enumerate(nodes)}
    for row, terminal in enumerate(correction.terminals): incidence[row, position[terminal_to_global_node[terminal]]] = 1.0
    result = incidence.T @ correction.correction_maxwell_capacitance_f @ incidence
    result.setflags(write=False)
    return nodes, result


def require_mesh_crop_convergence(*, coarse: DetailCorrectionResult, fine: DetailCorrectionResult, expanded: DetailCorrectionResult, relative_tolerance: float = 0.08) -> DetailCorrectionResult:
    """Fail closed unless h/h2 and R/2R corrections agree elementwise."""
    if not (coarse.terminals == fine.terminals == expanded.terminals):
        raise AxisymmetricElectrostaticError("convergence comparison terminal ordering differs")
    scale = np.maximum(np.abs(fine.correction_maxwell_capacitance_f), 1e-18)
    mesh = float(np.max(np.abs(coarse.correction_maxwell_capacitance_f-fine.correction_maxwell_capacitance_f)/scale))
    crop = float(np.max(np.abs(expanded.correction_maxwell_capacitance_f-fine.correction_maxwell_capacitance_f)/scale))
    if max(mesh, crop) > relative_tolerance:
        raise AxisymmetricElectrostaticError(f"mesh/crop convergence gate: mesh={mesh:.3g}, crop={crop:.3g}, tolerance={relative_tolerance:.3g}")
    return fine
