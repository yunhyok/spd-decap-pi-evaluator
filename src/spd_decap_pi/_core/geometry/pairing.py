"""Coordinate-based Device PWR/DGND bump pairing and clustering."""

from __future__ import annotations

from collections.abc import Iterable
from math import hypot

import numpy as np
from pydantic import Field
from scipy.optimize import linear_sum_assignment
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import min_weight_full_bipartite_matching
from scipy.spatial import cKDTree

from ..domain import ConfidenceLevel, DomainModel, PinKind, PinRecord, TerminalKind


class BumpPairingError(ValueError):
    pass


# The dense solver remains the source of truth for ordinary projects.  Above
# this limit, keeping both the distance and biased-cost matrices can consume
# hundreds of MiB (and the former list-of-lists implementation considerably
# more).  The large-input path bounds its candidate graph independently.
_DENSE_COST_MATRIX_MAX_CELLS = 2_000_000
_SPARSE_CANDIDATE_MAX_CELLS = 1_000_000
_SPARSE_INITIAL_NEIGHBORS = 8
_SPARSE_MAX_NEIGHBORS = 128
_NEAREST_CLUSTER_CANDIDATES = 8


class BumpPair(DomainModel):
    group_key: str
    power_pin_id: str
    ground_pin_id: str
    distance_um: float = Field(ge=0)


class BumpCluster(DomainModel):
    cluster_id: str
    group_key: str
    power_pin_ids: list[str]
    ground_pin_ids: list[str]
    max_distance_um: float = Field(ge=0)


class BumpPairingResult(DomainModel):
    pairs: list[BumpPair]
    clusters: list[BumpCluster]
    unmatched_power_pin_ids: list[str] = Field(default_factory=list)
    unmatched_ground_pin_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel
    confidence_reason: str


def _distance(first: PinRecord, second: PinRecord) -> float:
    return hypot(first.x_um - second.x_um, first.y_um - second.y_um)


def _coordinates(pins: list[PinRecord]) -> np.ndarray:
    return np.asarray([(pin.x_um, pin.y_um) for pin in pins], dtype=float)


def _dense_minimum_cost_indices(
    powers: list[PinRecord], grounds: list[PinRecord]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run the legacy exact assignment for a safely bounded matrix."""

    costs = np.asarray(
        [[_distance(power, ground) for ground in grounds] for power in powers],
        dtype=float,
    )
    # Stable, insignificant tie-break that does not alter physical rankings.
    row_bias = np.arange(len(powers), dtype=float)[:, None] * 1.0e-12
    column_bias = np.arange(len(grounds), dtype=float)[None, :] * 1.0e-15
    row_indices, column_indices = linear_sum_assignment(costs + row_bias + column_bias)
    return row_indices, column_indices, costs


def _candidate_arrays(
    query_coordinates: np.ndarray,
    tree: cKDTree,
    *,
    neighbor_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    distances, indices = tree.query(query_coordinates, k=neighbor_count, workers=1)
    distances = np.asarray(distances, dtype=float)
    indices = np.asarray(indices, dtype=np.int64)
    if neighbor_count == 1:
        distances = distances[:, None]
        indices = indices[:, None]

    # cKDTree does not promise a stable order for equidistant points.  Sorting
    # the returned candidates makes repeated imports deterministic.
    for row in range(len(query_coordinates)):
        order = np.lexsort((indices[row], distances[row]))
        distances[row] = distances[row, order]
        indices[row] = indices[row, order]
    return distances, indices


def _fallback_greedy_indices(
    candidate_distances: np.ndarray,
    candidate_indices: np.ndarray,
    *,
    tree: cKDTree,
    query_coordinates: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a complete, deterministic matching without a dense matrix.

    This path is only used when the bounded nearest-neighbor graph has no full
    matching.  It greedily accepts the shortest candidate edges, then assigns
    each remaining row to its nearest unused point.  A single-row query may
    grow to the full tree, so memory remains O(number of pins), not O(PWR*GND).
    """

    row_grid = np.broadcast_to(
        np.arange(len(query_coordinates), dtype=np.int64)[:, None],
        candidate_indices.shape,
    )
    flat_rows = row_grid.ravel()
    flat_columns = candidate_indices.ravel()
    flat_distances = candidate_distances.ravel()
    edge_order = np.lexsort((flat_columns, flat_rows, flat_distances))

    assigned_rows: dict[int, int] = {}
    used_columns: set[int] = set()
    for edge in edge_order:
        row = int(flat_rows[edge])
        column = int(flat_columns[edge])
        if row in assigned_rows or column in used_columns:
            continue
        assigned_rows[row] = column
        used_columns.add(column)
        if len(assigned_rows) == len(query_coordinates):
            break

    point_count = int(tree.n)
    initial_count = min(
        point_count,
        max(_SPARSE_INITIAL_NEIGHBORS, candidate_indices.shape[1]),
    )
    for row in range(len(query_coordinates)):
        if row in assigned_rows:
            continue
        neighbor_count = initial_count
        while True:
            distances, indices = tree.query(
                query_coordinates[row], k=neighbor_count, workers=1
            )
            distances = np.atleast_1d(np.asarray(distances, dtype=float))
            indices = np.atleast_1d(np.asarray(indices, dtype=np.int64))
            order = np.lexsort((indices, distances))
            available = [
                int(indices[index])
                for index in order
                if int(indices[index]) not in used_columns
            ]
            if available:
                assigned_rows[row] = available[0]
                used_columns.add(available[0])
                break
            if neighbor_count == point_count:
                raise BumpPairingError("unable to construct a unique bump pairing")
            neighbor_count = min(point_count, neighbor_count * 2)

    rows = np.asarray(sorted(assigned_rows), dtype=np.int64)
    columns = np.asarray([assigned_rows[int(row)] for row in rows], dtype=np.int64)
    return rows, columns


def _sparse_minimum_cost_indices(
    powers: list[PinRecord], grounds: list[PinRecord]
) -> tuple[np.ndarray, np.ndarray]:
    """Approximate the assignment on a bounded nearest-neighbor graph."""

    power_coordinates = _coordinates(powers)
    ground_coordinates = _coordinates(grounds)
    query_is_power = len(powers) <= len(grounds)
    query_coordinates = power_coordinates if query_is_power else ground_coordinates
    tree_coordinates = ground_coordinates if query_is_power else power_coordinates
    tree = cKDTree(tree_coordinates)

    max_neighbors = min(
        len(tree_coordinates),
        _SPARSE_MAX_NEIGHBORS,
        max(1, _SPARSE_CANDIDATE_MAX_CELLS // len(query_coordinates)),
    )
    neighbor_count = min(_SPARSE_INITIAL_NEIGHBORS, max_neighbors)
    while True:
        distances, indices = _candidate_arrays(
            query_coordinates,
            tree,
            neighbor_count=neighbor_count,
        )
        query_rows = np.repeat(
            np.arange(len(query_coordinates), dtype=np.int64), neighbor_count
        )
        tree_columns = indices.ravel()

        if query_is_power:
            power_indices = query_rows
            ground_indices = tree_columns
        else:
            power_indices = tree_columns
            ground_indices = query_rows
        biased_costs = (
            distances.ravel()
            + power_indices.astype(float) * 1.0e-12
            + ground_indices.astype(float) * 1.0e-15
        )
        # Sparse matrices discard exact zero entries, which would remove valid
        # coincident-coordinate candidates.
        biased_costs[biased_costs == 0.0] = np.finfo(float).tiny
        graph = csr_matrix(
            (biased_costs, (query_rows, tree_columns)),
            shape=(len(query_coordinates), len(tree_coordinates)),
        )
        try:
            query_matches, tree_matches = min_weight_full_bipartite_matching(graph)
            if len(query_matches) == len(query_coordinates):
                break
        except ValueError:
            pass

        if neighbor_count == max_neighbors:
            query_matches, tree_matches = _fallback_greedy_indices(
                distances,
                indices,
                tree=tree,
                query_coordinates=query_coordinates,
            )
            break
        neighbor_count = min(max_neighbors, neighbor_count * 2)

    if query_is_power:
        return query_matches.astype(np.int64), tree_matches.astype(np.int64)
    return tree_matches.astype(np.int64), query_matches.astype(np.int64)


def minimum_cost_pairs(
    power_pins: Iterable[PinRecord],
    ground_pins: Iterable[PinRecord],
    *,
    group_key: str = "all",
    max_distance_um: float | None = None,
) -> list[BumpPair]:
    """Return unique PWR/GND pairs without an unbounded distance matrix.

    Inputs within the dense matrix limit retain the exact legacy Hungarian
    assignment.  Larger inputs use a bounded nearest-neighbor candidate graph
    and may therefore be approximate.
    """

    powers = sorted(power_pins, key=lambda pin: pin.pin_id.casefold())
    grounds = sorted(ground_pins, key=lambda pin: pin.pin_id.casefold())
    if not powers or not grounds:
        return []
    matrix_cells = len(powers) * len(grounds)
    if matrix_cells <= _DENSE_COST_MATRIX_MAX_CELLS:
        row_indices, column_indices, costs = _dense_minimum_cost_indices(powers, grounds)
    else:
        row_indices, column_indices = _sparse_minimum_cost_indices(powers, grounds)
        costs = None
    result: list[BumpPair] = []
    for row, column in zip(row_indices, column_indices, strict=True):
        distance = (
            float(costs[int(row), int(column)])
            if costs is not None
            else _distance(powers[int(row)], grounds[int(column)])
        )
        if max_distance_um is not None and distance > max_distance_um:
            continue
        result.append(
            BumpPair(
                group_key=group_key,
                power_pin_id=powers[int(row)].pin_id,
                ground_pin_id=grounds[int(column)].pin_id,
                distance_um=distance,
            )
        )
    return sorted(result, key=lambda pair: (pair.group_key, pair.power_pin_id.casefold()))


def _attach_by_spatial_index(
    pins: list[PinRecord],
    *,
    cluster_data: list[dict[str, object]],
    source_key: str,
    target_key: str,
    source_lookup: dict[str, PinRecord],
    max_distance_um: float | None,
) -> list[str]:
    """Attach surplus pins to the nearest cluster using bounded spatial data."""

    if not pins or not cluster_data:
        return [pin.pin_id for pin in pins]

    candidate_coordinates: list[tuple[float, float]] = []
    candidate_clusters: list[int] = []
    for cluster_index, cluster in enumerate(cluster_data):
        source_ids = cluster[source_key]
        assert isinstance(source_ids, list)
        for pin_id in source_ids:
            source = source_lookup[pin_id]
            candidate_coordinates.append((source.x_um, source.y_um))
            candidate_clusters.append(cluster_index)

    if not candidate_coordinates:
        return [pin.pin_id for pin in pins]

    tree = cKDTree(np.asarray(candidate_coordinates, dtype=float))
    neighbor_count = min(_NEAREST_CLUSTER_CANDIDATES, len(candidate_coordinates))
    distances, indices = _candidate_arrays(
        _coordinates(pins), tree, neighbor_count=neighbor_count
    )
    unmatched: list[str] = []
    for row, pin in enumerate(pins):
        choices = [
            (
                float(distances[row, column]),
                candidate_clusters[int(indices[row, column])],
                int(indices[row, column]),
            )
            for column in range(neighbor_count)
        ]
        distance, cluster_index, _ = min(choices)
        if max_distance_um is not None and distance > max_distance_um:
            unmatched.append(pin.pin_id)
            continue
        target_ids = cluster_data[cluster_index][target_key]
        assert isinstance(target_ids, list)
        target_ids.append(pin.pin_id)
        cluster_data[cluster_index]["max_distance"] = max(
            float(cluster_data[cluster_index]["max_distance"]), distance
        )
    return unmatched


def _site_key(pin: PinRecord, *, group_by_site: bool) -> str:
    return pin.site or "UNSPECIFIED_SITE" if group_by_site else "all"


def pair_device_bumps(
    pins: Iterable[PinRecord],
    *,
    rail_net: str | None = None,
    gnd_aliases: Iterable[str] = ("DGND", "GND"),
    group_by_site: bool = True,
    max_distance_um: float | None = None,
) -> BumpPairingResult:
    """Propose editable pair/cluster groups without crossing site boundaries."""

    if max_distance_um is not None and max_distance_um <= 0:
        raise ValueError("max_distance_um must be positive")
    device_pins = [pin for pin in pins if pin.kind == PinKind.DEVICE_BUMP]
    gnd_keys = {alias.casefold() for alias in gnd_aliases}
    rail_key = rail_net.casefold() if rail_net is not None else None
    powers = [
        pin
        for pin in device_pins
        if pin.terminal == TerminalKind.PWR
        and (rail_key is None or pin.net.casefold() == rail_key)
    ]
    grounds = [
        pin
        for pin in device_pins
        if pin.terminal == TerminalKind.GND or pin.net.casefold() in gnd_keys
    ]
    if not powers:
        raise BumpPairingError("no Device PWR bumps match the requested rail")
    if not grounds:
        raise BumpPairingError("no Device DGND bumps are available")

    power_by_group: dict[str, list[PinRecord]] = {}
    ground_by_group: dict[str, list[PinRecord]] = {}
    for pin in powers:
        power_by_group.setdefault(_site_key(pin, group_by_site=group_by_site), []).append(pin)
    for pin in grounds:
        ground_by_group.setdefault(_site_key(pin, group_by_site=group_by_site), []).append(pin)

    all_pairs: list[BumpPair] = []
    all_clusters: list[BumpCluster] = []
    unmatched_power: list[str] = []
    unmatched_ground: list[str] = []
    unbalanced = False
    used_scalable_approximation = False
    for group_key in sorted(set(power_by_group) | set(ground_by_group), key=str.casefold):
        group_powers = sorted(
            power_by_group.get(group_key, []), key=lambda pin: pin.pin_id.casefold()
        )
        group_grounds = sorted(
            ground_by_group.get(group_key, []), key=lambda pin: pin.pin_id.casefold()
        )
        if not group_powers:
            unmatched_ground.extend(pin.pin_id for pin in group_grounds)
            continue
        if not group_grounds:
            unmatched_power.extend(pin.pin_id for pin in group_powers)
            continue
        pairs = minimum_cost_pairs(
            group_powers,
            group_grounds,
            group_key=group_key,
            max_distance_um=max_distance_um,
        )
        all_pairs.extend(pairs)
        power_lookup = {pin.pin_id: pin for pin in group_powers}
        ground_lookup = {pin.pin_id: pin for pin in group_grounds}
        cluster_data: list[dict[str, object]] = [
            {
                "power": [pair.power_pin_id],
                "ground": [pair.ground_pin_id],
                "max_distance": pair.distance_um,
            }
            for pair in pairs
        ]
        paired_power = {pair.power_pin_id for pair in pairs}
        paired_ground = {pair.ground_pin_id for pair in pairs}
        remaining_power = [pin for pin in group_powers if pin.pin_id not in paired_power]
        remaining_ground = [pin for pin in group_grounds if pin.pin_id not in paired_ground]
        if remaining_power or remaining_ground:
            unbalanced = True

        use_spatial_index = (
            len(group_powers) * len(group_grounds) > _DENSE_COST_MATRIX_MAX_CELLS
        )
        if use_spatial_index:
            used_scalable_approximation = True
            unmatched_power.extend(
                _attach_by_spatial_index(
                    remaining_power,
                    cluster_data=cluster_data,
                    source_key="ground",
                    target_key="power",
                    source_lookup=ground_lookup,
                    max_distance_um=max_distance_um,
                )
            )
            unmatched_ground.extend(
                _attach_by_spatial_index(
                    remaining_ground,
                    cluster_data=cluster_data,
                    source_key="power",
                    target_key="ground",
                    source_lookup=power_lookup,
                    max_distance_um=max_distance_um,
                )
            )
        else:
            for pin in remaining_power:
                choices: list[tuple[float, int]] = []
                for index, cluster in enumerate(cluster_data):
                    ground_ids = cluster["ground"]
                    assert isinstance(ground_ids, list)
                    distance = min(_distance(pin, ground_lookup[item]) for item in ground_ids)
                    choices.append((distance, index))
                if not choices:
                    unmatched_power.append(pin.pin_id)
                    continue
                distance, index = min(choices)
                if max_distance_um is not None and distance > max_distance_um:
                    unmatched_power.append(pin.pin_id)
                    continue
                power_ids = cluster_data[index]["power"]
                assert isinstance(power_ids, list)
                power_ids.append(pin.pin_id)
                cluster_data[index]["max_distance"] = max(
                    float(cluster_data[index]["max_distance"]), distance
                )

            for pin in remaining_ground:
                choices = []
                for index, cluster in enumerate(cluster_data):
                    power_ids = cluster["power"]
                    assert isinstance(power_ids, list)
                    distance = min(_distance(pin, power_lookup[item]) for item in power_ids)
                    choices.append((distance, index))
                if not choices:
                    unmatched_ground.append(pin.pin_id)
                    continue
                distance, index = min(choices)
                if max_distance_um is not None and distance > max_distance_um:
                    unmatched_ground.append(pin.pin_id)
                    continue
                ground_ids = cluster_data[index]["ground"]
                assert isinstance(ground_ids, list)
                ground_ids.append(pin.pin_id)
                cluster_data[index]["max_distance"] = max(
                    float(cluster_data[index]["max_distance"]), distance
                )

        for index, cluster in enumerate(cluster_data, start=1):
            all_clusters.append(
                BumpCluster(
                    cluster_id=f"{group_key}:PAIR_{index:03d}",
                    group_key=group_key,
                    power_pin_ids=list(cluster["power"]),
                    ground_pin_ids=list(cluster["ground"]),
                    max_distance_um=float(cluster["max_distance"]),
                )
            )

    unmatched_power = sorted(set(unmatched_power), key=str.casefold)
    unmatched_ground = sorted(set(unmatched_ground), key=str.casefold)
    if unmatched_power or unmatched_ground or used_scalable_approximation:
        confidence = ConfidenceLevel.LOW
    elif unbalanced:
        confidence = ConfidenceLevel.MEDIUM
    else:
        confidence = ConfidenceLevel.HIGH
    reason = (
        f"unique pairs={len(all_pairs)}, clusters={len(all_clusters)}, "
        f"unmatched PWR={len(unmatched_power)}, unmatched GND={len(unmatched_ground)}"
    )
    if unbalanced and confidence != ConfidenceLevel.LOW:
        reason += "; unequal terminal counts were represented as shared clusters"
    if used_scalable_approximation:
        reason += "; large group used bounded approximate spatial matching"
    return BumpPairingResult(
        pairs=all_pairs,
        clusters=all_clusters,
        unmatched_power_pin_ids=unmatched_power,
        unmatched_ground_pin_ids=unmatched_ground,
        confidence=confidence,
        confidence_reason=reason,
    )


__all__ = [
    "BumpCluster",
    "BumpPair",
    "BumpPairingError",
    "BumpPairingResult",
    "minimum_cost_pairs",
    "pair_device_bumps",
]
