from __future__ import annotations

from pathlib import Path

import pytest

from spd_decap_pi._core.io.conductor_graph import extract_spd_conductor_graph
from spd_decap_pi._core.solver.research_endpoint_via_reducer import (
    EndpointViaReducerError,
    build_endpoint_via_topology,
    compile_endpoint_via_mna,
)


def _source(
    tmp_path: Path, *, nodes: str, vias: str, traces: str = ""
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "endpoint-topology.spd"
    path.write_text(
        f"""Title endpoint topology
* Layer description lines
Signal$TOP Thickness = 20um Material = COPPER
Medium$D1 Thickness = 30um Material = ABF
Signal$MID Thickness = 20um Material = COPPER
Medium$D2 Thickness = 30um Material = ABF
Signal$PWR Thickness = 20um Material = COPPER
* Node description lines
{nodes}
* Trace description lines
{traces}
* Via description lines
{vias}
* PadStack collection description lines
.PadStackDef MICRO 0.025mm Material = COPPER
.PadDef Signal$TOP
Regular Circle 0.05mm
.EndPadDef
.PadDef Signal$MID
Regular Circle 0.05mm
.EndPadDef
.PadDef Signal$PWR
Regular Circle 0.05mm
.EndPadDef
.EndPadStackDef
.PadStackDef DR-2128 0.075mm Material = COPPER
.PadDef Signal$TOP
Regular Circle 0.1mm
.EndPadDef
.PadDef Signal$MID
Regular Circle 0.1mm
.EndPadDef
.PadDef Signal$PWR
Regular Circle 0.1mm
.EndPadDef
.EndPadStackDef
* Material description lines
.Material
.MetalModel COPPER
*Temperature(C) Conductivity(S/m)
20 5.959e7
.EndMetalModel
.EndMaterial
* Circuit description lines
""",
        encoding="utf-8",
    )
    return path


def _graph(path: Path):
    return extract_spd_conductor_graph(path, net_names=("VDD/0",), site="0")


def test_shared_bottleneck_is_series_then_parallel_not_three_parallel(tmp_path: Path) -> None:
    graph = _graph(_source(
        tmp_path,
        nodes="""NodeT!!1::VDD/0 X = 0mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeM0!!2::VDD/0 X = 0mm Y = 0mm Layer = Signal$MID PadStack = MICRO
NodeM1!!3::VDD/0 X = 0.1mm Y = 0mm Layer = Signal$MID PadStack = MICRO
NodeA!!4::VDD/0 X = 0mm Y = 0mm Layer = Signal$PWR PadStack = MICRO
NodeB!!5::VDD/0 X = 0.1mm Y = 0mm Layer = Signal$PWR PadStack = MICRO""",
        traces="TraceMid::VDD/0 StartingNode = NodeM0!!2::VDD/0 EndingNode = NodeM1!!3::VDD/0",
        vias="""ViaTrunk::VDD/0 UpperNode = NodeT!!1::VDD/0 LowerNode = NodeM0!!2::VDD/0 PadStack = MICRO
ViaA::VDD/0 UpperNode = NodeM0!!2::VDD/0 LowerNode = NodeA!!4::VDD/0 PadStack = MICRO
ViaB::VDD/0 UpperNode = NodeM1!!3::VDD/0 LowerNode = NodeB!!5::VDD/0 PadStack = MICRO""",
    ))
    topology = build_endpoint_via_topology(
        graph, net="VDD/0", external_node_ids=("NodeT",),
        target_node_ids=("NodeA", "NodeB"),
        target_artwork_components={"NodeA": "pwr-island-1", "NodeB": "pwr-island-1"},
    )
    assert [item.via_id for item in topology.branches] == ["ViaA", "ViaB", "ViaTrunk"]
    assert len(topology.external_node_ids) == len(topology.target_node_ids) == 1
    with pytest.raises(EndpointViaReducerError, match="solid-microvia PEEC compilation failed"):
        compile_endpoint_via_mna(topology)


def test_parallelism_requires_common_endpoint_supernodes(tmp_path: Path) -> None:
    graph = _graph(_source(
        tmp_path,
        nodes="""NodeT0!!1::VDD/0 X = 0mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeT1!!2::VDD/0 X = 0.1mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeA!!3::VDD/0 X = 0mm Y = 0mm Layer = Signal$MID PadStack = MICRO
NodeB!!4::VDD/0 X = 0.1mm Y = 0mm Layer = Signal$MID PadStack = MICRO""",
        traces="TraceTop::VDD/0 StartingNode = NodeT0!!1::VDD/0 EndingNode = NodeT1!!2::VDD/0",
        vias="""ViaA::VDD/0 UpperNode = NodeT0!!1::VDD/0 LowerNode = NodeA!!3::VDD/0 PadStack = MICRO
ViaB::VDD/0 UpperNode = NodeT1!!2::VDD/0 LowerNode = NodeB!!4::VDD/0 PadStack = MICRO""",
    ))
    topology = build_endpoint_via_topology(
        graph, net="VDD/0", external_node_ids=("NodeT0", "NodeT1"),
        target_node_ids=("NodeA", "NodeB"),
        target_artwork_components={"NodeA": "exact-plane", "NodeB": "exact-plane"},
    )
    assert len(topology.external_node_ids) == len(topology.target_node_ids) == 1
    assert topology.ideal_trace_ids == ("TraceTop",)
    with pytest.raises(EndpointViaReducerError, match="solid-microvia PEEC compilation failed"):
        compile_endpoint_via_mna(topology)


def test_direct_and_segmented_source_vias_are_not_path_normalized(tmp_path: Path) -> None:
    common_nodes = """NodeT!!1::VDD/0 X = 0mm Y = 0mm Layer = Signal$TOP PadStack = DR-2128
NodeM!!2::VDD/0 X = 0mm Y = 0mm Layer = Signal$MID PadStack = DR-2128
NodeP!!3::VDD/0 X = 0mm Y = 0mm Layer = Signal$PWR PadStack = DR-2128"""
    direct = build_endpoint_via_topology(
        _graph(_source(tmp_path / "direct", nodes=common_nodes,
            vias="ViaGeoMir::VDD/0 UpperNode = NodeT!!1::VDD/0 LowerNode = NodeP!!3::VDD/0 PadStack = DR-2128")),
        net="VDD/0", external_node_ids=("NodeT",), target_node_ids=("NodeP",),
    )
    segmented = build_endpoint_via_topology(
        _graph(_source(tmp_path / "segmented", nodes=common_nodes,
            vias="""ViaGeoMirA::VDD/0 UpperNode = NodeT!!1::VDD/0 LowerNode = NodeM!!2::VDD/0 PadStack = DR-2128
ViaGeoMirB::VDD/0 UpperNode = NodeM!!2::VDD/0 LowerNode = NodeP!!3::VDD/0 PadStack = DR-2128""")),
        net="VDD/0", external_node_ids=("NodeT",), target_node_ids=("NodeP",),
    )

    assert [item.via_id for item in direct.branches] == ["ViaGeoMir"]
    assert direct.branches[0].conductor_model == "HOLLOW_PLATED_BARREL"
    assert [item.via_id for item in segmented.branches] == ["ViaGeoMirA", "ViaGeoMirB"]
    assert {item.conductor_model for item in segmented.branches} == {"SOLID_COPPER_FILLED_MICROVIA"}
    # NodeM remains in the source audit topology but has no direct-Via branch;
    # it must not be silently joined or stamped as a singular MNA island.
    assert any(item.endswith("\x1fnodem") for item in direct.reduced_node_ids)
    direct_model = compile_endpoint_via_mna(direct)
    assert not any(item.endswith("\x1fnodem") for item in direct_model.operator.node_ids)
    assert direct_model.solve(1e6).impedance_ohm[0, 0].real > 0.0
    with pytest.raises(EndpointViaReducerError, match="solid-microvia PEEC compilation failed"):
        compile_endpoint_via_mna(segmented)


def test_source_order_is_deterministic_and_frequency_result_is_immutable(tmp_path: Path) -> None:
    nodes = """NodeT0!!1::VDD/0 X = 0mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeT1!!2::VDD/0 X = 0.1mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeA!!3::VDD/0 X = 0mm Y = 0mm Layer = Signal$MID PadStack = MICRO
NodeB!!4::VDD/0 X = 0.1mm Y = 0mm Layer = Signal$MID PadStack = MICRO"""
    via_a = "ViaA::VDD/0 UpperNode = NodeT0!!1::VDD/0 LowerNode = NodeA!!3::VDD/0 PadStack = MICRO"
    via_b = "ViaB::VDD/0 UpperNode = NodeT1!!2::VDD/0 LowerNode = NodeB!!4::VDD/0 PadStack = MICRO"
    trace = "TraceTop::VDD/0 StartingNode = NodeT0!!1::VDD/0 EndingNode = NodeT1!!2::VDD/0"
    kwargs = {"net": "VDD/0", "external_node_ids": ("NodeT0", "NodeT1"), "target_node_ids": ("NodeA", "NodeB"), "target_artwork_components": {"NodeA": "pwr", "NodeB": "pwr"}}
    first = build_endpoint_via_topology(_graph(_source(tmp_path / "first", nodes=nodes, traces=trace, vias=f"{via_b}\n{via_a}")), **kwargs)
    second = build_endpoint_via_topology(_graph(_source(tmp_path / "second", nodes=nodes, traces=trace, vias=f"{via_a}\n{via_b}")), **kwargs)

    assert [item.via_id for item in first.branches] == [item.via_id for item in second.branches] == ["ViaA", "ViaB"]
    for topology in (first, second):
        with pytest.raises(EndpointViaReducerError, match="solid-microvia PEEC compilation failed"):
            compile_endpoint_via_mna(topology)


def test_ambiguous_or_unproven_endpoint_evidence_fails_closed(tmp_path: Path) -> None:
    graph = _graph(_source(
        tmp_path,
        nodes="""NodeT!!1::VDD/0 X = 0mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeA!!2::VDD/0 X = 0mm Y = 0mm Layer = Signal$MID PadStack = MICRO""",
        vias="ViaA::VDD/0 UpperNode = NodeT!!1::VDD/0 LowerNode = NodeA!!2::VDD/0 PadStack = MICRO",
    ))
    with pytest.raises(EndpointViaReducerError, match="external Node"):
        build_endpoint_via_topology(graph, net="VDD/0", external_node_ids=("Nope",), target_node_ids=("NodeA",))
    with pytest.raises(EndpointViaReducerError, match="outside the selected target"):
        build_endpoint_via_topology(
            graph, net="VDD/0", external_node_ids=("NodeT",), target_node_ids=("NodeA",),
            target_artwork_components={"Nope": "invented"},
        )
