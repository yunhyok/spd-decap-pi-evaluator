from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from hashlib import sha256
import math
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from spd_decap_pi import raw_spatial_contact_compiler as compiler
from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.io.spd import SpdSourceInfo, _length_um
from spd_decap_pi.compiled_topology_asset import (
    COMPILED_TOPOLOGY_ASSET_METADATA_KEY,
    build_compiled_topology_asset,
)
from spd_decap_pi.raw_spatial_contact_asset import load_raw_spatial_contact_asset
from spd_decap_pi.surface_certificate_asset import (
    SURFACE_CERTIFICATE_METADATA_KEY,
    canonical_surface_certificate_sha256,
)

from test_surface_certificate_asset import _full_v4_roundtrip_fixture


def _raw_source(newline: bytes = b"\n") -> bytes:
    lines = (
        b".Shape L1PkgShape",
        b"Polygon::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm",
        b".EndShape",
        b".Shape L2PkgShape",
        b"Polygon::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm",
        b".EndShape",
        b"* Layer description lines",
        b"L1 Thickness = 0.035mm Material = Copper",
        b"L2 Thickness = 0.035mm Material = Copper",
        b"* Node description lines",
        b"Node1::VDD X = 0mm Y = 0mm Layer = L1",
        b"Node2 X = 1mm Y = 0mm Layer = L1",
        b"Node3 X = 0mm Y = 0mm Layer = L2",
        b"* Trace description lines",
        b"Trace1::VDD StartingNode = Node1::VDD EndingNode = Node2 Width = 0.1mm",
        b"Trace2::VDD StartingNode = Node2 EndingNode = Node1",
        b"* Via description lines",
        b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1 AbsoluteRotation = 180",
        b"* PadStack collection description lines",
        b".PadStackDef PS1 0.05mm Material = Copper",
        b".PadDef L1",
        b"Regular Circle 0.1mm",
        b".EndPadDef",
        b".PadDef L2",
        b"Regular Box 0.2mm 0.3mm",
        b".EndPadDef",
        b".EndPadStackDef",
        b"* Material description lines",
    )
    return newline.join(lines) + newline


@dataclass(slots=True)
class _Context:
    path: Path
    source: bytes
    analysis: object
    project: object
    attachments: dict[str, bytes]
    compiled: dict[str, str]


@dataclass(slots=True)
class _RealCompiledContext:
    path: Path
    source: bytes
    analysis: object
    project: object
    attachments: dict[str, bytes]
    compiled: dict[str, object]


def _real_envelope_raw_source() -> bytes:
    return b"\n".join(
        (
            b".Shape PWRPkgShape",
            b"Polygon::VDD+ 0mm 0mm 0.5mm 0mm 0.5mm 1mm 0mm 1mm Sub-element",
            b".EndShape",
            b".Shape GNDPkgShape",
            b"Polygon::DGND+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm Sub-element",
            b".EndShape",
            b"* Layer description lines",
            b"PWR Thickness = 0.035mm Material = Copper",
            b"GND Thickness = 0.035mm Material = Copper",
            b"* Node description lines",
            b"NodeP::VDD X = 0.2mm Y = 0.5mm Layer = PWR",
            b"NodeG::DGND X = 0.25mm Y = 0.5mm Layer = GND",
            b"* Trace description lines",
            b"* Via description lines",
            b"* PadStack collection description lines",
            b"* Material description lines",
            b"",
        )
    )


def _real_compiled_context(tmp_path: Path) -> _RealCompiledContext:
    raw = _real_envelope_raw_source()
    path = tmp_path / "real-envelope-board.spd"
    path.write_bytes(raw)
    stat = path.stat()
    source_sha = sha256(raw).hexdigest()

    scenario, geometry_attachments = _full_v4_roundtrip_fixture()
    project = scenario.base_project
    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    records: list[dict[str, object]] = []
    analysis_geometries: list[SimpleNamespace] = []
    for original in spd_import["plane_geometries"]:
        record = dict(original)
        asset_name = str(record["asset"])
        asset_sha = str(record["asset_sha256"])
        payload = core_services._decode_spd_geometry_asset(
            asset_sha, geometry_attachments[asset_name]
        )
        bounds = core_services._spd_geometry_bounds(payload)
        assert bounds is not None
        record["bbox_um"] = list(bounds)
        records.append(record)
        analysis_geometries.append(SimpleNamespace(**dict(payload)))

    certificate = deepcopy(spd_import[SURFACE_CERTIFICATE_METADATA_KEY])
    certificate["source_sha256"] = source_sha
    certificate["geometry_assets"] = [dict(record) for record in records]
    certificate.pop("evidence_sha256", None)
    certificate["evidence_sha256"] = canonical_surface_certificate_sha256(
        certificate
    )
    terminal_certificate = deepcopy(
        spd_import["layerwise_device_terminal_via_certificate"]
    )
    terminal_certificate["source_sha256"] = source_sha
    terminal_certificate.pop("evidence_sha256", None)
    terminal_certificate["evidence_sha256"] = (
        canonical_surface_certificate_sha256(terminal_certificate)
    )
    spd_import.update(
        {
            "source_sha256": source_sha,
            "plane_geometries": records,
            SURFACE_CERTIFICATE_METADATA_KEY: certificate,
            "layerwise_device_terminal_via_certificate": terminal_certificate,
        }
    )
    metadata["spd_import"] = spd_import
    inline_project = project.model_copy(update={"metadata": metadata})

    surface_stub, compiled_manifest, compiled_attachment = (
        build_compiled_topology_asset(inline_project, certificate)
    )
    stored_metadata = dict(inline_project.metadata)
    stored_spd_import = dict(stored_metadata["spd_import"])
    stored_spd_import[SURFACE_CERTIFICATE_METADATA_KEY] = surface_stub
    stored_spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY] = compiled_manifest
    stored_metadata["spd_import"] = stored_spd_import
    stored_project = inline_project.model_copy(update={"metadata": stored_metadata})

    attachments = {
        **geometry_attachments,
        compiled_attachment[0]: compiled_attachment[1],
    }
    analysis = SimpleNamespace(
        source=SpdSourceInfo(
            path=path,
            name=path.name,
            size_bytes=len(raw),
            mtime_ns=stat.st_mtime_ns,
            sha256=source_sha,
            title="real compiled-only envelope fixture",
        ),
        stackup_layers=(
            SimpleNamespace(name="PWR", is_conductor=True),
            SimpleNamespace(name="GND", is_conductor=True),
        ),
        padstacks=(),
        plane_geometries=tuple(analysis_geometries),
    )
    return _RealCompiledContext(
        path,
        raw,
        analysis,
        stored_project,
        attachments,
        compiled_manifest,
    )


def _context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: bytes | None = None,
) -> _Context:
    raw = source if source is not None else _raw_source()
    path = tmp_path / "board.spd"
    path.write_bytes(raw)
    stat = path.stat()
    source_sha = sha256(raw).hexdigest()
    square = ((0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0))
    l2_pad_shape = (
        SimpleNamespace(
            layer="L2",
            kind="UNSUPPORTED",
            width_um=None,
            height_um=None,
            reason="padstack 'PS1' uses unsupported or malformed Polygon geometry",
        )
        if b"Regular Polygon" in raw
        else SimpleNamespace(
            layer="L2",
            kind="RECTANGLE",
            width_um=200.0,
            height_um=300.0,
        )
    )
    analysis = SimpleNamespace(
        source=SpdSourceInfo(
            path=path,
            name=path.name,
            size_bytes=len(raw),
            mtime_ns=stat.st_mtime_ns,
            sha256=source_sha,
            title="synthetic",
        ),
        stackup_layers=(
            SimpleNamespace(name="L1", is_conductor=True),
            SimpleNamespace(name="L2", is_conductor=True),
        ),
        padstacks=(
            SimpleNamespace(
                name="PS1",
                drill_diameter_um=100.0,
                pad_width_um=200.0,
                pad_height_um=(
                    200.0 if b"Regular Polygon" in raw else 300.0
                ),
                layers=("L1", "L2"),
                material="Copper",
                pad_shapes=(
                    SimpleNamespace(
                        layer="L1",
                        kind="CIRCLE",
                        width_um=200.0,
                        height_um=200.0,
                    ),
                    l2_pad_shape,
                ),
            ),
        ),
        plane_geometries=(
            SimpleNamespace(
                net="VDD",
                layer="L1",
                positive_polygons_um=(square,),
                negative_polygons_um=(),
                positive_circles_um=(),
                negative_circles_um=(),
                primitive_order=(("positive_polygon", 0),),
                positive_subelement_count=0,
                negative_subelement_count=0,
                polygon_trace_count=0,
                box_count=0,
            ),
            SimpleNamespace(
                net="VDD",
                layer="L2",
                positive_polygons_um=(square,),
                negative_polygons_um=(),
                positive_circles_um=(),
                negative_circles_um=(),
                primitive_order=(("positive_polygon", 0),),
                positive_subelement_count=0,
                negative_subelement_count=0,
                polygon_trace_count=0,
                box_count=0,
            ),
        ),
    )
    def artwork(layer: str, net: str = "VDD") -> bytes:
        return core_services._compress_spd_geometry_payload(
            layer=layer,
            net=net,
            positive_polygons=(square,),
            negative_polygons=(),
            positive_circles=(),
            negative_circles=(),
            primitive_order=(("positive_polygon", 0),),
            positive_subelement_count=0,
            negative_subelement_count=0,
            polygon_trace_count=0,
            box_count=0,
        )[0]

    artwork_l1 = artwork("L1")
    artwork_l2 = artwork("L2")
    attachments = {
        "geometry/l1.spdgeom.zlib": artwork_l1,
        "geometry/l2.spdgeom.zlib": artwork_l2,
    }
    plane_geometries = [
        {
            "layer": "L1",
            "net": "VDD",
            "asset": "geometry/l1.spdgeom.zlib",
            "asset_sha256": sha256(artwork_l1).hexdigest(),
            "bbox_um": [0.0, 1000.0, 0.0, 1000.0],
            "island_ids": ["island:l1"],
        },
        {
            "layer": "L2",
            "net": "VDD",
            "asset": "geometry/l2.spdgeom.zlib",
            "asset_sha256": sha256(artwork_l2).hexdigest(),
            "bbox_um": [0.0, 1000.0, 0.0, 1000.0],
            "island_ids": ["island:l2"],
        },
    ]
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "source_sha256": source_sha,
                "plane_geometries": plane_geometries,
            }
        }
    )
    compiled = {
        "source_sha256": source_sha,
        "project_binding_sha256": "b" * 64,
        "certificate_evidence_sha256": "c" * 64,
        "topology_identity_sha256": "d" * 64,
    }
    monkeypatch.setattr(
        compiler,
        "validate_project_topology_storage_envelope",
        lambda _project, _attachments: ({"storage_schema": "synthetic"}, compiled),
    )
    return _Context(path, raw, analysis, project, attachments, compiled)


def _compile(context: _Context, **kwargs: object):
    return compiler.compile_raw_spatial_contact_asset(
        context.path,
        analysis=context.analysis,
        project=context.project,
        attachments=context.attachments,
        **kwargs,
    )


def _open(manifest: dict[str, object], attachment: tuple[str, bytes]):
    return load_raw_spatial_contact_asset(
        manifest,
        {attachment[0]: attachment[1]},
        expected_source_sha256=str(manifest["source_sha256"]),
        expected_project_binding_sha256=str(manifest["project_binding_sha256"]),
        expected_certificate_evidence_sha256=str(
            manifest["certificate_evidence_sha256"]
        ),
        expected_compiled_topology_identity_sha256=str(
            manifest["compiled_topology_identity_sha256"]
        ),
        expected_geometry_identity_sha256=str(manifest["geometry_identity_sha256"]),
    )


def _replace_l1_geometry(
    context: _Context,
    *,
    positive_polygons: tuple[object, ...] = (),
    negative_polygons: tuple[object, ...] = (),
    positive_circles: tuple[object, ...] = (),
    negative_circles: tuple[object, ...] = (),
    primitive_order: tuple[object, ...],
    box_count: int = 0,
) -> None:
    geometry = SimpleNamespace(
        net="VDD",
        layer="L1",
        positive_polygons_um=positive_polygons,
        negative_polygons_um=negative_polygons,
        positive_circles_um=positive_circles,
        negative_circles_um=negative_circles,
        primitive_order=primitive_order,
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=box_count,
    )
    context.analysis.plane_geometries = (
        geometry,
        context.analysis.plane_geometries[1],
    )
    artwork = core_services._compress_spd_geometry_payload(
        layer="L1",
        net="VDD",
        positive_polygons=positive_polygons,
        negative_polygons=negative_polygons,
        positive_circles=positive_circles,
        negative_circles=negative_circles,
        primitive_order=primitive_order,
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=box_count,
    )[0]
    context.attachments["geometry/l1.spdgeom.zlib"] = artwork
    record = context.project.metadata["spd_import"]["plane_geometries"][0]
    record["asset_sha256"] = sha256(artwork).hexdigest()
    bounds = core_services._spd_geometry_bounds(
        {
            "positive_polygons_um": positive_polygons,
            "negative_polygons_um": negative_polygons,
            "positive_circles_um": positive_circles,
            "negative_circles_um": negative_circles,
        }
    )
    assert bounds is not None
    record["bbox_um"] = list(bounds)


def test_real_compiled_only_envelope_compiles_and_loads_raw_spatial_asset(
    tmp_path: Path,
) -> None:
    context = _real_compiled_context(tmp_path)
    manifest, attachment = compiler.compile_raw_spatial_contact_asset(
        context.path,
        analysis=context.analysis,
        project=context.project,
        attachments=context.attachments,
        batch_rows=1,
    )

    assert context.path.read_bytes() == context.source
    assert manifest["source_sha256"] == sha256(context.source).hexdigest()
    assert manifest["project_binding_sha256"] == context.compiled[
        "project_binding_sha256"
    ]
    assert manifest["certificate_evidence_sha256"] == context.compiled[
        "certificate_evidence_sha256"
    ]
    assert manifest["compiled_topology_identity_sha256"] == context.compiled[
        "topology_identity_sha256"
    ]
    assert manifest["counts"] == {
        "source_coverage": 1,
        "section_coverage": 3,
        "layers": 2,
        "padstacks": 0,
        "pad_shapes": 0,
        "surfaces": 2,
        "nodes": 2,
        "traces": 0,
        "vias": 0,
    }
    assert str(manifest["asset_name"]).startswith(
        "spatial/raw-spatial-contact-v2-"
    )

    with load_raw_spatial_contact_asset(
        manifest,
        {attachment[0]: attachment[1]},
        expected_source_sha256=str(context.compiled["source_sha256"]),
        expected_project_binding_sha256=str(
            context.compiled["project_binding_sha256"]
        ),
        expected_certificate_evidence_sha256=str(
            context.compiled["certificate_evidence_sha256"]
        ),
        expected_compiled_topology_identity_sha256=str(
            context.compiled["topology_identity_sha256"]
        ),
        expected_geometry_identity_sha256=str(manifest["geometry_identity_sha256"]),
    ) as loaded:
        pwr = loaded.get_node("VDD", "NodeP")
        ground = loaded.get_node("DGND", "NodeG")
        assert pwr is not None and (
            pwr.layer_id,
            pwr.x_pm,
            pwr.y_pm,
        ) == ("PWR", 200_000_000, 500_000_000)
        assert ground is not None and (
            ground.layer_id,
            ground.x_pm,
            ground.y_pm,
        ) == ("GND", 250_000_000, 500_000_000)
        surfaces = {
            (row.net_name, row.layer_id): (
                row.min_x_pm,
                row.min_y_pm,
                row.max_x_pm,
                row.max_y_pm,
            )
            for row in loaded.iter_surfaces()
        }
        assert surfaces == {
            ("VDD", "PWR"): (0, 0, 500_000_000, 1_000_000_000),
            ("DGND", "GND"): (0, 0, 1_000_000_000, 1_000_000_000),
        }


@pytest.mark.parametrize(
    ("mutation", "nested_code"),
    (
        ("missing", "COMPILED_TOPOLOGY_ASSET_MISSING"),
        ("tampered", "COMPILED_TOPOLOGY_COMPRESSED_INTEGRITY_FAILED"),
        ("case_collision", "COMPILED_TOPOLOGY_ASSET_MISSING"),
    ),
)
def test_real_compiled_only_envelope_rejects_invalid_compiled_attachment(
    tmp_path: Path,
    mutation: str,
    nested_code: str,
) -> None:
    context = _real_compiled_context(tmp_path)
    asset_name = str(context.compiled["asset_name"])
    attachments = dict(context.attachments)
    if mutation == "missing":
        attachments.pop(asset_name)
    elif mutation == "tampered":
        content = attachments[asset_name]
        attachments[asset_name] = content[:-1] + bytes((content[-1] ^ 1,))
    else:
        attachments[asset_name.upper()] = attachments[asset_name]

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        compiler.compile_raw_spatial_contact_asset(
            context.path,
            analysis=context.analysis,
            project=context.project,
            attachments=attachments,
        )
    assert caught.value.code == "RAW_SPATIAL_TOPOLOGY_ENVELOPE_INVALID"
    assert f"[{nested_code}]" in str(caught.value)
    assert context.path.read_bytes() == context.source


@pytest.mark.parametrize("newline", (b"\n", b"\r\n", b"\r"))
def test_compiler_closes_exact_sections_and_preserves_contact_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    newline: bytes,
) -> None:
    context = _context(tmp_path, monkeypatch, _raw_source(newline))
    manifest, attachment = _compile(context, batch_rows=2)

    assert manifest["counts"]["nodes"] == 3
    assert manifest["counts"]["traces"] == 2
    assert manifest["counts"]["vias"] == 1
    with _open(manifest, attachment) as loaded:
        coverage = list(loaded.iter_section_coverage())
        for row in coverage:
            assert row.raw_header_count == row.logical_record_count == row.parsed_count
            assert row.section_sha256 == sha256(
                context.source[row.byte_start : row.byte_end]
            ).hexdigest()
        node = loaded.get_node("VDD", "Node2")
        assert node is not None
        assert (
            node.net_status,
            node.x_pm,
            node.y_pm,
            node.padstack_id,
            node.rotation_microdegrees,
        ) == (
            "INCIDENCE",
            1_000_000_000,
            0,
            None,
            None,
        )
        explicit = loaded.get_node("VDD", "Node1")
        inferred = loaded.get_node("VDD", "Node3")
        assert explicit is not None and explicit.net_status == "EXPLICIT"
        assert inferred is not None and inferred.net_status == "INCIDENCE"
        exact = loaded.get_trace("VDD", "Trace1")
        missing = loaded.get_trace("VDD", "Trace2")
        assert exact is not None and (
            exact.width_pm,
            exact.width_status,
            exact.width_location,
            exact.geometry_status,
        ) == (100_000_000, "EXACT", "same_line", "EXACT")
        assert missing is not None and (
            missing.width_pm,
            missing.width_status,
            missing.width_location,
            missing.geometry_status,
        ) == (None, "MISSING", "ABSENT", "TOPOLOGY_ONLY")
        via = loaded.get_via("VDD", "Via1")
        assert via is not None
        assert via.status == "EXACT"
        assert via.rotation_microdegrees == -180_000_000
        padstack = loaded.get_padstack("PS1")
        assert padstack is not None and padstack.drill_diameter_pm == 100_000_000
        shapes = list(loaded.iter_pad_shapes())
        assert [(row.layer_id, row.shape_kind, row.width_pm, row.height_pm) for row in shapes] == [
            ("L1", "CIRCLE", 200_000_000, 200_000_000),
            ("L2", "RECTANGLE", 200_000_000, 300_000_000),
        ]


@pytest.mark.parametrize("newline", (b"\n", b"\r\n", b"\r"))
def test_via_absolute_rotation_continuation_matches_tracked_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, newline: bytes
) -> None:
    primary = b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1"
    exact = primary + newline + b"+            AbsoluteRotation = 180" + newline
    source = _raw_source(newline).replace(
        primary + b" AbsoluteRotation = 180" + newline,
        exact,
    )
    context = _context(tmp_path, monkeypatch, source)

    manifest, attachment = _compile(context)

    with _open(manifest, attachment) as loaded:
        via = loaded.get_via("VDD", "Via1")
        assert via is not None
        assert via.rotation_microdegrees == -180_000_000
        assert via.source_record_sha256 == sha256(exact).hexdigest()


@pytest.mark.parametrize(
    ("replacement", "code"),
    (
        (
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1\n"
            b"+ AbsoluteRotation =\n",
            "RAW_SPATIAL_ATTRIBUTE_INVALID",
        ),
        (
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1\n"
            b"+ AbsoluteRotation = nope\n",
            "RAW_SPATIAL_ROTATION_INVALID",
        ),
        (
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1\n"
            b"+ UnknownAttribute = 90\n",
            "RAW_SPATIAL_ATTRIBUTE_UNKNOWN",
        ),
        (
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1\n"
            b"+ AbsoluteRotation = 90\n"
            b"+ AbsoluteRotation = 180\n",
            "RAW_SPATIAL_ATTRIBUTE_DUPLICATE",
        ),
        (
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1 "
            b"AbsoluteRotation = 180\n"
            b"+ AbsoluteRotation = 90\n",
            "RAW_SPATIAL_ATTRIBUTE_DUPLICATE",
        ),
    ),
)
def test_via_rotation_continuation_attributes_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: bytes,
    code: str,
) -> None:
    original = (
        b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1 "
        b"AbsoluteRotation = 180\n"
    )
    context = _context(tmp_path, monkeypatch, _raw_source().replace(original, replacement))

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)

    assert caught.value.code == code


@pytest.mark.parametrize(
    ("before", "after"),
    (
        (
            b"* Via description lines\n",
            b"* Via description lines\n+ AbsoluteRotation = 90\n",
        ),
        (
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1 "
            b"AbsoluteRotation = 180\n",
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1\n"
            b"\n+ AbsoluteRotation = 90\n",
        ),
    ),
)
def test_orphan_and_non_immediate_via_rotation_continuations_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: bytes,
    after: bytes,
) -> None:
    context = _context(tmp_path, monkeypatch, _raw_source().replace(before, after))

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)

    assert caught.value.code == "RAW_SPATIAL_ORPHAN_CONTINUATION"


def test_via_section_ends_before_optional_intermediate_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    optional = b"\n".join(
        (
            b"* WirebondDefinition description lines",
            b".WireBondModel Diameter = 0.0254mm",
            b".EndWirebondModel",
            b"* WirebondGroup description lines",
            b"* LeadframeDefinition description lines",
            b"* LeadframeGroup description lines",
            b"",
        )
    )
    source = _raw_source().replace(
        b"* PadStack collection description lines\n",
        optional + b"* PadStack collection description lines\n",
    )
    context = _context(tmp_path, monkeypatch, source)

    manifest, attachment = _compile(context)

    with _open(manifest, attachment) as loaded:
        via_coverage = next(
            row for row in loaded.iter_section_coverage() if row.section_name == "Via"
        )
        expected_end = source.index(b"* WirebondDefinition description lines")
        assert via_coverage.byte_end == expected_end
        assert via_coverage.section_sha256 == sha256(
            source[via_coverage.byte_start : expected_end]
        ).hexdigest()
        assert manifest["counts"]["vias"] == 1


def test_via_after_optional_intermediate_section_marker_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    misplaced = b"\n".join(
        (
            b"* WirebondDefinition description lines",
            b"Via2::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1",
            b"",
        )
    )
    source = _raw_source().replace(
        b"* PadStack collection description lines\n",
        misplaced + b"* PadStack collection description lines\n",
    )
    context = _context(tmp_path, monkeypatch, source)

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)

    assert caught.value.code == "RAW_SPATIAL_SECTION_ORDER_INVALID"


def test_compiler_is_batch_size_byte_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    first = _compile(context, batch_rows=1)
    second = _compile(context, batch_rows=17)
    assert first == second


def test_same_source_node_id_on_different_nets_fails_core_parser_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b"Node1::VDD X = 0mm Y = 0mm Layer = L1\n",
        b"Node1::VDD X = 0mm Y = 0mm Layer = L1\n"
        b"Node1::OTHER X = 2mm Y = 0mm Layer = L1\n",
    )
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_NODE_DUPLICATE"


def test_same_source_via_id_on_different_nets_fails_core_parser_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1 "
        b"AbsoluteRotation = 180\n",
        b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1 "
        b"AbsoluteRotation = 180\n"
        b"Via1::OTHER UpperNode = Node1 LowerNode = Node3 PadStack = PS1\n",
    )
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_VIA_DUPLICATE"


def test_isolated_netless_node_is_preserved_out_of_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b"Node3 X = 0mm Y = 0mm Layer = L2",
        b"Node3 X = 0mm Y = 0mm Layer = L2\n"
        b"Node4 X = 2mm Y = 0mm Layer = L2",
    )
    context = _context(tmp_path, monkeypatch, source)

    manifest, attachment = _compile(context)

    assert manifest["counts"]["nodes"] == 4
    with _open(manifest, attachment) as loaded:
        isolated = next(row for row in loaded.iter_nodes() if row.node_id == "Node4")
        assert isolated.net_name is None
        assert isolated.net_status == "OUT_OF_SCOPE"
        node_coverage = next(
            row
            for row in loaded.iter_section_coverage()
            if row.section_name == "Node"
        )
        assert (
            node_coverage.raw_header_count,
            node_coverage.logical_record_count,
            node_coverage.parsed_count,
            node_coverage.resolved_count,
            node_coverage.unresolved_count,
            node_coverage.retained_count,
            node_coverage.out_of_scope_count,
        ) == (4, 4, 4, 3, 1, 3, 1)


def test_netless_node_with_multiple_incident_nets_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b"Node3 X = 0mm Y = 0mm Layer = L2",
        b"Node3 X = 0mm Y = 0mm Layer = L2\n"
        b"Node4::OTHER X = 2mm Y = 0mm Layer = L2",
    ).replace(
        b"* Via description lines",
        b"Trace3::OTHER StartingNode = Node3 EndingNode = Node4 Width = 0.1mm\n"
        b"* Via description lines",
    )
    context = _context(tmp_path, monkeypatch, source)

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)

    assert caught.value.code == "RAW_SPATIAL_NODE_NET_AMBIGUOUS"
    assert "2 distinct incident nets" in str(caught.value)


def test_explicit_node_net_mismatch_still_fails_endpoint_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b"Node1::VDD X = 0mm Y = 0mm Layer = L1",
        b"Node1::OTHER X = 0mm Y = 0mm Layer = L1",
    )
    context = _context(tmp_path, monkeypatch, source)

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)

    assert caught.value.code == "RAW_SPATIAL_ENDPOINT_UNRESOLVED"


def test_out_of_scope_node_cannot_satisfy_an_endpoint_join() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(compiler._SPOOL_SQL)
        connection.executemany(
            "INSERT INTO nodes(ordinal,node_id,node_fold,explicit_net,explicit_net_fold,"
            "x_pm,y_pm,layer_id,layer_fold,padstack_id,rotation,source_sha) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (0, "Node1", "node1", "VDD", "vdd", 0, 0, "L1", "l1", None, None, "a" * 64),
                (1, "Node4", "node4", None, None, 1, 0, "L1", "l1", None, None, "b" * 64),
            ),
        )
        connection.execute(
            "INSERT INTO traces VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                0,
                "Trace1",
                "trace1",
                "VDD",
                "vdd",
                "Node4",
                "node4",
                "Node1",
                "node1",
                1,
                "EXACT",
                "same_line",
                "EXACT",
                "c" * 64,
            ),
        )

        with pytest.raises(compiler.RawSpatialCompilerError) as caught:
            compiler._resolve_contacts(
                connection,
                set(),
                set(),
                set(),
                {"l1": 0},
                1,
                lambda: False,
            )

        assert caught.value.code == "RAW_SPATIAL_ENDPOINT_UNRESOLVED"
    finally:
        connection.close()


def test_duplicate_node_still_aborts_without_an_asset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _raw_source().replace(
        b"Node3 X = 0mm Y = 0mm Layer = L2",
        b"Node3 X = 0mm Y = 0mm Layer = L2\n"
        b"Node1::VDD X = 3mm Y = 0mm Layer = L1",
    )
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_NODE_DUPLICATE"


def test_endpoint_net_suffix_mismatch_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(b"StartingNode = Node1::VDD", b"StartingNode = Node1::OTHER")
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_ENDPOINT_NET_MISMATCH"


def test_submicrodegree_rotation_aborts_instead_of_rounding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(b"AbsoluteRotation = 180", b"AbsoluteRotation = 180.0000001")
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_ROTATION_INVALID"


def test_via_rotation_alias_fails_tracked_core_parser_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(b"AbsoluteRotation = 180", b"Rotation = 180")
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_VIA_PARSER_MISMATCH"


def test_nested_paddef_from_missing_terminator_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(b".EndPadDef\n.PadDef L2", b".PadDef L2", 1)
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_PAD_SHAPE_INVALID"


@pytest.mark.parametrize("orphan", (b".EndPadDef\n", b".EndPadStackDef\n"))
def test_orphan_pad_terminator_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, orphan: bytes
) -> None:
    source = _raw_source().replace(
        b"* PadStack collection description lines\n",
        b"* PadStack collection description lines\n" + orphan,
    )
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError):
        _compile(context)


def test_compiler_rejects_batch_larger_than_asset_builder_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context, batch_rows=10_001)
    assert caught.value.code == "RAW_SPATIAL_BATCH_INVALID"


def test_surface_manifest_input_order_is_byte_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    first = _compile(context)
    context.project.metadata["spd_import"]["plane_geometries"].reverse()
    second = _compile(context)
    assert first == second


def test_contact_parse_uses_content_addressed_snapshot_against_restore_attack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    original_parse = compiler._parse_contacts
    initial_stat = context.path.stat()
    poisoned = context.source.replace(b"Node2 X = 1mm", b"Node2 X = 9mm")

    def attack(snapshot_path: Path, *args: object, **kwargs: object):
        context.path.write_bytes(poisoned)
        os.utime(
            context.path,
            ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns),
        )
        try:
            return original_parse(snapshot_path, *args, **kwargs)
        finally:
            context.path.write_bytes(context.source)
            os.utime(
                context.path,
                ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns),
            )

    monkeypatch.setattr(compiler, "_parse_contacts", attack)
    manifest, attachment = _compile(context)
    assert manifest["source_sha256"] == sha256(context.source).hexdigest()
    with _open(manifest, attachment) as loaded:
        node = loaded.get_node("VDD", "Node2")
        assert node is not None and node.x_pm == 1_000_000_000


def test_unsupported_pad_geometry_keeps_retained_coaxial_via_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b"Regular Box 0.2mm 0.3mm",
        b"Regular Polygon 0mm 0mm\n+ 0.2mm 0mm 0.2mm 0.3mm",
    )
    context = _context(tmp_path, monkeypatch, source)
    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        via = loaded.get_via("VDD", "Via1")
        assert via is not None and via.status == "UNRESOLVED"
        assert [row.layer_id for row in loaded.iter_pad_shapes()] == ["L1"]


def test_unsupported_pad_shape_reason_is_bound_to_core_parser_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b"Regular Box 0.2mm 0.3mm",
        b"Regular Polygon 0mm 0mm\n+ 0.2mm 0mm 0.2mm 0.3mm",
    )
    context = _context(tmp_path, monkeypatch, source)
    context.analysis.padstacks[0].pad_shapes[1].reason = "tampered reason"

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH"


@pytest.mark.parametrize("rotation", (b"90", b"junk"))
def test_undefined_declared_node_padstack_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rotation: bytes,
) -> None:
    original = b"Node1::VDD X = 0mm Y = 0mm Layer = L1"
    changed = original + b" PadStack = DUT AbsoluteRotation = " + rotation
    context = _context(tmp_path, monkeypatch, _raw_source().replace(original, changed, 1))
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_PADSTACK_UNRESOLVED"


def test_defined_node_padstack_and_rotation_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"Node1::VDD X = 0mm Y = 0mm Layer = L1"
    changed = original + b" PadStack = PS1 AbsoluteRotation = 90"
    context = _context(tmp_path, monkeypatch, _raw_source().replace(original, changed, 1))
    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        node = loaded.get_node("VDD", "Node1")
        assert node is not None
        assert (node.padstack_id, node.rotation_microdegrees) == ("PS1", 90_000_000)
        assert node.source_record_sha256 == sha256(changed + b"\n").hexdigest()
        via = loaded.get_via("VDD", "Via1")
        assert via is not None and via.padstack_id == "PS1" and via.status == "EXACT"


def test_zero_drill_component_padstack_is_none_and_cannot_prove_a_via(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap_block = (
        b".PadStackDef CAP 0.00mm Material = Copper\n"
        b".PadDef L1\n"
        b"Regular Circle 0.1mm\n"
        b".EndPadDef\n"
        b".PadDef L2\n"
        b"Regular Circle 0.1mm\n"
        b".EndPadDef\n"
        b".EndPadStackDef\n"
    )
    source = _raw_source().replace(
        b"* Material description lines\n",
        cap_block + b"* Material description lines\n",
    )

    def add_analysis_cap(context: _Context) -> None:
        cap = SimpleNamespace(
            name="CAP",
            drill_diameter_um=0.0,
            pad_width_um=200.0,
            pad_height_um=200.0,
            layers=("L1", "L2"),
            material="Copper",
            pad_shapes=(
                SimpleNamespace(
                    layer="L1", kind="CIRCLE", width_um=200.0, height_um=200.0
                ),
                SimpleNamespace(
                    layer="L2", kind="CIRCLE", width_um=200.0, height_um=200.0
                ),
            ),
        )
        context.analysis.padstacks = (*context.analysis.padstacks, cap)

    unused = _context(tmp_path, monkeypatch, source)
    add_analysis_cap(unused)
    manifest, attachment = _compile(unused)
    with _open(manifest, attachment) as loaded:
        cap = loaded.get_padstack("CAP")
        assert cap is not None
        assert cap.drill_diameter_pm is None
        assert cap.source_record_sha256 == sha256(cap_block).hexdigest()

    used_source = source.replace(b"PadStack = PS1", b"PadStack = CAP")
    used = _context(tmp_path, monkeypatch, used_source)
    add_analysis_cap(used)
    used_manifest, used_attachment = _compile(used)
    with _open(used_manifest, used_attachment) as loaded:
        via = loaded.get_via("VDD", "Via1")
        assert via is not None and via.status == "UNRESOLVED"


@pytest.mark.parametrize(
    ("needle", "replacement"),
    (
        (b".PadStackDef PS1 0.05mm", b".PadStackDef PS1 -0.05mm"),
        (b"Regular Circle 0.1mm", b"Regular Circle 0.00mm"),
    ),
)
def test_zero_drill_exception_does_not_weaken_physical_dimension_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    needle: bytes,
    replacement: bytes,
) -> None:
    context = _context(tmp_path, monkeypatch, _raw_source().replace(needle, replacement, 1))
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_LENGTH_INVALID"


def test_padstack_replays_core_binary64_operations_but_persists_exact_pm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (
        _raw_source()
        .replace(b".PadStackDef PS1 0.05mm", b".PadStackDef PS1 0.1um", 1)
        .replace(b"Regular Circle 0.1mm", b"Regular Circle 0.1um", 1)
    )
    context = _context(tmp_path, monkeypatch, source)
    expected_diameter_um = 2.0 * _length_um(b"0.1um")
    analysis_padstack = context.analysis.padstacks[0]
    analysis_padstack.drill_diameter_um = expected_diameter_um
    analysis_padstack.pad_shapes[0].width_um = expected_diameter_um
    analysis_padstack.pad_shapes[0].height_um = expected_diameter_um

    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        padstack = loaded.get_padstack("PS1")
        assert padstack is not None
        assert padstack.drill_diameter_pm == 200_000
        l1_shape = next(
            item
            for item in loaded.iter_pad_shapes()
            if item.padstack_id == "PS1" and item.layer_id == "L1"
        )
        assert (l1_shape.width_pm, l1_shape.height_pm) == (200_000, 200_000)


@pytest.mark.parametrize(
    "target",
    ("drill", "aggregate_width", "shape_width", "shape_height"),
)
def test_padstack_one_ulp_analysis_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    context = _context(tmp_path, monkeypatch)
    padstack = context.analysis.padstacks[0]
    if target == "drill":
        padstack.drill_diameter_um = math.nextafter(
            padstack.drill_diameter_um, math.inf
        )
    elif target == "aggregate_width":
        padstack.pad_width_um = math.nextafter(padstack.pad_width_um, math.inf)
    elif target == "shape_width":
        shape = padstack.pad_shapes[0]
        shape.width_um = math.nextafter(shape.width_um, math.inf)
    else:
        shape = padstack.pad_shapes[1]
        shape.height_um = math.nextafter(shape.height_um, math.inf)

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_ANALYSIS_PADSTACK_MISMATCH"


def test_pad_shape_provenance_binds_exact_paddef_and_regular_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_context = _context(tmp_path, monkeypatch)
    first_manifest, first_attachment = _compile(first_context)
    with _open(first_manifest, first_attachment) as loaded:
        first = list(loaded.iter_pad_shapes())[0]

    changed_source = _raw_source().replace(b".PadDef L1\n", b".PadDef   L1\n")
    second_context = _context(tmp_path, monkeypatch, changed_source)
    second_manifest, second_attachment = _compile(second_context)
    with _open(second_manifest, second_attachment) as loaded:
        second = list(loaded.iter_pad_shapes())[0]

    assert (first.padstack_id, first.layer_id, first.width_pm, first.height_pm) == (
        second.padstack_id,
        second.layer_id,
        second.width_pm,
        second.height_pm,
    )
    assert first.source_record_sha256 != second.source_record_sha256


def test_layer_source_hash_binds_exact_immediate_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_context = _context(tmp_path, monkeypatch)
    first_manifest, first_attachment = _compile(first_context)
    with _open(first_manifest, first_attachment) as loaded:
        first = list(loaded.iter_layers())[0]

    changed_source = _raw_source().replace(
        b"L1 Thickness = 0.035mm Material = Copper\n",
        b"L1 Thickness = 0.035mm Material = Copper\n+ SourceNote = exact\n",
    )
    second_context = _context(tmp_path, monkeypatch, changed_source)
    second_manifest, second_attachment = _compile(second_context)
    with _open(second_manifest, second_attachment) as loaded:
        second = list(loaded.iter_layers())[0]

    assert first.layer_id == second.layer_id == "L1"
    assert first.source_record_sha256 != second.source_record_sha256


def test_layer_patch_row_is_validated_and_bound_to_layer_source_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer = b"L1 Thickness = 0.035mm Material = Copper\n"
    patch = b"PatchL1 Shape = L1pkgshape Layer = L1\n"
    source = _raw_source().replace(layer, layer + patch, 1)
    context = _context(tmp_path, monkeypatch, source)

    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        stored = list(loaded.iter_layers())[0]

    assert stored.layer_id == "L1"
    assert stored.source_record_sha256 == sha256(layer + patch).hexdigest()


@pytest.mark.parametrize(
    ("patch", "code"),
    (
        (
            b"PatchL1 Shape = L1pkgshape\n",
            "RAW_SPATIAL_LAYER_PATCH_MALFORMED",
        ),
        (
            b"PatchL1 Shape = L2pkgshape Layer = L1\n",
            "RAW_SPATIAL_LAYER_PATCH_MISMATCH",
        ),
        (
            b"PatchL2 Shape = L2pkgshape Layer = L2\n",
            "RAW_SPATIAL_LAYER_PATCH_MISMATCH",
        ),
        (
            b"PatchL1 Shape = L1pkgshape Layer = L1\n"
            b"PatchL1 Shape = L1pkgshape Layer = L1\n",
            "RAW_SPATIAL_LAYER_PATCH_DUPLICATE",
        ),
    ),
)
def test_layer_patch_row_rejects_malformed_duplicate_or_mismatched_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch: bytes,
    code: str,
) -> None:
    layer = b"L1 Thickness = 0.035mm Material = Copper\n"
    context = _context(
        tmp_path,
        monkeypatch,
        _raw_source().replace(layer, layer + patch, 1),
    )

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == code


def test_surface_source_hash_binds_exact_shape_header_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_context = _context(tmp_path, monkeypatch)
    first_manifest, first_attachment = _compile(first_context)
    with _open(first_manifest, first_attachment) as loaded:
        first = list(loaded.iter_surfaces(layer_id="L1"))[0]

    changed_source = _raw_source().replace(
        b".Shape L1PkgShape\n", b".Shape   L1PkgShape\n"
    )
    second_context = _context(tmp_path, monkeypatch, changed_source)
    second_manifest, second_attachment = _compile(second_context)
    with _open(second_manifest, second_attachment) as loaded:
        second = list(loaded.iter_surfaces(layer_id="L1"))[0]

    assert first.layer_id == second.layer_id == "L1"
    assert first.source_record_sha256 != second.source_record_sha256


def _single_surface_geometry(
    *,
    polygons: tuple[tuple[tuple[float, float], ...], ...] = (),
    circles: tuple[tuple[float, float, float], ...] = (),
) -> SimpleNamespace:
    order = tuple(
        [("positive_polygon", index) for index in range(len(polygons))]
        + [("positive_circle", index) for index in range(len(circles))]
    )
    return SimpleNamespace(
        positive_polygons_um=polygons,
        negative_polygons_um=(),
        positive_circles_um=circles,
        negative_circles_um=(),
        primitive_order=order,
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )


def _expected_surface_source_hash(
    key: tuple[str, str], header: bytes, record: bytes
) -> str:
    digest = sha256()
    digest.update(compiler._SURFACE_DIGEST_DOMAIN)
    for token in key:
        encoded = token.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    for exact in (header, record):
        digest.update(len(exact).to_bytes(8, "big"))
        digest.update(exact)
    return digest.hexdigest()


def test_surface_source_hash_accepts_real_scale_polygon_continuations(
    tmp_path: Path,
) -> None:
    header = b".Shape L1PkgShape\n"
    first = b"Polygon0276107::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm\n"
    continuation = b"+" + (b" " * (16 * 1024)) + b"\n"
    record = first + continuation * 70
    assert len(record) > 1024 * 1024
    source = header + record + b".EndShape\n"
    path = tmp_path / "real-scale-shape.spd"
    path.write_bytes(source)
    key = ("vdd", "l1")
    geometry = _single_surface_geometry(
        polygons=(((0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)),)
    )

    hashes, bounds = compiler._surface_source_hashes(
        path, len(source), {key}, {key: geometry}, lambda: False
    )

    assert hashes[key] == _expected_surface_source_hash(key, header, record)
    assert bounds[key] == (
        Decimal(0),
        Decimal(1_000_000_000),
        Decimal(0),
        Decimal(1_000_000_000),
    )


def test_surface_source_hash_keeps_shape_record_bounds_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = b".Shape L1PkgShape\n"
    record = (
        b"Polygon::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm\n"
        + b"+ 0mm 0mm\n" * 4
    )
    source = header + record + b".EndShape\n"
    path = tmp_path / "over-bound-shape.spd"
    path.write_bytes(source)
    key = ("vdd", "l1")
    monkeypatch.setattr(compiler, "_MAX_SHAPE_LOGICAL_RECORD_BYTES", len(record) - 1)

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        compiler._surface_source_hashes(
            path,
            len(source),
            {key},
            {key: _single_surface_geometry()},
            lambda: False,
        )

    assert caught.value.code == "RAW_SPATIAL_SURFACE_RECORD_BOUND_EXCEEDED"
    assert caught.value.source_offset == len(header)


def test_nonpolygon_plus_line_matches_core_shape_record_framing(
    tmp_path: Path,
) -> None:
    header = b".Shape L1PkgShape\n"
    circle = b"Circle::VDD+ 0mm 0mm 1mm\n"
    ignored = b"+ 99mm 99mm\n"
    source = header + circle + ignored + b".EndShape\n"
    path = tmp_path / "circle-plus-line.spd"
    path.write_bytes(source)
    key = ("vdd", "l1")

    hashes, bounds = compiler._surface_source_hashes(
        path,
        len(source),
        {key},
        {key: _single_surface_geometry(circles=((0.0, 0.0, 1000.0),))},
        lambda: False,
    )

    assert hashes[key] == _expected_surface_source_hash(key, header, circle)
    assert bounds[key] == (
        Decimal(-1_000_000_000),
        Decimal(1_000_000_000),
        Decimal(-1_000_000_000),
        Decimal(1_000_000_000),
    )


@pytest.mark.parametrize(
    "mutator",
    (
        lambda raw: raw.replace(
            b"* Trace description lines\n",
            b"* Trace description lines\nNotATrace row\n",
        ),
        lambda raw: raw.replace(
            b"* Via description lines\n",
            b"* Via description lines\n+ orphan\n",
        ),
        lambda raw: raw.replace(
            b"* Via description lines\n",
            b"* Trace description lines\n* Via description lines\n",
        ),
    ),
)
def test_malformed_section_framing_aborts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutator: object,
) -> None:
    source = mutator(_raw_source())
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError):
        _compile(context)


def test_artwork_byte_mismatch_fails_before_contact_asset_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    context.attachments["geometry/l1.spdgeom.zlib"] = b"tampered"
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_ARTWORK_ASSET_INVALID"


def test_project_bbox_must_equal_decoded_and_analysis_artwork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    context.project.metadata["spd_import"]["plane_geometries"][0]["bbox_um"] = [
        0.0,
        999.0,
        0.0,
        1000.0,
    ]
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_SURFACE_BBOX_INVALID"


def test_artwork_payload_layer_and_net_are_verified_not_only_its_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    square = ((0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0))
    wrong = core_services._compress_spd_geometry_payload(
        layer="L1",
        net="OTHER",
        positive_polygons=(square,),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )[0]
    context.attachments["geometry/l1.spdgeom.zlib"] = wrong
    context.project.metadata["spd_import"]["plane_geometries"][0][
        "asset_sha256"
    ] = sha256(wrong).hexdigest()
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_ARTWORK_ASSET_INVALID"


def test_same_bbox_different_artwork_primitives_fail_analysis_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    triangle = ((0.0, 0.0), (1000.0, 0.0), (0.0, 1000.0))
    different = core_services._compress_spd_geometry_payload(
        layer="L1",
        net="VDD",
        positive_polygons=(triangle,),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )[0]
    context.attachments["geometry/l1.spdgeom.zlib"] = different
    context.project.metadata["spd_import"]["plane_geometries"][0][
        "asset_sha256"
    ] = sha256(different).hexdigest()
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_ANALYSIS_SURFACE_MISMATCH"


def test_raw_shape_coordinates_must_match_forged_analysis_and_artwork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"Polygon::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm"
    changed = b"Polygon::VDD+ 0mm 0mm 1mm 0mm 0.5mm 0.5mm 1mm 1mm 0mm 1mm"
    source = _raw_source().replace(original, changed, 1)

    # _context deliberately supplies the original square as both SpdAnalysis
    # and persisted artwork while binding all source identities to changed raw bytes.
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH"


def test_surface_one_ulp_analysis_and_artwork_drift_fails_source_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    tampered = (
        (0.0, 0.0),
        (1000.0, 0.0),
        (math.nextafter(1000.0, -math.inf), 1000.0),
        (0.0, 1000.0),
    )
    geometry = SimpleNamespace(
        net="VDD",
        layer="L1",
        positive_polygons_um=(tampered,),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )
    context.analysis.plane_geometries = (
        geometry,
        context.analysis.plane_geometries[1],
    )
    artwork = core_services._compress_spd_geometry_payload(
        layer="L1",
        net="VDD",
        positive_polygons=(tampered,),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )[0]
    context.attachments["geometry/l1.spdgeom.zlib"] = artwork
    context.project.metadata["spd_import"]["plane_geometries"][0][
        "asset_sha256"
    ] = sha256(artwork).hexdigest()

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH"


def test_unsupported_retained_primitive_after_endshape_matches_tracked_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b".EndShape\n.Shape L2PkgShape",
        b".EndShape\nArc::VDD+ 0mm 0mm 1mm\n.Shape L2PkgShape",
        1,
    )
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_SURFACE_PRIMITIVE_UNSUPPORTED"


def test_same_layer_via_cannot_be_exact_physical_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b"Node3 X = 0mm Y = 0mm Layer = L2",
        b"Node3 X = 0mm Y = 0mm Layer = L1",
    )
    context = _context(tmp_path, monkeypatch, source)
    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        via = loaded.get_via("VDD", "Via1")
        assert via is not None
        assert (via.start_layer_id, via.end_layer_id, via.status) == (
            "L1",
            "L1",
            "UNRESOLVED",
        )


def test_reversed_stack_via_cannot_be_exact_physical_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(
        b"UpperNode = Node1 LowerNode = Node3",
        b"UpperNode = Node3 LowerNode = Node1",
    )
    context = _context(tmp_path, monkeypatch, source)
    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        via = loaded.get_via("VDD", "Via1")
        assert via is not None
        assert (via.start_layer_id, via.end_layer_id, via.status) == (
            "L2",
            "L1",
            "UNRESOLVED",
        )


def test_via_endpoint_without_conductor_layer_evidence_is_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    context.analysis.stackup_layers = (
        SimpleNamespace(name="L1", is_conductor=True),
        SimpleNamespace(name="L2", is_conductor=False),
    )
    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        via = loaded.get_via("VDD", "Via1")
        assert via is not None and via.status == "UNRESOLVED"


def test_via_suffix_is_preserved_in_identity_and_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(b"Via1::VDD", b"Via1!!suffix::VDD")
    context = _context(tmp_path, monkeypatch, source)
    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        via = loaded.get_via("VDD", "Via1!!suffix")
        assert via is not None
        assert via.owner_id == "via:vdd:via1!!suffix"
        assert loaded.get_via("VDD", "Via1") is None


@pytest.mark.parametrize(
    "token",
    (b"1e-999999999mm", b"1e-1000015m", b"1e999999999mm"),
)
def test_decimal_underflow_subnormal_and_overflow_are_stable_length_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: bytes
) -> None:
    source = _raw_source().replace(b"Node1::VDD X = 0mm", b"Node1::VDD X = " + token)
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_LENGTH_INVALID"


@pytest.mark.parametrize("token", (b"1e-999999999", b"1e999999999"))
def test_rotation_underflow_and_overflow_are_stable_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: bytes
) -> None:
    source = _raw_source().replace(b"AbsoluteRotation = 180", b"AbsoluteRotation = " + token)
    context = _context(tmp_path, monkeypatch, source)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_ROTATION_INVALID"


def test_derived_diameter_enforces_signed_64_bit_bound() -> None:
    assert compiler._double_pm((2**63 - 1) // 2, "test", offset=0) == 2**63 - 2
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        compiler._double_pm((2**63 - 1) // 2 + 1, "test", offset=0)
    assert caught.value.code == "RAW_SPATIAL_LENGTH_INVALID"


@pytest.mark.parametrize(
    ("before", "after"),
    (
        (b".PadStackDef PS1 0.05mm", b".PadStackDef PS1 4611686018.427387904mm"),
        (b"Regular Circle 0.1mm", b"Regular Circle 4611686018.427387904mm"),
    ),
)
def test_derived_source_diameter_overflow_is_a_stable_compiler_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: bytes,
    after: bytes,
) -> None:
    context = _context(tmp_path, monkeypatch, _raw_source().replace(before, after, 1))
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_LENGTH_INVALID"


def test_exact_decimal_conversions_ignore_ambient_context() -> None:
    with localcontext() as context:
        context.prec = 1
        context.Emax = 1
        context.Emin = -1
        assert compiler._pm(b"123.456789um", "test", offset=0) == 123_456_789
        assert compiler._rotation_microdegrees(b"12.345678", offset=0) == 12_345_678
        assert compiler._um_to_pm("123.456789", "test") == 123_456_789
        assert compiler._surface_pm_decimal(
            "123.456789012345678901234567890", "test"
        ) == Decimal("123456789.012345678901234567890")
        assert compiler._half_integer_decimal(2**63 - 1) == Decimal(
            "4611686018427387903.5"
        )
        assert compiler._surface_pm_fraction(
            b"9.176753e-04mm", "Shape coordinate", offset=0
        ) == Fraction(9_176_753, 10)

        with pytest.raises(compiler.RawSpatialCompilerError) as surface_float:
            compiler._surface_pm_decimal(
                0.15000000000000002, "binary64 box edge"
            )
        assert (
            surface_float.value.code
            == "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH"
        )

        with pytest.raises(compiler.RawSpatialCompilerError) as bbox_float:
            compiler._um_to_pm(0.15000000000000002, "binary64 bbox edge")
        assert bbox_float.value.code == "RAW_SPATIAL_SURFACE_BBOX_INVALID"

        with pytest.raises(compiler.RawSpatialCompilerError) as length_error:
            compiler._pm(
                b"1.00000000000000000000000000001um",
                "test",
                offset=0,
            )
        assert length_error.value.code == "RAW_SPATIAL_LENGTH_INVALID"

        with pytest.raises(compiler.RawSpatialCompilerError) as rotation_error:
            compiler._rotation_microdegrees(
                b"1.00000000000000000000000000001",
                offset=0,
            )
        assert rotation_error.value.code == "RAW_SPATIAL_ROTATION_INVALID"

        with pytest.raises(compiler.RawSpatialCompilerError) as node_grid_error:
            compiler._pm(b"9.176753e-04mm", "Node coordinate", offset=0)
        assert node_grid_error.value.code == "RAW_SPATIAL_LENGTH_INVALID"


@pytest.mark.parametrize(
    "token",
    (
        b"1e-1001mm",
        b"1e1001mm",
        b"0e1001mm",
        b"-0e-1001mm",
        b"0" * 257 + b"mm",
    ),
)
def test_shape_fraction_parser_has_bounded_decimal_exponents(token: bytes) -> None:
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        compiler._surface_pm_fraction(token, "Shape coordinate", offset=0)
    assert caught.value.code == "RAW_SPATIAL_LENGTH_INVALID"


@pytest.mark.parametrize("token", (b"0e1000mm", b"-0e-1000mm"))
def test_shape_fraction_parser_allows_bounded_signed_zero(token: bytes) -> None:
    assert compiler._surface_pm_fraction(
        token, "Shape coordinate", offset=0
    ) == Fraction(0, 1)


def test_fractional_interior_shape_polygon_preserves_exact_integer_bbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = b"Polygon::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm"
    token = b"9.176753e-04mm"
    negative_record = (
        b"Polygon::VDD- "
        + token
        + b" 0.1mm 0.2mm 0.1mm 0.2mm 0.2mm"
    )
    source = _raw_source().replace(original, original + b"\n" + negative_record, 1)
    context = _context(tmp_path, monkeypatch, source)
    x_um = _length_um(token)
    square = ((0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0))
    negative = ((x_um, 100.0), (200.0, 100.0), (200.0, 200.0))
    _replace_l1_geometry(
        context,
        positive_polygons=(square,),
        negative_polygons=(negative,),
        primitive_order=(("positive_polygon", 0), ("negative_polygon", 0)),
    )

    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        surface = next(loaded.iter_surfaces(net_name="VDD", layer_id="L1"))
        assert (
            surface.min_x_pm,
            surface.min_y_pm,
            surface.max_x_pm,
            surface.max_y_pm,
        ) == (0, 0, 1_000_000_000, 1_000_000_000)


def test_fractional_positive_bbox_extrema_fail_v1_integer_pm_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = b"Polygon::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm"
    token = b"9.176753e-04mm"
    replacement = (
        b"Polygon::VDD+ "
        + token
        + b" 0mm 1mm 0mm 1mm 1mm "
        + token
        + b" 1mm"
    )
    source = _raw_source().replace(original, replacement, 1)
    context = _context(tmp_path, monkeypatch, source)
    x_um = _length_um(token)
    polygon = (
        (x_um, 0.0),
        (1000.0, 0.0),
        (1000.0, 1000.0),
        (x_um, 1000.0),
    )
    _replace_l1_geometry(
        context,
        positive_polygons=(polygon,),
        primitive_order=(("positive_polygon", 0),),
    )

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_SURFACE_BBOX_INVALID"


@pytest.mark.parametrize("kind", ("circle", "box"))
def test_fractional_shape_circle_and_box_edges_remain_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    original = b"Polygon::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm"
    token = b"1e-7um"
    if kind == "circle":
        fractional_record = b"Circle::VDD- " + b" ".join((token, token, token))
    else:
        fractional_record = b"Box::VDD- " + b" ".join(
            (token, token, token, token)
        )
    source = _raw_source().replace(original, original + b"\n" + fractional_record, 1)
    context = _context(tmp_path, monkeypatch, source)
    value_um = _length_um(token)
    square = ((0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0))
    if kind == "circle":
        _replace_l1_geometry(
            context,
            positive_polygons=(square,),
            negative_circles=((value_um, value_um, value_um),),
            primitive_order=(("positive_polygon", 0), ("negative_circle", 0)),
        )
    else:
        half = value_um / 2.0
        polygon = (
            (value_um - half, value_um - half),
            (value_um + half, value_um - half),
            (value_um + half, value_um + half),
            (value_um - half, value_um + half),
        )
        _replace_l1_geometry(
            context,
            positive_polygons=(square,),
            negative_polygons=(polygon,),
            primitive_order=(("positive_polygon", 0), ("negative_polygon", 0)),
            box_count=1,
        )

    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        surface = next(loaded.iter_surfaces(net_name="VDD", layer_id="L1"))
        assert (
            surface.min_x_pm,
            surface.min_y_pm,
            surface.max_x_pm,
            surface.max_y_pm,
        ) == (0, 0, 1_000_000_000, 1_000_000_000)


@pytest.mark.parametrize("fractional_pm", (0.125, 0.5))
def test_large_binary64_off_grid_values_never_certify_exact_picometres(
    fractional_pm: float,
) -> None:
    value_um = (2**48 + fractional_pm) / 1_000_000.0
    assert math.isfinite(value_um)
    assert value_um * 1_000_000.0 - 2**48 == fractional_pm

    with pytest.raises(compiler.RawSpatialCompilerError) as bbox_error:
        compiler._um_to_pm(value_um, "large binary64 bbox coordinate")
    assert bbox_error.value.code == "RAW_SPATIAL_SURFACE_BBOX_INVALID"

    with pytest.raises(compiler.RawSpatialCompilerError) as surface_error:
        compiler._surface_pm_decimal(
            value_um, "large binary64 surface coordinate"
        )
    assert (
        surface_error.value.code
        == "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH"
    )


@pytest.mark.parametrize(
    ("before", "after", "code"),
    (
        (
            b"Node1::VDD X = 0mm Y = 0mm Layer = L1",
            b"Node1::VDD Y = 0mm X = 0mm Layer = L1",
            "RAW_SPATIAL_NODE_PARSER_MISMATCH",
        ),
        (
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1",
            b"Via1::VDD PadStack = PS1 LowerNode = Node3 UpperNode = Node1",
            "RAW_SPATIAL_VIA_PARSER_MISMATCH",
        ),
    ),
)
def test_node_and_via_grammar_must_match_tracked_core_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: bytes,
    after: bytes,
    code: str,
) -> None:
    context = _context(tmp_path, monkeypatch, _raw_source().replace(before, after))
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("before", "after", "code"),
    (
        (
            b"Node1::VDD X = 0mm Y = 0mm Layer = L1",
            b"Node1::VDD X = 0mm\n+ Y = 0mm Layer = L1",
            "RAW_SPATIAL_NODE_PARSER_MISMATCH",
        ),
        (
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3 PadStack = PS1",
            b"Via1::VDD UpperNode = Node1 LowerNode = Node3\n+ PadStack = PS1",
            "RAW_SPATIAL_VIA_PARSER_MISMATCH",
        ),
    ),
)
def test_node_and_unsupported_via_continuations_fail_core_parser_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: bytes,
    after: bytes,
    code: str,
) -> None:
    context = _context(tmp_path, monkeypatch, _raw_source().replace(before, after))
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == code


def test_via_parser_parity_preserves_case_insensitive_padstack_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _raw_source().replace(b"PadStack = PS1", b"PadStack = ps1", 1)
    context = _context(tmp_path, monkeypatch, source)
    manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        via = loaded.get_via("VDD", "Via1")
        assert via is not None and via.padstack_id == "PS1"


def test_box_binary64_edges_round_trip_to_exact_picometres(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"Polygon::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm"
    source = _raw_source().replace(
        original,
        b"Box::VDD+ 0.1um 0.1um 0.1um 0.1um",
        1,
    )
    context = _context(tmp_path, monkeypatch, source)
    center_um = _length_um(b"0.1um")
    half_extent_um = _length_um(b"0.1um") / 2.0
    minimum_um = center_um - half_extent_um
    maximum_um = center_um + half_extent_um
    box = (
        (minimum_um, minimum_um),
        (maximum_um, minimum_um),
        (maximum_um, maximum_um),
        (minimum_um, maximum_um),
    )
    assert (minimum_um, maximum_um) == (0.05, 0.15000000000000002)
    l1_geometry = SimpleNamespace(
        net="VDD",
        layer="L1",
        positive_polygons_um=(box,),
        negative_polygons_um=(),
        positive_circles_um=(),
        negative_circles_um=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=1,
    )
    context.analysis.plane_geometries = (
        l1_geometry,
        context.analysis.plane_geometries[1],
    )
    artwork = core_services._compress_spd_geometry_payload(
        layer="L1",
        net="VDD",
        positive_polygons=(box,),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=1,
    )[0]
    context.attachments["geometry/l1.spdgeom.zlib"] = artwork
    row = context.project.metadata["spd_import"]["plane_geometries"][0]
    row["asset_sha256"] = sha256(artwork).hexdigest()
    row["bbox_um"] = [minimum_um, maximum_um, minimum_um, maximum_um]

    with localcontext() as decimal_context:
        decimal_context.prec = 1
        decimal_context.Emax = 1
        decimal_context.Emin = -1
        manifest, attachment = _compile(context)
    with _open(manifest, attachment) as loaded:
        surface = next(loaded.iter_surfaces(net_name="VDD", layer_id="L1"))
        assert (
            surface.min_x_pm,
            surface.min_y_pm,
            surface.max_x_pm,
            surface.max_y_pm,
        ) == (50_000, 50_000, 150_000, 150_000)


def test_project_bbox_integer_alias_is_not_accepted_as_binary64_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    row = context.project.metadata["spd_import"]["plane_geometries"][0]
    row["bbox_um"][0] = 0

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_SURFACE_BBOX_INVALID"


def test_artwork_integer_coordinate_alias_is_not_accepted_as_binary64_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    polygon = ((0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0))
    artwork = core_services._compress_spd_geometry_payload(
        layer="L1",
        net="VDD",
        positive_polygons=(polygon,),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )[0]
    context.attachments["geometry/l1.spdgeom.zlib"] = artwork
    row = context.project.metadata["spd_import"]["plane_geometries"][0]
    row["asset_sha256"] = sha256(artwork).hexdigest()

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_ARTWORK_ASSET_INVALID"


@pytest.mark.parametrize("primitive_kind", ("circle", "box"))
def test_every_derived_surface_edge_obeys_signed64_even_when_negative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primitive_kind: str,
) -> None:
    original = b"Polygon::VDD+ 0mm 0mm 1mm 0mm 1mm 1mm 0mm 1mm"
    maximum = b"9223372036854.775807um"
    if primitive_kind == "circle":
        primitive = b"Circle::VDD- " + maximum + b" 0um 0.000001um"
    else:
        primitive = b"Box::VDD- " + maximum + b" 0um 0.000002um 0.000002um"
    source = _raw_source().replace(original, original + b"\n" + primitive, 1)
    context = _context(tmp_path, monkeypatch, source)
    base = context.analysis.plane_geometries[0]
    x_um = _length_um(maximum)
    if primitive_kind == "circle":
        radius_um = _length_um(b"0.000001um")
        negative_polygons: tuple[object, ...] = ()
        negative_circles: tuple[object, ...] = ((x_um, 0.0, radius_um),)
        order = (("positive_polygon", 0), ("negative_circle", 0))
        box_count = 0
    else:
        width_um = _length_um(b"0.000002um")
        half_width = width_um / 2.0
        box = (
            (x_um - half_width, -half_width),
            (x_um + half_width, -half_width),
            (x_um + half_width, half_width),
            (x_um - half_width, half_width),
        )
        negative_polygons = (box,)
        negative_circles = ()
        order = (("positive_polygon", 0), ("negative_polygon", 0))
        box_count = 1
    geometry = SimpleNamespace(
        net="VDD",
        layer="L1",
        positive_polygons_um=base.positive_polygons_um,
        negative_polygons_um=negative_polygons,
        positive_circles_um=(),
        negative_circles_um=negative_circles,
        primitive_order=order,
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=box_count,
    )
    context.analysis.plane_geometries = (
        geometry,
        context.analysis.plane_geometries[1],
    )
    artwork = core_services._compress_spd_geometry_payload(
        layer="L1",
        net="VDD",
        positive_polygons=geometry.positive_polygons_um,
        negative_polygons=geometry.negative_polygons_um,
        positive_circles=geometry.positive_circles_um,
        negative_circles=geometry.negative_circles_um,
        primitive_order=geometry.primitive_order,
        positive_subelement_count=0,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=box_count,
    )[0]
    context.attachments["geometry/l1.spdgeom.zlib"] = artwork
    row = context.project.metadata["spd_import"]["plane_geometries"][0]
    row["asset_sha256"] = sha256(artwork).hexdigest()

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_SOURCE_GEOMETRY_MISMATCH"


def test_python_inventory_row_cap_is_enforced_before_asset_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    monkeypatch.setattr(compiler, "_MAX_ROWS", 1)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_BOUND_EXCEEDED"


@pytest.mark.parametrize("phase", ("hash", "decompress"))
def test_geometry_attachment_hash_and_decompression_are_chunk_cancellable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    context = _context(tmp_path, monkeypatch)
    content = context.attachments["geometry/l1.spdgeom.zlib"]
    monkeypatch.setattr(compiler, "_STREAM_BYTES", 1)
    target = 2 if phase == "hash" else len(content) + 1
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls == target

    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        compiler._decode_geometry_attachment(
            content,
            sha256(content).hexdigest(),
            cancelled,
        )
    assert caught.value.code == "RAW_SPATIAL_CANCELLED"


def test_original_source_is_rehashed_after_asset_builder_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    initial_stat = context.path.stat()
    poisoned = context.source.replace(b"Node2 X = 1mm", b"Node2 X = 9mm")
    real_builder = compiler.build_raw_spatial_contact_asset

    def mutate_during_builder(*args: object, **kwargs: object):
        result = real_builder(*args, **kwargs)
        context.path.write_bytes(poisoned)
        os.utime(
            context.path,
            ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns),
        )
        return result

    monkeypatch.setattr(compiler, "build_raw_spatial_contact_asset", mutate_during_builder)
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_SOURCE_MUTATED"


def test_compiled_topology_source_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    context.compiled["source_sha256"] = "e" * 64
    with pytest.raises(compiler.RawSpatialCompilerError) as caught:
        _compile(context)
    assert caught.value.code == "RAW_SPATIAL_COMPILED_TOPOLOGY_MISMATCH"


def test_cancellation_and_source_stat_mutation_are_hard_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path, monkeypatch)
    with pytest.raises(compiler.RawSpatialCompilerError) as cancelled:
        _compile(context, is_cancelled=lambda: True)
    assert cancelled.value.code == "RAW_SPATIAL_CANCELLED"

    real_source_state = compiler._source_state
    calls = 0

    def changed(path: Path) -> tuple[int, int, int, int]:
        nonlocal calls
        calls += 1
        state = real_source_state(path)
        return state if calls == 1 else (*state[:3], state[3] + 1)

    monkeypatch.setattr(compiler, "_source_state", changed)
    with pytest.raises(compiler.RawSpatialCompilerError) as mutated:
        _compile(context)
    assert mutated.value.code == "RAW_SPATIAL_SOURCE_MUTATED"
