"""Experimental exact-artwork, differential multilayer plane assembly.

This module is deliberately separate from the production modal evaluator.  It
is the small, auditable foundation required before an SPD plane can be coupled
to a terminal/via formulation: artwork is clipped with Shapely before any raster
quantity is formed, and every connected conductor fragment receives its own
unknown.  In particular, two nets that happen to occupy the same raster cell
are *not* unioned.

The lateral part follows the MFDM gap-loop construction.  Each exact shared
edge is partitioned into maximal strips with a constant conductor signature
and is stamped as ``G @ inv(K) @ G.T`` *per strip*.  It is therefore unsafe to
collapse different strip signatures before inverting ``K``.  The returned
matrix is an unreduced nodal *differential stamp*.  MFDM loop equations have
one common-potential null per active raster-column vertical group, not one
global common mode.  This stamp is therefore not an absolute-node global-MNA
block; its explicit nullspace/projection metadata must be consumed by a future
compatible mixed formulation.

Scope is intentionally narrow: rectangular bounded meshes and adjacent-layer
dielectrics are supported.  Aperture/fringe fields, non-adjacent coupling and
terminal/via attachment belong in a later differential-to-MNA formulation.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from math import ceil, floor, pi
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy import sparse
from scipy.linalg import LinAlgWarning, lu_factor, lu_solve
from scipy.sparse.linalg import eigsh
import warnings

from .mfdm import EPSILON_0_F_PER_M, MU_0_H_PER_M, copper_two_face_surface_impedance
from .modal import DielectricDispersion, DielectricLayer


class SurfacePatchPlaneError(ValueError):
    """Raised when exact artwork cannot be represented without a topology guess."""


def _readonly(value: Any, *, dtype: Any) -> NDArray[Any]:
    output = np.array(value, dtype=dtype, copy=True)
    output.setflags(write=False)
    return output


def _immutable_sparse_copy(value: sparse.spmatrix) -> sparse.csc_matrix:
    result = sparse.csc_matrix(value, copy=True)
    result.sum_duplicates()
    result.eliminate_zeros()
    result.data.setflags(write=False)
    result.indices.setflags(write=False)
    result.indptr.setflags(write=False)
    return result


def _positive(name: str, value: float, *, zero: bool = False) -> None:
    try:
        finite_value = float(value)
    except (TypeError, ValueError) as exc:
        raise SurfacePatchPlaneError(f"{name} must be a finite number") from exc
    if not np.isfinite(finite_value) or finite_value < 0.0 or (not zero and finite_value == 0.0):
        raise SurfacePatchPlaneError(f"{name} must be finite and {'>= 0' if zero else '> 0'}")


_MAX_COUPLED_FACTOR_CACHE = 128
_MAX_COUPLED_CONDITION = 1.0e12
_COUPLED_RESIDUAL_LIMIT = 1.0e-11
_RECIPROCITY_LIMIT = 1.0e-10
_NULLSPACE_LIMIT = 1.0e-10


@dataclass(frozen=True, slots=True)
class SurfacePatchArtwork:
    """One same-net artwork collection on one physical conductor layer."""

    layer: str
    net: str
    geometry_um: Any

    def __post_init__(self) -> None:
        if not self.layer.strip() or not self.net.strip():
            raise SurfacePatchPlaneError("artwork layer and net must not be empty")
        if self.geometry_um is None or bool(getattr(self.geometry_um, "is_empty", True)):
            raise SurfacePatchPlaneError("artwork geometry must not be empty")
        if not bool(getattr(self.geometry_um, "is_valid", False)) or float(getattr(self.geometry_um, "area", 0.0)) <= 0.0:
            raise SurfacePatchPlaneError("artwork geometry must be valid and have positive area")


@dataclass(frozen=True, slots=True)
class SurfacePatchConductor:
    conductivity_s_per_m: float = 5.8e7
    thickness_m: float = 35e-6
    provenance: str | None = None

    def __post_init__(self) -> None:
        _positive("conductivity_s_per_m", self.conductivity_s_per_m)
        _positive("thickness_m", self.thickness_m)
        if self.provenance is not None and not self.provenance.strip():
            raise SurfacePatchPlaneError("conductor provenance must be non-empty when supplied")


@dataclass(frozen=True, slots=True)
class SurfacePatchDielectric:
    """One source-proven dielectric row between consecutive layer surfaces."""

    upper_layer: str
    lower_layer: str
    separation_m: float
    relative_permittivity: float
    loss_tangent: float = 0.0
    dispersion: DielectricDispersion | None = None
    dielectric_layers: tuple[DielectricLayer, ...] = ()
    provenance: str | None = None

    def __post_init__(self) -> None:
        if not self.upper_layer.strip() or not self.lower_layer.strip() or self.upper_layer == self.lower_layer:
            raise SurfacePatchPlaneError("dielectric needs two distinct non-empty layers")
        _positive("separation_m", self.separation_m)
        _positive("relative_permittivity", self.relative_permittivity)
        _positive("loss_tangent", self.loss_tangent, zero=True)
        if self.provenance is not None and not self.provenance.strip():
            raise SurfacePatchPlaneError("dielectric provenance must be non-empty when supplied")
        layers = tuple(self.dielectric_layers)
        if self.dispersion is not None and layers:
            raise SurfacePatchPlaneError("use either dispersion or dielectric_layers, not both")
        if layers and not np.isclose(
            sum(layer.thickness_m for layer in layers),
            self.separation_m,
            rtol=1.0e-12,
            atol=self.separation_m * 1.0e-15,
        ):
            raise SurfacePatchPlaneError("series dielectric layer thicknesses must sum to separation_m")
        object.__setattr__(self, "dielectric_layers", layers)


@dataclass(frozen=True, slots=True)
class SurfacePatchNode:
    """A connected conductor fragment in a single mesh cell."""

    index: int
    layer: str
    net: str
    row: int
    column: int
    fragment: int
    area_m2: float


@dataclass(frozen=True, slots=True)
class SurfacePatchFinitePort:
    """A source-proven finite launch/observation footprint on one conductor.

    The footprint is integrated over exact clipped mesh fragments.  It is not
    a point sample and it deliberately has no implicit return; callers retain
    the differential/MNA reference convention outside this local operator.
    """

    port_id: str
    layer: str
    net: str
    geometry_um: Any
    provenance: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (self.port_id, self.layer, self.net, self.provenance)):
            raise SurfacePatchPlaneError("finite port needs non-empty id, layer, net and source provenance")
        geometry = self.geometry_um
        if geometry is None or bool(getattr(geometry, "is_empty", True)):
            raise SurfacePatchPlaneError("finite port geometry must not be empty")
        if not bool(getattr(geometry, "is_valid", False)) or float(getattr(geometry, "area", 0.0)) <= 0.0:
            raise SurfacePatchPlaneError("finite port geometry must be valid and have positive area")


@dataclass(frozen=True, slots=True)
class SurfacePatchFinitePortProjection:
    """Immutable area-weighted finite-port map into differential node space."""

    port_ids: tuple[str, ...]
    node_weights: sparse.csc_matrix
    port_areas_m2: NDArray[np.float64]

    def __post_init__(self) -> None:
        names = tuple(str(item).strip() for item in self.port_ids)
        weights = _immutable_sparse_copy(self.node_weights)
        areas = _readonly(self.port_areas_m2, dtype=np.float64)
        if not names or not all(names) or len({item.casefold() for item in names}) != len(names):
            raise SurfacePatchPlaneError("finite port ids must be non-empty and unique")
        if weights.shape[1] != len(names) or areas.shape != (len(names),) or not np.all(np.isfinite(areas)) or np.any(areas <= 0.0):
            raise SurfacePatchPlaneError("finite port projection dimensions are invalid")
        if not np.all(np.isfinite(weights.data)) or np.any(weights.data < 0.0):
            raise SurfacePatchPlaneError("finite port weights must be finite and non-negative")
        column_sums = np.asarray(weights.sum(axis=0)).reshape(-1)
        if not np.allclose(column_sums, 1.0, rtol=1.0e-11, atol=1.0e-13):
            raise SurfacePatchPlaneError("finite port weights must conserve unit terminal current")
        object.__setattr__(self, "port_ids", names)
        object.__setattr__(self, "node_weights", weights)
        object.__setattr__(self, "port_areas_m2", areas)


@dataclass(frozen=True, slots=True)
class SurfacePatchMeshDiagnostics:
    bounds_um: tuple[float, float, float, float]
    cell_um: float
    shape: tuple[int, int]
    mesh_mode: str
    artwork_count: int
    node_count: int
    same_layer_overlap_pairs: tuple[str, ...]
    ambiguous_boundary_contacts: tuple[str, ...]
    max_cells: int

    @property
    def same_layer_overlap_count(self) -> int:
        return len(self.same_layer_overlap_pairs)

    @property
    def ambiguous_boundary_contact_count(self) -> int:
        return len(self.ambiguous_boundary_contacts)


@dataclass(frozen=True, slots=True)
class _PatchFragment:
    node: SurfacePatchNode
    geometry_um: Any


@dataclass(frozen=True, slots=True)
class SurfacePatchMesh:
    """Ragged exact-clipped rectangular mesh.

    ``adaptive`` is deliberately a bounded *global fine fallback*, not an
    unverified non-conforming quadtree.  That choice preserves exact neighbour
    edge semantics until a future adapter supplies a conforming quadtree.
    """

    layer_order: tuple[str, ...]
    nodes: tuple[SurfacePatchNode, ...]
    fragments: tuple[_PatchFragment, ...]
    cell_fragments: Mapping[tuple[int, int, int], tuple[int, ...]]
    diagnostics: SurfacePatchMeshDiagnostics

    @classmethod
    def uniform(
        cls,
        *,
        layer_order: Sequence[str],
        artwork: Sequence[SurfacePatchArtwork],
        cell_um: float,
        bounds_um: tuple[float, float, float, float] | None = None,
        max_cells: int = 100_000,
    ) -> "SurfacePatchMesh":
        return _build_mesh(layer_order, artwork, cell_um, bounds_um, max_cells, "uniform")

    @classmethod
    def adaptive(
        cls,
        *,
        layer_order: Sequence[str],
        artwork: Sequence[SurfacePatchArtwork],
        coarse_cell_um: float,
        fine_cell_um: float,
        bounds_um: tuple[float, float, float, float] | None = None,
        max_cells: int = 100_000,
    ) -> "SurfacePatchMesh":
        """Return a bounded fine mesh when adaptive refinement is requested.

        This provides an API-safe refinement route while keeping all edges
        conforming.  A local quadtree is intentionally not substituted here:
        an unproved hanging-edge rule would alter the loop impedance.
        """
        _positive("coarse_cell_um", coarse_cell_um)
        _positive("fine_cell_um", fine_cell_um)
        if fine_cell_um > coarse_cell_um:
            raise SurfacePatchPlaneError("fine_cell_um must be <= coarse_cell_um")
        return _build_mesh(layer_order, artwork, fine_cell_um, bounds_um, max_cells, "adaptive-conforming-fine")


@dataclass(frozen=True, slots=True)
class SurfacePatchLateralStrip:
    """One maximal constant-signature shared-edge interval."""

    node_pairs_by_layer: tuple[tuple[int, int] | None, ...]
    cross_length_m: float
    strip_width_m: float

    def __post_init__(self) -> None:
        _positive("cross_length_m", self.cross_length_m)
        _positive("strip_width_m", self.strip_width_m)


@dataclass(frozen=True, slots=True)
class SurfacePatchVerticalStamp:
    node_a: int
    node_b: int
    overlap_area_m2: float
    gap: int

    def __post_init__(self) -> None:
        if self.node_a < 0 or self.node_b < 0 or self.node_a == self.node_b or self.gap < 0:
            raise SurfacePatchPlaneError("vertical stamp needs two distinct nodes and a non-negative gap")
        _positive("overlap_area_m2", self.overlap_area_m2)


@dataclass(frozen=True, slots=True)
class SurfacePatchDifferentialProjection:
    """Explicit local-common nullspace and one auditable complement.

    Columns of ``nullspace`` are independent common-potential shifts.  Columns
    of ``projection`` span a complement used only after a caller has accepted
    the differential formulation; they are not absolute global-MNA nodes.
    """

    projection: sparse.csc_matrix
    nullspace: sparse.csc_matrix
    vertical_groups: tuple[tuple[int, ...], ...]
    reference_nodes: tuple[int, ...]

    def __post_init__(self) -> None:
        projection = _immutable_sparse_copy(self.projection)
        nullspace = _immutable_sparse_copy(self.nullspace)
        groups = tuple(tuple(int(node) for node in group) for group in self.vertical_groups)
        references = tuple(int(node) for node in self.reference_nodes)
        object.__setattr__(self, "projection", projection)
        object.__setattr__(self, "nullspace", nullspace)
        object.__setattr__(self, "vertical_groups", groups)
        object.__setattr__(self, "reference_nodes", references)


@dataclass(frozen=True, slots=True)
class SurfacePatchPlaneDiagnostics:
    node_count: int
    capacitance_stamp_count: int
    lateral_strip_count: int
    disconnected_component_count: int
    differential_nullity: int
    same_layer_overlap_count: int
    ambiguous_boundary_contact_count: int
    conductor_provenance: tuple[str, ...]
    dielectric_provenance: tuple[str, ...]
    synthetic_material_defaults_used: bool


@dataclass(frozen=True, slots=True)
class SurfacePatchAdmittance:
    """Unreduced intermediate MFDM differential nodal stamp."""

    frequency_hz: float
    nodal_admittance_s: sparse.csc_matrix
    raw_reciprocity_relative: float
    nullspace_relative_residual: float
    passivity_min_eigenvalue_s: float
    passivity_tolerance_s: float


@dataclass(frozen=True, slots=True)
class _CoupledFactorization:
    lu: NDArray[np.complex128]
    pivots: NDArray[np.int32]
    condition: float

    def solve(self, rhs: NDArray[np.float64]) -> NDArray[np.complex128]:
        return np.asarray(lu_solve((self.lu, self.pivots), rhs), dtype=np.complex128)


@dataclass(frozen=True, slots=True)
class SurfacePatchPlaneOperator:
    """Experimental differential plane operator; not an absolute MNA block."""

    mesh: SurfacePatchMesh
    conductors: tuple[SurfacePatchConductor, ...]
    dielectrics: tuple[SurfacePatchDielectric | None, ...]
    capacitance_stamps: tuple[SurfacePatchVerticalStamp, ...]
    lateral_strips: tuple[SurfacePatchLateralStrip, ...]
    diagnostics: SurfacePatchPlaneDiagnostics
    _projection_metadata: SurfacePatchDifferentialProjection
    _coupled_factor_cache: OrderedDict[tuple[float, tuple[int, ...]], _CoupledFactorization] = field(
        default_factory=OrderedDict, compare=False, repr=False
    )

    def assemble_global_mna_admittance(self, frequency_hz: float) -> SurfacePatchAdmittance:
        """Reject the mathematically invalid absolute-MNA interpretation."""
        del frequency_hz
        raise SurfacePatchPlaneError(
            "surface-patch MFDM is a differential stamp with one local common-potential null per vertical group; "
            "it cannot be attached as an absolute global-MNA nodal block"
        )

    def differential_projection(self) -> SurfacePatchDifferentialProjection:
        """Return immutable topology metadata for a compatible future solver."""
        source = self._projection_metadata
        return SurfacePatchDifferentialProjection(
            source.projection,
            source.nullspace,
            source.vertical_groups,
            source.reference_nodes,
        )

    def finite_port_projection(
        self,
        ports: Sequence[SurfacePatchFinitePort],
    ) -> SurfacePatchFinitePortProjection:
        """Build finite-footprint current/voltage weights without a point port.

        The entire named footprint must be covered by retained same-net copper
        on its stated layer.  Partial overlap, another-net overlap and a
        missing layer are evidence failures: accepting them would turn an
        unknown launch into an invented plane connection.
        """
        requested = tuple(ports)
        if not requested or not all(isinstance(item, SurfacePatchFinitePort) for item in requested):
            raise SurfacePatchPlaneError("at least one typed finite port is required")
        port_ids = tuple(item.port_id.strip() for item in requested)
        if len({item.casefold() for item in port_ids}) != len(port_ids):
            raise SurfacePatchPlaneError("finite port ids must be unique")
        rows: list[int] = []
        columns: list[int] = []
        values: list[float] = []
        areas: list[float] = []
        for column, port in enumerate(requested):
            matching = tuple(
                fragment for fragment in self.mesh.fragments
                if fragment.node.layer.casefold() == port.layer.casefold()
                and fragment.node.net.casefold() == port.net.casefold()
            )
            if not matching:
                raise SurfacePatchPlaneError(
                    f"finite port {port.port_id!r} has no retained same-net conductor on {port.layer!r}/{port.net!r}"
                )
            other_net_overlap = 0.0
            overlaps: list[tuple[_PatchFragment, float]] = []
            for fragment in self.mesh.fragments:
                try:
                    overlap = float(fragment.geometry_um.intersection(port.geometry_um).area)
                except Exception as exc:
                    raise SurfacePatchPlaneError(f"finite port {port.port_id!r} geometry intersection failed") from exc
                if overlap <= 1.0e-12:
                    continue
                if fragment.node.layer.casefold() == port.layer.casefold() and fragment.node.net.casefold() != port.net.casefold():
                    other_net_overlap += overlap
                elif fragment.node.layer.casefold() == port.layer.casefold():
                    overlaps.append((fragment, overlap))
            if other_net_overlap > 1.0e-9:
                raise SurfacePatchPlaneError(f"finite port {port.port_id!r} overlaps another conductor identity")
            covered_area_um2 = sum(area for _fragment, area in overlaps)
            requested_area_um2 = float(port.geometry_um.area)
            tolerance = max(requested_area_um2 * 1.0e-10, 1.0e-9)
            if covered_area_um2 <= 0.0 or abs(covered_area_um2 - requested_area_um2) > tolerance:
                raise SurfacePatchPlaneError(
                    f"finite port {port.port_id!r} is not completely covered by retained same-net artwork"
                )
            for fragment, area in overlaps:
                rows.append(fragment.node.index)
                columns.append(column)
                values.append(area / covered_area_um2)
            areas.append(covered_area_um2 * 1.0e-12)
        weights = sparse.coo_matrix(
            (values, (rows, columns)), shape=(len(self.mesh.nodes), len(requested)), dtype=np.float64
        ).tocsc()
        return SurfacePatchFinitePortProjection(port_ids, weights, np.asarray(areas, dtype=np.float64))

    def assemble_differential_admittance(self, frequency_hz: float) -> SurfacePatchAdmittance:
        """Build the raw, unreduced MFDM differential stamp at one frequency."""
        _positive("frequency_hz", frequency_hz)
        omega = 2.0 * pi * frequency_hz
        rows: list[int] = []
        columns: list[int] = []
        values: list[complex] = []
        passivity_lower_bound = 0.0
        passivity_tolerance = 0.0
        for stamp in self.capacitance_stamps:
            dielectric = self.dielectrics[stamp.gap]
            assert dielectric is not None
            value = _dielectric_admittance_per_area(dielectric, frequency_hz) * stamp.overlap_area_m2
            local_minimum, local_tolerance = _local_passivity_gate(
                np.asarray(((value, -value), (-value, value)), dtype=np.complex128),
                name="dielectric overlap stamp",
            )
            passivity_lower_bound += min(local_minimum, 0.0)
            passivity_tolerance += local_tolerance
            _append_two_terminal(rows, columns, values, stamp.node_a, stamp.node_b, value)
        for strip in self.lateral_strips:
            local_nodes, voltage_map, base_impedance, factor, active_gaps = self._strip_matrices(
                strip, frequency_hz, omega
            )
            key = (float(frequency_hz), active_gaps)
            factorization = self._coupled_factor_cache.get(key)
            if factorization is None:
                factorization = _factor_coupled_impedance(base_impedance)
                self._coupled_factor_cache[key] = factorization
                self._coupled_factor_cache.move_to_end(key)
                while len(self._coupled_factor_cache) > _MAX_COUPLED_FACTOR_CACHE:
                    self._coupled_factor_cache.popitem(last=False)
            else:
                self._coupled_factor_cache.move_to_end(key)
            solution = factorization.solve(voltage_map.T)
            if not np.all(np.isfinite(solution)):
                raise SurfacePatchPlaneError("surface-patch coupled strip solve produced non-finite values")
            residual = float(
                np.linalg.norm(base_impedance @ solution - voltage_map.T)
                / max(
                    np.linalg.norm(base_impedance) * np.linalg.norm(solution) + np.linalg.norm(voltage_map.T),
                    1.0e-30,
                )
            )
            if residual > _COUPLED_RESIDUAL_LIMIT:
                raise SurfacePatchPlaneError(
                    f"surface-patch coupled strip solve residual {residual:.3e} exceeds {_COUPLED_RESIDUAL_LIMIT:.3e}"
                )
            stamp = (voltage_map @ solution) / factor
            local_minimum, local_tolerance = _local_passivity_gate(
                stamp, name="coupled lateral strip stamp"
            )
            passivity_lower_bound += min(local_minimum, 0.0)
            passivity_tolerance += local_tolerance
            for left, node_a in enumerate(local_nodes):
                for right, node_b in enumerate(local_nodes):
                    if stamp[left, right] != 0.0:
                        rows.append(node_a)
                        columns.append(node_b)
                        values.append(complex(stamp[left, right]))
        count = len(self.mesh.nodes)
        matrix = sparse.coo_matrix((values, (rows, columns)), shape=(count, count), dtype=np.complex128).tocsc()
        nullspace = self._projection_metadata.nullspace
        reciprocity, null_residual, passive_minimum, passive_tolerance = _validate_differential_stamp(
            matrix,
            nullspace,
            passivity_certificate=(passivity_lower_bound, passivity_tolerance),
        )
        return SurfacePatchAdmittance(
            frequency_hz, matrix, reciprocity, null_residual, passive_minimum, passive_tolerance
        )

    def _strip_matrices(
        self,
        strip: SurfacePatchLateralStrip,
        frequency_hz: float,
        omega: float,
    ) -> tuple[
        tuple[int, ...], NDArray[np.float64], NDArray[np.complex128], float, tuple[int, ...]
    ]:
        pairs = strip.node_pairs_by_layer
        active_gaps = _active_gaps(strip, self.dielectrics)
        if not active_gaps:
            raise SurfacePatchPlaneError("lateral strip has no supported dielectric gap")
        factor = strip.cross_length_m / strip.strip_width_m
        node_sets: list[tuple[int, int, int, int]] = []
        for gap in active_gaps:
            top_a, top_b = pairs[gap] or (-1, -1)
            bottom_a, bottom_b = pairs[gap + 1] or (-1, -1)
            node_sets.append((top_a, top_b, bottom_b, bottom_a))
        local_nodes = tuple(dict.fromkeys(node for group in node_sets for node in group))
        local_index = {node: index for index, node in enumerate(local_nodes)}
        voltage_map = np.zeros((len(local_nodes), len(active_gaps)), dtype=float)
        for column, group in enumerate(node_sets):
            for node, sign in zip(group, (1.0, -1.0, 1.0, -1.0), strict=True):
                voltage_map[local_index[node], column] += sign
        coupled = np.zeros((len(active_gaps), len(active_gaps)), dtype=np.complex128)
        for layer, conductor in enumerate(self.conductors):
            mapping = np.zeros((2, len(active_gaps)), dtype=float)
            geometry = np.zeros((2, 2), dtype=float)
            upper_face_column = next((column for column, gap in enumerate(active_gaps) if gap + 1 == layer), None)
            lower_face_column = next((column for column, gap in enumerate(active_gaps) if gap == layer), None)
            if upper_face_column is not None:
                mapping[0, upper_face_column] = -1.0
                geometry[0, 0] = 1.0
            if lower_face_column is not None:
                mapping[1, lower_face_column] = 1.0
                geometry[1, 1] = 1.0
            if upper_face_column is not None and lower_face_column is not None:
                # Exact same strip supports both physical faces.  Face indices
                # stay 0/1 regardless of the number or indices of active gaps.
                geometry[0, 1] = geometry[1, 0] = 1.0
            face = copper_two_face_surface_impedance(
                frequency_hz, conductor.conductivity_s_per_m, conductor.thickness_m, MU_0_H_PER_M
            )
            coupled += mapping.T @ (face * geometry) @ mapping
        for column, gap in enumerate(active_gaps):
            dielectric = self.dielectrics[gap]
            assert dielectric is not None
            coupled[column, column] += 1j * omega * MU_0_H_PER_M * dielectric.separation_m
        return local_nodes, voltage_map, coupled, factor, active_gaps


def _active_gaps(
    strip: SurfacePatchLateralStrip,
    dielectrics: Sequence[SurfacePatchDielectric | None],
) -> tuple[int, ...]:
    pairs = strip.node_pairs_by_layer
    return tuple(
        gap for gap, dielectric in enumerate(dielectrics)
        if dielectric is not None and pairs[gap] is not None and pairs[gap + 1] is not None
    )


def _factor_coupled_impedance(matrix: NDArray[np.complex128]) -> _CoupledFactorization:
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[0] != matrix.shape[1]:
        raise SurfacePatchPlaneError("surface-patch coupled strip impedance must be square and non-empty")
    if not np.all(np.isfinite(matrix)):
        raise SurfacePatchPlaneError("surface-patch coupled strip impedance contains non-finite values")
    condition = float(np.linalg.cond(matrix))
    if not np.isfinite(condition) or condition > _MAX_COUPLED_CONDITION:
        raise SurfacePatchPlaneError(
            f"surface-patch coupled strip impedance condition {condition:.3e} exceeds {_MAX_COUPLED_CONDITION:.3e}"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", LinAlgWarning)
            lu, pivots = lu_factor(matrix, check_finite=True)
    except (LinAlgWarning, ValueError, np.linalg.LinAlgError) as exc:
        raise SurfacePatchPlaneError("surface-patch coupled strip impedance factorization failed") from exc
    lu = _readonly(lu, dtype=np.complex128)
    pivots = _readonly(pivots, dtype=np.int32)
    return _CoupledFactorization(lu, pivots, condition)


def _local_passivity_gate(
    stamp: NDArray[np.complex128], *, name: str
) -> tuple[float, float]:
    if stamp.ndim != 2 or stamp.shape[0] != stamp.shape[1] or not np.all(np.isfinite(stamp)):
        raise SurfacePatchPlaneError(f"{name} must be finite and square")
    hermitian = (stamp + stamp.conj().T) * 0.5
    scale = float(np.linalg.norm(hermitian))
    tolerance = max(scale * 1.0e-10, float(np.linalg.norm(stamp)) * 1.0e-14, 1.0e-24)
    minimum = float(np.min(np.linalg.eigvalsh(hermitian)))
    if not np.isfinite(minimum) or minimum < -tolerance:
        raise SurfacePatchPlaneError(
            f"{name} passivity minimum {minimum:.3e} is below tolerance {-tolerance:.3e}"
        )
    return minimum, tolerance


def _validate_differential_stamp(
    matrix: sparse.csc_matrix,
    nullspace: sparse.csc_matrix,
    *,
    passivity_certificate: tuple[float, float] | None = None,
) -> tuple[float, float, float, float]:
    """Apply scale-aware reciprocity, local-nullspace and passivity gates."""
    if matrix.shape[0] == 0 or matrix.shape[0] != matrix.shape[1] or not np.all(np.isfinite(matrix.data)):
        raise SurfacePatchPlaneError("surface-patch differential stamp must be finite, square and non-empty")
    matrix_norm = float(sparse.linalg.norm(matrix))
    if not np.isfinite(matrix_norm) or matrix_norm <= 0.0:
        raise SurfacePatchPlaneError("surface-patch differential stamp has zero or non-finite norm")
    mismatch = matrix - matrix.T
    reciprocity = float(sparse.linalg.norm(mismatch) / matrix_norm)
    if not np.isfinite(reciprocity) or reciprocity > _RECIPROCITY_LIMIT:
        raise SurfacePatchPlaneError(
            f"surface-patch reciprocity residual {reciprocity:.3e} exceeds {_RECIPROCITY_LIMIT:.3e}"
        )
    null_norm = max(float(sparse.linalg.norm(nullspace)), 1.0)
    null_residual = float(sparse.linalg.norm(matrix @ nullspace) / (matrix_norm * null_norm))
    if not np.isfinite(null_residual) or null_residual > _NULLSPACE_LIMIT:
        raise SurfacePatchPlaneError(
            f"surface-patch local-nullspace residual {null_residual:.3e} exceeds {_NULLSPACE_LIMIT:.3e}"
        )
    if passivity_certificate is not None:
        passive_minimum, passivity_tolerance = (float(item) for item in passivity_certificate)
        if (
            not np.isfinite(passive_minimum)
            or not np.isfinite(passivity_tolerance)
            or passivity_tolerance < 0.0
        ):
            raise SurfacePatchPlaneError("surface-patch local-stamp passivity certificate is non-finite")
    else:
        hermitian_conductance = sparse.csc_matrix((matrix + matrix.getH()) * 0.5)
        conductance_scale = float(sparse.linalg.norm(hermitian_conductance))
        passivity_tolerance = max(conductance_scale * 1.0e-10, matrix_norm * 1.0e-14, 1.0e-18)
        try:
            if matrix.shape[0] <= 256:
                passive_minimum = float(np.min(np.linalg.eigvalsh(hermitian_conductance.toarray())))
            else:
                passive_minimum = float(
                    eigsh(
                        hermitian_conductance,
                        k=1,
                        which="SA",
                        return_eigenvectors=False,
                        tol=1.0e-10,
                        maxiter=max(1000, 20 * matrix.shape[0]),
                    )[0]
                )
        except Exception as exc:
            raise SurfacePatchPlaneError("surface-patch sparse passivity eigensolve failed") from exc
    if not np.isfinite(passive_minimum) or passive_minimum < -passivity_tolerance:
        raise SurfacePatchPlaneError(
            f"surface-patch passivity minimum {passive_minimum:.3e} S is below "
            f"tolerance {-passivity_tolerance:.3e} S"
        )
    return reciprocity, null_residual, passive_minimum, passivity_tolerance


def _dielectric_admittance_per_area(dielectric: SurfacePatchDielectric, frequency_hz: float) -> complex:
    """Return source-table or scalar dielectric shunt admittance per area."""
    omega = 2.0 * pi * frequency_hz
    if dielectric.dielectric_layers:
        inverse_permittivity = 0.0j
        for layer in dielectric.dielectric_layers:
            dk, df = layer.dispersion.interpolate(np.asarray((frequency_hz,), dtype=np.float64))
            inverse_permittivity += layer.thickness_m / (
                EPSILON_0_F_PER_M * float(dk[0]) * (1.0 - 1j * float(df[0]))
            )
        return complex(1j * omega / inverse_permittivity)
    if dielectric.dispersion is not None:
        dk, df = dielectric.dispersion.interpolate(np.asarray((frequency_hz,), dtype=np.float64))
        relative_permittivity = float(dk[0])
        loss_tangent = float(df[0])
    else:
        relative_permittivity = dielectric.relative_permittivity
        loss_tangent = dielectric.loss_tangent
    return complex(
        1j * omega * EPSILON_0_F_PER_M * relative_permittivity * (1.0 - 1j * loss_tangent)
        / dielectric.separation_m
    )


def compile_surface_patch_plane(
    mesh: SurfacePatchMesh,
    *,
    conductors: Mapping[str, SurfacePatchConductor] | None = None,
    dielectrics: Sequence[SurfacePatchDielectric],
    allow_ambiguous_boundary_contacts: bool = False,
    synthetic_material_defaults: bool = False,
) -> SurfacePatchPlaneOperator:
    """Compile exact patch overlaps and constant-signature lateral strips.

    Production/research-data callers must supply every conductor row and
    provenance for every conductor and dielectric.  The historical 35 um
    copper default exists only behind the explicit
    ``synthetic_material_defaults=True`` test/analytic switch.
    """
    if mesh.diagnostics.same_layer_overlap_count:
        joined = ", ".join(mesh.diagnostics.same_layer_overlap_pairs[:3])
        raise SurfacePatchPlaneError(f"same-layer different-net artwork overlap: {joined}")
    if mesh.diagnostics.ambiguous_boundary_contact_count and not allow_ambiguous_boundary_contacts:
        joined = ", ".join(mesh.diagnostics.ambiguous_boundary_contacts[:3])
        raise SurfacePatchPlaneError(f"ambiguous distinct-net boundary contact: {joined}")
    layer_order = mesh.layer_order
    expected_pairs = tuple(zip(layer_order, layer_order[1:]))
    supplied_dielectrics = tuple(dielectrics)
    supplied_pairs = tuple((item.upper_layer, item.lower_layer) for item in supplied_dielectrics)
    if supplied_pairs != expected_pairs:
        raise SurfacePatchPlaneError(
            "dielectric rows must be complete, ordered, non-reversed consecutive layer pairs: "
            f"expected {expected_pairs!r}, got {supplied_pairs!r}"
        )
    conductor_map = dict(conductors or {})
    unknown_conductors = tuple(sorted(set(conductor_map) - set(layer_order)))
    if unknown_conductors:
        raise SurfacePatchPlaneError(f"conductor map contains unknown layer(s): {unknown_conductors!r}")
    missing_conductors = tuple(layer for layer in layer_order if layer not in conductor_map)
    if missing_conductors and not synthetic_material_defaults:
        raise SurfacePatchPlaneError(f"conductor map is missing source row(s): {missing_conductors!r}")
    synthetic_used = bool(missing_conductors)
    if missing_conductors:
        for layer in missing_conductors:
            conductor_map[layer] = SurfacePatchConductor(provenance="synthetic-default-35um")
    layer_conductors = tuple(conductor_map[layer] for layer in layer_order)
    if any(not isinstance(item, SurfacePatchConductor) for item in layer_conductors):
        raise SurfacePatchPlaneError("every conductor map value must be SurfacePatchConductor")
    missing_conductor_provenance = tuple(
        layer for layer, conductor in zip(layer_order, layer_conductors, strict=True) if conductor.provenance is None
    )
    missing_dielectric_provenance = tuple(
        f"{item.upper_layer}/{item.lower_layer}" for item in supplied_dielectrics if item.provenance is None
    )
    if (missing_conductor_provenance or missing_dielectric_provenance) and not synthetic_material_defaults:
        raise SurfacePatchPlaneError(
            "material provenance is required for conductor and dielectric source rows; "
            f"conductors={missing_conductor_provenance!r}, dielectrics={missing_dielectric_provenance!r}"
        )
    synthetic_used = synthetic_used or bool(missing_conductor_provenance or missing_dielectric_provenance)
    conductor_provenance = tuple(item.provenance or "synthetic-explicit" for item in layer_conductors)
    dielectric_provenance = tuple(item.provenance or "synthetic-explicit" for item in supplied_dielectrics)
    stack: tuple[SurfacePatchDielectric | None, ...] = supplied_dielectrics
    caps: list[SurfacePatchVerticalStamp] = []
    for row in range(mesh.diagnostics.shape[0]):
        for column in range(mesh.diagnostics.shape[1]):
            for gap, dielectric in enumerate(stack):
                if dielectric is None:
                    continue
                upper = _fragments(mesh, gap, row, column)
                lower = _fragments(mesh, gap + 1, row, column)
                for first in upper:
                    for second in lower:
                        overlap_area_m2 = float(first.geometry_um.intersection(second.geometry_um).area) * 1.0e-12
                        if overlap_area_m2 <= 0.0:
                            continue
                        caps.append(SurfacePatchVerticalStamp(first.node.index, second.node.index, overlap_area_m2, gap))
    strips, contacts = _build_lateral_strips(mesh)
    # Mesh diagnostics includes contacts discovered during construction; the
    # strip pass independently checks exact crossing geometry as a guard.
    if contacts and not allow_ambiguous_boundary_contacts:
        joined = ", ".join(contacts[:3])
        raise SurfacePatchPlaneError(f"ambiguous distinct-net boundary contact: {joined}")
    usable_strips = tuple(
        strip for strip in strips
        if any(
            dielectric is not None and strip.node_pairs_by_layer[gap] is not None and strip.node_pairs_by_layer[gap + 1] is not None
            for gap, dielectric in enumerate(stack)
        )
    )
    if not caps:
        raise SurfacePatchPlaneError("surface-patch plane needs at least one exact adjacent-layer overlap")
    components = _structural_components(len(mesh.nodes), caps, usable_strips, stack)
    projection = _build_differential_projection(len(mesh.nodes), caps, usable_strips, stack)
    diagnostics = SurfacePatchPlaneDiagnostics(
        node_count=len(mesh.nodes),
        capacitance_stamp_count=len(caps),
        lateral_strip_count=len(usable_strips),
        disconnected_component_count=components,
        differential_nullity=projection.nullspace.shape[1],
        same_layer_overlap_count=mesh.diagnostics.same_layer_overlap_count,
        ambiguous_boundary_contact_count=mesh.diagnostics.ambiguous_boundary_contact_count,
        conductor_provenance=conductor_provenance,
        dielectric_provenance=dielectric_provenance,
        synthetic_material_defaults_used=synthetic_used,
    )
    return SurfacePatchPlaneOperator(mesh, layer_conductors, stack, tuple(caps), usable_strips, diagnostics, projection)


def _append_two_terminal(rows: list[int], columns: list[int], values: list[complex], a: int, b: int, value: complex) -> None:
    rows.extend((a, a, b, b))
    columns.extend((a, b, a, b))
    values.extend((value, -value, -value, value))


def _geometry_parts(geometry: Any) -> Iterable[Any]:
    if geometry.is_empty:
        return ()
    if geometry.geom_type == "Polygon":
        return (geometry,)
    return tuple(part for part in getattr(geometry, "geoms", ()) if part.geom_type == "Polygon" and part.area > 0.0)


def _build_mesh(
    layer_order: Sequence[str], artwork: Sequence[SurfacePatchArtwork], cell_um: float,
    bounds_um: tuple[float, float, float, float] | None, max_cells: int, mode: str,
) -> SurfacePatchMesh:
    try:
        from shapely.geometry import box
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover
        raise SurfacePatchPlaneError("Shapely is required for surface patch meshes") from exc
    _positive("cell_um", cell_um)
    if max_cells <= 0:
        raise SurfacePatchPlaneError("max_cells must be positive")
    layers = tuple(layer_order)
    if len(layers) < 2 or len(set(layers)) != len(layers) or any(not item.strip() for item in layers):
        raise SurfacePatchPlaneError("layer_order must contain at least two unique non-empty layers")
    known = set(layers)
    groups: dict[tuple[str, str], list[Any]] = {}
    for item in artwork:
        if item.layer not in known:
            raise SurfacePatchPlaneError(f"artwork layer {item.layer!r} is not in layer_order")
        groups.setdefault((item.layer, item.net), []).append(item.geometry_um)
    if not groups:
        raise SurfacePatchPlaneError("at least one artwork polygon is required")
    merged = {key: unary_union(value) for key, value in groups.items()}
    if bounds_um is None:
        min_x = min(float(geometry.bounds[0]) for geometry in merged.values())
        min_y = min(float(geometry.bounds[1]) for geometry in merged.values())
        max_x = max(float(geometry.bounds[2]) for geometry in merged.values())
        max_y = max(float(geometry.bounds[3]) for geometry in merged.values())
    else:
        min_x, min_y, max_x, max_y = (float(value) for value in bounds_um)
    if not np.all(np.isfinite((min_x, min_y, max_x, max_y))) or max_x <= min_x or max_y <= min_y:
        raise SurfacePatchPlaneError("bounds_um must have finite positive extent")
    min_x = floor(min_x / cell_um) * cell_um
    min_y = floor(min_y / cell_um) * cell_um
    max_x = ceil(max_x / cell_um) * cell_um
    max_y = ceil(max_y / cell_um) * cell_um
    # Exact source polygons can span the full board and contain tens of
    # thousands of vertices.  Clip once to the explicitly bounded analysis
    # domain before pair/contact checks and per-cell clipping.  This preserves
    # all in-domain topology while avoiding repeated whole-board overlays.
    analysis_domain = box(min_x, min_y, max_x, max_y)
    merged = {
        key: clipped
        for key, geometry in merged.items()
        if not (clipped := geometry.intersection(analysis_domain)).is_empty
    }
    if not merged:
        raise SurfacePatchPlaneError("bounded mesh contains no conductor artwork")
    overlaps: list[str] = []
    interior_contacts: list[str] = []
    for layer in layers:
        choices = [(net, geometry) for (candidate, net), geometry in merged.items() if candidate == layer]
        for index, (left_name, left) in enumerate(choices):
            for right_name, right in choices[index + 1:]:
                left_bounds, right_bounds = left.bounds, right.bounds
                if (
                    left_bounds[2] < right_bounds[0] or right_bounds[2] < left_bounds[0]
                    or left_bounds[3] < right_bounds[1] or right_bounds[3] < left_bounds[1]
                ):
                    continue
                if float(left.intersection(right).area) > 1.0e-9:
                    overlaps.append(f"{layer}:{left_name}/{right_name}")
                if float(left.boundary.intersection(right.boundary).length) > 1.0e-9:
                    interior_contacts.append(f"{layer}:{left_name}/{right_name}@interior")
    nx = int(round((max_x - min_x) / cell_um))
    ny = int(round((max_y - min_y) / cell_um))
    if nx <= 0 or ny <= 0 or nx * ny > max_cells:
        raise SurfacePatchPlaneError(f"mesh has {nx * ny} cells; max_cells is {max_cells}")
    node_records: list[SurfacePatchNode] = []
    patch_records: list[_PatchFragment] = []
    mutable_cell_map: dict[tuple[int, int, int], list[int]] = {}
    layer_index = {layer: index for index, layer in enumerate(layers)}
    for row in range(ny):
        for column in range(nx):
            cell = box(min_x + column * cell_um, min_y + row * cell_um, min_x + (column + 1) * cell_um, min_y + (row + 1) * cell_um)
            for (layer, net), geometry in sorted(merged.items()):
                pieces = sorted(_geometry_parts(geometry.intersection(cell)), key=lambda part: (part.centroid.x, part.centroid.y, part.area))
                ids: list[int] = []
                for fragment_index, piece in enumerate(pieces):
                    node = SurfacePatchNode(len(node_records), layer, net, row, column, fragment_index, float(piece.area) * 1.0e-12)
                    node_records.append(node)
                    patch_records.append(_PatchFragment(node, piece))
                    ids.append(node.index)
                if ids:
                    mutable_cell_map.setdefault((layer_index[layer], row, column), []).extend(ids)
    cell_map = {key: tuple(value) for key, value in mutable_cell_map.items()}
    # Boundary contacts are collected here for diagnostics.  Compilation again
    # checks them while constructing strips so direct object construction cannot
    # bypass this evidence.
    temporary = SurfacePatchMeshDiagnostics(
        (min_x, min_y, max_x, max_y), cell_um, (ny, nx), mode, len(artwork), len(node_records),
        tuple(overlaps), tuple(sorted(set(interior_contacts))), max_cells,
    )
    mesh = SurfacePatchMesh(layers, tuple(node_records), tuple(patch_records), cell_map, temporary)
    _strips, contacts = _build_lateral_strips(mesh)
    diagnostics = SurfacePatchMeshDiagnostics(
        (min_x, min_y, max_x, max_y), cell_um, (ny, nx), mode, len(artwork), len(node_records),
        tuple(overlaps), tuple(sorted(set(interior_contacts + contacts))), max_cells,
    )
    return SurfacePatchMesh(layers, tuple(node_records), tuple(patch_records), cell_map, diagnostics)


def _fragments(mesh: SurfacePatchMesh, layer: int, row: int, column: int) -> tuple[_PatchFragment, ...]:
    ids = mesh.cell_fragments.get((layer, row, column), ())
    return tuple(mesh.fragments[index] for index in ids)


def _boundary_intervals(geometry: Any, line: Any, *, vertical: bool) -> list[tuple[float, float]]:
    intersection = geometry.boundary.intersection(line)
    def lines(item: Any) -> tuple[Any, ...]:
        if item.is_empty:
            return ()
        if item.geom_type == "LineString":
            return (item,)
        return tuple(part for child in getattr(item, "geoms", ()) for part in lines(child))
    pieces = lines(intersection)
    output: list[tuple[float, float]] = []
    for part in pieces:
        if getattr(part, "length", 0.0) <= 1.0e-12:
            continue
        coords = list(part.coords)
        values = [float(point[1] if vertical else point[0]) for point in coords]
        output.append((min(values), max(values)))
    return output


def _supporting_fragment(
    supports: tuple[tuple[_PatchFragment, tuple[tuple[float, float], ...]], ...],
    value: float,
) -> _PatchFragment | None:
    hits = []
    for fragment, intervals in supports:
        for start, stop in intervals:
            if start + 1.0e-10 < value < stop - 1.0e-10:
                hits.append(fragment)
                break
    if len(hits) > 1:
        raise SurfacePatchPlaneError("one mesh edge has overlapping same-layer fragment support")
    return hits[0] if hits else None


def _build_lateral_strips(mesh: SurfacePatchMesh) -> tuple[list[SurfacePatchLateralStrip], list[str]]:
    try:
        from shapely.geometry import LineString
    except ImportError as exc:  # pragma: no cover
        raise SurfacePatchPlaneError("Shapely is required for surface patch meshes") from exc
    min_x, min_y, max_x, max_y = mesh.diagnostics.bounds_um
    cell = mesh.diagnostics.cell_um
    ny, nx = mesh.diagnostics.shape
    strips: list[SurfacePatchLateralStrip] = []
    contacts: list[str] = []

    def visit(row_a: int, column_a: int, row_b: int, column_b: int, line: Any, *, vertical: bool, cross_um: float) -> None:
        breakpoints = {float(line.coords[0][1 if vertical else 0]), float(line.coords[-1][1 if vertical else 0])}
        support_a: list[tuple[tuple[_PatchFragment, tuple[tuple[float, float], ...]], ...]] = []
        support_b: list[tuple[tuple[_PatchFragment, tuple[tuple[float, float], ...]], ...]] = []
        for layer in range(len(mesh.layer_order)):
            left_support = tuple(
                (fragment, tuple(_boundary_intervals(fragment.geometry_um, line, vertical=vertical)))
                for fragment in _fragments(mesh, layer, row_a, column_a)
            )
            right_support = tuple(
                (fragment, tuple(_boundary_intervals(fragment.geometry_um, line, vertical=vertical)))
                for fragment in _fragments(mesh, layer, row_b, column_b)
            )
            support_a.append(left_support)
            support_b.append(right_support)
            for _fragment, intervals in left_support + right_support:
                for start, stop in intervals:
                    breakpoints.update((start, stop))
        ordered = sorted(breakpoints)
        pending: SurfacePatchLateralStrip | None = None
        for left, right in zip(ordered, ordered[1:]):
            if right - left <= 1.0e-10:
                continue
            midpoint = (left + right) * 0.5
            signature: list[tuple[int, int] | None] = []
            for layer in range(len(mesh.layer_order)):
                first = _supporting_fragment(support_a[layer], midpoint)
                second = _supporting_fragment(support_b[layer], midpoint)
                if first is not None and second is not None and first.node.net != second.node.net:
                    contacts.append(f"{mesh.layer_order[layer]}:{first.node.net}/{second.node.net}@r{row_a}c{column_a}")
                signature.append((first.node.index, second.node.index) if first is not None and second is not None and first.node.net == second.node.net else None)
            candidate = SurfacePatchLateralStrip(tuple(signature), cross_um * 1.0e-6, (right - left) * 1.0e-6)
            if pending is not None and pending.node_pairs_by_layer == candidate.node_pairs_by_layer and abs(pending.strip_width_m + candidate.strip_width_m) > 0.0:
                pending = SurfacePatchLateralStrip(pending.node_pairs_by_layer, pending.cross_length_m, pending.strip_width_m + candidate.strip_width_m)
            else:
                if pending is not None:
                    strips.append(pending)
                pending = candidate
        if pending is not None:
            strips.append(pending)

    for row in range(ny):
        for column in range(nx - 1):
            x = min_x + (column + 1) * cell
            visit(row, column, row, column + 1, LineString(((x, min_y + row * cell), (x, min_y + (row + 1) * cell))), vertical=True, cross_um=cell)
    for row in range(ny - 1):
        for column in range(nx):
            y = min_y + (row + 1) * cell
            visit(row, column, row + 1, column, LineString(((min_x + column * cell, y), (min_x + (column + 1) * cell, y))), vertical=False, cross_um=cell)
    return strips, contacts


def _structural_components(
    count: int,
    caps: Sequence[SurfacePatchVerticalStamp],
    strips: Sequence[SurfacePatchLateralStrip],
    dielectrics: Sequence[SurfacePatchDielectric | None],
) -> int:
    if not count:
        return 0
    rows: list[int] = []
    columns: list[int] = []
    for stamp in caps:
        rows.extend((stamp.node_a, stamp.node_b)); columns.extend((stamp.node_b, stamp.node_a))
    for strip in strips:
        for gap, dielectric in enumerate(dielectrics):
            if dielectric is None or strip.node_pairs_by_layer[gap] is None or strip.node_pairs_by_layer[gap + 1] is None:
                continue
            values = (*strip.node_pairs_by_layer[gap], *strip.node_pairs_by_layer[gap + 1])
            for left in values:
                for right in values:
                    if left != right:
                        rows.append(left); columns.append(right)
    if not rows:
        return count
    graph = sparse.coo_matrix((np.ones(len(rows)), (rows, columns)), shape=(count, count)).tocsr()
    from scipy.sparse.csgraph import connected_components
    return int(connected_components(graph, directed=False, return_labels=False))


def _build_differential_projection(
    count: int,
    caps: Sequence[SurfacePatchVerticalStamp],
    strips: Sequence[SurfacePatchLateralStrip],
    dielectrics: Sequence[SurfacePatchDielectric | None],
) -> SurfacePatchDifferentialProjection:
    """Build local vertical groups whose common potentials are unobservable."""
    parent = list(range(count))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for stamp in caps:
        union(stamp.node_a, stamp.node_b)
    for strip in strips:
        for gap in _active_gaps(strip, dielectrics):
            top_a, top_b = strip.node_pairs_by_layer[gap] or (-1, -1)
            bottom_a, bottom_b = strip.node_pairs_by_layer[gap + 1] or (-1, -1)
            # Each endpoint raster column has its own unobservable common
            # voltage.  Do not union the two columns across the lateral edge.
            union(top_a, bottom_a)
            union(top_b, bottom_b)
    collected: dict[int, list[int]] = {}
    for node in range(count):
        collected.setdefault(find(node), []).append(node)
    groups = tuple(tuple(nodes) for _root, nodes in sorted(collected.items()))
    null_rows: list[int] = []
    null_columns: list[int] = []
    projection_rows: list[int] = []
    projection_columns: list[int] = []
    references: list[int] = []
    dof = 0
    for column, group in enumerate(groups):
        references.append(group[0])
        for node in group:
            null_rows.append(node)
            null_columns.append(column)
        for node in group[1:]:
            projection_rows.append(node)
            projection_columns.append(dof)
            dof += 1
    nullspace = sparse.coo_matrix(
        (np.ones(len(null_rows)), (null_rows, null_columns)), shape=(count, len(groups)), dtype=np.float64
    ).tocsc()
    projection = sparse.coo_matrix(
        (np.ones(len(projection_rows)), (projection_rows, projection_columns)), shape=(count, dof), dtype=np.float64
    ).tocsc()
    return SurfacePatchDifferentialProjection(projection, nullspace, groups, tuple(references))


__all__ = [
    "SurfacePatchArtwork", "SurfacePatchConductor", "SurfacePatchDielectric", "SurfacePatchNode",
    "SurfacePatchFinitePort", "SurfacePatchFinitePortProjection",
    "SurfacePatchMesh", "SurfacePatchMeshDiagnostics", "SurfacePatchLateralStrip", "SurfacePatchVerticalStamp",
    "SurfacePatchDifferentialProjection", "SurfacePatchPlaneError", "SurfacePatchPlaneDiagnostics",
    "SurfacePatchAdmittance", "SurfacePatchPlaneOperator", "compile_surface_patch_plane",
]
