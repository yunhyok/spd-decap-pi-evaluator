from spd_decap_pi._core.domain import StackupLayer
from spd_decap_pi._core.io.spd import SpdPlaneGeometry
from spd_decap_pi.eligibility import (
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
