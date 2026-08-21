from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from spd_decap_pi._core.io.reduced_conductor import (
    ReducedConductorCompilationCancelled,
    ReducedConductorGraphError,
    compile_reduced_conductor_graph,
)


SPD = """Reduced conductor fixture
* Layer description lines
Signal$TOP Thickness = 20um Material = COPPER
Medium$D1 Thickness = 35um Material = ABF
Signal$PWR Thickness = 20um Material = COPPER
* Node description lines
NodeA!!1::VDD/0 X = 0mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeB!!2::VDD/0 X = 1mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeC!!3::VDD/0 X = 0mm Y = 0mm Layer = Signal$PWR PadStack = MICRO
NodeD!!4::VDD/0 X = 1mm Y = 0mm Layer = Signal$PWR PadStack = MICRO
NodeE!!5::VDD/0 X = 2mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeF!!6::VDD/0 X = 3mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
NodeG!!7::VDD/0 X = 4mm Y = 0mm Layer = Signal$TOP PadStack = MICRO
* Trace description lines
TraceEF::VDD/0 StartingNode = NodeE!!5::VDD/0 EndingNode = NodeF!!6::VDD/0
* Via description lines
Via1::VDD/0 UpperNode = NodeA!!1::VDD/0 LowerNode = NodeC!!3::VDD/0 PadStack = MICRO
Via2::VDD/0 UpperNode = NodeB!!2::VDD/0 LowerNode = NodeD!!4::VDD/0 PadStack = MICRO
Via3::VDD/0 UpperNode = NodeE!!5::VDD/0 LowerNode = NodeC!!3::VDD/0 PadStack = MICRO
* PadStack collection description lines
.PadStackDef MICRO 0.025mm Material = COPPER
.PadDef Signal$TOP
Regular Circle 0.05mm
.EndPadDef
.PadDef Signal$PWR
Regular Box 0.08mm 0.06mm
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


def _source(tmp_path: Path, text: str = SPD) -> Path:
    path = tmp_path / "reduced.spd"
    path.write_text(text, encoding="utf-8")
    return path


def _island(
    _net: str,
    _layer: str,
    node_id: str,
    _x_um: float,
    _y_um: float,
) -> str | None:
    return {
        "NodeA": "top-ab",
        "NodeB": "top-ab",
        "NodeC": "pwr-cd",
        "NodeD": "pwr-cd",
        # These intentionally differ: their Trace, not artwork contact, joins
        # them as one disclosed topology-only conductor.
        "NodeE": "top-e",
        "NodeF": "top-f",
        # None proves no island contact; the source Node remains explicit.
        "NodeG": None,
    }[node_id]


def _node_for_source(graph: object, source_id: str):
    return next(
        node
        for node in graph.nodes
        if source_id in node.source_node_ids
    )


def test_surface_islands_and_traces_form_only_disclosed_ideal_unions(
    tmp_path: Path,
) -> None:
    progress: list[int] = []
    graph = compile_reduced_conductor_graph(
        _source(tmp_path),
        requested_nets=("VDD/0",),
        surface_island_for_node=_island,
        required_via_ids=("Via1", "Via2"),
        progress=lambda value, _message: progress.append(value),
    )

    node_a = _node_for_source(graph, "NodeA")
    node_b = _node_for_source(graph, "NodeB")
    node_e = _node_for_source(graph, "NodeE")
    node_f = _node_for_source(graph, "NodeF")
    node_g = _node_for_source(graph, "NodeG")

    assert node_a.reduced_node_id == node_b.reduced_node_id
    assert node_e.reduced_node_id == node_f.reduced_node_id
    assert node_a.reduced_node_id != node_e.reduced_node_id
    assert node_e.reduced_node_id != node_g.reduced_node_id
    assert {item.island_id for item in node_e.surface_contacts} == {
        "top-e",
        "top-f",
    }
    assert node_g.surface_contacts == ()
    assert graph.statistics["source_nodes"] == 7
    assert graph.statistics["reduced_nodes"] == 4
    assert graph.ideal_trace_unions[0].mode == "topology_only_ideal"
    assert graph.ideal_trace_unions[0].reduced_node_id == node_e.reduced_node_id
    assert graph.certificate.approximation_codes == (
        "TRACE_TOPOLOGY_ONLY_IDEAL",
    )
    assert any(
        item.code == "TRACE_TOPOLOGY_ONLY_IDEAL"
        for item in graph.diagnostics
    )
    assert progress[0] == 0
    assert progress[-1] == 100
    assert progress == sorted(set(progress))


def test_finite_vias_aggregate_by_reduced_endpoints_padstack_and_span(
    tmp_path: Path,
) -> None:
    graph = compile_reduced_conductor_graph(
        _source(tmp_path),
        requested_nets=("VDD/0",),
        surface_island_for_node=_island,
        required_via_ids=("via1", "VIA2", "Via3"),
    )

    assert len(graph.finite_via_branches) == 2
    aggregate = next(
        item for item in graph.finite_via_branches if item.count == 2
    )
    assert aggregate.source_via_ids == ("Via1", "Via2")
    assert aggregate.mode == "finite_source_evidence"
    assert aggregate.padstack.name == "MICRO"
    assert aggregate.padstack.material == "COPPER"
    assert aggregate.padstack.conductivity_s_m == pytest.approx(5.959e7)
    assert aggregate.interval_layers == (
        "Signal$TOP",
        "Medium$D1",
        "Signal$PWR",
    )
    assert aggregate.upper_conductor_thickness_um == pytest.approx(20.0)
    assert aggregate.lower_conductor_thickness_um == pytest.approx(20.0)
    via1 = graph.required_via("VIA1")
    via2 = graph.required_via("via2")
    assert via1.branch_id == aggregate.branch_id == via2.branch_id
    assert via1.upper_reduced_node_id == via2.upper_reduced_node_id
    assert via1.lower_reduced_node_id == via2.lower_reduced_node_id
    assert graph.statistics["aggregated_source_vias"] == 1
    assert graph.certificate.owner_ledger["via:vdd/0:via1"] == aggregate.branch_id
    with pytest.raises(TypeError):
        graph.certificate.owner_ledger["via:vdd/0:via1"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        graph.required_via_endpoints["Via1"] = via1  # type: ignore[index]


def test_required_via_missing_or_ambiguous_is_fail_closed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(ReducedConductorGraphError, match="missing or rejected"):
        compile_reduced_conductor_graph(
            source,
            requested_nets=("VDD/0",),
            surface_island_for_node=_island,
            required_via_ids=("ViaMissing",),
        )

    ambiguous = SPD.replace(
        "* Trace description lines\n",
        "NodeH!!8::VSS/0 X = 5mm Y = 0mm Layer = Signal$TOP PadStack = MICRO\n"
        "NodeI!!9::VSS/0 X = 5mm Y = 0mm Layer = Signal$PWR PadStack = MICRO\n"
        "* Trace description lines\n",
    ).replace(
        "* PadStack collection description lines\n",
        "Via1::VSS/0 UpperNode = NodeH!!8::VSS/0 LowerNode = NodeI!!9::VSS/0 PadStack = MICRO\n"
        "* PadStack collection description lines\n",
    )
    ambiguous_source = tmp_path / "ambiguous.spd"
    ambiguous_source.write_text(ambiguous, encoding="utf-8")
    with pytest.raises(ReducedConductorGraphError, match="ambiguous"):
        compile_reduced_conductor_graph(
            ambiguous_source,
            requested_nets=("VDD/0", "VSS/0"),
            surface_island_for_node=lambda net, layer, node_id, _x, _y: (
                f"{net}:{layer}:{node_id}"
            ),
            required_via_ids=("Via1",),
        )


def test_incomplete_via_material_evidence_is_fail_closed(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        SPD.replace(".MetalModel COPPER", ".MetalModel NOT_COPPER"),
    )

    with pytest.raises(
        ReducedConductorGraphError,
        match="source conductor evidence is incomplete",
    ):
        compile_reduced_conductor_graph(
            source,
            requested_nets=("VDD/0",),
            surface_island_for_node=_island,
            required_via_ids=("Via1",),
        )


def test_cancellation_is_checked_during_surface_resolution(tmp_path: Path) -> None:
    calls = 0

    def resolver(
        net: str,
        layer: str,
        node_id: str,
        x_um: float,
        y_um: float,
    ) -> str | None:
        nonlocal calls
        calls += 1
        return _island(net, layer, node_id, x_um, y_um)

    with pytest.raises(
        ReducedConductorCompilationCancelled,
        match="cancelled",
    ):
        compile_reduced_conductor_graph(
            _source(tmp_path),
            requested_nets=("VDD/0",),
            surface_island_for_node=resolver,
            is_cancelled=lambda: calls >= 2,
        )
    assert calls == 2


def test_topology_identity_is_deterministic_and_integrity_is_verifiable(
    tmp_path: Path,
) -> None:
    first_source = _source(tmp_path)
    reordered_source = tmp_path / "reordered.spd"
    node_lines = [line for line in SPD.splitlines() if line.startswith("Node")]
    trace_lines = [line for line in SPD.splitlines() if line.startswith("Trace")]
    via_lines = [line for line in SPD.splitlines() if line.startswith("Via")]
    reordered = SPD.replace("\n".join(node_lines), "\n".join(reversed(node_lines)))
    reordered = reordered.replace(
        "\n".join(trace_lines), "\n".join(reversed(trace_lines))
    )
    reordered = reordered.replace("\n".join(via_lines), "\n".join(reversed(via_lines)))
    reordered_source.write_text(reordered, encoding="utf-8")

    first = compile_reduced_conductor_graph(
        first_source,
        requested_nets=("VDD/0",),
        surface_island_for_node=_island,
        required_via_ids=("Via1", "Via2"),
    )
    second = compile_reduced_conductor_graph(
        reordered_source,
        requested_nets=("VDD/0",),
        surface_island_for_node=_island,
        required_via_ids=("Via1", "Via2"),
    )

    assert first.certificate.compiler_version == "reduced-conductor/v1"
    assert first.certificate.source_sha256 != second.certificate.source_sha256
    assert first.certificate.topology_sha256 == second.certificate.topology_sha256
    first.require_integrity()
    first.require_source_integrity()
    corrupted_view = replace(first, nodes=first.nodes[:-1])
    with pytest.raises(ReducedConductorGraphError, match="certificate"):
        corrupted_view.require_integrity()

    first_source.write_text(SPD + "* changed\n", encoding="utf-8")
    with pytest.raises(ReducedConductorGraphError, match="source size changed"):
        first.require_source_integrity()


def test_invalid_surface_island_identity_is_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ReducedConductorGraphError, match="non-empty-string"):
        compile_reduced_conductor_graph(
            _source(tmp_path),
            requested_nets=("VDD/0",),
            surface_island_for_node=lambda *_args: "",
        )
