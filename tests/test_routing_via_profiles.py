from __future__ import annotations

from types import SimpleNamespace

import pytest

from spd_decap_pi._core.io.shared_pad import SpdPadShape
from spd_decap_pi._core.io.spd import SpdPadStack
from spd_decap_pi.spd_adapter import _routing_via_profiles


def test_source_pad_shapes_and_barrel_assumption_form_layerwise_profile() -> None:
    project = SimpleNamespace(
        via_templates=(SimpleNamespace(template_id="VT1"),),
        metadata={
            "spd_via_template_provenance": {
                "VT1": {
                    "padstack": "DR1524",
                    "barrel_plating_assumption_um": 20.0,
                }
            }
        },
    )
    padstack = SpdPadStack(
        name="DR1524",
        drill_diameter_um=150.0,
        pad_width_um=350.0,
        pad_height_um=350.0,
        layers=("L15", "L24"),
        pad_shapes=(
            SpdPadShape("L15", "CIRCLE", 350.0, 350.0),
            SpdPadShape("L24", "CIRCLE", 100.0, 100.0),
        ),
    )

    profile = _routing_via_profiles(project, (padstack,))[0]

    assert profile.radius_for_layer("L15") == pytest.approx(175.0)
    assert profile.radius_for_layer("L24") == pytest.approx(50.0)
    assert profile.radius_for_layer("L20") == pytest.approx(95.0)
    assert "ANALYTICAL_BARREL" in profile.provenance


def test_missing_source_recipe_remains_explicitly_unresolved() -> None:
    project = SimpleNamespace(
        via_templates=(SimpleNamespace(template_id="VT_MISSING"),),
        metadata={"spd_via_template_provenance": {}},
    )

    profile = _routing_via_profiles(project, ())[0]

    assert profile.complete is False
    assert profile.radius_for_layer("L1") is None
    assert profile.unresolved_reason is not None
