"""Fail-closed SPD artwork adapter for the sparse MFDM kernel.

The modal evaluator intentionally reduces an SPD to one effective PWR/GND
pair.  This module is the separate, bounded path used to experiment with an
exact-artwork multilayer finite-difference model.  It does *not* replace the
production evaluation route yet.

Two rules are deliberately non-negotiable here:

* different nets on the same physical conductor layer are retained as
  different labels; they are never unioned or selected by a dominant fill;
* when one raster cell contains two such artworks, the build is rejected with
  a quantitative diagnostic.  A caller must refine/subdivide the cell or use
  a future multi-patch/triangular mesh -- guessing would create a short.

The public input is normalized scenario artwork (``plane_geometries`` plus
attachments), so this adapter can be exercised independently of the GUI and
without reparsing a multi-gigabyte raw SPD file.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from ..domain import ProjectSpec
from .. import services
from .mfdm import (
    MfdmCutCellGeometry,
    MfdmEdgeStrip,
    MfdmMaterial,
    MfdmNode,
    MfdmOperator,
    MfdmPort,
    MfdmSolveResult,
    MfdmSolverError,
    compile_mfdm_operator,
    solve_mfdm,
)


class MfdmArtworkAdapterError(MfdmSolverError):
    """Raised when SPD artwork cannot be represented without a topology guess."""


@dataclass(frozen=True, slots=True)
class MfdmArtwork:
    """One electrically distinct net surface on one physical layer.

    ``geometry_um`` is a Shapely geometry in SPD micrometre coordinates.  The
    adapter accepts it as ``Any`` to avoid making Shapely a domain-model type.
    """

    layer: str
    net: str
    geometry_um: Any

    def __post_init__(self) -> None:
        if not self.layer.strip() or not self.net.strip():
            raise MfdmArtworkAdapterError("artwork layer and net must not be empty")
        geometry = self.geometry_um
        if geometry is None or bool(getattr(geometry, "is_empty", True)):
            raise MfdmArtworkAdapterError("artwork geometry must not be empty")
        if not bool(getattr(geometry, "is_valid", False)) or float(getattr(geometry, "area", 0.0)) <= 0.0:
            raise MfdmArtworkAdapterError("artwork geometry must be valid and have positive area")


@dataclass(frozen=True, slots=True)
class MfdmRasterDiagnostics:
    """Auditable representation evidence for one artwork raster attempt."""

    layer_order: tuple[str, ...]
    bounds_um: tuple[float, float, float, float]
    cell_um: float
    shape: tuple[int, int]
    artwork_count: int
    active_cells_per_layer: tuple[int, ...]
    mixed_cells_per_layer: tuple[int, ...]
    mixed_area_um2_per_layer: tuple[float, ...]
    mixed_net_pairs: tuple[str, ...]
    shared_edge_check_performed: bool
    incompatible_shared_gap_edge_count: int
    incompatible_shared_gap_pairs: tuple[str, ...]
    port_support_fraction: float | None = None

    @property
    def mixed_cell_count(self) -> int:
        return sum(self.mixed_cells_per_layer)


@dataclass(frozen=True, slots=True)
class MfdmRasterBuild:
    """A compiled-ready cut-cell raster, or a quantified fail-closed result."""

    diagnostics: MfdmRasterDiagnostics
    geometry: MfdmCutCellGeometry | None
    artwork_labels: Mapping[tuple[str, str], int]
    cell_bounds_um: tuple[NDArray[np.float64], NDArray[np.float64]]
    cell_pieces_um: NDArray[np.object_] | None

    def __post_init__(self) -> None:
        """Freeze derived raster evidence before it is handed to a worker/cache.

        A frozen dataclass alone does not make a dict or NumPy array immutable.
        Copying here prevents a caller from changing label identity or cell
        bounds after a geometry has been compiled, which would otherwise make
        a Windows worker's diagnostic/provenance disagree with its solve.
        """

        labels = dict(self.artwork_labels)
        if not labels or any(
            not isinstance(key, tuple)
            or len(key) != 2
            or not all(isinstance(item, str) and item.strip() for item in key)
            or not isinstance(value, (int, np.integer))
            or value <= 0
            for key, value in labels.items()
        ):
            raise MfdmArtworkAdapterError("artwork_labels must map non-empty (layer, net) pairs to positive integers")
        if len(set(labels.values())) != len(labels):
            raise MfdmArtworkAdapterError("artwork_labels must not assign one label to different physical artworks")
        edges: list[NDArray[np.float64]] = []
        for index, values in enumerate(self.cell_bounds_um):
            array = np.asarray(values, dtype=np.float64).copy()
            if array.ndim != 1 or array.size < 2 or not np.all(np.isfinite(array)) or np.any(np.diff(array) <= 0.0):
                raise MfdmArtworkAdapterError(f"cell_bounds_um[{index}] must be a strictly increasing finite edge array")
            array.setflags(write=False)
            edges.append(array)
        if len(edges) != 2:
            raise MfdmArtworkAdapterError("cell_bounds_um must contain exactly x/y edge arrays")
        pieces = None
        if self.cell_pieces_um is not None:
            pieces = np.asarray(self.cell_pieces_um, dtype=object).copy()
            pieces.setflags(write=False)
        object.__setattr__(self, "artwork_labels", MappingProxyType(labels))
        object.__setattr__(self, "cell_bounds_um", (edges[0], edges[1]))
        object.__setattr__(self, "cell_pieces_um", pieces)

    @property
    def is_representable(self) -> bool:
        return self.geometry is not None

    def require_geometry(self) -> MfdmCutCellGeometry:
        if self.geometry is None:
            detail = ", ".join(self.diagnostics.mixed_net_pairs[:4]) or "unknown artwork pair"
            raise MfdmArtworkAdapterError(
                "MFDM raster is not representable: "
                f"{self.diagnostics.mixed_cell_count} mixed same-layer cell(s) at "
                f"{self.diagnostics.cell_um:g} um ({detail}). Refine/subdivide the mesh; "
                "do not union different nets."
            )
        return self.geometry


@dataclass(frozen=True, slots=True)
class MfdmWeightedPort:
    """A finite-area, co-located differential P/G terminal footprint.

    This is intentionally stricter than an arbitrary two-terminal via pair.
    The present MFDM kernel removes one common voltage per raster column, so a
    distributed terminal is valid only as a weighted sum of *co-located*
    positive/negative local differential ports.  Distinct P/G footprints or a
    reconstructed via route would need the missing vertical topology and are
    rejected rather than guessed.
    """

    port_id: str
    positive_conductor: int
    negative_conductor: int
    footprint_um: Any
    positive_label: int | None = None
    negative_label: int | None = None

    def __post_init__(self) -> None:
        if not self.port_id.strip() or self.positive_conductor == self.negative_conductor:
            raise MfdmArtworkAdapterError("weighted port needs an ID and two distinct conductors")
        footprint = self.footprint_um
        if footprint is None or bool(getattr(footprint, "is_empty", True)) or float(getattr(footprint, "area", 0.0)) <= 0.0:
            raise MfdmArtworkAdapterError("weighted port footprint must have positive area")


@dataclass(frozen=True, slots=True)
class MfdmWeightedSolveResult:
    """Open multiport result reconstructed from finite-area local terminals."""

    frequency_hz: float
    port_ids: tuple[str, ...]
    impedance_ohm: NDArray[np.complex128]
    local_result: MfdmSolveResult
    local_port_count: int
    support_fractions: tuple[float, ...]

    def __post_init__(self) -> None:
        ports = tuple(self.port_ids)
        impedance = np.asarray(self.impedance_ohm, dtype=np.complex128).copy()
        support = tuple(float(value) for value in self.support_fractions)
        if not ports or len(set(ports)) != len(ports) or any(not isinstance(item, str) or not item.strip() for item in ports):
            raise MfdmArtworkAdapterError("weighted solve result needs unique non-empty port IDs")
        if impedance.shape != (len(ports), len(ports)) or not np.all(np.isfinite(impedance)):
            raise MfdmArtworkAdapterError("weighted solve result has an invalid impedance matrix")
        if len(support) != len(ports) or any(not np.isfinite(value) or not 0.0 < value <= 1.0 for value in support):
            raise MfdmArtworkAdapterError("weighted solve result has invalid support fractions")
        impedance.setflags(write=False)
        object.__setattr__(self, "port_ids", ports)
        object.__setattr__(self, "impedance_ohm", impedance)
        object.__setattr__(self, "support_fractions", support)


def _rounded_bounds(bounds_um: tuple[float, float, float, float], cell_um: float) -> tuple[float, float, float, float]:
    minimum_x, minimum_y, maximum_x, maximum_y = bounds_um
    if not np.all(np.isfinite((minimum_x, minimum_y, maximum_x, maximum_y))) or maximum_x <= minimum_x or maximum_y <= minimum_y:
        raise MfdmArtworkAdapterError("raster bounds must be finite and have positive area")
    return (
        floor(minimum_x / cell_um) * cell_um,
        floor(minimum_y / cell_um) * cell_um,
        ceil(maximum_x / cell_um) * cell_um,
        ceil(maximum_y / cell_um) * cell_um,
    )


def _merged_artwork(artwork: Sequence[MfdmArtwork], layer_order: tuple[str, ...]) -> tuple[dict[tuple[str, str], Any], dict[tuple[str, str], int]]:
    try:
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover - package dependency is declared
        raise MfdmArtworkAdapterError("Shapely is required for exact SPD artwork") from exc
    known_layers = {item.casefold() for item in layer_order}
    groups: dict[tuple[str, str], list[Any]] = {}
    canonical_layer = {item.casefold(): item for item in layer_order}
    for item in artwork:
        if item.layer.casefold() not in known_layers:
            raise MfdmArtworkAdapterError(f"artwork layer {item.layer!r} is not in the requested physical slab")
        key = (canonical_layer[item.layer.casefold()], item.net)
        groups.setdefault(key, []).append(item.geometry_um)
    if not groups:
        raise MfdmArtworkAdapterError("MFDM artwork slab has no retained conductor geometry")
    merged = {key: unary_union(value) for key, value in groups.items()}
    if any(shape.is_empty or not shape.is_valid for shape in merged.values()):
        raise MfdmArtworkAdapterError("same-net SPD artwork union is invalid")
    labels = {key: index + 1 for index, key in enumerate(sorted(merged, key=lambda value: (value[0].casefold(), value[1].casefold())))}
    return merged, labels


def build_mfdm_artwork_raster(
    artwork: Sequence[MfdmArtwork],
    *,
    layer_order: Sequence[str],
    cell_um: float,
    bounds_um: tuple[float, float, float, float] | None = None,
    sliver_fraction: float = 1.0e-9,
) -> MfdmRasterBuild:
    """Create an exact-area/edge cut-cell raster without net merging.

    A raster cell can hold only one electrical artwork per physical layer in
    the current MFDM core.  This function calculates exact Shapely cut areas,
    detects every counterexample, and returns ``geometry=None`` when any are
    found.  The diagnostic is deliberately usable for adaptive refinement.
    """

    if not np.isfinite(cell_um) or cell_um <= 0.0:
        raise MfdmArtworkAdapterError("cell_um must be finite and > 0")
    layers = tuple(str(item) for item in layer_order)
    if len(layers) < 2 or len({item.casefold() for item in layers}) != len(layers):
        raise MfdmArtworkAdapterError("layer_order needs at least two unique physical layers")
    merged, labels = _merged_artwork(artwork, layers)
    if bounds_um is None:
        all_bounds = np.asarray([shape.bounds for shape in merged.values()], dtype=float)
        bounds_um = (float(np.min(all_bounds[:, 0])), float(np.min(all_bounds[:, 1])), float(np.max(all_bounds[:, 2])), float(np.max(all_bounds[:, 3])))
    x0, y0, x1, y1 = _rounded_bounds(bounds_um, float(cell_um))
    nx = int(round((x1 - x0) / cell_um))
    ny = int(round((y1 - y0) / cell_um))
    if nx <= 0 or ny <= 0 or nx * ny > 2_000_000:
        raise MfdmArtworkAdapterError(f"raster has {nx * ny:,} cells; use a bounded slab or a coarser exploratory mesh")
    x_edges = np.linspace(x0, x1, nx + 1, dtype=np.float64)
    y_edges = np.linspace(y0, y1, ny + 1, dtype=np.float64)
    cell_area = float(cell_um * cell_um)
    layer_index = {name.casefold(): index for index, name in enumerate(layers)}
    fractions = np.zeros((len(layers), ny, nx), dtype=np.float64)
    conductor_labels = np.zeros((len(layers), ny, nx), dtype=np.int64)
    pieces: NDArray[np.object_] = np.empty((len(layers), ny, nx), dtype=object)
    pieces.fill(None)
    mixed = np.zeros((len(layers), ny, nx), dtype=bool)
    mixed_area = np.zeros(len(layers), dtype=np.float64)
    mixed_pairs: set[str] = set()
    try:
        import shapely
    except ImportError as exc:  # pragma: no cover
        raise MfdmArtworkAdapterError("Shapely is required for exact SPD artwork") from exc

    # Clip once before the per-cell operations.  In particular, a board-sized
    # DGND pour otherwise makes a small bounded VQPS slab repeatedly traverse
    # millions of irrelevant vertices.  This is a geometric crop only, never
    # a net union or an electrical boundary condition.
    crop = shapely.box(x0, y0, x1, y1)
    merged = {key: shapely.intersection(shape, crop) for key, shape in merged.items()}

    # Exact cell clipping is intentionally only over each artwork bounding box;
    # board-scale empty cells never call GEOS.
    for (layer, net), shape in merged.items():
        if shape.is_empty:
            continue
        index = layer_index[layer.casefold()]
        label = labels[(layer, net)]
        min_x, min_y, max_x, max_y = shape.bounds
        start_x = max(0, int(floor((min_x - x0) / cell_um)))
        stop_x = min(nx, int(ceil((max_x - x0) / cell_um)))
        start_y = max(0, int(floor((min_y - y0) / cell_um)))
        stop_y = min(ny, int(ceil((max_y - y0) / cell_um)))
        columns = np.arange(start_x, stop_x, dtype=np.intp)
        rows = np.arange(start_y, stop_y, dtype=np.intp)
        if not columns.size or not rows.size:
            continue
        grid_rows, grid_columns = np.meshgrid(rows, columns, indexing="ij")
        cells = shapely.box(
            x_edges[grid_columns], y_edges[grid_rows],
            x_edges[grid_columns + 1], y_edges[grid_rows + 1],
        )
        clipped = shapely.intersection(shape, cells)
        areas = np.asarray(shapely.area(clipped), dtype=float)
        for local_row, local_column in np.argwhere(areas > cell_area * sliver_fraction):
            row = int(grid_rows[local_row, local_column])
            column = int(grid_columns[local_row, local_column])
            area = float(areas[local_row, local_column])
            old_label = int(conductor_labels[index, row, column])
            if old_label == 0:
                conductor_labels[index, row, column] = label
                fractions[index, row, column] = min(1.0, area / cell_area)
                pieces[index, row, column] = clipped[local_row, local_column]
            elif old_label != label:
                mixed[index, row, column] = True
                mixed_area[index] += area
                old_key = next(key for key, value in labels.items() if value == old_label)
                mixed_pairs.add(f"{layer}:{old_key[1]} <> {net}")

    # A mixed cell is never passed to the kernel.  We still preserve its count
    # and exact reported intersected area to guide refinement.
    active_counts = tuple(int(np.count_nonzero(fractions[index] > sliver_fraction)) for index in range(len(layers)))
    diagnostics = MfdmRasterDiagnostics(
        layer_order=layers,
        bounds_um=(x0, y0, x1, y1),
        cell_um=float(cell_um),
        shape=(ny, nx),
        artwork_count=len(merged),
        active_cells_per_layer=active_counts,
        mixed_cells_per_layer=tuple(int(np.count_nonzero(mixed[index])) for index in range(len(layers))),
        mixed_area_um2_per_layer=tuple(float(value) for value in mixed_area),
        mixed_net_pairs=tuple(sorted(mixed_pairs)),
        shared_edge_check_performed=False,
        incompatible_shared_gap_edge_count=0,
        incompatible_shared_gap_pairs=(),
    )
    if np.any(mixed):
        return MfdmRasterBuild(diagnostics, None, labels, (x_edges, y_edges), None)

    # Partition every physical cell boundary where any retained conductor
    # intersection starts or ends.  A strip keeps the per-layer *net* label,
    # not just an aggregate width: disjoint upper/lower supports must enter
    # independent K matrices in the sparse MFDM core.
    from shapely.geometry import LineString, Point
    label_to_shape = {value: merged[key] for key, value in labels.items()}

    def _line_coordinates(geometry: Any) -> list[tuple[float, float]]:
        if geometry.is_empty:
            return []
        kind = geometry.geom_type
        if kind in {"LineString", "LinearRing"}:
            return [(float(x), float(y)) for x, y, *_rest in geometry.coords]
        if hasattr(geometry, "geoms"):
            return [coordinate for child in geometry.geoms for coordinate in _line_coordinates(child)]
        return []

    def _maximal_strips(
        line: Any,
        layer_shapes: tuple[Any | None, ...],
        layer_labels: tuple[int, ...],
        *,
        vary_x: bool,
    ) -> tuple[MfdmEdgeStrip, ...]:
        start = float(line.coords[0][0 if vary_x else 1])
        stop = float(line.coords[-1][0 if vary_x else 1])
        span = stop - start
        breakpoints = [start, stop]
        intersections = []
        for shape in layer_shapes:
            intersection = line.intersection(shape) if shape is not None else None
            intersections.append(intersection)
            if intersection is not None:
                for x, y in _line_coordinates(intersection):
                    value = x if vary_x else y
                    if start < value < stop:
                        breakpoints.append(value)
        values = sorted({round(value, 9) for value in breakpoints})
        result: list[MfdmEdgeStrip] = []
        for left, right in zip(values[:-1], values[1:], strict=True):
            if right - left <= max(1.0e-12, span * sliver_fraction):
                continue
            middle = 0.5 * (left + right)
            point = Point(middle, float(line.coords[0][1])) if vary_x else Point(float(line.coords[0][0]), middle)
            # Intersections supply the exact breakpoints, but cannot classify
            # continuity by themselves: a polygon that merely *ends* on this
            # cell boundary has a LineString intersection and ``covers`` its
            # midpoint even though no copper crosses into the neighbouring
            # cell.  Strict containment in the cropped source polygon is the
            # correct one-sided-boundary discriminator.  A true crossing puts
            # the boundary midpoint in the polygon interior.
            signature = tuple(
                0 if shape is None or not shape.contains(point) else int(label)
                for shape, label in zip(layer_shapes, layer_labels, strict=True)
            )
            width = (right - left) / span
            if result and result[-1].conductor_labels == signature:
                previous = result.pop()
                result.append(MfdmEdgeStrip(previous.width_fraction + width, signature))
            else:
                result.append(MfdmEdgeStrip(width, signature))
        if not result:
            return (MfdmEdgeStrip(1.0, (0,) * len(layers)),)
        # Rounding endpoints may leave a negligible closure remainder.  Fold
        # it into the last maximal strip so the core sees an exact partition.
        total = sum(strip.width_fraction for strip in result)
        if not np.isclose(total, 1.0, rtol=0.0, atol=1.0e-10):
            last = result.pop()
            result.append(MfdmEdgeStrip(last.width_fraction + (1.0 - total), last.conductor_labels))
        return tuple(result)

    horizontal = np.zeros((len(layers), ny, max(nx - 1, 0)), dtype=np.float64)
    vertical = np.zeros((len(layers), max(ny - 1, 0), nx), dtype=np.float64)
    gap_horizontal = np.zeros((len(layers) - 1, ny, max(nx - 1, 0)), dtype=np.float64)
    gap_vertical = np.zeros((len(layers) - 1, max(ny - 1, 0), nx), dtype=np.float64)
    common_horizontal = np.zeros((max(len(layers) - 2, 0), ny, max(nx - 1, 0)), dtype=np.float64)
    common_vertical = np.zeros((max(len(layers) - 2, 0), max(ny - 1, 0), nx), dtype=np.float64)

    def _record(strips: tuple[MfdmEdgeStrip, ...], coverage: NDArray[np.float64], gaps: NDArray[np.float64], common: NDArray[np.float64], row: int, column: int) -> None:
        for layer in range(len(layers)):
            coverage[layer, row, column] = sum(strip.width_fraction for strip in strips if strip.conductor_labels[layer] > 0)
        for gap in range(len(layers) - 1):
            gaps[gap, row, column] = sum(strip.width_fraction for strip in strips if gap in strip.active_gap_ids)
        for gap in range(max(len(layers) - 2, 0)):
            common[gap, row, column] = sum(strip.width_fraction for strip in strips if gap in strip.active_gap_ids and gap + 1 in strip.active_gap_ids)

    horizontal_strips: list[tuple[tuple[MfdmEdgeStrip, ...], ...]] = []
    for row in range(ny):
        strip_row: list[tuple[MfdmEdgeStrip, ...]] = []
        for column in range(max(nx - 1, 0)):
            selected = tuple(
                label_to_shape.get(int(conductor_labels[layer, row, column]))
                if conductor_labels[layer, row, column] > 0 and conductor_labels[layer, row, column] == conductor_labels[layer, row, column + 1]
                else None
                for layer in range(len(layers))
            )
            selected_labels = tuple(
                int(conductor_labels[layer, row, column])
                if selected[layer] is not None else 0 for layer in range(len(layers))
            )
            strips = _maximal_strips(LineString(((x_edges[column + 1], y_edges[row]), (x_edges[column + 1], y_edges[row + 1]))), selected, selected_labels, vary_x=False)
            _record(strips, horizontal, gap_horizontal, common_horizontal, row, column)
            strip_row.append(strips)
        horizontal_strips.append(tuple(strip_row))
    vertical_strips: list[tuple[tuple[MfdmEdgeStrip, ...]] ] = []
    for row in range(max(ny - 1, 0)):
        strip_row = []
        for column in range(nx):
            selected = tuple(
                label_to_shape.get(int(conductor_labels[layer, row, column]))
                if conductor_labels[layer, row, column] > 0 and conductor_labels[layer, row, column] == conductor_labels[layer, row + 1, column]
                else None
                for layer in range(len(layers))
            )
            selected_labels = tuple(
                int(conductor_labels[layer, row, column])
                if selected[layer] is not None else 0 for layer in range(len(layers))
            )
            strips = _maximal_strips(LineString(((x_edges[column], y_edges[row + 1]), (x_edges[column + 1], y_edges[row + 1]))), selected, selected_labels, vary_x=True)
            _record(strips, vertical, gap_vertical, common_vertical, row, column)
            strip_row.append(strips)
        vertical_strips.append(tuple(strip_row))
    diagnostics = MfdmRasterDiagnostics(
        layer_order=diagnostics.layer_order,
        bounds_um=diagnostics.bounds_um,
        cell_um=diagnostics.cell_um,
        shape=diagnostics.shape,
        artwork_count=diagnostics.artwork_count,
        active_cells_per_layer=diagnostics.active_cells_per_layer,
        mixed_cells_per_layer=diagnostics.mixed_cells_per_layer,
        mixed_area_um2_per_layer=diagnostics.mixed_area_um2_per_layer,
        mixed_net_pairs=diagnostics.mixed_net_pairs,
        shared_edge_check_performed=True,
        incompatible_shared_gap_edge_count=0,
        incompatible_shared_gap_pairs=(),
    )

    overlaps = np.zeros((len(layers) - 1, ny, nx), dtype=np.float64)
    for layer in range(len(layers) - 1):
        for row, column in np.argwhere((conductor_labels[layer] > 0) & (conductor_labels[layer + 1] > 0)):
            upper = pieces[layer, row, column]
            lower = pieces[layer + 1, row, column]
            overlaps[layer, row, column] = min(1.0, float(upper.intersection(lower).area / cell_area))
    geometry = MfdmCutCellGeometry(
        cell_areas_m2=np.full((ny, nx), cell_area * 1.0e-12, dtype=np.float64),
        conductor_area_fractions=fractions,
        conductor_labels=conductor_labels,
        horizontal_edge_fractions=horizontal,
        vertical_edge_fractions=vertical,
        gap_overlap_fractions=overlaps,
        dx_m=float(cell_um) * 1.0e-6,
        dy_m=float(cell_um) * 1.0e-6,
        sliver_fraction=sliver_fraction,
        horizontal_gap_edge_overlap_fractions=gap_horizontal,
        vertical_gap_edge_overlap_fractions=gap_vertical,
        horizontal_adjacent_gap_overlap_fractions=common_horizontal,
        vertical_adjacent_gap_overlap_fractions=common_vertical,
        horizontal_edge_strips=tuple(horizontal_strips),
        vertical_edge_strips=tuple(vertical_strips),
    )
    pieces.setflags(write=False)
    return MfdmRasterBuild(diagnostics, geometry, labels, (x_edges, y_edges), pieces)


def mfdm_material_from_project(project: ProjectSpec, layer_order: Sequence[str]) -> MfdmMaterial:
    """Build exact adjacent-gap material data from a physical project stack-up."""

    layers = tuple(str(item) for item in layer_order)
    by_name = {item.name.casefold(): (index, item) for index, item in enumerate(project.stackup_layers)}
    try:
        indexed = [by_name[item.casefold()] for item in layers]
    except KeyError as exc:
        raise MfdmArtworkAdapterError(f"unknown stack-up layer {exc.args[0]!r}") from exc
    indices = [item[0] for item in indexed]
    if indices != sorted(indices) or any(not item[1].is_conductor for item in indexed):
        raise MfdmArtworkAdapterError("MFDM layer order must be top-to-bottom physical conductor layers")
    separations: list[float] = []
    epsilon_rs: list[float] = []
    tangents: list[float] = []
    for upper, lower in zip(indices[:-1], indices[1:], strict=True):
        dielectric = project.stackup_layers[upper + 1:lower]
        if not dielectric or any(item.is_conductor or item.dk is None or item.df is None for item in dielectric):
            raise MfdmArtworkAdapterError("each bounded MFDM gap must contain only fully specified dielectric rows")
        terms = [item.thickness_um / float(item.dk) for item in dielectric]
        denominator = sum(terms)
        separation = sum(item.thickness_um for item in dielectric)
        separations.append(separation * 1.0e-6)
        epsilon_rs.append(separation / denominator)
        tangents.append(sum(term * float(item.df) for term, item in zip(terms, dielectric, strict=True)) / denominator)
    conductivities = tuple(float(item[1].conductivity_s_m) for item in indexed)
    thicknesses = tuple(float(item[1].thickness_um) * 1.0e-6 for item in indexed)
    return MfdmMaterial(
        upper_target_gap_m=separations[0],
        target_lower_gap_m=separations[1] if len(separations) > 1 else separations[0],
        upper_target_relative_permittivity=epsilon_rs[0],
        target_lower_relative_permittivity=epsilon_rs[1] if len(epsilon_rs) > 1 else epsilon_rs[0],
        upper_target_loss_tangent=tangents[0],
        target_lower_loss_tangent=tangents[1] if len(tangents) > 1 else tangents[0],
        gap_separations_m=tuple(separations),
        gap_relative_permittivities=tuple(epsilon_rs),
        gap_loss_tangents=tuple(tangents),
        conductivity_s_per_m=conductivities,
        copper_thickness_m=thicknesses,
    )


def artwork_from_project(project: ProjectSpec, attachments: Mapping[str, bytes], layer_order: Sequence[str]) -> tuple[MfdmArtwork, ...]:
    """Decode retained SPD primitives for every net on the bounded layer set."""

    required = {str(item).casefold() for item in layer_order}
    records = project.metadata.get("spd_import", {}).get("plane_geometries", ())
    if not isinstance(records, Sequence):
        raise MfdmArtworkAdapterError("project has no retained SPD plane geometry index")
    result: list[MfdmArtwork] = []
    for raw in records:
        if not isinstance(raw, Mapping) or str(raw.get("layer", "")).casefold() not in required:
            continue
        asset = str(raw.get("asset", ""))
        digest = str(raw.get("asset_sha256", ""))
        try:
            payload = services._decode_spd_geometry_asset(digest, attachments[asset])
            services._validate_spd_geometry_payload(payload, expected_layer=str(raw.get("layer", "")), expected_net=str(raw.get("net", "")))
            geometry = services._ordered_spd_geometry(payload)
        except (KeyError, ValueError) as exc:
            raise MfdmArtworkAdapterError(f"cannot decode retained SPD artwork {asset!r}") from exc
        if geometry is None:
            raise MfdmArtworkAdapterError(f"cannot construct valid ordered SPD artwork {asset!r}")
        result.append(MfdmArtwork(str(raw["layer"]), str(raw["net"]), geometry))
    if not result:
        raise MfdmArtworkAdapterError("no retained SPD artwork matches the requested physical slab")
    return tuple(result)


def _weighted_local_ports(
    build: MfdmRasterBuild,
    ports: Sequence[MfdmWeightedPort],
) -> tuple[tuple[MfdmPort, ...], NDArray[np.float64], tuple[float, ...]]:
    geometry = build.require_geometry()
    if build.cell_pieces_um is None:  # require_geometry above makes this defensive
        raise MfdmArtworkAdapterError("finite-area port support requires retained exact cut-cell artwork")
    try:
        from shapely.geometry import box
    except ImportError as exc:  # pragma: no cover
        raise MfdmArtworkAdapterError("Shapely is required for finite-area ports") from exc
    x_edges, y_edges = build.cell_bounds_um
    ny, nx = geometry.cell_areas_m2.shape
    local_ports: list[MfdmPort] = []
    columns: list[list[tuple[int, float]]] = []
    support: list[float] = []
    for port in ports:
        if not (0 <= port.positive_conductor < geometry.conductor_area_fractions.shape[0] and 0 <= port.negative_conductor < geometry.conductor_area_fractions.shape[0]):
            raise MfdmArtworkAdapterError(f"weighted port {port.port_id!r} uses a conductor outside the slab")
        footprint_area = float(port.footprint_um.area)
        local: list[tuple[int, float]] = []
        covered = 0.0
        min_x, min_y, max_x, max_y = port.footprint_um.bounds
        for row in range(max(0, int(floor((min_y - y_edges[0]) / build.diagnostics.cell_um))), min(ny, int(ceil((max_y - y_edges[0]) / build.diagnostics.cell_um)))):
            for column in range(max(0, int(floor((min_x - x_edges[0]) / build.diagnostics.cell_um))), min(nx, int(ceil((max_x - x_edges[0]) / build.diagnostics.cell_um)))):
                if geometry.conductor_area_fractions[port.positive_conductor, row, column] <= geometry.sliver_fraction or geometry.conductor_area_fractions[port.negative_conductor, row, column] <= geometry.sliver_fraction:
                    continue
                labels = geometry.conductor_labels
                if (port.positive_label is not None and labels[port.positive_conductor, row, column] != port.positive_label) or (port.negative_label is not None and labels[port.negative_conductor, row, column] != port.negative_label):
                    continue
                # Do not count a nominal raster cell merely because both
                # conductors have nonzero fill.  The terminal must lie in the
                # exact *common* P/G artwork in this cell; otherwise a tiny
                # unrelated sliver can falsely claim full support.
                positive_piece = build.cell_pieces_um[port.positive_conductor, row, column]
                negative_piece = build.cell_pieces_um[port.negative_conductor, row, column]
                if positive_piece is None or negative_piece is None:
                    continue
                area = float(port.footprint_um.intersection(positive_piece).intersection(negative_piece).area)
                if area <= 0.0:
                    continue
                identifier = len(local_ports)
                local_ports.append(MfdmPort(f"{port.port_id}#{identifier}", MfdmNode(port.positive_conductor, row, column), MfdmNode(port.negative_conductor, row, column)))
                local.append((identifier, area / footprint_area))
                covered += area
        fraction = covered / footprint_area
        if not local or fraction < 1.0 - 1.0e-9:
            raise MfdmArtworkAdapterError(
                f"weighted port {port.port_id!r} has only {fraction:.6%} co-located P/G artwork support; "
                "do not invent a via/return path outside the bounded slab"
            )
        # Normalize after the strict support check to keep one ampere total.
        total = sum(weight for _index, weight in local)
        columns.append([(index, weight / total) for index, weight in local])
        support.append(fraction)
    if not local_ports:
        raise MfdmArtworkAdapterError("at least one finite-area local port is required")
    weights = np.zeros((len(local_ports), len(ports)), dtype=np.float64)
    for column, entries in enumerate(columns):
        for row, weight in entries:
            weights[row, column] = weight
    return tuple(local_ports), weights, tuple(support)


def solve_mfdm_weighted_ports(operator: MfdmOperator, build: MfdmRasterBuild, frequency_hz: float, ports: Sequence[MfdmWeightedPort]) -> MfdmWeightedSolveResult:
    """Solve finite-area local P/G footprints by exact linear superposition.

    The weights prescribe a uniform current density over the supported common
    P/G footprint and report the corresponding weighted-average voltage.  It
    is not an equipotential metal-pad unknown-current terminal; that requires
    explicit pad/via topology which this bounded SPD artwork slab does not
    reconstruct.
    """

    port_tuple = tuple(ports)
    if not port_tuple or len({item.port_id for item in port_tuple}) != len(port_tuple):
        raise MfdmArtworkAdapterError("weighted ports must be non-empty with unique IDs")
    local_ports, weights, support = _weighted_local_ports(build, port_tuple)
    local_result = solve_mfdm(operator, frequency_hz, local_ports)
    impedance = np.asarray(weights.T @ local_result.impedance_ohm @ weights, dtype=np.complex128)
    impedance.setflags(write=False)
    return MfdmWeightedSolveResult(float(frequency_hz), tuple(item.port_id for item in port_tuple), impedance, local_result, len(local_ports), support)


__all__ = [
    "MfdmArtwork",
    "MfdmArtworkAdapterError",
    "MfdmRasterBuild",
    "MfdmRasterDiagnostics",
    "MfdmWeightedPort",
    "MfdmWeightedSolveResult",
    "artwork_from_project",
    "build_mfdm_artwork_raster",
    "mfdm_material_from_project",
    "solve_mfdm_weighted_ports",
]
