from __future__ import annotations

from math import pi

import pytest

from spd_decap_pi._core.domain import StackupLayer
from spd_decap_pi._core.via_model import (
    COPPER_CONDUCTIVITY_S_PER_M,
    HOLLOW_PLATED_BARREL,
    SOLID_COPPER_FILLED_MICROVIA,
    ViaModelError,
    estimate_via_segment_rl,
)


def _stackup() -> list[StackupLayer]:
    return [
        StackupLayer(name="TOP", thickness_um=20.0, conductivity_s_m=5.959e7),
        StackupLayer(name="D1", thickness_um=60.0, dk=3.4),
        StackupLayer(name="L02", thickness_um=20.0, conductivity_s_m=5.959e7),
        StackupLayer(name="D2", thickness_um=80.0, dk=3.4),
        StackupLayer(name="L04", thickness_um=20.0, conductivity_s_m=5.959e7),
    ]


def test_qualified_copper_microvia_uses_full_circular_area() -> None:
    model = estimate_via_segment_rl(
        length_um=60.0,
        drill_diameter_um=100.0,
        padstack_material="COPPER",
        start_layer="TOP",
        end_layer="L02",
        stackup_layers=_stackup(),
    )

    assert model.classification.conductor_model == SOLID_COPPER_FILLED_MICROVIA
    assert (
        model.classification.fill_provenance
        == "USER_CONFIRMED_MLO_COPPER_FILL_ASSUMPTION_WITH_SOURCE_COPPER_AND_GEOMETRY"
    )
    expected_area = pi * (50.0e-6) ** 2
    assert model.classification.effective_area_m2 == pytest.approx(expected_area)
    assert model.resistance_ohm == pytest.approx(
        60.0e-6 / (COPPER_CONDUCTIVITY_S_PER_M * expected_area)
    )


@pytest.mark.parametrize(
    ("drill_um", "end_layer", "material"),
    [
        # DR-2128_350 has a 150 um drill, but its nonadjacent/core span is
        # not the one-dielectric MLO microvia profile.
        (150.0, "L04", "COPPER"),
        (100.0, "L02", None),  # Old bundles have no source material.
    ],
)
def test_nonqualified_or_missing_evidence_retains_plated_barrel(
    drill_um: float, end_layer: str, material: str | None
) -> None:
    model = estimate_via_segment_rl(
        length_um=60.0,
        drill_diameter_um=drill_um,
        padstack_material=material,
        start_layer="TOP",
        end_layer=end_layer,
        stackup_layers=_stackup(),
    )

    assert model.classification.conductor_model == HOLLOW_PLATED_BARREL
    expected_area = pi * drill_um * min(20.0, drill_um / 4.0) * 1.0e-12
    assert model.classification.effective_area_m2 == pytest.approx(expected_area)
    assert model.resistance_ohm == pytest.approx(
        60.0e-6 / (COPPER_CONDUCTIVITY_S_PER_M * expected_area)
    )


def test_legacy_40um_barrel_resistance_is_unchanged() -> None:
    model = estimate_via_segment_rl(
        length_um=100.0,
        drill_diameter_um=40.0,
        padstack_material=None,
        start_layer="TOP",
        end_layer="L02",
        stackup_layers=_stackup(),
    )
    expected_area = pi * 40.0 * min(20.0, 40.0 / 4.0) * 1.0e-12
    assert model.resistance_ohm == pytest.approx(
        100.0e-6 / (COPPER_CONDUCTIVITY_S_PER_M * expected_area)
    )


@pytest.mark.parametrize("length_um,drill_um", [(0.0, 40.0), (60.0, 0.0), (float("nan"), 40.0)])
def test_nonphysical_via_dimensions_fail(length_um: float, drill_um: float) -> None:
    with pytest.raises(ViaModelError, match="finite and > 0"):
        estimate_via_segment_rl(
            length_um=length_um,
            drill_diameter_um=drill_um,
            padstack_material="COPPER",
            start_layer="TOP",
            end_layer="L02",
            stackup_layers=_stackup(),
        )
