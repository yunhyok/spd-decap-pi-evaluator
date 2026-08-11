from __future__ import annotations

from pathlib import Path

import pytest

from spd_decap_pi._core.io.conductor_graph import (
    SpdConductorGraphError,
    extract_spd_conductor_graph,
)


SPD = """Title graph fixture
* Layer description lines
Signal$TOP Thickness = 20um Material = COPPER
Medium$D1 Thickness = 30um Material = ABF
Signal$PWR Thickness = 20um Material = COPPER
Medium$D2 Thickness = 30um Material = ABF
Signal$GND Thickness = 20um Material = COPPER
* Node description lines
NodeA!!1::VDD/0 X = 0mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeB!!2::VDD/0 X = 1mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeC!!3::VDD/0 X = 0mm Y = 0mm Layer = Signal$PWR PadStack = MICRO
NodeD!!4::VDD/0 X = 1mm Y = 0mm Layer = Signal$PWR PadStack = MICRO
NodeE!!5::VDD/0 X = 2mm Y = 0mm Layer = Signal$PWR PadStack = MICRO
NodeF!!6::VDD/0 X = 2mm Y = 0mm Layer = Signal$GND PadStack = MICRO
NodeOther!!7::OTHER/0 X = 3mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
* Trace description lines
TraceAB::VDD/0 StartingNode = NodeA!!1::VDD/0 EndingNode = NodeB!!2::VDD/0 Width = 0.10mm
TraceCD::VDD/0 StartingNode = NodeC!!3::VDD/0 EndingNode = NodeD!!4::VDD/0
TraceDE::VDD/0 StartingNode = NodeD!!4::VDD/0 EndingNode = NodeE!!5::VDD/0 Width = 0.08mm
* Via description lines
ViaAC::VDD/0 UpperNode = NodeA!!1::VDD/0 LowerNode = NodeC!!3::VDD/0 PadStack = MICRO Rotation = 90
ViaBD::VDD/0 UpperNode = NodeB!!2::VDD/0 LowerNode = NodeD!!4::VDD/0 PadStack = MICRO
ViaEF::VDD/0 UpperNode = NodeE!!5::VDD/0 LowerNode = NodeF!!6::VDD/0 PadStack = MICRO
* PadStack collection description lines
.PadStackDef MICRO 0.025mm Material = COPPER
.PadDef Signal$TOP
Regular Circle 0.05mm
.EndPadDef
.PadDef Signal$PWR
Regular Box 0.08mm 0.06mm
.EndPadDef
.PadDef Signal$GND
Regular Circle 0.04mm
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
"""


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "branched.spd"
    path.write_text(SPD, encoding="utf-8")
    return path


def test_branched_graph_keeps_all_edges_and_dimensions(tmp_path: Path) -> None:
    graph = extract_spd_conductor_graph(_source(tmp_path), net_names=("VDD/0",), site="0")

    assert [item.source_id for item in graph.nodes] == ["NodeA", "NodeB", "NodeC", "NodeD", "NodeE", "NodeF"]
    assert [item.source_id for item in graph.vias] == ["ViaAC", "ViaBD", "ViaEF"]
    assert [item.source_id for item in graph.traces] == ["TraceAB", "TraceCD", "TraceDE"]
    assert graph.vias[0].upper_layer == "Signal$TOP"
    assert graph.vias[0].lower_layer == "Signal$PWR"
    assert graph.vias[0].upper_node_net_evidence == "VDD/0"
    assert graph.vias[0].lower_node_net_evidence == "VDD/0"
    assert graph.vias[0].upper_conductor_thickness_um == pytest.approx(20.0)
    assert graph.vias[0].lower_conductor_thickness_um == pytest.approx(20.0)
    assert graph.layers[0].name == "Signal$TOP"
    assert graph.vias[0].padstack is not None
    assert graph.vias[0].padstack.drill_diameter_um == pytest.approx(50.0)
    assert graph.vias[0].padstack.pad_width_um == pytest.approx(100.0)
    assert graph.vias[0].padstack.conductivity_s_m == pytest.approx(5.959e7)
    assert graph.layers[0].conductivity_s_m == pytest.approx(5.959e7)
    pads = {item.layer: item for item in graph.vias[0].padstack.pad_defs}
    assert pads["Signal$TOP"].kind == "CIRCLE"
    assert pads["Signal$TOP"].width_um == pytest.approx(100.0)
    assert pads["Signal$PWR"].kind == "RECTANGLE"
    assert pads["Signal$PWR"].width_um == pytest.approx(80.0)
    assert pads["Signal$PWR"].height_um == pytest.approx(60.0)
    assert graph.vias[0].interval_layers == (
        "Signal$TOP", "Medium$D1", "Signal$PWR"
    )
    assert graph.traces[1].width_um is None
    assert graph.traces[0].starting_node_net_evidence == "VDD/0"
    assert any(item.code == "TRACE_WIDTH_MISSING" for item in graph.diagnostics)
    assert any(
        item.code == "POLYGON_CONNECTIVITY_NOT_INCLUDED"
        for item in graph.diagnostics
    )

    node_b = next(item for item in graph.nodes if item.source_id == "NodeB")
    # TraceAB and ViaBD remain incident; their branch joins TraceCD through
    # NodeD, rather than being collapsed into one inferred vertical path.
    assert len(graph.topology_adjacency[node_b.key]) == 2
    assert len(graph.topology_components) == 1
    # TraceCD has no Width, so it remains only in the topology graph.
    node_c = next(item for item in graph.nodes if item.source_id == "NodeC")
    assert len(graph.electrical_adjacency[node_c.key]) == 1
    assert "TraceCD" in graph.topology_components[0].trace_ids
    assert "TraceCD" not in graph.electrical_components[0].trace_ids
    assert graph.electrical_ready is False
    with pytest.raises(SpdConductorGraphError, match="TRACE_WIDTH_MISSING"):
        graph.require_electrical_ready()
    with pytest.raises(TypeError):
        graph.topology_adjacency["new"] = ()  # type: ignore[index]


def test_layer_filter_reports_cross_boundary_without_inventing_path(tmp_path: Path) -> None:
    graph = extract_spd_conductor_graph(
        _source(tmp_path),
        net_names=("VDD/0",),
        layer_range=("Signal$TOP", "Signal$PWR"),
    )

    assert [item.source_id for item in graph.nodes] == ["NodeA", "NodeB", "NodeC", "NodeD", "NodeE"]
    assert [item.source_id for item in graph.vias] == ["ViaAC", "ViaBD"]
    assert any(item.code == "EDGE_LEAVES_LAYER_FILTER" for item in graph.diagnostics)
    assert graph.statistics["nodes_outside_layer_filter"] == 1


def test_request_requires_consistent_site_and_known_layers(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(SpdConductorGraphError, match="does not belong"):
        extract_spd_conductor_graph(source, net_names=("VDD/0",), site="1")
    with pytest.raises(SpdConductorGraphError, match="not in the SPD stack-up"):
        extract_spd_conductor_graph(source, net_names=("VDD/0",), layer_names=("Signal$NOPE",))


def test_via_with_reversed_physical_interval_is_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(
            "ViaBD::VDD/0 UpperNode = NodeB!!2::VDD/0 LowerNode = NodeD!!4::VDD/0",
            "ViaBD::VDD/0 UpperNode = NodeD!!4::VDD/0 LowerNode = NodeB!!2::VDD/0",
        ),
        encoding="utf-8",
    )
    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))

    assert "ViaBD" not in {item.source_id for item in graph.vias}
    assert any(item.code == "VIA_INTERVAL_ORDER_INVALID" for item in graph.diagnostics)


def test_via_missing_endpoint_paddef_is_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(
            ".PadDef Signal$GND\nRegular Circle 0.04mm\n.EndPadDef\n",
            "",
        ),
        encoding="utf-8",
    )
    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))

    assert "ViaEF" not in {item.source_id for item in graph.vias}
    assert any(item.code == "VIA_ENDPOINT_PADDEF_MISSING" for item in graph.diagnostics)


def test_extraction_is_deterministic(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = extract_spd_conductor_graph(source, net_names=("VDD/0",))
    second = extract_spd_conductor_graph(source, net_names=("VDD/0",))

    assert first == second


def test_certificate_separates_exact_source_bytes_from_canonical_topology(
    tmp_path: Path,
) -> None:
    """Record order is not an electrical topology change, but is source evidence."""

    original = tmp_path / "original.spd"
    reordered = tmp_path / "reordered.spd"
    original.write_text(SPD, encoding="utf-8")
    node_lines = [line for line in SPD.splitlines() if line.startswith("Node")]
    trace_lines = [line for line in SPD.splitlines() if line.startswith("Trace")]
    via_lines = [line for line in SPD.splitlines() if line.startswith("Via")]
    reordered_text = SPD
    reordered_text = reordered_text.replace("\n".join(node_lines), "\n".join(reversed(node_lines)))
    reordered_text = reordered_text.replace("\n".join(trace_lines), "\n".join(reversed(trace_lines)))
    reordered_text = reordered_text.replace("\n".join(via_lines), "\n".join(reversed(via_lines)))
    reordered.write_text(reordered_text, encoding="utf-8")

    first = extract_spd_conductor_graph(original, net_names=("VDD/0",))
    second = extract_spd_conductor_graph(reordered, net_names=("VDD/0",))

    assert first.certificate.source_sha256 != second.certificate.source_sha256
    assert first.certificate.topology_sha256 == second.certificate.topology_sha256
    assert first.certificate.compiler_version == "conductor-graph/v1"
    assert "POLYGON_CONNECTIVITY_NOT_INCLUDED" in first.certificate.unresolved_codes
    assert "via:vdd/0:viaac" in first.certificate.owner_ledger
    with pytest.raises(TypeError):
        first.certificate.owner_ledger["replacement"] = "not allowed"  # type: ignore[index]


def test_explicit_endpoint_net_mismatch_is_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(
            "ViaAC::VDD/0 UpperNode = NodeA!!1::VDD/0",
            "ViaAC::VDD/0 UpperNode = NodeA!!1::OTHER/0",
        ),
        encoding="utf-8",
    )
    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))

    assert "ViaAC" not in {item.source_id for item in graph.vias}
    assert any(item.code == "VIA_ENDPOINT_NET_MISMATCH" for item in graph.diagnostics)


@pytest.mark.parametrize(
    ("source_width", "diagnostic"),
    (("0mm", "TRACE_WIDTH_NONPOSITIVE"), ("-0.1mm", "TRACE_WIDTH_NONPOSITIVE"), ("nan", "TRACE_WIDTH_INVALID")),
)
def test_invalid_trace_width_remains_topology_only(
    tmp_path: Path, source_width: str, diagnostic: str
) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace("Width = 0.10mm", f"Width = {source_width}"),
        encoding="utf-8",
    )
    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))
    trace = next(item for item in graph.traces if item.source_id == "TraceAB")

    assert trace.electrically_resolved is False
    assert trace.ending_node_id.casefold() in {
        key.rsplit("\x1f", 1)[1]
        for key in graph.topology_adjacency[
            next(item for item in graph.nodes if item.source_id == "NodeA").key
        ]
    }
    node_a = next(item for item in graph.nodes if item.source_id == "NodeA")
    node_b = next(item for item in graph.nodes if item.source_id == "NodeB")
    assert node_b.key not in graph.electrical_adjacency[node_a.key]
    assert any(item.code == diagnostic for item in graph.diagnostics)


def test_unresolved_metal_model_blocks_all_r_inputs(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(".MetalModel COPPER", ".MetalModel NOT_COPPER"),
        encoding="utf-8",
    )
    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))

    assert all(
        item.conductivity_s_m is None
        for item in graph.layers
        if item.kind == "CONDUCTOR"
    )
    assert all(not item.electrically_resolved for item in graph.vias)
    assert all(not item.electrically_resolved for item in graph.traces)
    assert any(
        item.code == "CONDUCTOR_CONDUCTIVITY_UNRESOLVED"
        for item in graph.diagnostics
    )
    with pytest.raises(SpdConductorGraphError, match="CONDUCTOR_CONDUCTIVITY_UNRESOLVED"):
        graph.require_electrical_ready()


def test_malformed_selected_edges_and_bad_coordinates_are_diagnosed(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(
            "NodeA!!1::VDD/0 X = 0mm",
            "NodeA!!1::VDD/0 X = nope",
        ).replace(
            "TraceAB::VDD/0 StartingNode = NodeA!!1::VDD/0 EndingNode = NodeB!!2::VDD/0 Width = 0.10mm",
            "TraceAB::VDD/0 StartingNode = NodeA!!1::VDD/0 Width = 0.10mm",
        ).replace(
            "ViaBD::VDD/0 UpperNode = NodeB!!2::VDD/0 LowerNode = NodeD!!4::VDD/0 PadStack = MICRO",
            "ViaBD::VDD/0 UpperNode = NodeB!!2::VDD/0 PadStack = MICRO",
        ),
        encoding="utf-8",
    )
    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))
    codes = {item.code for item in graph.diagnostics}

    assert "NODE_COORDINATES_INVALID" in codes
    assert "MALFORMED_SELECTED_TRACE" in codes
    assert "MALFORMED_SELECTED_VIA" in codes


def test_duplicate_layer_and_padstack_are_fail_closed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    duplicate_padstack = """.PadStackDef MICRO 0.025mm Material = COPPER
.PadDef Signal$TOP
Regular Circle 0.05mm
.EndPadDef
.EndPadStackDef
"""
    source.write_text(
        SPD.replace(
            "Signal$TOP Thickness = 20um Material = COPPER\n",
            "Signal$TOP Thickness = 20um Material = COPPER\nSignal$TOP Thickness = 20um Material = COPPER\n",
        ).replace(
            "* Material description lines\n",
            duplicate_padstack + "* Material description lines\n",
        ),
        encoding="utf-8",
    )
    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))
    codes = {item.code for item in graph.diagnostics}

    assert "DUPLICATE_LAYER" in codes
    assert "DUPLICATE_PADSTACK" in codes
    assert not graph.vias
    assert graph.electrical_ready is False


@pytest.mark.parametrize("explicit_value", ("0", "-1", "NaN"))
def test_invalid_explicit_layer_conductivity_never_uses_material_fallback(
    tmp_path: Path, explicit_value: str
) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(
            "Signal$TOP Thickness = 20um Material = COPPER",
            f"Signal$TOP Thickness = 20um Material = COPPER Conductivity = {explicit_value}",
        ),
        encoding="utf-8",
    )
    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))
    top = next(item for item in graph.layers if item.name == "Signal$TOP")

    assert top.conductivity_s_m is None
    assert any(
        item.code == "EXPLICIT_LAYER_CONDUCTIVITY_INVALID"
        for item in graph.diagnostics
    )
    assert any(
        not item.electrically_resolved
        for item in graph.vias
        if item.upper_layer == "Signal$TOP"
    )
    assert any(
        not item.electrically_resolved
        for item in graph.traces
        if item.layer == "Signal$TOP"
    )


def test_bad_coordinate_incident_edge_is_not_misreported_as_filter_exit(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace("NodeA!!1::VDD/0 X = 0mm", "NodeA!!1::VDD/0 X = nope"),
        encoding="utf-8",
    )
    graph = extract_spd_conductor_graph(
        source,
        net_names=("VDD/0",),
        layer_range=("Signal$TOP", "Signal$PWR"),
    )

    invalid_messages = [
        item.message
        for item in graph.diagnostics
        if item.code == "EDGE_ENDPOINT_NODE_INVALID"
    ]
    exit_messages = [
        item.message
        for item in graph.diagnostics
        if item.code == "EDGE_LEAVES_LAYER_FILTER"
    ]
    assert any("ViaAC" in message or "TraceAB" in message for message in invalid_messages)
    assert not any("ViaAC" in message or "TraceAB" in message for message in exit_messages)


def test_trace_continuation_width_is_electrically_resolved(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(
            "TraceDE::VDD/0 StartingNode = NodeD!!4::VDD/0 "
            "EndingNode = NodeE!!5::VDD/0 Width = 0.08mm",
            "TraceDE::VDD/0 StartingNode = NodeD!!4::VDD/0 "
            "EndingNode = NodeE!!5::VDD/0\n+ Width = 0.08mm",
        ),
        encoding="utf-8",
    )

    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))
    trace = next(item for item in graph.traces if item.source_id == "TraceDE")
    node_d = next(item for item in graph.nodes if item.source_id == "NodeD")
    node_e = next(item for item in graph.nodes if item.source_id == "NodeE")

    assert trace.width_um == pytest.approx(80.0)
    assert trace.electrically_resolved is True
    assert node_e.key in graph.electrical_adjacency[node_d.key]
    assert graph.statistics["logical_trace_records"] == 3


@pytest.mark.parametrize(
    ("replacement", "diagnostic"),
    (
        (
            "TraceAB::VDD/0 StartingNode = NodeA!!1::VDD/0 "
            "EndingNode = NodeB!!2::VDD/0 Width = 0.10mm\n"
            "+ Width = 0.20mm",
            "TRACE_WIDTH_DUPLICATE",
        ),
        (
            "TraceAB::VDD/0 StartingNode = NodeA!!1::VDD/0 "
            "EndingNode = NodeB!!2::VDD/0\n+ Unsupported = 0.10mm",
            "TRACE_CONTINUATION_UNKNOWN",
        ),
    ),
)
def test_unresolved_logical_trace_geometry_stays_topology_only(
    tmp_path: Path,
    replacement: str,
    diagnostic: str,
) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(
            "TraceAB::VDD/0 StartingNode = NodeA!!1::VDD/0 "
            "EndingNode = NodeB!!2::VDD/0 Width = 0.10mm",
            replacement,
        ),
        encoding="utf-8",
    )

    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))
    trace = next(item for item in graph.traces if item.source_id == "TraceAB")
    node_a = next(item for item in graph.nodes if item.source_id == "NodeA")
    node_b = next(item for item in graph.nodes if item.source_id == "NodeB")

    assert trace.width_um is None
    assert trace.electrically_resolved is False
    assert node_b.key in graph.topology_adjacency[node_a.key]
    assert node_b.key not in graph.electrical_adjacency[node_a.key]
    assert diagnostic in trace.electrical_blockers
    assert diagnostic in graph.certificate.unresolved_codes


def test_orphan_trace_continuation_rejects_section_framing(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(
            "* Trace description lines\n",
            "* Trace description lines\n+ Width = 0.10mm\n",
        ),
        encoding="utf-8",
    )

    with pytest.raises(SpdConductorGraphError, match="ORPHAN_TRACE_CONTINUATION"):
        extract_spd_conductor_graph(source, net_names=("VDD/0",))


def test_cross_layer_trace_is_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source.write_text(
        SPD.replace(
            "NodeE!!5::VDD/0 X = 2mm Y = 0mm Layer = Signal$PWR",
            "NodeE!!5::VDD/0 X = 2mm Y = 0mm Layer = Signal$GND",
        ),
        encoding="utf-8",
    )

    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))

    assert "TraceDE" not in {item.source_id for item in graph.traces}
    assert any(item.code == "TRACE_CROSSES_LAYERS" for item in graph.diagnostics)


def test_duplicate_trace_id_rejects_both_logical_records(tmp_path: Path) -> None:
    source = _source(tmp_path)
    duplicate = (
        "TraceAB::VDD/0 StartingNode = NodeC!!3::VDD/0 "
        "EndingNode = NodeD!!4::VDD/0 Width = 0.12mm\n"
    )
    source.write_text(
        SPD.replace(
            "* Via description lines\n",
            duplicate + duplicate + "* Via description lines\n",
        ),
        encoding="utf-8",
    )

    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))

    assert "TraceAB" not in {item.source_id for item in graph.traces}
    assert any(item.code == "DUPLICATE_TRACE_ID" for item in graph.diagnostics)
    assert graph.statistics["rejected_duplicate_trace_records"] == 3


def test_malformed_duplicate_trace_id_also_rejects_valid_record(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    malformed_duplicate = (
        "TraceAB::VDD/0 StartingNode = NodeC!!3::VDD/0 Width = 0.12mm\n"
    )
    source.write_text(
        SPD.replace(
            "* Via description lines\n",
            malformed_duplicate + "* Via description lines\n",
        ),
        encoding="utf-8",
    )

    graph = extract_spd_conductor_graph(source, net_names=("VDD/0",))

    assert "TraceAB" not in {item.source_id for item in graph.traces}
    assert graph.statistics["malformed_selected_trace_records"] == 1
    assert graph.statistics["rejected_duplicate_trace_records"] == 2
    assert sum(item.code == "DUPLICATE_TRACE_ID" for item in graph.diagnostics) == 1
