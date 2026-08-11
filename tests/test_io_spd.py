from __future__ import annotations

from dataclasses import replace
import mmap
import os
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.domain import (
    MixedReferenceGroundWitness,
    PinKind,
    StackupLayer,
    TerminalKind,
)
from spd_decap_pi._core.io import spd as spd_io
from spd_decap_pi._core.io.spd import (
    SpdImportError,
    SpdSurfaceConnectivityComponent,
    SpdTraceRecordError,
    _iter_spd_trace_records,
    _length_pm_exact,
    _length_um,
    _lengths,
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


def test_bulk_length_parser_preserves_units_and_strict_validation() -> None:
    """Bulk geometry parsing reuses matched tokens without relaxing validation."""

    assert _lengths(b"-1mm 2.5mil 3u 4um 0.5m") == pytest.approx(
        [-1000.0, 63.5, 3.0, 4.0, 500_000.0]
    )
    assert _length_um(b"+1.25mm") == pytest.approx(1250.0)
    with pytest.raises(ValueError, match="invalid SPD length"):
        _length_um(b"1mm trailing")
    with pytest.raises(ValueError, match="SPD length is not finite"):
        _length_um(b"1e309mm")


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


def test_chunked_shape_headers_preserve_offsets_across_crlf_lf_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "chunked-shapes.spd"
    source.write_bytes(
        b".Shape Signal$TOPpkgshape\r\n"
        b"Polygon1::VDD_A+ 0mm 0mm 1mm 0mm 1mm 1mm\r\n"
        b".Shape Signal$L01pkgshape\n"
        b"Polygon2::VDD_B+ 0mm 0mm 1mm 0mm 1mm 1mm\n"
        b".Shape Signal$L02pkgshape\r\n"
    )
    monkeypatch.setattr(spd_io, "_SHAPE_INDEX_CHUNK_BYTES", 37)

    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        expected = [
            (match.start(), match.group(1))
            for match in spd_io._SHAPE_RE.finditer(data, 0, len(data))
        ]
        actual = [
            (match.start(), match.group(1))
            for match in spd_io._iter_shape_headers(
                data, 0, len(data), spd_io._Reporter(None, None)
            )
        ]
        chunks = list(spd_io._iter_line_bounded_chunks(data, 0, len(data)))
        raw = data[:]

    assert actual == expected
    assert len(chunks) > 1
    assert all(end == len(raw) or raw[end - 1 : end] == b"\n" for _start, end in chunks)


def test_chunked_shape_headers_check_cancellation_between_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "cancel-chunked-shapes.spd"
    source.write_bytes(
        b".Shape Signal$TOPpkgshape\n"
        + b"x" * 30
        + b"\n.Shape Signal$L01pkgshape\n"
        + b"y" * 30
        + b"\n.Shape Signal$L02pkgshape\n"
    )
    monkeypatch.setattr(spd_io, "_SHAPE_INDEX_CHUNK_BYTES", 24)
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        with pytest.raises(SpdImportError, match="SPD import cancelled"):
            list(
                spd_io._iter_shape_headers(
                    data, 0, len(data), spd_io._Reporter(None, cancelled)
                )
            )

    assert checks == 2


def test_chunked_find_line_matches_whole_range_reference_at_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "chunked-find-line.spd"
    raw = (
        b".NetList\r\n"
        b"not-a-marker .NetList\n"
        + b"x" * 97
        + b"\n.NetList\n"
        + b"y" * 91
        + b"\r\n.EndNetList\r\n"
        + b".NetList trailing\n"
        + b"\r.NetListCRonly\r"
    )
    source.write_bytes(raw)
    monkeypatch.setattr(spd_io, "_SHAPE_INDEX_CHUNK_BYTES", 31)

    def whole_range_reference(
        data: mmap.mmap, prefix: bytes, start: int = 0, end: int | None = None
    ) -> int:
        stop = len(data) if end is None else end
        search = max(0, start)
        while search < stop:
            found = data.find(prefix, search, stop)
            if found < 0:
                return -1
            if found == 0 or data[found - 1 : found] in {b"\n", b"\r"}:
                return found
            search = found + max(1, len(prefix))
        return -1

    second = raw.find(b"\n.NetList", 1) + 1
    end_marker = raw.find(b".EndNetList")
    cr_only = raw.find(b".NetListCRonly")
    cases = (
        (b".NetList", 0, None),
        (b".NetList", 1, None),
        (b".NetList", second, None),
        (b".NetList", second + 1, None),
        (b".NetList", 0, second + len(b".NetList")),
        (b".NetList", 0, second + len(b".NetList") - 1),
        (b".EndNetList", 0, None),
        (b".EndNetList", 0, end_marker + len(b".EndNetList")),
        (b".EndNetList", 0, end_marker + len(b".EndNetList") - 1),
        (b".NetListCRonly", cr_only - 1, None),
        (b".NetListCRonly", cr_only, cr_only + len(b".NetListCRonly")),
        (b".NetListCRonly", cr_only, cr_only + len(b".NetListCRonly") - 1),
    )
    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        chunk_starts = {
            start for start, _end in spd_io._iter_line_bounded_chunks(data, 0, len(data))
        }
        assert second in chunk_starts
        for prefix, start, end in cases:
            assert spd_io._find_line(data, prefix, start, end) == whole_range_reference(
                data, prefix, start, end
            )


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
    assert result.statistics["via_section_passes"] == 2
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


def test_ground_reachability_reports_completion_after_result_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "completion-order.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    landing = SpdViaLanding(
        via_id="Via2",
        net="DGND",
        endpoint_node_id="Node4",
        x_um=1_200.0,
        y_um=2_000.0,
        padstack="DR-0102_60",
    )
    events: list[str] = []
    original_result_type = spd_io.SpdGroundReachability

    def construct_result(*args: object, **kwargs: object) -> object:
        events.append("constructed")
        return original_result_type(*args, **kwargs)

    def progress(value: int, message: str) -> None:
        if value == 100 and message == (
            "Checked mixed-reference GND landing reachability"
        ):
            events.append("reported-100")

    monkeypatch.setattr(spd_io, "SpdGroundReachability", construct_result)

    recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        progress=progress,
    )

    assert events == ["constructed", "reported-100"]


def test_mixed_reference_ground_reachability_uses_compact_dense_components(
    tmp_path: Path,
) -> None:
    """Large sparse source graphs must not become nested Python edge sets."""

    edge_count = 2_048
    node_lines = "\n".join(
        f"NodeRoute{index}!!1::DGND X = {index}um Y = 0um "
        f"Layer = {'Signal$GND' if index == edge_count else 'Signal$TOP'} "
        "PadStack = DR-0102_60"
        for index in range(edge_count + 1)
    )
    trace_lines = "\n".join(
        f"TraceRoute{index}::DGND StartingNode = NodeRoute{index}::DGND "
        f"EndingNode = NodeRoute{index + 1}::DGND Width = 0.10mm"
        for index in range(edge_count)
    )
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=node_lines,
        via_lines="",
        trace_lines=trace_lines,
    )
    landing = SpdViaLanding(
        via_id="ViaRoute",
        net="DGND",
        endpoint_node_id="NodeRoute0",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        target_node_predicate=lambda _net, _layer, node_id, _x, _y: (
            node_id == f"NodeRoute{edge_count}"
        ),
    )

    assert result.reaches(landing, "Signal$GND")
    assert result.statistics["graph_edges"] >= edge_count
    assert result.statistics["graph_nodes"] >= edge_count + 1
    assert result.statistics["components"] >= 1


def test_surface_connectivity_reports_branched_exact_layers_deterministically(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeSurfaceStart!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeSurfaceBranch!!1::PWR X = 100um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeSurfacePwr!!1::PWR X = 100um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodeSurfaceGnd!!1::PWR X = 100um Y = 0um Layer = Signal$GND "
            "PadStack = DR-0102_60"
        ),
        trace_lines=(
            "TraceSurface::PWR StartingNode = NodeSurfaceStart::PWR "
            "EndingNode = NodeSurfaceBranch::PWR Width = 0.10mm"
        ),
        via_lines=(
            "ViaSurfacePwr::PWR UpperNode = NodeSurfaceBranch "
            "LowerNode = NodeSurfacePwr PadStack = DR-0102_60\n"
            "ViaSurfaceGnd::PWR UpperNode = NodeSurfaceBranch "
            "LowerNode = NodeSurfaceGnd PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaSurfaceStart",
        net="PWR",
        endpoint_node_id="NodeSurfaceStart",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    first = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$PWR", "Signal$GND")},
    )
    second = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$GND", "Signal$PWR")},
    )

    expected_component = SpdSurfaceConnectivityComponent(
        net="PWR", layers=("Signal$GND", "Signal$PWR")
    )
    landing_key = ("viasurfacestart", "nodesurfacestart")
    assert first.surface_components == (expected_component,)
    assert second.surface_components == first.surface_components
    assert first.surface_layers_by_landing[landing_key] == (
        "Signal$GND",
        "Signal$PWR",
    )
    assert dict(second.surface_layers_by_landing) == dict(
        first.surface_layers_by_landing
    )
    assert first.reaches(landing, "Signal$GND")
    assert first.reaches(landing, "Signal$PWR")
    with pytest.raises(TypeError):
        first.surface_layers_by_landing[landing_key] = ("Signal$TOP",)  # type: ignore[index]


def test_surface_island_equivalence_uses_coordinate_binding_and_raw_graph(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeTopA1!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeTopA2!!1::PWR X = 1um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeTopB!!1::PWR X = 10um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodePwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines=(
            "TraceAcrossIslands::PWR StartingNode = NodeTopA1::PWR "
            "EndingNode = NodeTopB::PWR Width = 0.10mm"
        ),
        via_lines=(
            "ViaFromSameCopper::PWR UpperNode = NodeTopA2 "
            "LowerNode = NodePwr PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaFromSameCopper",
        net="PWR",
        endpoint_node_id="NodeTopA2",
        x_um=1.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    def resolve(
        _net: str, layer: str, _node: str, x_um: float, _y_um: float
    ) -> str | None:
        if layer == "Signal$PWR":
            return "island-pwr"
        if layer == "Signal$TOP":
            return "island-top-a" if x_um < 5.0 else "island-top-b"
        return None

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        terminal_contact_landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
        target_node_surface_resolver=resolve,
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("island-top-a", "island-top-b"),
            ("PWR", "Signal$PWR"): ("island-pwr",),
        },
    )

    proofs = {
        (item.net, item.layer): item for item in result.surface_equivalence_proofs
    }
    top = proofs[("PWR", "Signal$TOP")]
    assert top.status == "complete"
    assert top.island_ids == ("island-top-a", "island-top-b")
    assert top.contacted_island_ids == top.island_ids
    assert top.graph_component_count == 1
    assert result.statistics["artwork_island_count"] == 3
    assert result.statistics["artwork_island_unions"] == 1
    assert result.statistics["surface_equivalence_complete"] == 2
    assert result.surface_islands_by_landing[
        ("viafromsamecopper", "nodetopa2")
    ] == ("island-pwr", "island-top-a", "island-top-b")
    top_components = tuple(
        item.island_ids
        for item in result.surface_equivalence_components
        if item.net == "PWR" and item.layer == "Signal$TOP"
    )
    assert top_components == (("island-top-a", "island-top-b"),)
    contact = next(
        item
        for item in result.landing_surface_contacts
        if item.landing_key == ("viafromsamecopper", "nodetopa2")
    )
    # The new endpoint proof follows the physical Via to its internal/opposite
    # endpoint.  It deliberately does not force-bind the external TOP node or
    # copy the three-island full reachability union above.
    assert dict(contact.contact_island_ids_by_layer) == {
        "Signal$PWR": ("island-pwr",)
    }
    assert contact.internal_endpoint_node_id == "NodePwr"
    assert contact.endpoint_layer == "Signal$PWR"
    assert contact.endpoint_island_id == "island-pwr"


def test_surface_island_equivalence_fails_closed_for_uncontacted_island(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeOnly!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodePwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines="",
        via_lines=(
            "ViaOnly::PWR UpperNode = NodeOnly LowerNode = NodePwr "
            "PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="Landing",
        net="PWR",
        endpoint_node_id="NodeOnly",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
        target_node_surface_resolver=lambda _n, layer, _i, _x, _y: (
            "island-pwr" if layer == "Signal$PWR" else "island-top-a"
        ),
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("island-top-a", "island-top-floating"),
            ("PWR", "Signal$PWR"): ("island-pwr",),
        },
    )

    top = next(
        item
        for item in result.surface_equivalence_proofs
        if item.layer == "Signal$TOP"
    )
    assert top.status == "uncontacted_island"
    assert top.contacted_island_ids == ("island-top-a",)
    assert result.statistics["surface_equivalence_incomplete"] == 1


def test_via_only_cross_layer_detour_does_not_prove_same_layer_equivalence(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeTopA!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeTopB!!1::PWR X = 10um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodePwrA!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodePwrB!!1::PWR X = 10um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines="",
        via_lines=(
            "ViaA::PWR UpperNode = NodeTopA LowerNode = NodePwrA "
            "PadStack = DR-0102_60\n"
            "ViaB::PWR UpperNode = NodeTopB LowerNode = NodePwrB "
            "PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="Landing",
        net="PWR",
        endpoint_node_id="NodeTopA",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    def resolve(
        _net: str, layer: str, _node: str, x_um: float, _y_um: float
    ) -> str | None:
        if layer == "Signal$PWR":
            return "island-pwr"
        if layer == "Signal$TOP":
            return "island-top-a" if x_um < 5.0 else "island-top-b"
        return None

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
        target_node_surface_resolver=resolve,
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("island-top-a", "island-top-b"),
            ("PWR", "Signal$PWR"): ("island-pwr",),
        },
    )

    proofs = {
        (item.net, item.layer): item for item in result.surface_equivalence_proofs
    }
    assert proofs[("PWR", "Signal$TOP")].status == "complete"
    assert proofs[("PWR", "Signal$TOP")].graph_component_count == 2
    assert proofs[("PWR", "Signal$PWR")].status == "complete"
    assert result.statistics["via_edges_excluded_from_surface_equivalence"] == 2
    # Full reachability still observes the Via detour; only equipotential
    # coalescing excludes it.
    assert result.surface_islands_by_landing[("landing", "nodetopa")] == (
        "island-pwr",
        "island-top-a",
        "island-top-b",
    )
    top_components = tuple(
        item.island_ids
        for item in result.surface_equivalence_components
        if item.layer == "Signal$TOP"
    )
    assert top_components == (("island-top-a",), ("island-top-b",))
    assert all(
        item.terminal_owned_count is None
        and item.substrate_count is None
        for item in result.via_island_pair_aggregates
    )
    assert {
        (
            item.start_island_id,
            item.end_island_id,
            item.count,
        )
        for item in result.via_island_pair_aggregates
    } == {
        ("island-top-a", "island-pwr", 1),
        ("island-top-b", "island-pwr", 1),
    }


def test_via_island_pair_aggregate_hashes_and_subtracts_exact_terminal_ids(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeTopOwned!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeTopSubstrate!!1::PWR X = 1um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodePwrOwned!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodePwrSubstrate!!1::PWR X = 1um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines="",
        via_lines=(
            "ViaOwned::PWR UpperNode = NodeTopOwned "
            "LowerNode = NodePwrOwned PadStack = DR-0102_60\n"
            "ViaSubstrate::PWR UpperNode = NodeTopSubstrate "
            "LowerNode = NodePwrSubstrate PadStack = DR-0102_60"
        ),
    )
    requested = SpdViaLanding(
        via_id="ViaOwned",
        net="PWR",
        endpoint_node_id="NodeTopOwned",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )
    additional_terminal = SpdViaLanding(
        via_id="ViaSubstrate",
        net="PWR",
        endpoint_node_id="NodeTopSubstrate",
        x_um=1.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(requested,),
        terminal_contact_landings=(requested, additional_terminal),
        terminal_owned_via_ids=("ViaOwned",),
        padstacks=analysis.padstacks,
        stackup_layers=analysis.stackup_layers,
        target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
        target_node_surface_resolver=lambda _n, layer, _i, _x, _y: (
            "island-top" if layer == "Signal$TOP" else "island-pwr"
        ),
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("island-top",),
            ("PWR", "Signal$PWR"): ("island-pwr",),
        },
    )

    assert len(result.via_island_pair_aggregates) == 1
    aggregate = result.via_island_pair_aggregates[0]
    assert (
        aggregate.net,
        aggregate.padstack,
        aggregate.start_layer,
        aggregate.end_layer,
        aggregate.start_island_id,
        aggregate.end_island_id,
    ) == (
        "PWR",
        "DR-0102_60",
        "Signal$TOP",
        "Signal$PWR",
        "island-top",
        "island-pwr",
    )
    assert aggregate.count == aggregate.total_count == 2
    assert aggregate.terminal_owned_count == 1
    assert aggregate.substrate_count == 1
    assert aggregate.physical_model_status == "complete"
    assert aggregate.physical_model_issues == ()
    assert aggregate.drill_diameter_um == 40.0
    assert aggregate.material == "COPPER"
    assert [
        (
            item.ordinal,
            item.start_layer,
            item.end_layer,
            item.length_um,
        )
        for item in aggregate.segments
    ] == [(0, "Signal$TOP", "Signal$PWR", 120.0)]
    expected_hash = sha256()
    for via_id in ("viaowned", "viasubstrate"):
        encoded = via_id.encode("utf-8")
        expected_hash.update(len(encoded).to_bytes(4, "big"))
        expected_hash.update(encoded)
    assert aggregate.via_ids_sha256 == expected_hash.hexdigest()
    assert result.statistics["terminal_owned_via_id_count"] == 1
    assert result.statistics["terminal_owned_via_observed_count"] == 1
    assert result.statistics["via_island_pair_terminal_owned_record_count"] == 1
    assert result.statistics["via_island_pair_substrate_record_count"] == 1
    contacts = {
        item.landing_key: dict(item.contact_island_ids_by_layer)
        for item in result.landing_surface_contacts
    }
    assert contacts == {
        ("viaowned", "nodetopowned"): {
            "Signal$PWR": ("island-pwr",)
        },
        ("viasubstrate", "nodetopsubstrate"): {
            "Signal$PWR": ("island-pwr",)
        },
    }
    assert {
        item.internal_endpoint_node_id
        for item in result.landing_surface_contacts
    } == {"NodePwrOwned", "NodePwrSubstrate"}
    assert all(
        item.terminal_owner_kind == "decap"
        and item.external_endpoint_layer == "Signal$TOP"
        and item.physical_model_status == "complete"
        and item.physical_model_issues == ()
        and item.segments == aggregate.segments
        for item in result.landing_surface_contacts
    )
    coverage = result.via_island_pair_coverage
    assert coverage is not None and coverage.status == "complete"
    assert coverage.raw_target_via_count == coverage.paired_via_count == 2
    assert coverage.terminal_owned_unpaired_count == 0
    assert coverage.unsupported_missing_endpoint_count == 0


def test_terminal_endpoint_contact_rejects_multiple_direct_layers() -> None:
    with pytest.raises(
        ValueError,
        match="cannot directly contact multiple layers",
    ):
        spd_io.SpdLandingSurfaceContact(
            via_id="Via1",
            endpoint_node_id="Node1",
            net="PWR",
            contact_island_ids_by_layer={
                "Signal$TOP": ("island-top",),
                "Signal$PWR": ("island-pwr",),
            },
        )


def test_terminal_owned_one_sided_via_is_excluded_but_nonterminal_blocks(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeExternal!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeInternal!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines="",
        via_lines=(
            "ViaOneSided::PWR UpperNode = NodeExternal "
            "LowerNode = NodeInternal PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaOneSided",
        net="PWR",
        endpoint_node_id="NodeExternal",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    def recover(owned_ids: tuple[str, ...]):
        return recover_spd_ground_reachability(
            source,
            landings=(landing,),
            terminal_contact_landings=(landing,),
            terminal_owned_via_ids=owned_ids,
            padstacks=analysis.padstacks,
            stackup_layers=analysis.stackup_layers,
            target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
            target_node_surface_resolver=lambda _n, layer, _i, _x, _y: (
                "island-pwr" if layer == "Signal$PWR" else None
            ),
            target_surface_island_ids={
                ("PWR", "Signal$TOP"): ("island-top",),
                ("PWR", "Signal$PWR"): ("island-pwr",),
            },
        )

    terminal_owned = recover(("ViaOneSided",))
    coverage = terminal_owned.via_island_pair_coverage
    assert coverage is not None and coverage.status == "complete"
    assert coverage.raw_target_via_count == 1
    assert coverage.paired_via_count == 0
    assert coverage.terminal_owned_unpaired_count == 1
    assert coverage.unsupported_missing_endpoint_count == 0
    assert coverage.outside_retained_interface_scope_count == 0
    assert coverage.model_relevant_via_count == 1
    assert terminal_owned.via_island_pair_aggregates == ()
    contact = terminal_owned.landing_surface_contacts[0]
    assert contact.endpoint_node_id == "NodeExternal"
    assert contact.internal_endpoint_node_id == "NodeInternal"
    assert contact.endpoint_layer == "Signal$PWR"
    assert contact.endpoint_island_id == "island-pwr"
    assert contact.terminal_owner_kind == "decap"
    assert contact.external_endpoint_layer == "Signal$TOP"
    assert contact.padstack == "DR-0102_60"
    assert contact.physical_model_status == "complete"
    assert contact.physical_model_issues == ()
    assert [
        (item.ordinal, item.start_layer, item.end_layer, item.length_um)
        for item in contact.segments
    ] == [(0, "Signal$TOP", "Signal$PWR", 120.0)]

    nonterminal = recover(())
    blocked = nonterminal.via_island_pair_coverage
    assert blocked is not None and blocked.status == "complete"
    assert blocked.terminal_owned_unpaired_count == 0
    assert blocked.unsupported_missing_endpoint_count == 0
    assert blocked.outside_retained_interface_scope_count == 1
    assert blocked.model_relevant_via_count == 0
    assert (
        blocked.outside_retained_interface_scope_via_ids_sha256
        == coverage.terminal_owned_unpaired_via_ids_sha256
    )


def test_nonterminal_unpaired_via_blocks_when_component_joins_two_interfaces(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeOutside!!1::PWR X = 100um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodePwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodeGnd!!1::PWR X = 0um Y = 0um Layer = Signal$GND "
            "PadStack = DR-0102_60"
        ),
        trace_lines="",
        via_lines=(
            "ViaMissing::PWR UpperNode = NodeOutside "
            "LowerNode = NodePwr PadStack = DR-0102_60\n"
            "ViaPaired::PWR UpperNode = NodePwr "
            "LowerNode = NodeGnd PadStack = DR-0102_60"
        ),
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(),
        terminal_owned_via_ids=(),
        target_layers_by_net={"PWR": ("Signal$PWR", "Signal$GND")},
        target_node_surface_resolver=lambda _n, layer, _i, _x, _y: (
            "island-pwr" if layer == "Signal$PWR" else "island-gnd"
        ),
        target_surface_island_ids={
            ("PWR", "Signal$PWR"): ("island-pwr",),
            ("PWR", "Signal$GND"): ("island-gnd",),
        },
    )

    coverage = result.via_island_pair_coverage
    assert coverage is not None and coverage.status == "incomplete"
    assert coverage.raw_target_via_count == 2
    assert coverage.paired_via_count == 1
    assert coverage.terminal_owned_unpaired_count == 0
    assert coverage.unsupported_missing_endpoint_count == 1
    assert coverage.outside_retained_interface_scope_count == 0
    assert coverage.model_relevant_via_count == 2


def test_device_terminal_via_id_has_one_global_owner(tmp_path: Path) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeTop!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodePwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines="",
        via_lines=(
            "Via1::PWR UpperNode = NodeTop LowerNode = NodePwr "
            "PadStack = DR-0102_60"
        ),
    )
    first = SimpleNamespace(
        via_id="Via1",
        net="PWR",
        endpoint_node_id="NodeTop",
        pin_id="SITE0:1",
    )
    second = SimpleNamespace(
        via_id="Via1",
        net="PWR",
        endpoint_node_id="NodePwr",
        pin_id="SITE0:2",
    )

    with pytest.raises(ValueError, match="one physical terminal Via ID"):
        recover_spd_ground_reachability(
            source,
            landings=(),
            terminal_contact_landings=(first, second),
            terminal_owned_via_ids=("Via1",),
            target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
        )


def test_same_net_reachability_can_exclude_lateral_trace_connections(
    tmp_path: Path,
) -> None:
    """A Via-only audit must not treat a distant Trace branch as local reach."""

    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeLocal!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeRemoteTop!!1::PWR X = 1000um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeRemotePwr!!1::PWR X = 1000um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines=(
            "TraceRoute::PWR StartingNode = NodeLocal::PWR "
            "EndingNode = NodeRemoteTop::PWR Width = 0.10mm"
        ),
        via_lines=(
            "ViaRemote::PWR UpperNode = NodeRemoteTop LowerNode = NodeRemotePwr "
            "PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaLocal",
        net="PWR",
        endpoint_node_id="NodeLocal",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    trace_connected = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$PWR",)},
    )
    via_only = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$PWR",)},
        include_traces=False,
    )

    assert trace_connected.reaches(landing, "Signal$PWR")
    assert not via_only.reaches(landing, "Signal$PWR")
    assert via_only.statistics["trace_section_passes"] == 0


def test_mixed_reference_ground_reachability_rejects_source_changed_during_recovery(
    tmp_path: Path,
) -> None:
    source = tmp_path / "changed-ground.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    landing = SpdViaLanding(
        via_id="Via2",
        net="DGND",
        endpoint_node_id="Node4",
        x_um=1200.0,
        y_um=2000.0,
        padstack="DR-0102_60",
    )
    changed = False

    def mutate_after_open(value: int, _message: str) -> None:
        nonlocal changed
        if changed or value < 15:
            return
        before = source.stat()
        os.utime(
            source,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        changed = True

    with pytest.raises(SpdImportError, match="changed during mixed-reference GND"):
        recover_spd_ground_reachability(
            source,
            landings=(landing,),
            target_layers_by_net={"DGND": ("Signal$GND",)},
            progress=mutate_after_open,
        )


def _replace_source_bytes_preserving_size_and_mtime(path: Path) -> None:
    """Model a same-stat replacement that only the recorded source hash catches."""

    before = path.stat()
    original = path.read_bytes()
    replacement = original.replace(b"Title tiny SPD", b"Title tiny SPX", 1)
    assert replacement != original
    assert len(replacement) == len(original)
    path.write_bytes(replacement)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def test_mixed_reference_ground_reachability_rejects_same_stat_source_replacement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "replaced-ground.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    analysis = analyze_spd(source)
    _replace_source_bytes_preserving_size_and_mtime(source)
    landing = SpdViaLanding(
        via_id="Via2", net="DGND", endpoint_node_id="Node4", x_um=1200.0,
        y_um=2000.0, padstack="DR-0102_60",
    )

    with pytest.raises(SpdImportError, match="SHA-256 mismatch"):
        recover_spd_ground_reachability(
            source,
            landings=(landing,),
            target_layers_by_net={"DGND": ("Signal$GND",)},
            expected_source=analysis.source,
        )


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


def test_finite_via_quotient_contracts_only_bridges_and_preserves_cycle(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
NodeQA!!1::PWR X = 10um Y = 0um Layer = Signal$TOP PadStack = DR-ALL
NodeQX!!1::PWR X = 20um Y = 0um Layer = Signal$PWR PadStack = DR-ALL
NodeQB!!1::PWR X = 30um Y = 0um Layer = Signal$TOP PadStack = DR-ALL
NodeQC!!1::PWR X = 40um Y = 0um Layer = Signal$GND PadStack = DR-ALL
NodeQD!!1::PWR X = 30um Y = 10um Layer = Signal$PWR PadStack = DR-ALL
NodeQE!!1::PWR X = 30um Y = 20um Layer = Signal$GND PadStack = DR-ALL
""",
        via_lines="""
ViaQAX::PWR UpperNode = NodeQA LowerNode = NodeQX PadStack = DR-ALL
ViaQXB::PWR UpperNode = NodeQX LowerNode = NodeQB PadStack = DR-ALL
ViaQBC::PWR UpperNode = NodeQB LowerNode = NodeQC PadStack = DR-ALL
ViaQBD::PWR UpperNode = NodeQB LowerNode = NodeQD PadStack = DR-ALL
ViaQDE::PWR UpperNode = NodeQD LowerNode = NodeQE PadStack = DR-ALL
ViaQEB::PWR UpperNode = NodeQE LowerNode = NodeQB PadStack = DR-ALL
""",
        padstack_defs="""
.PadStackDef DR-ALL 0.04mm Material = COPPER
.PadDef Signal$TOP
Regular Circle 0.06mm
.EndPadDef
.PadDef Signal$PWR
Regular Circle 0.06mm
.EndPadDef
.PadDef Signal$GND
Regular Circle 0.06mm
.EndPadDef
.EndPadStackDef
""",
    )
    landing = SimpleNamespace(
        via_id="ViaQAX",
        net="PWR",
        endpoint_node_id="NodeQA",
        pin_id="SITE0:Q1",
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        terminal_contact_landings=(landing,),
        terminal_owned_via_ids=(),
        padstacks=analysis.padstacks,
        stackup_layers=analysis.stackup_layers,
        target_layers_by_net={"PWR": ("Signal$GND",)},
        target_node_surface_resolver=(
            lambda _net, _layer, node_id, _x, _y: (
                "island-gnd" if node_id == "NodeQC" else None
            )
        ),
        target_surface_island_ids={
            ("PWR", "Signal$GND"): ("island-gnd",)
        },
    )

    coverage = result.finite_via_coverage
    assert coverage is not None and coverage.status == "complete"
    assert coverage.raw_target_via_count == 6
    assert coverage.modeled_global_via_count == 6
    assert coverage.pruned_dangling_via_count == 0
    assert coverage.physical_complete_via_count == 6
    assert coverage.physical_incomplete_via_count == 0
    assert coverage.terminal_exclusive_via_count == 0
    canonical_owner_hash = sha256()
    for owner_id in sorted(
        (
            owner_id
            for edge in result.finite_via_edges
            for owner_id in edge.owner_ids
        ),
        key=lambda value: (value.casefold(), value),
    ):
        encoded = owner_id.casefold().encode("utf-8")
        canonical_owner_hash.update(len(encoded).to_bytes(4, "big"))
        canonical_owner_hash.update(encoded)
    assert (
        coverage.modeled_owner_canonical_sha256
        == canonical_owner_hash.hexdigest()
    )
    assert len(result.finite_via_vertices) == 5
    assert len(result.finite_via_edges) == 5
    contracted = [
        item
        for item in result.finite_via_edges
        if item.mode == "contracted_series"
    ]
    assert len(contracted) == 1
    assert contracted[0].raw_via_count == 2
    assert contracted[0].per_path_via_count == 2
    assert sum(item.count for item in contracted[0].series_terms) == 2
    explicit = [
        item
        for item in result.finite_via_edges
        if item.mode == "retained_explicit"
    ]
    assert len(explicit) == 4
    landing_key = ("viaqax", "nodeqa")
    assert result.finite_via_vertex_id_by_landing[landing_key]
    assert (
        result.finite_via_edge_id_by_landing[landing_key]
        == contracted[0].edge_id
    )
    assert {
        role
        for item in result.finite_via_vertices
        for role in item.roles
    } >= {"terminal", "retained_surface", "junction", "cycle_anchor"}


def test_finite_via_scenario_isolation_blocks_raw_top_bypass_and_binds_destination(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
NodeIsoA!!1::PWR X = 10um Y = 0um Layer = Signal$TOP PadStack = DR-ALL
NodeIsoB!!1::PWR X = 20um Y = 0um Layer = Signal$TOP PadStack = DR-ALL
NodeIsoX!!1::PWR X = 10um Y = 10um Layer = Signal$PWR PadStack = DR-ALL
NodeIsoD!!1::PWR X = 10um Y = 20um Layer = Signal$GND PadStack = DR-ALL
""",
        trace_lines="""
TraceIso::PWR StartingNode = NodeIsoA::PWR EndingNode = NodeIsoB::PWR Width = 0.10mm
""",
        via_lines="""
ViaIso::PWR UpperNode = NodeIsoA LowerNode = NodeIsoX PadStack = DR-ALL
ViaDestination::PWR UpperNode = NodeIsoX LowerNode = NodeIsoD PadStack = DR-ALL
""",
        padstack_defs="""
.PadStackDef DR-ALL 0.04mm Material = COPPER
.PadDef Signal$TOP
Regular Circle 0.06mm
.EndPadDef
.PadDef Signal$PWR
Regular Circle 0.06mm
.EndPadDef
.PadDef Signal$GND
Regular Circle 0.06mm
.EndPadDef
.EndPadStackDef
""",
    )
    landing = SimpleNamespace(
        via_id="ViaIso",
        net="PWR",
        endpoint_node_id="NodeIsoA",
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        terminal_contact_landings=(landing,),
        scenario_isolated_terminal_landings=(landing,),
        retarget_destination_requests=(
            ("PWR", "Signal$GND", "NodeIsoD"),
        ),
        terminal_owned_via_ids=("ViaIso",),
        padstacks=analysis.padstacks,
        stackup_layers=analysis.stackup_layers,
        target_layers_by_net={
            "PWR": ("Signal$TOP", "Signal$GND"),
        },
        target_node_surface_resolver=(
            lambda _net, layer, node_id, _x, _y: (
                "island-top"
                if layer == "Signal$TOP"
                and node_id in {"NodeIsoA", "NodeIsoB"}
                else "island-gnd"
                if layer == "Signal$GND" and node_id == "NodeIsoD"
                else None
            )
        ),
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("island-top",),
            ("PWR", "Signal$GND"): ("island-gnd",),
        },
    )

    landing_key = ("viaiso", "nodeisoa")
    isolation = result.finite_via_scenario_isolation_coverage
    assert isolation is not None and isolation.status == "complete"
    assert isolation.requested_landing_count == 1
    assert isolation.isolated_landing_count == 1
    assert isolation.isolated_node_count == 1
    assert isolation.suppressed_artwork_contact_count == 1
    assert isolation.suppressed_trace_edge_count == 1
    assert landing_key in result.finite_via_scenario_isolated_landing_keys
    source_vertex_id = result.finite_via_vertex_id_by_landing[landing_key]
    source_vertex = next(
        item
        for item in result.finite_via_vertices
        if item.vertex_id == source_vertex_id
    )
    assert source_vertex.source_node_count == 1
    assert "retained_surface" not in source_vertex.roles
    first_edge_id = result.finite_via_edge_id_by_landing[landing_key]
    first_edge = next(
        item for item in result.finite_via_edges if item.edge_id == first_edge_id
    )
    assert first_edge.mode == "retained_explicit"
    assert first_edge.owner_ids == ("via:ViaIso",)

    destination_key = ("pwr", "signal$gnd", "nodeisod")
    destination = result.finite_via_retarget_destination_coverage
    assert destination is not None and destination.status == "complete", {
        "coverage": destination,
        "mapping": dict(
            result.finite_via_vertex_id_by_retarget_destination
        ),
        "vertices": result.finite_via_vertices,
        "statistics": dict(result.statistics),
    }
    assert destination.requested_destination_count == 1
    assert destination.resolved_destination_count == 1
    destination_vertex_id = (
        result.finite_via_vertex_id_by_retarget_destination[destination_key]
    )
    destination_vertex = next(
        item
        for item in result.finite_via_vertices
        if item.vertex_id == destination_vertex_id
    )
    assert destination_vertex.net == "PWR"
    assert destination_vertex.layer == "Signal$GND"
    assert destination_vertex.retained_component_island_ids_by_layer == {
        "Signal$GND": ("island-gnd",)
    }


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


def test_recover_spd_via_paths_indexes_more_than_legacy_250k_trace_budget(
    tmp_path: Path,
) -> None:
    edge_count = 250_001
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="\n".join(
            (
                "Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = "
                "Signal$TOP PadStack = DR-0102_60",
                "Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = "
                "Signal$PWR PadStack = DR-0102_60",
                *(
                    f"Node{index + 12}!!1::VDD_CORE/0 X = {index + 2}um "
                    "Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60"
                    for index in range(edge_count + 1)
                ),
            )
        ),
        via_lines=(
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 "
            "LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60"
        ),
        trace_lines="\n".join(
            f"Trace{index}::VDD_CORE/0 StartingNode = "
            f"Node{10 if index == 0 else index + 11}::VDD_CORE/0 "
            f"EndingNode = Node{index + 12}::VDD_CORE/0 Width = 0.10mm"
            for index in range(edge_count)
        ),
    )

    recovery = _recover_power_path(source, analysis)

    assert recovery.evidence_for("ViaRoute", "Signal$PWR") is not None
    assert recovery.statistics["requested"] == 1
    assert recovery.statistics["recovered"] == 1
    assert recovery.statistics["relevant_trace_records_indexed"] == edge_count
    assert recovery.statistics["alternate_exit_trace_nodes_indexed"] == edge_count + 1
    assert not any(
        item.code == "SPD_VIA_PATH_RESOURCE_GUARD"
        for item in recovery.diagnostics
    )


def test_recover_spd_via_paths_retains_structural_evidence_when_target_pad_unsupported(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = "
            "Signal$TOP PadStack = DR-0102_60\n"
            "Node11!!1::VDD_CORE/0 X = 1.25mm Y = 2.5mm Layer = "
            "Signal$PWR PadStack = DR-0102_60"
        ),
        via_lines=(
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 "
            "LowerNode = Node11::VDD_CORE/0 PadStack = DR-UNSUPPORTED"
        ),
        padstack_defs=(
            ".PadStackDef DR-UNSUPPORTED 0.02mm Material = COPPER\n"
            ".PadDef Signal$PWR\n"
            "Regular Oval 0.03mm\n"
            ".EndPadDef\n"
            ".EndPadStackDef"
        ),
    )

    recovery = _recover_power_path(source, analysis)

    assert recovery.evidence_for("ViaRoute", "Signal$PWR") is None
    structural = recovery.structural_evidence_for("ViaRoute", "Signal$PWR")
    assert structural is not None
    assert structural.target_node_id == "Node11"
    assert (structural.target_x_um, structural.target_y_um) == (1250.0, 2500.0)
    assert [item.via_id for item in structural.segments] == ["ViaRoute"]
    assert structural.segments[0].length_um == pytest.approx(120.0)
    assert recovery.statistics["failure_target_pad_unsupported"] == 1
    assert recovery.statistics["structural_recovered"] == 1


def _alternate_exit_budget_source(tmp_path: Path) -> tuple[Path, object]:
    return _recoverable_via_source(
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
""",
        trace_lines=(
            "TraceMesh::VDD_CORE/0 StartingNode = Node10::VDD_CORE/0 "
            "EndingNode = Node12::VDD_CORE/0 Width = 0.10mm"
        ),
    )


def test_recover_spd_via_paths_compact_index_retains_exact_parallel_exit(
    tmp_path: Path,
) -> None:
    source, analysis = _alternate_exit_budget_source(tmp_path)

    recovery = _recover_power_path(source, analysis)

    evidence = recovery.evidence_for("ViaRoute", "Signal$PWR")
    assert evidence is not None
    assert evidence.trace_alternate_exit is True
    assert recovery.statistics["requested"] == 1
    assert recovery.statistics["recovered"] == 1
    assert recovery.statistics["alternate_exit_via_edges_retained"] == 2
    assert recovery.statistics["alternate_exit_nodes_retained"] == 4


def test_recover_spd_via_paths_fails_closed_if_compact_index_cannot_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, analysis = _alternate_exit_budget_source(tmp_path)
    original = spd_io._TRACE_RE

    class _ExhaustedTraceIndex:
        calls = 0

        def finditer(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise MemoryError
            return original.finditer(*args, **kwargs)

    monkeypatch.setattr(spd_io, "_TRACE_RE", _ExhaustedTraceIndex())

    recovery = _recover_power_path(source, analysis)

    assert recovery.evidence_for("ViaRoute", "Signal$PWR") is None
    assert recovery.statistics["requested"] == 1
    assert recovery.statistics["recovered"] == 0
    assert recovery.statistics["resource_guard_fallback"] == 1
    assert recovery.statistics["alternate_exit_via_edges_retained"] == 0
    assert recovery.statistics["alternate_exit_nodes_retained"] == 0
    assert any(item.code == "SPD_VIA_PATH_RESOURCE_GUARD" for item in recovery.diagnostics)


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
        "relevant_trace_records_indexed": 1,
        "alternate_exit_trace_nodes_indexed": 2,
        "alternate_exit_via_edges_indexed": 1,
        "alternate_exit_via_edges_retained": 1,
        "alternate_exit_nodes_retained": 3,
        "alternate_exit_numeric_capacity": 100,
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
    # Start identity participates in the exact cache key because each query
    # excludes its own Via source while considering the other source.
    assert recovery.statistics["alternate_exit_cache_entries"] == 2
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


def test_recover_spd_via_paths_rejects_same_stat_source_replacement(
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
    _replace_source_bytes_preserving_size_and_mtime(source)

    with pytest.raises(SpdImportError, match="SHA-256 mismatch"):
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


def test_recovery_uses_continued_via_rotation_for_rectangular_target_pad(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = RECT_ROUTE
Node11!!1::VDD_CORE/0 X = 1.5mm Y = 2.5mm Layer = Signal$PWR PadStack = NODE_FEATURE
""",
        via_lines=(
            "ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 "
            "LowerNode = Node11::VDD_CORE/0 PadStack = RECT_ROUTE\n"
            "+ AbsoluteRotation = 90"
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
    # Exercise the exact CRLF framing used by Windows-produced SPD files even
    # when this test runs on a platform whose text writer emits bare LF.
    source.write_bytes(
        source.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    )
    analysis = analyze_spd(source)

    recovery = _recover_power_path(source, analysis)

    evidence = recovery.evidence_for("ViaRoute", "Signal$PWR")
    assert evidence is not None
    assert evidence.target_padstack == "RECT_ROUTE"
    assert (evidence.target_pad_width_um, evidence.target_pad_height_um) == (
        200.0,
        100.0,
    )
    assert evidence.segments[-1].rotation_degrees == 90.0


@pytest.mark.parametrize(
    "line_ending", (b"\n", b"\r\n", b"\r"), ids=("lf", "crlf", "cr")
)
def test_via_match_tail_includes_one_immediate_rotation_continuation(
    line_ending: bytes,
) -> None:
    first = (
        b"ViaA::VDD UpperNode = NodeA LowerNode = NodeB PadStack = RECT"
    )
    continuation = b"  + AbsoluteRotation = 90\t"
    second = b"ViaB::VDD UpperNode = NodeB LowerNode = NodeC PadStack = ROUND"
    source = first + line_ending + continuation + line_ending + second

    matches = list(spd_io._VIA_RE.finditer(source))

    assert len(matches) == 2
    assert matches[0].groups() == (
        b"ViaA",
        b"VDD",
        b"NodeA",
        b"NodeB",
        b"RECT",
        line_ending + continuation,
    )
    assert matches[0].group(0) == first + line_ending + continuation
    assert matches[1].groups() == (
        b"ViaB",
        b"VDD",
        b"NodeB",
        b"NodeC",
        b"ROUND",
        b"",
    )


def test_via_match_preserves_same_line_rotation_bytes_and_span() -> None:
    exact = (
        b"ViaA::VDD UpperNode = NodeA LowerNode = NodeB PadStack = RECT "
        b"AbsoluteRotation = 270\t"
    )

    match = spd_io._VIA_RE.fullmatch(exact)

    assert match is not None
    assert match.groups() == (
        b"ViaA",
        b"VDD",
        b"NodeA",
        b"NodeB",
        b"RECT",
        b" AbsoluteRotation = 270\t",
    )
    assert match.span() == (0, len(exact))


def test_via_match_does_not_consume_duplicate_continued_rotation() -> None:
    primary = (
        b"ViaA::VDD UpperNode = NodeA LowerNode = NodeB PadStack = RECT "
        b"AbsoluteRotation = 0"
    )
    continuation = b"\n+ AbsoluteRotation = 90"

    matches = list(spd_io._VIA_RE.finditer(primary + continuation))

    assert len(matches) == 1
    assert matches[0].group(6) == b" AbsoluteRotation = 0"
    assert matches[0].span() == (0, len(primary))


@pytest.mark.parametrize(
    "suffix",
    (
        b"\n+ AbsoluteRotation =",
        b"\n+ Unsupported = 90",
        b"\n+ AbsoluteRotation = 90\n+ AbsoluteRotation = 180",
    ),
    ids=("malformed", "unsupported", "multiple"),
)
def test_via_match_does_not_consume_invalid_continuation_syntax(
    suffix: bytes,
) -> None:
    primary = b"ViaA::VDD UpperNode = NodeA LowerNode = NodeB PadStack = RECT"

    matches = list(spd_io._VIA_RE.finditer(primary + suffix))

    assert len(matches) == 1
    assert matches[0].group(6) == b""
    assert matches[0].span() == (0, len(primary))


def test_orphan_via_rotation_continuation_is_not_a_via_record() -> None:
    orphan = b"+ AbsoluteRotation = 90\n"

    assert list(spd_io._VIA_RE.finditer(orphan)) == []


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
    assert analysis.stackup_layers[1].material == "ABF"
    assert [
        (item.frequency_hz, item.dk, item.df)
        for item in analysis.stackup_layers[1].dielectric_properties
    ] == pytest.approx([(1.0e6, 3.4, 0.005), (1.0e9, 3.3, 0.004)])
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
    assert device_power.source_node_id == "Node1"
    assert device_power.source_layer == "Signal$TOP"
    assert device_power.source_padstack == "DUT"
    assert device_ground.site == "SITE0"
    endpoint_by_pin = {
        item.pin_id: item for item in analysis.device_terminal_via_endpoints
    }
    assert endpoint_by_pin[device_power.pin_id].status == "complete"
    assert endpoint_by_pin[device_power.pin_id].incident_via_id == "Via1"
    assert endpoint_by_pin[device_power.pin_id].incident_padstack == (
        "DR-0102_60"
    )
    assert endpoint_by_pin[device_power.pin_id].candidate_count == 1
    assert endpoint_by_pin[device_ground.pin_id].status == "complete"
    assert endpoint_by_pin[device_ground.pin_id].incident_via_id == "Via2"
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


def test_device_terminal_first_via_status_is_missing_or_ambiguous_without_guessing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "terminal-via-cardinality.spd"
    payload = MINI_SPD.replace(
        "Via1::VDD_CORE/0 UpperNode = Node1 LowerNode = Node3 "
        "PadStack = DR-0102_60\n",
        "Via1::VDD_CORE/0 UpperNode = Node1 LowerNode = Node3 "
        "PadStack = DR-0102_60\n"
        "Via3::VDD_CORE/0 UpperNode = Node1 LowerNode = Node5 "
        "PadStack = DR-0102_60\n",
    ).replace(
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 "
        "PadStack = DR-0102_60\n",
        "",
    )
    source.write_text(payload, encoding="ascii")

    analysis = analyze_spd(source)
    endpoint_by_net = {
        item.net: item for item in analysis.device_terminal_via_endpoints
    }

    assert endpoint_by_net["VDD_CORE/0"].status == (
        "ambiguous_incident_via"
    )
    assert endpoint_by_net["VDD_CORE/0"].candidate_count == 2
    assert endpoint_by_net["VDD_CORE/0"].candidate_via_ids == (
        "Via1",
        "Via3",
    )
    assert endpoint_by_net["VDD_CORE/0"].incident_via_id is None
    assert endpoint_by_net["DGND"].status == "missing_incident_via"
    assert endpoint_by_net["DGND"].candidate_count == 0
    assert any(
        item.code == "SPD_DEVICE_TERMINAL_VIA_INCOMPLETE"
        for item in analysis.diagnostics
    )


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


def test_spd_routing_asset_compile_does_not_swallow_one_shot_cancellation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cancel-routing-asset.spd"
    source.write_text(
        MINI_SPD.replace(
            "* Via description lines",
            "* Trace description lines\n"
            "Trace1::VDD_CORE/0 StartingNode = Node1!!101::VDD_CORE/0 "
            "EndingNode = Node3!!1::VDD_CORE/0 Width = 0.02mm\n"
            "* Via description lines",
        ),
        encoding="ascii",
    )
    compiling_routing = False
    cancelled_once = False

    def progress(_percent: int, message: str) -> None:
        nonlocal compiling_routing
        compiling_routing = message == "Compiling immutable signal-routing evidence"

    def cancelled() -> bool:
        nonlocal cancelled_once
        if compiling_routing and not cancelled_once:
            cancelled_once = True
            return True
        return False

    with pytest.raises(SpdImportError, match="SPD import cancelled"):
        analyze_spd(
            source,
            scope="decap_scenario",
            progress=progress,
            is_cancelled=cancelled,
        )

    assert cancelled_once


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
    # The production hierarchy keeps the two multi-primitive runs batched and
    # directly forwards the final singleton run without an unnecessary GEOS
    # unary-union call.
    assert batches == [2, 2]


def test_ordered_geometry_facade_forwards_progress_and_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = object()
    calls: list[object] = []
    updates: list[tuple[int, str]] = []
    cancellation = lambda: False

    def delegated(
        record: object,
        *,
        progress: object,
        is_cancelled: object,
    ) -> object:
        calls.extend((record, progress, is_cancelled))
        assert callable(progress)
        progress(37, "delegated ordered geometry")
        return marker

    monkeypatch.setattr(core_services, "ordered_spd_geometry", delegated)
    record = {"primitive_order": (("positive_polygon", 0),)}

    actual = core_services._ordered_spd_geometry(
        record,
        progress=lambda value, message: updates.append((value, message)),
        is_cancelled=cancellation,
    )

    assert actual is marker
    assert calls[0] is record
    assert calls[2] is cancellation
    assert updates == [(37, "delegated ordered geometry")]


def test_surface_island_identity_reports_progress_and_cancels_between_parts() -> None:
    from shapely.geometry import MultiPolygon, box

    shape = MultiPolygon(
        [box(index * 3.0, 0.0, index * 3.0 + 1.0, 1.0) for index in range(80)]
    )
    completed: list[int] = []
    islands = core_services._spd_surface_islands(
        layer="Signal$PWR",
        net="VDD/0",
        asset_sha256="a" * 64,
        shape=shape,
        progress=lambda value, _message: completed.append(value),
    )

    assert len(islands) == 80
    assert completed[0] == 0
    assert completed[-1] == 100
    assert completed == sorted(set(completed))

    cancelled = False
    interrupted: list[int] = []

    def progress(value: int, _message: str) -> None:
        nonlocal cancelled
        interrupted.append(value)
        if value >= 25:
            cancelled = True

    with pytest.raises(RuntimeError, match="surface-island compilation cancelled"):
        core_services._spd_surface_islands(
            layer="Signal$PWR",
            net="VDD/0",
            asset_sha256="a" * 64,
            shape=shape,
            progress=progress,
            is_cancelled=lambda: cancelled,
        )
    assert 25 <= interrupted[-1] < 100


def test_mixed_reference_geometry_reports_progress_and_forwards_cancellation() -> None:
    def geometry_record(
        layer: str,
        net: str,
        polygons: tuple[tuple[tuple[float, float], ...], ...],
    ) -> tuple[dict[str, object], bytes]:
        compressed, _uncompressed_bytes = core_services._compress_spd_geometry_payload(
            layer=layer,
            net=net,
            positive_polygons=polygons,
            negative_polygons=(),
            positive_circles=(),
            negative_circles=(),
            primitive_order=tuple(
                ("positive_polygon", index) for index in range(len(polygons))
            ),
            positive_subelement_count=len(polygons),
            negative_subelement_count=0,
            polygon_trace_count=0,
            box_count=0,
        )
        digest = sha256(compressed).hexdigest()
        return (
            {
                "layer": layer,
                "net": net,
                "asset": f"geometry/{net}.spdgeom.zlib",
                "asset_sha256": digest,
            },
            compressed,
        )

    pwr_polygons = tuple(
        (
            (float(index), 0.0),
            (float(index + 2), 0.0),
            (float(index + 2), 10.0),
            (float(index), 10.0),
        )
        for index in range(180)
    )
    pwr, pwr_asset = geometry_record("PWR0", "VDD", pwr_polygons)
    ground, ground_asset = geometry_record(
        "DGND_MIX",
        "DGND",
        (((-10.0, -10.0), (300.0, -10.0), (300.0, 20.0), (-10.0, 20.0)),),
    )
    records = [pwr, ground]
    assets = {
        str(pwr["asset"]): pwr_asset,
        str(ground["asset"]): ground_asset,
    }
    layers = [
        StackupLayer(
            name="PWR0",
            thickness_um=20.0,
            conductivity_s_m=5.8e7,
            pwr_nets=("VDD",),
        ),
        StackupLayer(name="D1", thickness_um=80.0, dk=4.0, df=0.01),
        StackupLayer(
            name="DGND_MIX",
            thickness_um=20.0,
            conductivity_s_m=5.8e7,
            pwr_nets=("DGND", "SIG_RETURN"),
        ),
    ]
    updates: list[int] = []
    certificates = core_services._mixed_reference_certificates(
        records,
        assets,
        layers,
        power_keys={"vdd"},
        ground_keys={"dgnd"},
        progress=lambda value, _message: updates.append(value),
    )

    assert len(certificates) == 1
    assert updates[0] == 0
    assert updates[-1] == 100
    assert updates == sorted(set(updates))
    assert len(updates) >= 10

    cancelled = False
    messages: list[str] = []

    def progress(value: int, message: str) -> None:
        nonlocal cancelled
        messages.append(message)
        if "Constructing and reducing ordered PowerSI primitives" in message:
            cancelled = True

    with pytest.raises(RuntimeError, match="cancelled"):
        core_services._mixed_reference_certificates(
            records,
            assets,
            layers,
            power_keys={"vdd"},
            ground_keys={"dgnd"},
            progress=progress,
            is_cancelled=lambda: cancelled,
        )
    assert any("ordered PowerSI primitives" in message for message in messages)


def test_mixed_reference_defers_pwr_geometry_until_structural_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PWR artwork without a candidate DGND layer must not enter GEOS."""

    def geometry_record(layer: str, net: str) -> tuple[dict[str, object], bytes]:
        compressed, uncompressed_bytes = core_services._compress_spd_geometry_payload(
            layer=layer,
            net=net,
            positive_polygons=(((0.0, 0.0), (10.0, 0.0), (10.0, 10.0)),),
            negative_polygons=(),
            positive_circles=(),
            negative_circles=(),
            primitive_order=(("positive_polygon", 0),),
            positive_subelement_count=1,
            negative_subelement_count=0,
            polygon_trace_count=0,
            box_count=0,
        )
        digest = sha256(compressed).hexdigest()
        return (
            {
                "layer": layer,
                "net": net,
                "asset": f"geometry/{net}.spdgeom.zlib",
                "asset_sha256": digest,
                "uncompressed_bytes": uncompressed_bytes,
            },
            compressed,
        )

    eligible, eligible_content = geometry_record("PWR0", "VDD_ELIGIBLE")
    ground, ground_content = geometry_record("DGND_MIX", "DGND")
    no_candidate, no_candidate_content = geometry_record("PWR_NO_GND", "VDD_SKIP")
    records = [eligible, ground, no_candidate]
    attachments = {
        str(record["asset"]): content
        for record, content in (
            (eligible, eligible_content),
            (ground, ground_content),
            (no_candidate, no_candidate_content),
        )
    }
    layers = [
        StackupLayer(
            name="PWR0", thickness_um=20.0, conductivity_s_m=5.8e7,
            pwr_nets=("VDD_ELIGIBLE",),
        ),
        StackupLayer(name="D1", thickness_um=80.0, dk=4.0, df=0.01),
        StackupLayer(
            name="DGND_MIX", thickness_um=20.0, conductivity_s_m=5.8e7,
            pwr_nets=("DGND", "SIG_RETURN"),
        ),
        StackupLayer(
            name="PWR_NO_GND", thickness_um=20.0, conductivity_s_m=5.8e7,
            pwr_nets=("VDD_SKIP",),
        ),
    ]
    calls: list[str] = []

    def counted_geometry(payload: object):
        from shapely.geometry import box

        assert isinstance(payload, dict)
        calls.append(str(payload["net"]))
        return box(0.0, 0.0, 10.0, 10.0)

    monkeypatch.setattr(core_services, "_ordered_spd_geometry", counted_geometry)
    certificates = core_services._mixed_reference_certificates(
        records,
        attachments,
        layers,
        power_keys={"vdd_eligible", "vdd_skip"},
        ground_keys={"dgnd"},
    )

    assert len(certificates) == 1
    assert calls == ["VDD_ELIGIBLE", "DGND"]


def test_mixed_reference_decodes_only_candidates_and_caches_shared_ground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def geometry_record(layer: str, net: str) -> tuple[dict[str, object], bytes]:
        compressed, _uncompressed_bytes = core_services._compress_spd_geometry_payload(
            layer=layer,
            net=net,
            positive_polygons=(((0.0, 0.0), (10.0, 0.0), (10.0, 10.0)),),
            negative_polygons=(),
            positive_circles=(),
            negative_circles=(),
            primitive_order=(("positive_polygon", 0),),
            positive_subelement_count=1,
            negative_subelement_count=0,
            polygon_trace_count=0,
            box_count=0,
        )
        digest = sha256(compressed).hexdigest()
        return (
            {
                "layer": layer,
                "net": net,
                "asset": f"geometry/{net}.spdgeom.zlib",
                "asset_sha256": digest,
            },
            compressed,
        )

    pwr_a, pwr_a_content = geometry_record("PWR0", "VDD_A")
    pwr_b, pwr_b_content = geometry_record("PWR0", "VDD_B")
    unrelated, unrelated_content = geometry_record("UNRELATED", "VDD_SKIP")
    ground, ground_content = geometry_record("DGND_MIX", "DGND")
    records = [pwr_a, pwr_b, unrelated, ground]
    attachments = {
        str(record["asset"]): content
        for record, content in (
            (pwr_a, pwr_a_content),
            (pwr_b, pwr_b_content),
            (unrelated, unrelated_content),
            (ground, ground_content),
        )
    }
    layers = [
        StackupLayer(
            name="PWR0", thickness_um=20.0, conductivity_s_m=5.8e7,
            pwr_nets=("VDD_A", "VDD_B"),
        ),
        StackupLayer(name="D1", thickness_um=80.0, dk=4.0, df=0.01),
        StackupLayer(
            name="DGND_MIX", thickness_um=20.0, conductivity_s_m=5.8e7,
            pwr_nets=("DGND", "SIG_RETURN"),
        ),
    ]
    original_decode = core_services._decode_spd_geometry_asset
    decoded_digests: list[str] = []
    ordered_nets: list[str] = []

    def counted_decode(digest: str, content: bytes):
        decoded_digests.append(digest)
        return original_decode(digest, content)

    def counted_geometry(payload: object):
        from shapely.geometry import box

        assert isinstance(payload, dict)
        ordered_nets.append(str(payload["net"]))
        return box(0.0, 0.0, 10.0, 10.0)

    monkeypatch.setattr(core_services, "_decode_spd_geometry_asset", counted_decode)
    monkeypatch.setattr(core_services, "_ordered_spd_geometry", counted_geometry)
    certificates = core_services._mixed_reference_certificates(
        records,
        attachments,
        layers,
        power_keys={"vdd_a", "vdd_b", "vdd_skip"},
        ground_keys={"dgnd"},
    )

    assert len(certificates) == 2
    assert decoded_digests == [
        str(pwr_a["asset_sha256"]),
        str(ground["asset_sha256"]),
        str(pwr_b["asset_sha256"]),
    ]
    assert str(unrelated["asset_sha256"]) not in decoded_digests
    assert ordered_nets == ["VDD_A", "DGND", "VDD_B"]


def test_geometry_asset_compression_is_deterministic_roundtrips_and_keeps_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = {
        "layer": "Signal$PWR",
        "net": "VDD_CORE/0",
        "positive_polygons": ([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0]],),
        "negative_polygons": (),
        "positive_circles": (),
        "negative_circles": (),
        "primitive_order": (("positive_polygon", 0),),
        "positive_subelement_count": 1,
        "negative_subelement_count": 0,
        "polygon_trace_count": 0,
        "box_count": 0,
    }

    first, first_size = core_services._compress_spd_geometry_payload(**kwargs)
    second, second_size = core_services._compress_spd_geometry_payload(**kwargs)
    digest = sha256(first).hexdigest()

    assert first == second
    assert first_size == second_size
    decoded = core_services._decode_spd_geometry_asset(digest, first)
    core_services._validate_spd_geometry_payload(
        decoded,
        expected_layer="Signal$PWR",
        expected_net="VDD_CORE/0",
    )
    assert decoded["positive_polygons_um"] == list(kwargs["positive_polygons"])
    geometry_record = {
        "layer": "Signal$PWR",
        "net": "VDD_CORE/0",
        "asset": "geometry/test.spdgeom.zlib",
        "asset_sha256": digest,
        "uncompressed_bytes": first_size,
    }
    assert core_services.spd_plane_geometry_record_payload(
        geometry_record,
        {"geometry/test.spdgeom.zlib": first},
    )["net"] == "VDD_CORE/0"
    with pytest.raises(ValueError, match="decoded size does not match"):
        core_services.spd_plane_geometry_record_payload(
            {**geometry_record, "uncompressed_bytes": first_size + 1},
            {"geometry/test.spdgeom.zlib": first},
        )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        core_services._decode_spd_geometry_asset(digest, first + b"\\x00")

    monkeypatch.setattr(core_services, "_SPD_GEOMETRY_MAX_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(core_services._SpdGeometryAssetTooLarge):
        core_services._compress_spd_geometry_payload(**kwargs)


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

    ground_disclosure = next(
        item.message
        for item in plan.diagnostics
        if item.code == "SPD_GROUND_CONTINUOUS_REFERENCE_ASSUMPTION"
    )
    assert "Layerwise exact retained-surface uniform Maxwell-Y/global Kron" in (
        ground_disclosure
    )
    assert "Research nonuniform modes" in ground_disclosure
    assert "Legacy modal path" in ground_disclosure
    assert "aggregate rectangular higher-mode correction" not in ground_disclosure
    geometry_summary = next(
        item for item in plan.summary_lines if item.startswith("PowerSI PWR geometry:")
    )
    assert "Layerwise exact retained-surface terminal-complete Maxwell-Y/global Kron" in (
        geometry_summary
    )
    assert "no legacy modal one-port difference" in geometry_summary
    assert "synthesized topology-only surface capacitance" in geometry_summary
    assert "synthesized fringing" in geometry_summary

    terminal_certificate = plan.project.metadata["spd_import"][
        "layerwise_device_terminal_via_certificate"
    ]
    assert terminal_certificate["source_sha256"] == analysis.source.sha256
    assert terminal_certificate["status"] == "complete"
    assert terminal_certificate["terminal_count"] == 2
    assert terminal_certificate["complete_terminal_count"] == 2
    assert terminal_certificate["raw_spd_embedded"] is False
    terminal_payload = dict(terminal_certificate)
    terminal_hash = terminal_payload.pop("evidence_sha256")
    assert terminal_hash == core_services._canonical_metadata_sha256(
        terminal_payload
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
    assert "Legacy model boundary: rectangular PWR bounding-box modal cavity" in (
        view.confidence_note
    )
    assert "continuous DGND return" in view.confidence_note
    # Lightweight legacy outcomes predate the profile/provenance contract.
    # They must remain displayable as the conservative default, not research.
    assert view.solver_profile_key == "legacy_modal_v017"
    assert view.solver_profile_badge == "LEGACY"
    assert view.solver_provenance == {}

    outcome.solver_profile_key = "research_uniform_admittance"
    research_view = _evaluation_view(plan.project, outcome)
    assert "Research model boundary: source-only exact-artwork uniform C00" in (
        research_view.confidence_note
    )
    assert "nonuniform modes retain the continuous rectangular-return" in (
        research_view.confidence_note
    )
    assert "exact retained-surface uniform Maxwell-Y" not in (
        research_view.confidence_note
    )

    outcome.solver_profile_key = "layerwise_admittance_v1"
    layerwise_view = _evaluation_view(plan.project, outcome)
    assert "terminal-complete exact retained-surface Maxwell-Y" in (
        layerwise_view.confidence_note
    )
    assert "one global Schur/Kron reduction" in layerwise_view.confidence_note
    assert "external Device-port Zii is used alone" in layerwise_view.confidence_note
    assert "no legacy rectangular higher-mode one-port difference is added" in (
        layerwise_view.confidence_note
    )
    assert "source-proven finite Via links" in layerwise_view.confidence_note
    assert "exact same-layer Trace connectivity" in (
        layerwise_view.confidence_note
    )
    assert "finite Via/Trace links" not in layerwise_view.confidence_note
    assert "topology-only surfaces receive zero synthesized adjacent-gap" in (
        layerwise_view.confidence_note
    )
    assert "no synthesized fringing" in layerwise_view.confidence_note
    assert "all mounted decap terminations" in (
        layerwise_view.confidence_note
    )
    assert "no package/connector coupling" in layerwise_view.confidence_note
    assert "no full-wave claim" in layerwise_view.confidence_note
    assert "exact retained artwork is used by the terminal-complete Maxwell-Y/global Kron" in (
        layerwise_view.confidence_note
    )


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


def test_logical_trace_records_preserve_exact_bytes_digest_and_cardinality() -> None:
    primary = (
        b"TraceSame::VDD/0 StartingNode = NodeA!!1::VDD/0 "
        b"EndingNode = NodeB!!2::VDD/0 Width = 0.125mm\r\n"
    )
    continued = (
        b"TraceContinued::VDD/0 StartingNode = NodeB::VDD/0 "
        b"EndingNode = NodeC::VDD/0\n"
        b"+ Width = 4mil\r\n"
    )
    widthless = (
        b"TraceTopology::VDD/0 StartingNode = NodeC::VDD/0 "
        b"EndingNode = NodeD::VDD/0"
    )
    source = b"* Trace description lines\r\n" + primary + continued + widthless

    records = list(_iter_spd_trace_records(BytesIO(source), 0, len(source)))

    assert len(records) == 3
    assert [item.line_count for item in records] == [1, 2, 1]
    assert [item.line_offsets for item in records] == [
        (len(b"* Trace description lines\r\n"),),
        (
            len(b"* Trace description lines\r\n") + len(primary),
            len(b"* Trace description lines\r\n") + len(primary)
            + len(continued.splitlines(keepends=True)[0]),
        ),
        (len(b"* Trace description lines\r\n") + len(primary) + len(continued),),
    ]
    assert [item.exact_bytes for item in records] == [primary, continued, widthless]
    assert [item.source_sha256 for item in records] == [
        sha256(item).hexdigest() for item in (primary, continued, widthless)
    ]
    assert records[0].width_pm == 125_000_000
    assert records[1].width_pm == 101_600_000
    assert records[1].starting_node_net_evidence == "VDD/0"
    assert records[1].geometry_status == "resolved"
    assert [item.width_location for item in records] == [
        "same_line",
        "continuation",
        "absent",
    ]
    assert records[2].geometry_status == "topology_only"
    assert records[2].issue_codes == ("TRACE_WIDTH_MISSING",)


@pytest.mark.parametrize(
    ("record", "issue_code"),
    (
        (
            b"TraceA::VDD/0 StartingNode = NodeA EndingNode = NodeB "
            b"Width = 0.1mm\n+ Width = 0.2mm\n",
            "TRACE_WIDTH_DUPLICATE",
        ),
        (
            b"TraceA::VDD/0 StartingNode = NodeA EndingNode = NodeB Width =\n",
            "TRACE_WIDTH_MALFORMED",
        ),
        (
            b"TraceA::VDD/0 StartingNode = NodeA EndingNode = NodeB Width = NaN\n",
            "TRACE_WIDTH_NONFINITE",
        ),
        (
            b"TraceA::VDD/0 StartingNode = NodeA EndingNode = NodeB Width = 0mm\n",
            "TRACE_WIDTH_NONPOSITIVE",
        ),
        (
            b"TraceA::VDD/0 StartingNode = NodeA EndingNode = NodeB\n"
            b"+ Unsupported = 0.1mm\n",
            "TRACE_CONTINUATION_UNKNOWN",
        ),
        (
            b"TraceA::VDD/0 StartingNode = NodeA EndingNode = NodeB "
            b"Foo = 1 Width = 0.1mm\n",
            "TRACE_PRIMARY_TAIL_UNKNOWN",
        ),
        (
            b"TraceA::VDD/0 StartingNode = NodeA Width = 0.1mm\n",
            "MALFORMED_TRACE_RECORD",
        ),
    ),
)
def test_logical_trace_record_geometry_errors_are_retained_unresolved(
    record: bytes,
    issue_code: str,
) -> None:
    parsed = list(_iter_spd_trace_records(BytesIO(record), 0, len(record)))

    assert len(parsed) == 1
    assert parsed[0].exact_bytes == record
    assert parsed[0].geometry_status == "unresolved"
    assert parsed[0].width_pm is None
    assert issue_code in parsed[0].issue_codes


def test_orphan_trace_continuation_fails_section_framing() -> None:
    source = b"* Trace description lines\n+ Width = 0.1mm\n"

    with pytest.raises(SpdTraceRecordError, match="orphan Trace continuation") as exc:
        list(_iter_spd_trace_records(BytesIO(source), 0, len(source)))

    assert exc.value.code == "ORPHAN_TRACE_CONTINUATION"


def test_exact_picometre_parser_does_not_round_fractional_source_units() -> None:
    assert _length_pm_exact(b"1e-6um") == 1
    with pytest.raises(ValueError, match="exact integer"):
        _length_pm_exact(b"0.5e-6um")


def test_exact_picometre_parser_is_independent_of_decimal_context() -> None:
    from decimal import localcontext

    with localcontext() as context:
        context.prec = 8
        assert _length_pm_exact(b"123.456789um") == 123_456_789
        with pytest.raises(ValueError, match="exact integer"):
            _length_pm_exact(b"1.00000000000000000000000000001um")


def test_logical_trace_record_detects_field_and_byte_tamper() -> None:
    exact = (
        b"TraceA::VDD/0 StartingNode = NodeA EndingNode = NodeB "
        b"Width = 0.1mm\n"
    )
    record = list(_iter_spd_trace_records(BytesIO(exact), 0, len(exact)))[0]

    with pytest.raises(ValueError, match="parsed fields were tampered"):
        replace(record, source_id="TraceForged")
    tampered = exact.replace(b"NodeA", b"NodeC")
    with pytest.raises(ValueError, match="SHA-256"):
        replace(record, exact_bytes=tampered)

    reparsed = list(
        _iter_spd_trace_records(BytesIO(tampered), 0, len(tampered))
    )[0]
    assert len(record.exact_bytes) == len(reparsed.exact_bytes)
    assert record.source_sha256 == sha256(record.exact_bytes).hexdigest()
    assert reparsed.source_sha256 == sha256(reparsed.exact_bytes).hexdigest()
    assert record.source_sha256 != reparsed.source_sha256


def test_empty_trace_net_is_retained_as_malformed_without_parser_crash() -> None:
    exact = b"TraceA::\n"

    record = list(_iter_spd_trace_records(BytesIO(exact), 0, len(exact)))[0]

    assert record.source_id == "TraceA"
    assert record.net is None
    assert record.geometry_status == "unresolved"
    assert record.width_location == "unresolved"
    assert "MALFORMED_TRACE_RECORD" in record.issue_codes


def test_extreme_trace_width_fails_closed_without_float_overflow() -> None:
    exact = (
        b"TraceHuge::VDD StartingNode = NodeA EndingNode = NodeB Width = "
        + b"9" * 4000
        + b"m\n"
    )

    record = list(_iter_spd_trace_records(BytesIO(exact), 0, len(exact)))[0]

    assert record.geometry_status == "unresolved"
    assert record.width_pm is None
    assert record.width_um is None
    assert record.width_location == "unresolved"
    assert "TRACE_WIDTH_INVALID" in record.issue_codes


def test_trace_record_byte_and_line_work_bounds_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spd_io, "_MAX_TRACE_RECORD_BYTES", 48)
    oversized = b"TraceA::VDD StartingNode = NodeA EndingNode = NodeB\n"
    with pytest.raises(SpdTraceRecordError) as byte_exc:
        list(_iter_spd_trace_records(BytesIO(oversized), 0, len(oversized)))
    assert byte_exc.value.code == "TRACE_RECORD_BYTE_BOUND_EXCEEDED"
    assert byte_exc.value.offset == 0

    monkeypatch.setattr(spd_io, "_MAX_TRACE_RECORD_BYTES", 1024)
    monkeypatch.setattr(spd_io, "_MAX_TRACE_RECORD_LINES", 2)
    too_many_lines = (
        b"TraceA::VDD StartingNode = NodeA EndingNode = NodeB\n"
        b"+ Width = 1mm\n"
        b"+ Width = 2mm\n"
    )
    with pytest.raises(SpdTraceRecordError) as line_exc:
        list(_iter_spd_trace_records(BytesIO(too_many_lines), 0, len(too_many_lines)))
    assert line_exc.value.code == "TRACE_RECORD_LINE_BOUND_EXCEEDED"
    assert line_exc.value.offset == 0


def test_logical_trace_framing_preserves_bare_carriage_return_lines() -> None:
    first = (
        b"TraceA::VDD StartingNode = NodeA EndingNode = NodeB\r"
        b"+ Width = 1mm\r"
    )
    second = b"TraceB::VDD StartingNode = NodeB EndingNode = NodeC\r"
    source = first + second

    records = list(_iter_spd_trace_records(BytesIO(source), 0, len(source)))

    assert [item.exact_bytes for item in records] == [first, second]
    assert records[0].line_offsets == (
        0,
        len(first.splitlines(keepends=True)[0]),
    )
    assert records[0].width_location == "continuation"
    assert records[1].line_offsets == (len(first),)
    assert records[1].width_location == "absent"
