"""W11-c gate 1 (data-free): decap scenario -> engine mapping.

`docs/engine/W11_PLAN_2026-09-19.md` SS3, SS6 row W11-c.  Reuses the synthetic
`ScenarioSpec`/`ScenarioDecap` construction style of
`tests/test_spd_decap_evaluation.py` (`_base_project`/`_decap`/`_scenario`) and
`tests/test_solver_profiles.py` (`_manufactured_project`), extended to two
rails so a decap can move between them.  No SPD data, no `tmp_path`
(`tests/test_engine_adapter_reproduction.py` explains why the latter is
avoided in this environment).

Covers `engine_adapter.decap_config`/`cross_net_decap_refdes` end to end
through the real `ScenarioSpec` model (not the `SimpleNamespace` stand-ins
`test_engine_adapter_reproduction.py` uses for the pure-function checks),
plus the product entry points `evaluate_scenario`/`evaluate_comparison_batch`
fail-closed on a cross-net decap assignment, and `original_configuration()`
recovers the SPD as-built mapping while the live scenario reflects edits.
"""

from __future__ import annotations

from spd_decap_pi._core.domain import (
    CapModel,
    ConfidenceLevel,
    MLOOutline,
    PinKind,
    PinRecord,
    PlaneCell,
    PlanePartitionSpec,
    ProjectSpec,
    RailSpec,
    StackupLayer,
    TerminalKind,
    ViaLoopTemplate,
    ViaPathKind,
)
from spd_decap_pi._core.solver import engine_adapter
from spd_decap_pi.evaluation import (
    ScenarioEvaluationPreflightError,
    evaluate_comparison_batch,
    evaluate_scenario,
)
from spd_decap_pi.scenario import (
    DecapPadState,
    RailEligibility,
    ScenarioDecap,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSpec,
    SourceIdentity,
)

RAIL_A = "RAIL_VDD"
RAIL_B = "RAIL_VDD2"
_RAIL_NETS = {RAIL_A: "VDD", RAIL_B: "VDD2"}


def _project() -> ProjectSpec:
    """Two independent rails sharing one PWR/GND layer pair (no mixed-reference
    certificate needed: `GND1` carries only the configured GND alias)."""

    return ProjectSpec(
        name="Engine mapping fixture",
        outline=MLOOutline(width_um=10_000.0, height_um=8_000.0),
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(name="TOP", thickness_um=35.0, conductivity_s_m=5.8e7),
            StackupLayer(name="D1", thickness_um=100.0, dk=4.0),
            StackupLayer(
                name="PWR1",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD", "VDD2"],
            ),
            StackupLayer(name="D2", thickness_um=80.0, dk=4.0),
            StackupLayer(
                name="GND1",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["DGND"],
            ),
        ],
        rails=[
            RailSpec(
                rail_id=RAIL_A,
                family="VDD",
                domain="VDD",
                net="VDD",
                site="SITE0",
                pwr_layer="PWR1",
                gnd_layer="GND1",
            ),
            RailSpec(
                rail_id=RAIL_B,
                family="VDD2",
                domain="VDD2",
                net="VDD2",
                site="SITE0",
                pwr_layer="PWR1",
                gnd_layer="GND1",
            ),
        ],
        pins=[
            PinRecord(
                refdes="U1", pin="P1", net="VDD", x_um=1000.0, y_um=1000.0,
                kind=PinKind.DEVICE_BUMP, terminal=TerminalKind.PWR,
                domain="VDD", site="SITE0",
            ),
            PinRecord(
                refdes="U1", pin="G1", net="DGND", x_um=1100.0, y_um=1000.0,
                kind=PinKind.DEVICE_BUMP, terminal=TerminalKind.GND,
                site="SITE0",
            ),
            PinRecord(
                refdes="U2", pin="P1", net="VDD2", x_um=3000.0, y_um=1000.0,
                kind=PinKind.DEVICE_BUMP, terminal=TerminalKind.PWR,
                domain="VDD2", site="SITE0",
            ),
            PinRecord(
                refdes="U2", pin="G1", net="DGND", x_um=3100.0, y_um=1000.0,
                kind=PinKind.DEVICE_BUMP, terminal=TerminalKind.GND,
                site="SITE0",
            ),
        ],
        partitions=[
            PlanePartitionSpec(
                layer="PWR1", rows=1, columns=2,
                domain_to_cell={"VDD": "CELL0", "VDD2": "CELL1"},
                cells=[
                    PlaneCell(
                        cell_id="CELL0", row=0, column=0,
                        x_min_um=0.0, x_max_um=5_000.0,
                        y_min_um=0.0, y_max_um=8_000.0,
                    ),
                    PlaneCell(
                        cell_id="CELL1", row=0, column=1,
                        x_min_um=5_000.0, x_max_um=10_000.0,
                        y_min_um=0.0, y_max_um=8_000.0,
                    ),
                ],
                split_gap_um=0.0,
                confidence=ConfidenceLevel.HIGH,
                confirmed=False,
            )
        ],
        cap_models=[
            CapModel(
                model_id="M1", capacitance_f=1.0e-6, esr_ohm=0.01, esl_h=0.5e-9,
                footprint="0402", inventory=0, source_hash="fixture-m1",
            ),
            CapModel(
                model_id="M2", capacitance_f=2.2e-6, esr_ohm=0.01, esl_h=0.5e-9,
                footprint="0402", inventory=0, source_hash="fixture-m2",
            ),
        ],
        via_templates=[
            ViaLoopTemplate(
                template_id="VT_ALLOWED",
                pwr_reference_layer="PWR1",
                gnd_reference_layer="GND1",
                path_kind=ViaPathKind.DIRECT,
                finite_port_width_um=100.0,
                finite_port_height_um=100.0,
                loop_resistance_ohm=0.001,
                loop_inductance_h=0.2e-9,
            )
        ],
        assumptions=["custom SPD solver assumption"],
        metadata={
            "plane_pair_confirmed": False,
            "spd_import": {"source_name": "fixture.spd"},
            "spd_mlo_transition_policy": {
                "policy_version": "MLO_TRANSITION_RECIPE_GATE_V1",
                "transition_required": False,
                "translated_recipe_validated": False,
                "evidence_codes": [],
                "source_sha256": "a" * 64,
            },
        },
    )


def _eligibility(rail_id: str) -> RailEligibility:
    return RailEligibility(
        rail_id=rail_id, net=_RAIL_NETS[rail_id], pwr_layer="PWR1", gnd_layer="GND1",
        via_template_id="VT_ALLOWED", allowed=True,
    )


def _decap(
    refdes: str,
    *,
    offset: float,
    source_rail_id: str = RAIL_A,
    current_rail_id: str | None = None,
    source_model_id: str | None = "M1",
    model_id: str | None = "M1",
    enabled: bool = True,
    source_mounted: bool | None = None,
    pad_state: DecapPadState = DecapPadState.NORMAL,
) -> ScenarioDecap:
    current_rail_id = current_rail_id or source_rail_id
    if source_mounted is None:
        source_mounted = enabled
    return ScenarioDecap(
        refdes=refdes,
        center=ScenarioPoint(x_um=1550.0 + offset, y_um=2000.0),
        pwr_pad=ScenarioPad(x_um=1500.0 + offset, y_um=2000.0, layer="TOP", padstack="CAP_PWR"),
        gnd_pad=ScenarioPad(x_um=1600.0 + offset, y_um=2000.0, layer="TOP", padstack="CAP_GND"),
        footprint="0402",
        source_net=_RAIL_NETS[source_rail_id],
        current_net=_RAIL_NETS[current_rail_id],
        source_rail_id=source_rail_id,
        current_rail_id=current_rail_id,
        source_model_id=source_model_id,
        model_id=model_id,
        enabled=enabled,
        pad_state=pad_state,
        source_mounted=source_mounted,
        eligibility={rid: _eligibility(rid) for rid in _RAIL_NETS},
    )


def _scenario(decaps: list[ScenarioDecap], *, revision: int = 1) -> ScenarioSpec:
    return ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixtures/engine_mapping.spd",
            name="engine_mapping.spd",
            size_bytes=123,
            sha256="a" * 64,
        ),
        normalized_project=_project(),
        decaps=decaps,
        connection_analysis=None,
        revision=revision,
    )


def _mixed_fixture_scenario() -> ScenarioSpec:
    """One scenario touching every state item 1 asks for.

    C1 mounted / C2 disabled / C3 ISOLATION_GAP / C4 model-swapped, all staying
    on RAIL_A; C5 moved from RAIL_A onto RAIL_B (still modelable: unmounted on
    RAIL_A); C6 moved from RAIL_B onto RAIL_A (unmodelable cross-net move).
    """

    return _scenario(
        [
            _decap("C1", offset=0.0),
            _decap("C2", offset=200.0, enabled=False),
            _decap(
                "C3", offset=400.0, enabled=False, pad_state=DecapPadState.ISOLATION_GAP
            ),
            _decap("C4", offset=600.0, source_model_id="M1", model_id="M2"),
            _decap(
                "C5", offset=800.0,
                source_rail_id=RAIL_A, current_rail_id=RAIL_B,
            ),
            _decap(
                "C6", offset=1000.0,
                source_rail_id=RAIL_B, current_rail_id=RAIL_A,
            ),
        ]
    )


def test_decap_config_covers_enabled_disabled_isolation_gap_and_model_swap() -> None:
    scenario = _mixed_fixture_scenario()

    assert engine_adapter.decap_config(scenario, RAIL_A) == {
        "C1": "M1",
        "C2": None,
        "C3": None,
        "C4": "M2",
        "C5": None,  # moved off RAIL_A -- the engine can only express it as unmounted
        "C6": "M1",  # moved onto RAIL_A -- unmodelable, but decap_config is a pure mapping
    }
    assert engine_adapter.decap_config(scenario, RAIL_B) == {
        "C5": "M1",
        "C6": None,
    }


def test_decap_moved_into_the_rail_is_reported_as_cross_net() -> None:
    scenario = _mixed_fixture_scenario()

    # A move is cross-net from whichever rail *receives* it: C6 (RAIL_B ->
    # RAIL_A) blocks RAIL_A, and C5 (RAIL_A -> RAIL_B) equally blocks RAIL_B --
    # the rail it left (RAIL_A) does not care where it went, it is simply
    # unmounted there (`decap_config`'s `None`, asserted separately above).
    assert engine_adapter.cross_net_decap_refdes(scenario, RAIL_A) == ("C6",)
    assert engine_adapter.cross_net_decap_refdes(scenario, RAIL_B) == ("C5",)


def test_cross_net_move_blocks_preflight_and_evaluation_refuses_to_start() -> None:
    scenario = _mixed_fixture_scenario()

    for run in (
        lambda: evaluate_scenario(scenario, RAIL_A, solver_profile="hybrid_plane_pair_v1"),
        lambda: evaluate_comparison_batch(
            scenario, (RAIL_A,), solver_profile="hybrid_plane_pair_v1"
        ),
    ):
        try:
            run()
        except ScenarioEvaluationPreflightError as exc:
            blockers = exc.preflight.blockers
        else:
            raise AssertionError("expected ScenarioEvaluationPreflightError")
        cross_net = [
            b for b in blockers
            if engine_adapter.ENGINE_CROSS_NET_DECAP_ASSIGNMENT in b.reason
        ]
        assert len(cross_net) == 1, blockers
        assert cross_net[0].rail_id == RAIL_A
        assert cross_net[0].refdes == "C6"


def test_original_configuration_recovers_as_built_mapping_while_tuned_reflects_edits() -> None:
    """`original_configuration(rail)` -> the SPD as-built decap mapping;
    `decap_config(scenario, rail)` -> the live, edited mapping.  Exact dicts."""

    scenario = _scenario(
        [
            _decap("C1", offset=0.0),  # unchanged
            _decap("C2", offset=200.0, enabled=False),  # unchanged, off by default
            # tuned edits below: model swap, newly disabled, moved off the rail
            _decap("C4", offset=600.0, source_model_id="M1", model_id="M2"),
            _decap(
                "C7", offset=1200.0, source_model_id="M1", model_id="M1",
                enabled=False, source_mounted=True,
            ),
            _decap(
                "C5", offset=800.0,
                source_rail_id=RAIL_A, current_rail_id=RAIL_B,
            ),
        ]
    )

    tuned_config = engine_adapter.decap_config(scenario, RAIL_A)
    assert tuned_config == {
        "C1": "M1",
        "C2": None,
        "C4": "M2",
        "C7": None,
        "C5": None,
    }

    prepared = scenario.with_baseline_captures((RAIL_A,))
    original = prepared.original_configuration(RAIL_A)
    as_built_config = engine_adapter.decap_config(original, RAIL_A)
    # C5's *source* rail is RAIL_A, so the as-built state restores it there;
    # C4/C7 restore their source model/enabled state instead of the tuned edit.
    assert as_built_config == {
        "C1": "M1",
        "C2": None,
        "C4": "M1",
        "C7": "M1",
        "C5": "M1",
    }
    assert as_built_config != tuned_config
