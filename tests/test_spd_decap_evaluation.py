from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from hashlib import sha256
import json

from pydantic import ValidationError
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
    ScenarioEvaluationCacheError,
    analyze_scenario_with_local_llm,
    baseline_fallback_model_refdes,
    build_evaluation_project,
    evaluate_comparison_batch,
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
from spd_decap_pi.scenario_io import load_scenario_bundle, save_scenario


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


def _fake_evaluator(calls: list[str]):
    def evaluate(actual, rail_id, target_ohm=None, modal_max_index=8, **_kwargs):
        calls.append(f"{rail_id}:{actual.design_fingerprint}")
        view = _view(rail_id)
        view.solver_version = evaluation_module.SOLVER_VERSION
        key = evaluation_module.ScenarioResultKey.from_settings(
            design_fingerprint=actual.design_fingerprint,
            rail_id=rail_id,
            settings={
                "target_ohm": target_ohm,
                "modal_max_index": modal_max_index,
            },
            solver_version=view.solver_version,
        )
        return evaluation_module.ScenarioEvaluation(
            state=object(),
            view=view,
            result_key=key,
            scenario_revision=actual.revision,
        )

    return evaluate


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


def test_original_capture_freezes_fallback_model_and_restores_source_state() -> None:
    scenario = _scenario()
    original = scenario.decaps[0]
    setup_model = original.model_copy(
        update={"source_model_id": None, "model_id": "M1", "enabled": False}
    )
    tuned = ScenarioSpec.model_validate(
        {**scenario.model_dump(mode="python"), "decaps": [setup_model, scenario.decaps[1]]}
    )

    assert baseline_fallback_model_refdes(tuned, ["rail_vdd"]) == ("C1",)
    captured = tuned.with_baseline_captures(("RAIL_VDD",))
    baseline = captured.original_configuration("rail_vdd")
    restored = baseline.decaps[0]

    assert restored.enabled is True
    assert restored.current_net == restored.source_net
    assert restored.current_rail_id == restored.source_rail_id
    assert restored.model_id == "M1"
    later_tuning = ScenarioSpec.model_validate(
        {
            **captured.model_dump(mode="python"),
            "decaps": [
                captured.decaps[0].model_copy(update={"model_id": "OTHER", "enabled": False}),
                captured.decaps[1],
            ],
        }
    )
    assert (
        later_tuning.baseline_captures["RAIL_VDD"].capture_fingerprint
        == captured.baseline_captures["RAIL_VDD"].capture_fingerprint
    )
    assert later_tuning.original_configuration("RAIL_VDD").decaps[0].model_id == "M1"


def test_original_capture_blocks_missing_initial_models() -> None:
    scenario = _scenario()
    missing = scenario.decaps[0].model_copy(
        update={"source_model_id": None, "model_id": None}
    )
    scenario = ScenarioSpec.model_validate(
        {**scenario.model_dump(mode="python"), "decaps": [missing, scenario.decaps[1]]}
    )

    with pytest.raises(ValueError, match="assign initial models.*C1"):
        scenario.with_baseline_captures(("RAIL_VDD",))


def test_comparison_batch_caches_baseline_then_reuses_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario()
    tuned = scenario.decaps[0].model_copy(update={"enabled": False})
    scenario = ScenarioSpec.model_validate(
        {**scenario.model_dump(mode="python"), "decaps": [tuned, scenario.decaps[1]]}
    )
    calls: list[str] = []

    def fake_evaluate(actual, rail_id, target_ohm=None, modal_max_index=8, **_kwargs):
        calls.append(actual.design_fingerprint)
        view = _view(rail_id)
        view.solver_version = evaluation_module.SOLVER_VERSION
        key = evaluation_module.ScenarioResultKey.from_settings(
            design_fingerprint=actual.design_fingerprint,
            rail_id=rail_id,
            settings={
                "target_ohm": target_ohm,
                "modal_max_index": modal_max_index,
            },
            solver_version=view.solver_version,
        )
        return evaluation_module.ScenarioEvaluation(
            state=object(),
            view=view,
            result_key=key,
            scenario_revision=actual.revision,
        )

    monkeypatch.setattr(evaluation_module, "evaluate_scenario", fake_evaluate)
    first = evaluate_comparison_batch(scenario, ["rail_vdd", "RAIL_VDD"])

    assert len(calls) == 2
    assert len(first.comparisons) == 1
    assert not first.comparisons[0].baseline_from_cache
    assert len(first.updated_scenario.evaluation_cache) == 1
    assert first.updated_scenario.baseline_captures
    assert first.updated_scenario.revision == scenario.revision + 1

    calls.clear()
    second = evaluate_comparison_batch(
        first.updated_scenario,
        ["RAIL_VDD"],
        attachments=first.updated_attachments,
    )
    assert len(calls) == 1
    assert second.comparisons[0].baseline_from_cache
    assert second.comparisons[0].baseline.view.magnitude_ohm == [0.02, 0.03]


def test_comparison_batch_rejects_tampered_baseline_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evaluation_module,
        "build_evaluation_project",
        lambda *_args, **_kwargs: _base_project(),
    )

    def fake_evaluate(actual, rail_id, target_ohm=None, modal_max_index=8, **_kwargs):
        view = _view(rail_id)
        view.solver_version = evaluation_module.SOLVER_VERSION
        return evaluation_module.ScenarioEvaluation(
            state=object(),
            view=view,
            result_key=evaluation_module.ScenarioResultKey.from_settings(
                design_fingerprint=actual.design_fingerprint,
                rail_id=rail_id,
                settings={
                    "target_ohm": target_ohm,
                    "modal_max_index": modal_max_index,
                },
                solver_version=view.solver_version,
            ),
            scenario_revision=actual.revision,
        )

    monkeypatch.setattr(evaluation_module, "evaluate_scenario", fake_evaluate)
    first = evaluate_comparison_batch(_scenario(), ["RAIL_VDD"])
    tampered = dict(first.updated_attachments)
    result_name = next(name for name in tampered if name.startswith("results/"))
    tampered[result_name] = b"{}"

    with pytest.raises(ScenarioEvaluationCacheError, match="SHA-256"):
        evaluate_comparison_batch(
            first.updated_scenario, ["RAIL_VDD"], attachments=tampered
        )


def test_cached_baseline_rejects_numeric_strings_even_with_updated_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evaluation_module, "evaluate_scenario", _fake_evaluator([])
    )
    first = evaluate_comparison_batch(_scenario(), ["RAIL_VDD"])
    cache_key, metadata = next(iter(first.updated_scenario.evaluation_cache.items()))
    payload = json.loads(first.updated_attachments[metadata.attachment_name])
    payload["view"]["frequency_hz"][0] = "100000"
    content = json.dumps(payload, sort_keys=True).encode("utf-8")
    digest = sha256(content).hexdigest()
    changed_metadata = metadata.model_copy(update={"attachment_sha256": digest})
    changed_hashes = dict(first.updated_scenario.attachment_hashes)
    changed_hashes[metadata.attachment_name] = digest
    changed_scenario = ScenarioSpec.model_validate(
        {
            **first.updated_scenario.model_dump(mode="python"),
            "attachment_hashes": changed_hashes,
            "evaluation_cache": {cache_key: changed_metadata},
        }
    )
    changed_attachments = dict(first.updated_attachments)
    changed_attachments[metadata.attachment_name] = content

    with pytest.raises(ScenarioEvaluationCacheError, match="numeric JSON array"):
        evaluate_comparison_batch(
            changed_scenario,
            ["RAIL_VDD"],
            attachments=changed_attachments,
        )


def test_cached_baseline_rejects_duplicate_json_keys_with_updated_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evaluation_module, "evaluate_scenario", _fake_evaluator([])
    )
    first = evaluate_comparison_batch(_scenario(), ["RAIL_VDD"])
    cache_key, metadata = next(iter(first.updated_scenario.evaluation_cache.items()))
    original = first.updated_attachments[metadata.attachment_name]
    content = original.replace(
        b'"format":',
        b'"format":"shadowed-duplicate","format":',
        1,
    )
    digest = sha256(content).hexdigest()
    changed_metadata = metadata.model_copy(update={"attachment_sha256": digest})
    changed_hashes = dict(first.updated_scenario.attachment_hashes)
    changed_hashes[metadata.attachment_name] = digest
    changed_scenario = ScenarioSpec.model_validate(
        {
            **first.updated_scenario.model_dump(mode="python"),
            "attachment_hashes": changed_hashes,
            "evaluation_cache": {cache_key: changed_metadata},
        }
    )
    changed_attachments = dict(first.updated_attachments)
    changed_attachments[metadata.attachment_name] = content

    with pytest.raises(ScenarioEvaluationCacheError, match="strict JSON"):
        evaluate_comparison_batch(
            changed_scenario,
            ["RAIL_VDD"],
            attachments=changed_attachments,
        )


def test_baseline_capture_is_frozen_and_known_source_model_cannot_be_rebound() -> None:
    scenario = _scenario()
    project = scenario.base_project
    model_2 = project.cap_models[0].model_copy(
        update={"model_id": "M2", "source_hash": "fixture-2"}
    )
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_copy(
                update={"cap_models": [*project.cap_models, model_2]}
            ),
        }
    )
    captured = scenario.with_baseline_captures(("RAIL_VDD",))
    capture = captured.baseline_captures["RAIL_VDD"]

    with pytest.raises(ValidationError, match="frozen"):
        capture.model_bindings[0].model_id = "M2"
    with pytest.raises(AttributeError):
        capture.model_bindings.append(capture.model_bindings[0])

    payload = captured.model_dump(mode="python")
    payload["baseline_captures"]["RAIL_VDD"]["model_bindings"][0][
        "model_id"
    ] = "M2"
    with pytest.raises(ValidationError, match="SPD source model"):
        ScenarioSpec.model_validate(payload)


def test_unused_model_addition_does_not_invalidate_saved_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario()
    tuned = scenario.decaps[0].model_copy(update={"enabled": False})
    scenario = ScenarioSpec.model_validate(
        {**scenario.model_dump(mode="python"), "decaps": [tuned, scenario.decaps[1]]}
    )
    calls: list[str] = []
    monkeypatch.setattr(
        evaluation_module, "evaluate_scenario", _fake_evaluator(calls)
    )
    first = evaluate_comparison_batch(scenario, ["RAIL_VDD"])
    capture_fingerprint = first.updated_scenario.baseline_captures[
        "RAIL_VDD"
    ].capture_fingerprint

    project = first.updated_scenario.base_project
    unused = project.cap_models[0].model_copy(
        update={"model_id": "UNUSED_M2", "source_hash": "unused-model"}
    )
    with_unused = ScenarioSpec.model_validate(
        {
            **first.updated_scenario.model_dump(mode="python"),
            "normalized_project": project.model_copy(
                update={"cap_models": [*project.cap_models, unused]}
            ),
            "revision": first.updated_scenario.revision + 1,
        }
    )
    assert (
        with_unused.baseline_captures["RAIL_VDD"].capture_fingerprint
        == capture_fingerprint
    )

    calls.clear()
    second = evaluate_comparison_batch(
        with_unused,
        ["RAIL_VDD"],
        attachments=first.updated_attachments,
    )

    assert len(calls) == 1
    assert second.comparisons[0].baseline_from_cache
    assert len(second.updated_scenario.evaluation_cache) == 1


def test_batch_rejects_missing_extra_or_modified_input_attachments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted = b"trusted model bytes"
    scenario = ScenarioSpec.model_validate(
        {
            **_scenario().model_dump(mode="python"),
            "attachment_names": ["models/trusted.lib"],
            "attachment_hashes": {
                "models/trusted.lib": sha256(trusted).hexdigest()
            },
        }
    )
    monkeypatch.setattr(
        evaluation_module,
        "evaluate_scenario",
        lambda *_args, **_kwargs: pytest.fail("solver must not run"),
    )

    cases = (
        ({}, "attachment set mismatch"),
        ({"models/trusted.lib": b"modified"}, "SHA-256"),
        (
            {"models/trusted.lib": trusted, "extra.bin": b"extra"},
            "unexpected extra.bin",
        ),
    )
    for supplied, message in cases:
        with pytest.raises(ScenarioEvaluationCacheError, match=message):
            evaluate_comparison_batch(
                scenario, ["RAIL_VDD"], attachments=supplied
            )


def test_saved_baseline_is_decoded_and_reused_after_scenario_reload(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _scenario()
    tuned = scenario.decaps[0].model_copy(update={"enabled": False})
    scenario = ScenarioSpec.model_validate(
        {**scenario.model_dump(mode="python"), "decaps": [tuned, scenario.decaps[1]]}
    )
    calls: list[str] = []
    monkeypatch.setattr(
        evaluation_module, "evaluate_scenario", _fake_evaluator(calls)
    )
    first = evaluate_comparison_batch(scenario, ["RAIL_VDD"])
    path = save_scenario(
        first.updated_scenario,
        tmp_path / "baseline.spdpi",
        attachments=first.updated_attachments,
    )
    loaded = load_scenario_bundle(path)

    calls.clear()
    second = evaluate_comparison_batch(
        loaded.scenario,
        ["RAIL_VDD"],
        attachments=loaded.attachments,
    )

    assert len(calls) == 1
    assert second.comparisons[0].baseline_from_cache
    assert second.comparisons[0].baseline.view.magnitude_ohm == [0.02, 0.03]


def test_batch_accepts_independent_adaptive_frequency_grids() -> None:
    scenario = _scenario()
    tuned = scenario.decaps[0].model_copy(update={"enabled": False})
    scenario = ScenarioSpec.model_validate(
        {**scenario.model_dump(mode="python"), "decaps": [tuned, scenario.decaps[1]]}
    )

    batch = evaluate_comparison_batch(
        scenario,
        ["RAIL_VDD"],
        target_ohm=0.02,
        modal_max_index=6,
    )

    comparison = batch.comparisons[0]
    assert comparison.baseline.view.frequency_hz != comparison.tuned.view.frequency_hz
    batch.validate_for_scenario(scenario)


def test_batch_nested_identity_validation_rejects_a_stale_tuned_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario()
    tuned_decap = scenario.decaps[0].model_copy(update={"enabled": False})
    scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "decaps": [tuned_decap, scenario.decaps[1]],
        }
    )
    monkeypatch.setattr(
        evaluation_module, "evaluate_scenario", _fake_evaluator([])
    )
    batch = evaluate_comparison_batch(scenario, ["RAIL_VDD"])
    batch.validate_for_scenario(scenario)
    comparison = batch.comparisons[0]
    stale_key = comparison.tuned.result_key.model_copy(
        update={"design_fingerprint": "f" * 64}
    )
    stale = replace(
        batch,
        comparisons=(
            replace(
                comparison,
                tuned=replace(comparison.tuned, result_key=stale_key),
            ),
        ),
    )

    with pytest.raises(ScenarioEvaluationCacheError, match="Tuned result.*stale"):
        stale.validate_for_scenario(scenario)


def test_batch_validation_compares_displayed_original_to_persisted_attachment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario()
    monkeypatch.setattr(
        evaluation_module, "evaluate_scenario", _fake_evaluator([])
    )
    batch = evaluate_comparison_batch(scenario, ["RAIL_VDD"])
    comparison = batch.comparisons[0]
    changed_view = deepcopy(comparison.baseline.view)
    changed_view.magnitude_ohm[0] = 9.99
    changed = replace(
        batch,
        comparisons=(
            replace(
                comparison,
                baseline=replace(comparison.baseline, view=changed_view),
            ),
        ),
    )

    with pytest.raises(ScenarioEvaluationCacheError, match="differs.*persisted"):
        changed.validate_for_scenario(scenario)


def test_cache_attachment_name_casing_reuses_after_save_and_reload(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _scenario()
    tuned = scenario.decaps[0].model_copy(update={"enabled": False})
    scenario = ScenarioSpec.model_validate(
        {**scenario.model_dump(mode="python"), "decaps": [tuned, scenario.decaps[1]]}
    )
    calls: list[str] = []
    monkeypatch.setattr(
        evaluation_module, "evaluate_scenario", _fake_evaluator(calls)
    )
    first = evaluate_comparison_batch(scenario, ["RAIL_VDD"])
    cache_key, metadata = next(iter(first.updated_scenario.evaluation_cache.items()))
    upper_metadata = metadata.model_copy(
        update={"attachment_name": metadata.attachment_name.upper()}
    )
    upper_scenario = ScenarioSpec.model_validate(
        {
            **first.updated_scenario.model_dump(mode="python"),
            "evaluation_cache": {cache_key: upper_metadata},
        }
    )
    path = save_scenario(
        upper_scenario,
        tmp_path / "casefold.spdpi",
        attachments=first.updated_attachments,
    )
    loaded = load_scenario_bundle(path)

    calls.clear()
    second = evaluate_comparison_batch(
        loaded.scenario,
        ["RAIL_VDD"],
        attachments=loaded.attachments,
    )
    assert len(calls) == 1
    assert second.comparisons[0].baseline_from_cache


def test_generated_cache_enforces_the_same_size_limit_as_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        evaluation_module, "evaluate_scenario", _fake_evaluator([])
    )
    monkeypatch.setattr(
        evaluation_module, "MAX_EVALUATION_ATTACHMENT_BYTES", 100
    )

    with pytest.raises(ScenarioEvaluationCacheError, match="generated.*too large"):
        evaluate_comparison_batch(_scenario(), ["RAIL_VDD"])


def test_cancellation_after_original_does_not_mutate_caller_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario()
    tuned = scenario.decaps[0].model_copy(update={"enabled": False})
    scenario = ScenarioSpec.model_validate(
        {**scenario.model_dump(mode="python"), "decaps": [tuned, scenario.decaps[1]]}
    )
    before = scenario.model_dump(mode="json")
    cancelled = False

    def fake_evaluate(*args, **kwargs):
        nonlocal cancelled
        result = _fake_evaluator([])(*args, **kwargs)
        cancelled = True
        return result

    monkeypatch.setattr(evaluation_module, "evaluate_scenario", fake_evaluate)
    with pytest.raises(RuntimeError, match="cancelled"):
        evaluate_comparison_batch(
            scenario,
            ["RAIL_VDD"],
            is_cancelled=lambda: cancelled,
        )

    assert scenario.model_dump(mode="json") == before
    assert scenario.baseline_captures == {}
