from __future__ import annotations

import mmap
import os
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import math
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
    SpdSurfaceConnectivityComponent,
    SpdTraceRecordError,
    SpdImportError,
    SpdPlaneGeometry,
    _length_um,
    _length_pm_exact,
    _lengths,
    _iter_spd_trace_records,
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
from spd_decap_pi.eligibility import IndexedPlaneGeometry


def test_bulk_length_parser_preserves_units_and_strict_validation() -> None:
    """Bulk geometry parsing reuses matched tokens without relaxing validation."""

    assert _lengths(b"-1mm 2.5mil 3u 4um 0.5m") == pytest.approx(
        [-1000.0, 63.5, 3.0, 4.0, 500_000.0]
    )
    assert _lengths(b"1MM 2e-3mM 3E+2MIL 4uM") == pytest.approx(
        [1_000.0, 2.0, 7_620.0, 4.0]
    )
    with pytest.raises(ValueError, match="SPD length is not finite"):
        _lengths(b"1e309mm")
    assert _length_um(b"+1.25mm") == pytest.approx(1250.0)
    with pytest.raises(ValueError, match="invalid SPD length"):
        _length_um(b"1mm trailing")
    with pytest.raises(ValueError, match="SPD length is not finite"):
        _length_um(b"1e309mm")


def test_invalid_via_id_digest_external_merge_matches_scalar_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = ["ViaZ", "viaß", "VIASS", "VIAΩ", "viaω", "ViaA", "ViaA"]

    def scalar(rows: list[str]) -> str:
        digest = sha256()
        for value in sorted(item.casefold() for item in rows):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
        return digest.hexdigest()

    created: list[BytesIO] = []

    def temporary_file(*, mode: str) -> BytesIO:
        assert mode == "w+b"
        created.append(BytesIO())
        return created[-1]

    monkeypatch.setattr(spd_io, "TemporaryFile", temporary_file)
    expected = scalar(values)
    assert (
        spd_io._canonical_casefolded_ids_digest(values, chunk_size=2).hexdigest()
        == expected
    )
    assert (
        spd_io._canonical_casefolded_ids_digest(
            reversed(values), chunk_size=2
        ).hexdigest()
        == expected
    )
    assert len(created) >= 6 and all(run.closed for run in created)

    created.clear()
    checks = 0

    def cancel_after_first_spill() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise SpdImportError("cancelled")

    with pytest.raises(SpdImportError, match="cancelled"):
        spd_io._canonical_casefolded_ids_digest(
            values, chunk_size=2, check=cancel_after_first_spill
        )
    assert created and all(run.closed for run in created)


def test_node_attribute_padstack_group_is_reachable() -> None:
    """The documented PadStack fallback group must be able to capture."""

    match = spd_io._NODE_ATTR_RE.search(
        b"Node1!!101::VDD X = 0mm Y = 0mm Layer = Signal$TOP PadStack = DUT"
    )

    assert match is not None
    assert match.groups() == (b"0mm", b"0mm", b"DUT")
    without = spd_io._NODE_ATTR_RE.search(b"Node2!!1::VDD X = 1mm Y = 2mm")
    assert without is not None
    assert without.groups() == (b"1mm", b"2mm", None)


MINI_SPD = """Title tiny SPD
* SourceGraphCapability = LEGACY_SOURCE_GRAPH_UNAVAILABLE
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


def test_parsed_pin_and_device_endpoint_preserve_source_node_layer(
    tmp_path: Path,
) -> None:
    source = tmp_path / "pin-source-layer.spd"
    source.write_text(MINI_SPD, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")

    device_pins = {
        pin.pin_id: pin
        for pin in analysis.pins
        if pin.kind == PinKind.DEVICE_BUMP
    }
    assert device_pins["SITE0:101"].source_node_id == "Node1"
    assert device_pins["SITE0:101"].source_layer == "Signal$TOP"
    assert device_pins["SITE0:102"].source_node_id == "Node2"
    assert device_pins["SITE0:102"].source_layer == "Signal$TOP"
    endpoints = {
        endpoint.pin_id: endpoint
        for endpoint in analysis.device_terminal_via_endpoints
    }
    assert endpoints["SITE0:101"].source_layer == "Signal$TOP"
    assert endpoints["SITE0:102"].source_layer == "Signal$TOP"


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
        expected_events = [
            (
                match.start(),
                match.group("shape_name"),
                match.group("primitive_kind"),
                match.group("net"),
                match.group("polarity"),
            )
            for match in spd_io._SHAPE_EVENT_RE.finditer(data, 0, len(data))
        ]
        expected_primitives = [
            (match.start(), match.group(1), match.group(2), match.group(3))
            for match in spd_io._SHAPE_PRIMITIVE_RE.finditer(data, 0, len(data))
        ]
        expected_shape_projection = [
            (match.start(), match.group(1))
            for match in spd_io._SHAPE_RE.finditer(data, 0, len(data))
        ]
        actual_events = [
            (
                match.start(),
                match.group("shape_name"),
                match.group("primitive_kind"),
                match.group("net"),
                match.group("polarity"),
            )
            for match in spd_io._iter_shape_events(
                data, 0, len(data), spd_io._Reporter(None, None)
            )
        ]
        chunks = list(spd_io._iter_line_bounded_chunks(data, 0, len(data)))
        raw = data[:]

    assert actual == expected
    assert actual_events == expected_events
    assert [
        (start, kind, net, polarity)
        for start, shape, kind, net, polarity in actual_events
        if kind is not None
    ] == expected_primitives
    assert [
        (start, shape)
        for start, shape, kind, _net, _polarity in actual_events
        if shape is not None
    ] == expected_shape_projection
    assert len(chunks) > 1
    assert all(end == len(raw) or raw[end - 1 : end] == b"\n" for _start, end in chunks)


def test_chunked_shape_events_check_cancellation_between_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "cancel-chunked-shapes.spd"
    source.write_bytes(
        b".Shape Signal$TOPpkgshape\n"
        b"Polygon1::VDD_A+ 0mm 0mm 1mm 0mm 1mm 1mm\n"
        b".Shape Signal$L01pkgshape\n"
        b"Polygon2::VDD_B+ 0mm 0mm 1mm 0mm 1mm 1mm\n"
        b".Shape Signal$L02pkgshape\n"
    )
    monkeypatch.setattr(spd_io, "_SHAPE_INDEX_CHUNK_BYTES", 48)
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        with pytest.raises(SpdImportError, match="SPD import cancelled"):
            events = []
            iterator = spd_io._iter_shape_events(
                data, 0, len(data), spd_io._Reporter(None, cancelled)
            )
            while True:
                events.append(next(iterator))
        primitive_kinds = [
            event.group("primitive_kind")
            for event in events
            if event.group("primitive_kind") is not None
        ]

    assert checks == 3
    assert primitive_kinds


def test_shape_parser_streaming_preserves_geometry_order_and_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One-pass Shape traversal keeps the legacy geometry contract intact."""

    source = tmp_path / "streaming-shapes.spd"
    source.write_bytes(
        b".Shape Signal$PWRpkgshape\n"
        b"Polygon1::VDD+ 0mm 0mm 2mm 0mm\n"
        b"+ 2mm 2mm 0mm 2mm\n"
        b"Spline2::VDD+ 0mm 0mm 1mm 1mm\n"
        b"Circle3::VDD- Sub-element 1mm 1mm\n"
        b"Polygon4::VDD- Sub-element 0mm 0mm 1mm 0mm 0mm 1mm\n"
        b"Box5::VDD+ 3mm 3mm 1mm 1mm\n"
        b".EndShape\n"
    )
    monkeypatch.setattr(spd_io, "_SHAPE_INDEX_CHUNK_BYTES", 48)
    # The parser must not fall back to the removed header-index traversal.
    monkeypatch.setattr(
        spd_io,
        "_iter_shape_headers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Shape headers should be streamed by _parse_shapes")
        ),
    )

    diagnostics: list[spd_io.SpdDiagnostic] = []
    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        outline, by_layer, nets, geometries = spd_io._parse_shapes(
            data,
            0,
            len(data),
            {"vdd"},
            {"vdd"},
            spd_io._Reporter(None, None),
            diagnostics,
        )

    assert outline is not None
    assert (outline.width_um, outline.height_um) == pytest.approx((2_000.0, 2_000.0))
    assert by_layer == {"signal$pwr": ("VDD",)}
    assert nets == ("VDD",)
    assert len(geometries) == 1
    geometry = geometries[0]
    assert geometry.layer == "Signal$PWR"
    assert geometry.net == "VDD"
    assert geometry.primitive_order == (
        ("positive_polygon", 0),
        ("negative_polygon", 0),
        ("positive_polygon", 1),
    )
    assert geometry.positive_subelement_count == 0
    assert geometry.negative_subelement_count == 1
    assert geometry.box_count == 1
    assert [item.code for item in diagnostics] == [
        "SPD_PLANE_PRIMITIVE_UNSUPPORTED",
        "SPD_PLANE_PRIMITIVE_MALFORMED",
        "SPD_BOX_START_CORNER_SIZE_INTERPRETATION",
    ]

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        with pytest.raises(SpdImportError, match="SPD import cancelled"):
            spd_io._parse_shapes(
                data,
                0,
                len(data),
                {"vdd"},
                {"vdd"},
                spd_io._Reporter(None, cancelled),
                [],
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


def test_ground_reachability_dispatches_target_predicate_by_net_and_layer(
    tmp_path: Path,
) -> None:
    """A shared graph pass can apply a stricter predicate to one target key."""

    source = tmp_path / "per-target-ground-predicate.spd"
    source.write_text(
        MINI_SPD.replace(
            "* Via description lines",
            "\n".join(
                (
                    "* Node description lines",
                    "NodeTarget!!1::DGND X = 1.3mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60",
                    "* Via description lines",
                )
            ),
        ).replace(
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
            "ViaTarget::DGND UpperNode = Node4 LowerNode = NodeTarget PadStack = DR-0102_60",
        ),
        encoding="ascii",
    )
    landing = SpdViaLanding(
        via_id="Via2",
        net="DGND",
        endpoint_node_id="Node4",
        x_um=1200.0,
        y_um=2000.0,
        padstack="DR-0102_60",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        target_node_predicate=lambda *_args: False,
        target_node_predicates_by_key={
            ("dgnd", "signal$gnd"): lambda _net, _layer, node, _x, _y: node
            == "NodeTarget"
        },
    )
    assert result.reaches(landing, "Signal$GND")


def test_ground_reachability_target_batch_bypasses_scalar_predicate(
    tmp_path: Path,
) -> None:
    source = tmp_path / "target-batch-ground.spd"
    source.write_text(
        MINI_SPD.replace(
            "* Via description lines",
            "\n".join(
                (
                    "* Node description lines",
                    "NodeTarget!!1::DGND X = 1.3mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60",
                    "* Via description lines",
                )
            ),
        ).replace(
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
            "ViaTarget::DGND UpperNode = Node4 LowerNode = NodeTarget PadStack = DR-0102_60",
        ),
        encoding="ascii",
    )
    landing = SpdViaLanding(
        via_id="Via2", net="DGND", endpoint_node_id="Node4",
        x_um=1200.0, y_um=2000.0, padstack="DR-0102_60",
    )
    scalar_calls = 0
    progress: list[tuple[int, str]] = []
    def scalar(*_args):
        nonlocal scalar_calls
        scalar_calls += 1
        raise AssertionError("scalar target predicate should not run")
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        target_node_predicate=scalar,
        target_node_predicate_batch=lambda _net, _layer, points: tuple(
            x_um == 1300.0 for x_um, _y_um in points
        ),
        progress=lambda value, message: progress.append((value, message)),
    )
    assert scalar_calls == 0
    assert result.reaches(landing, "Signal$GND")
    assert any(15 < value < 40 for value, _message in progress)


def test_ground_artwork_only_net_is_indexed_without_cross_net_reachability(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeAuxA!!1::DGND_AUX X = 0um Y = 1um Layer = Signal$L10 PadStack = DR-0102_60\n"
            "NodeAuxB!!1::DGND_AUX X = 1um Y = 1um Layer = Signal$L10 PadStack = DR-0102_60"
        ),
        via_lines=(
            "ViaAux::DGND_AUX UpperNode = NodeAuxA::DGND_AUX "
            "LowerNode = NodeAuxB::DGND_AUX PadStack = DR-0102_60"
        ),
        trace_lines="",
    )
    landing = SpdViaLanding(
        via_id="Via2", net="DGND", endpoint_node_id="Node2", x_um=100.0,
        y_um=0.0, padstack="DR-0102_60",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        same_layer_artwork_layers_by_net={"DGND_AUX": ("Signal$L10",)},
        same_layer_artwork_component=lambda *_args: "aux",
    )
    assert not result.reaches(landing, "Signal$GND")
    assert result.statistics["artwork_nodes"] == 2


def test_ground_reachability_releases_interleaved_artwork_layers_after_last_node(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeA!!11::DGND X = 4mm Y = 4mm Layer = Signal$TOP PadStack = DUT\n"
            "NodeB!!12::DGND X = 5mm Y = 5mm Layer = Signal$PWR PadStack = DUT\n"
            "NodeA2!!13::DGND X = 6mm Y = 6mm Layer = Signal$TOP PadStack = DUT"
        ),
        via_lines="",
    )
    releases: list[str] = []
    artwork_batches: list[str] = []
    events: list[str] = []
    def release(net: str, layer: str) -> None:
        events.append("release")
        releases.append(f"{net.casefold()}:{layer.casefold()}")
    landing = SpdViaLanding(
        via_id="Via2", net="DGND", endpoint_node_id="Node2",
        x_um=100.0, y_um=0.0, padstack="DR-0102_60",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$TOP", "Signal$PWR")},
        same_layer_artwork_layers_by_net={"DGND": ("Signal$TOP", "Signal$PWR")},
        same_layer_artwork_components_batch=lambda _net, layer, points: (
            artwork_batches.append(layer.casefold()) or (0,) * len(points)
        ),
        same_layer_artwork_release=release,
        target_node_predicate_batch=lambda _net, _layer, points: (
            events.append("target") or (True,) * len(points)
        ),
    )
    assert result.statistics["node_release_index_passes"] == 0
    assert result.statistics["deferred_artwork_passes"] == 1
    assert result.statistics["deferred_artwork_keys"] == 2
    assert result.statistics["max_live_artwork_shapes"] == 1
    assert "dgnd:signal$pwr" in releases
    assert "dgnd:signal$top" in releases
    assert releases.index("dgnd:signal$top") < releases.index("dgnd:signal$pwr")
    assert artwork_batches
    assert events.index("target") < events.index("release")


def test_ground_reachability_deferred_surface_batches_are_bounded_and_exact(
    tmp_path: Path,
) -> None:
    extra_nodes = "\n".join(
        f"NodeBatch{index}!!1::DGND X = {index + 10}um Y = 0um "
        "Layer = Signal$TOP PadStack = DUT"
        for index in range(4096)
    )
    source = tmp_path / "deferred-surface-batches.spd"
    source.write_text(
        MINI_SPD.replace(
            "* Via description lines",
            extra_nodes + "\n* Via description lines",
        ),
        encoding="ascii",
    )
    landing = SpdViaLanding(
        via_id="Via2", net="DGND", endpoint_node_id="Node2",
        x_um=100.0, y_um=0.0, padstack="DR-0102_60",
    )
    scalar_surface_ids: list[str] = []

    def is_target(x_um: float) -> bool:
        return int(x_um) % 257 == 0

    def scalar_surface(
        _net: str, _layer: str, node_id: str, _x_um: float, _y_um: float,
    ) -> str:
        scalar_surface_ids.append(node_id)
        return "top-island"

    scalar = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$TOP",)},
        target_node_predicate=lambda _net, _layer, _node, x_um, _y_um: is_target(x_um),
        same_layer_artwork_layers_by_net={"DGND": ("Signal$TOP",)},
        same_layer_artwork_component=lambda *_args: "top-component",
        target_node_surface_resolver=scalar_surface,
        target_surface_island_ids={
            ("DGND", "Signal$TOP"): ("top-island",),
        },
    )

    artwork_batch_sizes: list[int] = []
    surface_batch_sizes: list[int] = []
    batch_surface_ids: list[str] = []

    def artwork_batch(
        _net: str, _layer: str, points: tuple[tuple[float, float], ...],
    ) -> tuple[str, ...]:
        artwork_batch_sizes.append(len(points))
        return ("top-component",) * len(points)

    def surface_batch(
        _net: str,
        _layer: str,
        node_ids: tuple[str, ...],
        _points: tuple[tuple[float, float], ...],
    ) -> tuple[str, ...]:
        surface_batch_sizes.append(len(node_ids))
        batch_surface_ids.extend(node_ids)
        return ("top-island",) * len(node_ids)

    batched = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$TOP",)},
        target_node_predicate_batch=lambda _net, _layer, points: tuple(
            is_target(x_um) for x_um, _y_um in points
        ),
        same_layer_artwork_layers_by_net={"DGND": ("Signal$TOP",)},
        same_layer_artwork_components_batch=artwork_batch,
        target_node_surface_resolver_batch=surface_batch,
        target_surface_island_ids={
            ("DGND", "Signal$TOP"): ("top-island",),
        },
    )

    assert len(surface_batch_sizes) > 1
    assert max(artwork_batch_sizes) == max(surface_batch_sizes) == 4096
    assert batch_surface_ids == scalar_surface_ids
    assert batched.reachable_keys == scalar.reachable_keys
    assert batched.target_contacts_by_key == scalar.target_contacts_by_key
    assert batched.target_contact_count_by_key == scalar.target_contact_count_by_key
    assert batched.target_contact_hash_by_key == scalar.target_contact_hash_by_key


def test_ground_reachability_batch_preserves_target_layer_bits_and_bound(
    tmp_path: Path,
) -> None:
    prefix, node_tail = MINI_SPD.split("* Node description lines", 1)
    _old_nodes, via_tail = node_tail.split("* Via description lines", 1)
    source = tmp_path / "target-batch-layers.spd"
    nodes = "\n".join(
        (
            "Node2!!102::DGND X = 0.1mm Y = 0mm Layer = Signal$TOP PadStack = DUT",
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
            "Node6!!2::DGND X = 3.2mm Y = 2mm Layer = Signal$PWR PadStack = CAP",
        )
    )
    source.write_text(
        prefix
        + "* Node description lines\n"
        + nodes
        + "\n* Via description lines\n"
        + "ViaTop::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
        + "ViaPwr::DGND UpperNode = Node4 LowerNode = Node6 PadStack = DR-0102_60"
        + via_tail,
        encoding="ascii",
    )
    landing = SpdViaLanding(
        via_id="ViaTop", net="DGND", endpoint_node_id="Node2",
        x_um=100.0, y_um=0.0, padstack="DR-0102_60",
    )
    batches: list[tuple[str, str, int]] = []
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$TOP", "Signal$PWR")},
        target_node_predicate_batch=lambda net, layer, points: (
            batches.append((layer, "batch", len(points))) or (True,) * len(points)
        ),
    )
    assert max(size for _layer, _kind, size in batches) <= 32768
    assert {layer for layer, _kind, _size in batches} == {"Signal$TOP", "Signal$PWR"}
    assert sum(size for _layer, _kind, size in batches) == 3
    assert result.reaches(landing, "Signal$TOP")
    assert result.reaches(landing, "Signal$PWR")


def test_ground_reachability_deferred_artwork_duplicate_id_preserves_masks_and_last_source(
    tmp_path: Path,
) -> None:
    """Artwork-deferred and immediate target records retain scalar source order."""

    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeDup!!1::DGND X = 1mm Y = 1mm Layer = Signal$TOP PadStack = DUT\n"
            "NodeDup!!1::DGND X = 2mm Y = 2mm Layer = Signal$PWR PadStack = DUT"
        ),
        via_lines=(
            "ViaDup::DGND UpperNode = Node2 LowerNode = NodeDup PadStack = DUT"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaDup", net="DGND", endpoint_node_id="Node2",
        x_um=100.0, y_um=0.0, padstack="DUT",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$TOP", "Signal$PWR")},
        same_layer_artwork_layers_by_net={"DGND": ("Signal$TOP",)},
        same_layer_artwork_components_batch=lambda _net, layer, points: (
            ("top-component",) * len(points)
            if layer.casefold() == "signal$top"
            else (None,) * len(points)
        ),
        target_node_predicate=lambda _net, _layer, node_id, *_coords: node_id == "NodeDup",
    )
    assert result.reaches(landing, "Signal$TOP")
    assert result.reaches(landing, "Signal$PWR")
    top_key = ("viadup", "node2", "signal$top")
    pwr_key = ("viadup", "node2", "signal$pwr")
    assert result.target_contact_count_by_key[top_key] == 1
    assert result.target_contact_count_by_key[pwr_key] == 1
    assert result.target_contact_hash_by_key[top_key] == result.target_contact_hash_by_key[pwr_key]
    assert result.target_contacts_by_key[top_key] == result.target_contacts_by_key[pwr_key]
    assert result.target_contacts_by_key[top_key] == (("NodeDup", 2000.0, 2000.0),)
    assert result.statistics["max_live_artwork_shapes"] == 1


def test_ground_reachability_filters_unrelated_no_artwork_target_components(
    tmp_path: Path,
) -> None:
    """No-artwork target predicates see only requested Trace/Via roots."""

    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeRequested!!1::DGND X = 1mm Y = 2mm Layer = Signal$GND PadStack = DUT\n"
            "NodeConnected!!1::DGND X = 2mm Y = 2mm Layer = Signal$GND PadStack = DUT\n"
            "NodeUnrelated!!1::DGND X = 9mm Y = 9mm Layer = Signal$GND PadStack = DUT"
        ),
        via_lines="",
        trace_lines=(
            "TraceRequested::DGND StartingNode = NodeRequested::DGND "
            "EndingNode = NodeConnected::DGND Width = 1mm"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaRequested",
        net="DGND",
        endpoint_node_id="NodeRequested",
        x_um=1000.0,
        y_um=2000.0,
        padstack="DUT",
    )
    seen: list[str] = []
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        target_node_predicate=lambda _net, _layer, node_id, *_coords: (
            seen.append(node_id) or node_id == "NodeConnected"
        ),
    )
    assert result.reaches(landing, "Signal$GND")
    assert seen == ["NodeRequested", "NodeConnected"]
    assert result.statistics["target_nodes_considered"] == 3
    assert result.statistics["target_nodes_filtered"] == 1
    assert result.statistics["conditional_target_component_passes"] == 0


def test_ground_reachability_root_filter_keeps_requested_standalone_and_via_only(
    tmp_path: Path,
) -> None:
    """Requested Via roots remain exact when Trace is disabled."""

    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeRequested!!1::DGND X = 1mm Y = 2mm Layer = Signal$GND PadStack = DUT\n"
            "NodeViaTarget!!1::DGND X = 2mm Y = 2mm Layer = Signal$GND PadStack = DUT\n"
            "NodeUnrelated!!1::DGND X = 9mm Y = 9mm Layer = Signal$GND PadStack = DUT\n"
            "NodeStandalone!!1::DGND X = 3mm Y = 2mm Layer = Signal$GND PadStack = DUT"
        ),
        via_lines=(
            "ViaRequested::DGND UpperNode = NodeRequested LowerNode = NodeViaTarget PadStack = DUT"
        ),
        trace_lines=(
            "TraceIgnored::DGND StartingNode = NodeViaTarget::DGND "
            "EndingNode = NodeUnrelated::DGND Width = 1mm"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaRequested",
        net="DGND",
        endpoint_node_id="NodeRequested",
        x_um=1000.0,
        y_um=2000.0,
        padstack="DUT",
    )
    standalone = SpdViaLanding(
        via_id="ViaStandalone",
        net="DGND",
        endpoint_node_id="NodeStandalone",
        x_um=3000.0,
        y_um=2000.0,
        padstack="DUT",
    )
    seen: list[str] = []
    result = recover_spd_ground_reachability(
        source,
        landings=(landing, standalone),
        target_layers_by_net={"DGND": ("Signal$GND",)},
        target_node_predicate=lambda _net, _layer, node_id, *_coords: (
            seen.append(node_id) or node_id in {"NodeViaTarget", "NodeStandalone"}
        ),
        include_traces=False,
    )
    assert result.reaches(landing, "Signal$GND")
    assert result.reaches(standalone, "Signal$GND")
    assert seen == ["NodeRequested", "NodeViaTarget", "NodeStandalone"]
    assert result.statistics["trace_section_passes"] == 0
    assert result.statistics["target_nodes_filtered"] == 1


def test_ground_reachability_deferred_filtered_trace_artwork_matches_unfiltered_oracle(
    tmp_path: Path,
) -> None:
    """PWR Trace-to-artwork seams recover filtered contacts with exact hashes."""

    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeTraceStart!!1::VDD_CORE/0 X = -1mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeTraceBoundary!!1::VDD_CORE/0 X = 0mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeTraceTarget!!1::VDD_CORE/0 X = 0.5mm Y = 5mm Layer = Signal$L08 PadStack = DUT"
        ),
        via_lines="",
        trace_lines=(
            "TraceFinite::VDD_CORE/0 StartingNode = NodeTraceStart::VDD_CORE/0 "
            "EndingNode = NodeTraceBoundary::VDD_CORE/0 Width = 2mm"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaTrace",
        net="VDD_CORE/0",
        endpoint_node_id="NodeTraceStart",
        x_um=-1000.0,
        y_um=5000.0,
        padstack="DUT",
    )

    def component(_net: str, _layer: str, *_coords: float) -> str:
        return "component"

    def target(_net: str, _layer: str, node_id: str, *_coords: float) -> bool:
        return node_id == "NodeTraceTarget"

    def trace_contact(_net: str, *_args: object) -> tuple[str, str]:
        return "Signal$L08", "component"

    kwargs = dict(
        landings=(landing,),
        target_layers_by_net={"VDD_CORE/0": ("Signal$L08",)},
        same_layer_artwork_component=component,
        target_node_predicate=target,
        target_trace_contact_predicate=trace_contact,
    )
    oracle = recover_spd_ground_reachability(source, **kwargs)
    filtered = recover_spd_ground_reachability(
        source,
        same_layer_artwork_layers_by_net={"DGND": ("Signal$L08",)},
        **kwargs,
    )
    key = ("viatrace", "nodetracestart", "signal$l08")
    assert filtered.reachable_keys == oracle.reachable_keys
    assert filtered.unreachable_keys == oracle.unreachable_keys
    assert filtered.target_contacts_by_key[key] == oracle.target_contacts_by_key[key]
    assert filtered.target_contact_count_by_key[key] == oracle.target_contact_count_by_key[key]
    assert filtered.target_contact_hash_by_key[key] == oracle.target_contact_hash_by_key[key]
    assert filtered.statistics["conditional_target_component_passes"] == 1
    assert filtered.statistics["conditional_target_components_recovered"] >= 1
    assert oracle.statistics["conditional_target_component_passes"] == 0

    # A generic seam callback without an exact scalar artwork resolver must
    # fail safe to the unfiltered path rather than discard contact candidates.
    no_resolver_kwargs = {**kwargs, "same_layer_artwork_component": None}
    no_resolver_seen: list[str] = []
    no_resolver_kwargs["target_node_predicate"] = (
        lambda _net, _layer, node_id, *_coords: (
            no_resolver_seen.append(node_id) or node_id == "NodeTraceTarget"
        )
    )
    no_resolver = recover_spd_ground_reachability(
        source,
        same_layer_artwork_layers_by_net={"DGND": ("Signal$L08",)},
        **no_resolver_kwargs,
    )
    assert "NodeTraceTarget" in no_resolver_seen
    assert no_resolver.statistics["target_nodes_filtered"] == 0
    assert no_resolver.statistics["conditional_target_component_passes"] == 0


def test_mixed_reference_ground_reachability_bridges_strict_same_layer_artwork(
    tmp_path: Path,
) -> None:
    """Intermediate same-plane copper bridges the retained L06 Node chain.

    This is the reduced topology observed for the production C1301_0 path
    (the retained raw records are Node1247035/Node1247025 on L06 DGND).
    """

    source = tmp_path / "same-layer-artwork-ground.spd"
    payload = MINI_SPD.replace(
        "* Via description lines",
        "\n".join(
            (
                "* Node description lines",
                "NodeL06A!!1::DGND X = 1.5mm Y = 2mm Layer = Signal$L06 PadStack = DR-0102_60",
                "NodeL06B!!1::DGND X = 1.6mm Y = 2mm Layer = Signal$L06 PadStack = DR-0102_60",
                "NodeVoidRoot!!1::DGND X = 2.4mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60",
                "NodeVoid!!1::DGND X = 2.5mm Y = 2mm Layer = Signal$L06 PadStack = DR-0102_60",
                "NodeVoidB!!1::DGND X = 2.6mm Y = 2mm Layer = Signal$L06 PadStack = DR-0102_60",
                "NodeBoundaryRoot!!1::DGND X = 2.1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60",
                "NodeBoundary!!1::DGND X = 2.0mm Y = 2mm Layer = Signal$L06 PadStack = DR-0102_60",
                "NodeBoundaryB!!1::DGND X = 1.9mm Y = 2mm Layer = Signal$L06 PadStack = DR-0102_60",
                "NodeTargetInside!!1::DGND X = 1.6mm Y = 2mm Layer = Signal$L08 PadStack = DR-0102_60",
                "NodeTargetVoid!!1::DGND X = 2.5mm Y = 2mm Layer = Signal$L08 PadStack = DR-0102_60",
                "NodeTargetBoundary!!1::DGND X = 2.0mm Y = 2mm Layer = Signal$L08 PadStack = DR-0102_60",
                "* Via description lines",
            )
        ),
    ).replace(
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
        "\n".join(
            (
                "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
                "ViaInside::DGND UpperNode = Node4 LowerNode = NodeL06A PadStack = DR-0102_60",
                "ViaInsideTarget::DGND UpperNode = NodeL06B LowerNode = NodeTargetInside PadStack = DR-0102_60",
                "ViaVoid::DGND UpperNode = NodeVoidRoot LowerNode = NodeVoid PadStack = DR-0102_60",
                "ViaVoidTarget::DGND UpperNode = NodeVoidB LowerNode = NodeTargetVoid PadStack = DR-0102_60",
                "ViaBoundary::DGND UpperNode = NodeBoundaryRoot LowerNode = NodeBoundary PadStack = DR-0102_60",
                "ViaBoundaryTarget::DGND UpperNode = NodeBoundaryB LowerNode = NodeTargetBoundary PadStack = DR-0102_60",
            )
        ),
    )
    source.write_text(payload, encoding="ascii")
    landings = tuple(
        SpdViaLanding(
            via_id=via,
            net="DGND",
            endpoint_node_id="Node4",
            x_um=1200.0,
            y_um=2000.0,
            padstack="DR-0102_60",
        )
        for via in ("ViaInside", "ViaVoid", "ViaBoundary")
    )
    landings = tuple(
        replace(landing, endpoint_node_id=endpoint)
        for landing, endpoint in zip(
            landings, ("NodeL06A", "NodeVoid", "NodeBoundary"), strict=True
        )
    )
    # Keep the component callback strict: the two interior L06 nodes share one
    # ordered-artwork component, while the void and boundary points do not.
    def component(_net: str, layer: str, x_um: float, _y_um: float) -> object | None:
        return (
            "L06-component"
            if layer == "Signal$L06" and 1000.0 < x_um < 2000.0
            else None
        )

    without_artwork = recover_spd_ground_reachability(
        source,
        landings=landings,
        target_layers_by_net={"DGND": ("Signal$L08",)},
        target_node_predicate=lambda _net, _layer, node_id, _x, _y: node_id
        in {"NodeTargetInside", "NodeTargetVoid", "NodeTargetBoundary"},
    )
    assert not without_artwork.reaches(landings[0], "Signal$L08")
    result = recover_spd_ground_reachability(
        source,
        landings=landings,
        target_layers_by_net={"DGND": ("Signal$L08",)},
        same_layer_artwork_layers_by_net={"DGND": ("Signal$L06",)},
        same_layer_artwork_component=component,
        target_node_predicate=lambda _net, _layer, node_id, _x, _y: node_id
        in {"NodeTargetInside", "NodeTargetVoid", "NodeTargetBoundary"},
    )
    assert result.reaches(landings[0], "Signal$L08")
    assert not result.reaches(landings[1], "Signal$L08")
    assert not result.reaches(landings[2], "Signal$L08")


def test_ordered_artwork_component_rejects_void_and_boundary_points() -> None:
    geometry = SpdPlaneGeometry(
        layer="L06",
        net="DGND",
        positive_polygons_um=(
            ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
            ((20.0, 0.0), (30.0, 0.0), (30.0, 10.0), (20.0, 10.0)),
        ),
        negative_polygons_um=(
            ((4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)),
        ),
        primitive_order=(
            ("positive_polygon", 0),
            ("positive_polygon", 1),
            ("negative_polygon", 0),
        ),
    )
    indexed = IndexedPlaneGeometry.build(geometry)
    assert indexed is not None
    assert indexed.artwork_component(1.0, 1.0) == 0
    assert indexed.artwork_component(1.0e-7, 5.0) == 0
    assert indexed.artwork_component(21.0, 1.0) == 1
    assert indexed.artwork_component(5.0, 5.0) is None
    assert indexed.artwork_component(0.0, 5.0) is None
    assert all(key < 0 for key in indexed._shape_cache)

    invalid = IndexedPlaneGeometry.build(
        SpdPlaneGeometry(
            layer="L07",
            net="DGND",
            positive_polygons_um=(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),),
            negative_polygons_um=(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),),
            primitive_order=(("positive_polygon", 0), ("negative_polygon", 0)),
        )
    )
    assert invalid is not None
    assert invalid.artwork_component(0.5, 0.5) is None
    assert invalid.artwork_shape_built
    invalid.release_artwork_shape()
    assert invalid.artwork_shape_built


def test_ordered_artwork_batch_matches_scalar_for_negative_circle_sliver() -> None:
    """Batch ``within`` keeps scalar ordered-circle sliver semantics."""

    geometry = SpdPlaneGeometry(
        layer="L02",
        net="DGND",
        positive_polygons_um=(
            ((-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)),
        ),
        negative_polygons_um=(),
        negative_circles_um=((0.0, 0.0, 1.0),),
        primitive_order=(("positive_polygon", 0), ("negative_circle", 0)),
    )
    indexed = IndexedPlaneGeometry.build(geometry)
    assert indexed is not None
    singleton = indexed._artwork_components()
    assert singleton is not False
    assert len(singleton[0]) == 1
    theta = math.pi / 256.0
    points = (
        (math.cos(theta) * 0.99996, math.sin(theta) * 0.99996),
        (0.0, 0.0),
        (10.0, 0.0),
        (0.0, 10.0),
    )
    scalar = tuple(indexed.artwork_component(*point) for point in points)
    assert indexed.artwork_components_batch(points) == scalar
    indexed.release_artwork_shape()
    assert indexed.artwork_shape_built
    assert indexed.artwork_components_batch(points) == scalar

    split = SpdPlaneGeometry(
        layer="L06",
        net="DGND",
        positive_polygons_um=(
            ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
        ),
        negative_polygons_um=(
            ((4.0, -1.0), (6.0, -1.0), (6.0, 11.0), (4.0, 11.0)),
        ),
        primitive_order=(("positive_polygon", 0), ("negative_polygon", 0)),
    )
    split_index = IndexedPlaneGeometry.build(split)
    assert split_index is not None
    assert split_index.artwork_component(2.0, 5.0) == 0
    assert split_index.artwork_component(8.0, 5.0) == 1
    assert split_index.artwork_component(5.0, 5.0) is None
    split_points = ((2.0, 5.0), (8.0, 5.0), (5.0, 5.0), (4.0, 5.0), (10.0, 5.0))
    assert split_index.artwork_components_batch(split_points) == tuple(
        split_index.artwork_component(*point) for point in split_points
    )

    contact = split_index.artwork_trace_component(-1.0, 5.0, 5.0, 5.0, 2.0)
    assert contact is not None
    assert contact[0] == 0
    assert 0.0 < contact[1] < 4.0
    assert split_index.artwork_trace_component(-1.0, 20.0, 5.0, 20.0, 2.0) is None
    assert split_index.artwork_trace_component(-1.0, 5.0, 5.0, 5.0, 0.0) is None
    assert split_index.artwork_trace_component(0.0, 0.0, 0.0, 10.0, 2.0) is not None
    assert split_index.artwork_trace_component(-1.0, 5.0, 31.0, 5.0, 2.0) is None


def test_local_artwork_replay_matches_legacy_ordered_geometry() -> None:
    """Spatial-local run replay preserves the legacy final geometry exactly."""

    from shapely.ops import unary_union

    geometry = SpdPlaneGeometry(
        layer="L07",
        net="DGND",
        positive_polygons_um=(
            ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)),
            ((30.0, 0.0), (50.0, 0.0), (50.0, 20.0), (30.0, 20.0)),
            ((18.0, 8.0), (32.0, 8.0), (32.0, 12.0), (18.0, 12.0)),
        ),
        negative_polygons_um=(
            ((4.0, 4.0), (16.0, 4.0), (16.0, 16.0), (4.0, 16.0)),
            ((34.0, 4.0), (46.0, 4.0), (46.0, 16.0), (34.0, 16.0)),
        ),
        primitive_order=(
            ("positive_polygon", 0),
            ("positive_polygon", 1),
            ("negative_polygon", 0),
            ("negative_polygon", 1),
            ("positive_polygon", 2),
        ),
    )
    indexed = IndexedPlaneGeometry.build(geometry)
    assert indexed is not None
    local = indexed._artwork_components()
    assert local is not False
    local_shape = unary_union(local[0])
    legacy = core_services._ordered_spd_geometry(
        {
            "positive_polygons_um": geometry.positive_polygons_um,
            "negative_polygons_um": geometry.negative_polygons_um,
            "positive_circles_um": (),
            "negative_circles_um": (),
            "primitive_order": geometry.primitive_order,
        }
    )
    assert legacy is not None
    assert local_shape.is_valid
    assert local_shape.symmetric_difference(legacy).is_empty


def test_ground_reachability_accepts_only_finite_trace_artwork_contact(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
            node_lines=(
                "NodeTraceStart!!1::DGND X = -1mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
                "NodeTraceBoundary!!1::DGND X = 0mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
                "NodeTraceTarget!!1::DGND X = 0.5mm Y = 5mm Layer = Signal$L08 PadStack = DUT"
        ),
        via_lines="",
        trace_lines=(
            "TraceFinite::DGND StartingNode = NodeTraceStart::DGND "
            "EndingNode = NodeTraceBoundary::DGND Width = 2mm"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaTrace",
        net="DGND",
        endpoint_node_id="NodeTraceStart",
        x_um=-1000.0,
        y_um=5000.0,
        padstack="DR-0102_60",
    )
    no_contact = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$L08",)},
        same_layer_artwork_layers_by_net={"DGND": ("Signal$L08",)},
        same_layer_artwork_component=lambda *_args: "component",
        target_node_predicate=lambda *_args: False,
    )
    assert not no_contact.reaches(landing, "Signal$L08")
    seen_nets: list[str] = []
    def trace_contact(net: str, *_args):
        seen_nets.append(net)
        assert net == "DGND"
        return "Signal$L08", "component"
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$L08",)},
        same_layer_artwork_component=lambda *_args: "component",
        target_node_predicate=lambda _net, _layer, node_id, *_args: node_id
        == "NodeTraceTarget",
        target_trace_contact_predicate=trace_contact,
    )
    assert result.reaches(landing, "Signal$L08")
    assert seen_nets == ["DGND"]


def test_trace_artwork_contact_second_pass_skips_irrelevant_components(
    tmp_path: Path,
) -> None:
    """Only unresolved requested roots may invoke the expensive seam callback."""

    irrelevant_nodes = "\n".join(
        f"NodeIrrelevant{index}A!!1::DGND X = {100 + index}mm Y = 5mm "
        "Layer = Signal$L08 PadStack = DUT\n"
        f"NodeIrrelevant{index}B!!1::DGND X = {100.5 + index}mm Y = 5mm "
        "Layer = Signal$L08 PadStack = DUT"
        for index in range(48)
    )
    irrelevant_traces = "\n".join(
        f"TraceIrrelevant{index}::DGND StartingNode = NodeIrrelevant{index}A::DGND "
        f"EndingNode = NodeIrrelevant{index}B::DGND Width = 2mm"
        for index in range(48)
    )
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeTraceStart!!1::DGND X = -1mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeTraceBoundary!!1::DGND X = 0mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeTraceTarget!!1::DGND X = 0.5mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            + irrelevant_nodes
        ),
        via_lines="",
        trace_lines=(
            "TraceFinite::DGND StartingNode = NodeTraceStart::DGND "
            "EndingNode = NodeTraceBoundary::DGND Width = 2mm\n"
            + irrelevant_traces
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaTrace",
        net="DGND",
        endpoint_node_id="NodeTraceStart",
        x_um=-1000.0,
        y_um=5000.0,
        padstack="DR-0102_60",
    )
    callback_calls: list[str] = []

    def trace_contact(net: str, *_args):
        callback_calls.append(net)
        return "Signal$L08", "component"

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$L08",)},
        same_layer_artwork_component=lambda *_args: "component",
        target_node_predicate=lambda _net, _layer, node_id, *_args: node_id
        == "NodeTraceTarget",
        target_trace_contact_predicate=trace_contact,
    )
    assert result.reaches(landing, "Signal$L08")
    assert callback_calls == ["DGND"]
    assert result.statistics["trace_artwork_conditional_passes"] == 1
    assert result.statistics["trace_artwork_conditional_checks"] == 1
    assert result.statistics["trace_artwork_conditional_successes"] == 1
    no_trace_calls: list[str] = []
    no_trace = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$L08",)},
        same_layer_artwork_component=lambda *_args: "component",
        target_node_predicate=lambda _net, _layer, node_id, *_args: node_id
        == "NodeTraceTarget",
        target_trace_contact_predicate=lambda net, *_args: (
            no_trace_calls.append(net) or ("Signal$L08", "component")
        ),
        include_traces=False,
    )
    assert not no_trace.reaches(landing, "Signal$L08")
    assert no_trace_calls == []
    assert no_trace.statistics["trace_section_passes"] == 0
    assert no_trace.statistics["trace_artwork_conditional_passes"] == 0


def test_trace_artwork_contact_collects_all_target_layers_before_union(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeStart!!1::DGND X = -1mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeL09Start!!1::DGND X = -1mm Y = 6mm Layer = Signal$L09 PadStack = DUT\n"
            "NodeL08Boundary!!1::DGND X = 0mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeL09Boundary!!1::DGND X = 0mm Y = 6mm Layer = Signal$L09 PadStack = DUT\n"
            "NodeL08Target!!1::DGND X = 0.5mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeL09Target!!1::DGND X = 0.5mm Y = 6mm Layer = Signal$L09 PadStack = DUT"
        ),
        via_lines="ViaL09::DGND UpperNode = NodeStart LowerNode = NodeL09Start PadStack = DUT",
        trace_lines=(
            "TraceL08::DGND StartingNode = NodeStart::DGND EndingNode = NodeL08Boundary::DGND Width = 2mm\n"
            "TraceL09::DGND StartingNode = NodeL09Start::DGND EndingNode = NodeL09Boundary::DGND Width = 2mm"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaTrace",
        net="DGND",
        endpoint_node_id="NodeStart",
        x_um=-1000.0,
        y_um=5000.0,
        padstack="DR-0102_60",
    )

    def trace_contact(_net: str, trace_id: str, *_args):
        return ("Signal$L08", "component-08") if trace_id == "TraceL08" else (
            "Signal$L09", "component-09"
        )

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$L08", "Signal$L09")},
        same_layer_artwork_component=lambda _net, layer, *_args: (
            "component-08" if layer.casefold() == "signal$l08" else "component-09"
        ),
        target_node_predicate=lambda _net, _layer, node_id, *_args: node_id
        in {"NodeL08Target", "NodeL09Target"},
        target_trace_contact_predicate=trace_contact,
    )
    assert result.reaches(landing, "Signal$L08")
    assert result.reaches(landing, "Signal$L09")
    assert result.statistics["trace_artwork_conditional_successes"] == 2


def test_trace_artwork_contact_preserves_all_same_layer_candidates(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeStart!!1::DGND X = -1mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeBoundaryA!!1::DGND X = 0mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeBoundaryB!!1::DGND X = 1mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeTargetA!!1::DGND X = 0.5mm Y = 5mm Layer = Signal$L08 PadStack = DUT\n"
            "NodeTargetB!!1::DGND X = 1.5mm Y = 5mm Layer = Signal$L08 PadStack = DUT"
        ),
        via_lines="",
        trace_lines=(
            "TraceA::DGND StartingNode = NodeStart::DGND EndingNode = NodeBoundaryA::DGND Width = 2mm\n"
            "TraceB::DGND StartingNode = NodeStart::DGND EndingNode = NodeBoundaryB::DGND Width = 2mm"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaTrace",
        net="DGND",
        endpoint_node_id="NodeStart",
        x_um=0.0,
        y_um=5000.0,
        padstack="DR-0102_60",
    )

    def trace_contact(_net: str, trace_id: str, *_args):
        return ("Signal$L08", "component-a") if trace_id == "TraceA" else (
            "Signal$L08", "component-b"
        )

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$L08",)},
        same_layer_artwork_component=lambda _net, _layer, x_um, *_args: (
            "component-a" if x_um < 1000.0 else "component-b"
        ),
        target_node_predicate=lambda _net, _layer, node_id, *_args: node_id
        in {"NodeTargetA", "NodeTargetB"},
        target_trace_contact_predicate=trace_contact,
    )
    assert result.reaches(landing, "Signal$L08")
    key = ("viatrace", "nodestart", "signal$l08")
    assert result.target_contact_count_by_key[key] == 2
    assert result.target_contacts_by_key[key] == (("NodeTargetA", 500.0, 5000.0),)


def test_ground_reachability_nearest_contact_tree_preserves_legacy_ties_and_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large components use one deterministic tree without changing witnesses."""

    target_coordinates = {
        "NodeTargetA": (1000.0, 0.0),
        "NodeTargetB": (1000.0, 0.0),
        "NodeTargetZ": (-1000.0, 0.0),
    }
    target_coordinates.update(
        {
            f"NodeTarget{index:02d}": (10_000.0 + index, 2_000.0)
            for index in range(61)
        }
    )
    node_lines = "\n".join(
        [
            "NodeStart!!1::DGND X = 0um Y = 0um Layer = Signal$L08 PadStack = DUT",
            *(
                f"{node_id}!!1::DGND X = {x_um:g}um Y = {y_um:g}um "
                "Layer = Signal$L08 PadStack = DUT"
                for node_id, (x_um, y_um) in target_coordinates.items()
            ),
        ]
    )
    target_ids = tuple(target_coordinates)
    trace_lines = "\n".join(
        [
            "TraceStart::DGND StartingNode = NodeStart::DGND "
            f"EndingNode = {target_ids[0]}::DGND Width = 0.10mm",
            *(
                f"Trace{index}::DGND StartingNode = {target_ids[index]}::DGND "
                f"EndingNode = {target_ids[index + 1]}::DGND Width = 0.10mm"
                for index in range(len(target_ids) - 1)
            ),
        ]
    )
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=node_lines,
        via_lines="",
        trace_lines=trace_lines,
    )
    landings = tuple(
        SpdViaLanding(
            via_id=f"ViaTree{index}",
            net="DGND",
            endpoint_node_id="NodeStart",
            x_um=0.0,
            y_um=0.0,
            padstack="DR-0102_60",
        )
        for index in range(8)
    )

    builds = 0
    queries = 0
    real_tree = spd_io.cKDTree

    class CountingTree:
        def __init__(self, coordinates):
            nonlocal builds
            builds += 1
            self._tree = real_tree(coordinates)

        def query(self, *args, **kwargs):
            nonlocal queries
            queries += 1
            return self._tree.query(*args, **kwargs)

        def query_ball_point(self, *args, **kwargs):
            nonlocal queries
            queries += 1
            return self._tree.query_ball_point(*args, **kwargs)

    monkeypatch.setattr(spd_io, "cKDTree", CountingTree)
    result = recover_spd_ground_reachability(
        source,
        landings=landings,
        target_layers_by_net={"DGND": ("Signal$L08",)},
        target_node_predicate=lambda _net, _layer, node_id, *_args: node_id
        in target_coordinates,
    )

    expected_contacts = tuple(
        sorted(
            (
                (node_id, float(x_um), float(y_um))
                for node_id, (x_um, y_um) in target_coordinates.items()
            ),
            key=lambda item: (item[0].casefold(), item[0], item[1], item[2]),
        )
    )
    expected_hash = sha256(repr(expected_contacts).encode("utf-8")).hexdigest()
    legacy_selected = min(
        expected_contacts,
        key=lambda item: (
            item[1] ** 2 + item[2] ** 2,
            item[0].casefold(),
            item[0],
            item[1],
            item[2],
        ),
    )
    for landing in landings:
        key = (landing.via_id.casefold(), "nodestart", "signal$l08")
        assert result.reaches(landing, "Signal$L08")
        assert result.target_contact_count_by_key[key] == len(expected_contacts)
        assert result.target_contact_hash_by_key[key] == expected_hash
        # NodeTargetA wins both the duplicate-coordinate and equidistant ties,
        # matching the pre-tree exact min key byte-for-byte.
        assert result.target_contacts_by_key[key] == (legacy_selected,)
        assert legacy_selected == ("NodeTargetA", 1000.0, 0.0)
    assert builds == 1
    assert queries == 2 * len(landings)


def test_ground_reachability_tree_preserves_subnormal_legacy_distance_tie(
    tmp_path: Path,
) -> None:
    """Tree radius derives from Python d² for extremely small coordinates."""

    source_x = -5.291516328261232e-157
    source_y = source_x
    target_coordinates = {
        "NodeN000": (8.920401028942637e-155, 8.920401028942637e-155),
        "NodeN006": (8.920401028942635e-155, 8.920401028942635e-155),
    }
    target_coordinates.update(
        {
            f"NodeTarget{index:02d}": (10_000.0 + index, 2_000.0)
            for index in range(62)
        }
    )
    node_lines = "\n".join(
        [
            f"NodeStart!!1::DGND X = {source_x:.17e}um Y = {source_y:.17e}um "
            "Layer = Signal$L08 PadStack = DUT",
            *(
                f"{node_id}!!1::DGND X = {x_um:.17e}um Y = {y_um:.17e}um "
                "Layer = Signal$L08 PadStack = DUT"
                for node_id, (x_um, y_um) in target_coordinates.items()
            ),
        ]
    )
    target_ids = tuple(target_coordinates)
    trace_lines = "\n".join(
        [
            "TraceStart::DGND StartingNode = NodeStart::DGND "
            f"EndingNode = {target_ids[0]}::DGND Width = 0.10mm",
            *(
                f"Trace{index}::DGND StartingNode = {target_ids[index]}::DGND "
                f"EndingNode = {target_ids[index + 1]}::DGND Width = 0.10mm"
                for index in range(len(target_ids) - 1)
            ),
        ]
    )
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=node_lines,
        via_lines="",
        trace_lines=trace_lines,
    )
    landing = SpdViaLanding(
        via_id="ViaSubnormal",
        net="DGND",
        endpoint_node_id="NodeStart",
        x_um=source_x,
        y_um=source_y,
        padstack="DR-0102_60",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$L08",)},
        target_node_predicate=lambda _net, _layer, node_id, *_args: node_id
        in target_coordinates,
    )
    key = ("viasubnormal", "nodestart", "signal$l08")
    assert result.reaches(landing, "Signal$L08")
    # Both Python d² values round to the same subnormal float; the legacy
    # identifier tie-break therefore selects N000, not cKDTree's N006 order.
    assert result.target_contacts_by_key[key] == (
        (
            "NodeN000",
            target_coordinates["NodeN000"][0],
            target_coordinates["NodeN000"][1],
        ),
    )


def test_ground_reachability_nonfinite_source_origin_remains_fail_closed(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeStart!!1::DGND X = 0um Y = 0um Layer = Signal$L08 PadStack = DUT\n"
            "NodeTarget!!1::DGND X = 1mm Y = 0um Layer = Signal$L08 PadStack = DUT"
        ),
        via_lines="",
        trace_lines=(
            "TraceStart::DGND StartingNode = NodeStart::DGND "
            "EndingNode = NodeTarget::DGND Width = 0.10mm"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaNonfinite",
        net="DGND",
        endpoint_node_id="NodeStart",
        x_um=float("inf"),
        y_um=0.0,
        padstack="DR-0102_60",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"DGND": ("Signal$L08",)},
        target_node_predicate=lambda _net, _layer, node_id, *_args: node_id
        == "NodeTarget",
    )
    assert not result.reaches(landing, "Signal$L08")
    assert result.target_contacts_by_key == {}


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


def test_thermal_trace_records_join_same_net_reachability(tmp_path: Path) -> None:
    """PowerSI writes thermal relief copper as a Trace with a Thermal keyword."""

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
            "TraceThermal::PWR Thermal StartingNode = NodeLocal::PWR "
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

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$PWR",)},
    )

    assert result.reaches(landing, "Signal$PWR")
    assert result.statistics["trace_edges"] == 1


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
    # The physical-span correction pass must not drop the proven TOP landing.
    assert evidence.source_node_id == "Node10"


def test_recover_spd_via_path_allows_a_thermal_trace_hop(tmp_path: Path) -> None:
    """A thermal relief continuation is the same one-hop Trace evidence."""

    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="""
Node10!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DR-0102_60
Node11!!1::VDD_CORE/0 X = 1mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
Node12!!1::VDD_CORE/0 X = 1.2mm Y = 2mm Layer = Medium$D1 PadStack = DR-0102_60
Node13!!1::VDD_CORE/0 X = 1.2mm Y = 2mm Layer = Signal$PWR PadStack = DR-0102_60
""",
        via_lines="""
ViaRoute::VDD_CORE/0 UpperNode = Node10::VDD_CORE/0 LowerNode = Node11::VDD_CORE/0 PadStack = DR-0102_60
ViaTarget::VDD_CORE/0 UpperNode = Node12::VDD_CORE/0 LowerNode = Node13::VDD_CORE/0 PadStack = DR-0102_60
""",
        trace_lines="""
TraceThermal::VDD_CORE/0 Thermal StartingNode = Node11::VDD_CORE/0 EndingNode = Node12::VDD_CORE/0 Width = 0.10mm
""",
    )

    evidence = _recover_power_path(source, analysis).evidence_for("ViaRoute", "Signal$PWR")

    assert evidence is not None
    assert evidence.target_node_id == "Node13"
    assert evidence.trace_hops == 1
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


def test_unterminated_connect_block_keeps_following_records(tmp_path: Path) -> None:
    """A missing .EndC must warn, not swallow the .Part/.Component records."""

    source = tmp_path / "unterminated-connect.spd"
    source.write_text(
        MINI_SPD.replace(
            "2 $Package.Node6!!2::DGND\n.EndC\n.Component C1",
            "2 $Package.Node6!!2::DGND\n.Component C1",
        ),
        encoding="ascii",
    )

    analysis = analyze_spd(source, scope="decap_scenario")

    assert any(
        item.code == "CONNECT_UNTERMINATED" and item.severity == "warning"
        for item in analysis.diagnostics
    )
    assert [item.refdes for item in analysis.cap_instances] == ["C1", "C2"]
    assert all(
        item.start_layer == "Signal$TOP" for item in analysis.cap_instances
    )
    assert analysis.counts["device_pins"] == 2
    assert not analysis.has_errors


def test_conductor_layer_without_conductivity_blocks_the_import(
    tmp_path: Path,
) -> None:
    """A conductor row must never be demoted to a dielectric in silence."""

    source = tmp_path / "unresolved-conductivity.spd"
    source.write_text(
        MINI_SPD.replace(
            "Signal$TOP Thickness = 20u Material = COPPER",
            "Signal$TOP Thickness = 20u Material = COPPER_FOIL",
        ),
        encoding="ascii",
    )

    analysis = analyze_spd(source)

    assert any(
        item.code == "LAYER_CONDUCTIVITY_MISSING"
        and item.severity == "error"
        and "Signal$TOP" in item.message
        for item in analysis.diagnostics
    )
    assert analysis.has_errors


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
        (3_000.0, 2_000.0),
        (3_400.0, 2_000.0),
        (3_400.0, 2_600.0),
        (3_000.0, 2_600.0),
    )
    assert geometry.primitive_order[-2:] == (
        ("negative_polygon", 1),
        ("positive_polygon", 2),
    )
    assert analysis.counts["selected_plane_polygon_traces"] == 1
    assert analysis.counts["selected_plane_boxes"] == 1
    assert not analysis.has_errors


def test_box_start_corner_copper_bridges_three_separated_0402_pads(
    tmp_path: Path,
) -> None:
    source = tmp_path / "box-bridged-shared-pad.spd"
    payload = MINI_SPD.replace(
        "* Shape description lines\n.Shape Signal$GNDpkgshape",
        "* Shape description lines\n"
        ".Shape Signal$TOPpkgshape\n"
        "Box7::VDD_CORE/0+ Sub-element 0.9mm 1.9mm 0.66mm 0.2mm\n"
        "Box8::DGND+ Sub-element 0.9mm 2.2mm 0.66mm 0.2mm\n"
        ".EndShape\n"
        ".Shape Signal$GNDpkgshape",
    ).replace(
        "Regular Square 0.10mm",
        "Regular Square 0.20mm",
    ).replace(
        "Node4!!2::DGND X = 1.2mm Y = 2mm "
        "Layer = Signal$TOP PadStack = CAP",
        "Node4!!2::DGND X = 1mm Y = 2.3mm "
        "Layer = Signal$TOP PadStack = CAP",
    ).replace(
        "Node5!!1::VDD_DROP/0 X = 3mm Y = 2mm "
        "Layer = Signal$TOP PadStack = CAP",
        "Node5!!1::VDD_CORE/0 X = 1.23mm Y = 2mm "
        "Layer = Signal$TOP PadStack = CAP",
    ).replace(
        "Node6!!2::DGND X = 3.2mm Y = 2mm "
        "Layer = Signal$TOP PadStack = CAP",
        "Node6!!2::DGND X = 1.23mm Y = 2.3mm "
        "Layer = Signal$TOP PadStack = CAP\n"
        "Node7!!1::VDD_CORE/0 X = 1.46mm Y = 2mm "
        "Layer = Signal$TOP PadStack = CAP\n"
        "Node8!!2::DGND X = 1.46mm Y = 2.3mm "
        "Layer = Signal$TOP PadStack = CAP",
    ).replace(
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 "
        "PadStack = DR-0102_60",
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 "
        "PadStack = DR-0102_60\n"
        "Via3::VDD_CORE/0 UpperNode = Node1 LowerNode = Node7 "
        "PadStack = DR-0102_60\n"
        "Via4::DGND UpperNode = Node2 LowerNode = Node8 "
        "PadStack = DR-0102_60",
    ).replace(
        ".Connect C2 CAP_0402_100NF Checked = 1\n"
        "1 $Package.Node5!!1::VDD_DROP/0\n"
        "2 $Package.Node6!!2::DGND\n"
        ".EndC",
        ".Connect C2 CAP_0402_100NF Checked = 1\n"
        "1 $Package.Node5!!1::VDD_CORE/0\n"
        "2 $Package.Node6!!2::DGND\n"
        ".EndC\n"
        ".Connect C3 CAP_0402_100NF Checked = 1\n"
        "1 $Package.Node7!!1::VDD_CORE/0\n"
        "2 $Package.Node8!!2::DGND\n"
        ".EndC",
    ).replace(
        ".Component C2 3.1mm 2mm StartLayer = Signal$TOP",
        ".Component C2 1.23mm 2.15mm StartLayer = Signal$TOP\n"
        ".Component C3 1.46mm 2.15mm StartLayer = Signal$TOP",
    )
    source.write_text(payload, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")

    assert not analysis.has_errors
    by_refdes = {item.refdes: item for item in analysis.decap_connections}
    assert by_refdes["C1"].kind == "SHARED_ANCHOR"
    assert by_refdes["C2"].kind == "SHARED_DUMMY"
    assert by_refdes["C3"].kind == "SHARED_ANCHOR"
    assert len({by_refdes[refdes].cluster_id for refdes in by_refdes}) == 1
    cluster = analysis.shared_pad_clusters[0]
    assert cluster.member_refdes == ("C1", "C2", "C3")
    assert cluster.anchor_refdes == ("C1", "C3")
    assert cluster.dummy_refdes == ("C2",)
    assert cluster.power_edges == (("C1", "C2"), ("C2", "C3"))
    assert cluster.ground_edges == (("C1", "C2"), ("C2", "C3"))


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


def test_mixed_reference_large_assets_fail_closed_without_global_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def geometry_asset(
        layer: str,
        net: str,
        polygons: tuple[tuple[tuple[float, float], ...], ...],
    ) -> tuple[dict[str, object], bytes]:
        order = tuple(("positive_polygon", index) for index in range(len(polygons)))
        compressed, _ = core_services._compress_spd_geometry_payload(
            layer=layer,
            net=net,
            positive_polygons=polygons,
            negative_polygons=(),
            positive_circles=(),
            negative_circles=(),
            primitive_order=order,
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

    pwr, pwr_bytes = geometry_asset(
        "PWR0",
        "VDD",
        (((20.0, 2.0), (30.0, 2.0), (30.0, 12.0), (20.0, 12.0)),),
    )
    gnd, gnd_bytes = geometry_asset(
        "DGND_MIX",
        "DGND",
        tuple(
            (
                (20.0 + (index % 10), 2.0 + (index // 10 % 10)),
                (20.4 + (index % 10), 2.0 + (index // 10 % 10)),
                (20.0 + (index % 10), 2.4 + (index // 10 % 10)),
            )
            for index in range(2049)
        ),
    )
    failures: list[dict[str, object]] = []
    original_geometry = core_services._ordered_spd_geometry
    ordered_nets: list[str] = []

    def pwr_geometry_only(payload: dict):
        net = str(payload["net"])
        ordered_nets.append(net)
        if net == "DGND":
            raise AssertionError("local budget must fail before DGND union")
        return original_geometry(payload)

    monkeypatch.setattr(core_services, "_ordered_spd_geometry", pwr_geometry_only)
    certificates = core_services._mixed_reference_certificates(
        [pwr, gnd],
        {str(pwr["asset"]): pwr_bytes, str(gnd["asset"]): gnd_bytes},
        [
            StackupLayer(name="PWR0", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("VDD",)),
            StackupLayer(name="D1", thickness_um=80.0, dk=4.0, df=0.01),
            StackupLayer(name="DGND_MIX", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("DGND", "SIG_RETURN")),
        ],
        power_keys={"vdd"},
        ground_keys={"dgnd"},
        failures=failures,
    )
    assert certificates == ()
    assert ordered_nets == ["VDD"]
    assert failures == [
        {
            "rail_net": "VDD",
            "pwr_layer": "PWR0",
            "gnd_layer": "DGND_MIX",
            "gnd_net": "DGND",
            "reason": "retained mixed-reference artwork exceeds bounded certificate geometry budget",
            "code": "SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE",
            "blocking": False,
        }
    ]


def test_mixed_reference_clips_distant_ground_primitives_before_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def geometry_asset(layer: str, net: str, polygons: tuple) -> tuple[dict, bytes]:
        compressed, _ = core_services._compress_spd_geometry_payload(
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
            {"layer": layer, "net": net, "asset": f"geometry/{net}.zlib", "asset_sha256": digest},
            compressed,
        )

    pwr, pwr_bytes = geometry_asset(
        "PWR0", "VDD", (((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),)
    )
    gnd_polygons = (
        ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
        *tuple(
            ((100.0 + i, 100.0), (101.0 + i, 100.0), (100.0 + i, 101.0))
            for i in range(2049)
        ),
    )
    gnd, gnd_bytes = geometry_asset("DGND_MIX", "DGND", gnd_polygons)
    ordered: list[tuple[str, int]] = []

    original_geometry = core_services._ordered_spd_geometry

    def counted_geometry(payload: dict):
        ordered.append((str(payload["net"]), len(payload["primitive_order"])))
        return original_geometry(payload)

    monkeypatch.setattr(core_services, "_ordered_spd_geometry", counted_geometry)
    certificates = core_services._mixed_reference_certificates(
        [pwr, gnd],
        {str(pwr["asset"]): pwr_bytes, str(gnd["asset"]): gnd_bytes},
        [
            StackupLayer(name="PWR0", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("VDD",)),
            StackupLayer(name="D1", thickness_um=80.0, dk=4.0, df=0.01),
            StackupLayer(name="DGND_MIX", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("DGND", "SIG_RETURN")),
        ],
        power_keys={"vdd"},
        ground_keys={"dgnd"},
    )

    assert len(certificates) == 1
    assert ordered == [("VDD", 1), ("DGND", 1)]
    assert certificates[0].pwr_asset_sha256 == pwr["asset_sha256"]
    assert certificates[0].gnd_asset_sha256 == gnd["asset_sha256"]
    assert certificates[0].overlap_fraction == pytest.approx(1.0)
    assert certificates[0].dominant_overlap_component_fraction == pytest.approx(1.0)


def test_mixed_reference_local_positive_negative_cancellation_is_no_overlap() -> None:
    def geometry_asset(
        layer: str,
        net: str,
        positive_polygons: tuple[tuple[tuple[float, float], ...], ...],
        negative_polygons: tuple[tuple[tuple[float, float], ...], ...] = (),
    ) -> tuple[dict[str, object], bytes]:
        order = tuple(
            [("positive_polygon", index) for index in range(len(positive_polygons))]
            + [("negative_polygon", index) for index in range(len(negative_polygons))]
        )
        compressed, _ = core_services._compress_spd_geometry_payload(
            layer=layer,
            net=net,
            positive_polygons=positive_polygons,
            negative_polygons=negative_polygons,
            positive_circles=(),
            negative_circles=(),
            primitive_order=order,
            positive_subelement_count=len(positive_polygons),
            negative_subelement_count=len(negative_polygons),
            polygon_trace_count=0,
            box_count=0,
        )
        digest = sha256(compressed).hexdigest()
        return (
            {
                "layer": layer,
                "net": net,
                "asset": f"geometry/{net}.zlib",
                "asset_sha256": digest,
            },
            compressed,
        )

    local = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
    distant = ((100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0))
    pwr, pwr_bytes = geometry_asset("PWR0", "VDD", (local,))
    gnd, gnd_bytes = geometry_asset("DGND_MIX", "DGND", (distant, local), (local,))
    full_gnd_payload = core_services._decode_spd_geometry_asset(
        str(gnd["asset_sha256"]), gnd_bytes
    )
    full_gnd_shape = core_services._ordered_spd_geometry(full_gnd_payload)
    assert full_gnd_shape is not None
    assert tuple(full_gnd_shape.bounds) == pytest.approx((100.0, 100.0, 110.0, 110.0))
    failures: list[dict[str, object]] = []

    certificates = core_services._mixed_reference_certificates(
        [pwr, gnd],
        {str(pwr["asset"]): pwr_bytes, str(gnd["asset"]): gnd_bytes},
        [
            StackupLayer(name="PWR0", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("VDD",)),
            StackupLayer(name="D1", thickness_um=80.0, dk=4.0, df=0.01),
            StackupLayer(name="DGND_MIX", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("DGND", "SIG_RETURN")),
        ],
        power_keys={"vdd"},
        ground_keys={"dgnd"},
        failures=failures,
    )

    assert certificates == ()
    assert failures == [
        {
            "rail_net": "VDD",
            "pwr_layer": "PWR0",
            "gnd_layer": "DGND_MIX",
            "gnd_net": "DGND",
            "reason": "PWR and DGND artwork do not overlap",
            "code": "SPD_MIXED_REFERENCE_CERTIFICATE_REJECTED",
            "blocking": False,
        }
    ]


def test_mixed_reference_globally_empty_small_ground_remains_blocking() -> None:
    def geometry_asset(
        layer: str,
        net: str,
        positive_polygons: tuple[tuple[tuple[float, float], ...], ...],
        negative_polygons: tuple[tuple[tuple[float, float], ...], ...] = (),
    ) -> tuple[dict[str, object], bytes]:
        order = tuple(
            [("positive_polygon", index) for index in range(len(positive_polygons))]
            + [("negative_polygon", index) for index in range(len(negative_polygons))]
        )
        compressed, _ = core_services._compress_spd_geometry_payload(
            layer=layer,
            net=net,
            positive_polygons=positive_polygons,
            negative_polygons=negative_polygons,
            positive_circles=(),
            negative_circles=(),
            primitive_order=order,
            positive_subelement_count=len(positive_polygons),
            negative_subelement_count=len(negative_polygons),
            polygon_trace_count=0,
            box_count=0,
        )
        digest = sha256(compressed).hexdigest()
        return (
            {
                "layer": layer,
                "net": net,
                "asset": f"geometry/{net}.zlib",
                "asset_sha256": digest,
            },
            compressed,
        )

    local = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
    pwr, pwr_bytes = geometry_asset("PWR0", "VDD", (local,))
    gnd, gnd_bytes = geometry_asset("DGND_MIX", "DGND", (local,), (local,))
    failures: list[dict[str, object]] = []

    certificates = core_services._mixed_reference_certificates(
        [pwr, gnd],
        {str(pwr["asset"]): pwr_bytes, str(gnd["asset"]): gnd_bytes},
        [
            StackupLayer(name="PWR0", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("VDD",)),
            StackupLayer(name="D1", thickness_um=80.0, dk=4.0, df=0.01),
            StackupLayer(name="DGND_MIX", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("DGND", "SIG_RETURN")),
        ],
        power_keys={"vdd"},
        ground_keys={"dgnd"},
        failures=failures,
    )

    assert certificates == ()
    assert failures == [
        {
            "rail_net": "VDD",
            "pwr_layer": "PWR0",
            "gnd_layer": "DGND_MIX",
            "gnd_net": "DGND",
            "reason": "ordered PWR/DGND artwork geometry is invalid or unsupported",
            "code": "SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE",
            "blocking": True,
        }
    ]

    distant = (
        (100.0, 100.0),
        (110.0, 100.0),
        (110.0, 110.0),
        (100.0, 110.0),
    )
    distant_gnd, distant_gnd_bytes = geometry_asset(
        "DGND_MIX", "DGND", (distant,), (distant,)
    )
    distant_failures: list[dict[str, object]] = []
    distant_certificates = core_services._mixed_reference_certificates(
        [pwr, distant_gnd],
        {
            str(pwr["asset"]): pwr_bytes,
            str(distant_gnd["asset"]): distant_gnd_bytes,
        },
        [
            StackupLayer(name="PWR0", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("VDD",)),
            StackupLayer(name="D1", thickness_um=80.0, dk=4.0, df=0.01),
            StackupLayer(name="DGND_MIX", thickness_um=20.0, conductivity_s_m=5.8e7, pwr_nets=("DGND", "SIG_RETURN")),
        ],
        power_keys={"vdd"},
        ground_keys={"dgnd"},
        failures=distant_failures,
    )
    assert distant_certificates == ()
    assert distant_failures[0]["code"] == "SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE"
    assert distant_failures[0]["blocking"] is True


def test_spd_geometry_clip_checks_cancellation_while_scanning() -> None:
    polygons = tuple(
        ((float(index), 0.0), (float(index) + 0.5, 0.0), (float(index), 0.5))
        for index in range(600)
    )
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    with pytest.raises(RuntimeError, match="geometry clipping cancelled"):
        core_services._clip_spd_geometry_payload(
            {
                "positive_polygons_um": polygons,
                "negative_polygons_um": (),
                "positive_circles_um": (),
                "negative_circles_um": (),
                "primitive_order": tuple(
                    ("positive_polygon", index) for index in range(len(polygons))
                ),
            },
            (0.0, 0.0, 1_000.0, 1.0),
            max_primitives=1_000,
            is_cancelled=cancelled,
        )

    assert calls == 3


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

    # Scenario import retains every explicit PowerNets row with positive plane
    # geometry, even when PowerSI marks the row unselected for simulation. DNP
    # component mounting remains an independent property.
    assert analysis.power_plane_nets == ("VDD_CORE/0", "VDD_DROP/0")
    assert any(item.net == "VDD_DROP/0" for item in analysis.plane_geometries)
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


def test_distant_mixed_ground_is_nonblocking_when_pure_pair_exists(
    tmp_path: Path,
) -> None:
    source = tmp_path / "distant-mixed-ground.spd"
    payload = MINI_SPD.replace(
        ".Shape Signal$GNDpkgshape\n"
        "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
        ".EndShape",
        ".Shape Signal$GND_PUREpkgshape\n"
        "Polygon10::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
        ".EndShape\n"
        ".Shape Signal$GND_MIXpkgshape\n"
        "Polygon1::DGND+ 50mm 50mm 60mm 50mm 60mm 60mm 50mm 60mm\n"
        "Polygon11::SIG_RETURN+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
        ".EndShape",
    ).replace(
        "Signal$TOP Thickness = 20u Material = COPPER\n"
        "Medium$D1 Thickness = 0.10mm Material = ABF\n"
        "Signal$PWR Thickness = 20u Material = COPPER\n"
        "Medium$D2 Thickness = 100um Material = ABF\n"
        "Signal$GND Thickness = 20u Material = COPPER",
        "Signal$TOP Thickness = 20u Material = COPPER\n"
        "Medium$D0 Thickness = 100um Material = ABF\n"
        "Signal$GND_PURE Thickness = 20u Material = COPPER\n"
        "Medium$D1 Thickness = 20um Material = ABF\n"
        "Signal$PWR Thickness = 20u Material = COPPER\n"
        "Medium$D2 Thickness = 100um Material = ABF\n"
        "Signal$GND_MIX Thickness = 20u Material = COPPER",
    )
    source.write_text(payload, encoding="ascii")

    analysis = analyze_spd(source, scope="decap_scenario")
    plan = build_spd_import_plan(create_workspace_state().project, analysis, source)

    rail = next(item for item in plan.project.rails if item.net == "VDD_CORE/0")
    assert plan.can_apply, [
        (item.code, item.message)
        for item in plan.diagnostics
        if item.severity == "error"
    ]
    assert rail.gnd_layer == "Signal$GND_PURE"
    assert plan.mixed_reference_certificates == ()
    failures = plan.project.metadata["spd_import"][
        "mixed_reference_certificate_failures"
    ]
    assert any(
        item["gnd_layer"] == "Signal$GND_MIX"
        and item["reason"] == "PWR and DGND artwork do not overlap"
        and not item["blocking"]
        for item in failures
    )
    assert not any(
        item.code == "SPD_MIXED_REFERENCE_GEOMETRY_UNAVAILABLE"
        for item in plan.diagnostics
    )


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
    assert certificate in plan.mixed_reference_certificates
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
    # Lightweight legacy outcomes predate the profile/provenance contract.
    # They must remain displayable as the conservative default, not research.
    assert view.solver_profile_key == "legacy_modal_v017"
    assert view.solver_profile_badge == "LEGACY"
    assert view.solver_provenance == {}


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


def test_source_unselected_powernet_with_positive_shape_is_materialized(
    tmp_path: Path,
) -> None:
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

    assert analysis.power_plane_nets == ("VDD_CORE/0", "SIG_DATA")
    assert any(item.net == "SIG_DATA" for item in analysis.plane_geometries)


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


def test_build_spd_import_plan_reuses_only_validated_geometry_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "geometry-reuse.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    analysis = analyze_spd(source, scope="decap_scenario")
    calls = 0
    original = core_services._spd_plane_geometry_assets

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(core_services, "_spd_plane_geometry_assets", counted)
    current = create_workspace_state().project
    first = build_spd_import_plan(current, analysis, source)
    second = build_spd_import_plan(current, analysis, source, _geometry_from_plan=first)
    third = build_spd_import_plan(current, analysis, source, _geometry_from_plan=first)
    assert calls == 1
    first_geometry = first.project.metadata["spd_import"]["plane_geometries"]
    assert second.project.model_dump(mode="json") == first.project.model_dump(mode="json")
    assert second.project.metadata["spd_import"]["plane_geometries"] == first_geometry
    assert second.attachments == first.attachments
    geometry_names = [item["asset"] for item in first_geometry]
    assert {
        name: second.attachments[name] for name in geometry_names
    } == {name: first.attachments[name] for name in geometry_names}
    assert third.attachments == second.attachments

    tampered_name = geometry_names[0]
    tampered = replace(
        first,
        attachments={**first.attachments, tampered_name: b"tampered"},
    )
    build_spd_import_plan(current, analysis, source, _geometry_from_plan=tampered)
    assert calls == 2

    changed_ground = current.model_copy(
        update={"gnd_aliases": (*current.gnd_aliases, "EXTRA_GROUND")}
    )
    changed_donor = replace(first, project=changed_ground)
    build_spd_import_plan(current, analysis, source, _geometry_from_plan=changed_donor)
    assert calls == 3


def test_netlist_full_power_inventory_preserves_other_selection_boundaries(
    tmp_path: Path,
) -> None:
    source = tmp_path / "inventory-netlist.spd"
    source.write_text(
        ".NetList\n"
        "SIG_BEFORE::Unselected||DropShape Color = YELLOW\n"
        "DGND -> GroundNets Color = RED\n"
        "SENSE_GND::Unselected||DropShape Color = GREEN\n"
        "VDD_MAIN/0 -> PowerNets::Unselected||DropShape Color = BLUE\n"
        "VDD_AUX/0 Color = CYAN\n"
        "VDD_DROP/0::Unselected||DropShape Color = MAGENTA\n"
        ".EndNetList\n",
        encoding="ascii",
    )

    default_diagnostics = []
    inventory_diagnostics = []
    with source.open("rb") as handle, mmap.mmap(
        handle.fileno(), 0, access=mmap.ACCESS_READ
    ) as data:
        selected_power, selected_ground = _parse_netlist(
            data,
            {"dgnd", "sense_gnd"},
            default_diagnostics,
        )
        inventory_power, inventory_ground = _parse_netlist(
            data,
            {"dgnd", "sense_gnd"},
            inventory_diagnostics,
            include_unselected_power=True,
        )

    # Destination metadata starts the group; only source-token metadata marks
    # that row unselected. GroundNets selection never expands with PowerNets.
    assert selected_power == ("VDD_MAIN/0", "VDD_AUX/0")
    assert inventory_power == ("VDD_MAIN/0", "VDD_AUX/0", "VDD_DROP/0")
    assert selected_ground == inventory_ground == ("DGND",)
    assert "SIG_BEFORE" not in inventory_power
    assert "SENSE_GND" not in inventory_ground


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
    shared_root_landing = replace(
        landing,
        via_id="SharedRootLanding",
        endpoint_node_id="NodeTopA1",
        x_um=0.0,
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
        landings=(landing, shared_root_landing),
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
    landing_islands = result.surface_islands_by_landing[
        ("viafromsamecopper", "nodetopa2")
    ]
    shared_root_islands = result.surface_islands_by_landing[
        ("sharedrootlanding", "nodetopa1")
    ]
    landing_layers = result.surface_layers_by_landing[
        ("viafromsamecopper", "nodetopa2")
    ]
    shared_root_layers = result.surface_layers_by_landing[
        ("sharedrootlanding", "nodetopa1")
    ]
    assert landing_islands == (
        "island-pwr",
        "island-top-a",
        "island-top-b",
    )
    assert shared_root_islands is landing_islands
    assert landing_layers == ("Signal$PWR", "Signal$TOP")
    assert shared_root_layers is landing_layers
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
            "PadStack = DR-0102_60\n"
            "NodeTopAlt!!1::PWR X = 2um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-ALT\n"
            "NodePwrAlt!!1::PWR X = 2um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-ALT\n"
            "NodeTopOther!!1::PWR X = 100um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodePwrOther!!1::PWR X = 100um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines="",
        via_lines=(
            "ViaOwned::PWR UpperNode = NodeTopOwned "
            "LowerNode = NodePwrOwned PadStack = DR-0102_60\n"
            "ViaSubstrate::PWR UpperNode = NodeTopSubstrate "
            "LowerNode = NodePwrSubstrate PadStack = DR-0102_60\n"
            "ViaAlt::PWR UpperNode = NodeTopAlt "
            "LowerNode = NodePwrAlt PadStack = DR-ALT\n"
            "ViaOther::PWR UpperNode = NodeTopOther "
            "LowerNode = NodePwrOther PadStack = DR-0102_60"
        ),
        padstack_defs=(
            ".PadStackDef DR-ALT 0.03mm Material = COPPER\n"
            ".PadDef Signal$TOP\n"
            "Regular Circle 0.04mm\n"
            ".EndPadDef\n"
            ".PadDef Signal$PWR\n"
            "Regular Circle 0.04mm\n"
            ".EndPadDef\n"
            ".EndPadStackDef"
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
        target_node_surface_resolver=lambda _n, layer, _i, x, _y: (
            ("island-top" if layer == "Signal$TOP" else "island-pwr")
            + ("-other" if x > 50.0 else "")
        ),
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("island-top", "island-top-other"),
            ("PWR", "Signal$PWR"): ("island-pwr", "island-pwr-other"),
        },
    )

    assert len(result.via_island_pair_aggregates) == 3
    assert [
        (
            item.padstack,
            item.start_island_id,
            item.end_island_id,
        )
        for item in result.via_island_pair_aggregates
    ] == [
        ("DR-0102_60", "island-top", "island-pwr"),
        ("DR-0102_60", "island-top-other", "island-pwr-other"),
        ("DR-ALT", "island-top", "island-pwr"),
    ]
    aggregate = next(
        item
        for item in result.via_island_pair_aggregates
        if item.padstack == "DR-0102_60"
        and item.start_island_id == "island-top"
        and item.end_island_id == "island-pwr"
    )
    alternate = next(
        item
        for item in result.via_island_pair_aggregates
        if item.padstack == "DR-ALT"
    )
    unrelated = next(
        item
        for item in result.via_island_pair_aggregates
        if item.start_island_id == "island-top-other"
    )
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
    assert (
        alternate.start_island_id,
        alternate.end_island_id,
        alternate.count,
    ) == ("island-top", "island-pwr", 1)
    assert (
        unrelated.padstack,
        unrelated.start_island_id,
        unrelated.end_island_id,
        unrelated.count,
    ) == (
        "DR-0102_60",
        "island-top-other",
        "island-pwr-other",
        1,
    )
    assert unrelated.segments == aggregate.segments
    assert unrelated.segments[0] is aggregate.segments[0]
    shared_finite_terms = [
        term
        for edge in result.finite_via_edges
        for term in edge.series_terms
        if (
            term.padstack,
            term.start_layer,
            term.end_layer,
        ) == ("DR-0102_60", "Signal$TOP", "Signal$PWR")
    ]
    assert len(shared_finite_terms) == 3
    assert all(
        term.segments == aggregate.segments
        and term.segments[0] is aggregate.segments[0]
        for term in shared_finite_terms
    )
    assert result.statistics["terminal_owned_via_id_count"] == 1
    assert result.statistics["terminal_owned_via_observed_count"] == 1
    assert result.statistics["via_island_pair_terminal_owned_record_count"] == 1
    assert result.statistics["via_island_pair_substrate_record_count"] == 3
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
        and item.padstack == "DR-0102_60"
        and item.physical_model_status == "complete"
        and item.physical_model_issues == ()
        and item.segments == aggregate.segments
        for item in result.landing_surface_contacts
    )
    coverage = result.via_island_pair_coverage
    assert coverage is not None and coverage.status == "complete"
    assert coverage.raw_target_via_count == coverage.paired_via_count == 4
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


def test_unresolved_terminal_endpoint_does_not_copy_multilayer_component(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeExternal!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeInternal!!1::PWR X = 0um Y = 0um Layer = Signal$MID "
            "PadStack = DR-0102_60\n"
            "NodePwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodeGnd!!1::PWR X = 0um Y = 0um Layer = Signal$GND "
            "PadStack = DR-0102_60"
        ),
        via_lines=(
            "ViaTerminal::PWR UpperNode = NodeExternal "
            "LowerNode = NodeInternal PadStack = DR-0102_60\n"
            "ViaPwr::PWR UpperNode = NodeInternal "
            "LowerNode = NodePwr PadStack = DR-0102_60\n"
            "ViaGnd::PWR UpperNode = NodeInternal "
            "LowerNode = NodeGnd PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaTerminal",
        net="PWR",
        endpoint_node_id="NodeExternal",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(),
        terminal_contact_landings=(landing,),
        terminal_owned_via_ids=("ViaTerminal",),
        target_layers_by_net={"PWR": ("Signal$PWR", "Signal$GND")},
        target_node_surface_resolver=lambda _n, layer, _i, _x, _y: (
            "island-pwr" if layer == "Signal$PWR" else
            "island-gnd" if layer == "Signal$GND" else None
        ),
        target_surface_island_ids={
            ("PWR", "Signal$PWR"): ("island-pwr",),
            ("PWR", "Signal$GND"): ("island-gnd",),
        },
    )

    contact = result.landing_surface_contacts[0]
    assert result.surface_layers_by_landing[contact.landing_key] == (
        "Signal$GND",
        "Signal$PWR",
    )
    assert contact.internal_endpoint_node_id == "NodeInternal"
    assert dict(contact.contact_island_ids_by_layer) == {}
    assert contact.physical_model_status == "incomplete"
    assert contact.physical_model_issues == (
        "internal_endpoint_equivalence_component_missing",
    )


def test_terminal_contact_keeps_full_internal_equivalence_component(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeExternal!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeInternal!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodeInternalPeer!!1::PWR X = 10um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines=(
            "TraceInternal::PWR StartingNode = NodeInternal::PWR "
            "EndingNode = NodeInternalPeer::PWR Width = 0.10mm"
        ),
        via_lines=(
            "ViaTerminal::PWR UpperNode = NodeExternal "
            "LowerNode = NodeInternal PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaTerminal",
        net="PWR",
        endpoint_node_id="NodeExternal",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(),
        terminal_contact_landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
        target_node_surface_resolver=lambda _n, layer, _i, x, _y: (
            "island-top" if layer == "Signal$TOP" else
            "island-pwr-a" if x < 5.0 else "island-pwr-b"
        ),
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("island-top",),
            ("PWR", "Signal$PWR"): (
                "island-pwr-a",
                "island-pwr-b",
            ),
        },
    )

    contact = result.landing_surface_contacts[0]
    assert contact.internal_endpoint_node_id == "NodeInternal"
    assert dict(contact.contact_island_ids_by_layer) == {
        "Signal$PWR": ("island-pwr-a", "island-pwr-b"),
    }
    aggregate = result.via_island_pair_aggregates[0]
    assert {
        aggregate.start_layer: aggregate.start_component_island_ids,
        aggregate.end_layer: aggregate.end_component_island_ids,
    } == {
        "Signal$TOP": ("island-top",),
        "Signal$PWR": ("island-pwr-a", "island-pwr-b"),
    }
    assert any(
        dict(vertex.retained_component_island_ids_by_layer).get("Signal$PWR")
        == ("island-pwr-a", "island-pwr-b")
        for vertex in result.finite_via_vertices
    )


def test_landing_layer_index_preserves_many_exact_contact_hashes(
    tmp_path: Path,
) -> None:
    landing_count = 12
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines="\n".join(
            line
            for index in range(landing_count)
            for line in (
                f"NodeLanding{index}!!1::PWR X = {index}um Y = 0um "
                "Layer = Signal$TOP PadStack = DR-0102_60",
                f"NodePwr{index}!!1::PWR X = {index}um Y = 0um "
                "Layer = Signal$PWR PadStack = DR-0102_60",
                f"NodeGnd{index}!!1::PWR X = {index}um Y = 0um "
                "Layer = Signal$GND PadStack = DR-0102_60",
            )
        ),
        via_lines="\n".join(
            line
            for index in range(landing_count)
            for line in (
                f"ViaPwr{index}::PWR UpperNode = NodeLanding{index} "
                f"LowerNode = NodePwr{index} PadStack = DR-0102_60",
                f"ViaGnd{index}::PWR UpperNode = NodeLanding{index} "
                f"LowerNode = NodeGnd{index} PadStack = DR-0102_60",
            )
        ),
    )
    landings = tuple(
        SpdViaLanding(
            via_id=f"ViaPwr{index}",
            net="PWR",
            endpoint_node_id=f"NodeLanding{index}",
            x_um=float(index),
            y_um=0.0,
            padstack="DR-0102_60",
        )
        for index in range(landing_count)
    )

    result = recover_spd_ground_reachability(
        source,
        landings=landings,
        target_layers_by_net={"PWR": ("Signal$PWR", "Signal$GND")},
        target_node_surface_resolver=lambda _n, layer, _i, x, _y: (
            f"island-{layer.rsplit('$', 1)[-1].casefold()}-{int(x)}"
        ),
        target_surface_island_ids={
            ("PWR", layer): tuple(
                f"island-{layer.rsplit('$', 1)[-1].casefold()}-{index}"
                for index in range(landing_count)
            )
            for layer in ("Signal$PWR", "Signal$GND")
        },
    )

    for index in range(landing_count):
        landing_key = (f"viapwr{index}", f"nodelanding{index}")
        assert result.surface_layers_by_landing[landing_key] == (
            "Signal$GND",
            "Signal$PWR",
        )
        for layer, node_prefix in (
            ("Signal$PWR", "NodePwr"),
            ("Signal$GND", "NodeGnd"),
        ):
            target_key = (*landing_key, layer.casefold())
            contacts = ((f"{node_prefix}{index}", float(index), 0.0),)
            assert result.target_contacts_by_key[target_key] == contacts
            assert result.target_contact_count_by_key[target_key] == 1
            assert result.target_contact_hash_by_key[target_key] == sha256(
                repr(contacts).encode("utf-8")
            ).hexdigest()
    assert result.statistics["component_contact_records_retained"] == (
        landing_count * 2
    )
    assert result.statistics["component_contact_records_filtered"] == 0


def test_landing_layer_index_consumes_single_use_rows_once() -> None:
    rows = (
        ("via-a", "node-a", "signal$pwr"),
        ("via-b", "node-b", "signal$gnd"),
        ("via-a", "node-a", "signal$gnd"),
        ("via-a", "node-a", "signal$pwr"),
    ) + tuple(
        (
            f"via-unrelated-{index}",
            f"node-unrelated-{index}",
            "signal$other",
        )
        for index in range(100)
    )

    class SingleUseRows:
        iteration_count = 0
        row_count = 0

        def __iter__(self):
            self.iteration_count += 1
            if self.iteration_count != 1:
                raise AssertionError("reachable rows were iterated more than once")
            for row in rows:
                self.row_count += 1
                yield row

    reachable_rows = SingleUseRows()
    indexed = spd_io._index_reachable_layers_by_landing(
        reachable_rows,
        {("via-a", "node-a"), ("via-missing", "node-missing")},
    )

    assert indexed == {
        ("via-a", "node-a"): {"signal$gnd", "signal$pwr"},
    }
    assert reachable_rows.iteration_count == 1
    assert reachable_rows.row_count == len(rows)


def test_requested_target_layers_limit_only_landing_request_keys(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines="\n".join(
            f"Node{layer}{suffix}!!1::PWR X = {x}um Y = 0um "
            f"Layer = Signal${layer} PadStack = DR-0102_60"
            for suffix, x in (("A", 0), ("B", 100))
            for layer in ("TOP", "PWR", "GND")
        ),
        via_lines="\n".join(
            f"Via{layer}{suffix}::PWR UpperNode = NodeTOP{suffix} "
            f"LowerNode = Node{layer}{suffix} PadStack = DR-0102_60"
            for suffix in ("A", "B")
            for layer in ("PWR", "GND")
        ),
    )
    landings = tuple(
        SpdViaLanding(
            via_id=f"ViaPwr{suffix}",
            net="PWR",
            endpoint_node_id=f"NodeTop{suffix}",
            x_um=x,
            y_um=0.0,
            padstack="DR-0102_60",
        )
        for suffix, x in (("A", 0.0), ("B", 100.0))
    )
    layers = ("Signal$TOP", "Signal$PWR", "Signal$GND")
    inventory = {
        ("PWR", layer): tuple(
            f"island-{layer.rsplit('$', 1)[-1].casefold()}-{suffix}"
            for suffix in ("a", "b")
        )
        for layer in layers
    }
    kwargs = {
        "terminal_contact_landings": landings,
        "terminal_owned_via_ids": ("ViaPwrA", "ViaPwrB"),
        "padstacks": analysis.padstacks,
        "stackup_layers": analysis.stackup_layers,
        "target_layers_by_net": {"PWR": layers},
        "target_node_surface_resolver": (
            lambda _node, layer, _index, x, _y: (
                f"island-{layer.rsplit('$', 1)[-1].casefold()}-"
                f"{'a' if x < 50.0 else 'b'}"
            )
        ),
        "target_surface_island_ids": inventory,
    }

    legacy = recover_spd_ground_reachability(
        source,
        landings=landings,
        **kwargs,
    )
    exact_map = {
        ("ViaPwrA", "NodeTopA", "PWR"): ("Signal$PWR",),
        ("ViaPwrB", "NodeTopB", "PWR"): ("Signal$GND",),
    }
    exact = recover_spd_ground_reachability(
        source,
        landings=landings,
        requested_target_layers_by_landing=exact_map,
        **kwargs,
    )
    exact_keys = {
        ("viapwra", "nodetopa", "signal$pwr"),
        ("viapwrb", "nodetopb", "signal$gnd"),
    }

    assert legacy.statistics["requested"] == 6
    assert exact.statistics["requested"] == 2
    assert exact.reachable_keys == exact_keys
    assert exact.unreachable_keys == frozenset()
    assert set(exact.target_contacts_by_key) == exact_keys
    assert set(exact.target_contact_count_by_key) == exact_keys
    assert set(exact.target_contact_hash_by_key) == exact_keys
    for key in exact_keys:
        assert exact.target_contacts_by_key[key] == legacy.target_contacts_by_key[key]
        assert (
            exact.target_contact_count_by_key[key]
            == legacy.target_contact_count_by_key[key]
        )
        assert (
            exact.target_contact_hash_by_key[key]
            == legacy.target_contact_hash_by_key[key]
        )
    for attribute in (
        "surface_components",
        "surface_layers_by_landing",
        "surface_islands_by_landing",
        "surface_equivalence_proofs",
        "surface_equivalence_components",
        "landing_surface_contacts",
        "via_island_pair_aggregates",
        "via_island_pair_coverage",
        "finite_via_vertices",
        "finite_via_edges",
        "finite_via_vertex_id_by_landing",
        "finite_via_edge_id_by_landing",
        "finite_via_coverage",
        "finite_via_scenario_isolated_landing_keys",
        "finite_via_scenario_isolation_coverage",
        "finite_via_vertex_id_by_retarget_destination",
        "finite_via_retarget_destination_coverage",
    ):
        assert getattr(exact, attribute) == getattr(legacy, attribute)

    with pytest.raises(ValueError, match="keys must contain"):
        recover_spd_ground_reachability(
            source,
            landings=landings,
            requested_target_layers_by_landing={
                ("ViaPwrA", "NodeTopA"): ("Signal$PWR",),
            },
            **kwargs,
        )
    with pytest.raises(ValueError, match="coverage must exactly match"):
        recover_spd_ground_reachability(
            source,
            landings=landings,
            requested_target_layers_by_landing={
                ("ViaPwrA", "NodeTopA", "PWR"): ("Signal$PWR",),
            },
            **kwargs,
        )
    with pytest.raises(ValueError, match="outside target_layers_by_net"):
        recover_spd_ground_reachability(
            source,
            landings=landings,
            requested_target_layers_by_landing={
                ("ViaPwrA", "NodeTopA", "PWR"): ("Signal$NOPE",),
                ("ViaPwrB", "NodeTopB", "PWR"): ("Signal$GND",),
            },
            **kwargs,
        )

    casefold_union = recover_spd_ground_reachability(
        source,
        landings=(landings[0], landings[0], landings[1]),
        requested_target_layers_by_landing={
            ("ViaPwrA", "NodeTopA", "PWR"): ("Signal$PWR",),
            ("VIAPWRA", "NODETOPA", "pwr"): ("SIGNAL$GND",),
            ("ViaPwrB", "NodeTopB", "PWR"): (),
        },
        **kwargs,
    )
    assert casefold_union.statistics["requested"] == 2
    assert casefold_union.reachable_keys == {
        ("viapwra", "nodetopa", "signal$pwr"),
        ("viapwra", "nodetopa", "signal$gnd"),
    }


def test_terminal_contact_does_not_invent_mismatched_via_endpoint(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeViaTop!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeViaPwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodeNotIncident!!1::PWR X = 20um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60"
        ),
        via_lines=(
            "ViaTerminal::PWR UpperNode = NodeViaTop "
            "LowerNode = NodeViaPwr PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaTerminal",
        net="PWR",
        endpoint_node_id="NodeNotIncident",
        x_um=20.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(),
        terminal_contact_landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$PWR",)},
        target_node_surface_resolver=lambda *_args: "island-pwr",
        target_surface_island_ids={
            ("PWR", "Signal$PWR"): ("island-pwr",),
        },
    )

    contact = result.landing_surface_contacts[0]
    assert contact.internal_endpoint_node_id is None
    assert dict(contact.contact_island_ids_by_layer) == {}
    assert contact.physical_model_issues == (
        "internal_via_endpoint_missing_or_ambiguous",
    )


def test_component_contact_reduction_keeps_only_requested_root_hash(
    tmp_path: Path,
) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeRequestedTop!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeRequestedA!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodeRequestedB!!1::PWR X = 10um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodeIrrelevantA!!1::PWR X = 100um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60\n"
            "NodeIrrelevantB!!1::PWR X = 110um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        trace_lines=(
            "TraceRequested::PWR StartingNode = NodeRequestedA::PWR "
            "EndingNode = NodeRequestedB::PWR Width = 0.10mm\n"
            "TraceIrrelevant::PWR StartingNode = NodeIrrelevantA::PWR "
            "EndingNode = NodeIrrelevantB::PWR Width = 0.10mm"
        ),
        via_lines=(
            "ViaRequested::PWR UpperNode = NodeRequestedTop "
            "LowerNode = NodeRequestedA PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaRequested",
        net="PWR",
        endpoint_node_id="NodeRequestedTop",
        x_um=0.0,
        y_um=0.0,
        padstack="DR-0102_60",
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        target_layers_by_net={"PWR": ("Signal$PWR",)},
        same_layer_artwork_layers_by_net={"PWR": ("Signal$PWR",)},
        same_layer_artwork_component=lambda _n, _l, x, _y: (
            "requested" if x < 50.0 else "irrelevant"
        ),
        target_node_surface_resolver=lambda *_args: "island-pwr",
        target_surface_island_ids={
            ("PWR", "Signal$PWR"): ("island-pwr",),
        },
    )

    key = ("viarequested", "noderequestedtop", "signal$pwr")
    contacts = (
        ("NodeRequestedA", 0.0, 0.0),
        ("NodeRequestedB", 10.0, 0.0),
    )
    assert result.target_contacts_by_key[key] == (contacts[0],)
    assert result.target_contact_count_by_key[key] == 2
    assert result.target_contact_hash_by_key[key] == sha256(
        repr(contacts).encode("utf-8")
    ).hexdigest()
    assert result.statistics["component_contact_records_retained"] == 2
    assert result.statistics["component_contact_records_filtered"] == 2


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


def test_terminal_via_allows_device_and_decap_on_opposite_endpoints(
    tmp_path: Path,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeOwnerTop!!1::PWR X = 0um Y = 0um Layer = Signal$TOP "
            "PadStack = DR-0102_60\n"
            "NodeOwnerPwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR "
            "PadStack = DR-0102_60"
        ),
        via_lines=(
            "ViaOwner::PWR UpperNode = NodeOwnerTop "
            "LowerNode = NodeOwnerPwr PadStack = DR-0102_60"
        ),
    )
    device = SimpleNamespace(
        via_id="ViaOwner",
        net="PWR",
        endpoint_node_id="NodeOwnerTop",
        pin_id="SITE0:1",
    )
    decap = SimpleNamespace(
        via_id="ViaOwner",
        net="PWR",
        endpoint_node_id="NodeOwnerPwr",
    )

    result = recover_spd_ground_reachability(
        source,
        landings=(),
        terminal_contact_landings=(device, decap),
        terminal_owned_via_ids=("ViaOwner",),
        padstacks=analysis.padstacks,
        stackup_layers=analysis.stackup_layers,
        target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
        target_node_surface_resolver=(
            lambda _net, layer, _node, _x, _y: {
                "signal$top": "island-top",
                "signal$pwr": "island-pwr",
            }.get(layer.casefold())
        ),
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("island-top",),
            ("PWR", "Signal$PWR"): ("island-pwr",),
        },
    )

    contacts = {
        item.endpoint_node_id.casefold(): item
        for item in result.landing_surface_contacts
    }
    assert contacts["nodeownertop"].terminal_owner_kind == "device"
    assert contacts["nodeownerpwr"].terminal_owner_kind == "decap"
    terminal_ids_by_node = {
        item.representative_node_id.casefold(): set(item.terminal_ids)
        for item in result.finite_via_vertices
    }
    assert terminal_ids_by_node["nodeownertop"] == {"SITE0:1"}
    assert terminal_ids_by_node["nodeownerpwr"] == {
        "decap-via:viaowner:nodeownerpwr"
    }


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


def test_terminal_owned_one_sided_via_is_unpaired_but_physically_complete(
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
        via_lines=(
            "ViaOneSided::PWR UpperNode = NodeExternal "
            "LowerNode = NodeInternal PadStack = DR-0102_60"
        ),
    )
    landing = SpdViaLanding(
        via_id="ViaOneSided", net="PWR", endpoint_node_id="NodeExternal",
        x_um=0.0, y_um=0.0, padstack="DR-0102_60",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        terminal_contact_landings=(landing,),
        terminal_owned_via_ids=("ViaOneSided",),
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
    coverage = result.via_island_pair_coverage
    assert coverage is not None
    assert coverage.paired_via_count == 0
    assert coverage.terminal_owned_unpaired_count == 1
    assert coverage.unsupported_missing_endpoint_count == 0
    assert coverage.outside_retained_interface_scope_count == 0
    contact = result.landing_surface_contacts[0]
    assert contact.terminal_owner_kind == "decap"
    assert contact.external_endpoint_layer == "Signal$TOP"
    assert contact.physical_model_status == "complete"
    assert contact.segments


def test_terminal_contact_rejects_duplicate_physical_via_owner(tmp_path: Path) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeTop!!1::PWR X = 0um Y = 0um Layer = Signal$TOP PadStack = DUT\n"
            "NodePwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR PadStack = DUT"
        ),
        via_lines=(
            "Via1::PWR UpperNode = NodeTop LowerNode = NodePwr PadStack = DUT"
        ),
    )
    first = SimpleNamespace(via_id="Via1", net="PWR", endpoint_node_id="NodeTop")
    second = SimpleNamespace(via_id="Via1", net="PWR", endpoint_node_id="NodePwr")
    with pytest.raises(ValueError, match="one physical terminal Via ID"):
        recover_spd_ground_reachability(
            source,
            landings=(),
            terminal_contact_landings=(first, second),
            terminal_owned_via_ids=("Via1",),
            target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
        )


def test_finite_via_quotient_emits_owner_complete_graph(tmp_path: Path) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeTop!!1::PWR X = 0um Y = 0um Layer = Signal$TOP PadStack = DR-0102_60\n"
            "NodePwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR PadStack = DR-0102_60\n"
            "NodeGnd!!1::PWR X = 0um Y = 0um Layer = Signal$GND PadStack = DR-0102_60"
        ),
        via_lines=(
            "ViaTop::PWR UpperNode = NodeTop LowerNode = NodePwr PadStack = DR-0102_60\n"
            "ViaBottom::PWR UpperNode = NodePwr LowerNode = NodeGnd PadStack = DR-0102_60"
        ),
    )
    landing = SimpleNamespace(via_id="ViaTop", net="PWR", endpoint_node_id="NodeTop")
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        terminal_contact_landings=(landing,),
        terminal_owned_via_ids=(),
        padstacks=analysis.padstacks,
        stackup_layers=analysis.stackup_layers,
        target_layers_by_net={"PWR": ("Signal$GND",)},
    )
    assert result.finite_via_coverage is not None
    assert result.finite_via_coverage.modeled_global_via_count == 2
    assert len(result.finite_via_vertices) >= 2
    assert len(result.finite_via_edges) >= 1


def test_finite_via_quotient_contracts_bridge_not_cycle(tmp_path: Path) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeQA!!1::PWR X = 10um Y = 0um Layer = Signal$TOP PadStack = DR-0102_60\n"
            "NodeQX!!1::PWR X = 20um Y = 0um Layer = Signal$PWR PadStack = DR-0102_60\n"
            "NodeQB!!1::PWR X = 30um Y = 0um Layer = Signal$TOP PadStack = DR-0102_60\n"
            "NodeQC!!1::PWR X = 40um Y = 0um Layer = Signal$GND PadStack = DR-0102_60\n"
            "NodeQD!!1::PWR X = 30um Y = 10um Layer = Signal$PWR PadStack = DR-0102_60\n"
            "NodeQE!!1::PWR X = 30um Y = 20um Layer = Signal$GND PadStack = DR-0102_60"
        ),
        via_lines=(
            "ViaQAX::PWR UpperNode = NodeQA LowerNode = NodeQX PadStack = DR-0102_60\n"
            "ViaQXB::PWR UpperNode = NodeQX LowerNode = NodeQB PadStack = DR-0102_60\n"
            "ViaQBC::PWR UpperNode = NodeQB LowerNode = NodeQC PadStack = DR-0102_60\n"
            "ViaQBD::PWR UpperNode = NodeQB LowerNode = NodeQD PadStack = DR-0102_60\n"
            "ViaQDE::PWR UpperNode = NodeQD LowerNode = NodeQE PadStack = DR-0102_60\n"
            "ViaQEB::PWR UpperNode = NodeQE LowerNode = NodeQB PadStack = DR-0102_60"
        ),
    )
    landing = SimpleNamespace(via_id="ViaQAX", net="PWR", endpoint_node_id="NodeQA")
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        terminal_contact_landings=(landing,),
        terminal_owned_via_ids=(),
        padstacks=analysis.padstacks,
        stackup_layers=analysis.stackup_layers,
        target_layers_by_net={"PWR": ("Signal$GND",)},
    )
    assert result.finite_via_coverage is not None
    assert len(result.finite_via_vertices) == 5
    assert len(result.finite_via_edges) == 5
    contracted = [item for item in result.finite_via_edges if item.mode == "contracted_series"]
    assert len(contracted) == 1
    assert contracted[0].raw_via_count == 2


def test_finite_via_quotient_middle_first_chain_is_order_independent(tmp_path: Path) -> None:
    nodes = (
        "NodeA!!1::PWR X = 10um Y = 0um Layer = Signal$TOP PadStack = DR-0102_60\n"
        "NodeX!!1::PWR X = 20um Y = 0um Layer = Signal$PWR PadStack = DR-0102_60\n"
        "NodeY!!1::PWR X = 30um Y = 0um Layer = Signal$GND PadStack = DR-0102_60\n"
        "NodeB!!1::PWR X = 40um Y = 0um Layer = Signal$TOP PadStack = DR-0102_60"
    )
    normal = (
        "ViaAX::PWR UpperNode = NodeA LowerNode = NodeX PadStack = DR-0102_60\n"
        "ViaXY::PWR UpperNode = NodeX LowerNode = NodeY PadStack = DR-0102_60\n"
        "ViaYB::PWR UpperNode = NodeY LowerNode = NodeB PadStack = DR-0102_60"
    )
    middle_first = (
        "ViaXY::PWR UpperNode = NodeX LowerNode = NodeY PadStack = DR-0102_60\n"
        "ViaAX::PWR UpperNode = NodeA LowerNode = NodeX PadStack = DR-0102_60\n"
        "ViaYB::PWR UpperNode = NodeY LowerNode = NodeB PadStack = DR-0102_60"
    )

    def recover(root: Path, vias: str):
        root.mkdir(parents=True, exist_ok=True)
        source, analysis = _recoverable_via_source(root, node_lines=nodes, via_lines=vias)
        landing = SimpleNamespace(via_id="ViaAX", net="PWR", endpoint_node_id="NodeA")
        return recover_spd_ground_reachability(
            source,
            landings=(landing,),
            terminal_contact_landings=(landing,),
            terminal_owned_via_ids=(),
            padstacks=analysis.padstacks,
            stackup_layers=analysis.stackup_layers,
            target_layers_by_net={"PWR": ("Signal$GND",)},
        )

    first = recover(tmp_path / "normal", normal)
    second = recover(tmp_path / "middle", middle_first)
    first_edges = {(item.mode, item.raw_via_count, item.per_path_via_count) for item in first.finite_via_edges}
    second_edges = {(item.mode, item.raw_via_count, item.per_path_via_count) for item in second.finite_via_edges}
    assert first_edges == second_edges == {("contracted_series", 3, 3)}


def test_recover_keeps_same_node_ids_separate_by_net(tmp_path: Path) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeA!!1::PWR1 X = 0um Y = 0um Layer = Signal$TOP PadStack = DR-0102_60\n"
            "NodeB!!1::PWR1 X = 0um Y = 0um Layer = Signal$PWR PadStack = DR-0102_60\n"
            "NodeA!!1::PWR2 X = 0um Y = 0um Layer = Signal$GND PadStack = DR-0102_60\n"
            "NodeB!!1::PWR2 X = 0um Y = 0um Layer = Signal$TOP PadStack = DR-0102_60"
        ),
        via_lines=(
            "Via1::PWR1 UpperNode = NodeA LowerNode = NodeB PadStack = DR-0102_60\n"
            "Via2::PWR2 UpperNode = NodeA LowerNode = NodeB PadStack = DR-0102_60"
        ),
    )
    first = SimpleNamespace(via_id="Via1", net="PWR1", endpoint_node_id="NodeA")
    second = SimpleNamespace(via_id="Via2", net="PWR2", endpoint_node_id="NodeA")
    result = recover_spd_ground_reachability(
        source,
        landings=(first, second),
        terminal_contact_landings=(first, second),
        terminal_owned_via_ids=(),
        padstacks=analysis.padstacks,
        stackup_layers=analysis.stackup_layers,
        target_layers_by_net={
            "PWR1": ("Signal$TOP", "Signal$PWR"),
            "PWR2": ("Signal$GND", "Signal$TOP"),
        },
        target_node_surface_resolver=lambda net, layer, _node, _x, _y: (
            ("island-gnd" if net == "PWR1" else "island-gnd-2")
            if layer == "Signal$GND"
            else (
                ("island-pwr" if net == "PWR1" else "island-pwr-2")
                if layer == "Signal$PWR"
                else ("island-top" if net == "PWR1" else "island-top-2")
            )
        ),
        target_surface_island_ids={
            ("PWR1", "Signal$TOP"): ("island-top",),
            ("PWR1", "Signal$PWR"): ("island-pwr",),
            ("PWR2", "Signal$GND"): ("island-gnd-2",),
            ("PWR2", "Signal$TOP"): ("island-top-2",),
        },
    )
    assert {item.net for item in result.via_island_pair_aggregates} == {"PWR1", "PWR2"}
    assert {
        (item.net, item.representative_node_id): item.layer
        for item in result.finite_via_vertices
    } == {
        ("PWR1", "nodea"): "Signal$TOP",
        ("PWR1", "nodeb"): "Signal$PWR",
        ("PWR2", "nodea"): "Signal$GND",
        ("PWR2", "nodeb"): "Signal$TOP",
    }
    assert {
        item.net: tuple(
            (term.start_layer, term.end_layer) for term in item.series_terms
        )
        for item in result.finite_via_edges
    } == {
        "pwr1": (("Signal$TOP", "Signal$PWR"),),
        "pwr2": (("Signal$GND", "Signal$TOP"),),
    }
    assert result.statistics["via_source_record_replay_passes"] == 1


def test_finite_via_scenario_isolation_and_retarget_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeIsoA!!1::PWR X = 10um Y = 0um Layer = Signal$TOP PadStack = DR-0102_60\n"
            "NodeIsoX!!1::PWR X = 10um Y = 10um Layer = Signal$PWR PadStack = DR-0102_60\n"
            "NodeIsoD!!1::PWR X = 10um Y = 20um Layer = Signal$GND PadStack = DR-0102_60"
        ),
        via_lines=(
            "ViaIso::PWR UpperNode = NodeIsoA LowerNode = NodeIsoX PadStack = DR-0102_60\n"
            "ViaDestination::PWR UpperNode = NodeIsoX LowerNode = NodeIsoD PadStack = DR-0102_60"
        ),
    )
    landing = SimpleNamespace(via_id="ViaIso", net="PWR", endpoint_node_id="NodeIsoA")

    class SingleUseRetargetRequests:
        iteration_count = 0

        def __iter__(self):
            self.iteration_count += 1
            if self.iteration_count != 1:
                raise AssertionError("retarget requests were iterated more than once")
            yield "PWR", "Signal$GND", "NodeIsoD"

    retarget_requests = SingleUseRetargetRequests()
    needed_key_sets: list[set[tuple[str, str]]] = []
    real_index = spd_io._index_reachable_layers_by_landing

    class ReleaseCheckingIndex(dict):
        def get(self, key, default=None):
            assert not needed_key_sets[0]
            return super().get(key, default)

    def track_needed_keys(reachable_rows, needed_keys):
        needed_key_sets.append(needed_keys)
        return ReleaseCheckingIndex(real_index(reachable_rows, needed_keys))

    monkeypatch.setattr(
        spd_io, "_index_reachable_layers_by_landing", track_needed_keys
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(landing,),
        terminal_contact_landings=(landing,),
        scenario_isolated_terminal_landings=(landing,),
        retarget_destination_requests=retarget_requests,
        terminal_owned_via_ids=("ViaIso",),
        padstacks=analysis.padstacks,
        stackup_layers=analysis.stackup_layers,
        target_layers_by_net={"PWR": ("Signal$TOP", "Signal$GND")},
        target_node_surface_resolver=lambda _n, layer, node_id, _x, _y: (
            "island-top" if layer == "Signal$TOP" and node_id == "NodeIsoA" else
            "island-gnd" if layer == "Signal$GND" and node_id == "NodeIsoD" else None
        ),
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("island-top",),
            ("PWR", "Signal$GND"): ("island-gnd",),
        },
    )
    assert result.finite_via_scenario_isolation_coverage is not None
    assert retarget_requests.iteration_count == 1
    assert needed_key_sets == [set()]
    assert result.finite_via_scenario_isolated_landing_keys == {("viaiso", "nodeisoa")}
    assert result.finite_via_retarget_destination_coverage is not None
    assert result.finite_via_retarget_destination_coverage.resolved_destination_count == 1


def test_surface_batch_none_is_authoritative_and_skips_scalar(tmp_path: Path) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines="NodeSurface!!1::DGND X = 0um Y = 0um Layer = Signal$TOP PadStack = DUT",
        via_lines="",
    )
    scalar_calls = 0
    def scalar(*_args):
        nonlocal scalar_calls
        scalar_calls += 1
        raise AssertionError("scalar surface resolver must not run after batch None")
    result = recover_spd_ground_reachability(
        source,
        landings=(),
        target_layers_by_net={"DGND": ("Signal$TOP",)},
        same_layer_artwork_layers_by_net={"DGND": ("Signal$TOP",)},
        same_layer_artwork_components_batch=lambda _n, _l, points: (0,) * len(points),
        target_node_surface_resolver=scalar,
        target_node_surface_resolver_batch=lambda _n, _l, _ids, _points: None,
        target_surface_island_ids={("DGND", "Signal$TOP"): ("island",)},
    )
    assert scalar_calls == 0
    assert result.surface_equivalence_proofs


def test_surface_components_are_global_without_landings(tmp_path: Path) -> None:
    source, _analysis = _recoverable_via_source(
        tmp_path,
        node_lines=(
            "NodeTop!!1::PWR X = 0um Y = 0um Layer = Signal$TOP PadStack = DUT\n"
            "NodePwr!!1::PWR X = 0um Y = 0um Layer = Signal$PWR PadStack = DUT"
        ),
        via_lines="ViaSurface::PWR UpperNode = NodeTop LowerNode = NodePwr PadStack = DUT",
    )
    result = recover_spd_ground_reachability(
        source,
        landings=(),
        target_layers_by_net={"PWR": ("Signal$TOP", "Signal$PWR")},
        target_node_surface_resolver=lambda _n, layer, _i, _x, _y: (
            "top" if layer == "Signal$TOP" else "pwr"
        ),
        target_surface_island_ids={
            ("PWR", "Signal$TOP"): ("top",),
            ("PWR", "Signal$PWR"): ("pwr",),
        },
    )
    assert any(item.layers == ("Signal$PWR", "Signal$TOP") for item in result.surface_components)


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
