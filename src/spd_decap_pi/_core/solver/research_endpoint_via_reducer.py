"""Research-only endpoint-aware reduction of source SPD Via/Trace topology.

This adapter is deliberately *not* connected to Evaluation Analysis.  It is a
bounded experiment for checking whether a rail-template's single PWR leg has
discarded branches, serial microvia sections, or a shared bottleneck.  Trace
records are used only as ideal **topological** joins: their R/L is never
invented, and every resulting report carries that limitation.

The only zero-ohm merges made here are (1) source Trace-connected Nodes, and
(2) caller-supplied, source-proven members of one target-artwork component.
In particular, nodes merely sharing a NET name or a PadStack are not merged.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite, pi
from types import MappingProxyType
from typing import Literal

import numpy as np

from spd_decap_pi._core.io.conductor_graph import (
    ConductorLayer,
    ConductorNode,
    ConductorTrace,
    ConductorVia,
    SpdConductorGraph,
)
from spd_decap_pi._core.solver.global_mna import (
    DifferentialPort,
    FilledMicroviaBranch,
    GlobalMnaOperator,
    SeriesBranchBlock,
    compile_global_mna,
    filled_microvia_branch_block,
)
from spd_decap_pi._core.via_model import (
    HOLLOW_PLATED_BARREL,
    SOLID_COPPER_FILLED_MICROVIA,
    ViaModelError,
    estimate_via_segment_rl,
)


class EndpointViaReducerError(ValueError):
    """Raised when endpoint evidence would require an inferred connection."""


@dataclass(frozen=True, slots=True)
class EndpointViaDiagnostic:
    severity: Literal["info", "warning", "error"]
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class EndpointViaBranch:
    """One retained physical source Via; it is never path-compressed."""

    via_id: str
    padstack: str
    positive_source_node_key: str
    negative_source_node_key: str
    positive_reduced_node_id: str
    negative_reduced_node_id: str
    x_m: float
    y_m: float
    z0_m: float
    z1_m: float
    drill_diameter_um: float
    conductivity_s_per_m: float
    conductor_model: Literal["SOLID_COPPER_FILLED_MICROVIA", "HOLLOW_PLATED_BARREL"]
    resistance_ohm: float
    inductance_h: float


@dataclass(frozen=True, slots=True)
class EndpointViaTopology:
    """Immutable source-to-endpoint topology before an MNA frequency solve."""

    net: str
    source_path: str
    source_node_count: int
    source_via_count: int
    source_trace_count: int
    retained_via_count: int
    ideal_trace_ids: tuple[str, ...]
    reduced_node_ids: tuple[str, ...]
    external_node_ids: tuple[str, ...]
    target_node_ids: tuple[str, ...]
    branches: tuple[EndpointViaBranch, ...]
    diagnostics: tuple[EndpointViaDiagnostic, ...]

    @property
    def trace_rl_missing(self) -> bool:
        return bool(self.ideal_trace_ids)


@dataclass(frozen=True, slots=True)
class EndpointViaMnaModel:
    """Compiled passive MNA network and transparent topology evidence."""

    topology: EndpointViaTopology
    operator: GlobalMnaOperator
    port_ids: tuple[str, ...]

    def solve(self, frequency_hz: float):
        return self.operator.solve(frequency_hz)


@dataclass(frozen=True, slots=True)
class _LayerForViaModel:
    name: str
    thickness_um: float
    conductivity_s_m: float | None

    @property
    def is_conductor(self) -> bool:
        return self.conductivity_s_m is not None


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self._parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self._parent[value]
        while parent != self._parent[parent]:
            parent = self._parent[parent]
        while value != parent:
            next_value = self._parent[value]
            self._parent[value] = parent
            value = next_value
        return parent

    def union(self, first: str, second: str) -> None:
        left, right = self.find(first), self.find(second)
        if left != right:
            # Lexical representative makes results independent of source order.
            if right < left:
                left, right = right, left
            self._parent[right] = left


def _node_lookup(graph: SpdConductorGraph, net: str) -> dict[str, ConductorNode]:
    selected = {
        item.source_id.casefold(): item
        for item in graph.nodes
        if item.net.casefold() == net.casefold()
    }
    if not selected:
        raise EndpointViaReducerError(f"requested NET {net!r} has no retained source Nodes")
    return selected


def _canonical_source_ids(
    requested: Iterable[str],
    nodes: Mapping[str, ConductorNode],
    *,
    role: str,
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in requested:
        key = str(raw).strip().casefold()
        if not key:
            continue
        if key not in nodes:
            raise EndpointViaReducerError(
                f"{role} Node {raw!r} is absent from the exact requested NET graph"
            )
        if key not in seen:
            result.append(nodes[key].key)
            seen.add(key)
    if not result:
        raise EndpointViaReducerError(f"at least one exact {role} Node is required")
    return tuple(sorted(result))


def _source_stackup(graph: SpdConductorGraph) -> tuple[_LayerForViaModel, ...]:
    rows: list[_LayerForViaModel] = []
    for item in graph.layers:
        if item.thickness_um is None or not isfinite(item.thickness_um) or item.thickness_um <= 0.0:
            raise EndpointViaReducerError(
                f"stack-up layer {item.name!r} lacks finite positive thickness evidence"
            )
        rows.append(_LayerForViaModel(
            item.name,
            float(item.thickness_um),
            float(item.conductivity_s_m) if item.conductivity_s_m is not None else None,
        ))
    if not rows:
        raise EndpointViaReducerError("source graph has no physical stack-up rows")
    return tuple(rows)


def _layer_center_depths_m(layers: tuple[_LayerForViaModel, ...]) -> dict[str, float]:
    depth_um = 0.0
    centers: dict[str, float] = {}
    for item in layers:
        centers[item.name.casefold()] = (depth_um + item.thickness_um / 2.0) * 1.0e-6
        depth_um += item.thickness_um
    return centers


def _artwork_component_joins(
    union: _UnionFind,
    target_keys: tuple[str, ...],
    target_artwork_components: Mapping[str, str] | None,
) -> None:
    """Apply only explicit target-component evidence, never a NET-wide tie."""

    if target_artwork_components is None:
        return
    target_by_key = {key.rsplit("\x1f", 1)[1].casefold(): key for key in target_keys}
    grouped: dict[str, list[str]] = defaultdict(list)
    for raw_node, raw_component in target_artwork_components.items():
        node_key = str(raw_node).strip().casefold()
        component = str(raw_component).strip()
        if node_key not in target_by_key:
            raise EndpointViaReducerError(
                "target artwork evidence references a Node outside the selected target set: "
                f"{raw_node!r}"
            )
        if not component:
            raise EndpointViaReducerError(
                f"target artwork component for Node {raw_node!r} is blank"
            )
        grouped[component.casefold()].append(target_by_key[node_key])
    for members in grouped.values():
        for member in sorted(members)[1:]:
            union.union(members[0], member)


def build_endpoint_via_topology(
    graph: SpdConductorGraph,
    *,
    net: str,
    external_node_ids: Iterable[str],
    target_node_ids: Iterable[str],
    target_artwork_components: Mapping[str, str] | None = None,
) -> EndpointViaTopology:
    """Retain every source Via between selected external and target endpoints.

    ``external_node_ids`` and ``target_node_ids`` must be source ``Node`` IDs
    obtained from the same rail/pin geometry.  Component labels are optional
    evidence from an artwork connectivity extractor; without them, target
    landings remain separate MNA ports rather than being assumed equipotential.
    """

    canonical_net = next(
        (item for item in graph.requested_nets if item.casefold() == net.casefold()),
        None,
    )
    if canonical_net is None:
        raise EndpointViaReducerError(f"NET {net!r} was not requested from this graph")
    nodes = _node_lookup(graph, canonical_net)
    external_keys = _canonical_source_ids(external_node_ids, nodes, role="external")
    target_keys = _canonical_source_ids(target_node_ids, nodes, role="target")
    if set(external_keys) & set(target_keys):
        raise EndpointViaReducerError("an external Node cannot also be a target-plane Node")

    union = _UnionFind(item.key for item in nodes.values())
    traces = tuple(sorted(
        (item for item in graph.traces if item.net.casefold() == canonical_net.casefold()),
        key=lambda item: item.source_id.casefold(),
    ))
    diagnostics: list[EndpointViaDiagnostic] = []
    for trace in traces:
        first = nodes.get(trace.starting_node_id.casefold())
        second = nodes.get(trace.ending_node_id.casefold())
        if first is None or second is None:
            raise EndpointViaReducerError(
                f"Trace {trace.source_id!r} has an endpoint absent from the selected NET graph"
            )
        union.union(first.key, second.key)
    if traces:
        missing = sorted({
            blocker
            for item in traces
            for blocker in item.electrical_blockers
        })
        diagnostics.append(EndpointViaDiagnostic(
            "warning",
            "TRACE_TOPOLOGY_IDEAL_RL_UNRESOLVED",
            "Source Trace records are ideal topological joins only; trace R/L is not "
            "included" + (f" ({', '.join(missing)})" if missing else ""),
        ))
    _artwork_component_joins(union, target_keys, target_artwork_components)

    layers = _source_stackup(graph)
    centers_m = _layer_center_depths_m(layers)
    vias = tuple(sorted(
        (item for item in graph.vias if item.net.casefold() == canonical_net.casefold()),
        key=lambda item: item.source_id.casefold(),
    ))
    branches: list[EndpointViaBranch] = []
    for via in vias:
        if not via.electrically_resolved:
            raise EndpointViaReducerError(
                f"Via {via.source_id!r} has unresolved source electrical evidence: "
                f"{', '.join(via.electrical_blockers) or 'unknown'}"
            )
        upper = nodes.get(via.upper_node_id.casefold())
        lower = nodes.get(via.lower_node_id.casefold())
        if upper is None or lower is None:
            raise EndpointViaReducerError(f"Via {via.source_id!r} has an unknown endpoint")
        positive, negative = union.find(upper.key), union.find(lower.key)
        if positive == negative:
            raise EndpointViaReducerError(
                f"Via {via.source_id!r} becomes a zero-length circuit after only source-proven "
                "Trace/artwork joins; the endpoint evidence is contradictory"
            )
        z0, z1 = centers_m.get(via.upper_layer.casefold()), centers_m.get(via.lower_layer.casefold())
        if z0 is None or z1 is None or not z1 > z0:
            raise EndpointViaReducerError(
                f"Via {via.source_id!r} has no increasing physical endpoint depths"
            )
        try:
            estimate = estimate_via_segment_rl(
                length_um=(z1 - z0) * 1.0e6,
                drill_diameter_um=via.padstack.drill_diameter_um,
                padstack_material=via.padstack.material,
                start_layer=via.upper_layer,
                end_layer=via.lower_layer,
                stackup_layers=layers,
            )
        except ViaModelError as exc:
            raise EndpointViaReducerError(
                f"Via {via.source_id!r} source R/L classification failed: {exc}"
            ) from exc
        conductivity = via.padstack.conductivity_s_m
        if conductivity is None or not isfinite(conductivity) or conductivity <= 0.0:
            raise EndpointViaReducerError(
                f"Via {via.source_id!r} PadStack conductivity is unresolved"
            )
        branches.append(EndpointViaBranch(
            via.source_id, via.padstack.name, upper.key, lower.key, positive, negative,
            upper.x_um * 1.0e-6, upper.y_um * 1.0e-6, z0, z1,
            float(via.padstack.drill_diameter_um), float(conductivity),
            estimate.classification.conductor_model, estimate.resistance_ohm,
            estimate.inductance_h,
        ))

    if not branches:
        raise EndpointViaReducerError(f"NET {canonical_net!r} has no retained source Via branches")
    external_reduced = tuple(sorted({union.find(key) for key in external_keys}))
    target_reduced = tuple(sorted({union.find(key) for key in target_keys}))
    if set(external_reduced) & set(target_reduced):
        raise EndpointViaReducerError(
            "source Trace/artwork evidence directly shorts an external Node to a target-plane Node"
        )
    diagnostics.append(EndpointViaDiagnostic(
        "info", "NO_POLYGON_CONTACT_CLAIM",
        "Only supplied target-artwork components were merged; no NET-wide polygon "
        "connectivity or trace R/L is claimed.",
    ))
    return EndpointViaTopology(
        canonical_net, str(graph.source_path), len(nodes), len(vias), len(traces), len(branches),
        tuple(item.source_id for item in traces), tuple(sorted({union.find(item.key) for item in nodes.values()})),
        external_reduced, target_reduced, tuple(branches), tuple(diagnostics),
    )


def compile_endpoint_via_mna(topology: EndpointViaTopology) -> EndpointViaMnaModel:
    """Compile source Via branches into passive MNA without branch flattening."""

    solid = tuple(item for item in topology.branches if item.conductor_model == SOLID_COPPER_FILLED_MICROVIA)
    hollow = tuple(item for item in topology.branches if item.conductor_model == HOLLOW_PLATED_BARREL)
    blocks: list[SeriesBranchBlock] = []
    if solid:
        try:
            blocks.append(filled_microvia_branch_block(tuple(
                FilledMicroviaBranch(
                    item.via_id, item.positive_reduced_node_id, item.negative_reduced_node_id,
                    item.x_m, item.y_m, item.z0_m, item.z1_m,
                    item.drill_diameter_um * 0.5e-6, item.conductivity_s_per_m,
                )
                for item in solid
            ), block_id=f"endpoint-solid:{topology.net}"))
        except Exception as exc:
            raise EndpointViaReducerError(
                "solid-microvia PEEC compilation failed; no uncoupled replacement was assumed"
            ) from exc
    if hollow:
        impedance = np.asarray(
            [[item.resistance_ohm + 0.0j if row == column else 0.0j
              for column, _ in enumerate(hollow)] for row, item in enumerate(hollow)],
            dtype=np.complex128,
        )
        inductance = np.asarray([item.inductance_h for item in hollow], dtype=np.float64)
        blocks.append(SeriesBranchBlock(
            tuple(item.via_id for item in hollow),
            tuple(item.positive_reduced_node_id for item in hollow),
            tuple(item.negative_reduced_node_id for item in hollow),
            lambda frequency: impedance + 1j * 2.0 * pi * float(frequency) * np.diag(inductance),
            f"endpoint-hollow-self:{topology.net}",
        ))
    ports = tuple(
        DifferentialPort(
            f"{topology.net}:external-{external_index:02d}:target-{target_index:02d}",
            external, target,
        )
        for external_index, external in enumerate(topology.external_node_ids)
        for target_index, target in enumerate(topology.target_node_ids)
    )
    if not ports:
        raise EndpointViaReducerError("endpoint topology has no external-to-target ports")
    # Keep isolated source Nodes in ``EndpointViaTopology`` for audit, but do
    # not stamp them into this port-reduction MNA.  A no-branch source record
    # is not an electrical path and GlobalMNA correctly rejects it as a
    # singular island.
    active_nodes = tuple(sorted({
        node
        for branch in topology.branches
        for node in (branch.positive_reduced_node_id, branch.negative_reduced_node_id)
    } | set(topology.external_node_ids) | set(topology.target_node_ids)))
    try:
        operator = compile_global_mna(
            active_nodes, branch_blocks=tuple(blocks), ports=ports,
        )
    except Exception as exc:
        raise EndpointViaReducerError("endpoint-aware MNA compilation failed") from exc
    return EndpointViaMnaModel(topology, operator, tuple(item.port_id for item in ports))


def solve_endpoint_via_impedance(
    model: EndpointViaMnaModel, frequencies_hz: Iterable[float],
) -> Mapping[float, np.ndarray]:
    """Return open-port Z matrices at requested frequencies, preserving port order."""

    result: dict[float, np.ndarray] = {}
    for raw_frequency in frequencies_hz:
        frequency = float(raw_frequency)
        if not isfinite(frequency) or frequency < 0.0:
            raise EndpointViaReducerError("frequencies_hz must contain finite values >= 0")
        result[frequency] = model.solve(frequency).impedance_ohm
    return MappingProxyType(result)


__all__ = [
    "EndpointViaBranch",
    "EndpointViaDiagnostic",
    "EndpointViaMnaModel",
    "EndpointViaReducerError",
    "EndpointViaTopology",
    "build_endpoint_via_topology",
    "compile_endpoint_via_mna",
    "solve_endpoint_via_impedance",
]
