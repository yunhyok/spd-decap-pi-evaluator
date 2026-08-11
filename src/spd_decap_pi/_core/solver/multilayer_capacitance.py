"""Exact-artwork electrostatic bulk-capacitance extraction.

This is a deliberately small, fail-closed electrostatic companion to the
sparse MFDM work.  It constructs a *Maxwell* capacitance matrix from the
actual retained SPD polygons; it does not rasterise, select a dominant net,
or turn an adjacent PWR polygon into an ideal return.  It is useful for the
low-frequency P0 gate, where the PowerSI VQPS ports expose whether the
multiconductor plane topology is even represented correctly.

The model is a parallel-plate bulk term only. Adjacent physical conductor
surfaces are always considered. A true opening in an intermediate conductor
can optionally create a nearest-visible non-adjacent term, but only when the
caller supplies source-proven material for the missing conductor thickness.
The safe default disables such terms: SPD stack data identifies copper
thickness but not the resin/void/plating material inside an opening.
Fringing, coplanar fields, pads and vias are intentionally outside this
bounded extractor and must not be fitted from a Touchstone reference.
"""

from __future__ import annotations

from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
)
from dataclasses import dataclass
from math import isfinite
from os import cpu_count
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.sparse import csc_matrix, issparse

from ..domain import ProjectSpec
from .. import services


EPSILON_0_F_PER_M = 8.854_187_812_8e-12

# Keep each work group bounded so cancellation remains responsive even when a
# retained SPD polygon has a very large candidate set. Querying an array of
# upper geometries avoids one STRtree call per island; exact scalar overlays
# remain unchanged and run concurrently because Shapely 2 releases the GIL.
_ADJACENT_QUERY_BATCH_SIZE = 64
_ADJACENT_INTERSECTION_BATCH_SIZE = 256
_ADJACENT_INTERSECTION_WORKERS = min(8, max(1, cpu_count() or 1))
_ADJACENT_FUTURE_POLL_SECONDS = 0.05


class MultilayerCapacitanceError(ValueError):
    """Raised when exact artwork cannot support an unambiguous extraction."""


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


def _checkpoint(is_cancelled: CancelCallback | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise RuntimeError("evaluation cancelled")


def _report(
    progress: ProgressCallback | None,
    value: int,
    message: str,
) -> None:
    if progress is not None:
        progress(min(max(int(value), 0), 100), message)


def _should_report_work(completed: int, total: int) -> bool:
    interval = max(1, (max(total, 1) + 99) // 100)
    return completed >= total or completed % interval == 0


def _scalar_intersection_area_um2(left: Any, right: Any) -> float:
    """Return the legacy GEOS overlay area for one candidate pair.

    Shapely 2 releases the GIL during GEOS operations, so independent pairs
    can safely run in a bounded thread pool.  Keeping this operation scalar is
    intentional: every candidate still uses the exact overlay from before the
    batched fast path, including pairs where one geometry contains the other.
    """

    return float(left.intersection(right).area)


def _threaded_intersection_areas_um2(
    executor: ThreadPoolExecutor,
    left: NDArray[np.object_],
    right: NDArray[np.object_],
    *,
    is_cancelled: CancelCallback | None,
) -> NDArray[np.float64]:
    """Evaluate one bounded group in input order with cancellation polling."""

    if left.shape != right.shape or left.ndim != 1:
        raise MultilayerCapacitanceError(
            "adjacent-layer overlay work arrays are malformed"
        )
    futures = [
        executor.submit(_scalar_intersection_area_um2, left[index], right[index])
        for index in range(len(left))
    ]
    areas = np.empty(len(futures), dtype=np.float64)
    try:
        for index, future in enumerate(futures):
            while True:
                try:
                    areas[index] = float(
                        future.result(timeout=_ADJACENT_FUTURE_POLL_SECONDS)
                    )
                    break
                except FutureTimeoutError:
                    _checkpoint(is_cancelled)
            _checkpoint(is_cancelled)
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    return areas


@dataclass(frozen=True, slots=True)
class CapacitanceArtwork:
    """One electrically distinct net surface on one physical conductor layer.

    ``geometry_um`` is a valid Shapely polygonal geometry in SPD micrometres.
    Multiple entries for the same (layer, net) are allowed and safely unioned
    by the legacy dense extractor; entries for different nets are never
    unioned.  ``node_name`` is an optional, globally unique electrical-island
    identity.  The sparse island extractor uses it instead of ``net`` so two
    disconnected pieces of the same logical NET can never be silently
    coalesced.  Callers which already store a unique surface identity in
    ``net`` may leave it unset.
    """

    layer: str
    net: str
    geometry_um: Any
    node_name: str | None = None

    def __post_init__(self) -> None:
        if not self.layer.strip() or not self.net.strip():
            raise MultilayerCapacitanceError("artwork layer and net must not be empty")
        if self.node_name is not None:
            node_name = str(self.node_name).strip()
            if not node_name:
                raise MultilayerCapacitanceError(
                    "artwork node_name must not be blank when supplied"
                )
            object.__setattr__(self, "node_name", node_name)
        shape = self.geometry_um
        if shape is None or bool(getattr(shape, "is_empty", True)):
            raise MultilayerCapacitanceError("artwork geometry must not be empty")
        if not bool(getattr(shape, "is_valid", False)) or float(getattr(shape, "area", 0.0)) <= 0.0:
            raise MultilayerCapacitanceError("artwork geometry must be valid and have positive area")


@dataclass(frozen=True, slots=True)
class DielectricGap:
    """One physical dielectric gap in top-to-bottom conductor order."""

    separation_m: float
    relative_permittivity: float

    def __post_init__(self) -> None:
        if not isfinite(self.separation_m) or self.separation_m <= 0.0:
            raise MultilayerCapacitanceError("dielectric separation must be finite and > 0")
        if not isfinite(self.relative_permittivity) or self.relative_permittivity <= 0.0:
            raise MultilayerCapacitanceError("dielectric relative permittivity must be finite and > 0")


@dataclass(frozen=True, slots=True)
class MultilayerCapacitanceModel:
    """Exact geometry and stack data used to build a bulk Maxwell matrix."""

    layer_order: tuple[str, ...]
    gaps: tuple[DielectricGap, ...]
    artwork: tuple[CapacitanceArtwork, ...]
    opening_fill_by_layer: Mapping[str, DielectricGap] | None = None
    enable_nonadjacent_opening_coupling: bool = False

    def __post_init__(self) -> None:
        layers = tuple(str(item).strip() for item in self.layer_order)
        gaps = tuple(self.gaps)
        artwork = tuple(self.artwork)
        if len(layers) < 2 or not all(layers) or len({item.casefold() for item in layers}) != len(layers):
            raise MultilayerCapacitanceError("layer_order needs at least two unique physical layers")
        if len(gaps) != len(layers) - 1 or not all(isinstance(item, DielectricGap) for item in gaps):
            raise MultilayerCapacitanceError("one fully specified dielectric gap is required between every layer pair")
        wanted = {item.casefold() for item in layers}
        if not artwork or not all(isinstance(item, CapacitanceArtwork) for item in artwork):
            raise MultilayerCapacitanceError("exact capacitance model has no valid artwork")
        if any(item.layer.casefold() not in wanted for item in artwork):
            raise MultilayerCapacitanceError("artwork contains a layer outside the requested physical slab")
        fills = None
        if self.opening_fill_by_layer is not None:
            known = {item.casefold() for item in self.layer_order}
            canonical_fills: dict[str, DielectricGap] = {}
            for layer, fill in self.opening_fill_by_layer.items():
                if _canonical(layer) not in known or not isinstance(fill, DielectricGap):
                    raise MultilayerCapacitanceError("opening-fill data must name a physical layer and a fully specified dielectric")
                key = _canonical(layer)
                if key in canonical_fills:
                    raise MultilayerCapacitanceError("opening-fill data has duplicate physical-layer keys")
                canonical_fills[key] = fill
            fills = MappingProxyType(canonical_fills)
        if not isinstance(self.enable_nonadjacent_opening_coupling, bool):
            raise MultilayerCapacitanceError("enable_nonadjacent_opening_coupling must be a bool")
        object.__setattr__(self, "layer_order", layers)
        object.__setattr__(self, "gaps", gaps)
        object.__setattr__(self, "artwork", artwork)
        object.__setattr__(self, "opening_fill_by_layer", fills)


@dataclass(frozen=True, slots=True)
class MultilayerCapacitanceResult:
    """Global Maxwell matrix and optional floating-net-reduced port matrix."""

    net_names: tuple[str, ...]
    reference_net: str
    maxwell_capacitance_f: NDArray[np.float64]
    grounded_net_names: tuple[str, ...]
    grounded_capacitance_f: NDArray[np.float64]
    pair_capacitance_f: Mapping[tuple[str, str], float]
    nearest_visible_pair_count: int
    nonadjacent_opening_coupling_evaluated: bool
    selected_net_names: tuple[str, ...] | None = None
    selected_capacitance_f: NDArray[np.float64] | None = None
    # One immutable ordinary-symmetric Maxwell partial for each physical
    # adjacent dielectric gap.  These are explicitly *parallel-plate bulk*
    # artwork terms; they do not claim to include fringe, pad or via fields.
    adjacent_gap_partials: tuple["AdjacentGapMaxwellPartial", ...] = ()

    def __post_init__(self) -> None:
        nets = tuple(str(item).strip() for item in self.net_names)
        grounded_nets = tuple(str(item).strip() for item in self.grounded_net_names)
        if not nets or not all(nets) or len({_canonical(item) for item in nets}) != len(nets):
            raise MultilayerCapacitanceError("net_names must be non-empty and unique")
        if len(grounded_nets) != len(nets) - 1 or len({_canonical(item) for item in grounded_nets}) != len(grounded_nets):
            raise MultilayerCapacitanceError("grounded_net_names must be the unique non-reference nets")
        if _canonical(self.reference_net) not in {_canonical(item) for item in nets}:
            raise MultilayerCapacitanceError("reference_net is absent from net_names")
        for name, matrix, size in (("maxwell_capacitance_f", self.maxwell_capacitance_f, len(nets)), ("grounded_capacitance_f", self.grounded_capacitance_f, len(grounded_nets))):
            matrix = np.asarray(matrix, dtype=np.float64)
            if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or not np.all(np.isfinite(matrix)):
                raise MultilayerCapacitanceError(f"{name} must be a finite square matrix")
            if matrix.shape != (size, size):
                raise MultilayerCapacitanceError(f"{name} shape does not match named nets")
            object.__setattr__(self, name, _readonly(matrix))
        if self.selected_capacitance_f is not None:
            selected_names = tuple(str(item).strip() for item in (self.selected_net_names or ()))
            matrix = np.asarray(self.selected_capacitance_f, dtype=np.float64)
            if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or matrix.shape != (len(selected_names), len(selected_names)) or not np.all(np.isfinite(matrix)):
                raise MultilayerCapacitanceError("selected_capacitance_f must be a finite square matrix")
            object.__setattr__(self, "selected_capacitance_f", _readonly(matrix))
            object.__setattr__(self, "selected_net_names", selected_names)
        elif self.selected_net_names is not None:
            raise MultilayerCapacitanceError("selected_net_names requires selected_capacitance_f")
        partials = tuple(self.adjacent_gap_partials)
        if not all(isinstance(partial, AdjacentGapMaxwellPartial) for partial in partials):
            raise MultilayerCapacitanceError("adjacent_gap_partials must be typed partials")
        for partial in partials:
            if partial.net_names != nets:
                raise MultilayerCapacitanceError("adjacent partial net ordering differs from raw Maxwell matrix")
        pairs: dict[tuple[str, str], float] = {}
        for names_pair, value in self.pair_capacitance_f.items():
            if len(names_pair) != 2 or not all(isinstance(item, str) and item.strip() for item in names_pair):
                raise MultilayerCapacitanceError("pair capacitance keys must be two non-empty net names")
            number = float(value)
            if not isfinite(number) or number < 0.0:
                raise MultilayerCapacitanceError("pair capacitance values must be finite and non-negative")
            pairs[tuple(names_pair)] = number
        object.__setattr__(self, "net_names", nets)
        object.__setattr__(self, "grounded_net_names", grounded_nets)
        object.__setattr__(self, "adjacent_gap_partials", partials)
        object.__setattr__(self, "pair_capacitance_f", MappingProxyType(dict(sorted(pairs.items()))))


@dataclass(frozen=True, slots=True)
class AdjacentGapMaxwellPartial:
    """Immutable raw Maxwell C term for one physical adjacent dielectric gap.

    ``nominal_relative_permittivity`` is the Dk used in the artwork overlap
    extraction.  A frequency solver must scale this matrix by the supplied
    source dielectric complex-permittivity ratio; it must never add it to an
    existing scalar modal C00 shunt.
    """

    upper_layer: str
    lower_layer: str
    nominal_relative_permittivity: float
    net_names: tuple[str, ...]
    maxwell_capacitance_f: NDArray[np.float64] | csc_matrix
    separation_m: float | None = None

    def __post_init__(self) -> None:
        names = tuple(str(item).strip() for item in self.net_names)
        if (
            not names
            or not all(names)
            or len({item.casefold() for item in names}) != len(names)
        ):
            raise MultilayerCapacitanceError(
                "adjacent-gap Maxwell partial needs unique, non-empty node names"
            )
        if (
            not isfinite(self.nominal_relative_permittivity)
            or self.nominal_relative_permittivity <= 0.0
        ):
            raise MultilayerCapacitanceError(
                "adjacent-gap Maxwell partial needs finite positive permittivity"
            )
        separation = self.separation_m
        if separation is not None and (
            not isfinite(float(separation)) or float(separation) <= 0.0
        ):
            raise MultilayerCapacitanceError(
                "adjacent-gap separation must be finite and positive"
            )

        if issparse(self.maxwell_capacitance_f):
            matrix = csc_matrix(
                self.maxwell_capacitance_f, dtype=np.float64, copy=True
            )
            matrix.sum_duplicates()
            matrix.eliminate_zeros()
            matrix.sort_indices()
            if (
                matrix.ndim != 2
                or matrix.shape[0] != matrix.shape[1]
                or matrix.shape[0] != len(names)
                or not np.all(np.isfinite(matrix.data))
            ):
                raise MultilayerCapacitanceError(
                    "sparse adjacent-gap Maxwell partial must be finite, square and named"
                )
            scale = max(
                float(np.max(np.abs(matrix.data), initial=0.0)), 1.0e-30
            )
            tolerance = max(1.0e-24, scale * 1.0e-10)
            difference = matrix - matrix.T
            symmetry_error = (
                float(np.max(np.abs(difference.data), initial=0.0))
                if difference.nnz
                else 0.0
            )
            if symmetry_error > tolerance:
                raise MultilayerCapacitanceError(
                    "adjacent-gap Maxwell partial must be ordinary-symmetric"
                )
            row_sums = np.asarray(matrix.sum(axis=1), dtype=np.float64).ravel()
            if float(np.max(np.abs(row_sums), initial=0.0)) > tolerance:
                raise MultilayerCapacitanceError(
                    "adjacent-gap Maxwell partial row-sum check failed"
                )
            coo = matrix.tocoo(copy=False)
            off_diagonal = coo.data[coo.row != coo.col]
            if off_diagonal.size and float(np.max(off_diagonal)) > tolerance:
                raise MultilayerCapacitanceError(
                    "adjacent-gap Maxwell partial has a positive off-diagonal"
                )
            diagonal = matrix.diagonal()
            if diagonal.size != len(names) or np.any(diagonal <= 0.0):
                raise MultilayerCapacitanceError(
                    "sparse adjacent-gap Maxwell partial needs a positive diagonal for every local node"
                )
            # Sparse graph-Laplacian sign, symmetry and row-sum checks prove
            # positive semidefiniteness without an O(N^2) materialisation or
            # an O(N^3) dense eigensolve.
            matrix.data.setflags(write=False)
            matrix.indices.setflags(write=False)
            matrix.indptr.setflags(write=False)
            object.__setattr__(self, "net_names", names)
            object.__setattr__(self, "maxwell_capacitance_f", matrix)
            if separation is not None:
                object.__setattr__(self, "separation_m", float(separation))
            return

        matrix = np.asarray(self.maxwell_capacitance_f, dtype=np.float64)
        if (
            matrix.ndim != 2
            or matrix.shape[0] != matrix.shape[1]
            or matrix.shape[0] != len(names)
            or not np.all(np.isfinite(matrix))
        ):
            raise MultilayerCapacitanceError("adjacent-gap Maxwell partial must be finite, square and named")
        if not np.allclose(matrix, matrix.T, rtol=1.0e-12, atol=1.0e-24):
            raise MultilayerCapacitanceError("adjacent-gap Maxwell partial must be ordinary-symmetric")
        scale = max(float(np.max(np.abs(matrix))), 1.0e-30)
        if float(np.max(np.abs(matrix.sum(axis=1)))) > scale * 1.0e-10:
            raise MultilayerCapacitanceError("adjacent-gap Maxwell partial row-sum check failed")
        frozen = np.array(matrix, dtype=np.float64, copy=True)
        frozen.setflags(write=False)
        object.__setattr__(self, "net_names", names)
        object.__setattr__(self, "maxwell_capacitance_f", frozen)
        if separation is not None:
            object.__setattr__(self, "separation_m", float(separation))


def _canonical(value: str) -> str:
    result = value.strip()
    if not result:
        raise MultilayerCapacitanceError("layer and net names must not be blank")
    return result.casefold()


def _same_net_shapes(
    model: MultilayerCapacitanceModel,
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> tuple[dict[tuple[str, str], Any], dict[str, str]]:
    try:
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover - package dependency
        raise MultilayerCapacitanceError("Shapely is required for exact capacitance extraction") from exc
    grouped: dict[tuple[str, str], list[Any]] = {}
    names: dict[str, str] = {}
    for item in model.artwork:
        _checkpoint(is_cancelled)
        layer = _canonical(item.layer)
        net = _canonical(item.net)
        grouped.setdefault((layer, net), []).append(item.geometry_um)
        previous = names.setdefault(net, item.net.strip())
        if previous.casefold() != item.net.strip().casefold():  # defensive; canonical already proves this
            raise MultilayerCapacitanceError(f"ambiguous spelling for net {item.net!r}")
    shapes: dict[tuple[str, str], Any] = {}
    total = len(grouped)
    for ordinal, (key, values) in enumerate(grouped.items(), start=1):
        _checkpoint(is_cancelled)
        try:
            merged = unary_union(values)
        except Exception as exc:
            raise MultilayerCapacitanceError(f"same-net artwork union failed for {key[0]}/{key[1]}") from exc
        if bool(getattr(merged, "is_empty", True)) or not bool(getattr(merged, "is_valid", False)):
            raise MultilayerCapacitanceError(f"same-net artwork union is invalid for {key[0]}/{key[1]}")
        shapes[key] = merged
        if _should_report_work(ordinal, total):
            _report(
                progress,
                round(100 * ordinal / max(total, 1)),
                f"Unioned exact artwork surface {ordinal:,}/{total:,}",
            )
    _checkpoint(is_cancelled)
    return shapes, names


def _validate_layers_and_overlaps(
    model: MultilayerCapacitanceModel,
    shapes: Mapping[tuple[str, str], Any],
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> tuple[tuple[str, ...], dict[str, list[tuple[str, Any]]]]:
    layer_keys = tuple(_canonical(item) for item in model.layer_order)
    by_layer: dict[str, list[tuple[str, Any]]] = {layer: [] for layer in layer_keys}
    for (layer, net), shape in shapes.items():
        by_layer[layer].append((net, shape))
    total = sum(
        len(entries) * (len(entries) - 1) // 2
        for entries in by_layer.values()
    )
    completed = 0
    for layer, entries in by_layer.items():
        _checkpoint(is_cancelled)
        if not entries:
            raise MultilayerCapacitanceError(f"incomplete exact artwork: physical conductor layer {layer!r} has no retained net surface")
        for index, (left_net, left_shape) in enumerate(entries):
            for right_net, right_shape in entries[index + 1:]:
                _checkpoint(is_cancelled)
                try:
                    overlap = float(left_shape.intersection(right_shape).area)
                except Exception as exc:
                    raise MultilayerCapacitanceError(f"same-layer artwork intersection failed on {layer!r}") from exc
                if overlap > 1.0e-9:
                    raise MultilayerCapacitanceError(
                        f"ambiguous/shorted artwork: nets {left_net!r} and {right_net!r} overlap by {overlap:.9g} um^2 on {layer!r}"
                    )
                completed += 1
                if _should_report_work(completed, total):
                    _report(
                        progress,
                        round(100 * completed / max(total, 1)),
                        f"Validated same-layer separation {completed:,}/{total:,}",
                    )
    _checkpoint(is_cancelled)
    if total == 0:
        _report(progress, 100, "Same-layer artwork separation validated")
    return layer_keys, by_layer


def _series_gap(model: MultilayerCapacitanceModel, upper: int, lower: int) -> tuple[float, float]:
    """Return physical distance and series effective epsilon between surfaces."""

    gaps = model.gaps[upper:lower]
    distance = sum(item.separation_m for item in gaps)
    denominator = sum(item.separation_m / item.relative_permittivity for item in gaps)
    # A field crossing an opening in a nominal conductor must cross that
    # layer's copper-thickness interval too.  SPD stack rows do not identify
    # resin/void/plating in an aperture, so we reject the term unless callers
    # supply that material explicitly.  Silently skipping the thickness would
    # overstate C (20 um in a 60 um dielectric path is already 33%).
    if lower > upper + 1:
        fills = model.opening_fill_by_layer or {}
        for layer in model.layer_order[upper + 1:lower]:
            fill = fills.get(layer)
            if fill is None:
                # Permit case-insensitive mappings while preserving user
                # display spellings in the public model.
                fill = next((value for name, value in fills.items() if name.casefold() == layer.casefold()), None)
            if fill is None:
                raise MultilayerCapacitanceError(
                    f"non-adjacent opening through {layer!r} has no source-proven opening-fill thickness/permittivity"
                )
            distance += fill.separation_m
            denominator += fill.separation_m / fill.relative_permittivity
    if distance <= 0.0 or denominator <= 0.0:  # defensive after dataclass validation
        raise MultilayerCapacitanceError("invalid dielectric stack between visible conductor surfaces")
    return distance, distance / denominator


def _readonly(matrix: NDArray[np.float64]) -> NDArray[np.float64]:
    result = np.array(matrix, dtype=np.float64, copy=True)
    result.setflags(write=False)
    return result


def _reduce_floating(grounded: NDArray[np.float64], grounded_names: tuple[str, ...], selected: Sequence[str]) -> tuple[tuple[str, ...], NDArray[np.float64]]:
    selected_keys = tuple(_canonical(item) for item in selected)
    if not selected_keys or len(set(selected_keys)) != len(selected_keys):
        raise MultilayerCapacitanceError("selected nets must be non-empty and unique")
    positions = {item.casefold(): index for index, item in enumerate(grounded_names)}
    missing = [item for item in selected_keys if item not in positions]
    if missing:
        raise MultilayerCapacitanceError(f"selected nets are absent from the exact Maxwell matrix: {missing!r}")
    selection = np.asarray([positions[item] for item in selected_keys], dtype=int)
    floating = np.asarray([index for index in range(len(grounded_names)) if index not in set(selection)], dtype=int)
    css = grounded[np.ix_(selection, selection)]
    if not len(floating):
        return tuple(grounded_names[index] for index in selection), _readonly(css)
    cff = grounded[np.ix_(floating, floating)]
    try:
        condition = float(np.linalg.cond(cff))
        if not isfinite(condition) or condition > 1.0e12:
            raise np.linalg.LinAlgError(f"condition={condition:.3g}")
        correction = grounded[np.ix_(selection, floating)] @ np.linalg.solve(cff, grounded[np.ix_(floating, selection)])
    except np.linalg.LinAlgError as exc:
        raise MultilayerCapacitanceError(
            "floating-net capacitance block is singular/ill-conditioned; incomplete geometry or an electrically isolated net cannot be guessed"
        ) from exc
    reduced = 0.5 * ((css - correction) + (css - correction).T)
    eigenvalues = np.linalg.eigvalsh(reduced)
    if eigenvalues.size and float(eigenvalues.min()) < -max(1.0e-20, float(np.abs(reduced).max()) * 1.0e-10):
        raise MultilayerCapacitanceError("floating-net reduction produced a non-passive capacitance matrix")
    return tuple(grounded_names[index] for index in selection), _readonly(reduced)


def reduce_floating_multilayer_capacitance(
    result: MultilayerCapacitanceResult,
    selected_nets: Sequence[str],
) -> tuple[tuple[str, ...], NDArray[np.float64]]:
    """Schur-reduce an existing exact bulk matrix without recomputing artwork.

    This preserves every retained non-reference net as a floating Maxwell
    node. It is useful when comparing several port selections against one
    fixed artwork/stack slab; it must not be replaced with independently
    grounded scalar capacitances.
    """

    return _reduce_floating(result.grounded_capacitance_f, result.grounded_net_names, selected_nets)


def extract_sparse_adjacent_gap_island_capacitance(
    model: MultilayerCapacitanceModel,
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> tuple[AdjacentGapMaxwellPartial, ...]:
    """Build sparse, island-resolved Maxwell terms for adjacent gaps only.

    Each artwork item is one physical conductor island.  ``node_name`` is its
    electrical identity; for compatibility, ``net`` is used when node_name is
    absent.  Result partials contain only nodes with a positive coupling in
    that gap, so their CSC dimensions scale with participating islands rather
    than with a board-global node inventory.

    Candidate pairs are enumerated by one Shapely STRtree per lower surface
    layer.  There is no global dense N-by-N allocation, no cartesian artwork
    product, and no dense eigensolve.  This bounded extractor deliberately
    omits non-adjacent opening/fringing terms.  A model which explicitly
    enables non-adjacent opening coupling is rejected instead of being
    silently under-stamped; callers needing those terms must use the dense
    research path.
    """

    _checkpoint(is_cancelled)
    _report(progress, 0, "Preparing island-resolved adjacent-gap artwork")
    if model.enable_nonadjacent_opening_coupling:
        raise MultilayerCapacitanceError(
            "sparse island extraction supports adjacent physical gaps only; "
            "non-adjacent opening coupling requires the dense research extractor"
        )
    try:
        from shapely.strtree import STRtree
    except ImportError as exc:  # pragma: no cover - package dependency
        raise MultilayerCapacitanceError(
            "Shapely STRtree is required for sparse island capacitance extraction"
        ) from exc

    layer_keys = tuple(_canonical(item) for item in model.layer_order)
    layer_display = {
        _canonical(display): display for display in model.layer_order
    }
    by_layer: dict[str, list[tuple[str, CapacitanceArtwork]]] = {
        layer: [] for layer in layer_keys
    }
    identity_ledger: dict[str, str] = {}
    for artwork in model.artwork:
        _checkpoint(is_cancelled)
        node_name = str(artwork.node_name or artwork.net).strip()
        node_key = _canonical(node_name)
        if node_key in identity_ledger:
            raise MultilayerCapacitanceError(
                f"sparse island node identity {node_name!r} is not globally unique"
            )
        identity_ledger[node_key] = node_name
        by_layer[_canonical(artwork.layer)].append((node_name, artwork))

    for layer in layer_keys:
        if not by_layer[layer]:
            raise MultilayerCapacitanceError(
                "incomplete island artwork: physical conductor layer "
                f"{layer_display[layer]!r} has no retained island"
            )
        by_layer[layer].sort(key=lambda item: (item[0].casefold(), item[0]))

    # Fail closed on same-layer overlap without a quadratic pair loop.  Two
    # identities which overlap in area are not disconnected conductor islands.
    same_layer_queries = sum(len(items) for items in by_layer.values())
    completed_queries = 0
    for layer in layer_keys:
        entries = by_layer[layer]
        geometries = [item.geometry_um for _name, item in entries]
        try:
            tree = STRtree(geometries)
        except Exception as exc:
            raise MultilayerCapacitanceError(
                f"cannot index island artwork on {layer_display[layer]!r}"
            ) from exc
        for left_index, (left_name, left) in enumerate(entries):
            _checkpoint(is_cancelled)
            try:
                candidates = tree.query(left.geometry_um, predicate="intersects")
            except Exception as exc:
                raise MultilayerCapacitanceError(
                    f"same-layer island query failed on {layer_display[layer]!r}"
                ) from exc
            # STRtree traversal order is not part of Shapely's contract.
            # Sorting makes the first fail-closed overlap pair stable across
            # GEOS versions and index layouts.
            for right_index in sorted(int(item) for item in candidates):
                if right_index <= left_index:
                    continue
                _checkpoint(is_cancelled)
                right_name, right = entries[right_index]
                try:
                    overlap_um2 = float(
                        left.geometry_um.intersection(right.geometry_um).area
                    )
                except Exception as exc:
                    raise MultilayerCapacitanceError(
                        f"same-layer island intersection failed on {layer_display[layer]!r}"
                    ) from exc
                if overlap_um2 > 1.0e-9:
                    raise MultilayerCapacitanceError(
                        "ambiguous/shorted island artwork: nodes "
                        f"{left_name!r} and {right_name!r} overlap by "
                        f"{overlap_um2:.9g} um^2 on {layer_display[layer]!r}"
                    )
            completed_queries += 1
            if _should_report_work(completed_queries, same_layer_queries):
                _report(
                    progress,
                    5 + round(20 * completed_queries / max(same_layer_queries, 1)),
                    (
                        "Validated same-layer island separation "
                        f"{completed_queries:,}/{same_layer_queries:,}"
                    ),
                )

    adjacent_queries = sum(
        len(by_layer[layer_keys[index]]) for index in range(len(model.gaps))
    )
    completed_queries = 0
    partials: list[AdjacentGapMaxwellPartial] = []
    for gap_index, gap in enumerate(model.gaps):
        _checkpoint(is_cancelled)
        upper_layer = layer_keys[gap_index]
        lower_layer = layer_keys[gap_index + 1]
        upper_entries = by_layer[upper_layer]
        lower_entries = by_layer[lower_layer]
        lower_geometries = [item.geometry_um for _name, item in lower_entries]
        lower_geometry_array = np.empty(len(lower_geometries), dtype=object)
        lower_geometry_array[:] = lower_geometries
        upper_geometry_array = np.empty(len(upper_entries), dtype=object)
        upper_geometry_array[:] = [
            item.geometry_um for _name, item in upper_entries
        ]
        try:
            lower_tree = STRtree(lower_geometries)
        except Exception as exc:
            raise MultilayerCapacitanceError(
                "cannot index lower-layer island artwork for adjacent gap "
                f"{layer_display[upper_layer]!r}/{layer_display[lower_layer]!r}"
            ) from exc

        coefficient_f_per_um2 = (
            EPSILON_0_F_PER_M
            * float(gap.relative_permittivity)
            / float(gap.separation_m)
            * 1.0e-12
        )
        if (
            not isfinite(coefficient_f_per_um2)
            or coefficient_f_per_um2 <= 0.0
        ):
            raise MultilayerCapacitanceError(
                "adjacent-gap capacitance coefficient is not finite and positive"
            )
        edge_capacitance: dict[tuple[str, str], float] = {}
        intersection_executor = ThreadPoolExecutor(
            max_workers=_ADJACENT_INTERSECTION_WORKERS,
            thread_name_prefix="spd-decap-overlap",
        )
        extraction_succeeded = False
        try:
            for upper_start in range(
                0, len(upper_entries), _ADJACENT_QUERY_BATCH_SIZE
            ):
                _checkpoint(is_cancelled)
                upper_stop = min(
                    upper_start + _ADJACENT_QUERY_BATCH_SIZE,
                    len(upper_entries),
                )
                upper_batch = upper_geometry_array[upper_start:upper_stop]
                try:
                    candidate_pairs = np.asarray(
                        lower_tree.query(upper_batch, predicate="intersects"),
                        dtype=np.intp,
                    )
                except Exception as exc:
                    raise MultilayerCapacitanceError(
                        "adjacent-layer island query failed for "
                        f"{layer_display[upper_layer]!r}/{layer_display[lower_layer]!r}"
                    ) from exc

                _checkpoint(is_cancelled)
                if (
                    candidate_pairs.ndim != 2
                    or candidate_pairs.shape[0] != 2
                ):
                    raise MultilayerCapacitanceError(
                        "adjacent-layer island query returned malformed candidate pairs"
                    )
                if candidate_pairs.shape[1]:
                    local_upper_indices = candidate_pairs[0]
                    lower_indices = candidate_pairs[1]
                    if (
                        np.any(local_upper_indices < 0)
                        or np.any(local_upper_indices >= len(upper_batch))
                        or np.any(lower_indices < 0)
                        or np.any(lower_indices >= len(lower_entries))
                    ):
                        raise MultilayerCapacitanceError(
                            "adjacent-layer island query returned an invalid candidate index"
                        )
                    # STRtree hit order is an implementation detail.  Reorder
                    # by the same upper-then-lower indices used by the former
                    # scalar loops before any float is accumulated.
                    order = np.lexsort((lower_indices, local_upper_indices))
                    local_upper_indices = local_upper_indices[order]
                    lower_indices = lower_indices[order]

                for pair_start in range(
                    0,
                    candidate_pairs.shape[1],
                    _ADJACENT_INTERSECTION_BATCH_SIZE,
                ):
                    _checkpoint(is_cancelled)
                    pair_stop = min(
                        pair_start + _ADJACENT_INTERSECTION_BATCH_SIZE,
                        candidate_pairs.shape[1],
                    )
                    batch_upper_indices = local_upper_indices[
                        pair_start:pair_stop
                    ]
                    batch_lower_indices = lower_indices[pair_start:pair_stop]
                    batch_upper_geometries = upper_batch[batch_upper_indices]
                    batch_lower_geometries = lower_geometry_array[
                        batch_lower_indices
                    ]
                    try:
                        areas_um2 = _threaded_intersection_areas_um2(
                            intersection_executor,
                            batch_upper_geometries,
                            batch_lower_geometries,
                            is_cancelled=is_cancelled,
                        )
                    except RuntimeError as exc:
                        if str(exc) == "evaluation cancelled":
                            raise
                        raise MultilayerCapacitanceError(
                            "exact adjacent-layer island intersection failed"
                        ) from exc
                    except Exception as exc:
                        raise MultilayerCapacitanceError(
                            "exact adjacent-layer island intersection failed"
                        ) from exc
                    _checkpoint(is_cancelled)
                    if (
                        areas_um2.shape != (pair_stop - pair_start,)
                        or not np.all(np.isfinite(areas_um2))
                    ):
                        raise MultilayerCapacitanceError(
                            "exact adjacent-layer island intersection returned malformed areas"
                        )
                    for local_upper_index, lower_index, raw_area_um2 in zip(
                        batch_upper_indices,
                        batch_lower_indices,
                        areas_um2,
                        strict=True,
                    ):
                        area_um2 = float(raw_area_um2)
                        if area_um2 <= 1.0e-9:
                            continue
                        capacitance_f = coefficient_f_per_um2 * area_um2
                        if (
                            not isfinite(capacitance_f)
                            or capacitance_f <= 0.0
                        ):
                            raise MultilayerCapacitanceError(
                                "adjacent island coupling is not finite and positive"
                            )
                        upper_name = upper_entries[
                            upper_start + int(local_upper_index)
                        ][0]
                        lower_name = lower_entries[int(lower_index)][0]
                        pair = (upper_name, lower_name)
                        edge_capacitance[pair] = (
                            edge_capacitance.get(pair, 0.0) + capacitance_f
                        )

                completed_queries += upper_stop - upper_start
                if _should_report_work(completed_queries, adjacent_queries):
                    _report(
                        progress,
                        25
                        + round(
                            68
                            * completed_queries
                            / max(adjacent_queries, 1)
                        ),
                        (
                            "Extracted adjacent-gap island overlaps "
                            f"{completed_queries:,}/{adjacent_queries:,}"
                        ),
                    )
            extraction_succeeded = True
        finally:
            intersection_executor.shutdown(
                wait=extraction_succeeded,
                cancel_futures=not extraction_succeeded,
            )

        if not edge_capacitance:
            continue
        node_names = tuple(
            sorted(
                {
                    name
                    for pair in edge_capacitance
                    for name in pair
                },
                key=lambda item: (item.casefold(), item),
            )
        )
        node_index = {name: index for index, name in enumerate(node_names)}
        rows: list[int] = []
        columns: list[int] = []
        values: list[float] = []
        for (upper_name, lower_name), capacitance_f in sorted(
            edge_capacitance.items(),
            key=lambda item: (
                item[0][0].casefold(),
                item[0][0],
                item[0][1].casefold(),
                item[0][1],
            ),
        ):
            upper_index = node_index[upper_name]
            lower_index = node_index[lower_name]
            rows.extend((upper_index, upper_index, lower_index, lower_index))
            columns.extend(
                (upper_index, lower_index, upper_index, lower_index)
            )
            values.extend(
                (capacitance_f, -capacitance_f, -capacitance_f, capacitance_f)
            )
        matrix = csc_matrix(
            (values, (rows, columns)),
            shape=(len(node_names), len(node_names)),
            dtype=np.float64,
        )
        matrix.sum_duplicates()
        matrix.eliminate_zeros()
        partials.append(
            AdjacentGapMaxwellPartial(
                upper_layer=model.layer_order[gap_index],
                lower_layer=model.layer_order[gap_index + 1],
                nominal_relative_permittivity=gap.relative_permittivity,
                net_names=node_names,
                maxwell_capacitance_f=matrix,
                separation_m=gap.separation_m,
            )
        )

    _checkpoint(is_cancelled)
    if not partials:
        raise MultilayerCapacitanceError(
            "island artwork has no adjacent-layer overlap; capacitance is indeterminate"
        )
    _report(progress, 100, "Sparse adjacent-gap island extraction complete")
    return tuple(partials)


def extract_multilayer_bulk_capacitance(
    model: MultilayerCapacitanceModel,
    *,
    reference_net: str = "DGND",
    selected_nets: Sequence[str] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> MultilayerCapacitanceResult:
    """Build an exact-artwork global Maxwell C matrix.

    Adjacent artwork surfaces are coupled where they overlap. When
    ``enable_nonadjacent_opening_coupling`` is true, a non-adjacent pair is
    also coupled only inside a genuine opening in every intermediate layer;
    each crossed layer then requires explicit source-proven opening-fill
    material. The default is deliberately adjacent-only rather than silently
    inventing aperture material. ``selected_nets`` requests the
    physically correct Schur complement of *all* unselected floating nets.
    """

    _checkpoint(is_cancelled)
    _report(progress, 0, "Preparing exact artwork surfaces")
    shapes, display_names = _same_net_shapes(
        model,
        progress=lambda value, message: _report(
            progress, round(20 * value / 100), message
        ),
        is_cancelled=is_cancelled,
    )
    layer_keys, by_layer = _validate_layers_and_overlaps(
        model,
        shapes,
        progress=lambda value, message: _report(
            progress, 20 + round(20 * value / 100), message
        ),
        is_cancelled=is_cancelled,
    )
    ref_key = _canonical(reference_net)
    all_nets = sorted({net for _layer, net in shapes}, key=str.casefold)
    if ref_key not in all_nets:
        raise MultilayerCapacitanceError(f"reference net {reference_net!r} is absent from exact artwork")
    # A deterministic ordering makes the matrix serialisable/reproducible; the
    # reference remains in the full Maxwell matrix and is removed only below.
    ordered_keys = tuple(sorted(all_nets, key=lambda item: (item != ref_key, item)))
    indices = {item: index for index, item in enumerate(ordered_keys)}
    maxwell = np.zeros((len(ordered_keys), len(ordered_keys)), dtype=np.float64)
    # Keep the source-faithful adjacent physical gap contributions separate.
    # This is deliberately before reference removal or floating reduction.
    adjacent_raw = [
        np.zeros((len(ordered_keys), len(ordered_keys)), dtype=np.float64)
        for _ in model.gaps
    ]
    pair_cap: dict[tuple[str, str], float] = {}
    visible_pairs = 0
    overlap_task_count = sum(
        len(by_layer[layer_keys[upper]]) * len(by_layer[layer_keys[lower]])
        for upper in range(len(layer_keys) - 1)
        for lower in range(upper + 1, len(layer_keys))
        if lower == upper + 1 or model.enable_nonadjacent_opening_coupling
    )
    completed_overlap_tasks = 0

    def report_overlap_progress() -> None:
        if not _should_report_work(completed_overlap_tasks, overlap_task_count):
            return
        _report(
            progress,
            40
            + round(
                50 * completed_overlap_tasks / max(overlap_task_count, 1)
            ),
            (
                "Extracting adjacent-gap artwork overlaps "
                f"{completed_overlap_tasks:,}/{overlap_task_count:,}"
            ),
        )

    # For upper/lower surfaces i,j, subtract all XY locations occupied by any
    # intermediate conductor. This is the exact nearest-visible partition,
    # not a shortcut based on bounding boxes or raster labels. A non-adjacent
    # opening path is opt-in because the SPD stack alone cannot say what
    # fills the nominal conductor thickness inside the aperture.
    for upper in range(len(layer_keys) - 1):
        for lower in range(upper + 1, len(layer_keys)):
            _checkpoint(is_cancelled)
            nonadjacent = lower > upper + 1
            if nonadjacent and not model.enable_nonadjacent_opening_coupling:
                # Do not spend Shapely intersection time determining aperture
                # areas that cannot be physically stamped without fill data.
                continue
            intermediate = [shape for layer in layer_keys[upper + 1:lower] for _net, shape in by_layer[layer]]
            blocker = None
            if intermediate:
                _checkpoint(is_cancelled)
                try:
                    from shapely.ops import unary_union
                    blocker = unary_union(intermediate)
                except Exception as exc:
                    raise MultilayerCapacitanceError("intermediate-conductor artwork union failed") from exc
            for upper_net, upper_shape in by_layer[layer_keys[upper]]:
                for lower_net, lower_shape in by_layer[layer_keys[lower]]:
                    _checkpoint(is_cancelled)
                    completed_overlap_tasks += 1
                    if upper_net == lower_net:
                        report_overlap_progress()
                        continue  # a shorted net has no electrostatic terminal pair
                    try:
                        overlap = upper_shape.intersection(lower_shape)
                        if blocker is not None and not bool(getattr(blocker, "is_empty", True)):
                            overlap = overlap.difference(blocker)
                        area_um2 = float(overlap.area)
                    except Exception as exc:
                        raise MultilayerCapacitanceError("exact artwork overlap calculation failed") from exc
                    report_overlap_progress()
                    if area_um2 <= 1.0e-9:
                        continue
                    # Compute this only after an actual non-adjacent opening
                    # exists. Otherwise an unrelated absent overlap must not
                    # make a valid adjacent-only model fail.
                    distance, epsilon_r = _series_gap(model, upper, lower)
                    coefficient = EPSILON_0_F_PER_M * epsilon_r / distance * 1.0e-12  # um^2 -> m^2
                    capacitance = coefficient * area_um2
                    left, right = indices[upper_net], indices[lower_net]
                    maxwell[left, left] += capacitance
                    maxwell[right, right] += capacitance
                    maxwell[left, right] -= capacitance
                    maxwell[right, left] -= capacitance
                    if not nonadjacent:
                        partial = adjacent_raw[upper]
                        partial[left, left] += capacitance
                        partial[right, right] += capacitance
                        partial[left, right] -= capacitance
                        partial[right, left] -= capacitance
                    key = tuple(sorted((display_names[upper_net], display_names[lower_net]), key=str.casefold))
                    pair_cap[key] = pair_cap.get(key, 0.0) + capacitance
                    if nonadjacent:
                        visible_pairs += 1

    _checkpoint(is_cancelled)
    _report(progress, 92, "Validating the assembled Maxwell matrix")
    if not np.any(np.diag(maxwell) > 0.0):
        raise MultilayerCapacitanceError("exact artwork has no opposing-net overlap; capacitance is indeterminate")
    maxwell = 0.5 * (maxwell + maxwell.T)
    row_error = float(np.abs(maxwell.sum(axis=1)).max())
    scale = max(float(np.abs(maxwell).max()), 1.0e-30)
    if row_error > scale * 1.0e-10:
        raise MultilayerCapacitanceError("Maxwell matrix row-sum check failed")
    eigenvalues = np.linalg.eigvalsh(maxwell)
    if float(eigenvalues.min()) < -scale * 1.0e-10:
        raise MultilayerCapacitanceError("Maxwell matrix is non-passive")

    adjacent_partials = tuple(
        AdjacentGapMaxwellPartial(
            upper_layer=model.layer_order[index],
            lower_layer=model.layer_order[index + 1],
            nominal_relative_permittivity=model.gaps[index].relative_permittivity,
            net_names=tuple(display_names[item] for item in ordered_keys),
            maxwell_capacitance_f=partial,
            separation_m=model.gaps[index].separation_m,
        )
        for index, partial in enumerate(adjacent_raw)
    )
    if not model.enable_nonadjacent_opening_coupling:
        reconstructed = sum(
            (partial.maxwell_capacitance_f for partial in adjacent_partials),
            start=np.zeros_like(maxwell),
        )
        if not np.allclose(reconstructed, maxwell, rtol=1.0e-12, atol=1.0e-24):
            raise MultilayerCapacitanceError("adjacent physical-gap partials do not reconstruct raw Maxwell C")

    ref_index = indices[ref_key]
    grounded_indices = np.asarray([item for item in range(len(ordered_keys)) if item != ref_index], dtype=int)
    grounded = _readonly(maxwell[np.ix_(grounded_indices, grounded_indices)])
    grounded_names = tuple(display_names[ordered_keys[item]] for item in grounded_indices)
    selected_names: tuple[str, ...] | None = None
    selected_matrix: NDArray[np.float64] | None = None
    if selected_nets is not None:
        _checkpoint(is_cancelled)
        selected_names, selected_matrix = _reduce_floating(grounded, grounded_names, selected_nets)
    result = MultilayerCapacitanceResult(
        tuple(display_names[item] for item in ordered_keys),
        display_names[ref_key],
        _readonly(maxwell),
        grounded_names,
        grounded,
        dict(sorted(pair_cap.items(), key=lambda item: item[0])),
        visible_pairs,
        model.enable_nonadjacent_opening_coupling,
        selected_names,
        selected_matrix,
        adjacent_partials,
    )
    _checkpoint(is_cancelled)
    _report(progress, 100, "Exact adjacent-gap Maxwell extraction complete")
    return result


def capacitance_model_from_project(
    project: ProjectSpec,
    attachments: Mapping[str, bytes],
    layer_order: Sequence[str],
    *,
    validate_artwork: bool = True,
    island_resolved: bool = False,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> MultilayerCapacitanceModel:
    """Decode a complete, contiguous exact-artwork physical slab from SPD data.

    ``validate_artwork=False`` is reserved for callers that immediately pass
    the returned model to :func:`extract_multilayer_bulk_capacitance`, whose
    fail-closed union and same-layer separation checks are identical.  It
    avoids performing those geometry operations twice without weakening the
    extraction boundary.  ``island_resolved=True`` preserves every connected
    polygon from the source asset as a separate artwork item and assigns the
    same source-asset-bound island identity used by the raw graph certificate;
    it is the production input for the sparse island extractor.
    """

    _checkpoint(is_cancelled)
    _report(progress, 0, "Indexing retained exact artwork")
    layers = tuple(str(item) for item in layer_order)
    positions = {item.name.casefold(): index for index, item in enumerate(project.stackup_layers)}
    try:
        indices = [positions[_canonical(item)] for item in layers]
    except KeyError as exc:
        raise MultilayerCapacitanceError(f"unknown stack-up layer {exc.args[0]!r}") from exc
    if indices != sorted(indices) or any(not project.stackup_layers[index].is_conductor for index in indices):
        raise MultilayerCapacitanceError("layer order must be top-to-bottom physical conductor layers")
    gaps: list[DielectricGap] = []
    for upper, lower in zip(indices[:-1], indices[1:], strict=True):
        _checkpoint(is_cancelled)
        rows = project.stackup_layers[upper + 1:lower]
        if not rows or any(row.is_conductor or row.dk is None for row in rows):
            raise MultilayerCapacitanceError("each requested conductor gap must contain only fully specified dielectric rows")
        distance_um = sum(float(row.thickness_um) for row in rows)
        denominator = sum(float(row.thickness_um) / float(row.dk) for row in rows)
        gaps.append(DielectricGap(distance_um * 1.0e-6, distance_um / denominator))
    wanted = {_canonical(item) for item in layers}
    records = project.metadata.get("spd_import", {}).get("plane_geometries", ())
    if not isinstance(records, Sequence):
        raise MultilayerCapacitanceError("project has no retained SPD plane geometry index")
    selected_records = tuple(
        raw
        for raw in records
        if isinstance(raw, Mapping)
        and _canonical(str(raw.get("layer", ""))) in wanted
    )
    artwork: list[CapacitanceArtwork] = []
    for ordinal, raw in enumerate(selected_records, start=1):
        _checkpoint(is_cancelled)
        asset = str(raw.get("asset", ""))
        digest = str(raw.get("asset_sha256", ""))
        try:
            payload = services._decode_spd_geometry_asset(digest, attachments[asset])
            services._validate_spd_geometry_payload(payload, expected_layer=str(raw.get("layer", "")), expected_net=str(raw.get("net", "")))
            shape = services._ordered_spd_geometry(payload)
        except (KeyError, ValueError) as exc:
            raise MultilayerCapacitanceError(f"cannot decode retained SPD artwork {asset!r}") from exc
        if shape is None:
            raise MultilayerCapacitanceError(f"cannot construct valid exact artwork {asset!r}")
        if island_resolved:
            try:
                islands = services._spd_surface_islands(
                    layer=str(raw["layer"]),
                    net=str(raw["net"]),
                    asset_sha256=digest,
                    shape=shape,
                    is_cancelled=is_cancelled,
                )
            except ValueError as exc:
                raise MultilayerCapacitanceError(
                    f"cannot identify retained SPD artwork islands {asset!r}"
                ) from exc
            artwork.extend(
                CapacitanceArtwork(
                    str(raw["layer"]),
                    str(raw["net"]),
                    island,
                    node_name=island_id,
                )
                for island_id, island in islands
            )
        else:
            artwork.append(
                CapacitanceArtwork(str(raw["layer"]), str(raw["net"]), shape)
            )
        if _should_report_work(ordinal, len(selected_records)):
            _report(
                progress,
                round(50 * ordinal / max(len(selected_records), 1)),
                f"Decoded exact artwork asset {ordinal:,}/{len(selected_records):,}",
            )
    model = MultilayerCapacitanceModel(layers, tuple(gaps), tuple(artwork))
    # Standalone callers validate early so an empty/omitted conductor row is
    # never mistaken for an open field region.  The layerwise compiler defers
    # these identical checks to its immediate extraction call.
    if validate_artwork:
        shapes, _names = _same_net_shapes(
            model,
            progress=lambda value, message: _report(
                progress, 50 + round(25 * value / 100), message
            ),
            is_cancelled=is_cancelled,
        )
        _validate_layers_and_overlaps(
            model,
            shapes,
            progress=lambda value, message: _report(
                progress, 75 + round(25 * value / 100), message
            ),
            is_cancelled=is_cancelled,
        )
    _checkpoint(is_cancelled)
    _report(
        progress,
        100,
        (
            "Exact artwork model validated"
            if validate_artwork
            else "Exact artwork model decoded; extraction validation pending"
        ),
    )
    return model


__all__ = [
    "EPSILON_0_F_PER_M",
    "CapacitanceArtwork",
    "AdjacentGapMaxwellPartial",
    "DielectricGap",
    "MultilayerCapacitanceError",
    "MultilayerCapacitanceModel",
    "MultilayerCapacitanceResult",
    "capacitance_model_from_project",
    "extract_multilayer_bulk_capacitance",
    "extract_sparse_adjacent_gap_island_capacitance",
    "reduce_floating_multilayer_capacitance",
]
