from spd_decap_pi._core.domain import StackupLayer
from random import Random
from math import cos, pi, sin, sqrt

from spd_decap_pi._core.domain import PlanePairSuggestion
from spd_decap_pi._core.io.spd import SpdPlaneGeometry
from spd_decap_pi._core.plane_pairs import suggest_effective_plane_pairs
from spd_decap_pi.eligibility import (
    EligiblePlane,
    EligibilityResult,
    PlaneEligibilityIndex,
    eligible_power_planes,
    point_in_plane_geometry,
)


def _geometry(*, order=None):
    return SpdPlaneGeometry(
        layer="PWR1",
        net="VDD",
        positive_polygons_um=(
            ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
            ((40.0, 40.0), (60.0, 40.0), (60.0, 60.0), (40.0, 60.0)),
        ),
        negative_polygons_um=(
            ((25.0, 25.0), (75.0, 25.0), (75.0, 75.0), (25.0, 75.0)),
        ),
        primitive_order=order
        or (
            ("positive_polygon", 0),
            ("negative_polygon", 0),
            ("positive_polygon", 1),
        ),
    )


def _stackup():
    return (
        StackupLayer(
            name="TOP",
            thickness_um=35.0,
            conductivity_s_m=5.8e7,
            dk=4.0,
            df=0.02,
            pwr_nets=[],
        ),
        StackupLayer(name="D1", thickness_um=100.0, dk=4.0, df=0.02),
        StackupLayer(
            name="PWR1",
            thickness_um=35.0,
            conductivity_s_m=5.8e7,
            dk=4.0,
            df=0.02,
            pwr_nets=["VDD"],
        ),
        StackupLayer(name="D2", thickness_um=100.0, dk=4.0, df=0.02),
        StackupLayer(
            name="GND1",
            thickness_um=35.0,
            conductivity_s_m=5.8e7,
            dk=4.0,
            df=0.02,
            pwr_nets=["DGND"],
        ),
    )


def _positive_bounds(
    geometry: SpdPlaneGeometry,
) -> tuple[float, float, float, float] | None:
    bounds: list[tuple[float, float, float, float]] = []
    for polygon in geometry.positive_polygons_um:
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        bounds.append((min(xs), max(xs), min(ys), max(ys)))
    for center_x, center_y, radius in geometry.positive_circles_um:
        bounds.append(
            (
                center_x - radius,
                center_x + radius,
                center_y - radius,
                center_y + radius,
            )
        )
    if not bounds:
        return None
    return (
        min(item[0] for item in bounds),
        max(item[1] for item in bounds),
        min(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def _linear_eligibility(
    x_um: float,
    y_um: float,
    geometries: tuple[SpdPlaneGeometry, ...],
    stackup: tuple[StackupLayer, ...],
    *,
    tolerance_um: float = 1.0e-6,
) -> EligibilityResult:
    """Frozen pre-index semantics used as a differential-test oracle."""

    eligible: dict[tuple[str, str, str], EligiblePlane] = {}
    boundary: set[tuple[str, str, str]] = set()
    pair_net_names: dict[tuple[str, str, str], set[str]] = {}
    prepared: list[
        tuple[
            SpdPlaneGeometry,
            PlanePairSuggestion,
            tuple[str, str, str],
            tuple[float, float, float, float],
        ]
    ] = []
    for geometry in geometries:
        pair = next(
            (
                item
                for item in suggest_effective_plane_pairs(
                    stackup,
                    rail_net=geometry.net,
                    gnd_aliases=("DGND", "GND"),
                )
                if item.pwr_layer.casefold() == geometry.layer.casefold()
            ),
            None,
        )
        positive_bounds = _positive_bounds(geometry)
        if pair is None or positive_bounds is None:
            continue
        pair_key = (
            geometry.net.casefold(),
            pair.pwr_layer.casefold(),
            pair.gnd_layer.casefold(),
        )
        pair_net_names.setdefault(pair_key, set()).add(geometry.net)
        prepared.append((geometry, pair, pair_key, positive_bounds))

    for geometry, pair, pair_key, bounds in prepared:
        if pair_key in boundary:
            continue
        axis_padding = sqrt(2.0) * tolerance_um
        if not (
            bounds[0] - axis_padding <= x_um <= bounds[1] + axis_padding
            and bounds[2] - axis_padding <= y_um <= bounds[3] + axis_padding
        ):
            continue
        containment = point_in_plane_geometry(
            x_um,
            y_um,
            geometry,
            tolerance_um=tolerance_um,
        )
        if containment == "boundary":
            boundary.add(pair_key)
            eligible.pop(pair_key, None)
        elif containment == "inside" and pair_key not in eligible:
            eligible[pair_key] = EligiblePlane(
                net=geometry.net,
                pwr_layer=pair.pwr_layer,
                gnd_layer=pair.gnd_layer,
                separation_um=pair.separation_um,
            )

    ordered = tuple(
        sorted(
            eligible.values(),
            key=lambda item: (
                item.separation_um,
                item.net.casefold(),
                item.pwr_layer.casefold(),
            ),
        )
    )
    boundary_names = tuple(
        sorted(
            {
                net
                for key in boundary
                for net in pair_net_names.get(key, ())
            },
            key=str.casefold,
        )
    )
    return EligibilityResult(ordered, boundary_names)


def test_boolean_primitive_order_can_add_copper_back_after_void():
    geometry = _geometry()

    assert point_in_plane_geometry(10.0, 10.0, geometry) == "inside"
    assert point_in_plane_geometry(30.0, 30.0, geometry) == "outside"
    assert point_in_plane_geometry(50.0, 50.0, geometry) == "inside"


def test_primitive_boundary_is_fail_closed():
    geometry = _geometry()

    assert point_in_plane_geometry(25.0, 50.0, geometry) == "boundary"
    result = eligible_power_planes(25.0, 50.0, (geometry,), _stackup())

    assert result.nets == ()
    assert result.boundary_exclusions == ("VDD",)


def test_exact_under_pad_and_supported_plane_pair_are_both_required():
    geometry = _geometry()

    result = eligible_power_planes(10.0, 10.0, (geometry,), _stackup())
    blocked_by_void = eligible_power_planes(30.0, 30.0, (geometry,), _stackup())

    assert result.nets == ("VDD",)
    assert result.eligible[0].pwr_layer == "PWR1"
    assert result.eligible[0].gnd_layer == "GND1"
    assert blocked_by_void.nets == ()


def test_geometry_without_adjacent_solver_supported_ground_is_not_eligible():
    stack = list(_stackup())
    stack.insert(
        4,
        StackupLayer(
            name="SIG",
            thickness_um=35.0,
            conductivity_s_m=5.8e7,
            dk=4.0,
            df=0.02,
            pwr_nets=[],
        ),
    )

    assert eligible_power_planes(10.0, 10.0, (_geometry(),), stack).nets == ()


def test_same_net_boundary_on_other_layer_does_not_hide_valid_plane_pair():
    stack = (
        StackupLayer(
            name="A_PWR",
            thickness_um=10.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["VDD"],
        ),
        StackupLayer(name="A_D", thickness_um=20.0, dk=4.0),
        StackupLayer(
            name="A_GND",
            thickness_um=10.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["DGND"],
        ),
        StackupLayer(name="MID", thickness_um=1000.0, dk=4.0),
        StackupLayer(
            name="Z_PWR",
            thickness_um=10.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["VDD"],
        ),
        StackupLayer(name="Z_D", thickness_um=20.0, dk=4.0),
        StackupLayer(
            name="Z_GND",
            thickness_um=10.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["DGND"],
        ),
    )
    on_boundary = SpdPlaneGeometry(
        layer="A_PWR",
        net="VDD",
        positive_polygons_um=(
            ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
        ),
        negative_polygons_um=(),
    )
    valid_other_pair = SpdPlaneGeometry(
        layer="Z_PWR",
        net="VDD",
        positive_polygons_um=(
            ((-20.0, 40.0), (20.0, 40.0), (20.0, 60.0), (-20.0, 60.0)),
        ),
        negative_polygons_um=(),
    )

    result = eligible_power_planes(
        0.0, 50.0, (on_boundary, valid_other_pair), stack
    )

    assert [(item.net, item.pwr_layer, item.gnd_layer) for item in result.eligible] == [
        ("VDD", "Z_PWR", "Z_GND")
    ]
    assert result.boundary_exclusions == ("VDD",)


def test_same_pair_boundary_overrides_earlier_and_later_inside_geometry() -> None:
    inside = SpdPlaneGeometry(
        layer="PWR1",
        net="VDD",
        positive_polygons_um=(
            ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
        ),
        negative_polygons_um=(),
    )
    boundary = SpdPlaneGeometry(
        layer="PWR1",
        net="VDD",
        positive_polygons_um=(
            ((50.0, 25.0), (150.0, 25.0), (150.0, 125.0), (50.0, 125.0)),
        ),
        negative_polygons_um=(),
    )
    later_inside = SpdPlaneGeometry(
        layer="PWR1",
        net="VDD",
        positive_polygons_um=(),
        positive_circles_um=((50.0, 75.0, 20.0),),
        negative_polygons_um=(),
    )

    result = PlaneEligibilityIndex(
        (inside, boundary, later_inside), _stackup()
    ).query(50.0, 75.0)

    assert result.eligible == ()
    assert result.boundary_exclusions == ("VDD",)


def test_index_preserves_interleaved_circle_order_and_tolerance_boundary() -> None:
    geometry = SpdPlaneGeometry(
        layer="PWR1",
        net="VDD",
        positive_polygons_um=(),
        negative_polygons_um=(),
        positive_circles_um=((0.0, 0.0, 100.0), (0.0, 0.0, 10.0)),
        negative_circles_um=((0.0, 0.0, 60.0),),
        primitive_order=(
            ("positive_circle", 0),
            ("negative_circle", 0),
            ("positive_circle", 1),
        ),
    )
    index = PlaneEligibilityIndex((geometry,), _stackup(), tolerance_um=0.5)

    assert index.query(0.0, 0.0).nets == ("VDD",)
    assert index.query(30.0, 0.0).nets == ()
    assert index.query(60.25, 0.0).boundary_exclusions == ("VDD",)
    assert index.query(60.75, 0.0).boundary_exclusions == ()


def _fragmented_geometries() -> tuple[SpdPlaneGeometry, ...]:
    geometries: list[SpdPlaneGeometry] = []
    for geometry_index in range(64):
        column = geometry_index % 8
        row = geometry_index // 8
        x_min = -1_000.0 + column * 260.0
        y_min = -900.0 + row * 240.0
        negative_circles = tuple(
            (
                x_min + 25.0 + (index % 4) * 40.0,
                y_min + 25.0 + (index // 4) * 40.0,
                9.0,
            )
            for index in range(16)
        )
        positive_circles = tuple(
            (negative_circles[index][0], negative_circles[index][1], 3.0)
            for index in (0, 5, 10, 15)
        )
        order: list[tuple[str, int]] = [("positive_polygon", 0)]
        addback_index = 0
        for index in range(len(negative_circles)):
            order.append(("negative_circle", index))
            if index in (0, 5, 10, 15):
                order.append(("positive_circle", addback_index))
                addback_index += 1
        geometries.append(
            SpdPlaneGeometry(
                layer="PWR1",
                net="VDD",
                positive_polygons_um=(
                    (
                        (x_min, y_min),
                        (x_min + 180.0, y_min),
                        (x_min + 180.0, y_min + 180.0),
                        (x_min, y_min + 180.0),
                    ),
                ),
                negative_polygons_um=(),
                positive_circles_um=positive_circles,
                negative_circles_um=negative_circles,
                primitive_order=tuple(order),
            )
        )
    return tuple(geometries)


def test_spatial_index_matches_frozen_linear_semantics_on_fragmented_planes() -> None:
    geometries = _fragmented_geometries()
    stackup = _stackup()
    tolerance_um = 0.25
    index = PlaneEligibilityIndex(
        geometries,
        stackup,
        tolerance_um=tolerance_um,
    )
    random = Random(20260730)
    points = [
        (
            random.uniform(-1_050.0, 1_050.0),
            random.uniform(-950.0, 950.0),
        )
        for _index in range(240)
    ]
    points.extend(
        (
            geometry.positive_polygons_um[0][corner]
            for geometry in geometries[::7]
            for corner in range(4)
        )
    )

    for x_um, y_um in points:
        expected = _linear_eligibility(
            x_um,
            y_um,
            geometries,
            stackup,
            tolerance_um=tolerance_um,
        )
        assert index.query(x_um, y_um) == expected

    assert index._plane_grid is not None
    assert sum(1 for _index in index._plane_grid.candidates(-975.0, -875.0)) < len(
        geometries
    )


def test_broad_and_local_candidates_preserve_order_without_cell_replication() -> None:
    far_negative_circles = tuple(
        (
            20.0 + (index % 25) * 34.0,
            20.0 + (index // 25) * 34.0,
            2.0,
        )
        for index in range(510)
    )
    geometry = SpdPlaneGeometry(
        layer="PWR1",
        net="VDD",
        positive_polygons_um=(
            ((0.0, 0.0), (900.0, 0.0), (900.0, 900.0), (0.0, 900.0)),
            ((0.0, 0.0), (900.0, 0.0), (900.0, 900.0), (0.0, 900.0)),
        ),
        negative_polygons_um=(),
        negative_circles_um=(
            (450.0, 450.0, 20.0),
            *far_negative_circles,
        ),
        primitive_order=(
            ("positive_polygon", 0),
            ("negative_circle", 0),
            ("positive_polygon", 1),
            *(
                ("negative_circle", index)
                for index in range(1, len(far_negative_circles) + 1)
            ),
        ),
    )
    index = PlaneEligibilityIndex((geometry,), _stackup())
    grid = index._planes[0].primitive_grid

    assert len(grid.broad_indices) == 2
    assert all(
        broad not in local
        for local in grid.cells.values()
        for broad in grid.broad_indices
    )
    assert grid.stored_reference_count == (
        len(grid.broad_indices) + sum(len(items) for items in grid.cells.values())
    )
    assert index.query(450.0, 450.0) == _linear_eligibility(
        450.0,
        450.0,
        (geometry,),
        _stackup(),
    )


def test_large_polygon_scanline_index_matches_linear_edges_and_prunes_work() -> None:
    polygon = tuple(
        (
            500.0 * cos(2.0 * pi * index / 512),
            500.0 * sin(2.0 * pi * index / 512),
        )
        for index in range(512)
    )
    geometry = SpdPlaneGeometry(
        layer="PWR1",
        net="VDD",
        positive_polygons_um=(polygon,),
        negative_polygons_um=(),
    )
    index = PlaneEligibilityIndex((geometry,), _stackup(), tolerance_um=0.01)
    indexed_polygon = index._planes[0].primitives[0].polygon_y_index

    assert indexed_polygon is not None
    assert sum(1 for _edge in indexed_polygon.candidates(0.0)) < len(polygon) // 8

    random = Random(48)
    points = [
        (random.uniform(-550.0, 550.0), random.uniform(-550.0, 550.0))
        for _index in range(200)
    ]
    points.extend((polygon[0], polygon[128], polygon[256], polygon[384]))
    for x_um, y_um in points:
        assert index.query(x_um, y_um) == _linear_eligibility(
            x_um,
            y_um,
            (geometry,),
            _stackup(),
            tolerance_um=0.01,
        )


def test_diagonal_negative_endpoint_tolerance_is_fail_closed() -> None:
    tolerance_um = 10.0
    query = (100.0, 100.0 + 0.9 * sqrt(2.0) * tolerance_um)
    geometry = SpdPlaneGeometry(
        layer="PWR1",
        net="VDD",
        positive_polygons_um=(
            ((-500.0, -500.0), (500.0, -500.0), (500.0, 500.0), (-500.0, 500.0)),
        ),
        negative_polygons_um=(
            ((0.0, 0.0), (100.0, 100.0), (300.0, 0.0)),
        ),
        primitive_order=(
            ("positive_polygon", 0),
            ("negative_polygon", 0),
        ),
    )

    assert point_in_plane_geometry(
        *query,
        geometry,
        tolerance_um=tolerance_um,
    ) == "boundary"
    assert PlaneEligibilityIndex(
        (geometry,),
        _stackup(),
        tolerance_um=tolerance_um,
    ).query(*query) == EligibilityResult((), ("VDD",))


def test_large_polygon_diagonal_endpoint_matches_exact_boundary() -> None:
    tolerance_um = 10.0
    query = (100.0, 100.0 + 0.9 * sqrt(2.0) * tolerance_um)

    def segment_points(
        start: tuple[float, float],
        end: tuple[float, float],
        count: int,
    ) -> list[tuple[float, float]]:
        return [
            (
                start[0] + (end[0] - start[0]) * step / count,
                start[1] + (end[1] - start[1]) * step / count,
            )
            for step in range(1, count + 1)
        ]

    polygon = tuple(
        [(0.0, 0.0), (100.0, 100.0)]
        + segment_points((100.0, 100.0), (300.0, 100.0), 512)
        + segment_points((300.0, 100.0), (300.0, 300.0), 512)
        + segment_points((300.0, 300.0), (-100.0, 300.0), 512)
        + segment_points((-100.0, 300.0), (-100.0, -100.0), 512)
        + segment_points((-100.0, -100.0), (0.0, 0.0), 510)[:-1]
    )
    geometry = SpdPlaneGeometry(
        layer="PWR1",
        net="VDD",
        positive_polygons_um=(polygon,),
        negative_polygons_um=(),
    )
    index = PlaneEligibilityIndex(
        (geometry,),
        _stackup(),
        tolerance_um=tolerance_um,
    )

    assert len(polygon) >= 64
    assert point_in_plane_geometry(
        *query,
        geometry,
        tolerance_um=tolerance_um,
    ) == "boundary"
    assert index.query(*query) == _linear_eligibility(
        *query,
        (geometry,),
        _stackup(),
        tolerance_um=tolerance_um,
    ) == EligibilityResult((), ("VDD",))
