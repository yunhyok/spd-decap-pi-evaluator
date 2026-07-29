"""Finite-port rectangular cavity modal solver.

The basis is orthonormal under the area-average inner product.  This keeps the
constant mode equal to one and makes its low-frequency impedance exactly the
parallel-plane capacitance ``1 / (j*w*Cplane)``.  Decap populations are added as
exact-coordinate low-rank outer products and all linear systems are solved by
factorization; an explicit matrix inverse is never formed.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import warnings

import numpy as np
from numpy.typing import ArrayLike, NDArray

from spd_decap_pi._core.models.impedance import ImpedanceModel, frequency_array

try:  # SciPy is a runtime dependency, but the NumPy fallback aids source use.
    from scipy.linalg import LinAlgWarning, lu_factor, lu_solve
except ImportError:  # pragma: no cover - exercised only in minimal environments
    LinAlgWarning = RuntimeWarning
    lu_factor = None
    lu_solve = None


EPSILON_0_F_PER_M = 8.854_187_812_8e-12
MU_0_H_PER_M = 1.256_637_062_12e-6
LIGHT_SPEED_M_PER_S = 299_792_458.0


class ModalSolverError(ValueError):
    """Raised when cavity geometry or a modal solve is invalid."""


@dataclass(frozen=True, slots=True)
class RectangularPlane:
    """One effective rectangular PWR-DGND plane pair."""

    width_m: float
    height_m: float
    separation_m: float
    relative_permittivity: float
    loss_tangent: float = 0.0
    conductivity_s_per_m: float = 5.8e7
    power_thickness_m: float = 35e-6
    ground_thickness_m: float = 35e-6

    def __post_init__(self) -> None:
        positive = {
            "width_m": self.width_m,
            "height_m": self.height_m,
            "separation_m": self.separation_m,
            "relative_permittivity": self.relative_permittivity,
            "conductivity_s_per_m": self.conductivity_s_per_m,
            "power_thickness_m": self.power_thickness_m,
            "ground_thickness_m": self.ground_thickness_m,
        }
        for name, value in positive.items():
            if not np.isfinite(value) or value <= 0.0:
                raise ModalSolverError(f"{name} must be finite and > 0")
        if not np.isfinite(self.loss_tangent) or self.loss_tangent < 0.0:
            raise ModalSolverError("loss_tangent must be finite and >= 0")

    @property
    def area_m2(self) -> float:
        return self.width_m * self.height_m

    @property
    def plane_capacitance_f(self) -> float:
        return (
            EPSILON_0_F_PER_M
            * self.relative_permittivity
            * self.area_m2
            / self.separation_m
        )

    @property
    def sheet_resistance_ohm(self) -> float:
        return 1.0 / (self.conductivity_s_per_m * self.power_thickness_m) + 1.0 / (
            self.conductivity_s_per_m * self.ground_thickness_m
        )

    @property
    def vertical_cutoff_hz(self) -> float:
        return LIGHT_SPEED_M_PER_S / (
            2.0 * self.separation_m * np.sqrt(self.relative_permittivity)
        )

    def resonance_frequency_hz(self, mode_x: int, mode_y: int) -> float:
        if mode_x < 0 or mode_y < 0 or (mode_x == 0 and mode_y == 0):
            raise ModalSolverError("resonance mode indices must be non-negative and non-zero")
        wave_number = np.sqrt(
            (mode_x * np.pi / self.width_m) ** 2
            + (mode_y * np.pi / self.height_m) ** 2
        )
        return float(
            LIGHT_SPEED_M_PER_S
            * wave_number
            / (2.0 * np.pi * np.sqrt(self.relative_permittivity))
        )


@dataclass(frozen=True, slots=True)
class FinitePort:
    """A rectangular finite-area port on the cavity reference plane."""

    x_m: float
    y_m: float
    width_m: float
    height_m: float
    port_id: str = ""

    def __post_init__(self) -> None:
        for name, value in {
            "x_m": self.x_m,
            "y_m": self.y_m,
            "width_m": self.width_m,
            "height_m": self.height_m,
        }.items():
            if not np.isfinite(value):
                raise ModalSolverError(f"{name} must be finite")
        if self.width_m <= 0.0 or self.height_m <= 0.0:
            raise ModalSolverError("finite port dimensions must be > 0")

    def validate_inside(self, plane: RectangularPlane) -> None:
        tolerance = max(plane.width_m, plane.height_m) * 1e-12
        if (
            self.x_m - self.width_m / 2.0 < -tolerance
            or self.x_m + self.width_m / 2.0 > plane.width_m + tolerance
            or self.y_m - self.height_m / 2.0 < -tolerance
            or self.y_m + self.height_m / 2.0 > plane.height_m + tolerance
        ):
            label = f" {self.port_id!r}" if self.port_id else ""
            raise ModalSolverError(f"finite port{label} lies outside the rectangular plane")


@dataclass(frozen=True, slots=True)
class ShuntGroup:
    """Ports sharing exactly the same local frequency-dependent impedance."""

    group_id: str
    ports: tuple[FinitePort, ...]
    network: ImpedanceModel

    def __post_init__(self) -> None:
        if not self.group_id.strip():
            raise ModalSolverError("group_id must not be empty")
        if not self.ports:
            raise ModalSolverError("a shunt group must contain at least one port")


@dataclass(frozen=True, slots=True)
class DeviceBranch:
    """One Device P/G finite port and its differential bump/via path."""

    branch_id: str
    port: FinitePort
    series_path: ImpedanceModel

    def __post_init__(self) -> None:
        if not self.branch_id.strip():
            raise ModalSolverError("branch_id must not be empty")


@dataclass(frozen=True, slots=True)
class DeviceConnection:
    """Device branches tied to one external P/G supernode."""

    branches: tuple[DeviceBranch, ...]

    def __post_init__(self) -> None:
        if not self.branches:
            raise ModalSolverError("a device connection needs at least one branch")
        identifiers = [branch.branch_id for branch in self.branches]
        if len(set(identifiers)) != len(identifiers):
            raise ModalSolverError("device branch_id values must be unique")


@dataclass(frozen=True, slots=True)
class PreparedDeviceSystem:
    """Placement-independent numerical terms for repeated Device solves."""

    plane: RectangularPlane
    modes: tuple[tuple[int, int], ...]
    frequencies_hz: NDArray[np.float64]
    plane_admittance: NDArray[np.complex128]
    branch_data: tuple[
        tuple[
            NDArray[np.float64],
            NDArray[np.float64],
            int,
            NDArray[np.complex128],
        ],
        ...,
    ]

    def __post_init__(self) -> None:
        frequencies = frequency_array(self.frequencies_hz).copy()
        plane_admittance = np.asarray(
            self.plane_admittance, dtype=np.complex128
        ).copy()
        if plane_admittance.ndim != 2 or plane_admittance.shape[0] != frequencies.size:
            raise ModalSolverError(
                "prepared plane admittance must have shape (frequency count, mode count)"
            )
        mode_count = int(plane_admittance.shape[1])
        if mode_count < 1 or not np.all(np.isfinite(plane_admittance)):
            raise ModalSolverError("prepared plane admittance must be finite and non-empty")
        modes = tuple((int(mode_x), int(mode_y)) for mode_x, mode_y in self.modes)
        if len(modes) != mode_count or len(set(modes)) != mode_count:
            raise ModalSolverError(
                "prepared modal basis must contain one unique mode per admittance column"
            )

        copied_branches = []
        for overlap, basis_sum, branch_count, admittance in self.branch_data:
            copied_overlap = np.asarray(overlap, dtype=np.float64).copy()
            copied_basis = np.asarray(basis_sum, dtype=np.float64).copy()
            copied_admittance = np.asarray(admittance, dtype=np.complex128).copy()
            if copied_overlap.shape != (mode_count, mode_count):
                raise ModalSolverError("prepared Device overlap has an invalid mode shape")
            if copied_basis.shape != (mode_count,):
                raise ModalSolverError("prepared Device basis sum has an invalid mode shape")
            if int(branch_count) < 1:
                raise ModalSolverError("prepared Device branch count must be >= 1")
            if copied_admittance.shape != frequencies.shape:
                raise ModalSolverError(
                    "prepared Device admittance must match the frequency grid"
                )
            if not all(
                np.all(np.isfinite(values))
                for values in (copied_overlap, copied_basis, copied_admittance)
            ):
                raise ModalSolverError("prepared Device terms must be finite")
            copied_overlap.setflags(write=False)
            copied_basis.setflags(write=False)
            copied_admittance.setflags(write=False)
            copied_branches.append(
                (
                    copied_overlap,
                    copied_basis,
                    int(branch_count),
                    copied_admittance,
                )
            )

        frequencies.setflags(write=False)
        plane_admittance.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "modes", modes)
        object.__setattr__(self, "plane_admittance", plane_admittance)
        object.__setattr__(self, "branch_data", tuple(copied_branches))


@dataclass(frozen=True, slots=True)
class SolverDiagnostics:
    condition_numbers: NDArray[np.float64]
    relative_residuals: NDArray[np.float64]
    mode_count: int

    @property
    def max_condition_number(self) -> float:
        return float(np.max(self.condition_numbers))

    @property
    def max_relative_residual(self) -> float:
        return float(np.max(self.relative_residuals))


@dataclass(frozen=True, slots=True)
class ModalSolveResult:
    frequencies_hz: NDArray[np.float64]
    impedance_ohm: NDArray[np.complex128]
    diagnostics: SolverDiagnostics


@dataclass(frozen=True, slots=True)
class ShuntLeaveOneOutSolveResult:
    """Baseline and exact one-shunt-removed device impedance curves.

    ``without_impedance_ohm[row]`` corresponds to ``port_ids[row]``.  The
    batched solver uses the same complex-symmetric MNA equations as
    :meth:`RectangularCavitySolver.solve_device`; it only reuses each baseline
    factorization through an algebraically exact rank-one downdate.
    """

    frequencies_hz: NDArray[np.float64]
    port_ids: tuple[str, ...]
    baseline_impedance_ohm: NDArray[np.complex128]
    without_impedance_ohm: NDArray[np.complex128]

    def __post_init__(self) -> None:
        frequencies = frequency_array(self.frequencies_hz).copy()
        baseline = np.asarray(self.baseline_impedance_ohm, dtype=np.complex128).copy()
        without = np.asarray(self.without_impedance_ohm, dtype=np.complex128).copy()
        if baseline.shape != frequencies.shape:
            raise ModalSolverError("baseline leave-one-out impedance must match frequency")
        if without.shape != (len(self.port_ids), frequencies.size):
            raise ModalSolverError(
                "leave-one-out impedance must have shape (port count, frequency count)"
            )
        if len(set(self.port_ids)) != len(self.port_ids):
            raise ModalSolverError("leave-one-out shunt port_id values must be unique")
        if not np.all(np.isfinite(baseline.real)) or not np.all(np.isfinite(baseline.imag)):
            raise ModalSolverError("baseline leave-one-out solve produced non-finite impedance")
        if not np.all(np.isfinite(without.real)) or not np.all(np.isfinite(without.imag)):
            raise ModalSolverError("leave-one-out solve produced non-finite impedance")
        frequencies.setflags(write=False)
        baseline.setflags(write=False)
        without.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "baseline_impedance_ohm", baseline)
        object.__setattr__(self, "without_impedance_ohm", without)


class RectangularCavitySolver:
    """Galerkin modal solver for one rectangular effective plane pair."""

    def __init__(
        self,
        plane: RectangularPlane,
        *,
        max_mode_x: int = 6,
        max_mode_y: int = 6,
        mode_count: int | None = None,
    ) -> None:
        if max_mode_x < 0 or max_mode_y < 0:
            raise ModalSolverError("maximum mode indices must be >= 0")
        modes = [
            (mode_x, mode_y)
            for mode_x in range(max_mode_x + 1)
            for mode_y in range(max_mode_y + 1)
        ]
        modes.sort(
            key=lambda pair: (
                (pair[0] * np.pi / plane.width_m) ** 2
                + (pair[1] * np.pi / plane.height_m) ** 2,
                pair[0],
                pair[1],
            )
        )
        if mode_count is not None:
            if mode_count < 1:
                raise ModalSolverError("mode_count must be >= 1")
            modes = modes[:mode_count]
        self.plane = plane
        self.modes: tuple[tuple[int, int], ...] = tuple(modes)
        self._mode_index = {mode: index for index, mode in enumerate(self.modes)}
        self._wave_numbers_squared = np.asarray(
            [
                (mode_x * np.pi / plane.width_m) ** 2
                + (mode_y * np.pi / plane.height_m) ** 2
                for mode_x, mode_y in self.modes
            ],
            dtype=np.float64,
        )

    @property
    def mode_count(self) -> int:
        return len(self.modes)

    def mode_index(self, mode_x: int, mode_y: int) -> int:
        try:
            return self._mode_index[(mode_x, mode_y)]
        except KeyError as exc:
            raise ModalSolverError(f"mode ({mode_x}, {mode_y}) is not in this basis") from exc

    def basis_vector(self, port: FinitePort) -> NDArray[np.float64]:
        """Area-averaged cosine basis, including rectangular sinc weights."""

        port.validate_inside(self.plane)
        result = np.empty(self.mode_count, dtype=np.float64)
        for index, (mode_x, mode_y) in enumerate(self.modes):
            normalization = (1.0 if mode_x == 0 else np.sqrt(2.0)) * (
                1.0 if mode_y == 0 else np.sqrt(2.0)
            )
            x_weight = np.sinc(mode_x * port.width_m / (2.0 * self.plane.width_m))
            y_weight = np.sinc(mode_y * port.height_m / (2.0 * self.plane.height_m))
            result[index] = (
                normalization
                * np.cos(mode_x * np.pi * port.x_m / self.plane.width_m)
                * np.cos(mode_y * np.pi * port.y_m / self.plane.height_m)
                * x_weight
                * y_weight
            )
        return result

    def population_matrix(self, group: ShuntGroup) -> NDArray[np.float64]:
        """Return B whose columns are exact-coordinate finite-port basis vectors."""

        for port in group.ports:
            port.validate_inside(self.plane)
        x = np.fromiter((port.x_m for port in group.ports), dtype=np.float64)
        y = np.fromiter((port.y_m for port in group.ports), dtype=np.float64)
        width = np.fromiter((port.width_m for port in group.ports), dtype=np.float64)
        height = np.fromiter((port.height_m for port in group.ports), dtype=np.float64)
        mode_x = np.asarray([item[0] for item in self.modes], dtype=np.float64)[:, None]
        mode_y = np.asarray([item[1] for item in self.modes], dtype=np.float64)[:, None]
        normalization = np.where(mode_x == 0.0, 1.0, np.sqrt(2.0)) * np.where(
            mode_y == 0.0, 1.0, np.sqrt(2.0)
        )
        x_basis = np.cos(mode_x * np.pi * x[None, :] / self.plane.width_m)
        y_basis = np.cos(mode_y * np.pi * y[None, :] / self.plane.height_m)
        x_weight = np.sinc(mode_x * width[None, :] / (2.0 * self.plane.width_m))
        y_weight = np.sinc(mode_y * height[None, :] / (2.0 * self.plane.height_m))
        return np.asarray(
            normalization * x_basis * y_basis * x_weight * y_weight,
            dtype=np.float64,
        )

    def overlap_matrix(self, group: ShuntGroup) -> NDArray[np.float64]:
        population = self.population_matrix(group)
        return population @ population.T

    def modal_impedance(self, frequencies_hz: ArrayLike) -> NDArray[np.complex128]:
        """Return uncoupled modal impedance coefficients in ohms."""

        frequencies = frequency_array(frequencies_hz)
        omega = 2.0 * np.pi * frequencies
        complex_permittivity = (
            EPSILON_0_F_PER_M
            * self.plane.relative_permittivity
            * (1.0 - 1j * self.plane.loss_tangent)
        )
        shunt_admittance_per_area = 1j * omega * complex_permittivity / self.plane.separation_m
        sheet_impedance = self.plane.sheet_resistance_ohm + 1j * omega * MU_0_H_PER_M * self.plane.separation_m
        propagation_squared = -sheet_impedance * shunt_admittance_per_area
        denominator = self._wave_numbers_squared[None, :] - propagation_squared[:, None]
        with np.errstate(divide="ignore", invalid="ignore"):
            impedance = sheet_impedance[:, None] / self.plane.area_m2 / denominator
        constant_index = self._mode_index.get((0, 0))
        if constant_index is not None:
            impedance[:, constant_index] = 1.0 / (
                self.plane.area_m2 * shunt_admittance_per_area
            )
        if not np.all(np.isfinite(impedance.real)) or not np.all(np.isfinite(impedance.imag)):
            raise ModalSolverError(
                "modal impedance is singular; add physical conductor/dielectric loss or avoid an exact lossless resonance"
            )
        return np.asarray(impedance, dtype=np.complex128)

    def _shunt_data(
        self,
        frequencies: NDArray[np.float64],
        shunts: tuple[ShuntGroup, ...],
    ) -> list[tuple[NDArray[np.float64], NDArray[np.complex128]]]:
        data: list[tuple[NDArray[np.float64], NDArray[np.complex128]]] = []
        for group in shunts:
            impedance = np.asarray(group.network.impedance(frequencies), dtype=np.complex128)
            if impedance.shape != frequencies.shape or not np.all(np.isfinite(impedance)):
                raise ModalSolverError(f"shunt group {group.group_id!r} returned invalid impedance")
            if np.any(np.abs(impedance) < np.finfo(float).tiny):
                raise ModalSolverError(f"shunt group {group.group_id!r} returned zero impedance")
            data.append((self.overlap_matrix(group), 1.0 / impedance))
        return data

    def _base_matrix(
        self,
        frequency_index: int,
        plane_admittance: NDArray[np.complex128],
        shunt_data: list[tuple[NDArray[np.float64], NDArray[np.complex128]]],
    ) -> NDArray[np.complex128]:
        matrix = np.diag(plane_admittance[frequency_index]).astype(np.complex128)
        for overlap, admittance in shunt_data:
            matrix += admittance[frequency_index] * overlap
        return matrix

    def solve_ideal_port(
        self,
        frequencies_hz: ArrayLike,
        port: FinitePort,
        *,
        shunts: tuple[ShuntGroup, ...] = (),
    ) -> ModalSolveResult:
        """Solve Zii for an ideal finite-area source directly at the plane."""

        frequencies = frequency_array(frequencies_hz)
        source = self.basis_vector(port).astype(np.complex128)
        plane_admittance = 1.0 / self.modal_impedance(frequencies)
        shunt_data = self._shunt_data(frequencies, shunts)
        impedance = np.empty(frequencies.shape, dtype=np.complex128)
        conditions = np.empty(frequencies.shape, dtype=np.float64)
        residuals = np.empty(frequencies.shape, dtype=np.float64)
        for index in range(frequencies.size):
            matrix = self._base_matrix(index, plane_admittance, shunt_data)
            solution = _factorized_solve(matrix, source, frequencies[index])
            impedance[index] = source.T @ solution
            conditions[index] = np.linalg.cond(matrix)
            residuals[index] = _relative_residual(matrix, solution, source)
        return _result(frequencies, impedance, conditions, residuals, self.mode_count)

    def _device_branch_data(
        self,
        frequencies: NDArray[np.float64],
        device: DeviceConnection,
    ) -> list[
        tuple[
            NDArray[np.float64],
            NDArray[np.float64],
            int,
            NDArray[np.complex128],
        ]
    ]:
        """Precompute grouped Device branch contributions for one frequency grid."""

        # Branches that share the same series-path object also share exactly the
        # same admittance at every frequency.  Sum their Galerkin terms once.
        grouped_basis: dict[int, tuple[ImpedanceModel, list[NDArray[np.float64]]]] = {}
        for branch in device.branches:
            key = id(branch.series_path)
            if key not in grouped_basis:
                grouped_basis[key] = (branch.series_path, [])
            grouped_basis[key][1].append(self.basis_vector(branch.port))

        branch_data: list[
            tuple[
                NDArray[np.float64],
                NDArray[np.float64],
                int,
                NDArray[np.complex128],
            ]
        ] = []
        for series_path, basis_vectors in grouped_basis.values():
            impedance = np.asarray(
                series_path.impedance(frequencies), dtype=np.complex128
            )
            if impedance.shape != frequencies.shape or not np.all(np.isfinite(impedance)):
                raise ModalSolverError("device branch group returned invalid impedance")
            if np.any(np.abs(impedance) < np.finfo(float).tiny):
                raise ModalSolverError("device branch group returned zero impedance")
            population = np.column_stack(basis_vectors)
            branch_data.append(
                (
                    population @ population.T,
                    np.sum(population, axis=1),
                    population.shape[1],
                    1.0 / impedance,
                )
            )
        return branch_data

    def _device_system_matrix(
        self,
        frequency_index: int,
        plane_admittance: NDArray[np.complex128],
        shunt_data: list[tuple[NDArray[np.float64], NDArray[np.complex128]]],
        branch_data: list[
            tuple[
                NDArray[np.float64],
                NDArray[np.float64],
                int,
                NDArray[np.complex128],
            ]
        ],
    ) -> NDArray[np.complex128]:
        size = self.mode_count + 1
        matrix = np.zeros((size, size), dtype=np.complex128)
        matrix[:-1, :-1] = self._base_matrix(
            frequency_index, plane_admittance, shunt_data
        )
        for overlap, basis_sum, branch_count, admittance_values in branch_data:
            admittance = admittance_values[frequency_index]
            matrix[:-1, :-1] += admittance * overlap
            matrix[:-1, -1] -= admittance * basis_sum
            matrix[-1, :-1] -= admittance * basis_sum
            matrix[-1, -1] += admittance * branch_count
        return matrix

    def solve_device(
        self,
        frequencies_hz: ArrayLike,
        device: DeviceConnection,
        *,
        shunts: tuple[ShuntGroup, ...] = (),
        max_workers: int = 1,
    ) -> ModalSolveResult:
        """Solve Zii at the external Device P/G supernode.

        Branch admittances are included in the augmented MNA system, so current
        division among multiple bump/via branches follows the coupled cavity
        voltages instead of an assumed equal split.
        """

        prepared = self.prepare_device(frequencies_hz, device)
        return self.solve_prepared_device(
            prepared,
            shunts=shunts,
            max_workers=max_workers,
        )

    def prepare_device(
        self,
        frequencies_hz: ArrayLike,
        device: DeviceConnection,
    ) -> PreparedDeviceSystem:
        """Compile modal plane and Device branch terms once for repeated shunts."""

        frequencies = frequency_array(frequencies_hz)
        return PreparedDeviceSystem(
            plane=self.plane,
            modes=self.modes,
            frequencies_hz=frequencies,
            plane_admittance=1.0 / self.modal_impedance(frequencies),
            branch_data=tuple(self._device_branch_data(frequencies, device)),
        )

    def solve_prepared_device(
        self,
        prepared: PreparedDeviceSystem,
        *,
        shunts: tuple[ShuntGroup, ...] = (),
        max_workers: int = 1,
    ) -> ModalSolveResult:
        """Solve new shunt placements with a prepared plane/Device kernel."""

        if max_workers < 1:
            raise ModalSolverError("max_workers must be >= 1")
        if prepared.plane != self.plane:
            raise ModalSolverError("prepared Device plane does not match this solver")
        if prepared.modes != self.modes:
            raise ModalSolverError(
                "prepared Device modal basis does not match this solver"
            )
        frequencies = prepared.frequencies_hz
        plane_admittance = prepared.plane_admittance
        branch_data = list(prepared.branch_data)
        shunt_data = self._shunt_data(frequencies, shunts)

        size = self.mode_count + 1
        rhs = np.zeros(size, dtype=np.complex128)
        rhs[-1] = 1.0
        impedance = np.empty(frequencies.shape, dtype=np.complex128)
        conditions = np.empty(frequencies.shape, dtype=np.float64)
        residuals = np.empty(frequencies.shape, dtype=np.float64)

        def solve_frequency(
            index: int,
        ) -> tuple[int, complex, float, float]:
            matrix = self._device_system_matrix(
                index, plane_admittance, shunt_data, branch_data
            )
            solution = _factorized_solve(matrix, rhs, frequencies[index])
            return (
                index,
                complex(solution[-1]),
                float(np.linalg.cond(matrix)),
                _relative_residual(matrix, solution, rhs),
            )

        worker_count = min(max_workers, int(frequencies.size))
        if worker_count == 1:
            results = (solve_frequency(index) for index in range(frequencies.size))
            for solved_index, z_value, condition, residual in results:
                impedance[solved_index] = z_value
                conditions[solved_index] = condition
                residuals[solved_index] = residual
        else:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="mlo-evaluation",
            ) as executor:
                futures = [
                    executor.submit(solve_frequency, index)
                    for index in range(frequencies.size)
                ]
                for future in as_completed(futures):
                    solved_index, z_value, condition, residual = future.result()
                    impedance[solved_index] = z_value
                    conditions[solved_index] = condition
                    residuals[solved_index] = residual
        return _result(frequencies, impedance, conditions, residuals, self.mode_count)

    def solve_device_shunt_leave_one_out(
        self,
        frequencies_hz: ArrayLike,
        device: DeviceConnection,
        *,
        shunts: tuple[ShuntGroup, ...] = (),
        max_workers: int = 1,
        progress: Callable[[int, int], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ShuntLeaveOneOutSolveResult:
        """Solve every one-shunt-removed case from one factorization per frequency.

        Removing one finite shunt port changes the complex-symmetric Device MNA
        matrix from ``A`` to ``A - y*u*u.T``.  A batched multi-RHS solve obtains
        ``A^-1*u`` for every port, and the Sherman-Morrison downdate then returns
        the same Zii as an independent solve.  Near-singular downdates fall back
        to a direct factorization of the modified matrix.
        """

        frequencies = frequency_array(frequencies_hz)
        if max_workers < 1:
            raise ModalSolverError("max_workers must be >= 1")
        report = progress or (lambda _completed, _total: None)
        cancelled = is_cancelled or (lambda: False)
        if cancelled():
            raise RuntimeError("leave-one-out calculation cancelled")

        port_ids: list[str] = []
        population_parts: list[NDArray[np.float64]] = []
        admittance_parts: list[NDArray[np.complex128]] = []
        for group in shunts:
            population = self.population_matrix(group)
            impedance = np.asarray(
                group.network.impedance(frequencies), dtype=np.complex128
            )
            if impedance.shape != frequencies.shape or not np.all(np.isfinite(impedance)):
                raise ModalSolverError(
                    f"shunt group {group.group_id!r} returned invalid impedance"
                )
            if np.any(np.abs(impedance) < np.finfo(float).tiny):
                raise ModalSolverError(
                    f"shunt group {group.group_id!r} returned zero impedance"
                )
            port_ids.extend(port.port_id for port in group.ports)
            population_parts.append(population)
            admittance_parts.append(
                np.repeat((1.0 / impedance)[:, None], len(group.ports), axis=1)
            )
        if any(not port_id for port_id in port_ids):
            raise ModalSolverError(
                "leave-one-out sensitivity requires a non-empty port_id for every shunt"
            )
        if len(set(port_ids)) != len(port_ids):
            raise ModalSolverError(
                "leave-one-out sensitivity requires unique shunt port_id values"
            )

        candidate_count = len(port_ids)
        if candidate_count == 0:
            baseline = self.solve_device(frequencies, device, shunts=shunts)
            return ShuntLeaveOneOutSolveResult(
                frequencies,
                (),
                baseline.impedance_ohm,
                np.empty((0, frequencies.size), dtype=np.complex128),
            )

        population = np.column_stack(population_parts)
        candidate_admittance = np.column_stack(admittance_parts)
        size = self.mode_count + 1
        update_vectors = np.zeros((size, candidate_count), dtype=np.complex128)
        update_vectors[:-1, :] = population
        rhs = np.zeros(size, dtype=np.complex128)
        rhs[-1] = 1.0
        all_rhs = np.column_stack((rhs, update_vectors))

        plane_admittance = 1.0 / self.modal_impedance(frequencies)
        shunt_data = self._shunt_data(frequencies, shunts)
        branch_data = self._device_branch_data(frequencies, device)
        baseline_impedance = np.empty(frequencies.shape, dtype=np.complex128)
        without_impedance = np.empty(
            (candidate_count, frequencies.size), dtype=np.complex128
        )

        def solve_frequency(index: int) -> tuple[int, complex, NDArray[np.complex128]]:
            matrix = self._device_system_matrix(
                index, plane_admittance, shunt_data, branch_data
            )
            solutions = _factorized_solve_many(matrix, all_rhs, frequencies[index])
            baseline_solution = solutions[:, 0]
            update_solutions = solutions[:, 1:]
            # The MNA matrix is complex-symmetric, not Hermitian.  These are
            # ordinary transposes and must not conjugate either factor.
            coupling = update_vectors.T @ baseline_solution
            self_response = np.sum(update_vectors * update_solutions, axis=0)
            admittance = candidate_admittance[index]
            denominator = 1.0 - admittance * self_response
            scale = np.maximum(1.0, np.abs(admittance * self_response))
            unstable = (
                ~np.isfinite(denominator)
                | (np.abs(denominator) <= 1.0e-10 * scale)
            )
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                without = baseline_solution[-1] + (
                    admittance * coupling * coupling / denominator
                )
            unstable |= ~np.isfinite(without)
            for candidate_index in np.flatnonzero(unstable):
                vector = update_vectors[:, candidate_index]
                modified = matrix - admittance[candidate_index] * np.outer(
                    vector, vector
                )
                without[candidate_index] = _factorized_solve(
                    modified, rhs, frequencies[index]
                )[-1]
            return index, complex(baseline_solution[-1]), np.asarray(without)

        completed = 0
        worker_count = min(max_workers, int(frequencies.size))
        if worker_count == 1:
            for index in range(frequencies.size):
                if cancelled():
                    raise RuntimeError("leave-one-out calculation cancelled")
                solved_index, baseline_value, without = solve_frequency(index)
                baseline_impedance[solved_index] = baseline_value
                without_impedance[:, solved_index] = without
                completed += 1
                report(completed, int(frequencies.size))
        else:
            executor = ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="mlo-sensitivity",
            )
            futures = [
                executor.submit(solve_frequency, index)
                for index in range(frequencies.size)
            ]
            try:
                for future in as_completed(futures):
                    if cancelled():
                        for pending in futures:
                            pending.cancel()
                        raise RuntimeError("leave-one-out calculation cancelled")
                    solved_index, baseline_value, without = future.result()
                    baseline_impedance[solved_index] = baseline_value
                    without_impedance[:, solved_index] = without
                    completed += 1
                    report(completed, int(frequencies.size))
            finally:
                executor.shutdown(wait=True, cancel_futures=True)

        return ShuntLeaveOneOutSolveResult(
            frequencies,
            tuple(port_ids),
            baseline_impedance,
            without_impedance,
        )


def _factorized_solve_many(
    matrix: NDArray[np.complex128],
    rhs: NDArray[np.complex128],
    frequency_hz: float,
) -> NDArray[np.complex128]:
    """Solve one matrix against multiple RHS columns with one factorization."""

    try:
        if lu_factor is not None and lu_solve is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("error", LinAlgWarning)
                factor, pivots = lu_factor(matrix, check_finite=False)
                solution = lu_solve((factor, pivots), rhs, check_finite=False)
        else:  # pragma: no cover - normal packaged runtime includes SciPy
            solution = np.linalg.solve(matrix, rhs)
    except (np.linalg.LinAlgError, LinAlgWarning, ValueError) as exc:
        raise ModalSolverError(f"singular modal system at {frequency_hz:g} Hz") from exc
    if not np.all(np.isfinite(solution)):
        raise ModalSolverError(f"non-finite modal solution at {frequency_hz:g} Hz")
    return np.asarray(solution, dtype=np.complex128)


def _factorized_solve(
    matrix: NDArray[np.complex128], rhs: NDArray[np.complex128], frequency_hz: float
) -> NDArray[np.complex128]:
    try:
        if lu_factor is not None and lu_solve is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("error", LinAlgWarning)
                factor, pivots = lu_factor(matrix, check_finite=False)
                solution = lu_solve((factor, pivots), rhs, check_finite=False)
        else:  # pragma: no cover - normal packaged runtime includes SciPy
            solution = np.linalg.solve(matrix, rhs)
    except (np.linalg.LinAlgError, LinAlgWarning, ValueError) as exc:
        raise ModalSolverError(f"singular modal system at {frequency_hz:g} Hz") from exc
    if not np.all(np.isfinite(solution)):
        raise ModalSolverError(f"non-finite modal solution at {frequency_hz:g} Hz")
    return np.asarray(solution, dtype=np.complex128)


def _relative_residual(
    matrix: NDArray[np.complex128],
    solution: NDArray[np.complex128],
    rhs: NDArray[np.complex128],
) -> float:
    numerator = np.linalg.norm(matrix @ solution - rhs)
    denominator = np.linalg.norm(matrix) * np.linalg.norm(solution) + np.linalg.norm(rhs)
    return float(numerator / denominator) if denominator else 0.0


def _result(
    frequencies: NDArray[np.float64],
    impedance: NDArray[np.complex128],
    conditions: NDArray[np.float64],
    residuals: NDArray[np.float64],
    mode_count: int,
) -> ModalSolveResult:
    if not np.all(np.isfinite(impedance)):
        raise ModalSolverError("solver produced non-finite input impedance")
    return ModalSolveResult(
        frequencies_hz=frequencies.copy(),
        impedance_ohm=impedance,
        diagnostics=SolverDiagnostics(conditions, residuals, mode_count),
    )
