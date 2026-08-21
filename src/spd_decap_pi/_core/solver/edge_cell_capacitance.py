"""Research-only 2-D electrostatic edge-cell capacitance correction.

This module deliberately is *not* wired into the evaluator.  It calculates a
per-unit-length, indefinite Maxwell capacitance matrix for an explicit x-z
cross-section and returns only the detail-minus-vertical-column increment.
It is intended to add missing straight-edge/antipad fringing to the exact
polygon overlap bulk term without double-counting that bulk term.

The artificial x crop is not a metal box: for each terminal excitation its
two side boundaries receive the corresponding 1-D stratified column solution.
Consequently a uniform parallel-plate section has a zero correction (within
the mesh gate), rather than a crop-dependent capacitance.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite
from time import perf_counter
from typing import Iterable, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy import sparse
from scipy.sparse.linalg import spsolve


EPSILON_0_F_PER_M = 8.854_187_812_8e-12
_MAX_EDGE_CELL_CACHE = 64
_EDGE_CELL_CACHE: "OrderedDict[str, EdgeCellResult]" = OrderedDict()


class EdgeCellCapacitanceError(ValueError):
    """Raised when a cross-section cannot be solved without a modelling guess."""


def _positive(name: str, value: float, *, allow_zero: bool = False) -> None:
    if not isfinite(float(value)) or float(value) < 0.0 or (not allow_zero and float(value) == 0.0):
        raise EdgeCellCapacitanceError(f"{name} must be finite and {'>= 0' if allow_zero else '> 0'}")


@dataclass(frozen=True, slots=True)
class EdgeCellDielectric:
    """A horizontal dielectric stratum, with z measured in metres."""

    z_min_m: float
    z_max_m: float
    relative_permittivity: float
    name: str = ""

    def __post_init__(self) -> None:
        _positive("dielectric thickness", self.z_max_m - self.z_min_m)
        _positive("relative_permittivity", self.relative_permittivity)


@dataclass(frozen=True, slots=True)
class EdgeCellConductor:
    """An axis-aligned ideal conductor rectangle belonging to one terminal."""

    terminal: str
    x_min_m: float
    x_max_m: float
    z_min_m: float
    z_max_m: float

    def __post_init__(self) -> None:
        if not self.terminal.strip():
            raise EdgeCellCapacitanceError("conductor terminal must not be blank")
        _positive("conductor x extent", self.x_max_m - self.x_min_m)
        _positive("conductor z extent", self.z_max_m - self.z_min_m)


@dataclass(frozen=True, slots=True)
class EdgeCellProblem:
    """Fully explicit bounded cross-section.

    ``reference_terminal`` must physically form both crop-top and crop-bottom
    reference sheets.  This is intentional: without those sheets a local
    edge-cell has an ambiguous return and must not manufacture one.
    """

    x_min_m: float
    x_max_m: float
    z_min_m: float
    z_max_m: float
    cell_size_m: float
    dielectrics: tuple[EdgeCellDielectric, ...]
    conductors: tuple[EdgeCellConductor, ...]
    reference_terminal: str
    max_cells: int = 250_000
    max_runtime_s: float = 30.0

    def __post_init__(self) -> None:
        _positive("x crop width", self.x_max_m - self.x_min_m)
        _positive("z crop height", self.z_max_m - self.z_min_m)
        _positive("cell_size_m", self.cell_size_m)
        if not self.reference_terminal.strip():
            raise EdgeCellCapacitanceError("reference_terminal must not be blank")
        if not self.dielectrics or not self.conductors:
            raise EdgeCellCapacitanceError("cross-section requires dielectric and conductor data")
        if self.max_cells <= 0:
            raise EdgeCellCapacitanceError("max_cells must be positive")
        _positive("max_runtime_s", self.max_runtime_s)


@dataclass(frozen=True, slots=True)
class EdgeCellDiagnostics:
    signature: str
    nx: int
    nz: int
    cell_count: int
    unknown_count: int
    runtime_model: str
    elapsed_s: float
    max_linear_residual: float
    charge_energy_relative: float
    reciprocity_relative: float
    row_sum_relative: float
    min_eigenvalue_f_per_m: float
    finite: bool


@dataclass(frozen=True, slots=True)
class EdgeCellResult:
    terminal_names: tuple[str, ...]
    raw_maxwell_f_per_m: NDArray[np.float64]
    vertical_baseline_f_per_m: NDArray[np.float64]
    delta_maxwell_f_per_m: NDArray[np.float64]
    diagnostics: EdgeCellDiagnostics

    def __post_init__(self) -> None:
        names = tuple(str(item).strip() for item in self.terminal_names)
        if not names or not all(names) or len({item.casefold() for item in names}) != len(names):
            raise EdgeCellCapacitanceError("edge-cell terminal names must be non-empty and unique")
        matrices: list[NDArray[np.float64]] = []
        for name, item in (
            ("raw_maxwell_f_per_m", self.raw_maxwell_f_per_m),
            ("vertical_baseline_f_per_m", self.vertical_baseline_f_per_m),
            ("delta_maxwell_f_per_m", self.delta_maxwell_f_per_m),
        ):
            matrix = np.asarray(item, dtype=np.float64)
            if (
                matrix.shape != (len(names), len(names))
                or not np.all(np.isfinite(matrix))
                or not np.allclose(matrix, matrix.T, rtol=1.0e-11, atol=1.0e-24)
            ):
                raise EdgeCellCapacitanceError(f"{name} must be a finite symmetric terminal matrix")
            frozen = np.array(matrix, dtype=np.float64, copy=True)
            frozen.setflags(write=False)
            matrices.append(frozen)
        if not np.allclose(matrices[0] - matrices[1], matrices[2], rtol=1.0e-11, atol=1.0e-24):
            raise EdgeCellCapacitanceError("edge-cell delta must equal raw minus vertical baseline")
        object.__setattr__(self, "terminal_names", names)
        object.__setattr__(self, "raw_maxwell_f_per_m", matrices[0])
        object.__setattr__(self, "vertical_baseline_f_per_m", matrices[1])
        object.__setattr__(self, "delta_maxwell_f_per_m", matrices[2])


@dataclass(frozen=True, slots=True)
class EdgeCellConvergence:
    coarse: EdgeCellResult
    fine: EdgeCellResult
    wide: EdgeCellResult
    mesh_relative: float
    crop_relative: float


def _signature(problem: EdgeCellProblem) -> str:
    payload = asdict(problem)
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _public_copy(result: EdgeCellResult) -> EdgeCellResult:
    """Return fresh array owners so callers cannot poison the LRU cache."""
    return EdgeCellResult(
        terminal_names=result.terminal_names,
        raw_maxwell_f_per_m=np.array(result.raw_maxwell_f_per_m, copy=True),
        vertical_baseline_f_per_m=np.array(result.vertical_baseline_f_per_m, copy=True),
        delta_maxwell_f_per_m=np.array(result.delta_maxwell_f_per_m, copy=True),
        diagnostics=result.diagnostics,
    )


def _grid_count(length: float, h: float, name: str) -> int:
    ratio = length / h
    rounded = round(ratio)
    if not np.isclose(ratio, rounded, rtol=0.0, atol=1e-10):
        raise EdgeCellCapacitanceError(f"{name} must be an integer multiple of cell_size_m")
    if rounded < 2:
        raise EdgeCellCapacitanceError(f"{name} needs at least two cells")
    return int(rounded)


def _aligned(value: float, origin: float, h: float) -> bool:
    return bool(np.isclose((value - origin) / h, round((value - origin) / h), rtol=0.0, atol=1e-9))


def _prepare(problem: EdgeCellProblem) -> tuple[tuple[str, ...], NDArray[np.float64], NDArray[np.int_], int, int]:
    nx = _grid_count(problem.x_max_m - problem.x_min_m, problem.cell_size_m, "x crop width")
    nz = _grid_count(problem.z_max_m - problem.z_min_m, problem.cell_size_m, "z crop height")
    if nx * nz > problem.max_cells:
        raise EdgeCellCapacitanceError(f"edge-cell resource gate: {nx*nz} cells exceeds max_cells={problem.max_cells}")
    for d in problem.dielectrics:
        if d.z_min_m < problem.z_min_m or d.z_max_m > problem.z_max_m or not _aligned(d.z_min_m, problem.z_min_m, problem.cell_size_m) or not _aligned(d.z_max_m, problem.z_min_m, problem.cell_size_m):
            raise EdgeCellCapacitanceError("dielectric interfaces must be inside and aligned to the mesh")
    for c in problem.conductors:
        if c.x_min_m < problem.x_min_m or c.x_max_m > problem.x_max_m or c.z_min_m < problem.z_min_m or c.z_max_m > problem.z_max_m:
            raise EdgeCellCapacitanceError("conductor lies outside the explicit crop")
        if not all((_aligned(c.x_min_m, problem.x_min_m, problem.cell_size_m), _aligned(c.x_max_m, problem.x_min_m, problem.cell_size_m), _aligned(c.z_min_m, problem.z_min_m, problem.cell_size_m), _aligned(c.z_max_m, problem.z_min_m, problem.cell_size_m))):
            raise EdgeCellCapacitanceError("conductor boundaries must align to the mesh")
    names = tuple(sorted({c.terminal.strip() for c in problem.conductors}, key=str.casefold))
    if problem.reference_terminal not in names:
        raise EdgeCellCapacitanceError("reference_terminal has no conductor")
    eps = np.empty((nz, nx), dtype=float)
    owners = np.full((nz, nx), -1, dtype=int)
    for iz in range(nz):
        z = problem.z_min_m + (iz + 0.5) * problem.cell_size_m
        matching = [d for d in problem.dielectrics if d.z_min_m < z < d.z_max_m]
        if len(matching) != 1:
            raise EdgeCellCapacitanceError("dielectric strata must cover every non-conductor cell uniquely")
        eps[iz, :] = matching[0].relative_permittivity
    terminal_index = {name: i for i, name in enumerate(names)}
    for c in problem.conductors:
        ix0, ix1 = (round((c.x_min_m - problem.x_min_m) / problem.cell_size_m), round((c.x_max_m - problem.x_min_m) / problem.cell_size_m))
        iz0, iz1 = (round((c.z_min_m - problem.z_min_m) / problem.cell_size_m), round((c.z_max_m - problem.z_min_m) / problem.cell_size_m))
        old = owners[iz0:iz1, ix0:ix1]
        if np.any(old >= 0):
            raise EdgeCellCapacitanceError("overlapping conductor cells are ambiguous")
        owners[iz0:iz1, ix0:ix1] = terminal_index[c.terminal.strip()]
    if np.any(owners[0, :] != terminal_index[problem.reference_terminal]) or np.any(owners[-1, :] != terminal_index[problem.reference_terminal]):
        raise EdgeCellCapacitanceError("top and bottom crop boundaries must be complete reference-terminal sheets")
    return names, eps, owners, nx, nz


def _harmonic(a: float, b: float) -> float:
    return 2.0 * a * b / (a + b)


def _face_conductance(eps_a: float, owner_a: int, eps_b: float, owner_b: int) -> float:
    """Return an F/m face stamp for equal square cells.

    A dielectric-dielectric face spans one cell-centre spacing.  An ideal
    conductor's Dirichlet surface is at the shared face, only h/2 from the
    dielectric centre, so that stamp is ``2*epsilon_dielectric``.  The
    arbitrary material value stored beneath a conductor cell is never used.
    """
    if owner_a >= 0 and owner_b >= 0:
        return 0.0
    if owner_a >= 0:
        return 2.0 * EPSILON_0_F_PER_M * eps_b
    if owner_b >= 0:
        return 2.0 * EPSILON_0_F_PER_M * eps_a
    return EPSILON_0_F_PER_M * _harmonic(eps_a, eps_b)


def _column_solution(eps: NDArray[np.float64], owners: NDArray[np.int_], terminal: int) -> NDArray[np.float64]:
    """One-dimensional vertical solution for a single x-column."""
    nz = eps.size
    unknown = np.flatnonzero(owners < 0)
    index = {int(row): slot for slot, row in enumerate(unknown)}
    a = sparse.lil_matrix((unknown.size, unknown.size), dtype=float)
    b = np.zeros(unknown.size, dtype=float)
    values = np.zeros(nz, dtype=float)
    values[owners == terminal] = 1.0
    for row in unknown:
        k = index[int(row)]
        for other in (int(row) - 1, int(row) + 1):
            if other < 0 or other >= nz:
                raise EdgeCellCapacitanceError("reference sheets must occupy top and bottom boundaries")
            g = _face_conductance(float(eps[row]), int(owners[row]), float(eps[other]), int(owners[other]))
            a[k, k] += g
            if owners[other] < 0:
                a[k, index[other]] -= g
            else:
                b[k] += g * values[other]
    if unknown.size:
        values[unknown] = spsolve(a.tocsc(), b)
    return values


def _solve_matrix(eps: NDArray[np.float64], owners: NDArray[np.int_], terminal_count: int, *, horizontal: bool) -> tuple[NDArray[np.float64], NDArray[np.float64], float, float]:
    nz, nx = owners.shape
    unknown_positions = np.argwhere(owners < 0)
    lookup = np.full((nz, nx), -1, dtype=int)
    for slot, (iz, ix) in enumerate(unknown_positions):
        lookup[iz, ix] = slot
    n = len(unknown_positions)
    a = sparse.lil_matrix((n, n), dtype=float)
    rhs = np.zeros((n, terminal_count), dtype=float)
    # Side potentials are explicit 1-D column solutions, derived from their
    # actual side columns.  They are not a homogeneous boundary condition.
    side_left = np.column_stack([_column_solution(eps[:, 0], owners[:, 0], t) for t in range(terminal_count)])
    side_right = np.column_stack([_column_solution(eps[:, -1], owners[:, -1], t) for t in range(terminal_count)])
    for slot, (iz, ix) in enumerate(unknown_positions):
        for dz, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            if dx and not horizontal:
                continue
            oz, ox = int(iz + dz), int(ix + dx)
            if dz:
                if oz < 0 or oz >= nz:
                    raise EdgeCellCapacitanceError("reference sheets must occupy top and bottom boundaries")
                g = _face_conductance(float(eps[iz, ix]), int(owners[iz, ix]), float(eps[oz, ox]), int(owners[oz, ox]))
            else:
                # The explicit side value belongs to the same dielectric
                # column cell; accessing eps outside the crop would turn
                # this physical asymptote into an indexing accident.
                g = 2.0 * EPSILON_0_F_PER_M * float(eps[iz, ix]) if ox < 0 or ox >= nx else _face_conductance(
                    float(eps[iz, ix]), int(owners[iz, ix]), float(eps[oz, ox]), int(owners[oz, ox])
                )
            a[slot, slot] += g
            if ox < 0:
                rhs[slot, :] += g * side_left[iz, :]
            elif ox >= nx:
                rhs[slot, :] += g * side_right[iz, :]
            elif owners[oz, ox] >= 0:
                rhs[slot, owners[oz, ox]] += g
            else:
                a[slot, lookup[oz, ox]] -= g
    a_csc = a.tocsc()
    potentials = np.zeros((nz, nx, terminal_count), dtype=float)
    for t in range(terminal_count):
        potentials[owners == t, t] = 1.0
    if n:
        solved = spsolve(a_csc, rhs)
        if solved.ndim == 1:
            solved = solved[:, None]
        for slot, (iz, ix) in enumerate(unknown_positions):
            potentials[iz, ix, :] = solved[slot, :]
        residual = float(np.max(np.abs(a_csc @ solved - rhs))) / max(float(np.max(np.abs(rhs))), 1.0)
    else:
        residual = 0.0
    charge = np.zeros((terminal_count, terminal_count), dtype=float)
    energy = np.zeros_like(charge)
    # Count each dielectric-dielectric field face once for energy; conductor
    # interface fluxes supply terminal charge.  q = C V convention.
    for iz in range(nz):
        for ix in range(nx):
            owner = owners[iz, ix]
            for dz, dx in ((1, 0), (0, 1)):
                if dx and not horizontal:
                    continue
                oz, ox = iz + dz, ix + dx
                if oz >= nz or ox >= nx:
                    continue
                g = _face_conductance(float(eps[iz, ix]), int(owner), float(eps[oz, ox]), int(owners[oz, ox]))
                if owner >= 0 and owners[oz, ox] >= 0:
                    continue
                dv = potentials[iz, ix, :] - potentials[oz, ox, :]
                energy += g * np.outer(dv, dv)
    # Each conductor interface must be visited from the conductor side.  A
    # one-direction energy-face traversal would otherwise omit north/west
    # conductor faces and corrupt the independent charge check.
    for iz in range(nz):
        for ix in range(nx):
            owner = owners[iz, ix]
            if owner < 0:
                continue
            for dz, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if dx and not horizontal:
                    continue
                oz, ox = iz + dz, ix + dx
                if oz < 0 or oz >= nz or ox < 0 or ox >= nx or owners[oz, ox] >= 0:
                    continue
                g = _face_conductance(float(eps[iz, ix]), int(owner), float(eps[oz, ox]), int(owners[oz, ox]))
                charge[owner, :] += g * (potentials[iz, ix, :] - potentials[oz, ox, :])
    if horizontal:
        # Dirichlet side columns are linear functions of the terminal
        # excitation.  Virtual-work differentiation adds their boundary work
        # to terminal charge; without it the conventional conductor-only flux
        # misses the crop interface and cannot agree with field energy.
        for iz in range(nz):
            for ix, boundary in ((0, side_left[iz, :]), (nx - 1, side_right[iz, :])):
                if owners[iz, ix] >= 0:
                    continue
                # Dirichlet side surface to the boundary-cell centre is h/2.
                g = 2.0 * EPSILON_0_F_PER_M * float(eps[iz, ix])
                dv = potentials[iz, ix, :] - boundary
                energy += g * np.outer(dv, dv)
                charge += np.outer(boundary, -g * dv)
    # Energy includes each physical face exactly once and is the independent
    # reciprocal reference.  Side faces are intentionally excluded: their
    # 1-D boundary has no lateral field in the baseline asymptote.
    return charge, energy, residual, float(np.max(np.abs(charge - energy)) / max(float(np.max(np.abs(energy))), 1e-30))


def solve_edge_cell(problem: EdgeCellProblem) -> EdgeCellResult:
    """Solve the raw and vertical-baseline Maxwell matrices, fail-closed."""
    signature = _signature(problem)
    cached = _EDGE_CELL_CACHE.get(signature)
    if cached is not None:
        _EDGE_CELL_CACHE.move_to_end(signature)
        return _public_copy(cached)
    started = perf_counter()
    names, eps, owners, nx, nz = _prepare(problem)
    raw, raw_energy, residual, charge_energy = _solve_matrix(eps, owners, len(names), horizontal=True)
    baseline, base_energy, base_residual, base_charge_energy = _solve_matrix(eps, owners, len(names), horizontal=False)
    finite = bool(np.all(np.isfinite(raw)) and np.all(np.isfinite(baseline)))
    if not finite:
        raise EdgeCellCapacitanceError("non-finite electrostatic result")
    scale = max(float(np.max(np.abs(raw_energy))), 1e-30)
    reciprocity = float(np.max(np.abs(raw - raw.T)) / scale)
    row_sum = float(np.max(np.abs(raw.sum(axis=1))) / scale)
    eigenvalues = np.linalg.eigvalsh(0.5 * (raw + raw.T))
    min_eigenvalue = float(np.min(eigenvalues))
    tol = 2.0e-8
    if max(residual, base_residual) > 2.0e-10:
        raise EdgeCellCapacitanceError("sparse solve residual gate failed")
    if max(charge_energy, base_charge_energy) > tol:
        raise EdgeCellCapacitanceError("charge-versus-energy gate failed")
    if reciprocity > tol or row_sum > tol:
        raise EdgeCellCapacitanceError("Maxwell reciprocity or row-sum gate failed")
    if min_eigenvalue < -scale * tol:
        raise EdgeCellCapacitanceError("raw Maxwell PSD gate failed")
    elapsed_s = perf_counter() - started
    if elapsed_s > problem.max_runtime_s:
        raise EdgeCellCapacitanceError(f"edge-cell runtime gate: {elapsed_s:.3f}s exceeds max_runtime_s={problem.max_runtime_s:g}")
    delta = raw - baseline
    result = EdgeCellResult(
        terminal_names=names,
        raw_maxwell_f_per_m=np.array(raw, copy=True),
        vertical_baseline_f_per_m=np.array(baseline, copy=True),
        delta_maxwell_f_per_m=np.array(delta, copy=True),
        diagnostics=EdgeCellDiagnostics(
            signature=signature, nx=nx, nz=nz, cell_count=nx * nz, unknown_count=int(np.count_nonzero(owners < 0)),
            runtime_model="2-D sparse finite-volume, unit y length; explicit 1-D side asymptotes", elapsed_s=elapsed_s,
            max_linear_residual=max(residual, base_residual),
            charge_energy_relative=max(charge_energy, base_charge_energy), reciprocity_relative=reciprocity, row_sum_relative=row_sum,
            min_eigenvalue_f_per_m=min_eigenvalue, finite=finite,
        ),
    )
    _EDGE_CELL_CACHE[signature] = result
    _EDGE_CELL_CACHE.move_to_end(signature)
    while len(_EDGE_CELL_CACHE) > _MAX_EDGE_CELL_CACHE:
        _EDGE_CELL_CACHE.popitem(last=False)
    return _public_copy(result)


def convergence_check(problem: EdgeCellProblem) -> EdgeCellConvergence:
    """Return h->h/2 and R->2R checks without silently accepting either."""
    coarse = solve_edge_cell(problem)
    h = problem.cell_size_m
    fine_problem = EdgeCellProblem(
        x_min_m=problem.x_min_m, x_max_m=problem.x_max_m, z_min_m=problem.z_min_m, z_max_m=problem.z_max_m,
        cell_size_m=h / 2.0, dielectrics=problem.dielectrics, conductors=problem.conductors,
        reference_terminal=problem.reference_terminal, max_cells=problem.max_cells, max_runtime_s=problem.max_runtime_s,
    )
    fine = solve_edge_cell(fine_problem)
    width = problem.x_max_m - problem.x_min_m
    # A side-touching rectangle explicitly defines the asymptotic conductor
    # state at that boundary and is continued when R grows.  This leaves only
    # internal state transitions as counted physical edges.
    wide_problem = EdgeCellProblem(
        x_min_m=problem.x_min_m - width / 2.0, x_max_m=problem.x_max_m + width / 2.0, z_min_m=problem.z_min_m, z_max_m=problem.z_max_m,
        cell_size_m=h, dielectrics=problem.dielectrics,
        conductors=tuple(
            EdgeCellConductor(c.terminal, problem.x_min_m - width / 2.0 if np.isclose(c.x_min_m, problem.x_min_m) else c.x_min_m,
                              problem.x_max_m + width / 2.0 if np.isclose(c.x_max_m, problem.x_max_m) else c.x_max_m,
                              c.z_min_m, c.z_max_m)
            for c in problem.conductors
        ), reference_terminal=problem.reference_terminal, max_cells=problem.max_cells, max_runtime_s=problem.max_runtime_s,
    )
    wide = solve_edge_cell(wide_problem)
    denom = max(float(np.max(np.abs(fine.delta_maxwell_f_per_m))), 1e-30)
    mesh = float(np.max(np.abs(coarse.delta_maxwell_f_per_m - fine.delta_maxwell_f_per_m)) / denom)
    crop = float(np.max(np.abs(wide.delta_maxwell_f_per_m - coarse.delta_maxwell_f_per_m)) / denom)
    return EdgeCellConvergence(coarse=coarse, fine=fine, wide=wide, mesh_relative=mesh, crop_relative=crop)
