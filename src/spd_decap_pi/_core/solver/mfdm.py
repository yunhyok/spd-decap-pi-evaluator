"""Sparse multi-conductor finite-difference PDN kernel.

This module deliberately knows nothing about SPD files.  A caller supplies a
cut-cell raster (area and edge coverage are numbers, not inferred polygons),
then this module builds the frequency-domain circuit described by the
multilayer finite-difference method (MFDM).  In particular, each dielectric
gap gets its own four-terminal lateral *loop* element; it is not a scalar
series combination of layer impedances and neither neighbouring conductor is
made an ideal ground.

The implementation is intended as a conservative core for an SPD adapter:
topology that would silently turn a sliver or a disconnected conductor island
into a numerical ground is rejected instead of guessed.

Current scope: adjacent dielectric gaps and their shared sheet-current loss,
including finite-thickness two-face internal-conductor coupling, are modeled.
Non-adjacent plane coupling through slots/apertures remains an experimental
extension; callers must not present this kernel as a full-wave sign-off model.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import pi
from typing import Final

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import MatrixRankWarning, splu
import warnings


EPSILON_0_F_PER_M: Final[float] = 8.854_187_812_8e-12
MU_0_H_PER_M: Final[float] = 4.0e-7 * pi


class MfdmSolverError(ValueError):
    """Raised when a cut-cell model cannot be solved without guessing."""


@dataclass(frozen=True, slots=True)
class MfdmEdgeStrip:
    """One maximal, constant-signature portion of a raster cell boundary.

    ``conductor_labels`` is the exact presence signature on the two cells
    adjoining that boundary: zero means that layer has no continuous copper on
    this strip, while a positive value identifies the continuous net.  The
    strips of one spatial edge form an ordered partition of its physical
    width.  Keeping these records until circuit assembly is essential: two
    disjoint supports must be stamped as ``sum(Gs Ks^-1 Gs.T)`` rather than
    as one averaged K matrix.
    """

    width_fraction: float
    conductor_labels: tuple[int, ...]

    def __post_init__(self) -> None:
        _positive("edge-strip width_fraction", float(self.width_fraction))
        if float(self.width_fraction) > 1.0 + 1.0e-12:
            raise MfdmSolverError("edge-strip width_fraction must be <= 1")
        labels = tuple(int(value) for value in self.conductor_labels)
        if len(labels) < 2 or any(value < 0 for value in labels):
            raise MfdmSolverError("edge-strip conductor_labels must contain at least two non-negative labels")
        object.__setattr__(self, "conductor_labels", labels)

    @property
    def active_gap_ids(self) -> tuple[int, ...]:
        return tuple(index for index in range(len(self.conductor_labels) - 1)
                     if self.conductor_labels[index] > 0 and self.conductor_labels[index + 1] > 0)


def _readonly_array(
    value: ArrayLike,
    *,
    dtype: np.dtype[np.generic] | type[np.generic],
    name: str,
    ndim: int | None = None,
) -> NDArray[np.generic]:
    array = np.asarray(value, dtype=dtype)
    if ndim is not None and array.ndim != ndim:
        raise MfdmSolverError(f"{name} must have {ndim} dimensions")
    if not np.all(np.isfinite(array)):
        raise MfdmSolverError(f"{name} must contain only finite values")
    result = np.array(array, copy=True)
    result.setflags(write=False)
    return result


def _positive(name: str, value: float, *, allow_zero: bool = False) -> None:
    if not np.isfinite(value) or value < 0.0 or (not allow_zero and value == 0.0):
        relation = ">= 0" if allow_zero else "> 0"
        raise MfdmSolverError(f"{name} must be finite and {relation}")


def _as_positive_tuple(name: str, value: float | tuple[float, ...]) -> tuple[float, ...]:
    raw = (value,) if np.isscalar(value) else tuple(value)
    if not raw:
        raise MfdmSolverError(f"{name} must not be empty")
    result = tuple(float(item) for item in raw)
    for item in result:
        _positive(name, item)
    return result


@dataclass(frozen=True, slots=True)
class MfdmMaterial:
    """Copper and dielectric data for upper--target and target--lower gaps.

    A scalar copper property applies to every physical layer.  A tuple is
    ordered by physical layer and allows real stack-up thicknesses.
    """

    upper_target_gap_m: float
    target_lower_gap_m: float
    upper_target_relative_permittivity: float
    target_lower_relative_permittivity: float
    conductivity_s_per_m: float | tuple[float, ...] = 5.8e7
    copper_thickness_m: float | tuple[float, ...] = 35e-6
    upper_target_loss_tangent: float = 0.0
    target_lower_loss_tangent: float = 0.0
    permeability_h_per_m: float = MU_0_H_PER_M
    gap_separations_m: tuple[float, ...] | None = None
    gap_relative_permittivities: tuple[float, ...] | None = None
    gap_loss_tangents: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        for name, value in {
            "upper_target_gap_m": self.upper_target_gap_m,
            "target_lower_gap_m": self.target_lower_gap_m,
            "upper_target_relative_permittivity": self.upper_target_relative_permittivity,
            "target_lower_relative_permittivity": self.target_lower_relative_permittivity,
            "permeability_h_per_m": self.permeability_h_per_m,
        }.items():
            _positive(name, float(value))
        for name, value in {
            "upper_target_loss_tangent": self.upper_target_loss_tangent,
            "target_lower_loss_tangent": self.target_lower_loss_tangent,
        }.items():
            _positive(name, float(value), allow_zero=True)
        object.__setattr__(self, "conductivity_s_per_m", _as_positive_tuple("conductivity_s_per_m", self.conductivity_s_per_m))
        object.__setattr__(self, "copper_thickness_m", _as_positive_tuple("copper_thickness_m", self.copper_thickness_m))
        supplied = (self.gap_separations_m, self.gap_relative_permittivities, self.gap_loss_tangents)
        if any(item is not None for item in supplied):
            if any(item is None for item in supplied):
                raise MfdmSolverError("gap_separations_m, gap_relative_permittivities and gap_loss_tangents must be supplied together")
            separations = tuple(float(item) for item in self.gap_separations_m or ())
            epsilon_rs = tuple(float(item) for item in self.gap_relative_permittivities or ())
            loss_tangents = tuple(float(item) for item in self.gap_loss_tangents or ())
            if not separations or len(separations) != len(epsilon_rs) or len(separations) != len(loss_tangents):
                raise MfdmSolverError("explicit gap property tuples must be non-empty and have equal lengths")
            for value in separations + epsilon_rs:
                _positive("explicit gap material property", value)
            for value in loss_tangents:
                _positive("explicit gap_loss_tangents", value, allow_zero=True)
            object.__setattr__(self, "gap_separations_m", separations)
            object.__setattr__(self, "gap_relative_permittivities", epsilon_rs)
            object.__setattr__(self, "gap_loss_tangents", loss_tangents)

    def gap_properties(self, gap_count: int) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
        """Return per-gap (separation, epsilon_r, tan-delta) values.

        The named upper-target/target-lower values remain the safe default for
        two gaps.  A longer stack must supply all three explicit tuples; no
        unknown dielectric is extrapolated.
        """
        if self.gap_separations_m is not None:
            if len(self.gap_separations_m) != gap_count:
                raise MfdmSolverError("explicit gap material tuples do not match the geometry gap count")
            assert self.gap_relative_permittivities is not None and self.gap_loss_tangents is not None
            return self.gap_separations_m, self.gap_relative_permittivities, self.gap_loss_tangents
        if gap_count != 2:
            raise MfdmSolverError("a stack with other than two gaps requires explicit gap material tuples")
        return (
            (self.upper_target_gap_m, self.target_lower_gap_m),
            (self.upper_target_relative_permittivity, self.target_lower_relative_permittivity),
            (self.upper_target_loss_tangent, self.target_lower_loss_tangent),
        )

    def conductor_properties(self, conductor_count: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
        def expand(values: tuple[float, ...], name: str) -> tuple[float, ...]:
            if len(values) == 1:
                return values * conductor_count
            if len(values) != conductor_count:
                raise MfdmSolverError(f"{name} has {len(values)} values but the geometry has {conductor_count} conductor surfaces")
            return values
        return expand(self.conductivity_s_per_m, "conductivity_s_per_m"), expand(self.copper_thickness_m, "copper_thickness_m")


@dataclass(frozen=True, slots=True)
class MfdmCutCellGeometry:
    """Three physical conductor surfaces represented by an immutable raster.

    ``conductor_area_fractions`` has shape ``(n, ny, nx)``, with ``n >= 2``.
    Labels have the
    same shape: zero means no metal and a positive integer identifies one
    electrically continuous artwork/net on that *physical* layer.  The solver
    never joins cells carrying different labels.  Horizontal and vertical
    coverage arrays have shapes ``(n, ny, nx - 1)`` and ``(n, ny - 1, nx)``.
    A nonzero edge between unequal labels is an error rather than an accidental
    short.  The two gap overlap fractions are ordered upper-target,
    target-lower and have shape ``(n - 1, ny, nx)``.  Exact per-gap edge
    support and adjacent-gap common support are retained separately, because
    scalar coverage cannot locate disjoint or partially coincident segments.
    Scalar coverage alone is therefore accepted only for zero or full-width
    edge support.  The initial production
    material supports a three-conductor / two-gap slab; topology is kept
    generic so no differently-labelled artworks are ever electrically unioned.
    One raster slot cannot encode two different artworks on the same physical
    layer.  An SPD adapter must subdivide such a cell (preferred) or set
    ``unresolved_mixed_artwork_mask`` so this core fails closed; it must never
    choose a dominant label or merge the nets.
    """

    cell_areas_m2: ArrayLike
    conductor_area_fractions: ArrayLike
    conductor_labels: ArrayLike
    horizontal_edge_fractions: ArrayLike
    vertical_edge_fractions: ArrayLike
    gap_overlap_fractions: ArrayLike
    dx_m: float
    dy_m: float
    sliver_fraction: float = 1.0e-9
    unresolved_mixed_artwork_mask: ArrayLike | None = None
    horizontal_gap_edge_overlap_fractions: ArrayLike | None = None
    vertical_gap_edge_overlap_fractions: ArrayLike | None = None
    horizontal_adjacent_gap_overlap_fractions: ArrayLike | None = None
    vertical_adjacent_gap_overlap_fractions: ArrayLike | None = None
    # Exact boundary partitions.  A supplied tuple is indexed [row][column]
    # and contains all (including electrically empty) maximal strips of that
    # spatial edge.  Scalar edge support is retained only for legacy full-edge
    # cases and diagnostics; it is never used to average a partial strip.
    horizontal_edge_strips: tuple[tuple[tuple[MfdmEdgeStrip, ...], ...], ...] | None = None
    vertical_edge_strips: tuple[tuple[tuple[MfdmEdgeStrip, ...], ...], ...] | None = None

    def __post_init__(self) -> None:
        areas = _readonly_array(self.cell_areas_m2, dtype=np.float64, name="cell_areas_m2", ndim=2)
        fractions = _readonly_array(self.conductor_area_fractions, dtype=np.float64, name="conductor_area_fractions", ndim=3)
        labels = _readonly_array(self.conductor_labels, dtype=np.int64, name="conductor_labels", ndim=3)
        horizontal = _readonly_array(self.horizontal_edge_fractions, dtype=np.float64, name="horizontal_edge_fractions", ndim=3)
        vertical = _readonly_array(self.vertical_edge_fractions, dtype=np.float64, name="vertical_edge_fractions", ndim=3)
        overlaps = _readonly_array(self.gap_overlap_fractions, dtype=np.float64, name="gap_overlap_fractions", ndim=3)
        ny, nx = areas.shape
        conductor_count = fractions.shape[0]
        mixed = (
            np.zeros(fractions.shape, dtype=bool)
            if self.unresolved_mixed_artwork_mask is None
            else _readonly_array(
                self.unresolved_mixed_artwork_mask,
                dtype=bool,
                name="unresolved_mixed_artwork_mask",
                ndim=3,
            )
        )
        if conductor_count < 2 or labels.shape != (conductor_count, ny, nx):
            raise MfdmSolverError("conductor_area_fractions/conductor_labels must have shape (n >= 2, ny, nx)")
        if mixed.shape != (conductor_count, ny, nx):
            raise MfdmSolverError("unresolved_mixed_artwork_mask must have shape (n, ny, nx)")
        if np.any(mixed):
            raise MfdmSolverError(
                "a cut cell contains multiple artworks on one physical layer; "
                "subdivide it or represent separate patches before MFDM assembly"
            )
        if horizontal.shape != (conductor_count, ny, max(nx - 1, 0)):
            raise MfdmSolverError("horizontal_edge_fractions must have shape (n, ny, nx - 1)")
        if vertical.shape != (conductor_count, max(ny - 1, 0), nx):
            raise MfdmSolverError("vertical_edge_fractions must have shape (n, ny - 1, nx)")
        if overlaps.shape != (conductor_count - 1, ny, nx):
            raise MfdmSolverError("gap_overlap_fractions must have shape (n - 1, ny, nx)")
        gap_shape_h = (conductor_count - 1, ny, max(nx - 1, 0))
        gap_shape_v = (conductor_count - 1, max(ny - 1, 0), nx)
        cross_shape_h = (max(conductor_count - 2, 0), ny, max(nx - 1, 0))
        cross_shape_v = (max(conductor_count - 2, 0), max(ny - 1, 0), nx)

        def exact_edge_support(
            value: ArrayLike | None,
            *,
            name: str,
            shape: tuple[int, int, int],
            coverages: NDArray[np.float64],
        ) -> NDArray[np.float64]:
            if value is None:
                # A scalar partial edge coverage has no position (and can be a
                # union of disjoint segments), so it cannot determine either
                # gap support or the two-face transfer area.  Full coverage is
                # the one backwards-compatible exact case.
                partial = (coverages > self.sliver_fraction) & (coverages < 1.0 - 1.0e-12)
                if np.any(partial):
                    raise MfdmSolverError(
                        f"{name} is required for partial conductor edge coverage; "
                        "scalar coverage cannot determine exact shared-face overlap"
                    )
                result = np.ones(shape, dtype=np.float64)
                result[coverages[:-1] <= self.sliver_fraction] = 0.0
                result[coverages[1:] <= self.sliver_fraction] = 0.0
                return result
            result = _readonly_array(value, dtype=np.float64, name=name, ndim=3)
            if result.shape != shape:
                raise MfdmSolverError(f"{name} must have shape {shape}")
            return result

        gap_h = exact_edge_support(
            self.horizontal_gap_edge_overlap_fractions,
            name="horizontal_gap_edge_overlap_fractions",
            shape=gap_shape_h,
            coverages=horizontal,
        )
        gap_v = exact_edge_support(
            self.vertical_gap_edge_overlap_fractions,
            name="vertical_gap_edge_overlap_fractions",
            shape=gap_shape_v,
            coverages=vertical,
        )

        def exact_two_face_support(
            value: ArrayLike | None,
            *,
            name: str,
            shape: tuple[int, int, int],
            gap_support: NDArray[np.float64],
        ) -> NDArray[np.float64]:
            if value is None:
                partial = (gap_support[:-1] > self.sliver_fraction) & (gap_support[:-1] < 1.0 - 1.0e-12)
                partial |= (gap_support[1:] > self.sliver_fraction) & (gap_support[1:] < 1.0 - 1.0e-12)
                if np.any(partial):
                    raise MfdmSolverError(
                        f"{name} is required for partial adjacent-gap edge support; "
                        "scalar support cannot determine two-face overlap"
                    )
                return np.minimum(gap_support[:-1], gap_support[1:])
            result = _readonly_array(value, dtype=np.float64, name=name, ndim=3)
            if result.shape != shape:
                raise MfdmSolverError(f"{name} must have shape {shape}")
            return result

        cross_h = exact_two_face_support(
            self.horizontal_adjacent_gap_overlap_fractions,
            name="horizontal_adjacent_gap_overlap_fractions",
            shape=cross_shape_h,
            gap_support=gap_h,
        )
        cross_v = exact_two_face_support(
            self.vertical_adjacent_gap_overlap_fractions,
            name="vertical_adjacent_gap_overlap_fractions",
            shape=cross_shape_v,
            gap_support=gap_v,
        )
        for name, array in {
            "cell_areas_m2": areas,
            "conductor_area_fractions": fractions,
            "horizontal_edge_fractions": horizontal,
            "vertical_edge_fractions": vertical,
            "gap_overlap_fractions": overlaps,
            "horizontal_gap_edge_overlap_fractions": gap_h,
            "vertical_gap_edge_overlap_fractions": gap_v,
            "horizontal_adjacent_gap_overlap_fractions": cross_h,
            "vertical_adjacent_gap_overlap_fractions": cross_v,
        }.items():
            if np.any(array < 0.0) or np.any(array > 1.0):
                raise MfdmSolverError(f"{name} values must be in [0, 1]")
        if np.any(areas <= 0.0):
            raise MfdmSolverError("cell_areas_m2 values must be > 0")
        if np.any(labels < 0):
            raise MfdmSolverError("conductor_labels values must be non-negative")
        _positive("dx_m", self.dx_m)
        _positive("dy_m", self.dy_m)
        _positive("sliver_fraction", self.sliver_fraction, allow_zero=True)
        active = fractions > self.sliver_fraction
        if np.any(active != (labels > 0)):
            raise MfdmSolverError("each non-sliver conductor cell must have one positive label and vice versa")
        # An overlap may only be claimed where both physical surfaces exist.
        for gap, (top, bottom) in enumerate(zip(range(conductor_count - 1), range(1, conductor_count), strict=True)):
            valid = np.minimum(fractions[top], fractions[bottom])
            if np.any(overlaps[gap] > valid + 1.0e-12):
                raise MfdmSolverError("gap overlap exceeds one or both conductor cut-cell areas")
        for name, support, coverages in (
            ("horizontal_gap_edge_overlap_fractions", gap_h, horizontal),
            ("vertical_gap_edge_overlap_fractions", gap_v, vertical),
        ):
            if np.any(support > np.minimum(coverages[:-1], coverages[1:]) + 1.0e-12):
                raise MfdmSolverError(f"{name} exceeds one or both conductor edge coverages")
        for name, support, gap_support in (
            ("horizontal_adjacent_gap_overlap_fractions", cross_h, gap_h),
            ("vertical_adjacent_gap_overlap_fractions", cross_v, gap_v),
        ):
            if np.any(support > np.minimum(gap_support[:-1], gap_support[1:]) + 1.0e-12):
                raise MfdmSolverError(f"{name} exceeds one or both adjacent gap edge supports")
        # Edge coverage cannot cross an artwork boundary or attach a sliver.
        for layer in range(conductor_count):
            if nx > 1:
                valid = active[layer, :, :-1] & active[layer, :, 1:] & (labels[layer, :, :-1] == labels[layer, :, 1:])
                if np.any((horizontal[layer] > self.sliver_fraction) & ~valid):
                    raise MfdmSolverError("horizontal edge crosses inactive or differently labelled conductor artwork")
            if ny > 1:
                valid = active[layer, :-1, :] & active[layer, 1:, :] & (labels[layer, :-1, :] == labels[layer, 1:, :])
                if np.any((vertical[layer] > self.sliver_fraction) & ~valid):
                    raise MfdmSolverError("vertical edge crosses inactive or differently labelled conductor artwork")

        def normalise_strips(
            supplied: tuple[tuple[tuple[MfdmEdgeStrip, ...], ...], ...] | None,
            *,
            name: str,
            edge_coverages: NDArray[np.float64],
            gap_support: NDArray[np.float64],
            cross_support: NDArray[np.float64],
            edge_rows: int,
            edge_columns: int,
            endpoint_a: tuple[int, int],
            endpoint_b: tuple[int, int],
        ) -> tuple[tuple[tuple[MfdmEdgeStrip, ...], ...], ...]:
            """Validate exact partitions and derive only unambiguous full edges."""

            if supplied is None:
                # A partial scalar has lost its position along the boundary.
                # There is no mathematically defensible way to reconstruct a
                # two-face K matrix from it, so require a true partition.
                arrays = (edge_coverages, gap_support, cross_support)
                if any(np.any((array > self.sliver_fraction) & (array < 1.0 - 1.0e-12)) for array in arrays):
                    raise MfdmSolverError(
                        f"{name} is required for partial edge support; scalar support cannot be assembled exactly"
                    )
                result: list[tuple[tuple[MfdmEdgeStrip, ...], ...]] = []
                for row in range(edge_rows):
                    result_row: list[tuple[MfdmEdgeStrip, ...]] = []
                    for column in range(edge_columns):
                        first = endpoint_a(row, column)
                        second = endpoint_b(row, column)
                        signature = tuple(
                            int(labels[layer, first[0], first[1]])
                            if edge_coverages[layer, row, column] > self.sliver_fraction else 0
                            for layer in range(conductor_count)
                        )
                        # Full/zero scalar values have one unambiguous strip.
                        result_row.append((MfdmEdgeStrip(1.0, signature),))
                    result.append(tuple(result_row))
                return tuple(result)
            if len(supplied) != edge_rows or any(len(row) != edge_columns for row in supplied):
                raise MfdmSolverError(f"{name} must have raster shape ({edge_rows}, {edge_columns})")
            result = []
            for row, strip_row in enumerate(supplied):
                normalised_row: list[tuple[MfdmEdgeStrip, ...]] = []
                for column, strips in enumerate(strip_row):
                    values = tuple(strips)
                    if not values:
                        raise MfdmSolverError(f"{name} must partition every spatial edge, including empty strips")
                    if any(not isinstance(strip, MfdmEdgeStrip) for strip in values):
                        raise MfdmSolverError(f"{name} entries must be MfdmEdgeStrip values")
                    if any(len(strip.conductor_labels) != conductor_count for strip in values):
                        raise MfdmSolverError(f"{name} conductor signature does not match layer count")
                    if not np.isclose(sum(strip.width_fraction for strip in values), 1.0, rtol=0.0, atol=1.0e-10):
                        raise MfdmSolverError(f"{name} strips must cover exactly one physical edge width")
                    first = endpoint_a(row, column)
                    second = endpoint_b(row, column)
                    for layer in range(conductor_count):
                        expected = int(labels[layer, first[0], first[1]])
                        if expected != int(labels[layer, second[0], second[1]]):
                            expected = 0
                        actual = sum(strip.width_fraction for strip in values if strip.conductor_labels[layer] > 0)
                        if expected == 0:
                            if actual > self.sliver_fraction:
                                raise MfdmSolverError(f"{name} claims copper across an inactive or differently-labelled edge")
                        else:
                            if any(strip.conductor_labels[layer] not in (0, expected) for strip in values):
                                raise MfdmSolverError(f"{name} changes net label across one continuous edge")
                            if not np.isclose(actual, edge_coverages[layer, row, column], rtol=0.0, atol=1.0e-10):
                                raise MfdmSolverError(f"{name} conductor width does not match edge coverage")
                    for gap in range(conductor_count - 1):
                        actual = sum(strip.width_fraction for strip in values if gap in strip.active_gap_ids)
                        if not np.isclose(actual, gap_support[gap, row, column], rtol=0.0, atol=1.0e-10):
                            raise MfdmSolverError(f"{name} gap support does not match exact strip signature")
                    for gap in range(max(conductor_count - 2, 0)):
                        actual = sum(
                            strip.width_fraction for strip in values
                            if gap in strip.active_gap_ids and gap + 1 in strip.active_gap_ids
                        )
                        if not np.isclose(actual, cross_support[gap, row, column], rtol=0.0, atol=1.0e-10):
                            raise MfdmSolverError(f"{name} adjacent-gap common support does not match exact strip signature")
                    normalised_row.append(values)
                result.append(tuple(normalised_row))
            return tuple(result)

        horizontal_strips = normalise_strips(
            self.horizontal_edge_strips,
            name="horizontal_edge_strips",
            edge_coverages=horizontal,
            gap_support=gap_h,
            cross_support=cross_h,
            edge_rows=ny,
            edge_columns=max(nx - 1, 0),
            endpoint_a=lambda row, column: (row, column),
            endpoint_b=lambda row, column: (row, column + 1),
        )
        vertical_strips = normalise_strips(
            self.vertical_edge_strips,
            name="vertical_edge_strips",
            edge_coverages=vertical,
            gap_support=gap_v,
            cross_support=cross_v,
            edge_rows=max(ny - 1, 0),
            edge_columns=nx,
            endpoint_a=lambda row, column: (row, column),
            endpoint_b=lambda row, column: (row + 1, column),
        )
        object.__setattr__(self, "cell_areas_m2", areas)
        object.__setattr__(self, "conductor_area_fractions", fractions)
        object.__setattr__(self, "conductor_labels", labels)
        object.__setattr__(self, "horizontal_edge_fractions", horizontal)
        object.__setattr__(self, "vertical_edge_fractions", vertical)
        object.__setattr__(self, "gap_overlap_fractions", overlaps)
        object.__setattr__(self, "horizontal_gap_edge_overlap_fractions", gap_h)
        object.__setattr__(self, "vertical_gap_edge_overlap_fractions", gap_v)
        object.__setattr__(self, "horizontal_adjacent_gap_overlap_fractions", cross_h)
        object.__setattr__(self, "vertical_adjacent_gap_overlap_fractions", cross_v)
        object.__setattr__(self, "horizontal_edge_strips", horizontal_strips)
        object.__setattr__(self, "vertical_edge_strips", vertical_strips)


@dataclass(frozen=True, slots=True)
class MfdmNode:
    """One cut-cell voltage unknown addressed by physical layer, row, column."""

    conductor: int
    row: int
    column: int

    def __post_init__(self) -> None:
        for name, value in (
            ("conductor", self.conductor),
            ("row", self.row),
            ("column", self.column),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 0:
                raise MfdmSolverError(f"MfdmNode {name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class MfdmPort:
    """Differential external port; no port terminal is silently grounded."""

    port_id: str
    positive: MfdmNode
    negative: MfdmNode

    def __post_init__(self) -> None:
        if not isinstance(self.port_id, str) or not self.port_id.strip():
            raise MfdmSolverError("port_id must not be empty")
        if not isinstance(self.positive, MfdmNode) or not isinstance(self.negative, MfdmNode):
            raise MfdmSolverError("a port requires two MfdmNode terminals")
        if self.positive == self.negative:
            raise MfdmSolverError("a port requires two distinct nodes")


@dataclass(frozen=True, slots=True)
class MfdmOperator:
    """Immutable, frequency-independent MFDM topology and geometric stamps."""

    geometry: MfdmCutCellGeometry
    material: MfdmMaterial
    node_index: NDArray[np.int64]
    capacitance_stamps: NDArray[np.float64]
    loop_stamps: NDArray[np.float64]
    coupled_edge_gap_ids: NDArray[np.int64]
    coupled_edge_nodes: NDArray[np.int64]
    coupled_edge_gap_factors: NDArray[np.float64]
    coupled_edge_conductor_factors: NDArray[np.float64]
    coupled_edge_cross_factors: NDArray[np.float64]
    local_reference_nodes: NDArray[np.int64]
    gauge_node: int = 0
    # Placed after the historical gauge argument so direct experimental
    # construction that passed the gauge positionally stays readable.
    sheet_owner_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        node_index = _readonly_array(self.node_index, dtype=np.int64, name="node_index", ndim=3)
        caps = _readonly_array(self.capacitance_stamps, dtype=np.float64, name="capacitance_stamps", ndim=2)
        loops = _readonly_array(self.loop_stamps, dtype=np.float64, name="loop_stamps", ndim=2)
        edge_gaps = _readonly_array(self.coupled_edge_gap_ids, dtype=np.int64, name="coupled_edge_gap_ids", ndim=2)
        edge_nodes = _readonly_array(self.coupled_edge_nodes, dtype=np.int64, name="coupled_edge_nodes", ndim=3)
        edge_gap_factors = _readonly_array(self.coupled_edge_gap_factors, dtype=np.float64, name="coupled_edge_gap_factors", ndim=2)
        edge_conductor_factors = _readonly_array(self.coupled_edge_conductor_factors, dtype=np.float64, name="coupled_edge_conductor_factors", ndim=2)
        edge_cross_factors = _readonly_array(self.coupled_edge_cross_factors, dtype=np.float64, name="coupled_edge_cross_factors", ndim=2)
        references = _readonly_array(self.local_reference_nodes, dtype=np.int64, name="local_reference_nodes", ndim=2)
        if node_index.shape != self.geometry.conductor_area_fractions.shape:
            raise MfdmSolverError("node_index shape does not match geometry")
        if references.shape != node_index.shape[1:]:
            raise MfdmSolverError("local_reference_nodes must have shape (ny, nx)")
        count = int(np.max(node_index, initial=-1)) + 1
        if count < 2:
            raise MfdmSolverError("MFDM requires at least two active conductor nodes")
        if not 0 <= self.gauge_node < count:
            raise MfdmSolverError("gauge_node is outside node range")
        if isinstance(self.sheet_owner_ids, str):
            raise MfdmSolverError("sheet_owner_ids must be a tuple of non-empty IDs, not a string")
        if any(not isinstance(value, str) for value in self.sheet_owner_ids):
            raise MfdmSolverError("sheet_owner_ids must contain only strings")
        owners = tuple(value.strip() for value in self.sheet_owner_ids)
        if not owners:
            owners = tuple(f"mfdm-sheet:{index}" for index in range(node_index.shape[0]))
        if len(owners) != node_index.shape[0] or any(not value for value in owners):
            raise MfdmSolverError("sheet_owner_ids must contain one non-empty ID for every physical conductor sheet")
        if len(set(owners)) != len(owners):
            raise MfdmSolverError("sheet_owner_ids must be unique; a physical copper sheet cannot be stamped twice")
        active_per_cell = np.any(node_index >= 0, axis=0)
        if np.any((references < 0) != ~active_per_cell):
            raise MfdmSolverError("each active raster column must have exactly one local reference node")
        reference_values = references[references >= 0]
        if np.any(reference_values >= count) or np.unique(reference_values).size != reference_values.size:
            raise MfdmSolverError("local reference nodes must be unique active node indices")
        for row, column in np.argwhere(active_per_cell):
            if int(references[row, column]) not in node_index[:, row, column]:
                raise MfdmSolverError("a local reference must belong to its raster column")
        if caps.ndim != 2 or caps.shape[1] != 4:
            raise MfdmSolverError("capacitance_stamps must be (count, node_a, node_b, capacitance_f)")
        if loops.ndim != 2 or loops.shape[1] != 6:
            raise MfdmSolverError("loop_stamps must be (gap, node0, node1, node2, node3, geometry_factor)")
        gap_count = node_index.shape[0] - 1
        if edge_gaps.shape[1] != gap_count or edge_nodes.shape != (*edge_gaps.shape, 4):
            raise MfdmSolverError("coupled edge gap/node arrays have invalid shape")
        if edge_gap_factors.shape != edge_gaps.shape or edge_conductor_factors.shape != (edge_gaps.shape[0], node_index.shape[0]):
            raise MfdmSolverError("coupled edge factor arrays have invalid shape")
        if edge_cross_factors.shape != (edge_gaps.shape[0], max(gap_count - 1, 0)):
            raise MfdmSolverError("coupled edge cross-factor array has invalid shape")
        if np.any(edge_gap_factors < 0.0) or np.any(edge_conductor_factors < 0.0) or np.any(edge_cross_factors < 0.0):
            raise MfdmSolverError("coupled edge factors must be non-negative")
        for edge in range(edge_gaps.shape[0]):
            for slot, gap in enumerate(edge_gaps[edge]):
                if gap < 0:
                    if edge_gap_factors[edge, slot] != 0.0 or np.any(edge_nodes[edge, slot] != -1):
                        raise MfdmSolverError("unused coupled edge slots must carry zero factor and -1 nodes")
                    continue
                if gap >= gap_count or edge_gap_factors[edge, slot] <= 0.0:
                    raise MfdmSolverError("invalid coupled edge gap slot")
                nodes = edge_nodes[edge, slot]
                if np.any(nodes < 0) or np.any(nodes >= count):
                    raise MfdmSolverError("invalid coupled edge nodes")
                if edge_conductor_factors[edge, gap] <= 0.0 or edge_conductor_factors[edge, gap + 1] <= 0.0:
                    raise MfdmSolverError("a coupled gap requires both conductor lateral factors")
            for gap in range(max(gap_count - 1, 0)):
                cross = edge_cross_factors[edge, gap]
                if cross > 0.0 and (gap not in edge_gaps[edge] or gap + 1 not in edge_gaps[edge]):
                    raise MfdmSolverError("a coupled edge cross factor requires both adjacent gap slots")
        for row in caps:
            if int(row[0]) != row[0] or row[0] < 0 or row[0] >= gap_count or any(int(value) != value or value < 0 or value >= count for value in row[1:3]) or row[3] <= 0.0:
                raise MfdmSolverError("invalid capacitance stamp")
        for row in loops:
            if int(row[0]) != row[0] or row[0] < 0 or row[0] >= gap_count or any(int(value) != value or value < 0 or value >= count for value in row[1:5]) or row[5] <= 0.0:
                raise MfdmSolverError("invalid loop stamp")
        object.__setattr__(self, "node_index", node_index)
        object.__setattr__(self, "capacitance_stamps", caps)
        object.__setattr__(self, "loop_stamps", loops)
        object.__setattr__(self, "coupled_edge_gap_ids", edge_gaps)
        object.__setattr__(self, "coupled_edge_nodes", edge_nodes)
        object.__setattr__(self, "coupled_edge_gap_factors", edge_gap_factors)
        object.__setattr__(self, "coupled_edge_conductor_factors", edge_conductor_factors)
        object.__setattr__(self, "coupled_edge_cross_factors", edge_cross_factors)
        object.__setattr__(self, "local_reference_nodes", references)
        object.__setattr__(self, "sheet_owner_ids", owners)

    @property
    def node_count(self) -> int:
        return int(np.max(self.node_index, initial=-1)) + 1

    @property
    def relative_node_count(self) -> int:
        return self.node_count - int(np.count_nonzero(self.local_reference_nodes >= 0))

    @property
    def two_face_sheet_owner_ids(self) -> tuple[str, ...]:
        """Physical sheets shared by the two adjacent-gap face operators.

        The MFDM edge assembly stamps each listed sheet once through its 2x2
        ``coth/csch`` face matrix; it is never reintroduced as two scalar
        sheet impedances.  Consumers can carry these IDs into a global
        ownership ledger before adding via or local replacement blocks.
        """

        return self.sheet_owner_ids[1:-1]


@dataclass(frozen=True, slots=True)
class MfdmDiagnostics:
    residual_relative: float
    reciprocity_relative: float
    reciprocity_absolute_ohm: float
    min_hermitian_impedance_eigenvalue_ohm: float
    passivity_tolerance_ohm: float
    passive: bool
    condition_estimate: float
    gauge_node: int
    local_reference_count: int
    # These are intentionally raw-solve diagnostics.  The returned matrix is
    # made exactly symmetric only after the raw residual and pairwise
    # reciprocity gates pass; a cosmetic symmetrization must never conceal a
    # bad solve.
    raw_reciprocity_pair_max_relative: float = float("nan")
    raw_reciprocity_pair_max_absolute_ohm: float = float("nan")
    raw_validation_passed: bool = False
    forward_error_estimate_ohm: float = float("nan")
    solve_quality_estimate_passed: bool = False
    structural_component_count: int = 0
    physical_sheet_count: int = 0
    two_face_sheet_count: int = 0

@dataclass(frozen=True, slots=True)
class MfdmSolveResult:
    """Open-port result in local gauge-relative conductor coordinates.

    ``node_voltages_v_per_a`` is useful for reconstructing differential
    terminal voltages and currents.  It is deliberately not an absolute field
    voltage: one common-coordinate origin was removed per raster column.
    """

    frequency_hz: float
    port_ids: tuple[str, ...]
    impedance_ohm: NDArray[np.complex128]
    node_voltages_v_per_a: NDArray[np.complex128]
    diagnostics: MfdmDiagnostics
    raw_impedance_ohm: NDArray[np.complex128] | None = None

    def __post_init__(self) -> None:
        z = _readonly_array(self.impedance_ohm, dtype=np.complex128, name="impedance_ohm", ndim=2)
        raw_z = _readonly_array(
            z if self.raw_impedance_ohm is None else self.raw_impedance_ohm,
            dtype=np.complex128,
            name="raw_impedance_ohm",
            ndim=2,
        )
        v = _readonly_array(self.node_voltages_v_per_a, dtype=np.complex128, name="node_voltages_v_per_a", ndim=2)
        object.__setattr__(self, "impedance_ohm", z)
        object.__setattr__(self, "raw_impedance_ohm", raw_z)
        object.__setattr__(self, "node_voltages_v_per_a", v)


def _copper_two_face_terms(
    frequency_hz: float | ArrayLike,
    conductivity_s_per_m: float,
    thickness_m: float,
    permeability_h_per_m: float = MU_0_H_PER_M,
) -> tuple[NDArray[np.complex128], NDArray[np.complex128], NDArray[np.float64]]:
    """Return finite-thickness self/transfer surface impedances.

    The result is ``(Zc*coth(gamma*t), Zc*csch(gamma*t), frequencies)``.
    Both hyperbolic terms use their small-argument series, so the DC limit is
    the same sheet resistance for self and transfer faces.
    """

    _positive("conductivity_s_per_m", conductivity_s_per_m)
    _positive("thickness_m", thickness_m)
    _positive("permeability_h_per_m", permeability_h_per_m)
    frequencies = np.asarray(frequency_hz, dtype=np.float64)
    if not np.all(np.isfinite(frequencies)) or np.any(frequencies < 0.0):
        raise MfdmSolverError("frequency_hz must be finite and >= 0")
    omega = 2.0 * pi * frequencies
    root = np.sqrt(1j * omega * permeability_h_per_m * conductivity_s_per_m)
    x = thickness_m * root
    coth = np.empty_like(x, dtype=np.complex128)
    small = np.abs(x) < 1.0e-4
    csch = np.empty_like(x, dtype=np.complex128)
    # coth(x)=1/x+x/3-x^3/45+2x^5/945.
    # csch(x)=1/x-x/6+7x^3/360-31x^5/15120.
    if np.any(small):
        xs = x[small]
        # Avoid x=0 directly: the limiting result is filled afterwards.
        with np.errstate(divide="ignore", invalid="ignore"):
            coth[small] = 1.0 / xs + xs / 3.0 - xs**3 / 45.0 + 2.0 * xs**5 / 945.0
            csch[small] = 1.0 / xs - xs / 6.0 + 7.0 * xs**3 / 360.0 - 31.0 * xs**5 / 15_120.0
    if np.any(~small):
        xn = x[~small]
        coth[~small] = 1.0 / np.tanh(xn)
        csch[~small] = 1.0 / np.sinh(xn)
    with np.errstate(invalid="ignore"):
        characteristic = np.sqrt(1j * omega * permeability_h_per_m / conductivity_s_per_m)
        self_impedance = np.asarray(characteristic * coth, dtype=np.complex128)
        transfer_impedance = np.asarray(characteristic * csch, dtype=np.complex128)
    dc = 1.0 / (conductivity_s_per_m * thickness_m) + 0.0j
    self_impedance = np.where(frequencies == 0.0, dc, self_impedance)
    transfer_impedance = np.where(frequencies == 0.0, dc, transfer_impedance)
    return self_impedance, transfer_impedance, frequencies


def copper_surface_impedance(
    frequency_hz: float | ArrayLike,
    conductivity_s_per_m: float,
    thickness_m: float,
    permeability_h_per_m: float = MU_0_H_PER_M,
) -> complex | NDArray[np.complex128]:
    """Finite-thickness one-face copper surface impedance in ohms/square."""

    result, _transfer, _frequencies = _copper_two_face_terms(
        frequency_hz,
        conductivity_s_per_m,
        thickness_m,
        permeability_h_per_m,
    )
    if np.ndim(frequency_hz) == 0:
        return complex(result.item())
    return result


def copper_two_face_surface_impedance(
    frequency_hz: float | ArrayLike,
    conductivity_s_per_m: float,
    thickness_m: float,
    permeability_h_per_m: float = MU_0_H_PER_M,
) -> NDArray[np.complex128]:
    """Two-face conductor surface-impedance matrix in ohms/square.

    For each frequency this is ``Zc[[coth(gamma*t), csch(gamma*t)],
    [csch(gamma*t), coth(gamma*t)]]``.  A scalar frequency returns ``(2, 2)``;
    an array returns ``frequency_shape + (2, 2)``.
    """

    self_impedance, transfer_impedance, frequencies = _copper_two_face_terms(
        frequency_hz,
        conductivity_s_per_m,
        thickness_m,
        permeability_h_per_m,
    )
    output = np.empty(frequencies.shape + (2, 2), dtype=np.complex128)
    output[..., 0, 0] = self_impedance
    output[..., 1, 1] = self_impedance
    output[..., 0, 1] = transfer_impedance
    output[..., 1, 0] = transfer_impedance
    return output


def _append_outer(rows: list[int], columns: list[int], values: list[complex], nodes: tuple[int, ...], signs: tuple[float, ...], admittance: complex) -> None:
    for left, sign_left in zip(nodes, signs, strict=True):
        for right, sign_right in zip(nodes, signs, strict=True):
            rows.append(left)
            columns.append(right)
            values.append(admittance * sign_left * sign_right)


def compile_mfdm_operator(
    geometry: MfdmCutCellGeometry,
    material: MfdmMaterial,
    *,
    reference_conductors: ArrayLike | None = None,
    sheet_owner_ids: tuple[str, ...] | None = None,
) -> MfdmOperator:
    """Compile cut-cell geometry into immutable capacitance and loop stamps.

    One local common-voltage coordinate is removed from every active raster
    column.  This is a change of variables to relative conductor voltages, not
    an electrical ground or a scalar layer merge.  ``reference_conductors`` is
    optional solely for verification of gauge-choice invariance; production can
    use the deterministic first active layer default.

    Spatially disconnected balanced plane-pair components are valid and are
    intentionally retained.  They are factored independently at solve time;
    only a singular reduced component fails closed.
    """

    fractions = geometry.conductor_area_fractions
    active = fractions > geometry.sliver_fraction
    lone_columns = np.argwhere(np.count_nonzero(active, axis=0) == 1)
    if lone_columns.size:
        row, column = (int(value) for value in lone_columns[0])
        raise MfdmSolverError(
            "MFDM relative formulation cannot represent a raster column with only one active conductor "
            f"(first at row={row}, column={column}); refine/crop the bounded slab or retain its return conductor"
        )
    node_index = np.full(active.shape, -1, dtype=np.int64)
    node_index[active] = np.arange(int(np.count_nonzero(active)), dtype=np.int64)
    ny, nx = active.shape[1:]
    if reference_conductors is None:
        reference_layers = np.full((ny, nx), -1, dtype=np.int64)
        for row, column in np.argwhere(np.any(active, axis=0)):
            reference_layers[row, column] = int(np.flatnonzero(active[:, row, column])[0])
    else:
        reference_layers = np.asarray(reference_conductors, dtype=np.int64)
        if reference_layers.shape != (ny, nx):
            raise MfdmSolverError("reference_conductors must have shape (ny, nx)")
        reference_layers = np.array(reference_layers, copy=True)
        for row, column in np.ndindex(reference_layers.shape):
            if np.any(active[:, row, column]):
                layer = int(reference_layers[row, column])
                if not 0 <= layer < active.shape[0] or not active[layer, row, column]:
                    raise MfdmSolverError("each active raster column needs an active reference conductor")
            elif reference_layers[row, column] != -1:
                raise MfdmSolverError("inactive raster columns require reference conductor -1")
    local_reference_nodes = np.full((ny, nx), -1, dtype=np.int64)
    for row, column in np.argwhere(np.any(active, axis=0)):
        local_reference_nodes[row, column] = node_index[reference_layers[row, column], row, column]
    caps: list[tuple[float, float, float, float]] = []
    loops: list[tuple[float, float, float, float, float, float]] = []
    coupled_edges: list[tuple[list[int], list[tuple[int, int, int, int]], list[float], list[float], list[float]]] = []

    # Vertical dielectric cells: C=eps*A/d and G=w*C*tan(delta) at solve time.
    separations, epsilon_rs, _loss_tangents = material.gap_properties(geometry.conductor_area_fractions.shape[0] - 1)
    for gap, (top, bottom) in enumerate(zip(range(geometry.conductor_area_fractions.shape[0] - 1), range(1, geometry.conductor_area_fractions.shape[0]), strict=True)):
        epsilon_r = epsilon_rs[gap]
        separation = separations[gap]
        for row, column in np.argwhere(geometry.gap_overlap_fractions[gap] > geometry.sliver_fraction):
            node_a = int(node_index[top, row, column])
            node_b = int(node_index[bottom, row, column])
            if node_a < 0 or node_b < 0:  # guarded by geometry, retained for fail-closed clarity
                raise MfdmSolverError("nonzero dielectric overlap has no active conductor on both sides")
            capacitance = EPSILON_0_F_PER_M * epsilon_r * geometry.cell_areas_m2[row, column] * geometry.gap_overlap_fractions[gap, row, column] / separation
            caps.append((float(gap), float(node_a), float(node_b), float(capacitance)))

    def add_coupled_edge(
        endpoint_a: tuple[int, int],
        endpoint_b: tuple[int, int],
        *,
        length_m: float,
        width_m: float,
        strip: MfdmEdgeStrip,
    ) -> None:
        """Compile one exact edge strip; never combine signatures before K^-1."""

        factor = float(length_m / (width_m * strip.width_fraction))
        conductor_factors = [factor if label > 0 else 0.0 for label in strip.conductor_labels]
        gaps: list[int] = []
        nodes_per_gap: list[tuple[int, int, int, int]] = []
        gap_factors: list[float] = []
        row_a, column_a = endpoint_a
        row_b, column_b = endpoint_b
        for gap in range(geometry.conductor_area_fractions.shape[0] - 1):
            if gap not in strip.active_gap_ids:
                continue
            # Stored order [top_a, top_b, bottom_b, bottom_a] maps the
            # published Eq.(8) loop to signs (+, -, +, -).
            nodes = (
                int(node_index[gap, row_a, column_a]),
                int(node_index[gap, row_b, column_b]),
                int(node_index[gap + 1, row_b, column_b]),
                int(node_index[gap + 1, row_a, column_a]),
            )
            if min(nodes) < 0:
                raise MfdmSolverError("lateral loop reaches inactive conductor cell")
            loops.append((float(gap), *(float(node) for node in nodes), factor))
            gaps.append(gap)
            nodes_per_gap.append(nodes)
            gap_factors.append(factor)
        if gaps:
            cross_factors = [
                factor if gap in strip.active_gap_ids and gap + 1 in strip.active_gap_ids else 0.0
                for gap in range(max(geometry.conductor_area_fractions.shape[0] - 2, 0))
            ]
            coupled_edges.append((gaps, nodes_per_gap, gap_factors, conductor_factors, cross_factors))

    for row in range(geometry.cell_areas_m2.shape[0]):
        for column in range(max(geometry.cell_areas_m2.shape[1] - 1, 0)):
            for strip in geometry.horizontal_edge_strips[row][column]:
                add_coupled_edge(
                    (row, column), (row, column + 1),
                    length_m=geometry.dx_m, width_m=geometry.dy_m, strip=strip,
                )
    for row in range(max(geometry.cell_areas_m2.shape[0] - 1, 0)):
        for column in range(geometry.cell_areas_m2.shape[1]):
            for strip in geometry.vertical_edge_strips[row][column]:
                add_coupled_edge(
                    (row, column), (row + 1, column),
                    length_m=geometry.dy_m, width_m=geometry.dx_m, strip=strip,
                )

    if not caps:
        raise MfdmSolverError("MFDM needs at least one dielectric cut-cell overlap")
    gap_count = geometry.conductor_area_fractions.shape[0] - 1
    edge_gap_ids = np.full((len(coupled_edges), gap_count), -1, dtype=np.int64)
    edge_nodes = np.full((len(coupled_edges), gap_count, 4), -1, dtype=np.int64)
    edge_gap_factors = np.zeros((len(coupled_edges), gap_count), dtype=np.float64)
    edge_conductor_factors = np.zeros((len(coupled_edges), gap_count + 1), dtype=np.float64)
    edge_cross_factors = np.zeros((len(coupled_edges), max(gap_count - 1, 0)), dtype=np.float64)
    for edge, (gaps, nodes_per_gap, gap_factors, conductor_factors, cross_factors) in enumerate(coupled_edges):
        edge_gap_ids[edge, :len(gaps)] = gaps
        edge_nodes[edge, :len(gaps)] = nodes_per_gap
        edge_gap_factors[edge, :len(gaps)] = gap_factors
        edge_conductor_factors[edge] = conductor_factors
        edge_cross_factors[edge] = cross_factors
    return MfdmOperator(
        geometry=geometry,
        material=material,
        node_index=node_index,
        capacitance_stamps=np.asarray(caps, dtype=np.float64),
        loop_stamps=np.asarray(loops, dtype=np.float64).reshape((-1, 6)),
        coupled_edge_gap_ids=edge_gap_ids,
        coupled_edge_nodes=edge_nodes,
        coupled_edge_gap_factors=edge_gap_factors,
        coupled_edge_conductor_factors=edge_conductor_factors,
        coupled_edge_cross_factors=edge_cross_factors,
        local_reference_nodes=local_reference_nodes,
        sheet_owner_ids=() if sheet_owner_ids is None else sheet_owner_ids,
        gauge_node=int(local_reference_nodes[local_reference_nodes >= 0][0]),
    )


def _port_incidence(operator: MfdmOperator, ports: tuple[MfdmPort, ...]) -> NDArray[np.float64]:
    if not ports:
        raise MfdmSolverError("at least one external port is required")
    if len({port.port_id for port in ports}) != len(ports):
        raise MfdmSolverError("port_id values must be unique")
    incidence = np.zeros((operator.node_count, len(ports)), dtype=np.float64)
    shape = operator.node_index.shape
    for index, port in enumerate(ports):
        if (port.positive.row, port.positive.column) != (port.negative.row, port.negative.column):
            raise MfdmSolverError(
                f"port {port.port_id!r} spans raster columns; MFDM ports must be co-located differential P/G terminals"
            )
        for node, sign in ((port.positive, 1.0), (port.negative, -1.0)):
            if not (0 <= node.conductor < shape[0] and 0 <= node.row < shape[1] and 0 <= node.column < shape[2]):
                raise MfdmSolverError(f"port {port.port_id!r} node lies outside the cut-cell mesh")
            unknown = int(operator.node_index[node.conductor, node.row, node.column])
            if unknown < 0:
                raise MfdmSolverError(f"port {port.port_id!r} lands on inactive conductor artwork")
            incidence[unknown, index] += sign
    return incidence


def _relative_projection(operator: MfdmOperator) -> sparse.csc_matrix:
    """Map local *adjacent-gap voltage* coordinates to conductor voltages.

    Simply deleting a local reference-node row is algebraically valid, but it
    mixes adjacent-gap modes.  In a three-conductor column that turns
    ``(V0-V1, V1-V2)`` into ``(V0, V1)`` and can hide an exactly block-diagonal
    physical circuit behind a cancellation-prone dense reduced matrix.  The
    columns here are instead the consecutive physical layer differences
    ``u_k=V_k-V_(k+1)``.  A selected local reference fixes the common mode, and
    each conductor voltage is the signed cumulative path from that reference.

    This is an exact local spanning-tree change of variables, not a ground.
    It intentionally rejects a column with an inactive physical layer between
    active layers: the current adjacent-gap kernel has no unambiguous local
    tree across that missing conductor and must not invent one.
    """

    node_index = operator.node_index
    rows: list[int] = []
    columns: list[int] = []
    values: list[float] = []
    dof = 0
    for row, column in np.ndindex(operator.local_reference_nodes.shape):
        active_layers = np.flatnonzero(node_index[:, row, column] >= 0)
        if not active_layers.size:
            continue
        if not np.array_equal(active_layers, np.arange(active_layers[0], active_layers[-1] + 1)):
            raise MfdmSolverError(
                "MFDM adjacent-gap relative formulation requires contiguous active conductor layers in every raster column"
            )
        reference_node = int(operator.local_reference_nodes[row, column])
        reference_position = int(np.flatnonzero(node_index[active_layers, row, column] == reference_node)[0])
        # Each edge of the local vertical spanning tree is one differential
        # coordinate.  The conductor rows contain its signed path incidence.
        for edge_position in range(active_layers.size - 1):
            for target_position, layer in enumerate(active_layers):
                coefficient = 0.0
                if target_position <= edge_position < reference_position:
                    coefficient = 1.0
                elif reference_position <= edge_position < target_position:
                    coefficient = -1.0
                if coefficient:
                    rows.append(int(node_index[layer, row, column]))
                    columns.append(dof)
                    values.append(coefficient)
            dof += 1
    if dof == 0:
        raise MfdmSolverError("MFDM relative formulation has no differential conductor degrees of freedom")
    return sparse.coo_matrix(
        (values, (rows, columns)),
        shape=(operator.node_count, dof),
    ).tocsc()


def _system_matrix(operator: MfdmOperator, frequency_hz: float) -> sparse.csc_matrix:
    if not np.isfinite(frequency_hz) or frequency_hz <= 0.0:
        raise MfdmSolverError("MFDM port solves require a finite frequency_hz > 0")
    omega = 2.0 * pi * frequency_hz
    rows: list[int] = []
    columns: list[int] = []
    values: list[complex] = []
    separations, _epsilon_rs, loss_tangents = operator.material.gap_properties(operator.geometry.conductor_area_fractions.shape[0] - 1)
    for gap_value, node_a_value, node_b_value, capacitance in operator.capacitance_stamps:
        gap = int(gap_value)
        admittance = omega * capacitance * loss_tangents[gap] + 1j * omega * capacitance
        _append_outer(rows, columns, values, (int(node_a_value), int(node_b_value)), (1.0, -1.0), admittance)
    conductivities, thicknesses = operator.material.conductor_properties(operator.geometry.conductor_area_fractions.shape[0])
    face_impedances = tuple(
        copper_two_face_surface_impedance(
            frequency_hz,
            conductivities[index],
            thicknesses[index],
            operator.material.permeability_h_per_m,
        )
        for index in range(len(conductivities))
    )
    for edge in range(operator.coupled_edge_gap_ids.shape[0]):
        valid_slots = np.flatnonzero(operator.coupled_edge_gap_ids[edge] >= 0)
        if valid_slots.size == 0:  # operator validation makes this defensive only
            continue
        gap_ids = operator.coupled_edge_gap_ids[edge, valid_slots]
        gap_count = valid_slots.size
        # For gap-loop currents q, an interior conductor has upper/lower face
        # currents [-q_above, +q_below].  Its finite-thickness two-face
        # impedance therefore yields -Zc*csch(gamma*t) off-diagonals at DC
        # and decouples as the skin depth becomes small.
        face_current_maps: list[NDArray[np.float64]] = []
        face_geometries: list[NDArray[np.float64]] = []
        voltage_map_nodes: list[int] = []
        for column, (slot, gap_value) in enumerate(zip(valid_slots, gap_ids, strict=True)):
            gap = int(gap_value)
            voltage_map_nodes.extend(int(node) for node in operator.coupled_edge_nodes[edge, slot])
        node_ids = tuple(dict.fromkeys(voltage_map_nodes))
        voltage_map = np.zeros((len(node_ids), gap_count), dtype=np.float64)
        local_index = {node: index for index, node in enumerate(node_ids)}
        for column, slot in enumerate(valid_slots):
            n0, n1, n2, n3 = (int(node) for node in operator.coupled_edge_nodes[edge, slot])
            # Stored node order is [top_a, top_b, bottom_b, bottom_a].
            for node, sign in ((n0, 1.0), (n1, -1.0), (n2, 1.0), (n3, -1.0)):
                voltage_map[local_index[node], column] += sign
        for conductor in range(len(face_impedances)):
            mapping = np.zeros((2, gap_count), dtype=np.float64)
            face_geometry = np.zeros((2, 2), dtype=np.float64)
            for column, gap_value in enumerate(gap_ids):
                gap = int(gap_value)
                if conductor == gap:  # lower face of conductor at gap top
                    mapping[1, column] += 1.0
                    face_geometry[1, 1] = operator.coupled_edge_gap_factors[edge, valid_slots[column]]
                if conductor == gap + 1:  # upper face of conductor at gap bottom
                    mapping[0, column] -= 1.0
                    face_geometry[0, 0] = operator.coupled_edge_gap_factors[edge, valid_slots[column]]
            # An internal conductor sees two gap-current supports.  Its face
            # self terms use their individual support widths; the two-face
            # transfer uses only their exact common support width.
            if 0 < conductor < len(face_impedances) - 1:
                cross = operator.coupled_edge_cross_factors[edge, conductor - 1]
                if cross > 0.0:
                    face_geometry[0, 1] = cross
                    face_geometry[1, 0] = cross
            face_current_maps.append(mapping)
            face_geometries.append(face_geometry)
        gap_inductance = np.asarray(
            [
                1j * omega * operator.material.permeability_h_per_m * separations[int(gap)] * operator.coupled_edge_gap_factors[edge, slot]
                for slot, gap in zip(valid_slots, gap_ids, strict=True)
            ],
            dtype=np.complex128,
        )
        coupled_impedance = np.diag(gap_inductance)
        for conductor, (mapping, face_geometry) in enumerate(zip(face_current_maps, face_geometries, strict=True)):
            if np.any(face_geometry > 0.0):
                coupled_impedance += mapping.T @ (face_impedances[conductor] * face_geometry) @ mapping
        if not np.all(np.isfinite(coupled_impedance)):
            raise MfdmSolverError("non-finite MFDM coupled edge impedance")
        try:
            local_stamp = voltage_map @ np.linalg.solve(coupled_impedance, voltage_map.T)
        except np.linalg.LinAlgError as exc:
            raise MfdmSolverError("MFDM coupled edge impedance is singular") from exc
        for left, node_left in enumerate(node_ids):
            for right, node_right in enumerate(node_ids):
                value = local_stamp[left, right]
                if value != 0.0:
                    rows.append(node_left)
                    columns.append(node_right)
                    values.append(value)
    return sparse.coo_matrix((values, (rows, columns)), shape=(operator.node_count, operator.node_count), dtype=np.complex128).tocsc()


def _solve_structural_components(
    reduced: sparse.csc_matrix,
    rhs: NDArray[np.complex128],
) -> tuple[NDArray[np.complex128], tuple[float, ...], tuple[NDArray[np.intp], ...]]:
    """Factor and solve each exact graph component independently.

    A monolithic sparse factorization is permitted to use pivot operations
    across structurally disconnected blocks.  That is normally harmless, but
    it can turn an analytically zero transfer impedance into a few micro-ohms
    after cancellation.  Keep disconnected blocks separate, and never submit
    an all-zero excitation column to a block solve: the corresponding voltage
    columns stay *bitwise* zero outside their source component.

    Every block is still factored, even without an external port, so a hidden
    singular island fails closed rather than being silently ignored.
    """

    structural = reduced.copy().tocsc()
    structural.eliminate_zeros()
    if structural.shape[0] == 0:
        raise MfdmSolverError("MFDM relative-conductor nodal system has no unknowns")
    pattern = structural.copy()
    pattern.data = np.ones(pattern.nnz, dtype=np.int8)
    component_count, component_labels = connected_components(pattern, directed=False, return_labels=True)
    solution = np.zeros(rhs.shape, dtype=np.complex128)
    condition_estimates: list[float] = []
    component_indices: list[NDArray[np.intp]] = []
    for component in range(component_count):
        indices = np.flatnonzero(component_labels == component).astype(np.intp, copy=False)
        component_indices.append(indices)
        block = structural[indices, :][:, indices].tocsc()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", MatrixRankWarning)
                factor = splu(block)
        except Exception as exc:  # SuperLU errors are implementation-specific
            raise MfdmSolverError(
                "MFDM relative-conductor nodal component is singular or ill-posed; topology was not solved"
            ) from exc
        # A dense condition number is deliberately limited to small blocks;
        # for a larger block no optimistic numerical certificate is emitted.
        if block.shape[0] <= 256:
            try:
                condition = float(np.linalg.cond(block.toarray()))
            except np.linalg.LinAlgError:
                condition = float("nan")
            if not np.isfinite(condition):
                raise MfdmSolverError(
                    "MFDM relative-conductor nodal component is numerically ill-posed; topology was not solved"
                )
            condition_estimates.append(condition)
        else:
            condition_estimates.append(float("nan"))
        block_rhs = rhs[indices, :]
        active_columns = np.flatnonzero(np.any(block_rhs != 0.0, axis=0))
        if active_columns.size:
            try:
                solution[np.ix_(indices, active_columns)] = factor.solve(block_rhs[:, active_columns])
            except Exception as exc:  # defensive: factorization succeeded but solve did not
                raise MfdmSolverError(
                    "MFDM relative-conductor component solve failed; topology was not solved"
                ) from exc
    return solution, tuple(condition_estimates), tuple(component_indices)


def _raw_pairwise_reciprocity(impedance: NDArray[np.complex128]) -> tuple[float, float]:
    """Return worst raw pair error using a fixed 100 micro-ohm floor."""

    mismatch = np.abs(impedance - impedance.T)
    scale = np.maximum(np.maximum(np.abs(impedance), np.abs(impedance.T)), 100.0e-6)
    return float(np.max(mismatch / scale, initial=0.0)), float(np.max(mismatch, initial=0.0))


def solve_mfdm(operator: MfdmOperator, frequency_hz: float, ports: tuple[MfdmPort, ...] | list[MfdmPort]) -> MfdmSolveResult:
    """Solve an open multiport Z matrix with local relative conductor voltages.

    One common-voltage coordinate is removed per active raster column.  All
    port voltages are reconstructed as differential incidences, so neither of
    the adjacent conductor layers is implicitly ideal-grounded.  The general
    path is a direct sparse LU solve, not an iterative least-squares fallback.
    """

    port_tuple = tuple(ports)
    incidence = _port_incidence(operator, port_tuple)
    system = _system_matrix(operator, float(frequency_hz))
    projection = _relative_projection(operator)
    reduced = (projection.conj().T @ system @ projection).tocsc()
    reduced.eliminate_zeros()
    rhs = np.asarray(projection.conj().T @ incidence, dtype=np.complex128)
    solution_reduced, component_conditions, component_indices = _solve_structural_components(reduced, rhs)
    solution = np.asarray(projection @ solution_reduced, dtype=np.complex128)
    raw_impedance = np.asarray(incidence.T @ solution, dtype=np.complex128)
    # Normwise backward residual.  Dividing only by ||b|| falsely rejects a
    # well-factored but high-impedance PDN block, where ||A||*||x|| dominates
    # the physically relevant solve scale.
    residual_vector = reduced @ solution_reduced - rhs
    residual_denominator = max(
        float(np.linalg.norm(reduced.data)) * float(np.linalg.norm(solution_reduced)) + float(np.linalg.norm(rhs)),
        float(np.finfo(np.float64).tiny),
    )
    residual = float(np.linalg.norm(residual_vector) / residual_denominator)
    pair_reciprocity, pair_reciprocity_absolute = _raw_pairwise_reciprocity(raw_impedance)
    reciprocity_absolute = float(np.linalg.norm(raw_impedance - raw_impedance.T))
    # Preserve the legacy matrix-norm metric while adding a strict pairwise
    # metric below.  A large diagonal may otherwise hide an inaccurate tiny
    # transfer term, which is precisely the PDN use case here.
    reciprocity = float(
        reciprocity_absolute
        / max(float(np.linalg.norm(raw_impedance)), float(np.finfo(np.float64).tiny))
    )
    raw_validation_passed = residual <= 1.0e-12 and pair_reciprocity <= 1.0e-3
    # The algebraic MFDM matrix is reciprocal.  Never return a curve when the
    # unsymmetrized solve fails its residual/reciprocity gate: a caller could
    # otherwise mistake diagnostic raw values for product impedance.
    if not raw_validation_passed:
        raise MfdmSolverError(
            "MFDM raw solve validation failed: "
            f"backward residual={residual:.3e}, pair reciprocity={pair_reciprocity:.3e}, "
            f"absolute mismatch={pair_reciprocity_absolute:.3e} ohm"
        )
    impedance = (raw_impedance + raw_impedance.T) * 0.5
    hermitian = (impedance + impedance.conj().T) * 0.5
    minimum = float(np.min(np.linalg.eigvalsh(hermitian)).real)
    scale = max(float(np.linalg.norm(impedance, ord=2)), 1.0e-18)
    tolerance = 1.0e-9 * scale
    condition = max(component_conditions) if component_conditions and all(np.isfinite(item) for item in component_conditions) else float("nan")
    component_errors: list[float] = []
    for component, (indices, component_condition) in enumerate(zip(component_indices, component_conditions, strict=True)):
        # A source column belongs to precisely one structural block.  This is
        # also the port submatrix whose forward error can be bounded by the
        # component condition number in the ordinary direct-solve model.
        port_indices = np.flatnonzero(np.any(rhs[indices, :] != 0.0, axis=0))
        if not port_indices.size:
            component_errors.append(0.0)
            continue
        if not np.isfinite(component_condition):
            component_errors.append(float("nan"))
            continue
        component_norm = float(np.linalg.norm(raw_impedance[np.ix_(port_indices, port_indices)], ord=2))
        component_errors.append(component_condition * np.finfo(np.float64).eps * component_norm)
    forward_error = max(component_errors) if component_errors and all(np.isfinite(item) for item in component_errors) else float("nan")
    solve_quality_estimate_passed = bool(
        raw_validation_passed
        and np.isfinite(forward_error)
        and forward_error <= 0.1e-6
    )
    # A residual-sized allowance belongs in the passivity gate; a global fixed
    # 1e-9 scale can falsely condemn a very small, accurately solved block.
    tolerance = max(tolerance, residual * scale * 8.0)
    passive = minimum >= -tolerance
    if not passive:
        raise MfdmSolverError(
            "MFDM open-port impedance is non-passive: "
            f"minimum Hermitian eigenvalue={minimum:.3e} ohm, "
            f"tolerance={tolerance:.3e} ohm"
        )
    diagnostics = MfdmDiagnostics(
        residual_relative=residual,
        reciprocity_relative=reciprocity,
        reciprocity_absolute_ohm=reciprocity_absolute,
        min_hermitian_impedance_eigenvalue_ohm=minimum,
        passivity_tolerance_ohm=tolerance,
        passive=passive,
        condition_estimate=condition,
        gauge_node=operator.gauge_node,
        local_reference_count=int(np.count_nonzero(operator.local_reference_nodes >= 0)),
        raw_reciprocity_pair_max_relative=pair_reciprocity,
        raw_reciprocity_pair_max_absolute_ohm=pair_reciprocity_absolute,
        raw_validation_passed=raw_validation_passed,
        forward_error_estimate_ohm=forward_error,
        solve_quality_estimate_passed=solve_quality_estimate_passed,
        structural_component_count=len(component_indices),
        physical_sheet_count=len(operator.sheet_owner_ids),
        two_face_sheet_count=len(operator.two_face_sheet_owner_ids),
    )
    return MfdmSolveResult(float(frequency_hz), tuple(port.port_id for port in port_tuple), impedance, solution, diagnostics, raw_impedance)


__all__ = [
    "EPSILON_0_F_PER_M",
    "MU_0_H_PER_M",
    "MfdmCutCellGeometry",
    "MfdmDiagnostics",
    "MfdmEdgeStrip",
    "MfdmMaterial",
    "MfdmNode",
    "MfdmOperator",
    "MfdmPort",
    "MfdmSolveResult",
    "MfdmSolverError",
    "compile_mfdm_operator",
    "copper_surface_impedance",
    "copper_two_face_surface_impedance",
    "solve_mfdm",
]
