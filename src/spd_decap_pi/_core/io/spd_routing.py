"""Bounded logical-Trace extraction for signal-routing avoidance research.

This parser is intentionally separate from the historical ``_TRACE_RE`` used
by source-via graph recovery.  A PowerSI Trace is a logical record that may
continue on one or more ``+`` lines, so Width cannot be recovered correctly by
matching only its first physical line.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import mmap
from math import isfinite
import re

from ...routing_obstacles import (
    RoutingLayerCompleteness,
    RoutingNetRole,
    RoutingObjectProvenance,
    RoutingTraceSegment,
    TraceWidthSource,
)


CheckCallback = Callable[[], None]

_TRACE_HEADER_RE = re.compile(
    rb"^(Trace[^\r\n:\s]*)::([^\s]+)\s+"
    rb"(?:(Thermal)\s+)?StartingNode\s*=\s*(\S+)\s+"
    rb"EndingNode\s*=\s*(\S+)(.*)$",
    re.IGNORECASE,
)
_LENGTH_TOKEN_RE = re.compile(
    rb"([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)"
    rb"\s*(mil|mm|um|u|m)(?![A-Za-z])",
    re.IGNORECASE,
)
_WIDTH_RE = re.compile(
    rb"\bWidth\s*=\s*"
    rb"([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?)"
    rb"\s*(mil|mm|um|u|m)(?![A-Za-z])",
    re.IGNORECASE,
)
_NODE_X_RE = re.compile(rb"\bX\s*=\s*(\S+)", re.IGNORECASE)
_NODE_Y_RE = re.compile(rb"\bY\s*=\s*(\S+)", re.IGNORECASE)
_NODE_LAYER_RE = re.compile(rb"\bLayer\s*=\s*(\S+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SpdRoutingExtraction:
    segments: tuple[RoutingTraceSegment, ...]
    layer_completeness: tuple[RoutingLayerCompleteness, ...]
    net_roles: tuple[tuple[str, RoutingNetRole], ...]
    statistics: Mapping[str, int]
    compiler_policy: str = "WIDTHED_SIGNAL_TRACE_PROXY_V1"
    production_ready: bool = False


@dataclass(frozen=True, slots=True)
class _NodeReference:
    raw: str
    node_id: str
    qualifier: str | None
    net: str | None

    @property
    def exact_key(self) -> tuple[str, str]:
        return self.raw.casefold(), (self.net or "").casefold()

    @property
    def base_key(self) -> tuple[str, str]:
        return self.node_id.casefold(), (self.net or "").casefold()


@dataclass(frozen=True, slots=True)
class _LogicalTrace:
    source_offset: int
    trace_id: str
    net: str
    starting_node: _NodeReference
    ending_node: _NodeReference
    thermal: bool
    width_um: float | None
    width_source: TraceWidthSource
    unresolved_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ResolvedNode:
    raw: str
    node_id: str
    net: str
    layer: str
    x_um: float
    y_um: float


def parse_spd_routing_net_roles(data: mmap.mmap) -> dict[str, RoutingNetRole]:
    """Parse all .NetList rows, including ordinary signal-section rows."""

    start = _find_line(data, b".NetList")
    if start < 0:
        return {}
    end = _find_line(data, b".EndNetList", start)
    if end < 0:
        end = len(data)
    roles: dict[str, RoutingNetRole] = {}
    active_role = RoutingNetRole.SIGNAL
    for _offset, raw in _iter_lines(data, start, end):
        stripped = raw.strip()
        if not stripped or stripped.startswith((b".", b"*", b"+")):
            continue
        token = stripped.split(None, 1)[0]
        folded_token = token.lower()
        if folded_token in {b"groundnets", b"powernets"} or token.startswith(b"::"):
            continue
        if b"->" in stripped:
            destination = (
                stripped.split(b"->", 1)[1].strip().split(None, 1)[0]
                .split(b"::", 1)[0]
                .lower()
            )
            if destination == b"groundnets":
                active_role = RoutingNetRole.GROUND
            elif destination == b"powernets":
                active_role = RoutingNetRole.POWER
        # "GroundNets Color = ..." and "PowerNets Color = ..." are style
        # properties, not group transitions or physical net identities.
        if folded_token in {b"groundnets", b"powernets"}:
            continue
        name = _decode(token.split(b"::", 1)[0])
        if not name:
            continue
        _merge_role(roles, name, active_role)
    return roles


def merge_spd_routing_net_roles(
    base: Mapping[str, RoutingNetRole],
    *,
    power_nets: Iterable[str] = (),
    ground_nets: Iterable[str] = (),
    signal_nets: Iterable[str] = (),
) -> dict[str, RoutingNetRole]:
    result = dict(base)
    for role, values in (
        (RoutingNetRole.POWER, power_nets),
        (RoutingNetRole.GROUND, ground_nets),
        (RoutingNetRole.SIGNAL, signal_nets),
    ):
        for name in values:
            if str(name).strip():
                _merge_role(result, str(name).strip(), role)
    return result


def extract_spd_routing_obstacles(
    data: mmap.mmap,
    *,
    trace_start: int,
    trace_end: int,
    node_start: int,
    node_end: int,
    conductor_layers: Sequence[str],
    net_roles: Mapping[str, RoutingNetRole],
    check: CheckCallback | None = None,
) -> SpdRoutingExtraction:
    """Compile width-resolved signal-like Trace records into compact evidence.

    Widthless possible-signal records remain fail-closed.  Their endpoints are
    still joined so incompleteness is localized to the source conductor layer;
    only a missing/ambiguous endpoint is conservatively reflected more broadly.
    """

    check = check or (lambda: None)
    canonical_roles = _canonical_roles(net_roles)
    role_by_key = {
        name.casefold(): role for name, role in canonical_roles.items()
    }
    interesting: list[tuple[_LogicalTrace, RoutingNetRole]] = []
    statistics: Counter[str] = Counter()
    for record in _iter_logical_trace_records(
        data, trace_start, trace_end, check=check
    ):
        statistics["trace_records"] += 1
        if record.thermal:
            statistics["thermal_records"] += 1
        if record.width_source == TraceWidthSource.INLINE:
            statistics["raw_inline_width_records"] += 1
        elif record.width_source == TraceWidthSource.CONTINUATION:
            statistics["raw_continuation_width_records"] += 1
        else:
            statistics["raw_unresolved_width_records"] += 1
        role = role_by_key.get(record.net.casefold(), RoutingNetRole.UNKNOWN)
        if role in {RoutingNetRole.POWER, RoutingNetRole.GROUND}:
            statistics["scope_excluded_power_ground"] += 1
            continue
        if role == RoutingNetRole.UNKNOWN:
            statistics["unknown_role_records"] += 1
        if record.width_um is None:
            statistics["missing_width_records"] += 1
            interesting.append((record, role))
            continue
        statistics["width_resolved_records"] += 1
        interesting.append((record, role))

    references = tuple(
        reference
        for record, _role in interesting
        for reference in (record.starting_node, record.ending_node)
        if reference.node_id
    )
    resolved = _resolve_referenced_nodes(
        data,
        node_start,
        node_end,
        references,
        check=check,
    )
    conductor_by_key = {item.casefold(): item for item in conductor_layers}
    layer_counts: dict[str, Counter[str]] = {
        layer.casefold(): Counter() for layer in conductor_layers
    }
    layer_codes: dict[str, set[str]] = defaultdict(set)
    layer_examples: dict[str, list[str]] = defaultdict(list)
    segments: list[RoutingTraceSegment] = []

    def mark_layer(layer: str | None, code: str, trace_id: str, field: str) -> None:
        if layer is None or layer.casefold() not in conductor_by_key:
            targets = tuple(conductor_by_key)
        else:
            targets = (layer.casefold(),)
        for key in targets:
            layer_counts[key][field] += 1
            layer_codes[key].add(code)
            examples = layer_examples[key]
            if len(examples) < 8 and trace_id not in examples:
                examples.append(trace_id)

    for index, (record, role) in enumerate(interesting):
        if index % 8192 == 0:
            check()
        if {
            "TRACE_HEADER_UNSUPPORTED",
            "TRACE_CONTINUATION_ORPHANED",
        }.intersection(record.unresolved_codes):
            statistics["malformed_trace_records"] += 1
            statistics["unsupported_logical_records"] += 1
            for code in record.unresolved_codes:
                mark_layer(
                    None,
                    code,
                    record.trace_id,
                    "unresolved_provenance_count",
                )
            continue
        start_node = _resolved_for_reference(resolved, record.starting_node, record.net)
        end_node = _resolved_for_reference(resolved, record.ending_node, record.net)
        if start_node is None or end_node is None:
            statistics["unresolved_endpoint_records"] += 1
            mark_layer(
                None,
                "TRACE_ENDPOINT_UNRESOLVED",
                record.trace_id,
                "unresolved_endpoint_count",
            )
            continue
        if (
            start_node.net.casefold() != record.net.casefold()
            or end_node.net.casefold() != record.net.casefold()
        ):
            statistics["endpoint_net_mismatch_records"] += 1
            mark_layer(
                start_node.layer,
                "TRACE_ENDPOINT_NET_MISMATCH",
                record.trace_id,
                "unresolved_endpoint_count",
            )
            mark_layer(
                end_node.layer,
                "TRACE_ENDPOINT_NET_MISMATCH",
                record.trace_id,
                "unresolved_endpoint_count",
            )
            continue
        if start_node.layer.casefold() != end_node.layer.casefold():
            statistics["cross_layer_records"] += 1
            mark_layer(
                start_node.layer,
                "TRACE_CROSS_LAYER_ENDPOINTS",
                record.trace_id,
                "unresolved_endpoint_count",
            )
            mark_layer(
                end_node.layer,
                "TRACE_CROSS_LAYER_ENDPOINTS",
                record.trace_id,
                "unresolved_endpoint_count",
            )
            continue
        layer = conductor_by_key.get(start_node.layer.casefold())
        if layer is None:
            statistics["nonconductor_endpoint_records"] += 1
            mark_layer(
                None,
                "TRACE_LAYER_UNRESOLVED",
                record.trace_id,
                "unresolved_endpoint_count",
            )
            continue
        if record.width_um is None:
            mark_layer(
                layer,
                "TRACE_WIDTH_UNRESOLVED",
                record.trace_id,
                "unresolved_width_count",
            )
            for code in record.unresolved_codes:
                mark_layer(
                    layer,
                    code,
                    record.trace_id,
                    "unresolved_provenance_count",
                )
            if role == RoutingNetRole.UNKNOWN:
                mark_layer(
                    layer,
                    "TRACE_NET_ROLE_UNRESOLVED",
                    record.trace_id,
                    "unresolved_role_count",
                )
            continue
        if record.unresolved_codes:
            statistics["unsupported_logical_records"] += 1
            for code in record.unresolved_codes:
                mark_layer(
                    layer,
                    code,
                    record.trace_id,
                    "unresolved_provenance_count",
                )
            continue
        provenance = (
            RoutingObjectProvenance.PHYSICAL_ROUTING
            if role == RoutingNetRole.SIGNAL
            else RoutingObjectProvenance.UNRESOLVED
        )
        if role == RoutingNetRole.UNKNOWN:
            mark_layer(
                layer,
                "TRACE_NET_ROLE_UNRESOLVED",
                record.trace_id,
                "unresolved_role_count",
            )
            mark_layer(
                layer,
                "TRACE_PHYSICAL_PROVENANCE_UNRESOLVED",
                record.trace_id,
                "unresolved_provenance_count",
            )
        segments.append(
            RoutingTraceSegment(
                trace_id=record.trace_id,
                net=record.net,
                layer=layer,
                x1_um=start_node.x_um,
                y1_um=start_node.y_um,
                x2_um=end_node.x_um,
                y2_um=end_node.y_um,
                width_um=float(record.width_um),
                width_source=record.width_source,
                net_role=role,
                provenance=provenance,
                source_offset=record.source_offset,
                thermal=record.thermal,
            )
        )
        layer_counts[layer.casefold()]["resolved_segments"] += 1

    completeness = tuple(
        RoutingLayerCompleteness(
            layer=layer,
            parse_complete=True,
            unresolved_width_count=layer_counts[layer.casefold()][
                "unresolved_width_count"
            ],
            unresolved_role_count=layer_counts[layer.casefold()][
                "unresolved_role_count"
            ],
            unresolved_provenance_count=layer_counts[layer.casefold()][
                "unresolved_provenance_count"
            ],
            unresolved_endpoint_count=layer_counts[layer.casefold()][
                "unresolved_endpoint_count"
            ],
            unresolved_codes=tuple(sorted(layer_codes[layer.casefold()])),
            unresolved_examples=tuple(layer_examples[layer.casefold()]),
        )
        for layer in conductor_layers
    )
    statistics["retained_segments"] = len(segments)
    statistics["node_references"] = len(references)
    statistics["resolved_node_identities"] = len(resolved.exact)
    return SpdRoutingExtraction(
        segments=tuple(
            sorted(
                segments,
                key=lambda item: (
                    conductor_layers.index(item.layer),
                    item.trace_id.casefold(),
                    item.source_offset,
                ),
            )
        ),
        layer_completeness=completeness,
        net_roles=tuple(
            sorted(
                canonical_roles.items(),
                key=lambda item: item[0].casefold(),
            )
        ),
        statistics=dict(statistics),
    )


def _iter_logical_trace_records(
    data: mmap.mmap,
    start: int,
    end: int,
    *,
    check: CheckCallback,
):
    active: dict[str, object] | None = None

    def flush() -> _LogicalTrace | None:
        nonlocal active
        if active is None:
            return None
        inline = active["inline_width"]
        continuation = active["continuation_widths"]
        unresolved = list(active["unresolved_codes"])
        width: float | None
        source: TraceWidthSource
        if inline is not None:
            width = float(inline)
            source = TraceWidthSource.INLINE
            if continuation and any(abs(float(value) - width) > 1.0e-9 for value in continuation):
                unresolved.append("CONFLICTING_TRACE_WIDTH")
                width = None
                source = TraceWidthSource.UNRESOLVED
        elif continuation:
            first = float(continuation[0])
            if any(abs(float(value) - first) > 1.0e-9 for value in continuation[1:]):
                unresolved.append("CONFLICTING_TRACE_WIDTH")
                width = None
                source = TraceWidthSource.UNRESOLVED
            else:
                width = first
                source = TraceWidthSource.CONTINUATION
        else:
            width = None
            source = TraceWidthSource.UNRESOLVED
        if width is not None and (not isfinite(width) or width <= 0):
            unresolved.append("INVALID_TRACE_WIDTH")
            width = None
            source = TraceWidthSource.UNRESOLVED
        result = _LogicalTrace(
            source_offset=int(active["source_offset"]),
            trace_id=str(active["trace_id"]),
            net=str(active["net"]),
            starting_node=active["starting_node"],  # type: ignore[arg-type]
            ending_node=active["ending_node"],  # type: ignore[arg-type]
            thermal=bool(active["thermal"]),
            width_um=width,
            width_source=source,
            unresolved_codes=tuple(sorted(set(str(item) for item in unresolved))),
        )
        active = None
        return result

    for line_index, (offset, raw) in enumerate(_iter_lines(data, start, end)):
        if line_index % 8192 == 0:
            check()
        stripped = raw.strip()
        match = _TRACE_HEADER_RE.match(stripped)
        if match is not None:
            previous = flush()
            if previous is not None:
                yield previous
            net = _decode(match.group(2))
            inline_tail = match.group(6) or b""
            inline_matches = tuple(_WIDTH_RE.finditer(inline_tail))
            inline_match = inline_matches[0] if inline_matches else None
            inline_width: float | None = None
            unresolved: list[str] = []
            if inline_match is not None:
                try:
                    inline_width = _length_um(inline_match.group(0).split(b"=", 1)[1])
                except ValueError:
                    unresolved.append("INVALID_TRACE_WIDTH")
            if len(inline_matches) > 1:
                unresolved.append("CONFLICTING_TRACE_WIDTH")
            unsupported_inline = _WIDTH_RE.sub(b"", inline_tail).strip()
            if unsupported_inline:
                unresolved.append("TRACE_INLINE_GEOMETRY_UNSUPPORTED")
            active = {
                "source_offset": offset,
                "trace_id": _decode(match.group(1)),
                "net": net,
                "starting_node": _parse_node_reference(match.group(4), net),
                "ending_node": _parse_node_reference(match.group(5), net),
                "thermal": match.group(3) is not None,
                "inline_width": inline_width,
                "continuation_widths": [],
                "unresolved_codes": unresolved,
            }
            continue
        if stripped.lower().startswith(b"trace"):
            previous = flush()
            if previous is not None:
                yield previous
            identity = stripped.split(None, 1)[0]
            if b"::" in identity:
                trace_raw, net_raw = identity.split(b"::", 1)
            else:
                trace_raw, net_raw = identity, b""
            unresolved_reference = _NodeReference("", "", None, None)
            yield _LogicalTrace(
                source_offset=offset,
                trace_id=_decode(trace_raw) or f"Trace@{offset}",
                net=_decode(net_raw) or "<UNRESOLVED>",
                starting_node=unresolved_reference,
                ending_node=unresolved_reference,
                thermal=False,
                width_um=None,
                width_source=TraceWidthSource.UNRESOLVED,
                unresolved_codes=("TRACE_HEADER_UNSUPPORTED",),
            )
            continue
        if stripped.startswith(b"+") and active is not None:
            width_matches = tuple(_WIDTH_RE.finditer(stripped))
            for width_match in width_matches:
                try:
                    active["continuation_widths"].append(  # type: ignore[union-attr]
                        _length_um(width_match.group(0).split(b"=", 1)[1])
                    )
                except ValueError:
                    active["unresolved_codes"].append(  # type: ignore[union-attr]
                        "INVALID_TRACE_WIDTH"
                    )
            if len(width_matches) > 1:
                active["unresolved_codes"].append(  # type: ignore[union-attr]
                    "CONFLICTING_TRACE_WIDTH"
                )
            unsupported = re.sub(
                rb"\bWidth\s*=\s*" + _LENGTH_TOKEN_RE.pattern,
                b"",
                stripped[1:],
                flags=re.IGNORECASE,
            ).strip()
            if unsupported:
                active["unresolved_codes"].append(  # type: ignore[union-attr]
                    "TRACE_CONTINUATION_UNSUPPORTED"
                )
            continue
        if stripped.startswith(b"+"):
            unresolved_reference = _NodeReference("", "", None, None)
            yield _LogicalTrace(
                source_offset=offset,
                trace_id=f"Trace@{offset}",
                net="<UNRESOLVED>",
                starting_node=unresolved_reference,
                ending_node=unresolved_reference,
                thermal=False,
                width_um=None,
                width_source=TraceWidthSource.UNRESOLVED,
                unresolved_codes=("TRACE_CONTINUATION_ORPHANED",),
            )
            continue
        previous = flush()
        if previous is not None:
            yield previous
    previous = flush()
    if previous is not None:
        yield previous


@dataclass(slots=True)
class _ResolvedNodeIndex:
    exact: dict[tuple[str, str], _ResolvedNode]
    by_base: dict[tuple[str, str], tuple[_ResolvedNode, ...]]


def _resolve_referenced_nodes(
    data: mmap.mmap,
    start: int,
    end: int,
    references: Sequence[_NodeReference],
    *,
    check: CheckCallback,
) -> _ResolvedNodeIndex:
    requested_exact = {
        (item.raw.casefold(), (item.net or "").casefold()) for item in references
    }
    requested_base = {
        (item.node_id.casefold(), (item.net or "").casefold()) for item in references
    }
    exact: dict[tuple[str, str], _ResolvedNode] = {}
    ambiguous_exact: set[tuple[str, str]] = set()
    malformed_base: set[tuple[str, str]] = set()
    by_base_mutable: dict[tuple[str, str], list[_ResolvedNode]] = defaultdict(list)
    for line_index, (_offset, raw) in enumerate(_iter_lines(data, start, end)):
        if line_index % 16384 == 0:
            check()
        if not raw.startswith(b"Node"):
            continue
        token = raw.split(None, 1)[0]
        reference = _parse_node_reference(token, None)
        net = reference.net
        if net is None:
            continue
        exact_key = (reference.raw.casefold(), net.casefold())
        base_key = (reference.node_id.casefold(), net.casefold())
        if exact_key not in requested_exact and base_key not in requested_base:
            continue
        x_matches = tuple(_NODE_X_RE.finditer(raw))
        y_matches = tuple(_NODE_Y_RE.finditer(raw))
        layer_matches = tuple(_NODE_LAYER_RE.finditer(raw))
        if len(x_matches) != 1 or len(y_matches) != 1 or len(layer_matches) != 1:
            exact.pop(exact_key, None)
            ambiguous_exact.add(exact_key)
            malformed_base.add(base_key)
            continue
        x_match = x_matches[0]
        y_match = y_matches[0]
        layer_match = layer_matches[0]
        try:
            node = _ResolvedNode(
                raw=reference.raw,
                node_id=reference.node_id,
                net=net,
                layer=_decode(layer_match.group(1)),
                x_um=_length_um(x_match.group(1)),
                y_um=_length_um(y_match.group(1)),
            )
        except ValueError:
            exact.pop(exact_key, None)
            ambiguous_exact.add(exact_key)
            malformed_base.add(base_key)
            continue
        previous = exact.get(exact_key)
        if exact_key in ambiguous_exact:
            pass
        elif previous is not None and previous != node:
            # Ambiguous duplicates are represented by removing exact authority;
            # never let a later third duplicate restore that authority.
            exact.pop(exact_key, None)
            ambiguous_exact.add(exact_key)
        else:
            exact[exact_key] = node
        if base_key not in malformed_base and node not in by_base_mutable[base_key]:
            by_base_mutable[base_key].append(node)
    return _ResolvedNodeIndex(
        exact=exact,
        by_base={
            key: tuple(values)
            for key, values in by_base_mutable.items()
            if key not in malformed_base
        },
    )


def _resolved_for_reference(
    index: _ResolvedNodeIndex, reference: _NodeReference, trace_net: str
) -> _ResolvedNode | None:
    net = (reference.net or trace_net).casefold()
    exact = index.exact.get((reference.raw.casefold(), net))
    if exact is not None:
        return exact
    if reference.qualifier is not None:
        return None
    candidates = index.by_base.get((reference.node_id.casefold(), net), ())
    return candidates[0] if len(candidates) == 1 else None


def _parse_node_reference(raw: bytes, default_net: str | None) -> _NodeReference:
    token = raw.strip()
    if b"::" in token:
        node_raw, net_raw = token.split(b"::", 1)
        net = _decode(net_raw.split(None, 1)[0]) or default_net
    else:
        node_raw = token
        net = default_net
    if b"!!" in node_raw:
        base_raw, qualifier_raw = node_raw.split(b"!!", 1)
        qualifier = _decode(qualifier_raw) or None
    else:
        base_raw = node_raw
        qualifier = None
    return _NodeReference(
        raw=_decode(node_raw),
        node_id=_decode(base_raw),
        qualifier=qualifier,
        net=net,
    )


def _role_for(
    roles: Mapping[str, RoutingNetRole], net: str
) -> RoutingNetRole:
    key = net.casefold()
    for name, role in roles.items():
        if name.casefold() == key:
            return RoutingNetRole(role)
    return RoutingNetRole.UNKNOWN


def _canonical_roles(
    roles: Mapping[str, RoutingNetRole],
) -> dict[str, RoutingNetRole]:
    result: dict[str, RoutingNetRole] = {}
    key_to_name: dict[str, str] = {}
    for name, role in roles.items():
        key = str(name).casefold()
        canonical = key_to_name.setdefault(key, str(name))
        existing = result.get(canonical)
        normalized = RoutingNetRole(role)
        if existing is None:
            result[canonical] = normalized
        elif existing != normalized:
            result[canonical] = RoutingNetRole.UNKNOWN
    return result


def _merge_role(
    roles: dict[str, RoutingNetRole], name: str, role: RoutingNetRole
) -> None:
    key = name.casefold()
    existing_name = next((item for item in roles if item.casefold() == key), None)
    if existing_name is None:
        roles[name] = role
    elif roles[existing_name] != role:
        roles[existing_name] = RoutingNetRole.UNKNOWN


def _find_line(
    data: mmap.mmap, prefix: bytes, start: int = 0, end: int | None = None
) -> int:
    stop = len(data) if end is None else end
    candidate = max(start, 0)
    while candidate < stop:
        found = data.find(prefix, candidate, stop)
        if found < 0:
            return -1
        if found == 0 or data[found - 1 : found] in {b"\n", b"\r"}:
            return found
        candidate = found + 1
    return -1


def _iter_lines(data: mmap.mmap, start: int, end: int):
    position = max(0, start)
    if position and data[position - 1 : position] not in {b"\n", b"\r"}:
        newline = data.find(b"\n", position, end)
        position = end if newline < 0 else newline + 1
    while position < end:
        newline = data.find(b"\n", position, end)
        stop = end if newline < 0 else newline
        raw = data[position:stop]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        yield position, raw
        if newline < 0:
            break
        position = newline + 1


def _length_um(raw: bytes) -> float:
    match = _LENGTH_TOKEN_RE.fullmatch(raw.strip())
    if match is None:
        raise ValueError(f"invalid SPD length {_decode(raw)!r}")
    value = float(match.group(1))
    unit = match.group(2).lower()
    scale = {b"m": 1.0e6, b"mm": 1.0e3, b"u": 1.0, b"um": 1.0, b"mil": 25.4}[unit]
    result = value * scale
    if not isfinite(result):
        raise ValueError("SPD length is not finite")
    return result


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


__all__ = [
    "SpdRoutingExtraction",
    "extract_spd_routing_obstacles",
    "merge_spd_routing_net_roles",
    "parse_spd_routing_net_roles",
]
