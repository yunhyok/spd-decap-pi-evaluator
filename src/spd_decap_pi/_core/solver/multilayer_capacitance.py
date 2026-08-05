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

from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from ..domain import ProjectSpec
from .. import services


EPSILON_0_F_PER_M = 8.854_187_812_8e-12


class MultilayerCapacitanceError(ValueError):
    """Raised when exact artwork cannot support an unambiguous extraction."""


@dataclass(frozen=True, slots=True)
class CapacitanceArtwork:
    """One electrically distinct net surface on one physical conductor layer.

    ``geometry_um`` is a valid Shapely polygonal geometry in SPD micrometres.
    Multiple entries for the same (layer, net) are allowed and safely unioned;
    entries for different nets are never unioned.
    """

    layer: str
    net: str
    geometry_um: Any

    def __post_init__(self) -> None:
        if not self.layer.strip() or not self.net.strip():
            raise MultilayerCapacitanceError("artwork layer and net must not be empty")
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
    maxwell_capacitance_f: NDArray[np.float64]

    def __post_init__(self) -> None:
        matrix = np.asarray(self.maxwell_capacitance_f, dtype=np.float64)
        if (
            matrix.ndim != 2
            or matrix.shape[0] != matrix.shape[1]
            or matrix.shape[0] != len(self.net_names)
            or not np.all(np.isfinite(matrix))
            or not isfinite(self.nominal_relative_permittivity)
            or self.nominal_relative_permittivity <= 0.0
        ):
            raise MultilayerCapacitanceError("adjacent-gap Maxwell partial must be finite, square and named")
        if not np.allclose(matrix, matrix.T, rtol=1.0e-12, atol=1.0e-24):
            raise MultilayerCapacitanceError("adjacent-gap Maxwell partial must be ordinary-symmetric")
        scale = max(float(np.max(np.abs(matrix))), 1.0e-30)
        if float(np.max(np.abs(matrix.sum(axis=1)))) > scale * 1.0e-10:
            raise MultilayerCapacitanceError("adjacent-gap Maxwell partial row-sum check failed")
        frozen = np.array(matrix, dtype=np.float64, copy=True)
        frozen.setflags(write=False)
        object.__setattr__(self, "maxwell_capacitance_f", frozen)


def _canonical(value: str) -> str:
    result = value.strip()
    if not result:
        raise MultilayerCapacitanceError("layer and net names must not be blank")
    return result.casefold()


def _same_net_shapes(model: MultilayerCapacitanceModel) -> tuple[dict[tuple[str, str], Any], dict[str, str]]:
    try:
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover - package dependency
        raise MultilayerCapacitanceError("Shapely is required for exact capacitance extraction") from exc
    grouped: dict[tuple[str, str], list[Any]] = {}
    names: dict[str, str] = {}
    for item in model.artwork:
        layer = _canonical(item.layer)
        net = _canonical(item.net)
        grouped.setdefault((layer, net), []).append(item.geometry_um)
        previous = names.setdefault(net, item.net.strip())
        if previous.casefold() != item.net.strip().casefold():  # defensive; canonical already proves this
            raise MultilayerCapacitanceError(f"ambiguous spelling for net {item.net!r}")
    shapes: dict[tuple[str, str], Any] = {}
    for key, values in grouped.items():
        try:
            merged = unary_union(values)
        except Exception as exc:
            raise MultilayerCapacitanceError(f"same-net artwork union failed for {key[0]}/{key[1]}") from exc
        if bool(getattr(merged, "is_empty", True)) or not bool(getattr(merged, "is_valid", False)):
            raise MultilayerCapacitanceError(f"same-net artwork union is invalid for {key[0]}/{key[1]}")
        shapes[key] = merged
    return shapes, names


def _validate_layers_and_overlaps(model: MultilayerCapacitanceModel, shapes: Mapping[tuple[str, str], Any]) -> tuple[tuple[str, ...], dict[str, list[tuple[str, Any]]]]:
    layer_keys = tuple(_canonical(item) for item in model.layer_order)
    by_layer: dict[str, list[tuple[str, Any]]] = {layer: [] for layer in layer_keys}
    for (layer, net), shape in shapes.items():
        by_layer[layer].append((net, shape))
    for layer, entries in by_layer.items():
        if not entries:
            raise MultilayerCapacitanceError(f"incomplete exact artwork: physical conductor layer {layer!r} has no retained net surface")
        for index, (left_net, left_shape) in enumerate(entries):
            for right_net, right_shape in entries[index + 1:]:
                try:
                    overlap = float(left_shape.intersection(right_shape).area)
                except Exception as exc:
                    raise MultilayerCapacitanceError(f"same-layer artwork intersection failed on {layer!r}") from exc
                if overlap > 1.0e-9:
                    raise MultilayerCapacitanceError(
                        f"ambiguous/shorted artwork: nets {left_net!r} and {right_net!r} overlap by {overlap:.9g} um^2 on {layer!r}"
                    )
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


def extract_multilayer_bulk_capacitance(
    model: MultilayerCapacitanceModel,
    *,
    reference_net: str = "DGND",
    selected_nets: Sequence[str] | None = None,
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

    shapes, display_names = _same_net_shapes(model)
    layer_keys, by_layer = _validate_layers_and_overlaps(model, shapes)
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

    # For upper/lower surfaces i,j, subtract all XY locations occupied by any
    # intermediate conductor. This is the exact nearest-visible partition,
    # not a shortcut based on bounding boxes or raster labels. A non-adjacent
    # opening path is opt-in because the SPD stack alone cannot say what
    # fills the nominal conductor thickness inside the aperture.
    for upper in range(len(layer_keys) - 1):
        for lower in range(upper + 1, len(layer_keys)):
            nonadjacent = lower > upper + 1
            if nonadjacent and not model.enable_nonadjacent_opening_coupling:
                # Do not spend Shapely intersection time determining aperture
                # areas that cannot be physically stamped without fill data.
                continue
            intermediate = [shape for layer in layer_keys[upper + 1:lower] for _net, shape in by_layer[layer]]
            blocker = None
            if intermediate:
                try:
                    from shapely.ops import unary_union
                    blocker = unary_union(intermediate)
                except Exception as exc:
                    raise MultilayerCapacitanceError("intermediate-conductor artwork union failed") from exc
            for upper_net, upper_shape in by_layer[layer_keys[upper]]:
                for lower_net, lower_shape in by_layer[layer_keys[lower]]:
                    if upper_net == lower_net:
                        continue  # a shorted net has no electrostatic terminal pair
                    try:
                        overlap = upper_shape.intersection(lower_shape)
                        if blocker is not None and not bool(getattr(blocker, "is_empty", True)):
                            overlap = overlap.difference(blocker)
                        area_um2 = float(overlap.area)
                    except Exception as exc:
                        raise MultilayerCapacitanceError("exact artwork overlap calculation failed") from exc
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
        selected_names, selected_matrix = _reduce_floating(grounded, grounded_names, selected_nets)
    return MultilayerCapacitanceResult(
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


def capacitance_model_from_project(
    project: ProjectSpec,
    attachments: Mapping[str, bytes],
    layer_order: Sequence[str],
) -> MultilayerCapacitanceModel:
    """Decode a complete, contiguous exact-artwork physical slab from SPD data."""

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
    artwork: list[CapacitanceArtwork] = []
    for raw in records:
        if not isinstance(raw, Mapping) or _canonical(str(raw.get("layer", ""))) not in wanted:
            continue
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
        artwork.append(CapacitanceArtwork(str(raw["layer"]), str(raw["net"]), shape))
    model = MultilayerCapacitanceModel(layers, tuple(gaps), tuple(artwork))
    # Validate all physical layers early, so a caller never mistakes an empty
    #/omitted conductor row for an open field region.
    shapes, _names = _same_net_shapes(model)
    _validate_layers_and_overlaps(model, shapes)
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
    "reduce_floating_multilayer_capacitance",
]
