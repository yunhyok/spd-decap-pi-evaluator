from __future__ import annotations

import pytest

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
from spd_decap_pi._core.services import EvaluationView
from spd_decap_pi import evaluation as evaluation_module
from spd_decap_pi.evaluation import (
    ScenarioEvaluationBuildError,
    analyze_scenario_with_local_llm,
    build_evaluation_project,
    evaluate_scenario,
)
from spd_decap_pi.scenario import (
    RailEligibility,
    ScenarioDecap,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSpec,
    SourceIdentity,
)


def _base_project() -> ProjectSpec:
    return ProjectSpec(
        name="SPD fixture",
        outline=MLOOutline(width_um=10_000.0, height_um=8_000.0),
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(
                name="TOP",
                thickness_um=35.0,
                conductivity_s_m=5.8e7,
            ),
            StackupLayer(name="D1", thickness_um=100.0, dk=4.0),
            StackupLayer(
                name="PWR1",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD"],
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
                rail_id="RAIL_VDD",
                family="VDD",
                domain="VDD",
                net="VDD",
                site="SITE0",
                pwr_layer="PWR1",
                gnd_layer="GND1",
            )
        ],
        pins=[
            PinRecord(
                refdes="U1",
                pin="P1",
                net="VDD",
                x_um=1000.0,
                y_um=1000.0,
                kind=PinKind.DEVICE_BUMP,
                terminal=TerminalKind.PWR,
                domain="VDD",
                site="SITE0",
            ),
            PinRecord(
                refdes="U1",
                pin="G1",
                net="DGND",
                x_um=1100.0,
                y_um=1000.0,
                kind=PinKind.DEVICE_BUMP,
                terminal=TerminalKind.GND,
                site="SITE0",
            ),
            PinRecord(
                refdes="OLD",
                pin="PWR",
                net="VDD",
                x_um=9000.0,
                y_um=7000.0,
                kind=PinKind.DECAP_PAD,
                terminal=TerminalKind.PWR,
            ),
        ],
        partitions=[
            PlanePartitionSpec(
                layer="PWR1",
                rows=1,
                columns=1,
                domain_to_cell={"VDD": "CELL0"},
                cells=[
                    PlaneCell(
                        cell_id="CELL0",
                        row=0,
                        column=0,
                        x_min_um=0.0,
                        x_max_um=10_000.0,
                        y_min_um=0.0,
                        y_max_um=8_000.0,
                    )
                ],
                split_gap_um=0.0,
                confidence=ConfidenceLevel.HIGH,
                confirmed=False,
            )
        ],
        cap_models=[
            CapModel(
                model_id="M1",
                capacitance_f=1.0e-6,
                esr_ohm=0.01,
                esl_h=0.5e-9,
                footprint="0402",
                inventory=0,
                source_hash="fixture",
            )
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
        },
    )


def _decap(
    refdes: str,
    *,
    enabled: bool,
    model_id: str | None,
    footprint: str = "0402",
    eligibility: RailEligibility | None = None,
) -> ScenarioDecap:
    allowed = eligibility or RailEligibility(
        rail_id="RAIL_VDD",
        net="VDD",
        pwr_layer="PWR1",
        gnd_layer="GND1",
        via_template_id="VT_ALLOWED",
        allowed=True,
    )
    offset = 0.0 if refdes == "C1" else 1000.0
    return ScenarioDecap(
        refdes=refdes,
        center=ScenarioPoint(x_um=1550.0 + offset, y_um=2000.0),
        pwr_pad=ScenarioPad(
            x_um=1500.0 + offset,
            y_um=2000.0,
            layer="TOP",
            padstack="CAP_PWR",
        ),
        gnd_pad=ScenarioPad(
            x_um=1600.0 + offset,
            y_um=2000.0,
            layer="TOP",
            padstack="CAP_GND",
        ),
        footprint=footprint,
        source_net="VDD",
        current_net="VDD",
        source_rail_id="RAIL_VDD",
        current_rail_id="RAIL_VDD",
        source_model_id=model_id,
        model_id=model_id,
        enabled=enabled,
        source_mounted=enabled,
        eligibility={"RAIL_VDD": allowed},
    )


def _scenario() -> ScenarioSpec:
    return ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixtures/fixture.spd",
            name="fixture.spd",
            size_bytes=123,
            sha256="a" * 64,
        ),
        normalized_project=_base_project(),
        decaps=[
            _decap("C1", enabled=True, model_id="M1"),
            _decap("C2", enabled=False, model_id=None),
        ],
        revision=4,
    )


def _view(rail_id: str = "RAIL_VDD") -> EvaluationView:
    return EvaluationView(
        rail_id=rail_id,
        frequency_hz=[1.0e5, 1.0e6],
        magnitude_ohm=[0.02, 0.03],
        phase_deg=[0.0, 1.0],
        target_ohm=0.025,
        target_curve_ohm=[0.025, 0.025],
        max_violation_db=1.0,
        max_violation_frequency_hz=1.0e6,
        rms_violation_db=0.5,
        peak_magnitude_ohm=0.03,
        peak_frequency_hz=1.0e6,
        peak_prominence_db=1.2,
        peaks=[],
        cap_count=1,
        model_count=1,
        confidence="MEDIUM",
        confidence_note="fixture",
        confidence_bands=[],
        assumptions=[],
        solver_version="fixture-solver-1",
        solver_diagnostics={},
        convergence=None,
        z_real_ohm=[0.02, 0.03],
        z_imag_ohm=[0.0, 0.0],
    )


def test_build_evaluation_project_rebuilds_decap_electrical_state() -> None:
    scenario = _scenario()

    project = build_evaluation_project(scenario)

    assert [(pin.refdes, pin.pin) for pin in project.pins[:2]] == [
        ("U1", "P1"),
        ("U1", "G1"),
    ]
    assert "OLD" not in {pin.refdes for pin in project.pins}
    c1_power = next(
        pin
        for pin in project.pins
        if pin.refdes == "C1" and pin.terminal == TerminalKind.PWR
    )
    assert (c1_power.x_um, c1_power.y_um) == (1500.0, 2000.0)
    assert c1_power.via_template_id == "VT_ALLOWED"
    assert len(project.topology_maps) == 1
    assert all(item.topology == "DIRECT" for item in project.topology_maps)
    assert project.topology_maps[0].x_um == 1500.0
    assert "C2" not in {pin.refdes for pin in project.pins}
    assert [item.slot_id for item in project.placements] == ["SPDPI:C1"]
    assert project.cap_models[0].inventory == 1
    assert all(item.confirmed for item in project.partitions)
    assert project.metadata["plane_pair_confirmed"] is True
    assert project.metadata["spd_import"]["raw_spd_embedded"] is False
    assert "custom SPD solver assumption" in project.assumptions
    assert any("rectangular solver approximation" in item for item in project.assumptions)

    # The persisted normalized project remains untouched.
    assert scenario.base_project.partitions[0].confirmed is False
    assert any(pin.refdes == "OLD" for pin in scenario.base_project.pins)
    assert scenario.base_project.cap_models[0].inventory == 0


def test_disabled_unavailable_dnp_is_electrically_absent_and_does_not_block() -> None:
    scenario = _scenario()
    payload = scenario.model_dump(mode="json")
    payload["decaps"][1].update(
        {
            "current_net": "NO_PLANE",
            "current_rail_id": "UNAVAILABLE::NO_PLANE",
            "eligibility": {},
        }
    )

    project = build_evaluation_project(ScenarioSpec.model_validate(payload))

    assert [item.slot_id for item in project.topology_maps] == ["SPDPI:C1"]
    assert "C2" not in {item.refdes for item in project.pins}


def test_target_rail_only_requires_models_for_its_enabled_decaps() -> None:
    base = _base_project()
    layers = [
        item.model_copy(update={"pwr_nets": [*item.pwr_nets, "VDD2"]})
        if item.name == "PWR1"
        else item
        for item in base.stackup_layers
    ]
    second_rail = RailSpec(
        rail_id="RAIL_VDD2",
        family="VDD2",
        domain="VDD2",
        net="VDD2",
        site="SITE0",
        pwr_layer="PWR1",
        gnd_layer="GND1",
    )
    base = base.model_copy(
        update={"stackup_layers": layers, "rails": [*base.rails, second_rail]}
    )
    target = _decap("C1", enabled=True, model_id="M1")
    off_target_eligibility = RailEligibility(
        rail_id="RAIL_VDD2",
        net="VDD2",
        pwr_layer="PWR1",
        gnd_layer="GND1",
        via_template_id="VT_ALLOWED",
        allowed=True,
    )
    off_target = _decap("C2", enabled=True, model_id=None).model_copy(
        update={
            "source_net": "VDD2",
            "current_net": "VDD2",
            "source_rail_id": "RAIL_VDD2",
            "current_rail_id": "RAIL_VDD2",
            "eligibility": {"RAIL_VDD2": off_target_eligibility},
        }
    )
    scenario = ScenarioSpec.model_validate(
        {
            **_scenario().model_dump(mode="json"),
            "normalized_project": base,
            "decaps": [target, off_target],
        }
    )

    project = build_evaluation_project(
        scenario, evaluation_rail_id="rail_vdd"
    )

    assert [item.slot_id for item in project.placements] == ["SPDPI:C1"]
    assert "C2" not in {item.refdes for item in project.pins}

    target_unmodeled = target.model_copy(update={"model_id": None})
    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        build_evaluation_project(
            scenario.model_copy(
                update={"decaps": [target_unmodeled, off_target]}
            ),
            evaluation_rail_id="RAIL_VDD",
        )
    assert captured.value.code == "MODEL_REQUIRED"


def test_build_rejects_ineligible_current_rail_with_reason() -> None:
    blocked = RailEligibility(
        rail_id="RAIL_VDD",
        net="VDD",
        pwr_layer="PWR1",
        gnd_layer="GND1",
        allowed=False,
        reason="PWR plane does not contain the actual PWR pad point",
    )
    scenario = _scenario().model_copy(
        update={"decaps": [_decap("C1", enabled=True, model_id="M1", eligibility=blocked)]}
    )

    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        build_evaluation_project(scenario)

    assert captured.value.code == "RAIL_INELIGIBLE"
    assert captured.value.refdes == "C1"
    assert "actual PWR pad point" in str(captured.value)


@pytest.mark.parametrize(
    ("decap", "code"),
    [
        (_decap("C1", enabled=True, model_id=None), "MODEL_REQUIRED"),
        (_decap("C1", enabled=True, model_id="UNKNOWN"), "MODEL_UNKNOWN"),
        (
            _decap("C1", enabled=True, model_id="M1", footprint="0201"),
            "FOOTPRINT_MISMATCH",
        ),
    ],
)
def test_build_rejects_model_and_footprint_mismatches(
    decap: ScenarioDecap, code: str
) -> None:
    scenario = _scenario().model_copy(update={"decaps": [decap]})

    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        build_evaluation_project(scenario)

    assert captured.value.code == code


def test_evaluate_scenario_returns_deterministic_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float | None, int]] = []

    def fake_evaluate(state, rail_id, target_ohm, modal_max_index, **_kwargs):
        calls.append((rail_id, target_ohm, modal_max_index))
        view = _view(rail_id)
        state.last_evaluation = view
        return view

    monkeypatch.setattr(
        evaluation_module.evaluation_services, "evaluate_workspace", fake_evaluate
    )
    scenario = _scenario()

    first = evaluate_scenario(scenario, "rail_vdd", target_ohm=0.02, modal_max_index=6)
    ui_only = scenario.model_copy(
        update={
            "net_colors": {"VDD": "#112233"},
            "selected_refdes": ["C1"],
            "revision": 5,
        }
    )
    second = evaluate_scenario(ui_only, "RAIL_VDD", target_ohm=0.02, modal_max_index=6)

    assert calls == [("RAIL_VDD", 0.02, 6), ("RAIL_VDD", 0.02, 6)]
    assert first.design_fingerprint == scenario.design_fingerprint
    assert first.evaluation_fingerprint == second.evaluation_fingerprint
    assert first.matches(ui_only)
    assert not first.matches(ui_only, require_revision=True)
    assert first.state.last_evaluation is first.view


def test_ai_helper_forces_plot_analyst_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_evaluate(state, rail_id, *_args, **_kwargs):
        view = _view(rail_id)
        state.last_evaluation = view
        return view

    captured: dict[str, object] = {}

    def fake_analyze(state, endpoint, model, mode, allow_remote, **_kwargs):
        captured.update(
            state=state,
            endpoint=endpoint,
            model=model,
            mode=mode,
            allow_remote=allow_remote,
        )
        return "plot-only-result"

    monkeypatch.setattr(
        evaluation_module.evaluation_services, "evaluate_workspace", fake_evaluate
    )
    monkeypatch.setattr(
        evaluation_module.evaluation_services,
        "analyze_with_local_llm",
        fake_analyze,
    )
    result = evaluate_scenario(_scenario(), "RAIL_VDD")

    actual = analyze_scenario_with_local_llm(
        result,
        "http://127.0.0.1:11434",
        "local-model",
        allow_remote=False,
    )

    assert actual == "plot-only-result"
    assert captured["mode"] == "Plot Analyst"
    assert captured["state"] is result.state


def test_evaluate_rejects_unknown_rail_before_solver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def should_not_run(*_args, **_kwargs):
        nonlocal called
        called = True
        return _view()

    monkeypatch.setattr(
        evaluation_module.evaluation_services, "evaluate_workspace", should_not_run
    )

    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        evaluate_scenario(_scenario(), "NO_SUCH_RAIL")

    assert captured.value.code == "EVALUATION_RAIL_UNKNOWN"
    assert called is False


def test_small_scenario_runs_through_existing_evaluation_solver() -> None:
    result = evaluate_scenario(
        _scenario(),
        "RAIL_VDD",
        target_ohm=0.02,
        modal_max_index=6,
    )

    assert result.view.rail_id == "RAIL_VDD"
    assert result.view.cap_count == 1
    assert len(result.view.frequency_hz) >= 2
    assert result.state.last_evaluation is result.view
    assert len(result.evaluation_fingerprint) == 64
