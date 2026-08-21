"""Production-oriented reduction of source SPD conductor topology.

The raw SPD Node/Trace/Via graph is too large to stamp directly in a global
rail solve.  This module performs only reductions that have an explicit source
or geometry owner:

* same-layer Trace endpoints are joined as a disclosed
  ``topology_only_ideal`` approximation (Trace width is not required),
* Nodes assigned by the caller to the same ``(NET, layer, surface island)``
  are joined, and
* Vias are *never* joined by the reduction.  They remain finite branches with
  their source padstack, layer-span, material, and conductor evidence.

The result is deliberately independent of an electrical solver.  A later MNA
compiler can stamp the finite branches, while the certificate and owner ledger
make every reduction auditable and safe to cache.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from .conductor_graph import (
    ConductorNode,
    ConductorPadDef,
    ConductorPadStack,
    ConductorVia,
    SpdConductorGraph,
    SpdConductorGraphError,
    extract_spd_conductor_graph,
)


SurfaceIslandResolver = Callable[[str, str, str, float, float], str | None]
ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


_COMPILER_VERSION = "reduced-conductor/v1"
_TRACE_MODE = "topology_only_ideal"
_ALLOWED_SOURCE_ERROR_CODES = frozenset(
    {
        # Width and trace R evidence are intentionally outside this reduction:
        # a retained Trace is an explicit ideal topological union.
        "TRACE_WIDTH_INVALID",
        "TRACE_WIDTH_NONPOSITIVE",
        "TRACE_CONDUCTIVITY_UNRESOLVED",
    }
)


class ReducedConductorGraphError(ValueError):
    """Raised when a compact source-faithful graph cannot be certified."""


class ReducedConductorCompilationCancelled(RuntimeError):
    """Raised when the caller cancels a reduction."""


@dataclass(frozen=True, slots=True)
class ReducedConductorDiagnostic:
    severity: Literal["info", "warning", "error"]
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class SurfaceIslandContact:
    """One caller-proven contact between source Nodes and an artwork island."""

    layer: str
    island_id: str


@dataclass(frozen=True, slots=True)
class ReducedConductorNode:
    """One ideal-equivalence class of source Nodes on exactly one layer."""

    reduced_node_id: str
    net: str
    layer: str
    source_node_ids: tuple[str, ...]
    source_node_keys: tuple[str, ...]
    surface_contacts: tuple[SurfaceIslandContact, ...]


@dataclass(frozen=True, slots=True)
class IdealTraceUnion:
    """Audit record for a Trace consumed by an ideal DSU union."""

    source_trace_id: str
    net: str
    layer: str
    starting_source_node_id: str
    ending_source_node_id: str
    reduced_node_id: str
    mode: Literal["topology_only_ideal"] = _TRACE_MODE
    reason: str = "source Trace topology retained without inferred distributed R/L"


@dataclass(frozen=True, slots=True)
class ReducedFiniteViaBranch:
    """One deterministic aggregate of electrically identical source Vias.

    ``count`` source Vias are independent finite branches in parallel.  No R/L
    number is invented here; the complete geometry/material evidence required
    to calculate one is retained for the downstream electrical compiler.
    """

    branch_id: str
    net: str
    upper_reduced_node_id: str
    lower_reduced_node_id: str
    upper_layer: str
    lower_layer: str
    interval_layers: tuple[str, ...]
    upper_conductor_thickness_um: float
    lower_conductor_thickness_um: float
    upper_pad: ConductorPadDef
    lower_pad: ConductorPadDef
    padstack: ConductorPadStack
    count: int
    source_via_ids: tuple[str, ...]
    mode: Literal["finite_source_evidence"] = "finite_source_evidence"


@dataclass(frozen=True, slots=True)
class RequiredViaEndpointMapping:
    """Certified endpoint lookup for one caller-required source Via ID."""

    source_via_id: str
    net: str
    branch_id: str
    upper_reduced_node_id: str
    lower_reduced_node_id: str
    upper_layer: str
    lower_layer: str


@dataclass(frozen=True, slots=True)
class ReducedConductorCertificate:
    compiler_version: str
    source_compiler_version: str
    source_sha256: str
    source_topology_sha256: str
    topology_sha256: str
    source_size_bytes: int
    approximation_codes: tuple[str, ...]
    owner_ledger: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReducedConductorGraph:
    """Immutable compact topology plus identities needed by cache/MNA users."""

    source_path: Path
    requested_nets: tuple[str, ...]
    nodes: tuple[ReducedConductorNode, ...]
    ideal_trace_unions: tuple[IdealTraceUnion, ...]
    finite_via_branches: tuple[ReducedFiniteViaBranch, ...]
    required_via_endpoints: Mapping[str, RequiredViaEndpointMapping]
    diagnostics: tuple[ReducedConductorDiagnostic, ...]
    statistics: Mapping[str, int]
    certificate: ReducedConductorCertificate

    def required_via(self, source_via_id: str) -> RequiredViaEndpointMapping:
        """Return one required-Via mapping with a case-insensitive lookup."""

        key = str(source_via_id).strip().casefold()
        matches = tuple(
            value
            for source_id, value in self.required_via_endpoints.items()
            if source_id.casefold() == key
        )
        if len(matches) != 1:
            raise ReducedConductorGraphError(
                f"required Via ID {source_via_id!r} is not present in this certificate"
            )
        return matches[0]

    def require_integrity(self) -> None:
        """Recompute the canonical topology identity and fail on mutation."""

        actual = _compiled_topology_sha256(
            requested_nets=self.requested_nets,
            nodes=self.nodes,
            ideal_trace_unions=self.ideal_trace_unions,
            finite_via_branches=self.finite_via_branches,
            required_via_endpoints=self.required_via_endpoints,
            diagnostics=self.diagnostics,
            owner_ledger=self.certificate.owner_ledger,
            source_compiler_version=self.certificate.source_compiler_version,
            source_topology_sha256=self.certificate.source_topology_sha256,
        )
        if actual != self.certificate.topology_sha256:
            raise ReducedConductorGraphError(
                "reduced conductor topology no longer matches its certificate"
            )

    def require_source_integrity(self) -> None:
        """Verify that the current source bytes still match the certificate."""

        if not self.source_path.is_file():
            raise ReducedConductorGraphError(
                f"certified SPD source is no longer a file: {self.source_path}"
            )
        if self.source_path.stat().st_size != self.certificate.source_size_bytes:
            raise ReducedConductorGraphError(
                "certified SPD source size changed after conductor reduction"
            )
        if _file_sha256(self.source_path) != self.certificate.source_sha256:
            raise ReducedConductorGraphError(
                "certified SPD source bytes changed after conductor reduction"
            )


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._rank = [0] * size

    def find(self, value: int) -> int:
        while self._parent[value] != value:
            self._parent[value] = self._parent[self._parent[value]]
            value = self._parent[value]
        return value

    def union(self, first: int, second: int) -> None:
        left, right = self.find(first), self.find(second)
        if left == right:
            return
        if self._rank[left] < self._rank[right]:
            left, right = right, left
        self._parent[right] = left
        if self._rank[left] == self._rank[right]:
            self._rank[left] += 1


class _Reporter:
    def __init__(
        self,
        progress: ProgressCallback | None,
        is_cancelled: CancelCallback | None,
    ) -> None:
        self._progress = progress
        self._is_cancelled = is_cancelled
        self._last_value = -1

    def check(self) -> None:
        if self._is_cancelled is not None and self._is_cancelled():
            raise ReducedConductorCompilationCancelled(
                "reduced conductor compilation cancelled"
            )

    def emit(self, value: int, message: str) -> None:
        self.check()
        bounded = max(0, min(100, int(value)))
        if bounded < self._last_value:
            raise ReducedConductorGraphError("internal progress value regressed")
        if bounded != self._last_value and self._progress is not None:
            self._progress(bounded, message)
        self._last_value = bounded

    def loop(
        self,
        *,
        completed: int,
        total: int,
        low: int,
        high: int,
        message: str,
    ) -> None:
        self.check()
        if total <= 0:
            return
        value = low + int((high - low) * completed / total)
        self.emit(value, message)


def _canonical(value: object) -> object:
    if is_dataclass(value):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(value[key])
            for key in sorted(value, key=lambda item: str(item).casefold())
        }
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _sha256_payload(payload: object) -> str:
    encoded = json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return sha256(encoded.encode("ascii")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_required_via_ids(values: Iterable[str] | None) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        value = str(raw).strip()
        if not value:
            raise ReducedConductorGraphError("required Via IDs must not be blank")
        key = value.casefold()
        if key not in seen:
            result.append(value)
            seen.add(key)
    return tuple(sorted(result, key=str.casefold))


def _owner_key(kind: str, net: str, source_id: str) -> str:
    return f"{kind}:{net.casefold()}:{source_id.casefold()}"


def _island_owner_key(net: str, layer: str, island_id: str) -> str:
    # JSON length/escaping rules avoid delimiter ambiguity in source names.
    return "surface-island:" + json.dumps(
        [net.casefold(), layer.casefold(), island_id],
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _reduced_node_id(net: str, layer: str, source_keys: tuple[str, ...]) -> str:
    identity = _sha256_payload(
        {"net": net.casefold(), "layer": layer.casefold(), "nodes": source_keys}
    )
    return f"rcn-{identity[:24]}"


def _via_evidence_payload(via: ConductorVia) -> dict[str, object]:
    return {
        "net": via.net,
        "upper_layer": via.upper_layer,
        "lower_layer": via.lower_layer,
        "upper_conductor_thickness_um": via.upper_conductor_thickness_um,
        "lower_conductor_thickness_um": via.lower_conductor_thickness_um,
        "interval_layers": via.interval_layers,
        "upper_pad": via.upper_pad,
        "lower_pad": via.lower_pad,
        "padstack": via.padstack,
        "electrically_resolved": via.electrically_resolved,
        "electrical_blockers": via.electrical_blockers,
    }


def _branch_id(
    *,
    net: str,
    upper_reduced_node_id: str,
    lower_reduced_node_id: str,
    via: ConductorVia,
) -> str:
    identity = _sha256_payload(
        {
            "net": net.casefold(),
            "upper": upper_reduced_node_id,
            "lower": lower_reduced_node_id,
            "padstack": via.padstack.name.casefold(),
            "span": [item.casefold() for item in via.interval_layers],
            "evidence": _via_evidence_payload(via),
        }
    )
    return f"rcv-{identity[:24]}"


def _compiled_topology_sha256(
    *,
    requested_nets: tuple[str, ...],
    nodes: tuple[ReducedConductorNode, ...],
    ideal_trace_unions: tuple[IdealTraceUnion, ...],
    finite_via_branches: tuple[ReducedFiniteViaBranch, ...],
    required_via_endpoints: Mapping[str, RequiredViaEndpointMapping],
    diagnostics: tuple[ReducedConductorDiagnostic, ...],
    owner_ledger: Mapping[str, str],
    source_compiler_version: str,
    source_topology_sha256: str,
) -> str:
    return _sha256_payload(
        {
            "compiler_version": _COMPILER_VERSION,
            "source_compiler_version": source_compiler_version,
            "source_topology_sha256": source_topology_sha256,
            "requested_nets": sorted(requested_nets, key=str.casefold),
            "trace_mode": _TRACE_MODE,
            "nodes": nodes,
            "ideal_trace_unions": ideal_trace_unions,
            "finite_via_branches": finite_via_branches,
            "required_via_endpoints": required_via_endpoints,
            "diagnostics": sorted(
                diagnostics,
                key=lambda item: (item.severity, item.code, item.message),
            ),
            "owner_ledger": owner_ledger,
        }
    )


def _validate_source_diagnostics(graph: SpdConductorGraph) -> None:
    diagnostics = graph.diagnostics
    blocking = tuple(
        item
        for item in diagnostics
        if item.severity == "error" and item.code not in _ALLOWED_SOURCE_ERROR_CODES
    )
    if blocking:
        summary = "; ".join(
            f"{item.code}: {item.message}" for item in blocking[:8]
        )
        if len(blocking) > 8:
            summary += f"; +{len(blocking) - 8} more"
        raise ReducedConductorGraphError(
            "source conductor evidence is incomplete or ambiguous: " + summary
        )


def compile_reduced_conductor_graph(
    path: str | Path,
    *,
    requested_nets: Iterable[str],
    surface_island_for_node: SurfaceIslandResolver,
    required_via_ids: Iterable[str] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> ReducedConductorGraph:
    """Compile a compact, source-owned conductor graph.

    ``surface_island_for_node`` is called once for every retained source Node
    as ``(net, layer, node_id, x_um, y_um)``.  A non-empty string proves contact
    to that island; ``None`` leaves the Node unassigned and does not guess a
    contact.  Island identity is scoped by NET and layer.

    Required Via IDs are matched case-insensitively but must identify exactly
    one complete Via across all requested NETs.  The function raises instead
    of returning a partial mapping when an ID is missing, ambiguous, or lacks
    finite source evidence.
    """

    if not callable(surface_island_for_node):
        raise ReducedConductorGraphError("surface_island_for_node must be callable")
    required_ids = _normalise_required_via_ids(required_via_ids)
    reporter = _Reporter(progress, is_cancelled)
    reporter.emit(0, "Starting source conductor reduction")
    source_path = Path(path)
    if not source_path.is_file():
        raise ReducedConductorGraphError(f"SPD source is not a file: {source_path}")
    source_stat = source_path.stat()
    reporter.emit(3, "Parsing requested source Node/Trace/Via evidence")
    try:
        source_graph = extract_spd_conductor_graph(
            source_path,
            net_names=tuple(requested_nets),
        )
    except SpdConductorGraphError as exc:
        raise ReducedConductorGraphError(str(exc)) from exc
    reporter.check()
    source_certificate = source_graph.require_certificate()
    _validate_source_diagnostics(source_graph)
    reporter.emit(30, "Source conductor evidence parsed")

    source_nodes = source_graph.nodes
    node_index = {node.key: index for index, node in enumerate(source_nodes)}
    if len(node_index) != len(source_nodes):
        raise ReducedConductorGraphError(
            "source Node identity is ambiguous after conductor extraction"
        )
    dsu = _DisjointSet(len(source_nodes))
    island_by_node_key: dict[str, str] = {}
    first_node_by_island: dict[tuple[str, str, str], int] = {}
    total_nodes = len(source_nodes)
    for completed, node in enumerate(source_nodes, start=1):
        reporter.check()
        try:
            island_raw = surface_island_for_node(
                node.net,
                node.layer,
                node.source_id,
                node.x_um,
                node.y_um,
            )
        except Exception as exc:
            raise ReducedConductorGraphError(
                f"surface-island resolver failed for Node {node.source_id!r} "
                f"on {node.net!r}/{node.layer!r}: {exc}"
            ) from exc
        if island_raw is not None:
            if not isinstance(island_raw, str) or not island_raw.strip():
                raise ReducedConductorGraphError(
                    f"surface-island resolver returned a non-empty-string violation "
                    f"for Node {node.source_id!r}: {island_raw!r}"
                )
            island_id = island_raw.strip()
            island_by_node_key[node.key] = island_id
            island_key = (node.net.casefold(), node.layer.casefold(), island_id)
            previous = first_node_by_island.setdefault(
                island_key, node_index[node.key]
            )
            dsu.union(previous, node_index[node.key])
        reporter.loop(
            completed=completed,
            total=total_nodes,
            low=30,
            high=48,
            message="Resolving source Node contacts to surface islands",
        )

    source_node_by_identity = {
        (node.net.casefold(), node.source_id.casefold()): node
        for node in source_nodes
    }
    total_traces = len(source_graph.traces)
    for completed, trace in enumerate(source_graph.traces, start=1):
        reporter.check()
        first = source_node_by_identity.get(
            (trace.net.casefold(), trace.starting_node_id.casefold())
        )
        second = source_node_by_identity.get(
            (trace.net.casefold(), trace.ending_node_id.casefold())
        )
        if first is None or second is None:
            raise ReducedConductorGraphError(
                f"Trace {trace.source_id!r} has an unresolved source endpoint"
            )
        if first.layer.casefold() != second.layer.casefold():
            raise ReducedConductorGraphError(
                f"Trace {trace.source_id!r} crosses physical layers; ideal union refused"
            )
        dsu.union(node_index[first.key], node_index[second.key])
        reporter.loop(
            completed=completed,
            total=total_traces,
            low=48,
            high=58,
            message="Applying disclosed topology-only Trace unions",
        )
    reporter.emit(58, "Ideal conductor equivalence classes resolved")

    grouped_source_nodes: dict[int, list[ConductorNode]] = defaultdict(list)
    for index, node in enumerate(source_nodes):
        reporter.check()
        grouped_source_nodes[dsu.find(index)].append(node)

    reduced_nodes: list[ReducedConductorNode] = []
    reduced_id_by_source_key: dict[str, str] = {}
    for members in grouped_source_nodes.values():
        ordered = tuple(sorted(members, key=lambda item: item.key))
        net_keys = {item.net.casefold() for item in ordered}
        layer_keys = {item.layer.casefold() for item in ordered}
        if len(net_keys) != 1 or len(layer_keys) != 1:
            raise ReducedConductorGraphError(
                "an ideal conductor union crossed NET or physical-layer ownership"
            )
        source_keys = tuple(item.key for item in ordered)
        reduced_id = _reduced_node_id(ordered[0].net, ordered[0].layer, source_keys)
        contacts = tuple(
            sorted(
                {
                    SurfaceIslandContact(item.layer, island_by_node_key[item.key])
                    for item in ordered
                    if item.key in island_by_node_key
                },
                key=lambda item: (item.layer.casefold(), item.island_id),
            )
        )
        reduced_nodes.append(
            ReducedConductorNode(
                reduced_node_id=reduced_id,
                net=ordered[0].net,
                layer=ordered[0].layer,
                source_node_ids=tuple(item.source_id for item in ordered),
                source_node_keys=source_keys,
                surface_contacts=contacts,
            )
        )
        for item in ordered:
            reduced_id_by_source_key[item.key] = reduced_id
    reduced_nodes.sort(key=lambda item: item.reduced_node_id)
    if len({item.reduced_node_id for item in reduced_nodes}) != len(reduced_nodes):
        raise ReducedConductorGraphError("reduced Node hash collision")

    ideal_unions: list[IdealTraceUnion] = []
    for trace in source_graph.traces:
        first = source_node_by_identity[
            (trace.net.casefold(), trace.starting_node_id.casefold())
        ]
        second = source_node_by_identity[
            (trace.net.casefold(), trace.ending_node_id.casefold())
        ]
        reduced_id = reduced_id_by_source_key[first.key]
        if reduced_id != reduced_id_by_source_key[second.key]:
            raise ReducedConductorGraphError(
                f"Trace {trace.source_id!r} endpoints were not reduced together"
            )
        ideal_unions.append(
            IdealTraceUnion(
                source_trace_id=trace.source_id,
                net=trace.net,
                layer=trace.layer,
                starting_source_node_id=trace.starting_node_id,
                ending_source_node_id=trace.ending_node_id,
                reduced_node_id=reduced_id,
            )
        )
    ideal_unions.sort(
        key=lambda item: (item.net.casefold(), item.source_trace_id.casefold())
    )

    via_groups: dict[tuple[object, ...], list[ConductorVia]] = defaultdict(list)
    total_vias = len(source_graph.vias)
    via_endpoint_ids: dict[tuple[str, str], tuple[str, str]] = {}
    for completed, via in enumerate(source_graph.vias, start=1):
        reporter.check()
        if not via.electrically_resolved:
            blockers = ", ".join(via.electrical_blockers) or "unknown evidence"
            raise ReducedConductorGraphError(
                f"Via {via.source_id!r} lacks complete finite-branch evidence: {blockers}"
            )
        upper = source_node_by_identity.get(
            (via.net.casefold(), via.upper_node_id.casefold())
        )
        lower = source_node_by_identity.get(
            (via.net.casefold(), via.lower_node_id.casefold())
        )
        if upper is None or lower is None:
            raise ReducedConductorGraphError(
                f"Via {via.source_id!r} has an unresolved source endpoint"
            )
        upper_reduced = reduced_id_by_source_key[upper.key]
        lower_reduced = reduced_id_by_source_key[lower.key]
        if upper_reduced == lower_reduced:
            raise ReducedConductorGraphError(
                f"Via {via.source_id!r} was shorted by a non-Via ideal union"
            )
        key = (
            via.net.casefold(),
            upper_reduced,
            lower_reduced,
            via.padstack.name.casefold(),
            tuple(item.casefold() for item in via.interval_layers),
        )
        via_groups[key].append(via)
        via_endpoint_ids[(via.net.casefold(), via.source_id.casefold())] = (
            upper_reduced,
            lower_reduced,
        )
        reporter.loop(
            completed=completed,
            total=total_vias,
            low=58,
            high=74,
            message="Retaining finite source Via branches",
        )

    finite_branches: list[ReducedFiniteViaBranch] = []
    branch_id_by_source_via: dict[tuple[str, str], str] = {}
    total_groups = len(via_groups)
    for completed, (_, grouped_vias) in enumerate(
        sorted(via_groups.items(), key=lambda item: repr(item[0])), start=1
    ):
        reporter.check()
        vias = tuple(
            sorted(grouped_vias, key=lambda item: item.source_id.casefold())
        )
        exemplar = vias[0]
        expected_evidence = _via_evidence_payload(exemplar)
        if any(_via_evidence_payload(item) != expected_evidence for item in vias[1:]):
            raise ReducedConductorGraphError(
                "Vias sharing reduced endpoints/padstack/span have conflicting "
                "finite source evidence"
            )
        upper_reduced, lower_reduced = via_endpoint_ids[
            (exemplar.net.casefold(), exemplar.source_id.casefold())
        ]
        branch_id = _branch_id(
            net=exemplar.net,
            upper_reduced_node_id=upper_reduced,
            lower_reduced_node_id=lower_reduced,
            via=exemplar,
        )
        if any(item.branch_id == branch_id for item in finite_branches):
            raise ReducedConductorGraphError("finite Via branch hash collision")
        branch = ReducedFiniteViaBranch(
            branch_id=branch_id,
            net=exemplar.net,
            upper_reduced_node_id=upper_reduced,
            lower_reduced_node_id=lower_reduced,
            upper_layer=exemplar.upper_layer,
            lower_layer=exemplar.lower_layer,
            interval_layers=exemplar.interval_layers,
            upper_conductor_thickness_um=float(
                exemplar.upper_conductor_thickness_um
            ),
            lower_conductor_thickness_um=float(
                exemplar.lower_conductor_thickness_um
            ),
            upper_pad=exemplar.upper_pad,
            lower_pad=exemplar.lower_pad,
            padstack=exemplar.padstack,
            count=len(vias),
            source_via_ids=tuple(item.source_id for item in vias),
        )
        finite_branches.append(branch)
        for item in vias:
            branch_id_by_source_via[
                (item.net.casefold(), item.source_id.casefold())
            ] = branch_id
        reporter.loop(
            completed=completed,
            total=total_groups,
            low=74,
            high=84,
            message="Aggregating equivalent finite Via branches",
        )
    finite_branches.sort(key=lambda item: item.branch_id)

    vias_by_unqualified_id: dict[str, list[ConductorVia]] = defaultdict(list)
    for via in source_graph.vias:
        vias_by_unqualified_id[via.source_id.casefold()].append(via)
    required_mappings: dict[str, RequiredViaEndpointMapping] = {}
    required_failures: list[str] = []
    for required_id in required_ids:
        reporter.check()
        matches = vias_by_unqualified_id.get(required_id.casefold(), [])
        if not matches:
            required_failures.append(f"{required_id!r}: missing or rejected")
            continue
        if len(matches) != 1:
            owners = ", ".join(
                sorted(f"{item.source_id}::{item.net}" for item in matches)
            )
            required_failures.append(f"{required_id!r}: ambiguous ({owners})")
            continue
        via = matches[0]
        if not via.electrically_resolved:
            required_failures.append(
                f"{required_id!r}: incomplete finite evidence "
                f"({', '.join(via.electrical_blockers)})"
            )
            continue
        source_key = (via.net.casefold(), via.source_id.casefold())
        endpoints = via_endpoint_ids.get(source_key)
        branch_id = branch_id_by_source_via.get(source_key)
        if endpoints is None or branch_id is None:
            required_failures.append(
                f"{required_id!r}: no certified reduced endpoints"
            )
            continue
        required_mappings[via.source_id] = RequiredViaEndpointMapping(
            source_via_id=via.source_id,
            net=via.net,
            branch_id=branch_id,
            upper_reduced_node_id=endpoints[0],
            lower_reduced_node_id=endpoints[1],
            upper_layer=via.upper_layer,
            lower_layer=via.lower_layer,
        )
    if required_failures:
        raise ReducedConductorGraphError(
            "required Via endpoint certification failed: "
            + "; ".join(required_failures)
        )
    reporter.emit(88, "Required Via endpoint ownership certified")

    owner_ledger: dict[str, str] = {}

    def assign_owner(owner: str, target: str) -> None:
        previous = owner_ledger.setdefault(owner, target)
        if previous != target:
            raise ReducedConductorGraphError(
                f"owner {owner!r} maps to conflicting reduced entities"
            )

    for node in source_nodes:
        assign_owner(
            _owner_key("node", node.net, node.source_id),
            reduced_id_by_source_key[node.key],
        )
        island_id = island_by_node_key.get(node.key)
        if island_id is not None:
            assign_owner(
                _island_owner_key(node.net, node.layer, island_id),
                reduced_id_by_source_key[node.key],
            )
    for trace in source_graph.traces:
        first = source_node_by_identity[
            (trace.net.casefold(), trace.starting_node_id.casefold())
        ]
        assign_owner(
            _owner_key("trace", trace.net, trace.source_id),
            reduced_id_by_source_key[first.key],
        )
    for via in source_graph.vias:
        assign_owner(
            _owner_key("via", via.net, via.source_id),
            branch_id_by_source_via[(via.net.casefold(), via.source_id.casefold())],
        )

    diagnostics: list[ReducedConductorDiagnostic] = []
    if ideal_unions:
        diagnostics.append(
            ReducedConductorDiagnostic(
                "warning",
                "TRACE_TOPOLOGY_ONLY_IDEAL",
                f"{len(ideal_unions)} source Trace record(s) were reduced as ideal "
                "topology; no Trace R/L was inferred.",
            )
        )
    unassigned_nodes = sum(
        node.key not in island_by_node_key for node in source_nodes
    )
    if unassigned_nodes:
        diagnostics.append(
            ReducedConductorDiagnostic(
                "info",
                "SOURCE_NODE_WITHOUT_SURFACE_ISLAND",
                f"{unassigned_nodes} source Node(s) have no caller-proven surface "
                "island contact and remain explicit conductor nodes.",
            )
        )
    ignored_upstream_codes = {
        "POLYGON_CONNECTIVITY_NOT_INCLUDED",
        "TRACE_WIDTH_MISSING",
        "TRACE_WIDTH_INVALID",
        "TRACE_WIDTH_NONPOSITIVE",
        "TRACE_CONDUCTIVITY_UNRESOLVED",
    }
    for item in source_graph.diagnostics:
        if item.code not in ignored_upstream_codes:
            diagnostics.append(
                ReducedConductorDiagnostic(item.severity, item.code, item.message)
            )
    diagnostic_tuple = tuple(
        sorted(diagnostics, key=lambda item: (item.severity, item.code, item.message))
    )
    frozen_required = MappingProxyType(
        dict(sorted(required_mappings.items(), key=lambda item: item[0].casefold()))
    )
    frozen_owners = MappingProxyType(
        dict(sorted(owner_ledger.items(), key=lambda item: item[0]))
    )
    node_tuple = tuple(reduced_nodes)
    trace_tuple = tuple(ideal_unions)
    branch_tuple = tuple(finite_branches)
    topology_sha256 = _compiled_topology_sha256(
        requested_nets=source_graph.requested_nets,
        nodes=node_tuple,
        ideal_trace_unions=trace_tuple,
        finite_via_branches=branch_tuple,
        required_via_endpoints=frozen_required,
        diagnostics=diagnostic_tuple,
        owner_ledger=frozen_owners,
        source_compiler_version=source_certificate.compiler_version,
        source_topology_sha256=source_certificate.topology_sha256,
    )
    certificate = ReducedConductorCertificate(
        compiler_version=_COMPILER_VERSION,
        source_compiler_version=source_certificate.compiler_version,
        source_sha256=source_certificate.source_sha256,
        source_topology_sha256=source_certificate.topology_sha256,
        topology_sha256=topology_sha256,
        source_size_bytes=source_certificate.source_size_bytes,
        approximation_codes=("TRACE_TOPOLOGY_ONLY_IDEAL",) if ideal_unions else (),
        owner_ledger=frozen_owners,
    )
    statistics = MappingProxyType(
        {
            "source_nodes": len(source_nodes),
            "reduced_nodes": len(node_tuple),
            "surface_contacts": len(island_by_node_key),
            "surface_islands": len(first_node_by_island),
            "unassigned_source_nodes": unassigned_nodes,
            "ideal_trace_unions": len(trace_tuple),
            "source_vias": len(source_graph.vias),
            "finite_via_branches": len(branch_tuple),
            "aggregated_source_vias": len(source_graph.vias) - len(branch_tuple),
            "required_vias": len(frozen_required),
        }
    )
    source_stat_after = source_path.stat()
    if (
        source_stat_after.st_size != source_stat.st_size
        or source_stat_after.st_mtime_ns != source_stat.st_mtime_ns
    ):
        raise ReducedConductorGraphError(
            "SPD source changed while reduced conductor topology was compiled"
        )
    result = ReducedConductorGraph(
        source_path=source_path.resolve(),
        requested_nets=source_graph.requested_nets,
        nodes=node_tuple,
        ideal_trace_unions=trace_tuple,
        finite_via_branches=branch_tuple,
        required_via_endpoints=frozen_required,
        diagnostics=diagnostic_tuple,
        statistics=statistics,
        certificate=certificate,
    )
    result.require_integrity()
    reporter.emit(100, "Reduced conductor topology certified")
    return result


__all__ = [
    "CancelCallback",
    "IdealTraceUnion",
    "ProgressCallback",
    "ReducedConductorCertificate",
    "ReducedConductorCompilationCancelled",
    "ReducedConductorDiagnostic",
    "ReducedConductorGraph",
    "ReducedConductorGraphError",
    "ReducedConductorNode",
    "ReducedFiniteViaBranch",
    "RequiredViaEndpointMapping",
    "SurfaceIslandContact",
    "SurfaceIslandResolver",
    "compile_reduced_conductor_graph",
]
