from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import math

import pytest
from shapely.geometry import Polygon, box

import spd_decap_pi._core.geometry.source_trace as source_trace
from spd_decap_pi._core.geometry.source_trace import (
    SOURCE_TRACE_GEOMETRY_POLICY,
    SourceArtworkIsland,
    SourceTraceGeometryError,
    SourceTraceSegment,
    compile_source_trace_geometry,
)


def _segment(
    source_id: str,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    width_pm: int | None = 2,
    net: str = "VCPU",
    layer: str = "Signal$PWR",
) -> SourceTraceSegment:
    record = (
        f"{source_id}::{net} StartingNode = NodeA EndingNode = NodeB"
    ).encode("ascii")
    return SourceTraceSegment(
        source_id=source_id,
        net=net,
        layer=layer,
        starting_node_id=f"{source_id}Start",
        ending_node_id=f"{source_id}End",
        start_x_pm=start[0],
        start_y_pm=start[1],
        end_x_pm=end[0],
        end_y_pm=end[1],
        width_pm=width_pm,
        source_record_sha256=sha256(record).hexdigest(),
        source_record_size_bytes=len(record),
    )


def _artwork(
    island_id: str,
    geometry: object,
    *,
    net: str = "VCPU",
    layer: str = "Signal$PWR",
) -> SourceArtworkIsland:
    return SourceArtworkIsland.from_geometry(
        island_id,
        net,
        layer,
        geometry,
    )


def test_flat_policy_rectangle_and_widthless_disclosure_are_exact() -> None:
    resolved = _segment("TraceResolved", (0, 0), (10, 0), width_pm=4)
    widthless = _segment("TraceWidthless", (10, 0), (20, 0), width_pm=None)

    assert resolved.geometry_policy == SOURCE_TRACE_GEOMETRY_POLICY
    assert resolved.rectangle_coordinates_pm() == (
        (0.0, 2.0),
        (10.0, 2.0),
        (10.0, -2.0),
        (0.0, -2.0),
    )
    assert resolved.geometry().area == pytest.approx(40.0)
    assert widthless.rectangle_coordinates_pm() is None
    assert widthless.geometry() is None

    compiled = compile_source_trace_geometry((resolved, widthless,))[0]

    assert compiled.geometry_policy == SOURCE_TRACE_GEOMETRY_POLICY
    assert compiled.geometry is not None
    assert compiled.trace_identity_sha256s == (resolved.identity_sha256,)
    assert compiled.topology_only_trace_identity_sha256s == (
        widthless.identity_sha256,
    )


def test_branch_union_is_deterministic_and_keeps_flat_caps() -> None:
    horizontal = _segment("TraceHorizontal", (-10, 0), (10, 0), width_pm=2)
    vertical = _segment("TraceVertical", (0, 0), (0, 10), width_pm=2)

    first = compile_source_trace_geometry((horizontal, vertical))[0]
    second = compile_source_trace_geometry((vertical, horizontal))[0]

    assert first == second
    assert first.geometry.geom_type == "Polygon"
    assert first.geometry.area == pytest.approx(58.0)
    assert first.geometry.bounds == pytest.approx((-10.0, -1.0, 10.0, 10.0))


@pytest.mark.parametrize("second_start", (10, 8))
def test_same_net_line_or_positive_area_overlap_unions(second_start: int) -> None:
    first = _segment("TraceA", (0, 0), (10, 0))
    second = _segment("TraceB", (second_start, 0), (20, 0))

    compiled = compile_source_trace_geometry((first, second))[0]

    assert compiled.geometry.geom_type == "Polygon"
    assert compiled.geometry.area == pytest.approx(40.0)


def test_same_net_point_only_contact_fails_closed() -> None:
    horizontal = _segment("TraceA", (0, 0), (10, 0))
    vertical = _segment("TraceB", (11, 1), (11, 10))

    with pytest.raises(SourceTraceGeometryError) as exc:
        compile_source_trace_geometry((horizontal, vertical))

    assert exc.value.code == "POINT_ONLY_GEOMETRY_CONTACT"


def test_different_net_overlap_fails_closed() -> None:
    horizontal = _segment("TraceA", (-10, 0), (10, 0), net="VCPU")
    vertical = _segment("TraceB", (0, -10), (0, 10), net="VGFX")

    with pytest.raises(SourceTraceGeometryError) as exc:
        compile_source_trace_geometry((horizontal, vertical))

    assert exc.value.code == "CROSS_NET_GEOMETRY_CONTACT"


def test_same_net_artwork_line_contact_connects_without_inference() -> None:
    island = _artwork("IslandA", box(0, -5, 10, 5))
    trace = _segment("TraceStrip", (10, 0), (20, 0))

    compiled = compile_source_trace_geometry(
        (trace,),
        artwork_islands=(island,),
    )[0]

    assert compiled.geometry.geom_type == "Polygon"
    assert compiled.artwork_identity_sha256s == (island.identity_sha256,)


def test_same_net_trace_copper_may_cross_and_fill_artwork_hole() -> None:
    holed = Polygon(
        ((0, 0), (20, 0), (20, 20), (0, 20)),
        holes=(((8, 8), (12, 8), (12, 12), (8, 12)),),
    )
    island = _artwork("HoledIsland", holed)
    trace = _segment("TraceHoleCrossing", (10, 10), (18, 10))

    compiled = compile_source_trace_geometry((trace,), artwork_islands=(island,))[0]

    assert compiled.geometry.geom_type == "Polygon"
    assert compiled.geometry.area > holed.area


def test_cross_net_artwork_contact_fails_closed() -> None:
    island = _artwork("VgfxIsland", box(0, -5, 10, 5), net="VGFX")
    trace = _segment("VcpuTrace", (10, 0), (20, 0), net="VCPU")

    with pytest.raises(SourceTraceGeometryError) as exc:
        compile_source_trace_geometry((trace,), artwork_islands=(island,))

    assert exc.value.code == "CROSS_NET_GEOMETRY_CONTACT"


def test_vcpu_manufactured_islands_are_connected_only_by_source_strips() -> None:
    islands = (
        _artwork("VCPU_A", box(0, -5, 10, 5)),
        _artwork("VCPU_B", box(20, -5, 30, 5)),
        _artwork("VCPU_C", box(40, -5, 50, 5)),
    )
    strips = (
        _segment("TraceVcpuAB", (10, 0), (20, 0)),
        _segment("TraceVcpuBC", (30, 0), (40, 0)),
    )

    compiled = compile_source_trace_geometry(
        strips,
        artwork_islands=islands,
    )[0]

    assert compiled.geometry.geom_type == "Polygon"
    assert compiled.geometry.bounds == pytest.approx((0.0, -5.0, 50.0, 5.0))
    assert compiled.geometry.area == pytest.approx(340.0)


@pytest.mark.parametrize(
    "bad_coordinate",
    (-0.0, 1.5, complex(1, 0), math.nan, math.inf),
)
def test_coordinate_scalars_reject_signed_zero_fractional_complex_and_nonfinite(
    bad_coordinate: object,
) -> None:
    valid = _segment("TraceA", (0, 0), (10, 0))

    with pytest.raises(ValueError, match="exact integer"):
        replace(valid, start_x_pm=bad_coordinate)


@pytest.mark.parametrize("bad_width", (-0.0, 1.5, complex(1, 0), math.nan, 0, -1))
def test_width_scalar_must_be_exact_positive_integer_pm(bad_width: object) -> None:
    valid = _segment("TraceA", (0, 0), (10, 0))

    with pytest.raises(ValueError):
        replace(valid, width_pm=bad_width)


def test_source_record_and_compiled_identity_tamper_fail_closed() -> None:
    segment = _segment("TraceA", (0, 0), (10, 0))
    record = b"TraceA::VCPU StartingNode = NodeA EndingNode = NodeB"
    segment.verify_source_record(record)
    with pytest.raises(ValueError, match="digest changed"):
        segment.verify_source_record(record[:-1] + b"C")
    with pytest.raises(ValueError, match="cardinality changed"):
        segment.verify_source_record(record + b" ")

    compiled = compile_source_trace_geometry((segment,))[0]
    with pytest.raises(ValueError, match="identity was tampered"):
        replace(compiled, net="TAMPERED")


def test_disconnected_same_net_layer_components_are_preserved() -> None:
    first = _segment("TraceA", (0, 0), (10, 0))
    second = _segment("TraceB", (20, 0), (30, 0))

    compiled = compile_source_trace_geometry((first, second))[0]

    assert compiled.geometry.geom_type == "MultiPolygon"
    assert len(compiled.geometry.geoms) == 2


def test_extreme_integer_coordinates_fail_closed_without_raw_overflow() -> None:
    segment = _segment("TraceHuge", (10**400, 0), (10**400 + 1, 0))

    with pytest.raises(SourceTraceGeometryError) as exc:
        segment.rectangle_coordinates_pm()

    assert exc.value.code == "TRACE_RECTANGLE_NONFINITE"


def test_compiled_geometry_rejects_ownerless_forged_physical_geometry() -> None:
    compiled = compile_source_trace_geometry((_segment("TraceA", (0, 0), (10, 0)),))[0]

    with pytest.raises(ValueError, match="no physical source owner"):
        replace(
            compiled,
            trace_identity_sha256s=(),
            identity_sha256=sha256(
                b"deliberately-not-the-matching-canonical-identity"
            ).hexdigest(),
        )


def test_spatial_contact_work_bound_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_trace, "MAX_SOURCE_GEOMETRY_CONTACT_CHECKS", 0)
    first = _segment("TraceA", (0, 0), (10, 0))
    second = _segment("TraceB", (8, 0), (20, 0))

    with pytest.raises(SourceTraceGeometryError) as exc:
        compile_source_trace_geometry((first, second))

    assert exc.value.code == "SOURCE_GEOMETRY_CONTACT_BOUND_EXCEEDED"


def test_geometry_coordinate_work_bound_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_trace, "MAX_SOURCE_GEOMETRY_COORDINATES", 4)
    island = _artwork("IslandA", box(0, 0, 10, 10))

    with pytest.raises(SourceTraceGeometryError) as exc:
        compile_source_trace_geometry((), artwork_islands=(island,))

    assert exc.value.code == "SOURCE_GEOMETRY_COORDINATE_BOUND_EXCEEDED"
