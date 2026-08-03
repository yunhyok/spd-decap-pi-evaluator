from __future__ import annotations

from types import SimpleNamespace

import pytest

from spd_decap_pi._core.domain import MLOOutline, ProjectSpec, RailSpec, StackupLayer
from spd_decap_pi._core.io.spd import SpdPadStack, SpdViaUsage
from spd_decap_pi._core.services import _spd_via_templates


def _project() -> ProjectSpec:
    return ProjectSpec(
        name="legacy-via-template-test",
        outline=MLOOutline(width_um=1_000.0, height_um=1_000.0),
        gnd_aliases=["DGND"],
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(name="TOP", thickness_um=20.0, conductivity_s_m=5.959e7),
            StackupLayer(name="D01", thickness_um=30.0, dk=3.4),
            StackupLayer(name="L01", thickness_um=20.0, conductivity_s_m=5.959e7, pwr_nets=["DGND"]),
            StackupLayer(name="D02", thickness_um=30.0, dk=3.4),
            StackupLayer(name="L02", thickness_um=20.0, conductivity_s_m=5.959e7, pwr_nets=["VDD"]),
        ],
        rails=[RailSpec(
            rail_id="VDD", family="VDD", domain="VDD", net="VDD", site="SITE0",
            pwr_layer="L02", gnd_layer="L01",
        )],
    )


def _analysis(*, reverse: bool = False) -> SimpleNamespace:
    padstacks = [
        SpdPadStack("P_DEEP", 60.0, 100.0, 100.0, ("TOP", "L01", "L02"), material="COPPER"),
        SpdPadStack("G_NEAR", 80.0, 120.0, 120.0, ("TOP", "L01"), material="COPPER"),
    ]
    usages = [SpdViaUsage("VDD", "P_DEEP", 10), SpdViaUsage("DGND", "G_NEAR", 20)]
    if reverse:
        padstacks.reverse()
        usages.reverse()
    return SimpleNamespace(padstacks=tuple(padstacks), via_usage=tuple(usages))


def test_branched_or_aggregate_source_usage_keeps_v015_scalar_template() -> None:
    templates, provenance = _spd_via_templates(_project(), _analysis())

    template = templates[0]
    details = provenance[template.template_id]
    assert not template.impedance
    assert template.finite_port_width_um == pytest.approx(120.0)
    assert template.loop_resistance_ohm > 0.0
    assert template.loop_inductance_h > 0.0
    assert details["padstack"] == "P_DEEP"
    assert details["ground_padstack"] == "G_NEAR"
    assert details["calibration"] == "uncalibrated analytical estimate; no PowerSI fit applied"
    assert "adjacent_source_coverage" not in details


def test_legacy_template_selection_is_deterministic_under_source_ordering() -> None:
    first_templates, first_provenance = _spd_via_templates(_project(), _analysis())
    second_templates, second_provenance = _spd_via_templates(_project(), _analysis(reverse=True))

    assert first_templates[0].model_dump() == second_templates[0].model_dump()
    assert first_provenance == second_provenance
