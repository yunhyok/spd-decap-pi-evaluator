from __future__ import annotations

import mmap
import os
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.domain import (
    MixedReferenceGroundWitness,
    PinKind,
    TerminalKind,
)
from spd_decap_pi._core.io import spd as spd_io
from spd_decap_pi._core.io.spd import (
    SpdImportError,
    _parse_netlist,
    analyze_spd,
    recover_spd_via_paths,
    recover_spd_ground_reachability,
)
from spd_decap_pi._core.io.shared_pad import SpdViaLanding
from spd_decap_pi._core.services import (
    _evaluation_view,
    build_spd_import_plan,
    create_workspace_state,
)
from spd_decap_pi._core.solver.evaluator import EvaluationError, _planes_from_project


MINI_SPD = """Title tiny SPD
.Package $Package
* Shape description lines
.Shape Signal$GNDpkgshape
Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm
.EndShape
.Shape Signal$PWRpkgshape
Polygon2::VDD_CORE/0+ -4mm -3mm 4mm -3mm
+ 4mm 3mm -4mm 3mm
Polygon3::VDD_CORE/0- ViaHole_A Sub-element -0.2mm -0.2mm 0.2mm -0.2mm 0.2mm 0.2mm -0.2mm 0.2mm
Circle4::VDD_CORE/0- ViaHole_A Sub-element 1mm 1mm 0.06mm
Polygon5::VDD_CORE/0+ Sub-element 2mm 2mm 2.2mm 2mm 2.2mm 2.2mm 2mm 2.2mm
.EndShape
* Layer description lines
Signal$TOP Thickness = 20u Material = COPPER
Medium$D1 Thickness = 0.10mm Material = ABF
Signal$PWR Thickness = 20u Material = COPPER
Medium$D2 Thickness = 100um Material = ABF
Signal$GND Thickness = 20u Material = COPPER
* Node description lines
Node1!!101::VDD_CORE/0 X = 0mm Y = 0mm Layer = Signal$TOP PadStack = DUT
Node2!!102::DGND X = 0.1mm Y = 0mm Layer = Signal$TOP PadStack = DUT
Node3!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = CAP
Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP
Node5!!1::VDD_DROP/0 X = 3mm Y = 2mm Layer = Signal$TOP PadStack = CAP
Node6!!2::DGND X = 3.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP
* Via description lines
Via1::VDD_CORE/0 UpperNode = Node1 LowerNode = Node3 PadStack = DR-0102_60
Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60
* PadStack collection description lines
.PadStackDef DR-0102_60 0.02mm Material = COPPER
.PadDef Signal$TOP
Regular Circle 0.03mm
.EndPadDef
.PadDef Signal$PWR
Regular Circle 0.03mm
.EndPadDef
.EndPadStackDef
.PadStackDef CAP 0.00mm Material = COPPER
.PadDef Signal$TOP
Regular Square 0.10mm
.EndPadDef
.EndPadStackDef
* Material description lines
.Material
.DielectricModel ABF
*Frequency(MHz) Permittivity LossTangent
1 3.4 0.005
1000 3.3 0.004
.EndDielectricModel
.MetalModel COPPER
*Temperature(C) Conductivity(S/m)
20 5.959e7
.EndMetalModel
.EndMaterial
* Circuit description lines
.PartialCkt CAP_0402_100NF ExtNode = 1 2
R1 1 X 0.01
L1 X Y 0.2n
C1 Y 2 100n
.EndPartialCkt
.PartialCkt CAP_0402_100NF_NOT_MOUNTED ExtNode = 1 2
.EndPartialCkt
* Component description lines
.Connect SITE0 DUT Checked = 1
101 $Package.Node1!!101::VDD_CORE/0
102 $Package.Node2!!102::DGND
.EndC
.Connect C1 CAP_0402_100NF Checked = 1
1 $Package.Node3!!1::VDD_CORE/0
2 $Package.Node4!!2::DGND
.EndC
.Connect C2 CAP_0402_100NF Checked = 1
1 $Package.Node5!!1::VDD_DROP/0
2 $Package.Node6!!2::DGND
.EndC
.Component C1 1.1mm 2mm Rotation = 90 StartLayer = Signal$TOP
.Component C2 3.1mm 2mm StartLayer = Signal$TOP
.Part DUT Tags = "IO"
.Component SITE0 0mm 0mm StartLayer = Signal$TOP AttachLayer = TopAir
* Net description lines
.NetList
DGND -> GroundNets Voltage = 0
VDD_CORE/0 -> PowerNets Voltage = 0
VDD_DROP/0::Unselected||DropShape
.EndNetList
.EndPackage
"""


def test_mixed_reference_ground_reachability_accepts_branching_via_graph(
    tmp_path: Path,
) -> None:
    """Return reachability permits branches even though RL recovery cannot."""

    source = tmp_path / "branching-ground.spd"
    payload = MINI_SPD.replace(
        "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
        "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n"
        "Node7!!7::DGND X = 1.2mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60\n"
        "Node8!!8::DGND X = 1.3mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60\n"
        "Node9!!9::DGND X = 1.3mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60",
    ).replace(
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
        "Via7::DGND UpperNode = Node4 LowerNode = Node7 PadStack = DR-0102_60\n"
        "Via8::DGND UpperNode = Node4 LowerNode = Node8 PadStack = DR-0102_60\n"
        "Via9::DGND UpperNode = Node8 LowerNode = Node9 PadStack = DR-0102_60",
    )
    source.write_text(payload, encoding="ascii")
    landing = SpdViaLanding(
        via_id="Via2", net="DGND", endpoint_node_id="Node4", x_um=1200,
        y_um=2000, padstack="DR-0102_60",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        target_node_predicate=lambda _net, _layer, _node, x_um, y_um: (
            x_um == 1300 and y_um == 2000
        ),
    )
    assert result.reaches(landing, "Signal$GND")
    assert result.statistics["reachable"] == 1
    assert result.statistics["node_section_passes"] == 1
    assert result.statistics["via_section_passes"] == 1
    outside = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        target_node_predicate=lambda *_args: False,
    )
    assert not outside.reaches(landing, "Signal$GND")
    # ``contains`` rather than ``covers`` is the production boundary policy:
    # an artwork-edge target cannot be promoted into a certified return path.
    boundary = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        target_node_predicate=lambda _net, _layer, _node, x_um, _y_um: x_um > 1300,
    )
    assert not boundary.reaches(landing, "Signal$GND")


def test_mixed_reference_ground_reachability_fails_closed_when_target_unreachable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unreachable-ground.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    landing = SpdViaLanding(
        via_id="Via2", net="DGND", endpoint_node_id="Node4", x_um=1200,
        y_um=2000, padstack="DR-0102_60",
    )
    result = recover_spd_ground_reachability(
        source, landings=(landing,), target_layers_by_net={"DGND": ("Signal$GND",)}
    )
    assert not result.reaches(landing, "Signal$GND")
    assert result.statistics["unreachable"] == 1


def _recoverable_via_source(
    tmp_path: Path,
    *,
    node_lines: str,
    via_lines: str,
    trace_lines: str = "",
    padstack_defs: str = "",
) -> tuple[Path, object]:
    """Build one small SPD while keeping the production section grammar."""

    trace_section = (
        "* Trace description lines\n" + trace_lines.rstrip() + "\n"
        if trace_lines
        else ""
    )
    payload = MINI_SPD.replace(
        "* Via description lines",
        node_lines.rstrip() + "\n" + trace_section + "* Via description lines",
    ).replace(
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
        + via_lines.rstrip(),
    )
    if padstack_defs:
        payload = payload.replace(
            "* Material description lines",
            padstack_defs.rstrip() + "\n* Material description lines",
        )
    source = tmp_path / "via-recovery.spd"
    source.write_text(payload, encoding="ascii")
    return source, analyze_spd(source)


def _recover_power_path(source: Path, analysis: object, **kwargs):
    landing = SimpleNamespace(
        via_id="ViaRoute",
        net="VDD_CORE/0",
        endpoint_node_id="Node10",
        x_um=1_000.0,
        y_um=2_000.0,
        padstack="DR-0102_60",
    )
    return recover_spd_via_paths(
        source,
        landings=(landing,),
        target_layers_by_net={"VDD_CORE/0": ("Signal$PWR",)},
        stackup_layers=analysis.stackup_layers,
        padstacks=analysis.padstacks,
        top_layer="Signal$TOP",
        **kwargs,
    )


def test_padstack_material_is_retained_for_microvia_classification(tmp_path: Path) -> None:
    source = tmp_path / "material.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    analysis = analyze_spd(source)

    padstack = next(item for item in analysis.padstacks if item.name == "DR-0102_60")
    assert padstack.material == "COPPER"


@pytest.mark.parametrize("reversed_endpoints", [False, True])
@pytest.mark.parametrize(
    "target_padstack_suffix",
    ("", " PadStack = NODE_FEATURE"),
    ids=("target-node-has-no-padstack", "target-node-has-conflicting-padstack"),
)
def test_recover_spd_via_path_retains_target_pad_and_ignores_side_trace(
    tmp_path: Path,
    reversed_endpoints: bool,
    target_padstack_suffix: str,
) -> None:
    node_lines = f"""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1.25mm Y = 2.5mm Layer = Signal$PWR{target_padstack_suffix}
Node99!!1::VDD_CORE/0 X = 2mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
"""
    endpoints = (
        "UpperNode = Node11::VDD_CORE/0 LowerNode = Node10::VDD_CORE/0"
        if reversed_endpoints
        else "UpperNode = Node10::VDD_CORE/0 LowerNode = Node11::VDD_CORE/0"
    )
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=node_lines,
        via_lines=f"ViaRoute::VDD_CORE/0 {endpoints} PadStack = DR-0102_60",
        trace_lines=(
            "TraceSide::VDD_CORE/0 StartingNode = Node10::VDD_CORE/0 "
            "EndingNode = Node99::VDD_CORE/0 Width = 0.10mm"
        ),
    )

    recovery = _recover_power_path(source, analysis)

    assert recovery.statistics == {
        "requested": 1,
        "recovered": 1,
        "fallback": 0,
        "segments": 1,
        "trace_components_indexed": 1,
        "alternate_exit_cache_entries": 1,
        "alternate_exit_trace_nodes_indexed": 2,
        "alternate_exit_via_edges_indexed": 1,
        "path_node_section_passes": 1,
        "alternate_exit_node_section_passes": 1,
        "alternate_exit_nodes_resolved": 3,
    }
    evidence = recovery.evidence_for("ViaRoute", "Signal$PWR")
    assert evidence is not None
    assert evidence.target_node_id == "Node11"
    assert evidence.target_padstack == "DR-0102_60"
    assert evidence.target_pad_kind == "CIRCLE"
    assert (evidence.target_x_um, evidence.target_y_um) == (1_250.0, 2_500.0)
    assert (evidence.target_pad_width_um, evidence.target_pad_height_um) == (
        60.0,
        60.0,
    )
    assert len(evidence.segments) == 1
    assert evidence.segments[0].via_id == "ViaRoute"
    # The target Node's feature label is not a Via landing.  The final segment
    # remains authoritative even when its source padstack differs or the Node
    # has no PadStack at all.
    assert evidence.segments[0].padstack == "DR-0102_60"
    assert evidence.segments[0].rotation_degrees == 0.0
    # Conductor-centre depth, not an ordinal layer-index span.
    assert evidence.segments[0].length_um == pytest.approx(120.0)


def test_direct_via_with_trace_to_alternate_via_marks_parallel_mesh(
    tmp_path: Path,
) -> None:
    """The alternate Via's far node starts outside the recovery node cache."""

    irrelevant_vias = "\n".join(
        f"ViaIrrelevant{index}::VDD_CORE/0 UpperNode = JunkA{index} "
        f"LowerNode = JunkB{index} PadStack = DR-0102_60"
        for index in range(2_000)
    )
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60
Node12!!1::VDD_CORE/0 X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node13!!1::VDD_CORE/0 X = 1.2mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60
""",
        via_lines="""
ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60
ViaAlternate::VDD_CORE/0 UpperNode = Node12::VDD_CORE/0 LowerNode = Node13::VDD_CORE/0 PadStack = DR-0102_60
""" + irrelevant_vias,
        trace_lines=(
            "TraceMesh::VDD_CORE/0 StartingNode = Node10::VDD_CORE/0 "
            "EndingNode = Node12::VDD_CORE/0 Width = 0.10mm"
        ),
    )

    landings = tuple(
        SimpleNamespace(
            via_id=via_id,
            net="VDD_CORE/0",
            endpoint_node_id=node_id,
            x_um=x_um,
            y_um=2_000.0,
            padstack="DR-0102_60",
        )
        for via_id, node_id, x_um in (
            ("ViaRoute", "Node10", 1_000.0),
            ("ViaAlternate", "Node12", 1_200.0),
        )
    )
    recovery = recover_spd_via_paths(
        source,
        landings=landings,
        target_layers_by_net={"VDD_CORE/0": ("Signal$PWR",)},
        stackup_layers=analysis.stackup_layers,
        padstacks=analysis.padstacks,
        top_layer="Signal$TOP",
    )
    evidence = recovery.evidence_for("ViaRoute", "Signal$PWR")

    assert evidence is not None
    assert evidence.trace_hops == 0
    assert evidence.trace_alternate_exit is True
    assert [item.via_id for item in evidence.segments] == ["ViaRoute"]
    assert recovery.evidence_for(
        "ViaAlternate", "Signal$PWR"
    ).trace_alternate_exit is True
    assert recovery.statistics["trace_components_indexed"] == 1
    assert recovery.statistics["alternate_exit_cache_entries"] == 1
    assert recovery.statistics["alternate_exit_trace_nodes_indexed"] == 2
    # The 2,000 additional same-NET Vias are not Trace-incident and therefore
    # never enter the memory-heavy alternate-exit neighbor index.
    assert recovery.statistics["alternate_exit_via_edges_indexed"] == 2


def test_many_trace_components_share_one_compact_alternate_node_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component_count = 32
    node_lines = "\n".join(
        line
        for index in range(component_count)
        for line in (
            f"NodeRouteTop{index}!!1::VDD_CORE/0 X = {index}mm Y = 2mm "
            "Layer = Signal$TOP PadStack = DR-0102_60",
            f"NodeRoutePwr{index}!!1::VDD_CORE/0 X = {index}mm Y = 2mm "
            "Layer = Signal$PWR PadStack = DR-0102_60",
            f"NodeSideTop{index}!!1::VDD_CORE/0 X = {index}.1mm Y = 2mm "
            "Layer = Signal$TOP PadStack = DR-0102_60",
            f"NodeSidePwr{index}!!1::VDD_CORE/0 X = {index}.1mm Y = 2mm "
            "Layer = Signal$PWR PadStack = DR-0102_60",
        )
    )
    trace_lines = "\n".join(
        f"TraceMesh{index}::VDD_CORE/0 StartingNode = NodeRouteTop{index} "
        f"EndingNode = NodeSideTop{index} Width = 0.10mm"
        for index in range(component_count)
    )
    via_lines = "\n".join(
        line
        for index in range(component_count)
        for line in (
            f"ViaRoute{index}::VDD_CORE/0 UpperNode = NodeRouteTop{index} "
            f"LowerNode = NodeRoutePwr{index} PadStack = DR-0102_60",
            f"ViaAlternate{index}::VDD_CORE/0 UpperNode = NodeSideTop{index} "
            f"LowerNode = NodeSidePwr{index} PadStack = DR-0102_60",
        )
    )
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=node_lines,
        via_lines=via_lines,
        trace_lines=trace_lines,
    )
    landings = tuple(
        SimpleNamespace(
            via_id=f"ViaRoute{index}",
            net="VDD_CORE/0",
            endpoint_node_id=f"NodeRouteTop{index}",
            x_um=float(index) * 1_000.0,
            y_um=2_000.0,
            padstack="DR-0102_60",
        )
        for index in range(component_count)
    )
    original_iter_lines = spd_io._iter_lines
    section_passes = 0

    def counted_iter_lines(data, start, end):
        nonlocal section_passes
        section_passes += 1
        return original_iter_lines(data, start, end)

    monkeypatch.setattr(spd_io, "_iter_lines", counted_iter_lines)

    recovery = recover_spd_via_paths(
        source,
        landings=landings,
        target_layers_by_net={"VDD_CORE/0": ("Signal$PWR",)},
        stackup_layers=analysis.stackup_layers,
        padstacks=analysis.padstacks,
        top_layer="Signal$TOP",
    )

    assert recovery.statistics["requested"] == component_count
    assert recovery.statistics["recovered"] == component_count
    assert recovery.statistics["trace_components_indexed"] == component_count
    assert recovery.statistics["alternate_exit_cache_entries"] == component_count
    assert recovery.statistics["path_node_section_passes"] == 1
    assert recovery.statistics["alternate_exit_node_section_passes"] == 1
    assert recovery.statistics["alternate_exit_nodes_resolved"] == 4 * component_count
    # One path-frontier pass plus one compact alternate-exit pass: component
    # count no longer multiplies complete Node-section scans.
    assert section_passes == 2
    assert all(
        recovery.evidence_for(f"ViaRoute{index}", "Signal$PWR").trace_alternate_exit
        for index in range(component_count)
    )


def test_recover_spd_via_paths_rejects_source_changed_since_analysis(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1.25mm Y = 2.5mm Layer = Signal$PWR
""",
        via_lines=(
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 "
            "LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60"
        ),
    )
    source.write_text(source.read_text(encoding="ascii") + "\n", encoding="ascii")

    with pytest.raises(SpdImportError, match="changed after analysis before"):
        _recover_power_path(source, analysis, expected_source=analysis.source)


def test_recover_spd_via_paths_rejects_source_changed_during_recovery(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1.25mm Y = 2.5mm Layer = Signal$PWR
""",
        via_lines=(
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 "
            "LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60"
        ),
    )
    changed = False

    def mutate_after_identity(value: int, _message: str) -> None:
        nonlocal changed
        if changed or value < 4:
            return
        before = source.stat()
        os.utime(
            source,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        changed = True

    with pytest.raises(SpdImportError, match="changed during source Via path recovery"):
        _recover_power_path(
            source,
            analysis,
            expected_source=analysis.source,
            progress=mutate_after_identity,
        )

    assert changed


def test_recovery_uses_terminal_via_rotation_for_rectangular_target_pad(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = RECT_ROUTE
Node11!!1::VDD_CORE/0 X = 1.5mm Y = 2.5mm Layer = Signal$PWR PadStack = NODE_FEATURE
""",
        via_lines=(
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 "
            "LowerNode = Node11::VDD_CORE/0 PadStack = RECT_ROUTE "
            "AbsoluteRotation = 90"
        ),
        padstack_defs="""
.PadStackDef RECT_ROUTE 0.02mm Material = COPPER
.PadDef Signal$TOP
Regular Box 0.10mm 0.20mm
.EndPadDef
.PadDef Signal$PWR
Regular Box 0.10mm 0.20mm
.EndPadDef
.EndPadStackDef
""",
    )

    recovery = _recover_power_path(source, analysis)

    evidence = recovery.evidence_for("ViaRoute", "Signal$PWR")
    assert evidence is not None
    assert evidence.target_padstack == "RECT_ROUTE"
    assert (evidence.target_pad_width_um, evidence.target_pad_height_um) == (
        200.0,
        100.0,
    )
    assert evidence.segments[-1].rotation_degrees == 90.0


def test_recovery_fails_closed_for_non_axis_aligned_terminal_via_rotation(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$PWR
""",
        via_lines=(
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 "
            "LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60 "
            "AbsoluteRotation = 45"
        ),
    )

    recovery = _recover_power_path(source, analysis)

    assert recovery.statistics["recovered"] == 0
    assert any(
        item.code == "SPD_VIA_PATH_LEGACY_FALLBACK"
        and "UNSUPPORTED_VIA_ROTATION" in item.message
        for item in recovery.diagnostics
    )


def test_recovery_reports_monotonic_progress_and_honors_cancellation(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$PWR
""",
        via_lines=(
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 "
            "LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60"
        ),
    )
    updates: list[tuple[int, str]] = []

    recovery = _recover_power_path(
        source,
        analysis,
        progress=lambda value, message: updates.append((value, message)),
    )

    assert recovery.statistics["recovered"] == 1
    assert [value for value, _message in updates] == sorted(
        value for value, _message in updates
    )
    assert updates[0][0] == 0
    assert updates[-1][0] == 100
    with pytest.raises(SpdImportError, match="cancelled"):
        _recover_power_path(source, analysis, is_cancelled=lambda: True)


@pytest.mark.parametrize(
    ("node_lines", "via_lines", "trace_lines", "failure_code"),
    [
        (
            """
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
Node12!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60
Node13!!1::VDD_CORE/0 X = 1.1mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60
""",
            """
ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60
ViaBranchA::VDD_CORE/0 UpperNode = Node11::VDD_CORE/0 LowerNode = Node12::VDD_CORE/0 PadStack = DR-0102_60
ViaBranchB::VDD_CORE/0 UpperNode = Node11::VDD_CORE/0 LowerNode = Node13::VDD_CORE/0 PadStack = DR-0102_60
""",
            "",
            "AMBIGUOUS_VERTICAL_BRANCH",
        ),
        (
            """
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
Node12!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60
""",
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60",
            "TraceNeed::VDD_CORE/0 StartingNode = Node11::VDD_CORE/0 EndingNode = Node12::VDD_CORE/0 Width = 0.10mm",
            "TRACE_REQUIRED",
        ),
        (
            """
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60
""",
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 LowerNode = Node11::VDD_CORE/0 PadStack = MISSING",
            "",
            "MISSING_PADSTACK",
        ),
        (
            "Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60",
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 LowerNode = Node404::VDD_CORE/0 PadStack = DR-0102_60",
            "",
            "MISSING_NODE",
        ),
    ],
)
def test_recover_spd_via_path_fails_closed_for_nonunique_or_incomplete_routes(
    tmp_path: Path,
    node_lines: str,
    via_lines: str,
    trace_lines: str,
    failure_code: str,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=node_lines,
        via_lines=via_lines,
        trace_lines=trace_lines,
    )

    recovery = _recover_power_path(source, analysis)

    assert recovery.statistics["requested"] == 1
    assert recovery.statistics["recovered"] == 0
    assert recovery.statistics["fallback"] == 1
    assert recovery.evidence_for("ViaRoute", "Signal$PWR") is None
    assert any(
        diagnostic.code == "SPD_VIA_PATH_LEGACY_FALLBACK"
        and failure_code in diagnostic.message
        for diagnostic in recovery.diagnostics
    )


def test_recover_spd_via_path_allows_one_unique_same_net_trace_hop(tmp_path: Path) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
Node12!!1::VDD_CORE/0 X = 1.2mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
Node13!!1::VDD_CORE/0 X = 1.4mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
Node14!!1::VDD_CORE/0 X = 1.4mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60
""",
        via_lines="""
ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60
ViaTarget::VDD_CORE/0 UpperNode = Node13::VDD_CORE/0 LowerNode = Node14::VDD_CORE/0 PadStack = DR-0102_60
""",
        trace_lines="""
TraceNeedA::VDD_CORE/0 StartingNode = Node11::VDD_CORE/0 EndingNode = Node12::VDD_CORE/0 Width = 0.10mm
TraceNeedB::VDD_CORE/0 StartingNode = Node12::VDD_CORE/0 EndingNode = Node13::VDD_CORE/0 Width = 0.10mm
""",
    )

    evidence = _recover_power_path(source, analysis).evidence_for("ViaRoute", "Signal$PWR")

    assert evidence is not None
    assert evidence.target_node_id == "Node14"
    assert evidence.trace_hops == 2
    assert [item.via_id for item in evidence.segments] == ["ViaRoute", "ViaTarget"]


@pytest.mark.parametrize(
    ("trace_lines", "failure_code"),
    [
        (
            "\n".join(
                (
                    "TraceA::VDD_CORE/0 StartingNode = Node11::VDD_CORE/0 EndingNode = Node12::VDD_CORE/0 Width = 0.10mm",
                    "TraceB::VDD_CORE/0 StartingNode = Node11::VDD_CORE/0 EndingNode = Node13::VDD_CORE/0 Width = 0.10mm",
                )
            ),
            "AMBIGUOUS_TRACE_BRANCH",
        ),
        (
            "\n".join(
                (
                    "TraceA::VDD_CORE/0 StartingNode = Node11::VDD_CORE/0 EndingNode = Node12::VDD_CORE/0 Width = 0.10mm",
                    "TraceB::VDD_CORE/0 StartingNode = Node12::VDD_CORE/0 EndingNode = Node11::VDD_CORE/0 Width = 0.10mm",
                )
            ),
            "TRACE_CYCLE",
        ),
    ],
)
def test_recover_spd_via_path_fails_closed_for_trace_branch_or_cycle(
    tmp_path: Path, trace_lines: str, failure_code: str
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
Node12!!1::VDD_CORE/0 X = 1.2mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
Node13!!1::VDD_CORE/0 X = 1.4mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
""",
        via_lines="ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60",
        trace_lines=trace_lines,
    )

    recovery = _recover_power_path(source, analysis)

    assert recovery.statistics["recovered"] == 0
    assert any(failure_code in item.message for item in recovery.diagnostics)


def test_streaming_spd_normalizes_selected_geometry_and_passive_models(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tiny.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    progress: list[tuple[int, str]] = []

    analysis = analyze_spd(source, progress=lambda value, text: progress.append((value, text)))

    assert analysis.source.size_bytes == source.stat().st_size
    assert len(analysis.source.sha256) == 64
    assert analysis.outline.width_um == pytest.approx(10_000.0)
    assert analysis.outline.height_um == pytest.approx(8_000.0)
    assert len(analysis.stackup_layers) == 5
    assert sum(item.is_conductor for item in analysis.stackup_layers) == 3
    assert analysis.stackup_layers[1].dk == pytest.approx(3.3)
    assert analysis.stackup_layers[1].df == pytest.approx(0.004)
    assert analysis.power_plane_nets == ("VDD_CORE/0",)
    assert analysis.ground_nets == ("DGND",)
    geometry = next(
        item
        for item in analysis.plane_geometries
        if item.layer == "Signal$PWR" and item.net == "VDD_CORE/0"
    )
    assert [len(item) for item in geometry.positive_polygons_um] == [4, 4]
    assert [len(item) for item in geometry.negative_polygons_um] == [4]
    assert geometry.negative_circles_um == ((1_000.0, 1_000.0, 60.0),)
    assert geometry.positive_subelement_count == 1
    assert geometry.negative_subelement_count == 2
    assert geometry.primitive_order == (
        ("positive_polygon", 0),
        ("negative_polygon", 0),
        ("negative_circle", 0),
        ("positive_polygon", 1),
    )
    assert set(analysis.cap_models) == {"CAP_0402_100NF"}
    assert set(analysis.model_assets) == {"CAP_0402_100NF.lib"}
    assert len(analysis.cap_instances) == 1
    assert analysis.cap_instances[0].mounted
    assert analysis.cap_instances[0].geometry_present
    assert analysis.cap_instances[0].footprint == "0402"
    assert len(analysis.pins) == 4  # selected DUT PWR/GND plus selected cap PWR/GND
    assert {item.kind for item in analysis.pins} == {PinKind.DEVICE_BUMP, PinKind.DECAP_PAD}
    assert {item.terminal for item in analysis.pins} == {TerminalKind.PWR, TerminalKind.GND}
    device_power = next(
        item
        for item in analysis.pins
        if item.kind == PinKind.DEVICE_BUMP and item.terminal == TerminalKind.PWR
    )
    device_ground = next(
        item
        for item in analysis.pins
        if item.kind == PinKind.DEVICE_BUMP and item.terminal == TerminalKind.GND
    )
    assert device_power.site == "SITE0"
    assert device_power.net == "VDD_CORE/0"
    assert device_ground.site == "SITE0"
    padstack = next(item for item in analysis.padstacks if item.name == "DR-0102_60")
    assert padstack.drill_diameter_um == pytest.approx(40.0)
    assert padstack.pad_diameter_um == pytest.approx(60.0)
    assert padstack.pad_shapes[0].kind == "CIRCLE"
    assert padstack.pad_shapes[0].layer == "Signal$TOP"
    assert padstack.pad_shapes[0].width_um == pytest.approx(60.0)
    assert sum(item.count for item in analysis.via_usage) == 2
    assert analysis.counts["mounted_cap_instances"] == 1
    assert analysis.counts["skipped_unselected_cap_instances"] == 1
    assert analysis.counts["partial_circuits"] == 2
    assert progress[-1][0] == 100


def test_device_ground_bumps_preserve_site_provenance(tmp_path: Path) -> None:
    source = tmp_path / "two-site.spd"
    source.write_text(
        MINI_SPD.replace(
            "Node6!!2::DGND X = 3.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
            "Node6!!2::DGND X = 3.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n"
            "Node7!!201::VDD_CORE/0 X = 0.2mm Y = 0mm Layer = Signal$TOP PadStack = DUT\n"
            "Node8!!202::DGND/1 X = 0.3mm Y = 0mm Layer = Signal$TOP PadStack = DUT",
            1,
        ).replace(
            ".EndC\n.Connect C1",
            ".EndC\n.Connect SITE1 DUT Checked = 1\n"
            "201 $Package.Node7!!201::VDD_CORE/0\n"
            "202 $Package.Node8!!202::DGND/1\n"
            ".EndC\n.Connect C1",
            1,
        ).replace(
            ".Component SITE0 0mm 0mm StartLayer = Signal$TOP AttachLayer = TopAir",
            ".Component SITE0 0mm 0mm StartLayer = Signal$TOP AttachLayer = TopAir\n"
            ".Component SITE1 0.2mm 0mm StartLayer = Signal$TOP AttachLayer = TopAir",
            1,
        ),
        encoding="ascii",
    )

    analysis = analyze_spd(source)

    site1_power = next(
        item
        for item in analysis.pins
        if item.refdes == "SITE1" and item.kind == PinKind.DEVICE_BUMP
        and item.terminal == TerminalKind.PWR
    )
    assert site1_power.net == "VDD_CORE/0"
    ground_sites = {
        item.refdes: (item.net, item.site)
        for item in analysis.pins
        if item.kind == PinKind.DEVICE_BUMP and item.terminal == TerminalKind.GND
    }
    assert ground_sites == {
        "SITE0": ("DGND", "SITE0"),
        "SITE1": ("DGND/1", "SITE1"),
    }


def test_suffixed_configured_ground_is_classified_for_decap_ports(
    tmp_path: Path,
) -> None:
    source = tmp_path / "suffixed-cap-ground.spd"
    source.write_text(
        MINI_SPD.replace(
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
            "Node4!!2::DGND/1 X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
            1,
        ).replace(
            "2 $Package.Node4!!2::DGND",
            "2 $Package.Node4!!2::DGND/1",
            1,
        ),
        encoding="ascii",
    )

    analysis = analyze_spd(source)

    cap_ground = next(
        item
        for item in analysis.pins
        if item.refdes == "C1" and item.kind == PinKind.DECAP_PAD
        and item.terminal == TerminalKind.GND
    )
    assert cap_ground.net == "DGND/1"


def test_consecutive_nonempty_partial_circuits_are_all_parsed(tmp_path: Path) -> None:
    source = tmp_path / "consecutive-models.spd"
    payload = MINI_SPD.replace(
        ".PartialCkt CAP_0402_100NF_NOT_MOUNTED ExtNode = 1 2\n"
        ".EndPartialCkt",
        ".PartialCkt CAP_0402_1UF ExtNode = 1 2\n"
        "R1 1 X 0.02\n"
        "L1 X Y 0.3n\n"
        "C1 Y 2 1u\n"
        ".EndPartialCkt",
    )
    source.write_text(payload, encoding="ascii")

    analysis = analyze_spd(source)

    assert analysis.counts["partial_circuits"] == 2
    assert set(analysis.cap_models) == {"CAP_0402_100NF", "CAP_0402_1UF"}
    assert not any(
        item.code == "PARTIAL_CKT_UNTERMINATED"
        for item in analysis.diagnostics
    )


def test_spd_import_honors_cancellation_without_loading_source(tmp_path: Path) -> None:
    source = tmp_path / "cancel.spd"
    source.write_bytes((MINI_SPD * 5_000).encode("ascii"))

    with pytest.raises(SpdImportError, match="cancelled"):
        analyze_spd(source, is_cancelled=lambda: True)


def test_selected_unknown_plane_primitive_is_not_silently_ignored(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unsupported-shape.spd"
    source.write_text(
        MINI_SPD.replace(
            ".EndShape\n* Layer description lines",
            "Spline9::VDD_CORE/0+ 0mm 0mm 1mm 1mm\n"
            ".EndShape\n* Layer description lines",
        ),
        encoding="ascii",
    )

    analysis = analyze_spd(source)

    assert any(
        item.code == "SPD_PLANE_PRIMITIVE_UNSUPPORTED"
        and "Spline" in item.message
        and item.severity == "error"
        for item in analysis.diagnostics
    )
    assert analysis.has_errors


def test_polygon_trace_and_box_are_normalized_in_source_order(tmp_path: Path) -> None:
    source = tmp_path / "normalized-shapes.spd"
    source.write_text(
        MINI_SPD.replace(
            ".EndShape\n* Layer description lines",
            "PolygonTrace6::VDD_CORE/0- ViaHole_A Sub-element "
            "-1mm -1mm 0mm -1mm\n"
            "+ 0mm 0mm -1mm 0mm\n"
            "Box7::VDD_CORE/0+ Sub-element 3mm 2mm 0.4mm 0.6mm\n"
            ".EndShape\n* Layer description lines",
        ),
        encoding="ascii",
    )

    analysis = analyze_spd(source)
    geometry = next(
        item
        for item in analysis.plane_geometries
        if item.layer == "Signal$PWR" and item.net == "VDD_CORE/0"
    )

    assert geometry.polygon_trace_count == 1
    assert geometry.box_count == 1
    assert geometry.negative_polygons_um[-1] == (
        (-1_000.0, -1_000.0),
        (0.0, -1_000.0),
        (0.0, 0.0),
        (-1_000.0, 0.0),
    )
    assert geometry.positive_polygons_um[-1] == (
        (2_800.0, 1_700.0),
        (3_200.0, 1_700.0),
        (3_200.0, 2_300.0),
        (2_800.0, 2_300.0),
    )
    assert geometry.primitive_order[-2:] == (
        ("negative_polygon", 1),
        ("positive_polygon", 2),
    )
    assert analysis.counts["selected_plane_polygon_traces"] == 1
    assert analysis.counts["selected_plane_boxes"] == 1
    assert not analysis.has_errors


def test_ordered_geometry_batches_polarity_runs_without_changing_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shapely.geometry import GeometryCollection, Polygon
    import shapely.ops

    positive = [
        [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
        [(8.0, 0.0), (18.0, 0.0), (18.0, 10.0), (8.0, 10.0)],
        [(3.0, 3.0), (4.0, 3.0), (4.0, 4.0), (3.0, 4.0)],
    ]
    negative = [
        [(2.0, 2.0), (5.0, 2.0), (5.0, 5.0), (2.0, 5.0)],
        [(12.0, 2.0), (15.0, 2.0), (15.0, 5.0), (12.0, 5.0)],
    ]
    order = [
        ("positive_polygon", 0),
        ("positive_polygon", 1),
        ("negative_polygon", 0),
        ("negative_polygon", 1),
        ("positive_polygon", 2),
    ]
    record = {
        "positive_polygons_um": positive,
        "negative_polygons_um": negative,
        "positive_circles_um": [],
        "negative_circles_um": [],
        "primitive_order": order,
    }
    expected = GeometryCollection()
    for kind, index in order:
        primitive = Polygon(
            positive[index] if kind.startswith("positive_") else negative[index]
        )
        expected = (
            expected.union(primitive)
            if kind.startswith("positive_")
            else expected.difference(primitive)
        )
    original_unary_union = shapely.ops.unary_union
    batches: list[int] = []

    def counted_unary_union(items):
        values = list(items)
        batches.append(len(values))
        return original_unary_union(values)

    monkeypatch.setattr(shapely.ops, "unary_union", counted_unary_union)

    actual = core_services._ordered_spd_geometry(record)

    assert actual is not None
    assert actual.symmetric_difference(expected).area == pytest.approx(0.0)
    assert batches == [2, 2, 1]


def test_malformed_selected_supported_primitive_blocks_import(tmp_path: Path) -> None:
    source = tmp_path / "malformed-circle.spd"
    source.write_text(
        MINI_SPD.replace(
            ".EndShape\n* Layer description lines",
            "Circle8::VDD_CORE/0- Sub-element 1mm 2mm\n"
            ".EndShape\n* Layer description lines",
        ),
        encoding="ascii",
    )

    analysis = analyze_spd(source)

    assert any(
        item.code == "SPD_PLANE_PRIMITIVE_MALFORMED"
        and item.severity == "error"
        for item in analysis.diagnostics
    )
    assert analysis.has_errors


def test_decap_scenario_scope_includes_candidate_rails_dnp_and_pad_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scenario.spd"
    scenario_spd = MINI_SPD.replace(
        ".EndShape\n* Layer description lines",
        ".Shape Signal$PWRpkgshape\n"
        "Polygon9::VDD_DROP/0+ 2.5mm 1.5mm 3.5mm 1.5mm "
        "3.5mm 2.5mm 2.5mm 2.5mm\n"
        ".EndShape\n* Layer description lines",
    ).replace(
        ".Connect C2 CAP_0402_100NF Checked = 1",
        ".Connect C2 CAP_0402_100NF_NOT_MOUNTED Usage = 0b111000",
    )
    source.write_text(scenario_spd, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")

    # Scenario assignment is fail-closed to explicit .NetList PowerNets.
    # Unselected/DNP locations remain visible inventory but are not rail choices.
    assert analysis.power_plane_nets == ("VDD_CORE/0",)
    assert all(
        item.net != "VDD_DROP/0" for item in analysis.plane_geometries
    )
    assert [item.refdes for item in analysis.cap_instances] == ["C1", "C2"]
    c2 = analysis.cap_instances[1]
    assert not c2.mounted
    assert c2.start_layer == "Signal$TOP"
    assert c2.source_part_name == "CAP_0402_100NF_NOT_MOUNTED"
    assert c2.power_x_um == pytest.approx(3_000.0)
    assert c2.power_y_um == pytest.approx(2_000.0)
    assert c2.power_padstack == "CAP"


def test_decap_scenario_retains_non_top_configured_ground_geometry(
    tmp_path: Path,
) -> None:
    source = tmp_path / "non-top-ground.spd"
    source.write_text(MINI_SPD, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")

    assert any(
        item.layer == "Signal$GND" and item.net == "DGND"
        for item in analysis.plane_geometries
    )
    assert analysis.power_plane_nets == ("VDD_CORE/0",)


def test_unselected_positive_net_keeps_ground_layer_mixed_and_requires_certificate(
    tmp_path: Path,
) -> None:
    source = tmp_path / "mixed-ground.spd"
    payload = MINI_SPD.replace(
        "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
        "Polygon1::DGND+ -1mm -1mm 1mm -1mm 1mm 1mm -1mm 1mm\n"
        "PolygonSIG::SIG_RETURN+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
    )
    source.write_text(payload, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")
    gnd_layer = next(
        item for item in analysis.stackup_layers if item.name == "Signal$GND"
    )
    assert {item.casefold() for item in gnd_layer.pwr_nets} == {
        "dgnd",
        "sig_return",
    }
    assert any(
        item.layer == "Signal$GND" and item.net == "DGND"
        for item in analysis.plane_geometries
    )
    assert all(item.net != "SIG_RETURN" for item in analysis.plane_geometries)

    plan = build_spd_import_plan(
        create_workspace_state().project,
        analysis,
        source,
    )

    # DGND covers only a small part of the selected PWR artwork.  Retaining
    # SIG_RETURN occupancy prevents this layer from masquerading as pure GND,
    # and the failed coverage gate means no rail can be formed.
    assert plan.project.rails == []
    assert any(item.code == "SPD_NO_RAILS" for item in plan.diagnostics)
    assert any(
        item.code == "SPD_MIXED_REFERENCE_CERTIFICATE_REJECTED"
        and "coverage" in item.message
        for item in plan.diagnostics
    )
    failures = plan.project.metadata["spd_import"][
        "mixed_reference_certificate_failures"
    ]
    assert failures[0]["rail_net"] == "VDD_CORE/0"
    assert failures[0]["pwr_layer"] == "Signal$PWR"


def test_valid_mixed_reference_certificate_binds_assets_and_low_confidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "certified-mixed-ground.spd"
    payload = MINI_SPD.replace(
        "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
        "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
        "PolygonSIG::SIG_RETURN+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
    )
    source.write_text(payload, encoding="ascii")
    analysis = analyze_spd(source, scope="decap_scenario")

    plan = build_spd_import_plan(
        create_workspace_state().project,
        analysis,
        source,
    )

    rail = next(item for item in plan.project.rails if item.net == "VDD_CORE/0")
    certificate = rail.mixed_reference_certificate
    assert certificate is not None
    assert (rail.pwr_layer, rail.gnd_layer) == ("Signal$PWR", "Signal$GND")
    assert certificate.gnd_net == "DGND"
    assert certificate.overlap_fraction == pytest.approx(1.0)
    assert certificate.dominant_overlap_component_fraction == pytest.approx(1.0)
    records = plan.project.metadata["spd_import"]["plane_geometries"]
    pwr_record = next(
        item for item in records
        if item["layer"] == rail.pwr_layer and item["net"] == rail.net
    )
    gnd_record = next(
        item for item in records
        if item["layer"] == rail.gnd_layer and item["net"] == certificate.gnd_net
    )
    assert certificate.pwr_asset_sha256 == pwr_record["asset_sha256"]
    assert certificate.gnd_asset_sha256 == gnd_record["asset_sha256"]
    assert pwr_record["asset"] in plan.project.attachment_names
    assert gnd_record["asset"] in plan.project.attachment_names
    with pytest.raises(EvaluationError, match="source-ground reachability witness"):
        _planes_from_project(plan.project, rail)
    witness = MixedReferenceGroundWitness(
        rail_net=rail.net,
        gnd_net=certificate.gnd_net,
        pwr_layer=rail.pwr_layer,
        gnd_layer=rail.gnd_layer,
        gnd_asset_sha256=certificate.gnd_asset_sha256,
        source_sha256=analysis.source.sha256,
        landing_identities=(),
        landing_count=0,
        landing_identities_sha256=sha256(b"[]\n").hexdigest(),
    )
    rail = rail.model_copy(update={"mixed_reference_ground_witness": witness})
    project = plan.project.model_copy(
        update={
            "rails": [
                rail if item.rail_id == rail.rail_id else item
                for item in plan.project.rails
            ]
        }
    )
    _plane, _parallel, _origin, _confirmed, assumptions = _planes_from_project(
        project, rail
    )
    assert any("LOW confidence: mixed-reference" in item for item in assumptions)
    assert not any(
        item.code == "SPD_MIXED_REFERENCE_CERTIFICATE_REJECTED"
        for item in plan.diagnostics
    )
    frequencies = np.asarray([1.0e5, 1.0e6])
    outcome = SimpleNamespace(
        rail_id=rail.rail_id,
        solve=SimpleNamespace(
            frequencies_hz=frequencies,
            impedance_ohm=np.asarray([0.01 + 0.0j, 0.02 + 0.0j]),
            diagnostics=SimpleNamespace(
                mode_count=4,
                max_condition_number=1.0,
                max_relative_residual=0.0,
            ),
        ),
        metrics=SimpleNamespace(
            magnitude_ohm=np.asarray([0.01, 0.02]),
            phase_deg=np.asarray([0.0, 0.0]),
            target_ohm=np.asarray([0.02, 0.02]),
            violation_db=np.asarray([-6.0, 0.0]),
            max_violation_db=0.0,
            rms_violation_db=0.0,
            peaks=(),
        ),
        confidence=(
            SimpleNamespace(
                category=SimpleNamespace(value="Geometry"),
                level=SimpleNamespace(value="LOW"),
                start_hz=1.0e5,
                stop_hz=1.0e6,
                reason="mixed reference",
            ),
        ),
        assumptions=(),
        solver_version="test",
        convergence=None,
    )

    view = _evaluation_view(plan.project, outcome)

    assert "Mixed reference Signal$PWR/Signal$GND" in view.confidence_note
    assert "overlap 100.00%, dominant 100.00%" in view.confidence_note
    assert "rectangular-return approximation" in view.confidence_note


def test_mixed_reference_geometry_failure_blocks_import_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "mixed-geometry-failure.spd"
    payload = MINI_SPD.replace(
        "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
        "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
        "PolygonSIG::SIG_RETURN+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
    )
    source.write_text(payload, encoding="ascii")
    analysis = analyze_spd(source, scope="decap_scenario")
    monkeypatch.setattr(core_services, "_ordered_spd_geometry", lambda _record: None)

    plan = build_spd_import_plan(
        create_workspace_state().project,
        analysis,
        source,
    )

    assert not plan.can_apply
    assert any(
        item.severity == "error"
        and item.code == "SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE"
        for item in plan.diagnostics
    )


def test_single_node_and_via_passes_produce_exact_direct_connection_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "direct-via-evidence.spd"
    payload = MINI_SPD.replace(
        "Node6!!2::DGND X = 3.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n",
        "Node6!!2::DGND X = 3.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n"
        "Node7::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node8::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60\n"
        "Node9::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node10::DGND X = 1.2mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60\n",
    ).replace(
        "Via1::VDD_CORE/0 UpperNode = Node1 LowerNode = Node3 PadStack = DR-0102_60\n"
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
        "Via1::VDD_CORE/0 UpperNode = Node7::VDD_CORE/0 "
        "LowerNode = Node8::VDD_CORE/0 PadStack = DR-0102_60\n"
        "Via2::DGND UpperNode = Node9::DGND LowerNode = Node10::DGND "
        "PadStack = DR-0102_60",
    ).replace(
        "* Material description lines",
        ".PadStackDef CAP 0.01mm Material = COPPER\n"
        ".PadDef Signal$TOP\n"
        "Regular Square 0.10mm\n"
        ".EndPadDef\n"
        ".EndPadStackDef\n"
        "* Material description lines",
    )
    source.write_text(payload, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")

    by_refdes = {item.refdes: item for item in analysis.decap_connections}
    cap_padstack = next(item for item in analysis.padstacks if item.name == "CAP")
    assert len(cap_padstack.pad_shapes) == 1
    assert cap_padstack.pad_shapes[0].kind == "RECTANGLE"
    assert cap_padstack.pad_shapes[0].width_um == pytest.approx(100.0)
    assert cap_padstack.pad_shapes[0].height_um == pytest.approx(100.0)
    assert by_refdes["C1"].kind == "DIRECT"
    assert [item.via_id for item in by_refdes["C1"].power_vias] == ["Via1"]
    assert [item.via_id for item in by_refdes["C1"].ground_vias] == ["Via2"]
    assert by_refdes["C2"].kind == "FLOATING_DUMMY"
    assert analysis.counts["top_via_endpoints"] == 2


def test_decap_scenario_requires_explicit_power_net_classification(
    tmp_path: Path,
) -> None:
    source = tmp_path / "no-power-classification.spd"
    payload = MINI_SPD.replace(
        "VDD_CORE/0 -> PowerNets Voltage = 0\n",
        "VDD_CORE/0::Unselected||DropShape\n",
    )
    source.write_text(payload, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")

    assert analysis.power_plane_nets == ()
    assert any(
        item.code == "SPD_POWER_NET_CLASSIFICATION_MISSING"
        and item.severity == "error"
        for item in analysis.diagnostics
    )


def test_decap_scenario_requires_a_ground_plane_classification(
    tmp_path: Path,
) -> None:
    source = tmp_path / "no-ground-plane.spd"
    payload = MINI_SPD.replace(
        "Polygon1::DGND+",
        "Polygon1::RETURN+",
    ).replace(
        "DGND -> GroundNets Voltage = 0\n",
        "",
    )
    source.write_text(payload, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")

    assert any(
        item.code == "SPD_GROUND_PLANE_CLASSIFICATION_MISSING"
        and item.severity == "error"
        for item in analysis.diagnostics
    )


def test_top_io_signal_shape_is_not_promoted_to_a_power_rail(tmp_path: Path) -> None:
    source = tmp_path / "top-io-signal.spd"
    payload = MINI_SPD.replace(
        ".EndShape\n* Layer description lines",
        "Polygon8::SIG_DATA+ -4mm -3mm 4mm -3mm "
        "4mm 3mm -4mm 3mm\n"
        ".EndShape\n* Layer description lines",
    ).replace(
        "* Via description lines",
        "Node7!!103::SIG_DATA X = 0.2mm Y = 0mm "
        "Layer = Signal$TOP PadStack = DUT\n"
        "* Via description lines",
    ).replace(
        "102 $Package.Node2!!102::DGND\n.EndC",
        "102 $Package.Node2!!102::DGND\n"
        "103 $Package.Node7!!103::SIG_DATA\n"
        ".EndC",
    ).replace(
        ".EndNetList",
        "SIG_DATA::Unselected||DropShape\n.EndNetList",
    )
    source.write_text(payload, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")

    assert analysis.power_plane_nets == ("VDD_CORE/0",)
    assert all(item.net != "SIG_DATA" for item in analysis.plane_geometries)
    assert all(item.net != "SIG_DATA" for item in analysis.pins)


def test_netlist_uses_explicit_group_markers_and_inherited_rows(tmp_path: Path) -> None:
    source = tmp_path / "production-netlist.spd"
    source.write_text(
        ".NetList\n"
        "    RiseTime = 0ps %Coupling = 0\n"
        "    SIG_BEFORE Color = YELLOW\n"
        "    DGND -> GroundNets Color = RED\n"
        "    AGND Color = GREEN\n"
        "    VDD_CORE/0 -> PowerNets::Unselected||DropShape Color = BLUE\n"
        "    VDD_AUX/0 Color = CYAN\n"
        "    VDD_DNP/0::Unselected||DropShape Color = MAGENTA\n"
        ".EndNetList\n",
        encoding="ascii",
    )

    diagnostics = []
    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        power, ground = _parse_netlist(data, {"dgnd", "agnd"}, diagnostics)

    assert ground == ("DGND", "AGND")
    assert power == ("VDD_CORE/0", "VDD_AUX/0")
    assert "SIG_BEFORE" not in power


def test_netlist_truncated_arrow_keeps_the_inherited_group(tmp_path: Path) -> None:
    source = tmp_path / "truncated-arrow-netlist.spd"
    source.write_text(
        ".NetList\n"
        "DGND -> GroundNets\n"
        "VDD_CORE/0 -> PowerNets\n"
        "VDD_AUX/0 ->\n"
        ".EndNetList\n",
        encoding="ascii",
    )

    diagnostics = []
    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        power, ground = _parse_netlist(data, {"dgnd"}, diagnostics)

    assert ground == ("DGND",)
    assert power == ("VDD_CORE/0", "VDD_AUX/0")
