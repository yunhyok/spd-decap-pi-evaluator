from __future__ import annotations

import mmap
from pathlib import Path

from spd_decap_pi._core.io.spd_routing import (
    extract_spd_routing_obstacles,
    parse_spd_routing_net_roles,
)
from spd_decap_pi.routing_obstacles import (
    RoutingNetRole,
    RoutingObjectProvenance,
    TraceWidthSource,
)


def _fixture_bytes() -> bytes:
    return (
        ".NetList\n"
        "SIG_A\n"
        "SIG_B\n"
        "DGND -> GroundNets\n"
        "VDD -> PowerNets::Unselected\n"
        ".EndNetList\n"
        "* Node description lines\n"
        "Node1::SIG_A X = 0mm Y = 0mm Layer = SIG1\n"
        "Node2::SIG_A X = 1mm Y = 0mm Layer = SIG1\n"
        "Node3::SIG_A X = 0mm Y = 1mm Layer = SIG1\n"
        "Node4::SIG_A X = 1mm Y = 1mm Layer = SIG1\n"
        "Node5::SIG_B X = 2mm Y = 2mm Layer = SIG2\n"
        "Node6::SIG_B X = 3mm Y = 3mm Layer = SIG2\n"
        "Node7::SIG_A X = 4mm Y = 4mm Layer = SIG2\n"
        "* Trace description lines\n"
        "Trace1::SIG_A StartingNode = Node1::SIG_A EndingNode = Node2::SIG_A Width = 0.020mm\n"
        "Trace2::SIG_A Thermal StartingNode = Node3::SIG_A EndingNode = Node4::SIG_A\n"
        "+ Width = 0.025mm\n"
        "Trace3::SIG_A StartingNode = Node1::SIG_A EndingNode = Node1::SIG_A\n"
        "Trace4::SIG_B StartingNode = Node5::SIG_B EndingNode = Node6::SIG_B Width = 20um\n"
        "Trace5::SIG_A StartingNode = Node1::SIG_A EndingNode = Node7::SIG_A Width = 20um\n"
        "* Via description lines\n"
    ).encode()


def test_netlist_roles_do_not_use_layer_or_name_heuristics(tmp_path: Path) -> None:
    path = tmp_path / "roles.spd"
    path.write_bytes(_fixture_bytes())
    with path.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        roles = parse_spd_routing_net_roles(data)

    assert roles["SIG_A"] == RoutingNetRole.SIGNAL
    assert roles["SIG_B"] == RoutingNetRole.SIGNAL
    assert roles["DGND"] == RoutingNetRole.GROUND
    assert roles["VDD"] == RoutingNetRole.POWER


def test_logical_trace_parser_resolves_inline_continuation_and_thermal(
    tmp_path: Path,
) -> None:
    payload = _fixture_bytes()
    path = tmp_path / "routing.spd"
    path.write_bytes(payload)
    with path.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        roles = parse_spd_routing_net_roles(data)
        node_start = data.find(b"* Node description lines")
        trace_start = data.find(b"* Trace description lines")
        via_start = data.find(b"* Via description lines")
        result = extract_spd_routing_obstacles(
            data,
            trace_start=trace_start,
            trace_end=via_start,
            node_start=node_start,
            node_end=trace_start,
            conductor_layers=("TOP", "SIG1", "SIG2", "BOTTOM"),
            net_roles=roles,
        )

    by_id = {item.trace_id: item for item in result.segments}
    assert by_id["Trace1"].width_um == 20.0
    assert by_id["Trace1"].width_source == TraceWidthSource.INLINE
    assert by_id["Trace2"].width_um == 25.0
    assert by_id["Trace2"].width_source == TraceWidthSource.CONTINUATION
    assert by_id["Trace2"].thermal is True
    assert by_id["Trace1"].provenance == RoutingObjectProvenance.PHYSICAL_ROUTING
    assert "Trace3" not in by_id
    assert "Trace5" not in by_id
    assert result.statistics["missing_width_records"] == 1
    assert result.statistics["cross_layer_records"] == 1
    assert sum(not item.complete for item in result.layer_completeness) == 2
    assert next(
        item for item in result.layer_completeness if item.layer == "TOP"
    ).complete
    sig1 = next(item for item in result.layer_completeness if item.layer == "SIG1")
    assert "TRACE_WIDTH_UNRESOLVED" in sig1.unresolved_codes
    assert "TRACE_CROSS_LAYER_ENDPOINTS" in sig1.unresolved_codes


def test_malformed_trace_header_marks_every_layer_incomplete(tmp_path: Path) -> None:
    payload = (
        ".NetList\nSIG_A\n.EndNetList\n"
        "* Node description lines\n"
        "Node1::SIG_A X = 0mm Y = 0mm Layer = SIG1\n"
        "* Trace description lines\n"
        "TraceBroken::SIG_A UnsupportedHeader = Node1::SIG_A\n"
        "* Via description lines\n"
    ).encode()
    path = tmp_path / "malformed-trace.spd"
    path.write_bytes(payload)
    with path.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        result = extract_spd_routing_obstacles(
            data,
            trace_start=data.find(b"* Trace description lines"),
            trace_end=data.find(b"* Via description lines"),
            node_start=data.find(b"* Node description lines"),
            node_end=data.find(b"* Trace description lines"),
            conductor_layers=("TOP", "SIG1", "BOTTOM"),
            net_roles=parse_spd_routing_net_roles(data),
        )

    assert result.statistics["malformed_trace_records"] == 1
    assert all(not item.complete for item in result.layer_completeness)
    assert all(
        "TRACE_HEADER_UNSUPPORTED" in item.unresolved_codes
        for item in result.layer_completeness
    )


def test_third_conflicting_qualified_node_cannot_restore_exact_authority(
    tmp_path: Path,
) -> None:
    payload = (
        ".NetList\nSIG_A\n.EndNetList\n"
        "* Node description lines\n"
        "Node1!!A::SIG_A X = 0mm Y = 0mm Layer = SIG1\n"
        "Node1!!A::SIG_A X = 1mm Y = 0mm Layer = SIG1\n"
        "Node1!!A::SIG_A X = 2mm Y = 0mm Layer = SIG1\n"
        "Node2::SIG_A X = 3mm Y = 0mm Layer = SIG1\n"
        "* Trace description lines\n"
        "Trace1::SIG_A StartingNode = Node1!!A::SIG_A "
        "EndingNode = Node2::SIG_A Width = 20um\n"
        "* Via description lines\n"
    ).encode()
    path = tmp_path / "ambiguous-node.spd"
    path.write_bytes(payload)
    with path.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        result = extract_spd_routing_obstacles(
            data,
            trace_start=data.find(b"* Trace description lines"),
            trace_end=data.find(b"* Via description lines"),
            node_start=data.find(b"* Node description lines"),
            node_end=data.find(b"* Trace description lines"),
            conductor_layers=("TOP", "SIG1", "BOTTOM"),
            net_roles=parse_spd_routing_net_roles(data),
        )

    assert result.segments == ()
    assert result.statistics["unresolved_endpoint_records"] == 1
    sig1 = next(item for item in result.layer_completeness if item.layer == "SIG1")
    assert "TRACE_ENDPOINT_UNRESOLVED" in sig1.unresolved_codes
    assert all(not item.complete for item in result.layer_completeness)


def test_inline_geometry_tail_is_not_silently_treated_as_straight_trace(
    tmp_path: Path,
) -> None:
    payload = (
        ".NetList\nSIG_A\n.EndNetList\n"
        "* Node description lines\n"
        "Node1::SIG_A X = 0mm Y = 0mm Layer = SIG1\n"
        "Node2::SIG_A X = 1mm Y = 0mm Layer = SIG1\n"
        "* Trace description lines\n"
        "Trace1::SIG_A StartingNode = Node1::SIG_A EndingNode = Node2::SIG_A "
        "BreakPoint = 0.5mm,0.5mm Width = 20um\n"
        "* Via description lines\n"
    ).encode()
    path = tmp_path / "inline-tail.spd"
    path.write_bytes(payload)
    with path.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        result = extract_spd_routing_obstacles(
            data,
            trace_start=data.find(b"* Trace description lines"),
            trace_end=data.find(b"* Via description lines"),
            node_start=data.find(b"* Node description lines"),
            node_end=data.find(b"* Trace description lines"),
            conductor_layers=("TOP", "SIG1", "BOTTOM"),
            net_roles=parse_spd_routing_net_roles(data),
        )

    assert result.segments == ()
    sig1 = next(item for item in result.layer_completeness if item.layer == "SIG1")
    assert "TRACE_INLINE_GEOMETRY_UNSUPPORTED" in sig1.unresolved_codes


def test_duplicate_continuation_width_and_orphan_are_fail_closed(
    tmp_path: Path,
) -> None:
    payload = (
        ".NetList\nSIG_A\n.EndNetList\n"
        "* Node description lines\n"
        "Node1::SIG_A X = 0mm Y = 0mm Layer = SIG1\n"
        "Node2::SIG_A X = 1mm Y = 0mm Layer = SIG1\n"
        "* Trace description lines\n"
        "Trace1::SIG_A StartingNode = Node1::SIG_A EndingNode = Node2::SIG_A\n"
        "+ Width = 10um Width = 100um\n"
        "UnsupportedBoundary\n"
        "+ Width = 20um\n"
        "* Via description lines\n"
    ).encode()
    path = tmp_path / "continuation-errors.spd"
    path.write_bytes(payload)
    with path.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        result = extract_spd_routing_obstacles(
            data,
            trace_start=data.find(b"* Trace description lines"),
            trace_end=data.find(b"* Via description lines"),
            node_start=data.find(b"* Node description lines"),
            node_end=data.find(b"* Trace description lines"),
            conductor_layers=("TOP", "SIG1", "BOTTOM"),
            net_roles=parse_spd_routing_net_roles(data),
        )

    assert result.segments == ()
    sig1 = next(item for item in result.layer_completeness if item.layer == "SIG1")
    assert "CONFLICTING_TRACE_WIDTH" in sig1.unresolved_codes
    assert all(
        "TRACE_CONTINUATION_ORPHANED" in item.unresolved_codes
        for item in result.layer_completeness
    )


def test_duplicate_node_attribute_on_one_line_is_ambiguous(tmp_path: Path) -> None:
    payload = (
        ".NetList\nSIG_A\n.EndNetList\n"
        "* Node description lines\n"
        "Node1::SIG_A X = 0mm Y = 0mm Layer = SIG1 X = 100mm\n"
        "Node1::SIG_A X = 0mm Y = 0mm Layer = SIG1\n"
        "Node2::SIG_A X = 1mm Y = 0mm Layer = SIG1\n"
        "* Trace description lines\n"
        "Trace1::SIG_A StartingNode = Node1::SIG_A "
        "EndingNode = Node2::SIG_A Width = 20um\n"
        "* Via description lines\n"
    ).encode()
    path = tmp_path / "duplicate-node-attribute.spd"
    path.write_bytes(payload)
    with path.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        result = extract_spd_routing_obstacles(
            data,
            trace_start=data.find(b"* Trace description lines"),
            trace_end=data.find(b"* Via description lines"),
            node_start=data.find(b"* Node description lines"),
            node_end=data.find(b"* Trace description lines"),
            conductor_layers=("TOP", "SIG1", "BOTTOM"),
            net_roles=parse_spd_routing_net_roles(data),
        )

    assert result.segments == ()
    assert result.statistics["unresolved_endpoint_records"] == 1
