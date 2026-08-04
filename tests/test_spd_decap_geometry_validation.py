from __future__ import annotations

from copy import deepcopy

import pytest

from spd_decap_pi._core import services


def _payload() -> dict[str, object]:
    return {
        "format": "powersi-spd-plane-primitives-v1",
        "layer": "PWR1",
        "net": "VDD",
        "positive_polygons_um": [
            [[0.0, 0.0], [100.0, 0.0], [0.0, 100.0]],
        ],
        "negative_polygons_um": [],
        "positive_circles_um": [],
        "negative_circles_um": [],
        "primitive_order": [["positive_polygon", 0]],
    }


def test_spd_geometry_validation_accepts_finite_numeric_coordinates() -> None:
    services._validate_spd_geometry_payload(
        _payload(), expected_layer="pwr1", expected_net="vdd"
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("positive_polygons_um", True, "Polygon vertices must be finite"),
        ("positive_polygons_um", float("inf"), "Polygon vertices must be finite"),
        ("positive_circles_um", True, "positive radius"),
        ("positive_circles_um", float("nan"), "positive radius"),
    ),
)
def test_spd_geometry_validation_rejects_boolean_and_nonfinite_coordinates(
    field: str, value: object, message: str
) -> None:
    payload = deepcopy(_payload())
    if field == "positive_polygons_um":
        payload[field][0][0][0] = value
    else:
        payload[field] = [[0.0, 0.0, value]]
        payload["primitive_order"] = [
            ["positive_polygon", 0],
            ["positive_circle", 0],
        ]

    with pytest.raises(ValueError, match=message):
        services._validate_spd_geometry_payload(payload)
