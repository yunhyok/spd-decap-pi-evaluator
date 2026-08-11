"""Research-only FFT pulse-basis electrostatic capacitance prototype.

This module is intentionally not imported by the evaluator.  It is a bounded,
homogeneous-dielectric surface-charge BEM for checking whether artwork fringe
and aperture fields explain a low-frequency capacitance discrepancy.  There
are no fitted constants and it must not be used for production extraction.

Unknowns are pulse charges (C) on occupied uniform XY cells.  The potential
operator is the exact rectangular-cell Green integral (with an analytic self
term), applied as block-Toeplitz convolutions by FFT.  Floating conductors are
included as Lagrange multipliers whose net charge is constrained to zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asinh, isfinite, log, pi, sqrt
from time import perf_counter
from typing import Iterable, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.signal import fftconvolve
from scipy.sparse.linalg import LinearOperator, gmres


EPSILON_0_F_PER_M = 8.854_187_812_8e-12


class FFTBEMError(ValueError):
    """Raised for invalid artwork or a hard numerical/resource gate."""


@dataclass(frozen=True, slots=True)
class UniformXYGrid:
    """Cell-centre grid in metres, with ``mask[y, x]`` indexing."""

    origin_x_m: float
    origin_y_m: float
    pitch_m: float
    shape: tuple[int, int]

    def __post_init__(self) -> None:
        if not (isfinite(self.origin_x_m) and isfinite(self.origin_y_m) and isfinite(self.pitch_m)):
            raise FFTBEMError("grid origin and pitch must be finite")
        if self.pitch_m <= 0.0 or len(self.shape) != 2 or min(self.shape) <= 0:
            raise FFTBEMError("grid pitch and two-dimensional shape must be positive")

    @property
    def cell_area_m2(self) -> float:
        return self.pitch_m * self.pitch_m

    @property
    def cell_count(self) -> int:
        return int(self.shape[0] * self.shape[1])


@dataclass(frozen=True, slots=True)
class SurfaceConductor:
    """One infinitesimally thin conductor surface discretised on ``grid``."""

    name: str
    net: str
    z_m: float
    mask: NDArray[np.bool_]

    def __post_init__(self) -> None:
        mask = np.asarray(self.mask, dtype=bool)
        if not self.name.strip() or not self.net.strip() or not isfinite(self.z_m):
            raise FFTBEMError("surface name/net/z must be finite and non-blank")
        if mask.ndim != 2 or not mask.any():
            raise FFTBEMError("surface mask must be a non-empty two-dimensional boolean array")
        mask = mask.copy()
        mask.setflags(write=False)
        object.__setattr__(self, "mask", mask)


@dataclass(frozen=True, slots=True)
class BEMDiagnostics:
    status: str
    reason: str | None
    unknown_count: int
    grid_cell_count: int
    convolution_blocks: int
    runtime_seconds: float
    estimated_fft_workspace_bytes: int
    iterations: tuple[int, ...]
    relative_residuals: tuple[float, ...]
    potential_condition_proxy: float | None
    reciprocity_error: float | None
    charge_conservation_error_f: float | None
    min_energy_eigenvalue_f: float | None
    mesh_change_fraction: float | None = None


@dataclass(frozen=True, slots=True)
class BEMCapacitanceResult:
    net_names: tuple[str, ...]
    maxwell_capacitance_f: NDArray[np.float64]
    diagnostics: BEMDiagnostics


def rectangular_cell_green_integral(dx_m: float, dy_m: float, dz_m: float, pitch_m: float) -> float:
    """Return ``integral_cell 1/r dA`` for a cell centred at ``(dx,dy,dz)``.

    A closed-form rectangle primitive is used off the self cell.  The singular
    self term has the exact finite square integral, avoiding a hidden fit or a
    point-charge regularisation.
    """

    if pitch_m <= 0.0 or not all(isfinite(value) for value in (dx_m, dy_m, dz_m, pitch_m)):
        raise FFTBEMError("Green integral arguments must be finite and pitch > 0")
    if abs(dx_m) < 1e-18 * pitch_m and abs(dy_m) < 1e-18 * pitch_m and abs(dz_m) < 1e-18 * pitch_m:
        half = pitch_m * 0.5
        return 8.0 * half * asinh(1.0)  # 4[a asinh(b/a)+b asinh(a/b)], a=b

    half = pitch_m * 0.5

    def primitive(x: float, y: float, z: float) -> float:
        radius = sqrt(x * x + y * y + z * z)
        # x*log(y+r) and y*log(x+r) have removable zero-times-log-zero
        # corners at z=0.  The exact limiting contribution is zero.
        first = 0.0 if x == 0.0 else x * log(max(y + radius, np.finfo(float).tiny))
        second = 0.0 if y == 0.0 else y * log(max(x + radius, np.finfo(float).tiny))
        third = 0.0 if z == 0.0 else -z * np.arctan2(x * y, z * radius)
        return first + second + third

    x0, x1 = dx_m - half, dx_m + half
    y0, y1 = dy_m - half, dy_m + half
    value = primitive(x1, y1, dz_m) - primitive(x0, y1, dz_m) - primitive(x1, y0, dz_m) + primitive(x0, y0, dz_m)
    if not isfinite(value) or value <= 0.0:
        raise FFTBEMError("rectangular-cell Green integral was non-positive/non-finite")
    return value


def _canonical(value: str) -> str:
    result = value.strip()
    if not result:
        raise FFTBEMError("net names must not be blank")
    return result.casefold()


class FFTPulseBEM:
    """FFT accelerated pulse BEM with strict resource and numerical gates."""

    def __init__(
        self,
        grid: UniformXYGrid,
        surfaces: Sequence[SurfaceConductor],
        relative_permittivity: float,
        *,
        max_unknowns: int = 80_000,
        max_grid_cells: int = 1_000_000,
        max_fft_workspace_bytes: int = 1_500_000_000,
        max_iterations: int = 800,
        residual_tolerance: float = 2.0e-8,
        condition_proxy_limit: float = 1.0e10,
    ) -> None:
        if not isfinite(relative_permittivity) or relative_permittivity <= 0.0:
            raise FFTBEMError("relative_permittivity must be finite and > 0")
        if not surfaces:
            raise FFTBEMError("at least one conductor surface is required")
        self.grid = grid
        self.surfaces = tuple(surfaces)
        self.relative_permittivity = float(relative_permittivity)
        self.max_unknowns = int(max_unknowns)
        self.max_grid_cells = int(max_grid_cells)
        self.max_fft_workspace_bytes = int(max_fft_workspace_bytes)
        self.max_iterations = int(max_iterations)
        self.residual_tolerance = float(residual_tolerance)
        self.condition_proxy_limit = float(condition_proxy_limit)
        if any(item.mask.shape != grid.shape for item in self.surfaces):
            raise FFTBEMError("all surface masks must match the grid shape")
        # Different nets cannot occupy the same z/cell. Same-net fragments are
        # harmless but duplicate surfaces are rejected because they double count.
        occupied: dict[tuple[float, int, int], str] = {}
        for surface in self.surfaces:
            for y, x in np.argwhere(surface.mask):
                key = (surface.z_m, int(y), int(x))
                prior = occupied.setdefault(key, _canonical(surface.net))
                if prior != _canonical(surface.net):
                    raise FFTBEMError("different nets overlap on one physical surface cell")
        self._positions = tuple(np.argwhere(item.mask) for item in self.surfaces)
        self._offsets = np.cumsum((0,) + tuple(len(item) for item in self._positions))
        self._net_names = tuple(dict.fromkeys(item.net.strip() for item in self.surfaces))
        self._net_index = {_canonical(name): index for index, name in enumerate(self._net_names)}
        self._unknown_net = np.concatenate(
            [np.full(len(pos), self._net_index[_canonical(surface.net)], dtype=int) for surface, pos in zip(self.surfaces, self._positions)]
        )
        self._kernels: dict[float, NDArray[np.float64]] = {}

    @property
    def unknown_count(self) -> int:
        return int(self._offsets[-1])

    @property
    def net_names(self) -> tuple[str, ...]:
        return self._net_names

    def resource_diagnostics(self) -> tuple[int, int]:
        """Return estimated workspace bytes and number of surface blocks."""

        blocks = len({abs(left.z_m - right.z_m) for left in self.surfaces for right in self.surfaces})
        # fftconvolve has several temporary complex arrays. This conservative
        # estimate is deliberately a preflight gate, not a measured allocation.
        padded = (2 * self.grid.shape[0] - 1) * (2 * self.grid.shape[1] - 1)
        return int(blocks * padded * 16 * 5), blocks

    def _preflight(self) -> None:
        workspace, _ = self.resource_diagnostics()
        if self.grid.cell_count > self.max_grid_cells:
            raise FFTBEMError(f"resource gate: grid cells {self.grid.cell_count} exceed {self.max_grid_cells}")
        if self.unknown_count > self.max_unknowns:
            raise FFTBEMError(f"resource gate: pulse unknowns {self.unknown_count} exceed {self.max_unknowns}")
        if workspace > self.max_fft_workspace_bytes:
            raise FFTBEMError(f"resource gate: estimated FFT workspace {workspace} exceeds {self.max_fft_workspace_bytes}")

    def _kernel(self, dz_m: float) -> NDArray[np.float64]:
        key = abs(float(dz_m))
        cached = self._kernels.get(key)
        if cached is not None:
            return cached
        ny, nx = self.grid.shape
        kernel = np.empty((2 * ny - 1, 2 * nx - 1), dtype=np.float64)
        ys = np.arange(-(ny - 1), ny, dtype=float) * self.grid.pitch_m
        xs = np.arange(-(nx - 1), nx, dtype=float) * self.grid.pitch_m
        factor = 1.0 / (4.0 * pi * EPSILON_0_F_PER_M * self.relative_permittivity)
        for iy, dy in enumerate(ys):
            for ix, dx in enumerate(xs):
                kernel[iy, ix] = factor * rectangular_cell_green_integral(dx, dy, key, self.grid.pitch_m)
        kernel.setflags(write=False)
        self._kernels[key] = kernel
        return kernel

    def potential_operator(self) -> LinearOperator:
        """Return the symmetric block-Toeplitz charge-to-potential operator."""

        self._preflight()
        n = self.unknown_count
        area = self.grid.cell_area_m2

        def matvec(charges: NDArray[np.float64]) -> NDArray[np.float64]:
            vector = np.asarray(charges, dtype=float)
            if vector.shape != (n,):
                raise ValueError("charge vector has wrong shape")
            answer = np.zeros(n, dtype=float)
            for target_index, (target, target_pos) in enumerate(zip(self.surfaces, self._positions)):
                field = np.zeros(self.grid.shape, dtype=float)
                for source_index, (source, source_pos) in enumerate(zip(self.surfaces, self._positions)):
                    start, stop = int(self._offsets[source_index]), int(self._offsets[source_index + 1])
                    source_map = np.zeros(self.grid.shape, dtype=float)
                    # K maps density to V; charge unknowns require / area.
                    source_map[source_pos[:, 0], source_pos[:, 1]] = vector[start:stop] / area
                    field += fftconvolve(source_map, self._kernel(target.z_m - source.z_m), mode="same")
                start, stop = int(self._offsets[target_index]), int(self._offsets[target_index + 1])
                answer[start:stop] = field[target_pos[:, 0], target_pos[:, 1]]
            return answer

        return LinearOperator((n, n), matvec=matvec, rmatvec=matvec, dtype=np.float64)

    def _solve_excitation(self, driven: Mapping[str, float], floating: Sequence[str]) -> tuple[NDArray[np.float64], int, float, float, float]:
        operator = self.potential_operator()
        n = self.unknown_count
        float_indices = tuple(self._net_index[_canonical(name)] for name in floating)
        if len(set(float_indices)) != len(float_indices):
            raise FFTBEMError("floating nets must be unique")
        driven_keys = {_canonical(name): float(value) for name, value in driven.items()}
        if set(driven_keys).intersection({_canonical(name) for name in floating}):
            raise FFTBEMError("a conductor cannot be driven and floating")
        target = np.zeros(n, dtype=float)
        for index, net_index in enumerate(self._unknown_net):
            target[index] = driven_keys.get(_canonical(self._net_names[int(net_index)]), 0.0)
        columns = [np.flatnonzero(self._unknown_net == item) for item in float_indices]
        k = len(columns)
        # Scale charge coordinates by the inverse self-potential.  Without
        # this, charge-neutral equations (C) are numerically dwarfed by the
        # equipotential equations (V), and a nominal residual can conceal a
        # materially non-zero floating charge.
        charge_scale = self.grid.cell_area_m2 / self._kernel(0.0)[self.grid.shape[0] - 1, self.grid.shape[1] - 1]

        def saddle_matvec(vector: NDArray[np.float64]) -> NDArray[np.float64]:
            charges = vector[:n] * charge_scale
            multipliers = vector[n:]
            result = np.empty(n + k, dtype=float)
            result[:n] = operator @ charges
            for index, cells in enumerate(columns):
                result[cells] -= multipliers[index]
                result[n + index] = vector[cells].sum()
            return result

        rhs = np.concatenate((target, np.zeros(k, dtype=float)))
        saddle = LinearOperator((n + k, n + k), matvec=saddle_matvec, rmatvec=saddle_matvec, dtype=np.float64)
        iteration = [0]
        def count(_: NDArray[np.float64]) -> None:
            iteration[0] += 1
        solution, info = gmres(saddle, rhs, rtol=self.residual_tolerance, atol=0.0, restart=min(80, n + k), maxiter=self.max_iterations, callback=count, callback_type="legacy")
        residual = float(np.linalg.norm(saddle @ solution - rhs) / max(np.linalg.norm(rhs), 1.0))
        if info != 0 or iteration[0] > self.max_iterations or residual > self.residual_tolerance:
            raise FFTBEMError(f"solver gate: info={info}, iterations={iteration[0]}, relative_residual={residual:.3g}")
        # A transparent, deterministic condition proxy based on diagonal Green
        # scale versus maximum pair interaction. It is a gate against obvious
        # degenerate geometries, not a substitute for a dense condition number.
        diagonal = self._kernel(0.0)[self.grid.shape[0] - 1, self.grid.shape[1] - 1]
        coupled = max(float(np.max(np.abs(self._kernel(abs(left.z_m-right.z_m))))) for left in self.surfaces for right in self.surfaces)
        proxy = coupled / diagonal
        if not isfinite(proxy) or proxy > self.condition_proxy_limit:
            raise FFTBEMError(f"condition gate: potential condition proxy {proxy:.3g}")
        charges = solution[:n] * charge_scale
        floating_error = float(max((abs(charges[cells].sum()) for cells in columns), default=0.0))
        return charges, iteration[0], residual, proxy, floating_error

    def maxwell_capacitance(self, *, floating_nets: Iterable[str] = ()) -> BEMCapacitanceResult:
        """Compute net Maxwell C; selected conductors may be charge-neutral."""

        started = perf_counter()
        workspace, blocks = self.resource_diagnostics()
        try:
            floating = tuple(floating_nets)
            floating_keys = {_canonical(item) for item in floating}
            driven_names = tuple(name for name in self._net_names if _canonical(name) not in floating_keys)
            if not driven_names:
                raise FFTBEMError("at least one driven conductor is required")
            matrix = np.zeros((len(driven_names), len(driven_names)), dtype=float)
            iterations: list[int] = []
            residuals: list[float] = []
            proxies: list[float] = []
            floating_errors: list[float] = []
            for column, name in enumerate(driven_names):
                charges, its, residual, proxy, floating_error = self._solve_excitation({name: 1.0}, floating)
                iterations.append(its); residuals.append(residual); proxies.append(proxy)
                floating_errors.append(floating_error)
                for row, target_name in enumerate(driven_names):
                    net = self._net_index[_canonical(target_name)]
                    matrix[row, column] = charges[self._unknown_net == net].sum()
            reciprocity = float(np.max(np.abs(matrix - matrix.T)))
            matrix = 0.5 * (matrix + matrix.T)  # only removes solver roundoff after the gate below
            # Finite artwork has a physical capacitance to infinity, so row
            # sums of an all-driven Maxwell matrix are not a conservation law.
            # The actual conservation condition here is exactly the stated
            # floating-net constraint, which must close at solver tolerance.
            conservation = float(max(floating_errors, default=0.0)) if floating else None
            eigen_min = float(np.linalg.eigvalsh(matrix).min())
            scale = max(float(np.max(np.abs(matrix))), 1.0e-30)
            if reciprocity > 20.0 * self.residual_tolerance * scale:
                raise FFTBEMError(f"reciprocity gate: {reciprocity:.3g} F")
            if conservation is not None and conservation > 30.0 * self.residual_tolerance * scale:
                raise FFTBEMError(f"charge-conservation gate: {conservation:.3g} F")
            if eigen_min < -30.0 * self.residual_tolerance * scale:
                raise FFTBEMError(f"passivity gate: minimum energy eigenvalue {eigen_min:.3g} F")
            diagnostics = BEMDiagnostics("computed", None, self.unknown_count, self.grid.cell_count, blocks, perf_counter()-started, workspace, tuple(iterations), tuple(residuals), max(proxies), reciprocity, conservation, eigen_min)
            matrix.setflags(write=False)
            return BEMCapacitanceResult(driven_names, matrix, diagnostics)
        except FFTBEMError as exc:
            diagnostics = BEMDiagnostics("blocked_fail_closed", str(exc), self.unknown_count, self.grid.cell_count, blocks, perf_counter()-started, workspace, (), (), None, None, None, None)
            return BEMCapacitanceResult((), np.empty((0, 0), dtype=float), diagnostics)


def shapely_raster_grid(
    artwork: Sequence[tuple[str, str, float, object]], *, pitch_um: float, padding_cells: int = 2
) -> UniformXYGrid:
    """Plan a source-artwork grid without allocating per-surface masks."""

    if pitch_um <= 0.0 or not artwork:
        raise FFTBEMError("artwork and positive pitch_um are required")
    bounds = [item[3].bounds for item in artwork]
    minimum_x = min(item[0] for item in bounds) - padding_cells * pitch_um
    minimum_y = min(item[1] for item in bounds) - padding_cells * pitch_um
    maximum_x = max(item[2] for item in bounds) + padding_cells * pitch_um
    maximum_y = max(item[3] for item in bounds) + padding_cells * pitch_um
    nx = int(np.ceil((maximum_x - minimum_x) / pitch_um)); ny = int(np.ceil((maximum_y - minimum_y) / pitch_um))
    return UniformXYGrid(minimum_x * 1e-6, minimum_y * 1e-6, pitch_um * 1e-6, (ny, nx))


def rasterize_shapely_surfaces(
    artwork: Sequence[tuple[str, str, float, object]], *, pitch_um: float, padding_cells: int = 2
) -> tuple[UniformXYGrid, tuple[SurfaceConductor, ...]]:
    """Rasterise Shapely polygonal artwork by cell-centre inclusion.

    This is exact-source artwork on a disclosed uniform-grid approximation;
    no bounding boxes are substituted for holes or polygons.
    """

    try:
        from shapely import contains_xy
    except ImportError as exc:  # pragma: no cover
        raise FFTBEMError("Shapely 2 is required for artwork rasterisation") from exc
    grid = shapely_raster_grid(artwork, pitch_um=pitch_um, padding_cells=padding_cells)
    nx, ny = grid.shape[1], grid.shape[0]
    minimum_x, minimum_y = grid.origin_x_m * 1e6, grid.origin_y_m * 1e6
    x = minimum_x + (np.arange(nx) + 0.5) * pitch_um
    y = minimum_y + (np.arange(ny) + 0.5) * pitch_um
    xx, yy = np.meshgrid(x, y)
    surfaces = tuple(SurfaceConductor(name, net, z_m, contains_xy(shape, xx, yy)) for name, net, z_m, shape in artwork)
    return grid, surfaces
