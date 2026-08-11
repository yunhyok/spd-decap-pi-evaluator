from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pytest
from shapely.geometry import Point, box

from spd_decap_pi._core.io.conductor_graph import (
    ConductorComponent,
    ConductorNode,
    SpdConductorGraph,
    TopologyCertificate,
)
from spd_decap_pi._core.solver.multilayer_capacitance import (
    EPSILON_0_F_PER_M,
    CapacitanceArtwork,
    DielectricGap,
    MultilayerCapacitanceError,
    MultilayerCapacitanceModel,
    extract_multilayer_bulk_capacitance,
)
from spd_decap_pi._core.io.conductor_graph import ConductorPadDef
from spd_decap_pi._core.solver.pad_augmented_capacitance import (
    PadAugmentedCapacitanceError,
    PadPlacementEvidence,
    augment_multilayer_capacitance_with_pads,
    extract_pad_augmented_bulk_capacitance,
    pad_placements_from_conductor_graph,
    stream_spd_reference_regular_pads,
)


GAP = DielectricGap(100.0e-6, 3.4)
COEFFICIENT_F_PER_UM2 = EPSILON_0_F_PER_M * 3.4 / GAP.separation_m * 1.0e-12


def _circle_pad(layer: str = "TOP", diameter_um: float = 200.0) -> ConductorPadDef:
    return ConductorPadDef(layer, "CIRCLE", diameter_um, diameter_um, f"Regular Circle {diameter_um / 2}um")


def _placement(
    *,
    net: str = "P",
    x_um: float = 500.0,
    y_um: float = 500.0,
    layer: str = "TOP",
    pad: ConductorPadDef | None = None,
    source_id: str = "Via:V1:TOP",
) -> PadPlacementEvidence:
    return PadPlacementEvidence(
        source_id,
        layer,
        net,
        "component-0",
        x_um,
        y_um,
        "PS1",
        pad or _circle_pad(layer),
        None,
        "source Regular PadDef",
    )


def _model_with_top(*top: CapacitanceArtwork) -> MultilayerCapacitanceModel:
    return MultilayerCapacitanceModel(
        ("TOP", "BOT"),
        (GAP,),
        top + (CapacitanceArtwork("BOT", "DGND", box(0, 0, 1000, 1000)),),
    )


def test_same_net_pad_unions_into_shape_hole_and_adds_exact_bulk_area() -> None:
    pad = _placement()
    pad_shape = Point(500, 500).buffer(100, quad_segs=128)
    plane = box(0, 0, 1000, 1000).difference(pad_shape)
    model = _model_with_top(CapacitanceArtwork("TOP", "P", plane))
    before = extract_multilayer_bulk_capacitance(model)
    result = extract_pad_augmented_bulk_capacitance(model, (pad,))

    before_pair = before.pair_capacitance_f[("DGND", "P")]
    after_pair = result.capacitance.pair_capacitance_f[("DGND", "P")]
    assert after_pair - before_pair == pytest.approx(pad_shape.area * COEFFICIENT_F_PER_UM2, rel=2e-12)
    area = result.diagnostics.area_by_layer_net[0]
    assert (area.layer, area.net, area.pad_count) == ("TOP", "P", 1)
    assert area.added_union_area_um2 == pytest.approx(pad_shape.area)


def test_other_net_pad_in_opening_remains_a_separate_maxwell_conductor() -> None:
    opening = Point(500, 500).buffer(100, quad_segs=128)
    model = _model_with_top(
        CapacitanceArtwork("TOP", "P", box(0, 0, 1000, 1000).difference(opening))
    )
    before = extract_multilayer_bulk_capacitance(model)
    result = extract_pad_augmented_bulk_capacitance(
        model,
        (_placement(net="OTHER", pad=_circle_pad(diameter_um=100.0)),),
    ).capacitance

    assert set(result.net_names) == {"DGND", "P", "OTHER"}
    assert result.pair_capacitance_f[("DGND", "P")] == pytest.approx(
        before.pair_capacitance_f[("DGND", "P")]
    )
    assert result.pair_capacitance_f[("DGND", "OTHER")] > 0.0
    np.testing.assert_allclose(result.maxwell_capacitance_f, result.maxwell_capacitance_f.T)
    np.testing.assert_allclose(result.maxwell_capacitance_f.sum(axis=1), 0.0, atol=1e-22)
    assert np.linalg.eigvalsh(result.maxwell_capacitance_f).min() >= -1e-22


def test_duplicate_node_via_endpoint_pad_is_counted_once() -> None:
    first = _placement(source_id="Node:N1")
    duplicate = _placement(source_id="Via:V1:TOP")
    augmented = augment_multilayer_capacitance_with_pads(
        _model_with_top(CapacitanceArtwork("TOP", "P", box(0, 0, 100, 100))),
        (first, duplicate),
    )
    diagnostics = augmented.diagnostics
    assert diagnostics.source_placement_count == 2
    assert diagnostics.unique_pad_count == 1
    assert diagnostics.duplicate_pad_count == 1
    assert diagnostics.area_by_layer_net[0].pad_count == 1


def test_unsupported_missing_and_unrotated_pad_evidence_fail_closed() -> None:
    with pytest.raises(PadAugmentedCapacitanceError, match="net must not be empty"):
        _placement(net=" ")
    with pytest.raises(PadAugmentedCapacitanceError, match="unsupported or dimensionless"):
        _placement(pad=ConductorPadDef("TOP", "UNSUPPORTED", None, None, "Regular Octagon"))
    with pytest.raises(PadAugmentedCapacitanceError, match="no source rotation"):
        _placement(pad=ConductorPadDef("TOP", "RECTANGLE", 100.0, 50.0, "Regular Box"))
    with pytest.raises(PadAugmentedCapacitanceError, match="outside the requested slab"):
        augment_multilayer_capacitance_with_pads(
            _model_with_top(CapacitanceArtwork("TOP", "P", box(0, 0, 100, 100))),
            (_placement(layer="UNKNOWN", pad=_circle_pad("UNKNOWN")),),
        )


def test_missing_referenced_node_padstack_fails_closed() -> None:
    node = ConductorNode("N1", "P", 0.0, 0.0, "TOP", "MISSING")
    graph = SpdConductorGraph(
        source_path=__file__,
        requested_nets=("P",),
        site=None,
        selected_layers=("TOP",),
        layers=(),
        nodes=(node,),
        vias=(),
        traces=(),
        padstacks=(),
        topology_adjacency=MappingProxyType({node.key: ()}),
        topology_components=(ConductorComponent("component-0", "P", (node.key,), (), ()),),
        electrical_adjacency=MappingProxyType({}),
        electrical_components=(),
        polygon_connectivity_included=False,
        diagnostics=(),
        statistics=MappingProxyType({}),
        certificate=TopologyCertificate(
            "test", "0" * 64, "1" * 64, 0, (), MappingProxyType({}),
        ),
    )
    with pytest.raises(PadAugmentedCapacitanceError, match="references missing PadStack"):
        pad_placements_from_conductor_graph(graph, requested_layers=("TOP",))


def test_circle_32_64_128_refinement_converges_and_matrix_is_passive() -> None:
    model = _model_with_top(CapacitanceArtwork("TOP", "P", box(0, 0, 100, 100)))
    result = extract_pad_augmented_bulk_capacitance(model, (_placement(x_um=500, y_um=500),))
    diagnostics = result.diagnostics
    assert diagnostics.circle_quad_segs == (32, 64, 128)
    assert len(diagnostics.circle_relative_area_errors) == 3
    assert len(diagnostics.circle_relative_refinement_deltas) == 2
    assert all(
        diagnostics.circle_relative_area_errors[index + 1]
        < diagnostics.circle_relative_area_errors[index]
        for index in range(2)
    )
    assert diagnostics.maximum_circle_area_error < 3.0e-5
    matrix = result.capacitance.maxwell_capacitance_f
    np.testing.assert_allclose(matrix, matrix.T)
    np.testing.assert_allclose(matrix.sum(axis=1), 0.0, atol=1e-22)
    assert np.linalg.eigvalsh(matrix).min() >= -1e-22


def test_other_net_pad_overlapping_plane_does_not_silently_fill_or_short() -> None:
    model = _model_with_top(CapacitanceArtwork("TOP", "P", box(0, 0, 1000, 1000)))
    with pytest.raises(MultilayerCapacitanceError, match="ambiguous/shorted artwork"):
        extract_pad_augmented_bulk_capacitance(model, (_placement(net="OTHER"),))


def _streaming_spd(padstack_name: str = "PS1") -> str:
    return "\n".join((
        "* Node description lines",
        "Node1::DGND X = 0.5mm Y = 0.5mm Layer = TOP",
        "Node2::DGND X = 0.5mm Y = 0.5mm Layer = BOT",
        "* Via description lines",
        f"Via1::DGND UpperNode = Node1::DGND LowerNode = Node2::DGND PadStack = {padstack_name}",
        "* PadStack collection description lines",
        ".PadStackDef PS1 0.025mm",
        ".PadDef TOP",
        "Regular Circle 0.05mm",
        ".EndPadDef",
        ".PadDef BOT",
        "Regular Circle 0.05mm",
        ".EndPadDef",
        ".EndPadStackDef",
        "",
    ))


def test_streaming_reference_pad_keeps_only_hole_local_endpoint(tmp_path) -> None:
    source = tmp_path / "small.spd"
    source.write_text(_streaming_spd(), encoding="utf-8")
    opening = Point(500, 500).buffer(100, quad_segs=128)
    model = _model_with_top(
        CapacitanceArtwork("TOP", "P", box(0, 0, 1000, 1000).difference(opening))
    )
    streamed = stream_spd_reference_regular_pads(source, (model,))

    assert len(streamed) == 1
    assert len(streamed.added_artwork) == 1
    assert streamed[0].net == "DGND"
    assert streamed[0].layer == "TOP"
    diagnostics = streamed.diagnostics
    assert diagnostics.source_line_passes == 1
    assert diagnostics.scanned_reference_node_count == 2
    assert diagnostics.bbox_retained_node_count == 1
    assert diagnostics.raw_candidate_count == 1
    assert diagnostics.unique_candidate_count == 1
    assert diagnostics.outside_hole_neighborhood_count == 0
    assert diagnostics.unique_hole_pad_count == 1
    assert diagnostics.emitted_non_idempotent_pad_count == 1
    assert diagnostics.added_union_area_um2 == pytest.approx(
        Point(500, 500).buffer(50, quad_segs=128).area
    )


def test_streaming_reference_pad_missing_padstack_and_memory_gate_fail(tmp_path) -> None:
    source = tmp_path / "missing.spd"
    source.write_text(_streaming_spd("MISSING"), encoding="utf-8")
    opening = Point(500, 500).buffer(100, quad_segs=128)
    model = _model_with_top(
        CapacitanceArtwork("TOP", "P", box(0, 0, 1000, 1000).difference(opening))
    )
    with pytest.raises(PadAugmentedCapacitanceError, match="missing PadStack definitions"):
        stream_spd_reference_regular_pads(source, (model,))

    valid = tmp_path / "valid.spd"
    valid.write_text(_streaming_spd(), encoding="utf-8")
    two_hole_model = MultilayerCapacitanceModel(
        ("TOP", "BOT"),
        (GAP,),
        (
            CapacitanceArtwork("TOP", "P", box(0, 0, 1000, 1000).difference(opening)),
            CapacitanceArtwork("BOT", "DGND", box(0, 0, 1000, 1000).difference(opening)),
        ),
    )
    with pytest.raises(PadAugmentedCapacitanceError, match="Node gate exceeded"):
        stream_spd_reference_regular_pads(valid, (two_hole_model,), max_retained_nodes=1)
    with pytest.raises(PadAugmentedCapacitanceError, match="runtime gate exceeded"):
        stream_spd_reference_regular_pads(
            valid,
            (model,),
            max_runtime_seconds=1.0e-12,
        )
