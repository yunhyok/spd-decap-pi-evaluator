"""Read-only raw-SPD diagnostic for decap PWR escape-route components.

This is deliberately separate from the Distribution planner.  It answers a
narrow physical question for a selected set of donor rails:

* start at each persisted decap PWR-Via endpoint;
* follow only raw same-NET, same-layer Trace edges and validated copper Via
  edges; and
* call a receiver eligible only if one physical Via in that component spans an
  exact receiver-plane layer *and that Via's own immutable XY* is strictly
  inside the ordered PowerSI plane artwork.

No source SPD, scenario, workbook, or production code is modified.  In
particular this tool does not infer permissions from a nearby Via, a different
NET, a bounding box, or a trace endpoint location.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import mmap
from pathlib import Path
from time import perf_counter
import tracemalloc
from typing import Any, Iterable

from spd_decap_pi._core.io.spd import (
    _NODE_ATTR_RE,
    _TRACE_RE,
    _VIA_RE,
    _attribute,
    _decode,
    _find_line,
    _iter_lines,
    _length_um,
    _parse_padstacks,
)
from spd_decap_pi.distribution import distribution_present_counts
from spd_decap_pi.distribution_workbook import load_distribution_targets
from spd_decap_pi.scenario_io import load_scenario_bundle
from spd_decap_pi.spd_adapter import import_spd_scenario, verify_scenario_source

# The independent ordered-artwork evaluator is shared with the release replay
# harness.  It does not call the Distribution planner's eligibility helpers.
from validate_real_distribution_replay import _decode_geometry_asset, _ordered_artwork_contains


@dataclass(frozen=True, slots=True)
class _Node:
    net: str
    layer: str
    x_um: float
    y_um: float


@dataclass(frozen=True, slots=True)
class _Via:
    net: str
    via_id: str
    upper: int
    lower: int
    padstack: str
    rotation_tail: bytes


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple[str, int], tuple[str, int]] = {}
        self.rank: dict[tuple[str, int], int] = {}

    def add(self, item: tuple[str, int]) -> None:
        if item not in self.parent:
            self.parent[item] = item
            self.rank[item] = 0

    def find(self, item: tuple[str, int]) -> tuple[str, int]:
        self.add(item)
        parent = self.parent[item]
        if parent != item:
            parent = self.find(parent)
            self.parent[item] = parent
        return parent

    def union(self, first: tuple[str, int], second: tuple[str, int]) -> None:
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root == second_root:
            return
        if self.rank[first_root] < self.rank[second_root]:
            first_root, second_root = second_root, first_root
        self.parent[second_root] = first_root
        if self.rank[first_root] == self.rank[second_root]:
            self.rank[first_root] += 1


def _node_number(raw: bytes) -> int | None:
    value = raw[4:] if raw.startswith(b"Node") else raw
    return int(value) if value.isdigit() else None


def _node_identity(raw: bytes) -> tuple[int, str] | None:
    separator = raw.find(b"::")
    if separator < 0:
        return None
    raw_id = raw[:separator].split(b"!!", 1)[0].split(None, 1)[0]
    node_id = _node_number(raw_id)
    net_token = raw[separator + 2:].split(None, 1)[0]
    if node_id is None or not net_token:
        return None
    return node_id, _decode(net_token)


def _supported_quarter_rotation(raw: bytes) -> bool:
    try:
        value = float(_attribute(raw, b"AbsoluteRotation") or 0.0)
    except ValueError:
        return False
    normalized = value % 360.0
    return abs(normalized - round(normalized / 90.0) * 90.0) <= 1.0e-6 or abs(normalized - 360.0) <= 1.0e-6


def _span_includes(layer: str, first: str, second: str, order: dict[str, int]) -> bool:
    target = order.get(layer.casefold())
    left = order.get(first.casefold())
    right = order.get(second.casefold())
    return target is not None and left is not None and right is not None and min(left, right) <= target <= max(left, right)


def _receiver_cells(scenario: Any, workbook: Path) -> tuple[dict[tuple[str, str], int], tuple[str, ...]]:
    project = scenario.base_project
    imported = load_distribution_targets(
        workbook,
        rail_ids=tuple(item.rail_id for item in project.rails),
        model_ids=tuple(item.model_id for item in project.cap_models),
        current_present=distribution_present_counts(scenario),
        current_source_sha256=scenario.source.sha256,
        current_design_fingerprint=scenario.design_fingerprint,
    )
    requested: dict[tuple[str, str], int] = {}
    for (rail_id, model_id), target in imported.targets.items():
        present = distribution_present_counts(scenario).get((rail_id, model_id), 0)
        if int(target) > int(present):
            requested[(rail_id, model_id)] = int(target) - int(present)
    return requested, tuple(imported.warnings)


def _receiver_artwork(scenario: Any, attachments: dict[str, bytes], receiver_rails: Iterable[str]) -> dict[str, list[tuple[str, dict[str, object]]]]:
    project = scenario.base_project
    rails = {item.rail_id.casefold(): item for item in project.rails}
    wanted = {item.casefold() for item in receiver_rails}
    records = project.metadata.get("spd_import", {}).get("plane_geometries", [])
    result: dict[str, list[tuple[str, dict[str, object]]]] = {item: [] for item in wanted}
    for rail_key in wanted:
        rail = rails[rail_key]
        for record in records:
            if not isinstance(record, dict) or str(record.get("net", "")).casefold() != rail.net.casefold():
                continue
            payload = _decode_geometry_asset(record, attachments)
            result[rail_key].append((str(payload["layer"]), payload))
    return result


def _run(args: argparse.Namespace) -> dict[str, object]:
    started = perf_counter()
    tracemalloc.start()
    import_started = perf_counter()
    if args.scenario is not None:
        imported = load_scenario_bundle(args.scenario)
        scenario = imported.scenario
        verify_scenario_source(scenario, args.spd)
        scenario_load_s = perf_counter() - import_started
    else:
        imported = import_spd_scenario(args.spd)
        scenario = imported.scenario
        scenario_load_s = imported.timings.total_s
    requested, workbook_warnings = _receiver_cells(scenario, args.targets)
    rails = {item.rail_id.casefold(): item for item in scenario.base_project.rails}
    receiver_artwork = _receiver_artwork(scenario, imported.attachments, (rail for rail, _model in requested))

    token_set = {item.casefold() for item in args.donor_token}
    donor_rails = [
        rail for rail in scenario.base_project.rails
        if any(token in (rail.rail_id + " " + rail.net).casefold() for token in token_set)
    ]
    donor_net_keys = {rail.net.casefold() for rail in donor_rails}
    if not donor_net_keys:
        raise ValueError("No donor rails match --donor-token")

    analysis = scenario.connection_analysis
    if analysis is None:
        raise ValueError("Imported scenario lacks PWR landing evidence")
    decap_by_refdes = {item.refdes.casefold(): item for item in scenario.decaps}
    donor_seed_refdes: dict[tuple[str, int], set[str]] = defaultdict(set)
    invalid_seed_endpoints = 0
    for connection in analysis.connections.values():
        decap = decap_by_refdes.get(connection.refdes.casefold())
        if decap is None or decap.current_rail_id.casefold() not in {rail.rail_id.casefold() for rail in donor_rails}:
            continue
        for landing in connection.power_vias:
            endpoint = _node_number(str(landing.endpoint_node_id).encode("ascii", errors="ignore"))
            if endpoint is None or landing.net.casefold() not in donor_net_keys:
                invalid_seed_endpoints += 1
                continue
            donor_seed_refdes[(landing.net.casefold(), endpoint)].add(decap.refdes)

    path = Path(args.spd)
    stack_order = {layer.name.casefold(): index for index, layer in enumerate(scenario.base_project.stackup_layers)}
    trace_edges: list[tuple[str, int, int]] = []
    raw_vias: list[_Via] = []
    needed: set[tuple[str, int]] = set(donor_seed_refdes)
    phase_scan = perf_counter()
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        node_start = _find_line(data, b"* Node description lines")
        trace_start = _find_line(data, b"* Trace description lines")
        via_start = _find_line(data, b"* Via description lines")
        pad_start = _find_line(data, b"* PadStack collection description lines")
        if min(node_start, trace_start, via_start) < 0:
            raise ValueError("SPD is missing Node/Trace/Via sections")
        trace_end = via_start
        via_end = pad_start if pad_start > via_start else len(data)
        for match in _TRACE_RE.finditer(data, trace_start, trace_end):
            net = _decode(match.group(2)).casefold()
            if net not in donor_net_keys:
                continue
            first = _node_number(match.group(3))
            second = _node_number(match.group(4))
            if first is None or second is None:
                continue
            trace_edges.append((net, first, second))
            needed.update(((net, first), (net, second)))
        for match in _VIA_RE.finditer(data, via_start, via_end):
            net = _decode(match.group(2)).casefold()
            if net not in donor_net_keys:
                continue
            upper = _node_number(match.group(3))
            lower = _node_number(match.group(4))
            if upper is None or lower is None:
                continue
            raw_vias.append(_Via(net, _decode(match.group(1)), upper, lower, _decode(match.group(5)), match.group(6)))
            needed.update(((net, upper), (net, lower)))
        padstacks = {item.name.casefold(): item for item in _parse_padstacks(data, pad_start, len(data), [])}
        nodes: dict[tuple[str, int], _Node] = {}
        for _offset, raw in _iter_lines(data, node_start, trace_start):
            if not raw.startswith(b"Node"):
                continue
            identity = _node_identity(raw)
            if identity is None:
                continue
            node_id, net_original = identity
            key = (net_original.casefold(), node_id)
            if key not in needed:
                continue
            layer_raw = _attribute(raw, b"Layer")
            attrs = _NODE_ATTR_RE.search(raw)
            if layer_raw is None or attrs is None:
                continue
            try:
                nodes[key] = _Node(net_original, _decode(layer_raw), _length_um(attrs.group(1)), _length_um(attrs.group(2)))
            except ValueError:
                continue

    scan_s = perf_counter() - phase_scan
    union = _UnionFind()
    invalid_trace = 0
    for net, first, second in trace_edges:
        first_node = nodes.get((net, first))
        second_node = nodes.get((net, second))
        if first_node is None or second_node is None or first_node.layer.casefold() != second_node.layer.casefold():
            invalid_trace += 1
            continue
        union.union((net, first), (net, second))

    valid_vias: list[tuple[_Via, _Node, _Node]] = []
    invalid_via = 0
    for via in raw_vias:
        upper = nodes.get((via.net, via.upper))
        lower = nodes.get((via.net, via.lower))
        definition = padstacks.get(via.padstack.casefold())
        if (
            upper is None or lower is None or definition is None
            or definition.drill_diameter_um is None or definition.drill_diameter_um <= 0
            or (definition.material or "").casefold() not in {"copper", "cu"}
            or not _supported_quarter_rotation(via.rotation_tail)
            or upper.x_um != lower.x_um or upper.y_um != lower.y_um
            or not _span_includes(upper.layer, upper.layer, lower.layer, stack_order)
            or upper.layer.casefold() == lower.layer.casefold()
        ):
            invalid_via += 1
            continue
        union.union((via.net, via.upper), (via.net, via.lower))
        valid_vias.append((via, upper, lower))

    seeds_by_component: dict[tuple[str, int], set[str]] = defaultdict(set)
    for seed, refdes in donor_seed_refdes.items():
        if seed in nodes:
            seeds_by_component[union.find(seed)].update(refdes)
    via_by_component: dict[tuple[str, int], list[tuple[_Via, _Node, _Node]]] = defaultdict(list)
    for via, upper, lower in valid_vias:
        key = (via.net, via.upper)
        if key in nodes:
            via_by_component[union.find(key)].append((via, upper, lower))

    eligible_components: dict[str, set[tuple[str, int]]] = {rail: set() for rail, _model in requested}
    component_proofs: dict[tuple[str, int], dict[str, list[dict[str, object]]]] = defaultdict(lambda: defaultdict(list))
    for component, refdes in seeds_by_component.items():
        for rail_key, planes in receiver_artwork.items():
            for via, upper, lower in via_by_component.get(component, ()):
                for target_layer, payload in planes:
                    if not _span_includes(target_layer, upper.layer, lower.layer, stack_order):
                        continue
                    if _ordered_artwork_contains(payload, upper.x_um, upper.y_um):
                        eligible_components[rail_key].add(component)
                        component_proofs[component][rail_key].append({"via_id": via.via_id, "layer": target_layer, "x_um": upper.x_um, "y_um": upper.y_um})

    component_sizes = Counter(len(refdes) for refdes in seeds_by_component.values())
    span_histogram = Counter(
        f"{upper.layer}->{lower.layer}" for _via, upper, lower in valid_vias
    )
    per_receiver: list[dict[str, object]] = []
    for (rail_id, model_id), requested_count in sorted(requested.items()):
        rail_key = rail_id.casefold()
        sites = sorted({refdes for component in eligible_components.get(rail_key, set()) for refdes in seeds_by_component[component]})
        per_receiver.append({
            "rail_id": rail_id, "model_id": model_id, "requested_count": requested_count,
            "eligible_components": len(eligible_components.get(rail_key, set())),
            "eligible_donor_sites": len(sites), "eligible_refdes": sites,
        })
    components = []
    for component, refdes in sorted(seeds_by_component.items(), key=lambda item: (-len(item[1]), item[0])):
        components.append({
            "source_net": component[0], "root_node": component[1],
            "seed_refdes": sorted(refdes), "seed_refdes_count": len(refdes),
            "validated_via_count": len(via_by_component.get(component, ())),
            "eligible_receiver_rails": sorted(component_proofs.get(component, {})),
        })

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "format": "pwr_escape_route_component_diagnostic_v1",
        "source_spd": {"path": str(path), "sha256": scenario.source.sha256},
        "target_workbook": str(args.targets), "workbook_warnings": list(workbook_warnings),
        "donor_tokens": list(args.donor_token),
        "donor_rails": [{"rail_id": rail.rail_id, "net": rail.net} for rail in donor_rails],
        "timings_s": {"scenario_load": scenario_load_s, "raw_route_scan": scan_s, "total": perf_counter() - started},
        "python_memory_bytes": {"current": current, "peak": peak},
        "raw_graph": {"trace_edges": len(trace_edges), "via_edges": len(raw_vias), "needed_nodes": len(needed), "resolved_nodes": len(nodes), "invalid_trace_edges": invalid_trace, "invalid_via_edges": invalid_via, "validated_via_edges": len(valid_vias), "invalid_seed_endpoints": invalid_seed_endpoints},
        "components": {"seeded_component_count": len(seeds_by_component), "seed_refdes_multiplicity_histogram": dict(sorted(component_sizes.items())), "items": components},
        "validated_via_span_histogram": dict(sorted(span_histogram.items())),
        "per_receiver_requested_model": per_receiver,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spd", required=True, type=Path)
    parser.add_argument("--scenario", type=Path, help="Existing hash-matched .spdpi bundle; avoids re-importing the raw SPD.")
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--donor-token", action="append", default=["VTRIP", "VINT", "VCPU"], help="Substring selecting source donor rails; repeatable.")
    parser.add_argument("--json-report", required=True, type=Path)
    args = parser.parse_args()
    report = _run(args)
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "report": str(args.json_report),
        "components": report["components"]["seeded_component_count"],
        "raw_graph": report["raw_graph"],
        "per_receiver_requested_model": report["per_receiver_requested_model"],
        "timings_s": report["timings_s"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
