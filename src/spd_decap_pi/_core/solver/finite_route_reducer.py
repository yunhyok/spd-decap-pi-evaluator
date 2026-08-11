"""Exact topology reduction for finite-R/L conductor route graphs.

The SPD producer can make a quotient graph by coalescing only source-proven
same-layer Trace/artwork equivalence.  Every Via (or other finite two-terminal
conductor) is then supplied to this module as one edge.  This reducer performs
only two electrically exact graph operations:

* recursively remove non-boundary dangling trees, whose branch current is zero
  in an otherwise passive two-terminal network; and
* sum finite R/L along non-boundary degree-two *bridge* chains.

Parallel edges, branching vertices, and every edge that belongs to a cycle are
kept explicit for the downstream sparse MNA.  Every raw physical owner is
accounted for exactly once in the returned owner ledger, including owners of
safe dangling branches that were pruned.

The iterable entry point is suitable for a streaming producer.  A strict CSR
adapter is also provided so an mmap/array producer can validate its compact
adjacency without rebuilding source records merely to call the reducer.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from math import fsum, isfinite
from operator import index as integer_index
from types import MappingProxyType
from typing import Literal


class FiniteRouteReductionError(ValueError):
    """Raised when finite-route evidence or a requested reduction is unsafe."""


def _text(value: object, *, label: str) -> str:
    result = str(value).strip()
    if not result:
        raise FiniteRouteReductionError(f"{label} must not be blank")
    return result


def _key(value: object) -> str:
    return str(value).strip().casefold()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _integer_values(values: Sequence[object], *, label: str) -> tuple[int, ...]:
    """Materialise integer-index values without silently truncating floats."""

    result: list[int] = []
    for value in values:
        if isinstance(value, bool):
            raise FiniteRouteReductionError(
                f"finite-route CSR {label} must contain integers, not booleans"
            )
        try:
            result.append(integer_index(value))
        except TypeError as exc:
            raise FiniteRouteReductionError(
                f"finite-route CSR {label} must contain exact integers"
            ) from exc
    return tuple(result)


@dataclass(frozen=True, slots=True)
class FiniteRouteNode:
    """One ideal quotient vertex on exactly one physical conductor layer.

    ``boundary_roles`` must name every consumer that prevents dangling prune or
    series contraction (for example ``artwork``, ``device``, ``decap``, or
    ``port``).  Additional boundary IDs may be supplied at compile time.
    """

    node_id: str
    layer: str
    boundary_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        node_id = _text(self.node_id, label="finite-route node_id")
        layer = _text(self.layer, label="finite-route node layer")
        by_key: dict[str, str] = {}
        for raw in self.boundary_roles:
            role = _text(raw, label="finite-route boundary role")
            key = role.casefold()
            previous = by_key.get(key)
            if previous is not None and previous != role:
                raise FiniteRouteReductionError(
                    "finite-route boundary roles are ambiguous by case"
                )
            by_key[key] = role
        object.__setattr__(self, "node_id", node_id)
        object.__setattr__(self, "layer", layer)
        object.__setattr__(
            self,
            "boundary_roles",
            tuple(by_key[key] for key in sorted(by_key)),
        )


@dataclass(frozen=True, slots=True)
class FiniteRouteEdge:
    """One finite, passive two-terminal physical branch.

    Distinct physical branches in parallel must be supplied as distinct edges.
    ``owner_ids`` may contain multiple records only when they already form this
    one inseparable two-terminal branch; an owner may not appear on any other
    input edge.
    """

    edge_id: str
    first_node_id: str
    second_node_id: str
    first_layer: str
    second_layer: str
    resistance_ohm: float
    inductance_h: float
    length_um: float
    owner_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        edge_id = _text(self.edge_id, label="finite-route edge_id")
        first = _text(self.first_node_id, label="finite-route first node")
        second = _text(self.second_node_id, label="finite-route second node")
        first_layer = _text(self.first_layer, label="finite-route first layer")
        second_layer = _text(self.second_layer, label="finite-route second layer")
        if first.casefold() == second.casefold():
            raise FiniteRouteReductionError(
                f"finite-route edge {edge_id!r} is a self-loop"
            )
        if first_layer.casefold() == second_layer.casefold():
            raise FiniteRouteReductionError(
                f"finite-route edge {edge_id!r} does not cross conductor layers"
            )
        try:
            resistance = float(self.resistance_ohm)
            inductance = float(self.inductance_h)
            length = float(self.length_um)
        except (TypeError, ValueError) as exc:
            raise FiniteRouteReductionError(
                f"finite-route edge {edge_id!r} has non-numeric physical evidence"
            ) from exc
        if (
            not isfinite(resistance)
            or not isfinite(inductance)
            or not isfinite(length)
            or resistance < 0.0
            or inductance < 0.0
            or length <= 0.0
            or (resistance == 0.0 and inductance == 0.0)
        ):
            raise FiniteRouteReductionError(
                f"finite-route edge {edge_id!r} must have finite passive "
                "non-zero R/L and positive length"
            )
        owner_by_key: dict[str, str] = {}
        for raw in self.owner_ids:
            owner = _text(raw, label="finite-route raw owner")
            key = owner.casefold()
            previous = owner_by_key.get(key)
            if previous is not None:
                raise FiniteRouteReductionError(
                    f"finite-route edge {edge_id!r} duplicates raw owner {owner!r}"
                )
            owner_by_key[key] = owner
        if not owner_by_key:
            raise FiniteRouteReductionError(
                f"finite-route edge {edge_id!r} needs at least one exact raw owner"
            )
        object.__setattr__(self, "edge_id", edge_id)
        object.__setattr__(self, "first_node_id", first)
        object.__setattr__(self, "second_node_id", second)
        object.__setattr__(self, "first_layer", first_layer)
        object.__setattr__(self, "second_layer", second_layer)
        object.__setattr__(self, "resistance_ohm", resistance)
        object.__setattr__(self, "inductance_h", inductance)
        object.__setattr__(self, "length_um", length)
        object.__setattr__(
            self,
            "owner_ids",
            tuple(owner_by_key[key] for key in sorted(owner_by_key)),
        )


ReducedRouteMode = Literal["retained_explicit", "contracted_series"]
OwnerDispositionKind = Literal[
    "retained_explicit", "contracted_series", "pruned_dangling"
]


@dataclass(frozen=True, slots=True)
class ReducedFiniteRouteEdge:
    """One explicit MNA branch or one exact series-contracted bridge chain."""

    reduced_edge_id: str
    first_node_id: str
    second_node_id: str
    first_layer: str
    second_layer: str
    resistance_ohm: float
    inductance_h: float
    length_um: float
    source_edge_ids: tuple[str, ...]
    route_node_ids: tuple[str, ...]
    route_layers: tuple[str, ...]
    owner_ids: tuple[str, ...]
    mode: ReducedRouteMode

    def layer_surface_via_link_kwargs(self) -> Mapping[str, object]:
        """Return a lossless constructor payload for ``LayerSurfaceViaLink``.

        A contracted chain is one equivalent series branch, so it is emitted
        as one finite R/L link.  The endpoint IDs are deliberately preserved
        verbatim: they may name physical surface nodes or external terminal
        supernodes supplied by the producer.  ``owner_ids`` remains the exact
        raw-owner ledger for the branch rather than a synthetic aggregate ID.

        The reducer does not import ``layer_surface_network`` here because the
        network module is a downstream consumer.  The returned mapping can be
        passed directly as ``LayerSurfaceViaLink(**payload)``.
        """

        return MappingProxyType(
            {
                "link_id": self.reduced_edge_id,
                "first_node_id": self.first_node_id,
                "second_node_id": self.second_node_id,
                "count": 1,
                "mode": "finite_parallel_rl",
                "resistance_ohm_per_via": self.resistance_ohm,
                "inductance_h_per_via": self.inductance_h,
                "owner_ids": self.owner_ids,
            }
        )


@dataclass(frozen=True, slots=True)
class PrunedFiniteRouteEdge:
    """One zero-current dangling input edge retained for the audit ledger."""

    source_edge_id: str
    first_node_id: str
    second_node_id: str
    owner_ids: tuple[str, ...]
    reason: Literal["dangling_nonboundary_leaf"] = "dangling_nonboundary_leaf"


@dataclass(frozen=True, slots=True)
class FiniteRouteOwnerDisposition:
    """Exact-once destination for one raw physical owner."""

    owner_id: str
    source_edge_id: str
    kind: OwnerDispositionKind
    reduced_edge_id: str | None


@dataclass(frozen=True, slots=True)
class FiniteRouteReduction:
    """Deterministic boundary-preserving topology reduction result."""

    retained_nodes: tuple[FiniteRouteNode, ...]
    reduced_edges: tuple[ReducedFiniteRouteEdge, ...]
    pruned_edges: tuple[PrunedFiniteRouteEdge, ...]
    pruned_node_ids: tuple[str, ...]
    owner_ledger: Mapping[str, FiniteRouteOwnerDisposition]
    statistics: Mapping[str, int]
    evidence_sha256: str

    def __post_init__(self) -> None:
        if len(self.evidence_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.evidence_sha256
        ):
            raise FiniteRouteReductionError(
                "finite-route reduction evidence identity is not SHA-256"
            )
        object.__setattr__(
            self,
            "owner_ledger",
            MappingProxyType(dict(self.owner_ledger)),
        )
        object.__setattr__(
            self,
            "statistics",
            MappingProxyType(dict(self.statistics)),
        )


def _other(edge: FiniteRouteEdge, node_index: int, endpoints: tuple[int, int]) -> int:
    first, second = endpoints
    if node_index == first:
        return second
    if node_index == second:
        return first
    raise FiniteRouteReductionError(
        f"internal incidence mismatch for edge {edge.edge_id!r}"
    )


def _bridge_edges(
    *,
    active_nodes: set[int],
    active_edges: set[int],
    incidence: Sequence[set[int]],
    endpoints: Sequence[tuple[int, int]],
) -> set[int]:
    """Return bridges of an undirected multigraph without recursion.

    Edge identity, rather than parent vertex identity, is used by the DFS, so a
    pair of parallel edges correctly forms a two-edge cycle and neither edge is
    reported as a bridge.
    """

    discovery = [-1] * len(incidence)
    low = [-1] * len(incidence)
    timer = 0
    bridges: set[int] = set()
    for start in sorted(active_nodes):
        if discovery[start] >= 0 or not (incidence[start] & active_edges):
            continue
        discovery[start] = low[start] = timer
        timer += 1
        # (node, parent edge, parent node, sorted incident edges, cursor)
        stack: list[tuple[int, int, int, tuple[int, ...], int]] = [
            (
                start,
                -1,
                -1,
                tuple(sorted(incidence[start] & active_edges)),
                0,
            )
        ]
        while stack:
            node, parent_edge, parent_node, edge_ids, cursor = stack[-1]
            if cursor >= len(edge_ids):
                stack.pop()
                if parent_edge >= 0:
                    low[parent_node] = min(low[parent_node], low[node])
                    if low[node] > discovery[parent_node]:
                        bridges.add(parent_edge)
                continue
            edge_index = edge_ids[cursor]
            stack[-1] = (
                node,
                parent_edge,
                parent_node,
                edge_ids,
                cursor + 1,
            )
            if edge_index == parent_edge:
                continue
            first, second = endpoints[edge_index]
            neighbor = second if node == first else first
            if discovery[neighbor] < 0:
                discovery[neighbor] = low[neighbor] = timer
                timer += 1
                stack.append(
                    (
                        neighbor,
                        edge_index,
                        node,
                        tuple(sorted(incidence[neighbor] & active_edges)),
                        0,
                    )
                )
            else:
                low[node] = min(low[node], discovery[neighbor])
    return bridges


def _oriented_route(
    route_nodes: list[int],
    route_edges: list[int],
    *,
    nodes: Sequence[FiniteRouteNode],
    edges: Sequence[FiniteRouteEdge],
) -> tuple[list[int], list[int]]:
    forward = (
        tuple(_key(nodes[index].node_id) for index in route_nodes),
        tuple(_key(edges[index].edge_id) for index in route_edges),
    )
    reverse = (
        tuple(reversed(forward[0])),
        tuple(reversed(forward[1])),
    )
    if reverse < forward:
        return list(reversed(route_nodes)), list(reversed(route_edges))
    return route_nodes, route_edges


def _reduced_edge(
    route_nodes: list[int],
    route_edges: list[int],
    *,
    nodes: Sequence[FiniteRouteNode],
    edges: Sequence[FiniteRouteEdge],
) -> ReducedFiniteRouteEdge:
    route_nodes, route_edges = _oriented_route(
        route_nodes, route_edges, nodes=nodes, edges=edges
    )
    if len(route_nodes) != len(route_edges) + 1 or not route_edges:
        raise FiniteRouteReductionError("internal finite-route chain is malformed")
    owner_ids: list[str] = []
    for position, edge_index in enumerate(route_edges):
        edge = edges[edge_index]
        current = nodes[route_nodes[position]]
        following = nodes[route_nodes[position + 1]]
        forward = (
            _key(edge.first_node_id) == _key(current.node_id)
            and _key(edge.second_node_id) == _key(following.node_id)
            and _key(edge.first_layer) == _key(current.layer)
            and _key(edge.second_layer) == _key(following.layer)
        )
        reverse = (
            _key(edge.second_node_id) == _key(current.node_id)
            and _key(edge.first_node_id) == _key(following.node_id)
            and _key(edge.second_layer) == _key(current.layer)
            and _key(edge.first_layer) == _key(following.layer)
        )
        if not (forward or reverse):
            raise FiniteRouteReductionError(
                f"finite-route continuity failed at source edge {edge.edge_id!r}"
            )
        owner_ids.extend(edge.owner_ids)
    mode: ReducedRouteMode = (
        "retained_explicit" if len(route_edges) == 1 else "contracted_series"
    )
    payload = {
        "first_node_id": _key(nodes[route_nodes[0]].node_id),
        "second_node_id": _key(nodes[route_nodes[-1]].node_id),
        "source_edge_ids": [_key(edges[index].edge_id) for index in route_edges],
        "owner_ids": [_key(owner) for owner in owner_ids],
        "mode": mode,
    }
    identity = sha256(_canonical_json(payload)).hexdigest()
    return ReducedFiniteRouteEdge(
        reduced_edge_id=f"finite-route:{identity[:24]}",
        first_node_id=nodes[route_nodes[0]].node_id,
        second_node_id=nodes[route_nodes[-1]].node_id,
        first_layer=nodes[route_nodes[0]].layer,
        second_layer=nodes[route_nodes[-1]].layer,
        resistance_ohm=fsum(edges[index].resistance_ohm for index in route_edges),
        inductance_h=fsum(edges[index].inductance_h for index in route_edges),
        length_um=fsum(edges[index].length_um for index in route_edges),
        source_edge_ids=tuple(edges[index].edge_id for index in route_edges),
        route_node_ids=tuple(nodes[index].node_id for index in route_nodes),
        route_layers=tuple(nodes[index].layer for index in route_nodes),
        owner_ids=tuple(owner_ids),
        mode=mode,
    )


def reduce_finite_route_graph(
    nodes: Iterable[FiniteRouteNode],
    edges: Iterable[FiniteRouteEdge],
    *,
    boundary_node_ids: Iterable[str] = (),
    source_identity: str | None = None,
) -> FiniteRouteReduction:
    """Reduce a finite quotient graph without changing its port behaviour.

    Callers must mark every node carrying a capacitance/admittance stamp, port,
    source, or termination as a boundary.  Only an unmarked passive dangling
    tree can be proven zero-current and removed.  Only an unmarked degree-two
    vertex whose two incident edges are graph bridges can be series-contracted;
    cycle and parallel topology therefore remains explicit by construction.
    """

    node_values = tuple(nodes)
    edge_values = tuple(edges)
    if not node_values:
        raise FiniteRouteReductionError("finite-route graph needs at least one node")
    if not all(isinstance(item, FiniteRouteNode) for item in node_values):
        raise FiniteRouteReductionError(
            "finite-route graph nodes must contain FiniteRouteNode values"
        )
    if not all(isinstance(item, FiniteRouteEdge) for item in edge_values):
        raise FiniteRouteReductionError(
            "finite-route graph edges must contain FiniteRouteEdge values"
        )
    if source_identity is not None:
        source_identity = _text(source_identity, label="finite-route source identity")

    sorted_nodes = tuple(
        sorted(node_values, key=lambda item: (_key(item.node_id), item.node_id))
    )
    node_index_by_key: dict[str, int] = {}
    for index, node in enumerate(sorted_nodes):
        key = _key(node.node_id)
        if key in node_index_by_key:
            raise FiniteRouteReductionError(
                f"finite-route node identity {node.node_id!r} is duplicated or case-ambiguous"
            )
        node_index_by_key[key] = index

    sorted_edges = tuple(
        sorted(edge_values, key=lambda item: (_key(item.edge_id), item.edge_id))
    )
    edge_ids: set[str] = set()
    owner_by_key: dict[str, tuple[str, str]] = {}
    endpoints: list[tuple[int, int]] = []
    incidence: list[set[int]] = [set() for _node in sorted_nodes]
    for edge_index, edge in enumerate(sorted_edges):
        edge_key = _key(edge.edge_id)
        if edge_key in edge_ids:
            raise FiniteRouteReductionError(
                f"finite-route edge identity {edge.edge_id!r} is duplicated or case-ambiguous"
            )
        edge_ids.add(edge_key)
        first = node_index_by_key.get(_key(edge.first_node_id))
        second = node_index_by_key.get(_key(edge.second_node_id))
        if first is None or second is None:
            raise FiniteRouteReductionError(
                f"finite-route edge {edge.edge_id!r} references an unknown node"
            )
        if (
            _key(edge.first_layer) != _key(sorted_nodes[first].layer)
            or _key(edge.second_layer) != _key(sorted_nodes[second].layer)
        ):
            raise FiniteRouteReductionError(
                f"finite-route edge {edge.edge_id!r} layer evidence "
                "disagrees with its endpoint nodes"
            )
        endpoints.append((first, second))
        incidence[first].add(edge_index)
        incidence[second].add(edge_index)
        for owner in edge.owner_ids:
            owner_key = _key(owner)
            previous = owner_by_key.get(owner_key)
            if previous is not None:
                raise FiniteRouteReductionError(
                    f"raw finite-route owner {owner!r} is assigned to both "
                    f"{previous[1]!r} and {edge.edge_id!r}"
                )
            owner_by_key[owner_key] = (owner, edge.edge_id)

    boundary_keys = {
        _key(raw)
        for raw in boundary_node_ids
        if _text(raw, label="finite-route boundary node")
    }
    unknown_boundaries = sorted(boundary_keys - set(node_index_by_key))
    if unknown_boundaries:
        raise FiniteRouteReductionError(
            "finite-route boundary IDs reference unknown nodes: "
            + ", ".join(unknown_boundaries[:8])
        )
    boundary_nodes = {
        index
        for index, node in enumerate(sorted_nodes)
        if node.boundary_roles or _key(node.node_id) in boundary_keys
    }

    active_nodes = set(range(len(sorted_nodes)))
    active_edges = set(range(len(sorted_edges)))
    pruned_edges: set[int] = set()
    queue = deque(
        index
        for index in range(len(sorted_nodes))
        if index not in boundary_nodes and len(incidence[index]) <= 1
    )
    queued = set(queue)
    while queue:
        node = queue.popleft()
        queued.discard(node)
        if node not in active_nodes or node in boundary_nodes:
            continue
        current = incidence[node] & active_edges
        if len(current) > 1:
            continue
        if current:
            edge_index = next(iter(current))
            active_edges.remove(edge_index)
            pruned_edges.add(edge_index)
            neighbor = _other(
                sorted_edges[edge_index], node, endpoints[edge_index]
            )
            if (
                neighbor in active_nodes
                and neighbor not in boundary_nodes
                and len(incidence[neighbor] & active_edges) <= 1
                and neighbor not in queued
            ):
                queue.append(neighbor)
                queued.add(neighbor)
        active_nodes.remove(node)

    bridges = _bridge_edges(
        active_nodes=active_nodes,
        active_edges=active_edges,
        incidence=incidence,
        endpoints=endpoints,
    )
    contractible: set[int] = set()
    for node in active_nodes - boundary_nodes:
        incident = incidence[node] & active_edges
        if len(incident) != 2 or not incident <= bridges:
            continue
        neighbors = {
            _other(sorted_edges[edge], node, endpoints[edge])
            for edge in incident
        }
        if len(neighbors) == 2:
            contractible.add(node)
    kept_nodes = active_nodes - contractible

    visited_edges: set[int] = set()
    reduced: list[ReducedFiniteRouteEdge] = []
    source_to_reduced: dict[int, ReducedFiniteRouteEdge] = {}
    for start in sorted(kept_nodes):
        for first_edge in sorted(incidence[start] & active_edges):
            if first_edge in visited_edges:
                continue
            route_nodes = [start]
            route_edges: list[int] = []
            node = start
            edge_index = first_edge
            while True:
                if edge_index in visited_edges:
                    raise FiniteRouteReductionError(
                        "finite-route chain traversal encountered a cycle marked as a bridge"
                    )
                visited_edges.add(edge_index)
                route_edges.append(edge_index)
                following = _other(
                    sorted_edges[edge_index], node, endpoints[edge_index]
                )
                route_nodes.append(following)
                if following in kept_nodes:
                    break
                if following not in contractible:
                    raise FiniteRouteReductionError(
                        "finite-route chain terminated at an unclassified quotient vertex"
                    )
                candidates = (
                    incidence[following] & active_edges
                ) - {edge_index}
                if len(candidates) != 1:
                    raise FiniteRouteReductionError(
                        "finite-route degree-two bridge chain lost continuity"
                    )
                node = following
                edge_index = next(iter(candidates))
            result = _reduced_edge(
                route_nodes,
                route_edges,
                nodes=sorted_nodes,
                edges=sorted_edges,
            )
            reduced.append(result)
            for source_edge in route_edges:
                source_to_reduced[source_edge] = result
    if visited_edges != active_edges:
        missing = sorted(active_edges - visited_edges)
        raise FiniteRouteReductionError(
            "finite-route reduction failed to retain every cyclic/parallel edge: "
            + ", ".join(sorted_edges[index].edge_id for index in missing[:8])
        )

    reduced.sort(key=lambda item: item.reduced_edge_id)
    pruned_values = tuple(
        PrunedFiniteRouteEdge(
            source_edge_id=sorted_edges[index].edge_id,
            first_node_id=sorted_edges[index].first_node_id,
            second_node_id=sorted_edges[index].second_node_id,
            owner_ids=sorted_edges[index].owner_ids,
        )
        for index in sorted(pruned_edges, key=lambda value: _key(sorted_edges[value].edge_id))
    )
    ledger: dict[str, FiniteRouteOwnerDisposition] = {}
    for edge_index, edge in enumerate(sorted_edges):
        if edge_index in pruned_edges:
            kind: OwnerDispositionKind = "pruned_dangling"
            reduced_id = None
        else:
            target = source_to_reduced.get(edge_index)
            if target is None:
                raise FiniteRouteReductionError(
                    f"finite-route source edge {edge.edge_id!r} has no exact-once disposition"
                )
            kind = target.mode
            reduced_id = target.reduced_edge_id
        for owner in edge.owner_ids:
            ledger[owner] = FiniteRouteOwnerDisposition(
                owner_id=owner,
                source_edge_id=edge.edge_id,
                kind=kind,
                reduced_edge_id=reduced_id,
            )
    if {_key(owner) for owner in ledger} != set(owner_by_key):
        raise FiniteRouteReductionError(
            "finite-route owner ledger does not account for every raw owner exactly once"
        )

    retained_node_values = tuple(sorted_nodes[index] for index in sorted(kept_nodes))
    inactive_nodes = set(range(len(sorted_nodes))) - active_nodes
    degree_before_contraction = Counter(
        len(incidence[index] & active_edges) for index in active_nodes
    )
    endpoint_pair_counts = Counter(
        tuple(sorted((_key(item.first_node_id), _key(item.second_node_id))))
        for item in reduced
    )
    cycle_edges = active_edges - bridges
    cycle_nodes = {
        endpoint
        for edge_index in cycle_edges
        for endpoint in endpoints[edge_index]
    }
    statistics = {
        "input_node_count": len(sorted_nodes),
        "input_edge_count": len(sorted_edges),
        "raw_owner_count": len(owner_by_key),
        "boundary_node_count": len(boundary_nodes),
        "pruned_node_count": len(inactive_nodes),
        "pruned_edge_count": len(pruned_edges),
        "pruned_owner_count": sum(
            len(sorted_edges[index].owner_ids) for index in pruned_edges
        ),
        "surviving_quotient_node_count": len(active_nodes),
        "surviving_source_edge_count": len(active_edges),
        "bridge_edge_count": len(bridges),
        "cycle_or_parallel_edge_count": len(cycle_edges),
        "cycle_or_parallel_node_count": len(cycle_nodes),
        "degree_gt2_node_count": sum(
            count for degree, count in degree_before_contraction.items() if degree > 2
        ),
        "degree2_contracted_node_count": len(contractible),
        "retained_node_count": len(retained_node_values),
        "reduced_edge_count": len(reduced),
        "retained_explicit_edge_count": sum(
            item.mode == "retained_explicit" for item in reduced
        ),
        "contracted_series_edge_count": sum(
            item.mode == "contracted_series" for item in reduced
        ),
        "parallel_reduced_pair_count": sum(
            count > 1 for count in endpoint_pair_counts.values()
        ),
        "maximum_surviving_degree": max(degree_before_contraction, default=0),
    }
    canonical_reduced = [
        {
            "reduced_edge_id": item.reduced_edge_id,
            "first_node_id": _key(item.first_node_id),
            "second_node_id": _key(item.second_node_id),
            "resistance_ohm": item.resistance_ohm,
            "inductance_h": item.inductance_h,
            "length_um": item.length_um,
            "source_edge_ids": [_key(value) for value in item.source_edge_ids],
            "route_node_ids": [_key(value) for value in item.route_node_ids],
            "owner_ids": [_key(value) for value in item.owner_ids],
            "mode": item.mode,
        }
        for item in reduced
    ]
    canonical_ledger = [
        {
            "owner_id": _key(value.owner_id),
            "source_edge_id": _key(value.source_edge_id),
            "kind": value.kind,
            "reduced_edge_id": value.reduced_edge_id,
        }
        for _owner, value in sorted(ledger.items(), key=lambda item: _key(item[0]))
    ]
    evidence_sha256 = sha256(
        _canonical_json(
            {
                "schema": "finite-route-reduction-v1",
                "source_identity": source_identity,
                "input_nodes": [
                    {
                        "node_id": _key(item.node_id),
                        "layer": _key(item.layer),
                        "boundary_roles": [
                            _key(role) for role in item.boundary_roles
                        ],
                    }
                    for item in sorted_nodes
                ],
                "input_edges": [
                    {
                        "edge_id": _key(item.edge_id),
                        "first_node_id": _key(item.first_node_id),
                        "second_node_id": _key(item.second_node_id),
                        "first_layer": _key(item.first_layer),
                        "second_layer": _key(item.second_layer),
                        "resistance_ohm": item.resistance_ohm,
                        "inductance_h": item.inductance_h,
                        "length_um": item.length_um,
                        "owner_ids": [_key(owner) for owner in item.owner_ids],
                    }
                    for item in sorted_edges
                ],
                "boundaries": sorted(_key(sorted_nodes[index].node_id) for index in boundary_nodes),
                "retained_nodes": [
                    {
                        "node_id": _key(item.node_id),
                        "layer": _key(item.layer),
                        "boundary_roles": [_key(role) for role in item.boundary_roles],
                    }
                    for item in retained_node_values
                ],
                "reduced_edges": canonical_reduced,
                "pruned_edges": [
                    {
                        "source_edge_id": _key(item.source_edge_id),
                        "owner_ids": [_key(owner) for owner in item.owner_ids],
                    }
                    for item in pruned_values
                ],
                "owner_ledger": canonical_ledger,
                "statistics": statistics,
            }
        )
    ).hexdigest()
    return FiniteRouteReduction(
        retained_nodes=retained_node_values,
        reduced_edges=tuple(reduced),
        pruned_edges=pruned_values,
        pruned_node_ids=tuple(
            sorted(
                (sorted_nodes[index].node_id for index in inactive_nodes),
                key=lambda value: (value.casefold(), value),
            )
        ),
        owner_ledger=ledger,
        statistics=statistics,
        evidence_sha256=evidence_sha256,
    )


def reduce_finite_route_csr(
    nodes: Sequence[FiniteRouteNode],
    edges: Sequence[FiniteRouteEdge],
    indptr: Sequence[int],
    indices: Sequence[int],
    edge_indices: Sequence[int],
    *,
    boundary_node_ids: Iterable[str] = (),
    source_identity: str | None = None,
) -> FiniteRouteReduction:
    """Validate an undirected edge-indexed CSR view and reduce the same graph.

    Each physical input edge must appear in exactly two CSR slots: ``u -> v``
    and ``v -> u``.  Parallel branches use distinct ``edge_indices`` values and
    therefore remain distinguishable throughout validation and reduction.
    """

    node_values = tuple(nodes)
    edge_values = tuple(edges)
    if not all(isinstance(item, FiniteRouteNode) for item in node_values):
        raise FiniteRouteReductionError(
            "finite-route CSR nodes must contain FiniteRouteNode values"
        )
    if not all(isinstance(item, FiniteRouteEdge) for item in edge_values):
        raise FiniteRouteReductionError(
            "finite-route CSR edges must contain FiniteRouteEdge values"
        )
    pointers = _integer_values(indptr, label="indptr")
    neighbors = _integer_values(indices, label="indices")
    adjacency_edges = _integer_values(edge_indices, label="edge_indices")
    if (
        len(pointers) != len(node_values) + 1
        or not pointers
        or pointers[0] != 0
        or any(first > second for first, second in zip(pointers, pointers[1:]))
        or pointers[-1] != len(neighbors)
        or len(neighbors) != len(adjacency_edges)
    ):
        raise FiniteRouteReductionError("finite-route CSR pointer/slot dimensions are invalid")
    node_by_key = {_key(node.node_id): index for index, node in enumerate(node_values)}
    if len(node_by_key) != len(node_values):
        raise FiniteRouteReductionError("finite-route CSR node identities are ambiguous")
    observed: dict[int, list[tuple[int, int]]] = {}
    for node_index in range(len(node_values)):
        for slot in range(pointers[node_index], pointers[node_index + 1]):
            neighbor = neighbors[slot]
            edge_index = adjacency_edges[slot]
            if (
                neighbor < 0
                or neighbor >= len(node_values)
                or edge_index < 0
                or edge_index >= len(edge_values)
            ):
                raise FiniteRouteReductionError("finite-route CSR slot is out of range")
            edge = edge_values[edge_index]
            actual = {
                _key(edge.first_node_id),
                _key(edge.second_node_id),
            }
            expected = {
                _key(node_values[node_index].node_id),
                _key(node_values[neighbor].node_id),
            }
            if actual != expected:
                raise FiniteRouteReductionError(
                    f"finite-route CSR slot disagrees with edge {edge.edge_id!r} endpoints"
                )
            observed.setdefault(edge_index, []).append((node_index, neighbor))
    if set(observed) != set(range(len(edge_values))):
        raise FiniteRouteReductionError("finite-route CSR omits one or more physical edges")
    for edge_index, slots in observed.items():
        if len(slots) != 2 or slots[0] != (slots[1][1], slots[1][0]):
            raise FiniteRouteReductionError(
                f"finite-route CSR edge {edge_values[edge_index].edge_id!r} "
                "is not one reciprocal undirected pair"
            )
    return reduce_finite_route_graph(
        node_values,
        edge_values,
        boundary_node_ids=boundary_node_ids,
        source_identity=source_identity,
    )


__all__ = [
    "FiniteRouteEdge",
    "FiniteRouteNode",
    "FiniteRouteOwnerDisposition",
    "FiniteRouteReduction",
    "FiniteRouteReductionError",
    "PrunedFiniteRouteEdge",
    "ReducedFiniteRouteEdge",
    "reduce_finite_route_csr",
    "reduce_finite_route_graph",
]
