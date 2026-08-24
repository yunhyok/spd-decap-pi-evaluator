from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from copy import deepcopy
from hashlib import sha256
import json
from threading import Event, Thread
from time import sleep
from types import SimpleNamespace

from pydantic import ValidationError
import pytest

from spd_decap_pi._core.domain import (
    CapModel,
    ConfidenceLevel,
    ImpedanceSample,
    MLOOutline,
    PinKind,
    PinRecord,
    PlaneCell,
    PlanePartitionSpec,
    ProjectSpec,
    RailSpec,
    StackupLayer,
    TerminalKind,
    TopologyKind,
    ViaLoopTemplate,
    ViaPathKind,
)
from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.io.spd import SpdPlaneGeometry
from spd_decap_pi._core.solver import evaluator as solver_evaluator_module
from spd_decap_pi._core.services import EvaluationView
from spd_decap_pi._core.solver.evaluator import (
    EvaluationError,
    build_project_evaluation_request,
    compile_project_evaluation_template,
)
from spd_decap_pi._core.solver.modal import ModalSolverError
from spd_decap_pi._core.models.impedance import SeriesRLModel
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationError,
)
from spd_decap_pi._core.solver.layerwise_network import (
    LayerwiseNetworkUnavailable,
    LayerwiseUniformSourceModel,
)
from spd_decap_pi._core.solver.profiles import (
    LAYERWISE_ADMITTANCE_PROFILE,
    RESEARCH_UNIFORM_ADMITTANCE_PROFILE,
)
from spd_decap_pi._core.solver.research_uniform_profile import UniformC00SourceModel
from spd_decap_pi import evaluation as evaluation_module
from spd_decap_pi import spd_adapter as spd_adapter_module
from spd_decap_pi import scenario as scenario_module
from spd_decap_pi.distribution import (
    DistributionDistanceMode,
    apply_distribution_plan,
    build_distribution_power_projection,
    compute_distribution_plan,
)
from spd_decap_pi.evaluation import (
    EvaluationConnectivityPreflight,
    ScenarioEvaluationBuildError,
    ScenarioEvaluationCacheError,
    ScenarioEvaluationPreflightError,
    analyze_scenario_with_local_llm,
    baseline_fallback_model_refdes,
    build_evaluation_project,
    build_evaluation_workspace,
    evaluate_comparison_batch,
    evaluate_scenario,
    preflight_evaluation_comparison,
    preflight_evaluation_connectivity,
)
from spd_decap_pi.scenario import (
    DecapConnectionKind,
    DecapPadState,
    RailEligibility,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSpec,
    ScenarioViaLanding,
    ScenarioViaGraphContactEvidence,
    ScenarioViaPathEvidence,
    ScenarioViaSegment,
    SHARED_PAD_ANALYSIS_VERSION,
    SharedPadCluster,
    SharedPadClusterState,
    SharedPadConnectionAnalysis,
    SourceIdentity,
)
from spd_decap_pi.scenario_io import load_scenario_bundle, save_scenario


def test_default_scoped_blas_limit_is_one_without_user_backend_configuration(
    monkeypatch,
) -> None:
    monkeypatch.delenv("SPD_DECAP_PI_BLAS_THREADS", raising=False)

    assert core_services._requested_blas_thread_limit() == 1


def test_spd_blas_override_controls_default_and_explicit_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "24")
    assert core_services._requested_blas_thread_limit() == 1

    monkeypatch.setenv("SPD_DECAP_PI_BLAS_THREADS", "3")
    assert core_services._requested_blas_thread_limit() == 3

    monkeypatch.setenv("SPD_DECAP_PI_BLAS_THREADS", "auto")
    assert core_services._requested_blas_thread_limit() is None

    monkeypatch.setenv("SPD_DECAP_PI_BLAS_THREADS", "inherit")
    assert core_services._requested_blas_thread_limit() is None

    monkeypatch.setenv("SPD_DECAP_PI_BLAS_THREADS", "bad")
    assert core_services._requested_blas_thread_limit() == 1
    monkeypatch.setenv("SPD_DECAP_PI_BLAS_THREADS", "0")
    assert core_services._requested_blas_thread_limit() == 1


def test_scoped_blas_limit_calls_threadpoolctl_only_without_user_configuration(
    monkeypatch,
) -> None:
    calls: list[tuple[int, str]] = []
    monkeypatch.delenv("SPD_DECAP_PI_BLAS_THREADS", raising=False)
    monkeypatch.setattr(
        core_services,
        "threadpool_limits",
        lambda *, limits, user_api: calls.append((limits, user_api)) or nullcontext(),
    )

    with core_services.scoped_blas_threads():
        pass

    assert calls == [(1, "blas")]


def test_scoped_blas_limit_serializes_overlapping_contexts_and_restores_on_error(
    monkeypatch,
) -> None:
    active = 0
    maximum_active = 0
    lock_entered = Event()
    release_first = Event()

    class Limit:
        def __enter__(self):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            lock_entered.set()
            return self

        def __exit__(self, *_args):
            nonlocal active
            active -= 1
            return False

    monkeypatch.setattr(core_services, "threadpool_limits", lambda **_kwargs: Limit())

    def first() -> None:
        with core_services.scoped_blas_threads():
            release_first.wait(1)

    first_thread = Thread(target=first)
    first_thread.start()
    assert lock_entered.wait(1)
    second_done = Event()
    def second() -> None:
        try:
            with core_services.scoped_blas_threads():
                raise RuntimeError("expected")
        except RuntimeError:
            second_done.set()

    second_thread = Thread(target=second)
    second_thread.start()
    sleep(0.03)
    assert not second_done.is_set()
    release_first.set()
    first_thread.join(1)
    second_thread.join(1)
    assert second_done.is_set()
    assert maximum_active == 1
    assert active == 0


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
            "spd_mlo_transition_policy": {
                "policy_version": "MLO_TRANSITION_RECIPE_GATE_V1",
                "transition_required": False,
                "translated_recipe_validated": False,
                "evidence_codes": [],
                "source_sha256": "a" * 64,
            },
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
    decaps = [
        _decap("C1", enabled=True, model_id="M1"),
        _decap("C2", enabled=False, model_id=None),
    ]
    connections = {
        decap.refdes: ScenarioDecapConnection(
            refdes=decap.refdes,
            kind=DecapConnectionKind.DIRECT,
            power_vias=(
                ScenarioViaLanding(
                    via_id=f"VP-{decap.refdes}",
                    net=decap.source_net,
                    endpoint_node_id=f"NP-{decap.refdes}",
                    x_um=decap.pwr_pad.x_um,
                    y_um=decap.pwr_pad.y_um,
                    padstack="VIA",
                ),
            ),
            ground_vias=(
                ScenarioViaLanding(
                    via_id=f"VG-{decap.refdes}",
                    net="DGND",
                    endpoint_node_id=f"NG-{decap.refdes}",
                    x_um=decap.gnd_pad.x_um,
                    y_um=decap.gnd_pad.y_um,
                    padstack="VIA",
                ),
            ),
        )
        for decap in decaps
    }
    return ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixtures/fixture.spd",
            name="fixture.spd",
            size_bytes=123,
            sha256="a" * 64,
        ),
        normalized_project=_base_project(),
        decaps=decaps,
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="a" * 64,
            connections=connections,
        ),
        revision=4,
    )


def _scenario_with_connection(
    scenario: ScenarioSpec,
    refdes: str,
    connection: ScenarioDecapConnection,
    *,
    decaps: list[ScenarioDecap] | None = None,
) -> ScenarioSpec:
    assert scenario.connection_analysis is not None
    analysis = scenario.connection_analysis.model_copy(
        update={
            "connections": {
                **scenario.connection_analysis.connections,
                refdes: connection,
            }
        }
    )
    return ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "decaps": decaps or scenario.decaps,
            "connection_analysis": analysis,
        }
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
            settings=evaluation_module._evaluation_settings(
                target_ohm,
                modal_max_index,
                _kwargs.get(
                    "solver_profile",
                    evaluation_module.DEFAULT_SOLVER_PROFILE_KEY,
                ),
            ),
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


def test_layerwise_workspace_binds_exact_scenario_and_transient_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario()
    transient = _base_project()
    monkeypatch.setattr(
        evaluation_module,
        "build_evaluation_project",
        lambda *_args, **_kwargs: transient,
    )

    state = build_evaluation_workspace(
        scenario,
        evaluation_rail_id="RAIL_VDD",
        solver_profile="layerwise_admittance_v1",
    )

    factory = state.layerwise_termination_factory
    assert factory is not None
    assert factory.scenario is scenario
    assert factory.project is state.project is transient


def test_layerwise_request_keeps_compatibility_envelope_but_omits_modal_shunts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _scenario().model_dump(mode="python")
    payload["normalized_project"]["rails"][0]["target_mask"] = (
        {"frequency_hz": 1.0e3, "impedance_ohm": 0.1},
        {"frequency_hz": 1.0e9, "impedance_ohm": 0.1},
    )
    project = build_evaluation_project(
        ScenarioSpec.model_validate(payload), evaluation_rail_id="RAIL_VDD"
    )
    template = compile_project_evaluation_template(project, "RAIL_VDD")
    legacy = build_project_evaluation_request(
        project,
        "RAIL_VDD",
        max_mode_x=3,
        max_mode_y=3,
        template=template,
    )
    research = build_project_evaluation_request(
        project,
        "RAIL_VDD",
        max_mode_x=3,
        max_mode_y=3,
        template=template,
        solver_profile_key=RESEARCH_UNIFORM_ADMITTANCE_PROFILE.key,
        uniform_c00_source=object.__new__(UniformC00SourceModel),
    )
    layerwise_source = object.__new__(LayerwiseUniformSourceModel)
    object.__setattr__(
        layerwise_source,
        "uniform_port_scope",
        "external_device_port",
    )
    unbound_layerwise = build_project_evaluation_request(
        project,
        "RAIL_VDD",
        max_mode_x=3,
        max_mode_y=3,
        template=template,
        solver_profile_key=LAYERWISE_ADMITTANCE_PROFILE.key,
        uniform_c00_source=layerwise_source,
    )
    assert unbound_layerwise.confidence_inputs.model_validity_known is False
    assert unbound_layerwise.confidence_inputs.model_valid_min_hz is None
    assert unbound_layerwise.confidence_inputs.model_valid_max_hz is None

    termination_model = SeriesRLModel(
        model_id="mounted-termination",
        resistance_ohm=0.01,
        inductance_h=1.0e-9,
        valid_min_hz=2.0e3,
        valid_max_hz=4.0e8,
    )
    substrate_manifest = SimpleNamespace(
        clusters=(
            SimpleNamespace(
                source=SimpleNamespace(
                    branches=(SimpleNamespace(model=termination_model),)
                )
            ),
        )
    )
    object.__setattr__(
        layerwise_source,
        "substrate",
        SimpleNamespace(termination_manifest=substrate_manifest),
    )
    captured_confidence_models: list[tuple[object, ...]] = []
    original_shared_model_range = solver_evaluator_module._shared_model_range

    def capture_shared_model_range(
        models: tuple[object, ...],
    ) -> tuple[float | None, float | None, bool]:
        captured_confidence_models.append(models)
        assert original_shared_model_range((termination_model,)) == (
            2.0e3,
            4.0e8,
            True,
        )
        return 2.0e3, 4.0e8, True

    monkeypatch.setattr(
        solver_evaluator_module,
        "_shared_model_range",
        capture_shared_model_range,
    )
    layerwise = build_project_evaluation_request(
        project,
        "RAIL_VDD",
        max_mode_x=3,
        max_mode_y=3,
        template=template,
        solver_profile_key=LAYERWISE_ADMITTANCE_PROFILE.key,
        uniform_c00_source=layerwise_source,
    )

    assert legacy.shunts
    assert research.shunts == legacy.shunts
    assert layerwise.shunts == ()
    assert layerwise.device is legacy.device
    assert layerwise.plane == legacy.plane
    assert (layerwise.max_mode_x, layerwise.max_mode_y) == (3, 3)
    assert any(
        item is termination_model for item in captured_confidence_models[-1]
    )
    assert layerwise.confidence_inputs.model_valid_min_hz == 2.0e3
    assert layerwise.confidence_inputs.model_valid_max_hz == 4.0e8
    assert layerwise.confidence_inputs.model_validity_known is True
    with pytest.raises(EvaluationError, match="double-count physical branches"):
        replace(layerwise, shunts=legacy.shunts)


def _complete_layerwise_view_provenance() -> dict[str, object]:
    profile = evaluation_module.LAYERWISE_ADMITTANCE_PROFILE
    hash_values = {
        name: f"{index:x}" * 64
        for index, name in enumerate(
            evaluation_module._LAYERWISE_PROVENANCE_HASH_FIELDS,
            start=1,
        )
    }
    hash_values["static_compiler_algorithm_sha256"] = (
        evaluation_module.solver_profile_static_identity_sha256(profile)
    )
    return {
        "profile_key": profile.key,
        "profile_badge": profile.badge,
        "source_only": True,
        "powersi_used_for_parameters": False,
        "compiler_algorithm_id": profile.compiler_algorithm_id,
        "compiler_version": evaluation_module.LAYERWISE_COMPILER_VERSION,
        "termination_manifest_required": True,
        **hash_values,
    }


def _layerwise_view() -> EvaluationView:
    profile = evaluation_module.LAYERWISE_ADMITTANCE_PROFILE
    view = _view()
    view.solver_profile_key = profile.key
    view.solver_profile_label = profile.label
    view.solver_profile_badge = profile.badge
    view.solver_provenance = _complete_layerwise_view_provenance()
    return view


def test_layerwise_view_counts_use_bound_termination_state() -> None:
    view = _layerwise_view()
    view.cap_count = 0
    view.model_count = 0
    view.solver_provenance["termination_active_selected_rail_cluster_count"] = 1

    actual = evaluation_module._with_layerwise_mounted_view_counts(
        _scenario(), "RAIL_VDD", view
    )

    assert actual.cap_count == 1
    assert actual.model_count == 1


@pytest.mark.parametrize("provenance_count", [None, 0])
def test_layerwise_view_counts_fail_closed_without_matching_termination_proof(
    provenance_count: int | None,
) -> None:
    view = _layerwise_view()
    if provenance_count is not None:
        view.solver_provenance[
            "termination_active_selected_rail_cluster_count"
        ] = provenance_count

    with pytest.raises(ScenarioEvaluationBuildError) as error:
        evaluation_module._with_layerwise_mounted_view_counts(
            _scenario(), "RAIL_VDD", view
        )

    assert error.value.code == (
        "LAYERWISE_MOUNTED_COUNT_PROVENANCE_MISSING"
        if provenance_count is None
        else "LAYERWISE_MOUNTED_COUNT_MISMATCH"
    )


def test_layerwise_view_rejects_empty_provenance() -> None:
    view = _layerwise_view()
    view.solver_provenance = {}

    with pytest.raises(
        ScenarioEvaluationCacheError,
        match="incomplete source/compiler provenance",
    ):
        evaluation_module._validate_evaluation_view(view)


def test_layerwise_view_accepts_complete_canonical_provenance() -> None:
    evaluation_module._validate_evaluation_view(_layerwise_view())


def test_layerwise_view_accepts_pre_v022_terminal_proof_alias() -> None:
    view = _layerwise_view()
    terminal_hash = view.solver_provenance.pop(
        "terminal_surface_contact_proof_sha256"
    )
    view.solver_provenance["terminal_artwork_proof_sha256"] = terminal_hash

    evaluation_module._validate_evaluation_view(view)


@pytest.mark.parametrize(
    "removed_field",
    evaluation_module._LAYERWISE_PROVENANCE_HASH_FIELDS,
)
def test_layerwise_view_requires_bound_mounted_state_provenance(
    removed_field: str,
) -> None:
    view = _layerwise_view()
    view.solver_provenance.pop(removed_field)

    with pytest.raises(
        ScenarioEvaluationCacheError,
        match="incomplete source/compiler provenance",
    ):
        evaluation_module._validate_evaluation_view(view)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("source_only", False),
        ("powersi_used_for_parameters", True),
        ("compiler_algorithm_id", "wrong-compiler"),
        ("compiler_version", "wrong-version"),
        ("termination_manifest_required", False),
    ),
)
def test_layerwise_view_rejects_incomplete_identity_flags(
    field: str, invalid_value: object
) -> None:
    view = _layerwise_view()
    view.solver_provenance[field] = invalid_value

    with pytest.raises(
        ScenarioEvaluationCacheError,
        match="inconsistent|incomplete source/compiler provenance",
    ):
        evaluation_module._validate_evaluation_view(view)


def test_layerwise_baseline_freezes_source_mounted_unresolved_other_loads() -> None:
    scenario = _scenario()
    unresolved = scenario.decaps[1].model_copy(
        update={
            "enabled": True,
            "source_mounted": True,
            "model_id": "M1",
            "source_model_id": None,
        }
    )
    connection = scenario.connection_analysis.connections["C2"].model_copy(
        update={
            "kind": DecapConnectionKind.UNRESOLVED,
            "reason": "source graph requires cluster-level proof",
        }
    )
    scenario = _scenario_with_connection(
        scenario,
        "C2",
        connection,
        decaps=[scenario.decaps[0], unresolved],
    )

    assert baseline_fallback_model_refdes(
        scenario, ("RAIL_VDD",)
    ) == ()
    assert baseline_fallback_model_refdes(
        scenario,
        ("RAIL_VDD",),
        include_all_source_mounted=True,
    ) == ("C2",)
    legacy = scenario.with_baseline_captures(("RAIL_VDD",))
    expanded = scenario.with_baseline_captures(
        ("RAIL_VDD",),
        include_all_source_mounted=True,
    )
    assert {
        item.refdes for item in legacy.baseline_captures["RAIL_VDD"].model_bindings
    } == {"C1"}
    assert {
        item.refdes
        for item in expanded.baseline_captures["RAIL_VDD"].model_bindings
    } == {"C1", "C2"}

    restored_without_capture = evaluation_module._comparison_original_configuration(
        scenario
    )
    restored_with_capture = evaluation_module._comparison_original_configuration(
        expanded
    )
    unresolved_without_capture = next(
        item for item in restored_without_capture.decaps if item.refdes == "C2"
    )
    unresolved_with_capture = next(
        item for item in restored_with_capture.decaps if item.refdes == "C2"
    )
    assert unresolved_without_capture.enabled is True
    assert unresolved_without_capture.current_rail_id == unresolved.source_rail_id
    assert unresolved_without_capture.model_id == "M1"
    assert unresolved_with_capture.enabled is True
    assert unresolved_with_capture.current_rail_id == unresolved.source_rail_id
    assert unresolved_with_capture.model_id == "M1"


def test_build_evaluation_project_materializes_shared_cluster_once() -> None:
    scenario = _scenario()
    first = scenario.decaps[0]
    second = scenario.decaps[1].model_copy(
        update={
            "enabled": True,
            "source_mounted": True,
            "source_model_id": "M1",
            "model_id": "M1",
        }
    )
    power = ScenarioViaLanding(
        via_id="VP-SHARED",
        net="VDD",
        endpoint_node_id="NP-SHARED",
        x_um=first.pwr_pad.x_um,
        y_um=first.pwr_pad.y_um,
        padstack="VIA",
    )
    ground = ScenarioViaLanding(
        via_id="VG-SHARED",
        net="DGND",
        endpoint_node_id="NG-SHARED",
        x_um=first.gnd_pad.x_um,
        y_um=first.gnd_pad.y_um,
        padstack="VIA",
    )
    connections = {
        item.refdes: ScenarioDecapConnection(
            refdes=item.refdes,
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="CL-SHARED",
            power_vias=(power,),
            ground_vias=(ground,),
        )
        for item in (first, second)
    }
    analysis = SharedPadConnectionAnalysis(
        version=SHARED_PAD_ANALYSIS_VERSION,
        source_sha256=scenario.source.sha256,
        connections=connections,
        clusters=(
            SharedPadCluster(
                cluster_id="CL-SHARED",
                state=SharedPadClusterState.ANCHORED,
                member_refdes=(first.refdes, second.refdes),
                anchor_refdes=(first.refdes, second.refdes),
                dummy_refdes=(),
                power_net="VDD",
                ground_net="DGND",
                layer="TOP",
                power_edges=((first.refdes, second.refdes),),
                ground_edges=((first.refdes, second.refdes),),
                eligibility={
                    "RAIL_VDD": first.eligibility["RAIL_VDD"]
                },
                via_eligibility={
                    "VP-SHARED": {
                        "RAIL_VDD": first.eligibility["RAIL_VDD"]
                    }
                },
            ),
        ),
    )
    shared = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "decaps": [first, second],
            "connection_analysis": analysis,
        }
    )

    project = build_evaluation_project(shared, evaluation_rail_id="RAIL_VDD")

    assert len(project.shared_pad_clusters) == 1
    cluster = project.shared_pad_clusters[0]
    assert len(cluster.member_slot_ids) == 2
    assert [item.terminal for item in cluster.via_paths] == [
        TerminalKind.PWR,
        TerminalKind.GND,
    ]
    assert cluster.via_paths[0].source_via_id == "VP-SHARED"
    assert len(project.placements) == 2
    assert all(
        item.topology == TopologyKind.SHARED_PAD_CLUSTER
        for item in project.topology_maps
    )
    assert "custom SPD solver assumption" in project.assumptions
    assert any("rectangular solver approximation" in item for item in project.assumptions)

    # The persisted normalized project remains untouched.
    assert scenario.base_project.partitions[0].confirmed is False
    assert any(pin.refdes == "OLD" for pin in scenario.base_project.pins)
    assert scenario.base_project.cap_models[0].inventory == 0


def test_recovered_terminal_paths_use_target_geometry_rl_and_explicit_fallback() -> None:
    scenario = _scenario()
    analysis = scenario.connection_analysis
    assert analysis is not None
    original = analysis.connections["C1"]
    power_segment = ScenarioViaSegment(
        via_id="VP-C1",
        padstack="VIA",
        drill_diameter_um=100.0,
        start_layer="TOP",
        end_layer="PWR1",
        length_um=153.0,
        end_x_um=1_750.0,
        end_y_um=2_250.0,
        padstack_material="COPPER",
    )
    ground_segment = ScenarioViaSegment(
        via_id="VG-C1",
        padstack="VIA",
        drill_diameter_um=75.0,
        start_layer="TOP",
        end_layer="GND1",
        length_um=251.0,
        end_x_um=1_775.0,
        end_y_um=2_275.0,
    )

    def with_evidence(
        landing: ScenarioViaLanding,
        *,
        target_layer: str,
        target_node_id: str,
        segment: ScenarioViaSegment,
    ) -> ScenarioViaLanding:
        return landing.model_copy(
            update={
                "path_evidence": (
                    ScenarioViaPathEvidence(
                        target_layer=target_layer,
                        target_node_id=target_node_id,
                        target_padstack="VIA",
                        target_pad_kind="CIRCLE",
                        target_pad_width_um=160.0,
                        target_pad_height_um=120.0,
                        x_um=segment.end_x_um,
                        y_um=segment.end_y_um,
                        segments=(segment,),
                    ),
                )
            }
        )

    recovered_connection = original.model_copy(
        update={
            "power_vias": (
                with_evidence(
                    original.power_vias[0],
                    target_layer="PWR1",
                    target_node_id="NP-PWR1",
                    segment=power_segment,
                ),
            ),
            "ground_vias": (
                with_evidence(
                    original.ground_vias[0],
                    target_layer="GND1",
                    target_node_id="NG-GND1",
                    segment=ground_segment,
                ),
            ),
        }
    )
    recovered_analysis = analysis.model_copy(
        update={
            "connections": {**analysis.connections, "C1": recovered_connection}
        }
    )
    recovered_scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "connection_analysis": recovered_analysis,
        }
    )

    project = build_evaluation_project(
        recovered_scenario, evaluation_rail_id="RAIL_VDD"
    )

    assert len(project.shared_pad_clusters) == 1
    paths = project.shared_pad_clusters[0].via_paths
    power_path = next(item for item in paths if item.terminal == TerminalKind.PWR)
    ground_path = next(item for item in paths if item.terminal == TerminalKind.GND)
    assert (power_path.x_um, power_path.y_um) == (1_750.0, 2_250.0)
    assert (power_path.landing_pad_width_um, power_path.landing_pad_height_um) == (
        160.0,
        120.0,
    )
    assert power_path.terminal_provenance == "SOURCE_PROVEN_SEGMENT_RL"
    assert ground_path.terminal_provenance == "SOURCE_PROVEN_SEGMENT_RL"
    expected_power_rl = evaluation_module._source_terminal_rl(
        (power_segment,), recovered_scenario.base_project.stackup_layers
    )
    expected_ground_rl = evaluation_module._source_terminal_rl(
        (ground_segment,), recovered_scenario.base_project.stackup_layers
    )
    assert (power_path.terminal_resistance_ohm, power_path.terminal_inductance_h) == pytest.approx(
        expected_power_rl
    )
    assert (ground_path.terminal_resistance_ohm, ground_path.terminal_inductance_h) == pytest.approx(
        expected_ground_rl
    )
    assert power_path.conductor_model == "SOLID_COPPER_FILLED_MICROVIA"
    assert (
        power_path.fill_provenance
        == "USER_CONFIRMED_MLO_COPPER_FILL_ASSUMPTION_WITH_SOURCE_COPPER_AND_GEOMETRY"
    )
    assert ground_path.conductor_model == "HOLLOW_PLATED_BARREL"

    via = recovered_scenario.base_project.via_templates[0]
    fallback = evaluation_module._shared_pad_path_from_landing(
        path_id="FALLBACK",
        terminal=TerminalKind.PWR,
        landing=original.power_vias[0],
        target_layer="PWR1",
        via=via,
    )
    assert fallback.terminal_provenance == "LEGACY_RAIL_TEMPLATE"
    assert not fallback.has_source_landing_geometry
    assert not fallback.has_source_terminal_rl

    sampled_via = via.model_copy(
        update={
            "impedance": [
                ImpedanceSample(
                    frequency_hz=1.0e6,
                    real_ohm=0.01,
                    imag_ohm=0.04,
                ),
                ImpedanceSample(
                    frequency_hz=1.0e8,
                    real_ohm=0.02,
                    imag_ohm=0.40,
                ),
            ]
        }
    )
    sampled_path = evaluation_module._shared_pad_path_from_landing(
        path_id="SAMPLED",
        terminal=TerminalKind.PWR,
        landing=recovered_connection.power_vias[0],
        target_layer="PWR1",
        via=sampled_via,
    )
    assert sampled_path.has_source_landing_geometry
    assert not sampled_path.has_source_terminal_rl
    assert (
        sampled_path.terminal_provenance
        == "SAMPLED_DIFFERENTIAL_TEMPLATE_SYMMETRIC"
    )


def test_isolation_gap_removes_its_power_and_ground_via_paths() -> None:
    scenario = _scenario()
    first = scenario.decaps[0].model_copy(
        update={
            "enabled": False,
            "pad_state": DecapPadState.ISOLATION_GAP,
        }
    )
    second = scenario.decaps[1].model_copy(
        update={
            "enabled": True,
            "source_mounted": True,
            "source_model_id": "M1",
            "model_id": "M1",
        }
    )

    def landing(refdes: str, terminal: str, x_um: float) -> ScenarioViaLanding:
        return ScenarioViaLanding(
            via_id=f"V{terminal}-{refdes}",
            net="VDD" if terminal == "P" else "DGND",
            endpoint_node_id=f"N{terminal}-{refdes}",
            x_um=x_um,
            y_um=2_000.0,
            padstack="VIA",
        )

    connections = {
        item.refdes: ScenarioDecapConnection(
            refdes=item.refdes,
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="CL-GAP",
            power_vias=(landing(item.refdes, "P", item.pwr_pad.x_um),),
            ground_vias=(landing(item.refdes, "G", item.gnd_pad.x_um),),
        )
        for item in (first, second)
    }
    cluster = SharedPadCluster(
        cluster_id="CL-GAP",
        state=SharedPadClusterState.ANCHORED,
        member_refdes=(first.refdes, second.refdes),
        anchor_refdes=(first.refdes, second.refdes),
        dummy_refdes=(),
        power_net="VDD",
        ground_net="DGND",
        layer="TOP",
        power_edges=((first.refdes, second.refdes),),
        ground_edges=((first.refdes, second.refdes),),
        isolation_gap_refdes=(first.refdes, second.refdes),
        via_eligibility={
            f"VP-{item.refdes}": {
                "RAIL_VDD": item.eligibility["RAIL_VDD"]
            }
            for item in (first, second)
        },
    )
    isolated = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "decaps": [first, second],
            "connection_analysis": SharedPadConnectionAnalysis(
                version=SHARED_PAD_ANALYSIS_VERSION,
                source_sha256=scenario.source.sha256,
                connections=connections,
                clusters=(cluster,),
            ),
        }
    )

    project = build_evaluation_project(
        isolated, evaluation_rail_id="RAIL_VDD"
    )
    source_via_ids = {
        path.source_via_id for path in project.shared_pad_clusters[0].via_paths
    }
    assert source_via_ids == {f"VP-{second.refdes}", f"VG-{second.refdes}"}


def test_explicit_rail_build_skips_unrelated_anchored_cluster_derivation(
    monkeypatch,
) -> None:
    scenario = _scenario()
    payload = scenario.model_dump(mode="python")
    project = payload["normalized_project"]
    alternate = deepcopy(project["rails"][0])
    alternate.update(
        {
            "rail_id": "RAIL_ALT",
            "family": "VDD_ALT",
            "domain": "VDD_ALT",
            "net": "VDD_ALT",
        }
    )
    project["rails"].append(alternate)
    next(
        item for item in project["stackup_layers"] if item["name"] == "PWR1"
    )["pwr_nets"].append("VDD_ALT")
    project["pins"].append(
        PinRecord(
            refdes="U1",
            pin="P2",
            net="VDD_ALT",
            x_um=2_000.0,
            y_um=2_000.0,
            kind=PinKind.DEVICE_BUMP,
            terminal=TerminalKind.PWR,
            domain="VDD_ALT",
            site="SITE0",
        ).model_dump(mode="python")
    )
    assert scenario.connection_analysis is not None
    c1, c2 = scenario.decaps
    c1_connection = scenario.connection_analysis.connections["C1"].model_copy(
        update={
            "kind": DecapConnectionKind.SHARED_ANCHOR,
            "cluster_id": "CL-UNRELATED",
            "power_vias": (
                *scenario.connection_analysis.connections["C1"].power_vias,
                *scenario.connection_analysis.connections["C2"].power_vias,
            ),
            "ground_vias": (
                *scenario.connection_analysis.connections["C1"].ground_vias,
                *scenario.connection_analysis.connections["C2"].ground_vias,
            ),
        }
    )
    c2_connection = scenario.connection_analysis.connections["C2"].model_copy(
        update={
            "kind": DecapConnectionKind.SHARED_DUMMY,
            "cluster_id": "CL-UNRELATED",
            "power_vias": (),
            "ground_vias": (),
        }
    )
    payload["connection_analysis"]["connections"]["C1"] = c1_connection.model_dump(
        mode="python"
    )
    payload["connection_analysis"]["connections"]["C2"] = c2_connection.model_dump(
        mode="python"
    )
    payload["connection_analysis"]["clusters"] = (
        SharedPadCluster(
            cluster_id="CL-UNRELATED",
            state=SharedPadClusterState.ANCHORED,
            member_refdes=("C1", "C2"),
            anchor_refdes=("C1",),
            dummy_refdes=("C2",),
            power_net="VDD",
            ground_net="DGND",
            layer="TOP",
            power_edges=(("C1", "C2"),),
            ground_edges=(("C1", "C2"),),
            via_eligibility={
                **{
                    landing.via_id: {"RAIL_VDD": c1.eligibility["RAIL_VDD"]}
                    for landing in c1_connection.power_vias
                },
            },
        ).model_dump(mode="python"),
    )
    unrelated = ScenarioSpec.model_validate(payload)
    monkeypatch.setattr(
        evaluation_module,
        "derive_shared_pad_current_components",
        lambda *_args, **_kwargs: pytest.fail(
            "unrelated anchored cluster must not be derived"
        ),
    )

    built = build_evaluation_project(unrelated, evaluation_rail_id="RAIL_ALT")

    assert built.shared_pad_clusters == []


def test_enabled_dummy_keeps_anchor_physical_via_when_anchor_is_dnp() -> None:
    scenario = _scenario()
    anchor = scenario.decaps[0].model_copy(update={"enabled": False})
    dummy = scenario.decaps[1].model_copy(
        update={
            "enabled": True,
            "source_mounted": True,
            "source_model_id": "M1",
            "model_id": "M1",
        }
    )
    power = ScenarioViaLanding(
        via_id="VP-ANCHOR",
        net="VDD",
        endpoint_node_id="NP-ANCHOR",
        x_um=anchor.pwr_pad.x_um,
        y_um=anchor.pwr_pad.y_um,
        padstack="VIA",
    )
    ground = ScenarioViaLanding(
        via_id="VG-ANCHOR",
        net="DGND",
        endpoint_node_id="NG-ANCHOR",
        x_um=anchor.gnd_pad.x_um,
        y_um=anchor.gnd_pad.y_um,
        padstack="VIA",
    )
    power_2 = power.model_copy(
        update={
            "via_id": "VP-ANCHOR-2",
            "endpoint_node_id": "NP-ANCHOR-2",
            "x_um": power.x_um + 25.0,
        }
    )
    ground_2 = ground.model_copy(
        update={
            "via_id": "VG-ANCHOR-2",
            "endpoint_node_id": "NG-ANCHOR-2",
            "x_um": ground.x_um + 25.0,
        }
    )
    ground_3 = ground.model_copy(
        update={
            "via_id": "VG-ANCHOR-3",
            "endpoint_node_id": "NG-ANCHOR-3",
            "x_um": ground.x_um + 50.0,
        }
    )
    analysis = SharedPadConnectionAnalysis(
        version=SHARED_PAD_ANALYSIS_VERSION,
        source_sha256=scenario.source.sha256,
        connections={
            anchor.refdes: ScenarioDecapConnection(
                refdes=anchor.refdes,
                kind=DecapConnectionKind.SHARED_ANCHOR,
                cluster_id="CL-DUMMY",
                power_vias=(power, power_2),
                ground_vias=(ground, ground_2, ground_3),
            ),
            dummy.refdes: ScenarioDecapConnection(
                refdes=dummy.refdes,
                kind=DecapConnectionKind.SHARED_DUMMY,
                cluster_id="CL-DUMMY",
            ),
        },
        clusters=(
            SharedPadCluster(
                cluster_id="CL-DUMMY",
                state=SharedPadClusterState.ANCHORED,
                member_refdes=(anchor.refdes, dummy.refdes),
                anchor_refdes=(anchor.refdes,),
                dummy_refdes=(dummy.refdes,),
                power_net="VDD",
                ground_net="DGND",
                layer="TOP",
                power_edges=((anchor.refdes, dummy.refdes),),
                ground_edges=((anchor.refdes, dummy.refdes),),
                eligibility={"RAIL_VDD": anchor.eligibility["RAIL_VDD"]},
                via_eligibility={
                    item.via_id: {
                        "RAIL_VDD": anchor.eligibility["RAIL_VDD"]
                    }
                    for item in (power, power_2)
                },
                ),
        ),
    )
    shared = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "decaps": [anchor, dummy],
            "connection_analysis": analysis,
        }
    )

    project = build_evaluation_project(shared, evaluation_rail_id="RAIL_VDD")

    assert [item.slot_id for item in project.placements] == [f"SPDPI:{dummy.refdes}"]
    assert len(project.shared_pad_clusters) == 1
    assert project.shared_pad_clusters[0].member_slot_ids == [
        f"SPDPI:{anchor.refdes}",
        f"SPDPI:{dummy.refdes}",
    ]
    assert [
        (item.terminal, item.source_via_id)
        for item in project.shared_pad_clusters[0].via_paths
    ] == [
        (TerminalKind.PWR, "VP-ANCHOR"),
        (TerminalKind.PWR, "VP-ANCHOR-2"),
        (TerminalKind.GND, "VG-ANCHOR"),
        (TerminalKind.GND, "VG-ANCHOR-2"),
        (TerminalKind.GND, "VG-ANCHOR-3"),
    ]


@pytest.mark.parametrize(
    ("power_count", "ground_count"),
    ((1, 3), (3, 1)),
)
def test_direct_unequal_via_counts_use_coupled_two_terminal_model(
    power_count: int,
    ground_count: int,
) -> None:
    payload = _scenario().model_dump(mode="python")
    connection = payload["connection_analysis"]["connections"]["C1"]
    for terminal, count in (
        ("power_vias", power_count),
        ("ground_vias", ground_count),
    ):
        original = dict(connection[terminal][0])
        landings = [original]
        for index in range(2, count + 1):
            extra = dict(original)
            prefix = "VP" if terminal == "power_vias" else "VG"
            extra.update(
                {
                    "via_id": f"{prefix}-C1-{index}",
                    "endpoint_node_id": f"N-{prefix}-C1-{index}",
                    "x_um": original["x_um"] + 25.0 * index,
                }
            )
            landings.append(extra)
        connection[terminal] = landings
    scenario = ScenarioSpec.model_validate(payload)

    project = build_evaluation_project(scenario, evaluation_rail_id="RAIL_VDD")

    assert len(project.shared_pad_clusters) == 1
    cluster = project.shared_pad_clusters[0]
    assert cluster.cluster_id.endswith("DIRECT:C1")
    assert sum(
        item.terminal == TerminalKind.PWR for item in cluster.via_paths
    ) == power_count
    assert sum(
        item.terminal == TerminalKind.GND for item in cluster.via_paths
    ) == ground_count


def test_exact_batched_shared_pad_limit_allows_real_324_path_shape() -> None:
    payload = _scenario().model_dump(mode="python")
    connection = payload["connection_analysis"]["connections"]["C1"]

    def expanded_landings(key: str, count: int, prefix: str) -> list[dict[str, object]]:
        original = dict(connection[key][0])
        result: list[dict[str, object]] = []
        for index in range(count):
            landing = dict(original)
            landing.update(
                {
                    "via_id": f"{prefix}-{index + 1}",
                    "endpoint_node_id": f"N-{prefix}-{index + 1}",
                    "x_um": float(original["x_um"]) + index,
                }
            )
            result.append(landing)
        return result

    connection["power_vias"] = expanded_landings("power_vias", 162, "VP")
    connection["ground_vias"] = expanded_landings("ground_vias", 162, "VG")
    payload["normalized_project"]["rails"][0]["target_mask"] = (
        {"frequency_hz": 1.0e3, "impedance_ohm": 0.1},
        {"frequency_hz": 1.0e9, "impedance_ohm": 0.1},
    )
    production_shape = ScenarioSpec.model_validate(payload)
    project = build_evaluation_project(
        production_shape, evaluation_rail_id="RAIL_VDD"
    )
    assert len(project.shared_pad_clusters[0].via_paths) == 324
    request = build_project_evaluation_request(
        project,
        "RAIL_VDD",
        max_mode_x=1,
        max_mode_y=1,
    )
    cluster_group = max(request.shunts, key=lambda item: len(item.ports))
    assert len(cluster_group.ports) == 324
    assert cluster_group.network.homogeneous_one_component_via_model() is not None

    overflow_payload = deepcopy(payload)
    overflow_connection = overflow_payload["connection_analysis"]["connections"][
        "C1"
    ]
    overflow_connection["power_vias"] = expanded_landings(
        "power_vias", 257, "VP"
    )
    overflow_connection["ground_vias"] = expanded_landings(
        "ground_vias", 256, "VG"
    )
    overflow = ScenarioSpec.model_validate(overflow_payload)
    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        build_evaluation_project(overflow, evaluation_rail_id="RAIL_VDD")
    assert captured.value.code == "SHARED_PAD_CLUSTER_TOO_LARGE"
    assert "exact-batched supported limit is 512" in str(captured.value)


def test_one_component_shared_pad_source_terminal_models_batch_to_512_paths() -> None:
    payload = _scenario().model_dump(mode="python")
    connection = payload["connection_analysis"]["connections"]["C1"]

    def expanded_landings(key: str, count: int, prefix: str) -> list[dict[str, object]]:
        original = dict(connection[key][0])
        result: list[dict[str, object]] = []
        for index in range(count):
            landing = dict(original)
            landing.update(
                {
                    "via_id": f"{prefix}-{index + 1}",
                    "endpoint_node_id": f"N-{prefix}-{index + 1}",
                    "x_um": float(original["x_um"]) + index,
                }
            )
            result.append(landing)
        return result

    connection["power_vias"] = expanded_landings("power_vias", 64, "VP")
    connection["ground_vias"] = expanded_landings("ground_vias", 64, "VG")
    source_landing = connection["power_vias"][0]
    source_landing["path_evidence"] = (
        {
            "target_layer": "PWR1",
            "target_node_id": "SOURCE-PWR-NODE",
            "target_padstack": "VIA",
            "target_pad_kind": "CIRCLE",
            "target_pad_width_um": 100.0,
            "target_pad_height_um": 100.0,
            "x_um": source_landing["x_um"],
            "y_um": source_landing["y_um"],
            "segments": (
                {
                    "via_id": source_landing["via_id"],
                    "padstack": "VIA",
                    "drill_diameter_um": 100.0,
                    "start_layer": "TOP",
                    "end_layer": "PWR1",
                    "length_um": 153.0,
                    "end_x_um": source_landing["x_um"],
                    "end_y_um": source_landing["y_um"],
                },
            ),
        },
    )
    boundary = ScenarioSpec.model_validate(payload)
    project = build_evaluation_project(boundary, evaluation_rail_id="RAIL_VDD")
    paths = project.shared_pad_clusters[0].via_paths
    assert len(paths) == 128
    assert sum(item.has_source_terminal_rl for item in paths) == 1

    batched_payload = deepcopy(payload)
    batched_connection = batched_payload["connection_analysis"]["connections"][
        "C1"
    ]
    extra_power_landing = dict(batched_connection["power_vias"][-1])
    extra_power_landing.update(
        {
            "via_id": "VP-65",
            "endpoint_node_id": "N-VP-65",
            "x_um": float(extra_power_landing["x_um"]) + 1.0,
        }
    )
    batched_connection["power_vias"].append(extra_power_landing)
    batched = ScenarioSpec.model_validate(batched_payload)
    batched_project = build_evaluation_project(
        batched, evaluation_rail_id="RAIL_VDD"
    )
    assert len(batched_project.shared_pad_clusters[0].via_paths) == 129

    overflow_payload = deepcopy(payload)
    overflow_connection = overflow_payload["connection_analysis"]["connections"][
        "C1"
    ]
    overflow_connection["power_vias"] = expanded_landings(
        "power_vias", 257, "VP"
    )
    overflow_connection["ground_vias"] = expanded_landings(
        "ground_vias", 256, "VG"
    )
    overflow = ScenarioSpec.model_validate(overflow_payload)
    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        build_evaluation_project(overflow, evaluation_rail_id="RAIL_VDD")
    assert captured.value.code == "SHARED_PAD_CLUSTER_TOO_LARGE"
    assert "exact-batched supported limit is 512" in str(captured.value)


def test_split_power_components_require_ground_anchor_per_evaluation_rail() -> None:
    base_payload = _base_project().model_dump(mode="json")
    base_payload["stackup_layers"][2]["pwr_nets"].append("VDD_ALT")
    base_payload["rails"].append(
        RailSpec(
            rail_id="RAIL_ALT",
            family="ALT",
            domain="VDD_ALT",
            net="VDD_ALT",
            site="SITE0",
            pwr_layer="PWR1",
            gnd_layer="GND1",
        ).model_dump(mode="json")
    )
    # This fixture exercises electrical cluster construction, not the spatial
    # partitioner.  Two domains cannot intentionally share one partition cell.
    base_payload["partitions"] = []
    base = ProjectSpec.model_validate(base_payload)
    vdd_eligibility = RailEligibility(
        rail_id="RAIL_VDD",
        net="VDD",
        pwr_layer="PWR1",
        gnd_layer="GND1",
        via_template_id="VT_ALLOWED",
        allowed=True,
    )
    alt_eligibility = RailEligibility(
        rail_id="RAIL_ALT",
        net="VDD_ALT",
        pwr_layer="PWR1",
        gnd_layer="GND1",
        via_template_id="VT_ALLOWED",
        allowed=True,
    )

    def member(refdes: str, rail_id: str, net: str) -> ScenarioDecap:
        payload = _decap(refdes, enabled=True, model_id="M1").model_dump(
            mode="python"
        )
        payload.update(
            {
                "current_rail_id": rail_id,
                "current_net": net,
                "eligibility": {
                    "RAIL_VDD": vdd_eligibility,
                    "RAIL_ALT": alt_eligibility,
                },
            }
        )
        return ScenarioDecap.model_validate(payload)

    members = (
        member("C-A", "RAIL_VDD", "VDD"),
        member("C-B", "RAIL_VDD", "VDD").model_copy(
            update={
                "enabled": False,
                "pad_state": DecapPadState.ISOLATION_GAP,
            }
        ),
        member("C-C", "RAIL_ALT", "VDD_ALT"),
    )
    power_landings = {
        item.refdes: ScenarioViaLanding(
            via_id=f"VP-{item.refdes}",
            net="VDD",
            endpoint_node_id=f"NP-{item.refdes}",
            x_um=item.pwr_pad.x_um,
            y_um=item.pwr_pad.y_um,
            padstack="VIA",
        )
        for item in members
    }
    shared_ground = ScenarioViaLanding(
        via_id="VG-COMMON",
        net="DGND",
        endpoint_node_id="NG-COMMON",
        x_um=members[0].gnd_pad.x_um,
        y_um=members[0].gnd_pad.y_um,
        padstack="VIA",
    )
    connections = {
        item.refdes: ScenarioDecapConnection(
            refdes=item.refdes,
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="CL-SPLIT",
            power_vias=(power_landings[item.refdes],),
            ground_vias=(shared_ground,) if item is members[0] else (),
        )
        for item in members
    }
    cluster = SharedPadCluster(
        cluster_id="CL-SPLIT",
        state=SharedPadClusterState.ANCHORED,
        member_refdes=tuple(item.refdes for item in members),
        anchor_refdes=tuple(item.refdes for item in members),
        dummy_refdes=(),
        power_net="VDD",
        ground_net="DGND",
        layer="TOP",
        power_edges=(("C-A", "C-B"), ("C-B", "C-C")),
        # V5 preserves a separate C-C source GND supernode.  It is permitted
        # to deserialize, but its missing Via anchor must block only C-C's
        # selected-rail materialization below.
        ground_edges=(("C-A", "C-B"),),
        isolation_gap_refdes=("C-A", "C-B", "C-C"),
        via_eligibility={
            "VP-C-A": {"RAIL_VDD": vdd_eligibility},
            "VP-C-B": {"RAIL_VDD": vdd_eligibility},
            "VP-C-C": {"RAIL_ALT": alt_eligibility},
        },
    )
    scenario = ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixtures/fixture.spd",
            name="fixture.spd",
            size_bytes=123,
            sha256="b" * 64,
        ),
        normalized_project=base,
        decaps=members,
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="b" * 64,
            connections=connections,
            clusters=(cluster,),
        ),
    )

    with pytest.raises(ValidationError, match="legacy resolved shared-pad cluster GND"):
        SharedPadConnectionAnalysis(
            version="DIRECT_TOP_COPPER_PATH_V4",
            source_sha256="b" * 64,
            connections=connections,
            clusters=(cluster,),
        )

    vdd_project = build_evaluation_project(
        scenario, evaluation_rail_id="RAIL_VDD"
    )

    assert len(vdd_project.shared_pad_clusters) == 1
    vdd_cluster = vdd_project.shared_pad_clusters[0]
    assert [
        component.member_slot_ids for component in vdd_cluster.power_components
    ] == [["SPDPI:C-A"]]
    assert sum(
        path.terminal == TerminalKind.GND for path in vdd_cluster.via_paths
    ) == 1
    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        build_evaluation_project(scenario, evaluation_rail_id="RAIL_ALT")
    assert captured.value.code == "SHARED_PAD_CLUSTER_TERMINAL_MISSING"

    # V5 does not borrow a GND Via across a physical isolation gap.  Once the
    # alternate rail's independently derived GND component has source evidence,
    # its one-rail evaluation materializes normally.
    alt_ground = ScenarioViaLanding(
        via_id="VG-ALT",
        net="DGND",
        endpoint_node_id="NG-ALT",
        x_um=members[2].gnd_pad.x_um,
        y_um=members[2].gnd_pad.y_um,
        padstack="VIA",
    )
    anchored_connections = dict(connections)
    anchored_connections[members[2].refdes] = connections[members[2].refdes].model_copy(
        update={"ground_vias": (alt_ground,)}
    )
    anchored = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "connection_analysis": scenario.connection_analysis.model_copy(
                update={"connections": anchored_connections}
            ),
        }
    )
    alt_project = build_evaluation_project(
        anchored, evaluation_rail_id="RAIL_ALT"
    )
    assert len(alt_project.shared_pad_clusters) == 1
    alt_cluster = alt_project.shared_pad_clusters[0]
    assert [
        component.member_slot_ids for component in alt_cluster.power_components
    ] == [["SPDPI:C-C"]]
    assert sum(
        path.terminal == TerminalKind.GND for path in alt_cluster.via_paths
    ) == 1
    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        build_evaluation_project(scenario)
    assert captured.value.code == "EVALUATION_RAIL_REQUIRED_FOR_SPLIT_CLUSTER"


def test_unresolved_shared_cluster_blocks_evaluation_fail_closed() -> None:
    scenario = _scenario()
    first, second = scenario.decaps
    reason = "shared top-pad evidence is ambiguous"
    analysis = SharedPadConnectionAnalysis(
        version=SHARED_PAD_ANALYSIS_VERSION,
        source_sha256=scenario.source.sha256,
        connections={
            item.refdes: ScenarioDecapConnection(
                refdes=item.refdes,
                kind=DecapConnectionKind.UNRESOLVED,
                cluster_id="CL-UNRESOLVED",
                reason=reason,
            )
            for item in (first, second)
        },
        clusters=(
            SharedPadCluster(
                cluster_id="CL-UNRESOLVED",
                state=SharedPadClusterState.UNRESOLVED,
                member_refdes=(first.refdes, second.refdes),
                anchor_refdes=(first.refdes,),
                dummy_refdes=(second.refdes,),
                power_net="VDD",
                ground_net="DGND",
                layer="TOP",
                reason=reason,
            ),
        ),
    )
    unresolved = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "connection_analysis": analysis,
        }
    )

    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        build_evaluation_project(unresolved, evaluation_rail_id="RAIL_VDD")

    assert captured.value.code == "SHARED_PAD_CLUSTER_UNRESOLVED"


def test_unresolved_direct_decap_blocks_evaluation_fail_closed() -> None:
    scenario = _scenario()
    payload = scenario.model_dump(mode="python")
    connection = payload["connection_analysis"]["connections"]["C1"]
    connection.update(
        {
            "kind": DecapConnectionKind.UNRESOLVED,
            "power_vias": (),
            "ground_vias": (),
            "reason": "exact PWR landing cannot be proven",
        }
    )
    unresolved = ScenarioSpec.model_validate(payload)

    with pytest.raises(ScenarioEvaluationBuildError) as captured:
        build_evaluation_project(unresolved, evaluation_rail_id="RAIL_VDD")

    assert captured.value.code == "DECAP_CONNECTION_UNRESOLVED"


def test_layerwise_defers_unresolved_classification_to_exact_termination_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario()
    payload = scenario.model_dump(mode="python")
    payload["connection_analysis"]["connections"]["C1"].update(
        {
            "kind": DecapConnectionKind.UNRESOLVED,
            "power_vias": (),
            "ground_vias": (),
            "reason": "legacy plane-under-landing classifier is inconclusive",
        }
    )
    unresolved = ScenarioSpec.model_validate(payload)

    legacy = preflight_evaluation_connectivity(unresolved, ("RAIL_VDD",))
    layerwise = preflight_evaluation_connectivity(
        unresolved,
        ("RAIL_VDD",),
        solver_profile="layerwise_admittance_v1",
    )

    assert [(item.refdes, item.kind) for item in legacy.blockers] == [
        ("C1", DecapConnectionKind.UNRESOLVED)
    ]
    assert layerwise.is_clear

    # The profile project shell deliberately owns no modal decap placements.
    # Production preflight next compiles the exact endpoint-island manifest;
    # this fixture has no retained artwork and therefore tests only the clean
    # ownership boundary, not permission to run an unproven termination.
    monkeypatch.setattr(
        evaluation_module,
        "_layerwise_profile_project",
        lambda _scenario, project, _rail_ids: project,
    )
    project = build_evaluation_project(
        unresolved,
        evaluation_rail_id="RAIL_VDD",
        solver_profile="layerwise_admittance_v1",
    )

    assert not project.placements
    assert not project.topology_maps
    assert not project.shared_pad_clusters
    assert project.pins
    assert all(pin.kind == PinKind.DEVICE_BUMP for pin in project.pins)


def test_legacy_v3_connectivity_loads_but_blocks_all_evaluation_paths(
    monkeypatch,
) -> None:
    payload = _scenario().model_dump(mode="python")
    payload["connection_analysis"]["version"] = "DIRECT_TOP_COPPER_PATH_V3"
    legacy = ScenarioSpec.model_validate(payload)

    with pytest.raises(ScenarioEvaluationBuildError) as preflight:
        preflight_evaluation_connectivity(legacy, ("RAIL_VDD",))
    assert preflight.value.code == "CONNECTION_ANALYSIS_UPGRADE_REQUIRED"
    assert "V5 finite-pad/ordered-boolean" in str(preflight.value)

    with pytest.raises(ScenarioEvaluationBuildError) as direct:
        build_evaluation_project(legacy, evaluation_rail_id="RAIL_VDD")
    assert direct.value.code == "CONNECTION_ANALYSIS_UPGRADE_REQUIRED"

    monkeypatch.setattr(
        ScenarioSpec,
        "with_baseline_captures",
        lambda *_args, **_kwargs: pytest.fail("baseline capture must not start"),
    )
    with pytest.raises(ScenarioEvaluationBuildError) as batch:
        evaluate_comparison_batch(legacy, ("RAIL_VDD",))
    assert batch.value.code == "CONNECTION_ANALYSIS_UPGRADE_REQUIRED"


def test_connectivity_preflight_aggregates_all_selected_rail_blockers(
    monkeypatch,
) -> None:
    scenario = _scenario()
    payload = scenario.model_dump(mode="python")
    project = payload["normalized_project"]
    project["stackup_layers"][2]["pwr_nets"].append("VDD_ALT")
    project["rails"].append(
        RailSpec(
            rail_id="RAIL_ALT",
            family="VDD",
            domain="VDD",
            net="VDD_ALT",
            site="SITE0",
            pwr_layer="PWR1",
            gnd_layer="GND1",
        ).model_dump(mode="python")
    )
    c2 = next(item for item in payload["decaps"] if item["refdes"] == "C2")
    c2.update(
        {
            "source_net": "VDD_ALT",
            "current_net": "VDD_ALT",
            "source_rail_id": "RAIL_ALT",
            "current_rail_id": "RAIL_ALT",
            "source_model_id": "M1",
            "model_id": "M1",
            "enabled": True,
            "source_mounted": True,
            "eligibility": {
                "RAIL_ALT": RailEligibility(
                    rail_id="RAIL_ALT",
                    net="VDD_ALT",
                    pwr_layer="PWR1",
                    gnd_layer="GND1",
                    via_template_id="VT_ALLOWED",
                    allowed=True,
                ).model_dump(mode="python")
            },
        }
    )
    for refdes, kind, reason in (
        ("C1", DecapConnectionKind.UNRESOLVED, "PWR landing not proven"),
        ("C2", DecapConnectionKind.OUT_OF_SCOPE, "component is outside the TOP scope"),
    ):
        payload["connection_analysis"]["connections"][refdes].update(
            {
                "kind": kind,
                "power_vias": (),
                "ground_vias": (),
                "reason": reason,
            }
        )
    blocked = ScenarioSpec.model_validate(payload)

    preflight = preflight_evaluation_connectivity(
        blocked, ("rail_alt", "RAIL_VDD")
    )

    assert isinstance(preflight, EvaluationConnectivityPreflight)
    assert preflight.rail_ids == ("RAIL_VDD", "RAIL_ALT")
    assert [(item.rail_id, item.refdes, item.kind.value) for item in preflight.blockers] == [
        ("RAIL_VDD", "C1", "UNRESOLVED"),
        ("RAIL_ALT", "C2", "OUT_OF_SCOPE"),
    ]
    assert "RAIL_VDD" in preflight.message()
    assert "RAIL_ALT" in preflight.message()
    assert "2 of 2 selected PWR rail(s)" in preflight.message()

    selected_only = preflight_evaluation_connectivity(blocked, ("RAIL_VDD",))
    assert [item.refdes for item in selected_only.blockers] == ["C1"]

    monkeypatch.setattr(
        ScenarioSpec,
        "with_baseline_captures",
        lambda *_args, **_kwargs: pytest.fail("baseline capture must not start"),
    )
    with pytest.raises(ScenarioEvaluationPreflightError) as captured:
        evaluate_comparison_batch(blocked, ("RAIL_VDD", "RAIL_ALT"))
    assert captured.value.code == "EVALUATION_CONNECTIVITY_BLOCKED"
    assert captured.value.preflight == preflight


def test_geometry_preflight_aggregates_power_and_ground_terminal_overruns() -> None:
    scenario = _scenario()
    assert scenario.connection_analysis is not None
    c1_connection = scenario.connection_analysis.connections["C1"]
    outside_power = c1_connection.power_vias[0].model_copy(
        update={"x_um": -100.0}
    )
    scenario = _scenario_with_connection(
        scenario,
        "C1",
        c1_connection.model_copy(update={"power_vias": (outside_power,)}),
    )
    assert scenario.connection_analysis is not None
    c2_connection = scenario.connection_analysis.connections["C2"]
    outside_ground = c2_connection.ground_vias[0].model_copy(
        update={
            "via_id": "VG-C2-OUTSIDE",
            "endpoint_node_id": "NG-C2-OUTSIDE",
            "x_um": 10_050.0,
        }
    )
    enabled_c2 = scenario.decaps[1].model_copy(
        update={
            "enabled": True,
            "source_mounted": True,
            "source_model_id": "M1",
            "model_id": "M1",
        }
    )
    scenario = _scenario_with_connection(
        scenario,
        "C2",
        c2_connection.model_copy(
            update={
                "ground_vias": (*c2_connection.ground_vias, outside_ground),
            }
        ),
        decaps=[scenario.decaps[0], enabled_c2],
    )

    preflight = preflight_evaluation_connectivity(scenario, ("RAIL_VDD",))

    assert {(item.refdes, item.terminal) for item in preflight.blockers} == {
        ("C1", TerminalKind.PWR),
        ("C2", TerminalKind.GND),
    }
    power = next(item for item in preflight.blockers if item.terminal == TerminalKind.PWR)
    ground = next(item for item in preflight.blockers if item.terminal == TerminalKind.GND)
    assert power.overrun_left_um == pytest.approx(150.0)
    assert ground.overrun_right_um == pytest.approx(100.0)
    assert power.source_via_id == "VP-C1"
    assert ground.path_id == "SPDPI:CLUSTER:DIRECT:C2:GND:1:2"
    assert "clamp, expand, or drop" in preflight.message()


def test_geometry_preflight_checks_complete_port_footprint_at_plane_edge() -> None:
    scenario = _scenario()
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections["C1"]
    edge_crossing = connection.power_vias[0].model_copy(update={"x_um": 25.0})
    scenario = _scenario_with_connection(
        scenario,
        "C1",
        connection.model_copy(update={"power_vias": (edge_crossing,)}),
    )

    preflight = preflight_evaluation_connectivity(scenario, ("RAIL_VDD",))

    assert len(preflight.blockers) == 1
    blocker = preflight.blockers[0]
    assert blocker.center_x_um == 25.0
    assert blocker.width_um == 100.0
    assert blocker.overrun_left_um == pytest.approx(25.0)


def test_geometry_preflight_keeps_disabled_multi_via_physical_cluster_fail_closed() -> None:
    scenario = _scenario()
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections["C2"]
    outside_ground = connection.ground_vias[0].model_copy(
        update={
            "via_id": "VG-C2-PHYSICAL",
            "endpoint_node_id": "NG-C2-PHYSICAL",
            "x_um": 10_050.0,
        }
    )
    scenario = _scenario_with_connection(
        scenario,
        "C2",
        connection.model_copy(
            update={"ground_vias": (*connection.ground_vias, outside_ground)}
        ),
    )

    preflight = preflight_evaluation_connectivity(scenario, ("RAIL_VDD",))

    assert len(preflight.blockers) == 1
    assert preflight.blockers[0].refdes == "C2"
    assert preflight.blockers[0].terminal == TerminalKind.GND
    assert preflight.blockers[0].source_via_id == "VG-C2-PHYSICAL"


def test_geometry_preflight_uses_source_target_landing_not_top_via_coordinate() -> None:
    scenario = _scenario()
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections["C1"]
    segment = ScenarioViaSegment(
        via_id="VP-C1",
        padstack="VIA",
        drill_diameter_um=75.0,
        start_layer="TOP",
        end_layer="PWR1",
        length_um=150.0,
        end_x_um=100.0,
        end_y_um=2_000.0,
    )
    source_target = ScenarioViaPathEvidence(
        target_layer="PWR1",
        target_node_id="NP-C1-PWR1",
        target_padstack="VIA",
        target_pad_kind="CIRCLE",
        target_pad_width_um=100.0,
        target_pad_height_um=100.0,
        x_um=100.0,
        y_um=2_000.0,
        segments=(segment,),
    )
    physical_outside = connection.power_vias[0].model_copy(
        update={
            "x_um": -500.0,
            "path_evidence": (source_target,),
        }
    )
    scenario = _scenario_with_connection(
        scenario,
        "C1",
        connection.model_copy(update={"power_vias": (physical_outside,)}),
    )

    preflight = preflight_evaluation_connectivity(scenario, ("RAIL_VDD",))
    project = build_evaluation_project(scenario, evaluation_rail_id="RAIL_VDD")

    assert preflight.is_clear
    power_path = next(
        path
        for path in project.shared_pad_clusters[0].via_paths
        if path.terminal == TerminalKind.PWR
    )
    assert power_path.x_um == 100.0
    assert power_path.landing_pad_width_um == 100.0


def test_geometry_preflight_blocks_before_baseline_or_solver_worker(monkeypatch) -> None:
    scenario = _scenario()
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections["C1"]
    outside = connection.power_vias[0].model_copy(update={"x_um": -100.0})
    scenario = _scenario_with_connection(
        scenario,
        "C1",
        connection.model_copy(update={"power_vias": (outside,)}),
    )
    monkeypatch.setattr(
        ScenarioSpec,
        "with_baseline_captures",
        lambda *_args, **_kwargs: pytest.fail("baseline worker must not start"),
    )
    monkeypatch.setattr(
        evaluation_module,
        "evaluate_scenario",
        lambda *_args, **_kwargs: pytest.fail("solver worker must not start"),
    )

    with pytest.raises(ScenarioEvaluationPreflightError) as captured:
        evaluate_comparison_batch(scenario, ("RAIL_VDD",))

    assert captured.value.preflight.blockers[0].terminal == TerminalKind.PWR


def test_comparison_preflight_catches_original_reenabled_direct_terminal(
    monkeypatch,
) -> None:
    scenario = _scenario()
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections["C1"]
    outside_source = connection.power_vias[0].model_copy(update={"x_um": -100.0})
    current_disabled = scenario.decaps[0].model_copy(
        update={"enabled": False, "model_id": None}
    )
    scenario = _scenario_with_connection(
        scenario,
        "C1",
        connection.model_copy(update={"power_vias": (outside_source,)}),
        decaps=[current_disabled, *scenario.decaps[1:]],
    )

    assert preflight_evaluation_connectivity(scenario, ("RAIL_VDD",)).is_clear
    comparison = preflight_evaluation_comparison(scenario, ("RAIL_VDD",))

    assert len(comparison.blockers) == 1
    blocker = comparison.blockers[0]
    assert blocker.configuration == evaluation_module.EvaluationRole.BASELINE
    assert blocker.refdes == "C1"
    assert blocker.terminal == TerminalKind.PWR
    assert "Original DIRECT" in comparison.message()
    monkeypatch.setattr(
        ScenarioSpec,
        "with_baseline_captures",
        lambda *_args, **_kwargs: pytest.fail("baseline capture must not start"),
    )
    with pytest.raises(ScenarioEvaluationPreflightError) as captured:
        evaluate_comparison_batch(scenario, ("RAIL_VDD",))
    assert captured.value.preflight == comparison


@pytest.mark.parametrize(
    ("model_id", "reason_fragment"),
    (
        (None, "electrical model is missing"),
        ("UNKNOWN_MODEL", "is absent from the model library"),
    ),
)
def test_comparison_preflight_catches_tuned_enabled_model_failure_before_capture(
    model_id: str | None,
    reason_fragment: str,
    monkeypatch,
) -> None:
    scenario = _scenario()
    tuned = scenario.decaps[0].model_copy(update={"model_id": model_id})
    scenario = scenario.model_copy(update={"decaps": (tuned, *scenario.decaps[1:])})

    comparison = preflight_evaluation_comparison(scenario, ("RAIL_VDD",))

    assert len(comparison.blockers) == 1
    blocker = comparison.blockers[0]
    assert blocker.configuration == evaluation_module.EvaluationRole.TUNED
    assert blocker.rail_id == "RAIL_VDD"
    assert blocker.refdes == "C1"
    assert reason_fragment in blocker.reason
    assert "Tuned DIRECT" in comparison.message()
    monkeypatch.setattr(
        ScenarioSpec,
        "with_baseline_captures",
        lambda *_args, **_kwargs: pytest.fail("baseline capture must not start"),
    )
    with pytest.raises(ScenarioEvaluationPreflightError) as captured:
        evaluate_comparison_batch(scenario, ("RAIL_VDD",))
    assert captured.value.preflight == comparison


def test_comparison_preflight_converts_state_specific_dry_build_failure(
    monkeypatch,
) -> None:
    scenario = _scenario()
    current_disabled = scenario.decaps[0].model_copy(
        update={"enabled": False, "model_id": None}
    )
    scenario = scenario.model_copy(
        update={"decaps": (current_disabled, *scenario.decaps[1:])}
    )
    calls: list[bool] = []

    def dry_build(candidate, *, evaluation_rail_id=None, **_kwargs):
        source_mounted_is_enabled = candidate.decaps[0].enabled
        calls.append(source_mounted_is_enabled)
        if source_mounted_is_enabled:
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_CLUSTER_TOO_LARGE",
                "source cluster exceeds the exact-batched path limit",
                refdes="C1",
            )
        return candidate.base_project

    monkeypatch.setattr(evaluation_module, "build_evaluation_project", dry_build)

    comparison = preflight_evaluation_comparison(scenario, ("RAIL_VDD",))

    assert calls == [False, True]
    assert len(comparison.blockers) == 1
    blocker = comparison.blockers[0]
    assert blocker.configuration == evaluation_module.EvaluationRole.BASELINE
    assert blocker.rail_id == "RAIL_VDD"
    assert blocker.refdes == "C1"
    assert "SHARED_PAD_CLUSTER_TOO_LARGE" in blocker.reason


def test_layerwise_builder_preflight_compiles_one_board_binding_and_proves_92_rails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from spd_decap_pi._core.solver import layerwise_network as layerwise_network_module
    from spd_decap_pi import layerwise_termination_adapter as termination_adapter

    rails = tuple(f"R{index:02d}" for index in range(92))
    scenario = SimpleNamespace(
        design_fingerprint="a" * 64,
        connection_analysis=SimpleNamespace(connections={}),
    )
    project = SimpleNamespace()
    substrate = object()
    binding = object()
    project_builds: list[str] = []
    source_proofs: list[str] = []
    bound_rails: list[tuple[str, object, bool]] = []
    factory_scenarios: list[object] = []
    factory_calls: list[tuple[object, object]] = []

    def build_project(
        _scenario: object, *, evaluation_rail_id: str, **_kwargs: object
    ) -> object:
        project_builds.append(evaluation_rail_id)
        return SimpleNamespace(rail_id=evaluation_rail_id)

    def compile_template(
        candidate: object,
        rail_id: str,
        *,
        terminal_complete_external_input: bool,
    ) -> object:
        assert terminal_complete_external_input is True
        assert candidate.rail_id == rail_id
        return SimpleNamespace(rail_id=rail_id, cap_models={"M1": object()})

    class SourceModel:
        def __init__(self, rail_id: str) -> None:
            self.rail_id = rail_id
            self.substrate = substrate

        def with_termination_manifest(
            self, observed: object, *, required: bool
        ) -> "SourceModel":
            bound_rails.append((self.rail_id, observed, required))
            return self

    def build_source(
        _candidate: object,
        _attachments: object,
        rail_id: str,
        _template: object,
        **_kwargs: object,
    ) -> SourceModel:
        source_proofs.append(rail_id)
        return SourceModel(rail_id)

    def factory(
        *, scenario: object, project: object, attachments: object
    ) -> object:
        del project, attachments
        factory_scenarios.append(scenario)

        def compile_binding(observed_substrate: object, template: object) -> object:
            factory_calls.append((observed_substrate, template))
            return binding

        return compile_binding

    monkeypatch.setattr(evaluation_module, "build_evaluation_project", build_project)
    monkeypatch.setattr(
        evaluation_module, "compile_project_evaluation_template", compile_template
    )
    monkeypatch.setattr(
        layerwise_network_module, "build_layerwise_uniform_source_model", build_source
    )
    monkeypatch.setattr(
        termination_adapter, "LayerwiseScenarioTerminationFactory", factory
    )

    blockers = evaluation_module._builder_preflight_blockers(
        scenario,
        rails,
        (),
        project=project,
        solver_profile=LAYERWISE_ADMITTANCE_PROFILE.key,
        attachments={"proof": b"bytes"},
        stage_count=len(rails),
    )

    assert blockers == ()
    assert project_builds == list(rails)
    assert source_proofs == list(rails)
    assert factory_scenarios == [scenario]
    assert len(factory_calls) == 1
    assert factory_calls[0][0] is substrate
    assert bound_rails == [(rail_id, binding, True) for rail_id in rails]


def test_layerwise_builder_preflight_caches_only_board_binding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from spd_decap_pi._core.solver import layerwise_network as layerwise_network_module
    from spd_decap_pi import layerwise_termination_adapter as termination_adapter

    rails = ("R_BAD_PORT", "R_BOARD_FIRST", "R_BOARD_REPLAY")
    scenario = SimpleNamespace(
        design_fingerprint="b" * 64,
        connection_analysis=SimpleNamespace(connections={}),
    )
    substrate = object()
    source_attempts: list[str] = []
    factory_calls = 0

    monkeypatch.setattr(
        evaluation_module,
        "build_evaluation_project",
        lambda _scenario, *, evaluation_rail_id, **_kwargs: SimpleNamespace(
            rail_id=evaluation_rail_id
        ),
    )
    def compile_template(
        _candidate: object,
        rail_id: str,
        *,
        terminal_complete_external_input: bool,
    ) -> object:
        assert terminal_complete_external_input is True
        return SimpleNamespace(rail_id=rail_id, cap_models={"M1": object()})

    monkeypatch.setattr(
        evaluation_module,
        "compile_project_evaluation_template",
        compile_template,
    )

    class SourceModel:
        def __init__(self, rail_id: str) -> None:
            self.rail_id = rail_id
            self.substrate = substrate

        def with_termination_manifest(
            self, _manifest: object, *, required: bool
        ) -> "SourceModel":
            pytest.fail("a failed board binding must never be attached")

    def build_source(
        _candidate: object,
        _attachments: object,
        rail_id: str,
        _template: object,
        **_kwargs: object,
    ) -> SourceModel:
        source_attempts.append(rail_id)
        if rail_id == "R_BAD_PORT":
            raise LayerwiseNetworkUnavailable(
                "RAIL_PORT_MISSING", "selected rail has no external Device port"
            )
        return SourceModel(rail_id)

    def factory(**_kwargs: object) -> object:
        def fail_binding(_substrate: object, _template: object) -> object:
            nonlocal factory_calls
            factory_calls += 1
            raise LayerSurfaceTerminationError(
                "BOARD_BINDING_FAILED",
                "one mounted board termination has no exact endpoint",
            )

        return fail_binding

    monkeypatch.setattr(
        layerwise_network_module, "build_layerwise_uniform_source_model", build_source
    )
    monkeypatch.setattr(
        termination_adapter, "LayerwiseScenarioTerminationFactory", factory
    )

    blockers = evaluation_module._builder_preflight_blockers(
        scenario,
        rails,
        (),
        project=SimpleNamespace(),
        solver_profile=LAYERWISE_ADMITTANCE_PROFILE.key,
        attachments={},
        stage_count=len(rails),
    )

    assert source_attempts == list(rails)
    assert factory_calls == 1
    assert [item.rail_id for item in blockers] == list(rails)
    by_rail = {item.rail_id: item for item in blockers}
    assert "RAIL_PORT_MISSING" in by_rail["R_BAD_PORT"].reason
    assert "BOARD_BINDING_FAILED" in by_rail["R_BOARD_FIRST"].reason
    assert "BOARD_BINDING_FAILED" in by_rail["R_BOARD_REPLAY"].reason


def test_layerwise_board_binding_is_separate_for_tuned_and_original_builder_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from spd_decap_pi._core.solver import layerwise_network as layerwise_network_module
    from spd_decap_pi import layerwise_termination_adapter as termination_adapter

    rails = ("R1", "R2")
    tuned = SimpleNamespace(
        design_fingerprint="c" * 64,
        connection_analysis=SimpleNamespace(connections={}),
    )
    original = SimpleNamespace(
        design_fingerprint="d" * 64,
        connection_analysis=SimpleNamespace(connections={}),
    )
    bindings: dict[str, object] = {}
    factory_calls: list[str] = []
    attached: list[tuple[str, str, object]] = []
    substrate = object()

    monkeypatch.setattr(
        evaluation_module,
        "build_evaluation_project",
        lambda scenario, *, evaluation_rail_id, **_kwargs: SimpleNamespace(
            scenario=scenario, rail_id=evaluation_rail_id
        ),
    )
    def compile_template(
        candidate: object,
        rail_id: str,
        *,
        terminal_complete_external_input: bool,
    ) -> object:
        assert terminal_complete_external_input is True
        return SimpleNamespace(
            scenario=candidate.scenario,
            rail_id=rail_id,
            cap_models={"M1": object()},
        )

    monkeypatch.setattr(
        evaluation_module,
        "compile_project_evaluation_template",
        compile_template,
    )

    class SourceModel:
        def __init__(self, scenario: object, rail_id: str) -> None:
            self.scenario = scenario
            self.rail_id = rail_id
            self.substrate = substrate

        def with_termination_manifest(
            self, manifest: object, *, required: bool
        ) -> "SourceModel":
            assert required
            attached.append(
                (self.scenario.design_fingerprint, self.rail_id, manifest)
            )
            return self

    monkeypatch.setattr(
        layerwise_network_module,
        "build_layerwise_uniform_source_model",
        lambda candidate, _attachments, rail_id, _template, **_kwargs: SourceModel(
            candidate.scenario, rail_id
        ),
    )

    def factory(*, scenario: object, **_kwargs: object) -> object:
        fingerprint = scenario.design_fingerprint
        factory_calls.append(fingerprint)
        binding = bindings.setdefault(fingerprint, object())
        return lambda _substrate, _template: binding

    monkeypatch.setattr(
        termination_adapter, "LayerwiseScenarioTerminationFactory", factory
    )

    for scenario in (tuned, original):
        assert evaluation_module._builder_preflight_blockers(
            scenario,
            rails,
            (),
            project=SimpleNamespace(),
            solver_profile=LAYERWISE_ADMITTANCE_PROFILE.key,
            attachments={},
            stage_count=len(rails),
        ) == ()

    assert factory_calls == [tuned.design_fingerprint, original.design_fingerprint]
    assert [item[:2] for item in attached] == [
        (tuned.design_fingerprint, "R1"),
        (tuned.design_fingerprint, "R2"),
        (original.design_fingerprint, "R1"),
        (original.design_fingerprint, "R2"),
    ]
    assert all(
        manifest is bindings[fingerprint]
        for fingerprint, _rail_id, manifest in attached
    )


def test_geometry_preflight_keeps_bare_vqps_style_rail_clear() -> None:
    scenario = _scenario()
    payload = scenario.model_dump(mode="python")
    payload["decaps"] = []
    payload["connection_analysis"]["connections"] = {}
    bare = ScenarioSpec.model_validate(payload)

    assert preflight_evaluation_connectivity(bare, ("RAIL_VDD",)).is_clear
    assert preflight_evaluation_connectivity(
        bare,
        ("RAIL_VDD",),
        solver_profile="layerwise_admittance_v1",
    ).is_clear


def test_layerwise_geometry_bypass_still_fails_closed_on_pre_v4_source() -> None:
    scenario = _scenario()
    assert scenario.connection_analysis is not None
    connection = scenario.connection_analysis.connections["C1"]
    outside = connection.power_vias[0].model_copy(update={"x_um": -100.0})
    scenario = _scenario_with_connection(
        scenario,
        "C1",
        connection.model_copy(update={"power_vias": (outside,)}),
    )

    base = scenario.base_project
    metadata = {
        **base.metadata,
        "spd_import": {
            **base.metadata["spd_import"],
            "plane_geometries": [
                {
                    "layer": "PWR1",
                    "net": "VDD",
                    "bbox_um": [0.0, 10_000.0, 0.0, 8_000.0],
                },
                {
                    "layer": "GND1",
                    "net": "DGND",
                    "bbox_um": [0.0, 10_000.0, 0.0, 8_000.0],
                },
            ],
        },
    }
    project = base.model_copy(update={"metadata": metadata})
    scenario = scenario.model_copy(
        update={"normalized_project": project.model_dump(mode="json")}
    )

    legacy = preflight_evaluation_connectivity(scenario, ("RAIL_VDD",))
    assert any(
        "TERMINAL_OUTSIDE_SELECTED_PLANE" in item.reason
        for item in legacy.blockers
    )

    layerwise = preflight_evaluation_comparison(
        scenario,
        ("RAIL_VDD",),
        solver_profile=LAYERWISE_ADMITTANCE_PROFILE.key,
        attachments={},
    )

    assert not layerwise.is_clear
    assert all(
        "TERMINAL_OUTSIDE_SELECTED_PLANE" not in item.reason
        for item in layerwise.blockers
    )
    assert any(
        "TERMINAL_COMPLETE_REIMPORT_REQUIRED" in item.reason
        for item in layerwise.blockers
    )


def test_comparison_preflight_blocks_bare_legacy_scenario_without_analysis(
    monkeypatch,
) -> None:
    payload = _scenario().model_dump(mode="python")
    payload["decaps"] = []
    payload["connection_analysis"] = None
    legacy = ScenarioSpec.model_validate(payload)
    monkeypatch.setattr(
        evaluation_module,
        "build_evaluation_project",
        lambda *_args, **_kwargs: pytest.fail(
            "analysis-required rail must not reach the production dry build"
        ),
    )

    comparison = preflight_evaluation_comparison(legacy, ("RAIL_VDD",))

    assert len(comparison.blockers) == 1
    blocker = comparison.blockers[0]
    assert blocker.configuration is None
    assert blocker.rail_id == "RAIL_VDD"
    assert blocker.refdes == "<connection analysis>"
    assert blocker.kind == DecapConnectionKind.UNRESOLVED
    assert "source connection analysis is missing" in blocker.reason


def test_direct_source_assignment_uses_imported_template_without_plane_eligibility() -> None:
    payload = _scenario().model_dump(mode="python")
    c1 = next(item for item in payload["decaps"] if item["refdes"] == "C1")
    c1["eligibility"] = {}
    connection = payload["connection_analysis"]["connections"]["C1"]
    for terminal, prefix in (("power_vias", "VP"), ("ground_vias", "VG")):
        second = dict(connection[terminal][0])
        second.update(
            {
                "via_id": f"{prefix}-C1-B",
                "endpoint_node_id": f"N-{prefix}-C1-B",
                "x_um": float(second["x_um"]) + 25.0,
            }
        )
        connection[terminal] = (*connection[terminal], second)
    source = ScenarioSpec.model_validate(payload)

    preflight = preflight_evaluation_connectivity(source, ("RAIL_VDD",))

    assert preflight.is_clear
    project = build_evaluation_project(source, evaluation_rail_id="RAIL_VDD")
    assert len(project.shared_pad_clusters) == 1
    paths = project.shared_pad_clusters[0].via_paths
    assert len(paths) == 4
    assert {item.via_template_id for item in paths} == {"VT_ALLOWED"}
    assert {item.terminal_provenance for item in paths} == {
        "LEGACY_RAIL_TEMPLATE"
    }


def test_moved_direct_missing_exact_eligibility_is_preflighted_before_baseline_worker(
    monkeypatch,
) -> None:
    payload = _scenario().model_dump(mode="python")
    alternate = deepcopy(payload["normalized_project"]["rails"][0])
    alternate.update(
        {
            "rail_id": "RAIL_ALT",
            "family": "VDD_ALT",
            "domain": "VDD_ALT",
            "net": "VDD_ALT",
        }
    )
    payload["normalized_project"]["rails"].append(alternate)
    pwr_layer = next(
        item
        for item in payload["normalized_project"]["stackup_layers"]
        if item["name"] == "PWR1"
    )
    pwr_layer["pwr_nets"].append("VDD_ALT")
    c1 = next(item for item in payload["decaps"] if item["refdes"] == "C1")
    c1.update(
        {
            "current_net": "VDD_ALT",
            "current_rail_id": "RAIL_ALT",
            "eligibility": {},
        }
    )
    blocked = ScenarioSpec.model_validate(payload)

    preflight = preflight_evaluation_connectivity(blocked, ("RAIL_ALT",))

    assert [(item.refdes, item.kind.value) for item in preflight.blockers] == [
        ("C1", "DIRECT")
    ]
    assert "current-rail eligibility is missing" in preflight.blockers[0].reason
    monkeypatch.setattr(
        ScenarioSpec,
        "with_baseline_captures",
        lambda *_args, **_kwargs: pytest.fail("baseline worker must not start"),
    )
    comparison = preflight_evaluation_comparison(blocked, ("RAIL_ALT",))
    assert comparison.blockers[0].configuration == evaluation_module.EvaluationRole.TUNED
    with pytest.raises(ScenarioEvaluationPreflightError) as captured:
        evaluate_comparison_batch(blocked, ("RAIL_ALT",))
    assert captured.value.preflight == comparison
    with pytest.raises(ScenarioEvaluationBuildError) as direct:
        build_evaluation_project(blocked, evaluation_rail_id="RAIL_ALT")
    assert direct.value.code == "ELIGIBILITY_MISSING"


def test_disabled_direct_multi_via_source_assignment_uses_template_fallback() -> None:
    payload = _scenario().model_dump(mode="python")
    c2 = next(item for item in payload["decaps"] if item["refdes"] == "C2")
    c2["eligibility"] = {}
    connection = payload["connection_analysis"]["connections"]["C2"]
    second_power = dict(connection["power_vias"][0])
    second_power.update({"via_id": "VP-C2-B", "endpoint_node_id": "NP-C2-B"})
    connection["power_vias"] = (*connection["power_vias"], second_power)
    source = ScenarioSpec.model_validate(payload)

    preflight = preflight_evaluation_connectivity(source, ("RAIL_VDD",))

    assert preflight.is_clear
    project = build_evaluation_project(source, evaluation_rail_id="RAIL_VDD")
    cluster = next(
        item for item in project.shared_pad_clusters if item.cluster_id.endswith("DIRECT:C2")
    )
    assert {item.via_template_id for item in cluster.via_paths} == {"VT_ALLOWED"}


def test_connectivity_preflight_includes_disabled_unresolved_before_baseline(
    monkeypatch,
) -> None:
    payload = _scenario().model_dump(mode="python")
    payload["connection_analysis"]["connections"]["C2"].update(
        {
            "kind": DecapConnectionKind.UNRESOLVED,
            "power_vias": (),
            "ground_vias": (),
            "reason": "disabled pad source landing is unresolved",
        }
    )
    blocked = ScenarioSpec.model_validate(payload)

    preflight = preflight_evaluation_connectivity(blocked, ("RAIL_VDD",))

    assert [(item.refdes, item.kind.value) for item in preflight.blockers] == [
        ("C2", "UNRESOLVED")
    ]
    with pytest.raises(ScenarioEvaluationBuildError) as builder_error:
        build_evaluation_project(blocked, evaluation_rail_id="RAIL_VDD")
    assert builder_error.value.code == "DECAP_CONNECTION_UNRESOLVED"

    monkeypatch.setattr(
        ScenarioSpec,
        "with_baseline_captures",
        lambda *_args, **_kwargs: pytest.fail("baseline capture must not start"),
    )
    with pytest.raises(ScenarioEvaluationPreflightError) as captured:
        evaluate_comparison_batch(blocked, ("RAIL_VDD",))
    assert captured.value.preflight == preflight


def test_connectivity_preflight_includes_isolation_gap_unresolved_before_baseline(
    monkeypatch,
) -> None:
    scenario = _scenario()
    gap = scenario.decaps[0].model_copy(
        update={"enabled": False, "pad_state": DecapPadState.ISOLATION_GAP}
    )
    connection = scenario.connection_analysis.connections[gap.refdes].model_copy(
        update={
            "kind": DecapConnectionKind.UNRESOLVED,
            "power_vias": (),
            "ground_vias": (),
            "reason": "isolation-gap source landing is unresolved",
        }
    )
    analysis = scenario.connection_analysis.model_copy(
        update={
            "connections": {
                **scenario.connection_analysis.connections,
                gap.refdes: connection,
            }
        }
    )
    # This deliberately bypasses ScenarioSpec persistence validation: persisted
    # isolation gaps must be source-authorized shared-pad members, but the
    # builder checks a direct connection classification before pad state.
    blocked = scenario.model_copy(
        update={"decaps": [gap, *scenario.decaps[1:]], "connection_analysis": analysis}
    )

    preflight = preflight_evaluation_connectivity(blocked, ("RAIL_VDD",))

    assert [(item.refdes, item.kind.value) for item in preflight.blockers] == [
        ("C1", "UNRESOLVED")
    ]
    with pytest.raises(ScenarioEvaluationBuildError) as builder_error:
        build_evaluation_project(blocked, evaluation_rail_id="RAIL_VDD")
    assert builder_error.value.code == "DECAP_CONNECTION_UNRESOLVED"

    monkeypatch.setattr(
        ScenarioSpec,
        "with_baseline_captures",
        lambda *_args, **_kwargs: pytest.fail("baseline capture must not start"),
    )
    with pytest.raises(ScenarioEvaluationPreflightError) as captured:
        evaluate_comparison_batch(blocked, ("RAIL_VDD",))
    assert captured.value.preflight == preflight


def test_saved_distributed_scenario_reloads_and_blocks_outside_changed_rail(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _scenario().model_dump(mode="python")
    project = payload["normalized_project"]
    project["stackup_layers"][2]["pwr_nets"].append("VDD_ALT")
    project["rails"].append(
        RailSpec(
            rail_id="RAIL_ALT",
            family="VDD",
            domain="VDD_ALT",
            net="VDD_ALT",
            site="SITE0",
            pwr_layer="PWR1",
            gnd_layer="GND1",
        ).model_dump(mode="python")
    )
    project["pins"].append(
        PinRecord(
            refdes="U1",
            pin="P2",
            net="VDD_ALT",
            x_um=2_000.0,
            y_um=1_000.0,
            kind=PinKind.DEVICE_BUMP,
            terminal=TerminalKind.PWR,
            domain="VDD_ALT",
            site="SITE0",
        ).model_dump(mode="python")
    )
    partition = project["partitions"][0]
    partition["columns"] = 2
    partition["cells"][0]["x_max_um"] = 5_000.0
    partition["cells"].append(
        PlaneCell(
            cell_id="CELL1",
            row=0,
            column=1,
            x_min_um=5_000.0,
            x_max_um=10_000.0,
            y_min_um=0.0,
            y_max_um=8_000.0,
        ).model_dump(mode="python")
    )
    partition["domain_to_cell"]["VDD_ALT"] = "CELL1"
    alt_eligibility = RailEligibility(
        rail_id="RAIL_ALT",
        net="VDD_ALT",
        pwr_layer="PWR1",
        gnd_layer="GND1",
        via_template_id="VT_ALLOWED",
        allowed=True,
    ).model_dump(mode="python")
    for decap in payload["decaps"]:
        decap.update(
            {
                "source_model_id": "M1",
                "model_id": "M1",
                "enabled": True,
                "source_mounted": True,
                "eligibility": {
                    "RAIL_VDD": RailEligibility(
                        rail_id="RAIL_VDD",
                        net="VDD",
                        pwr_layer="PWR1",
                        gnd_layer="GND1",
                        via_template_id="VT_ALLOWED",
                        allowed=True,
                    ).model_dump(mode="python"),
                    "RAIL_ALT": alt_eligibility,
                },
            }
        )
    # This fixture models a conventional TOP-to-PWR1 through-via explicitly.
    # A board-level negative transition policy is only a positive-evidence
    # summary and cannot grant non-TOP permission to a pathless landing.
    for connection in payload["connection_analysis"]["connections"].values():
        for landing in connection["power_vias"]:
            landing["path_evidence"] = (
                ScenarioViaPathEvidence(
                    target_layer="PWR1",
                    target_node_id=f"TARGET-{landing['via_id']}",
                    target_padstack=landing["padstack"],
                    target_pad_kind="CIRCLE",
                    target_pad_width_um=300.0,
                    target_pad_height_um=300.0,
                    x_um=landing["x_um"],
                    y_um=landing["y_um"],
                    segments=(
                        ScenarioViaSegment(
                            via_id=landing["via_id"],
                            padstack=landing["padstack"],
                            drill_diameter_um=300.0,
                            start_layer="TOP",
                            end_layer="PWR1",
                            length_um=153.0,
                            end_x_um=landing["x_um"],
                            end_y_um=landing["y_um"],
                            padstack_material="COPPER",
                        ),
                    ),
                ).model_dump(mode="python"),
            )
    scenario = ScenarioSpec.model_validate(payload)
    plane_geometries = tuple(
        SpdPlaneGeometry(
            layer="PWR1",
            net=net,
            positive_polygons_um=(
                ((0.0, 0.0), (10_000.0, 0.0), (10_000.0, 8_000.0), (0.0, 8_000.0)),
            ),
            negative_polygons_um=(),
            primitive_order=(("positive_polygon", 0),),
        )
        for net in ("VDD", "VDD_ALT")
    )
    targets = {("RAIL_VDD", "M1"): 0, ("RAIL_ALT", "M1"): 2}
    projection = build_distribution_power_projection(
        scenario,
        {},
        plane_geometries=plane_geometries,
        targets=targets,
    )
    assert projection is not None
    plan = compute_distribution_plan(
        scenario,
        targets,
        DistributionDistanceMode.NEAREST,
        power_projection=projection,
    )
    distributed = apply_distribution_plan(
        scenario, plan, power_projection=projection
    )
    path = save_scenario(distributed, tmp_path / "distributed.spdpi")
    reloaded = load_scenario_bundle(path).scenario

    assert all(item.current_rail_id == "RAIL_ALT" for item in reloaded.decaps)
    preflight = preflight_evaluation_connectivity(reloaded, ("RAIL_ALT",))

    assert {item.refdes for item in preflight.blockers} == {"C1", "C2"}
    assert all(
        "TERMINAL_OUTSIDE_SELECTED_PLANE" in item.reason
        for item in preflight.blockers
    )
    with pytest.raises(ScenarioEvaluationPreflightError):
        build_evaluation_project(reloaded, evaluation_rail_id="RAIL_ALT")

    # The terminal-complete layerwise network does not stamp these contacts
    # into the legacy rectangular modal plane.  It binds them to exact retained
    # (layer, NET) surfaces later in the production builder preflight.
    layerwise = preflight_evaluation_connectivity(
        reloaded,
        ("RAIL_ALT",),
        solver_profile="layerwise_admittance_v1",
    )
    assert layerwise.is_clear

    # Isolate this public project-builder boundary from retained-artwork setup;
    # exact source and termination certificate failures remain covered by the
    # layerwise builder-preflight tests.
    monkeypatch.setattr(
        evaluation_module,
        "_layerwise_profile_project",
        lambda _scenario, project, _rail_ids: project,
    )
    layerwise_project = build_evaluation_project(
        reloaded,
        evaluation_rail_id="RAIL_ALT",
        solver_profile="layerwise_admittance_v1",
    )
    assert not layerwise_project.placements


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


def test_connectivity_message_reports_blocked_rails_out_of_selected_total() -> None:
    preflight = EvaluationConnectivityPreflight(
        ("R1", "R2", "R3", "R4", "R5", "R6"),
        (
            evaluation_module.EvaluationConnectivityBlocker(
                rail_id="R1", refdes="C1", kind=DecapConnectionKind.UNRESOLVED, reason="missing path"
            ),
            evaluation_module.EvaluationConnectivityBlocker(
                rail_id="R2", refdes="C2", kind=DecapConnectionKind.OUT_OF_SCOPE, reason="TOP scope"
            ),
        ),
    )

    assert "2 of 6 selected PWR rail(s)" in preflight.message()


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


def test_evaluate_scenario_modal_preset_is_part_of_cache_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_evaluate(state, rail_id, *_args, **_kwargs):
        view = _view(rail_id)
        state.last_evaluation = view
        return view

    monkeypatch.setattr(
        evaluation_module.evaluation_services, "evaluate_workspace", fake_evaluate
    )
    scenario = _scenario()

    fast = evaluate_scenario(scenario, "RAIL_VDD", modal_max_index=6)
    high = evaluate_scenario(scenario, "RAIL_VDD", modal_max_index=10)

    assert fast.result_key.settings_sha256 != high.result_key.settings_sha256
    assert fast.result_key.cache_key != high.result_key.cache_key


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


def test_legacy_stackup_schema_baseline_cache_remains_reusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _scenario().model_dump(mode="python")
    for row in payload["normalized_project"]["stackup_layers"]:
        row.pop("material", None)
        row.pop("dielectric_properties", None)
    legacy = ScenarioSpec.model_validate(payload)
    assert all(
        "material" not in row and "dielectric_properties" not in row
        for row in legacy.normalized_project["stackup_layers"]
    )
    tuned = legacy.decaps[0].model_copy(update={"enabled": False})
    legacy = ScenarioSpec.model_validate(
        {**legacy.model_dump(mode="python"), "decaps": [tuned, legacy.decaps[1]]}
    )
    calls: list[str] = []
    monkeypatch.setattr(evaluation_module, "evaluate_scenario", _fake_evaluator(calls))

    first = evaluate_comparison_batch(legacy, ["RAIL_VDD"])
    persisted = ScenarioSpec.model_validate(
        first.updated_scenario.model_dump(mode="python")
    )
    assert all(
        "material" not in row and "dielectric_properties" not in row
        for row in persisted.normalized_project["stackup_layers"]
    )

    calls.clear()
    second = evaluate_comparison_batch(
        persisted,
        ["RAIL_VDD"],
        attachments=first.updated_attachments,
    )
    assert len(calls) == 1
    assert second.comparisons[0].baseline_from_cache


def test_convergence_policy_is_explicit_and_changes_result_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = evaluation_module._evaluation_settings(None, 8)
    assert settings["convergence_policy"] == {
        "version": "adaptive-frequency-modal-v4",
        "max_refinement_iterations": 3,
        "max_new_frequency_points_per_iteration": 64,
        "curvature_threshold_db": 0.75,
        "rms_tolerance_db": 0.2,
        "max_tolerance_db": 0.5,
        "peak_shift_tolerance_percent": 2.0,
    }
    assert settings["modal_convergence_ceiling_index"] == 14
    layerwise_settings = evaluation_module._evaluation_settings(
        None, 8, "layerwise_admittance_v1"
    )
    assert layerwise_settings["modal_convergence_ceiling_index"] == 12
    original = evaluation_module.ScenarioResultKey.from_settings(
        design_fingerprint="a" * 64,
        rail_id="RAIL_VDD",
        settings=settings,
        solver_version=evaluation_module.SOLVER_VERSION,
    )
    monkeypatch.setattr(evaluation_module, "DEFAULT_MAX_NEW_FREQUENCY_POINTS", 65)
    changed = evaluation_module.ScenarioResultKey.from_settings(
        design_fingerprint="a" * 64,
        rail_id="RAIL_VDD",
        settings=evaluation_module._evaluation_settings(None, 8),
        solver_version=evaluation_module.SOLVER_VERSION,
    )
    assert original.settings_sha256 != changed.settings_sha256


def test_evaluation_policy_is_bound_and_source_sha_is_required_for_proof() -> None:
    strict = evaluation_module._evaluation_settings(None, 8)
    alternate = evaluation_module._evaluation_settings(
        None,
        8,
        evaluation_policy=evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE,
    )
    assert strict["evaluation_policy"] == evaluation_module.EVALUATION_POLICY_STRICT
    assert alternate["evaluation_policy"] == evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE
    assert strict != alternate
    scenario = _scenario()
    project = scenario.base_project.model_copy(update={
        "metadata": {
            "spd_import": {
                "selected_plane_pair_provenance": {
                    "RAIL_VDD": {
                        "pwr_layer": "PWR1",
                        "gnd_layer": "GND1",
                        "source_sha256": scenario.source.sha256,
                    }
                }
            }
        }
    })
    blocked = preflight_evaluation_connectivity(
        scenario,
        ("RAIL_VDD",),
        _project=project,
        evaluation_policy=evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE,
    )
    assert any(
        item.reason.startswith("SOURCE_GRAPH_PROVENANCE_INVALID:")
        for item in blocked.blockers
    )


def test_layerwise_anchor_compile_keeps_source_port_outside_legacy_rectangle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _base_project()
    source_sha256 = "a" * 64
    unresolved_rail = project.rails[0].model_copy(
        update={
            "rail_id": "RAIL_UNRESOLVED",
            "family": "VDD_UNRESOLVED",
            "domain": "VDD_UNRESOLVED",
            "net": "VDD_UNRESOLVED",
        }
    )
    power_pin = project.pins[0].model_copy(
        update={"source_node_id": "NODE_PWR", "source_layer": "TOP"}
    )
    ground_pin = project.pins[1].model_copy(
        update={"source_node_id": "NODE_GND", "source_layer": "TOP"}
    )
    partition = project.partitions[0]
    partition = partition.model_copy(
        update={
            "cells": [
                partition.cells[0].model_copy(update={"x_max_um": 500.0})
            ]
        }
    )
    metadata = deepcopy(project.metadata)
    metadata["spd_import"] = {
        "source_name": "fixture.spd",
        "source_sha256": source_sha256,
        "selected_plane_pair_provenance": {
            "VDD": {
                "source_sha256": source_sha256,
                "source_graph_capability": "TRACE_VIA_COMPONENTS_AVAILABLE",
                "device_route_witnesses": [
                    {
                        "pin_id": power_pin.pin_id,
                        "terminal": "PWR",
                        "source_node_id": power_pin.source_node_id,
                        "target_layer": "PWR1",
                        "reachable": True,
                        "target_contact_count": 1,
                        "target_contacts": [{"x_um": 1200.0, "y_um": 1000.0}],
                    },
                    {
                        "pin_id": ground_pin.pin_id,
                        "terminal": "GND",
                        "source_node_id": ground_pin.source_node_id,
                        "target_layer": "GND1",
                        "reachable": True,
                        "target_contact_count": 1,
                        "target_contacts": [{"x_um": 1300.0, "y_um": 1000.0}],
                    },
                ],
            },
            "vDd_UnReSoLvEd": {
                "pwr_layer": "PWR1",
                "gnd_layer": "GND1",
                "source_sha256": source_sha256,
                "source_graph_pair_unresolved": True,
            },
        },
    }
    project = project.model_copy(
        update={
            "pins": [power_pin, ground_pin, *project.pins[2:]],
            "partitions": [partition],
            "rails": [*project.rails, unresolved_rail],
            "metadata": metadata,
        }
    )

    with pytest.raises(ModalSolverError, match="lies outside the rectangular plane"):
        compile_project_evaluation_template(project, "RAIL_VDD")

    template = compile_project_evaluation_template(
        project,
        "RAIL_VDD",
        terminal_complete_external_input=True,
    )
    branch = template.device.branches[0]
    assert template.origin_um[0] + branch.port.x_m * 1.0e6 == pytest.approx(1000.0)
    assert template.origin_um[1] + branch.port.y_m * 1.0e6 == pytest.approx(1000.0)
    original_anchor_compile = (
        spd_adapter_module.compile_project_evaluation_template
    )
    anchor_compile_calls: list[str] = []

    def record_anchor_compile(
        compile_project: ProjectSpec,
        rail_id: str,
        **kwargs: object,
    ) -> object:
        anchor_compile_calls.append(rail_id)
        assert rail_id != "RAIL_UNRESOLVED"
        return original_anchor_compile(compile_project, rail_id, **kwargs)

    monkeypatch.setattr(
        spd_adapter_module,
        "compile_project_evaluation_template",
        record_anchor_compile,
    )
    bindings, failures = spd_adapter_module._compile_active_rail_anchor_bindings(
        project,
        source_sha256=source_sha256,
    )
    assert failures == []
    assert anchor_compile_calls == ["RAIL_VDD"]
    assert {item["rail_id"] for item in bindings} == {"RAIL_VDD"}
    assert {item["pin_id"] for item in bindings} == {power_pin.pin_id, ground_pin.pin_id}
    scenario = _scenario().model_copy(update={"normalized_project": project})
    preflight = preflight_evaluation_connectivity(
        scenario,
        ("RAIL_UNRESOLVED",),
        _project=project,
    )
    assert [item.reason for item in preflight.blockers] == [
        "SOURCE_GRAPH_PLANE_PAIR_UNRESOLVED: selected rail has no "
        "source-proven PWR/GND pair; re-import matching raw SPD"
    ]


def _alternate_fixture() -> tuple[ScenarioSpec, dict[str, bytes]]:
    """Small retained-artwork fixture for the real alternate project path."""

    scenario = _scenario()
    project = scenario.base_project
    layers = [
        *project.stackup_layers,
        StackupLayer(
            name="PWR2", thickness_um=18.0, conductivity_s_m=5.8e7,
            pwr_nets=["VDD"],
        ),
        StackupLayer(name="D3", thickness_um=80.0, dk=4.0),
        StackupLayer(
            name="GND2", thickness_um=18.0, conductivity_s_m=5.8e7,
            pwr_nets=["DGND"],
        ),
    ]
    cell = project.partitions[0].cells[0].model_copy(
        update={"x_max_um": 100.0, "y_max_um": 100.0}
    )
    partition = project.partitions[0].model_copy(update={"cells": [cell]})

    def asset(layer: str, net: str) -> tuple[bytes, dict[str, object]]:
        compressed, _ = core_services._compress_spd_geometry_payload(
            layer=layer,
            net=net,
            positive_polygons=[[
                (0.0, 0.0), (5000.0, 0.0), (5000.0, 5000.0), (0.0, 5000.0)
            ]],
            negative_polygons=[], positive_circles=[], negative_circles=[],
            primitive_order=[("positive_polygon", 0)],
            positive_subelement_count=1, negative_subelement_count=0,
            polygon_trace_count=0, box_count=0,
        )
        name = f"geometry/{layer}.zlib"
        return compressed, {
            "layer": layer,
            "net": net,
            "asset": name,
            "asset_sha256": sha256(compressed).hexdigest(),
            "bbox_um": [0.0, 5000.0, 0.0, 5000.0],
        }

    pwr, pwr_record = asset("PWR2", "VDD")
    gnd, gnd_record = asset("GND2", "DGND")
    metadata = dict(project.metadata)
    metadata["spd_import"] = {
        **metadata["spd_import"],
        "source_sha256": scenario.source.sha256,
        "plane_geometries": [pwr_record, gnd_record],
        "selected_plane_pair_provenance": {
            "VDD": {
                "pwr_layer": "PWR1",
                "gnd_layer": "GND1",
                "source_sha256": scenario.source.sha256,
            }
        },
    }
    project = project.model_copy(
        update={
            "stackup_layers": layers,
            "partitions": [partition],
            "metadata": metadata,
            "pins": [
                pin.model_copy(update={"via_template_id": "VT_ALLOWED"})
                if pin.kind == PinKind.DEVICE_BUMP else pin
                for pin in project.pins
            ],
        }
    )
    attachments = {pwr_record["asset"]: pwr, gnd_record["asset"]: gnd}
    return (
        scenario.model_copy(update={
            "normalized_project": project.model_dump(mode="python"),
            "attachment_names": sorted(attachments),
            "attachment_hashes": {name: sha256(content).hexdigest() for name, content in attachments.items()},
        }),
        attachments,
    )


def _mixed_policy_alternate_fixture(
    *, outside_configuration: str
) -> tuple[ScenarioSpec, dict[str, bytes]]:
    """Fixture where Original/Tuned resolve different geometry policies."""

    scenario, attachments = _alternate_fixture()
    project = scenario.base_project
    cell = project.partitions[0].cells[0].model_copy(
        update={"x_max_um": 2100.0, "y_max_um": 8000.0}
    )
    project = project.model_copy(
        update={"partitions": [project.partitions[0].model_copy(update={"cells": [cell]})]}
    )
    outside = scenario.decaps[1].model_copy(
        update=(
            {"enabled": True, "model_id": "M1"}
            if outside_configuration.casefold() == "tuned"
            else {
                "enabled": False,
                "model_id": None,
                "source_mounted": True,
                "source_model_id": "M1",
            }
        )
    )
    return (
        ScenarioSpec.model_validate({
            **scenario.model_dump(mode="python"),
            "normalized_project": project.model_dump(mode="python"),
            "decaps": [scenario.decaps[0], outside],
            "attachment_names": sorted(attachments),
            "attachment_hashes": {name: sha256(content).hexdigest() for name, content in attachments.items()},
        }),
        attachments,
    )


def test_retained_alternate_artwork_is_hash_bound_and_rejects_void_footprint() -> None:
    compressed, _ = core_services._compress_spd_geometry_payload(
        layer="L09",
        net="VDD",
        positive_polygons=[[(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)]],
        negative_polygons=[],
        positive_circles=[],
        negative_circles=[(530.0, 500.0, 20.0)],
        primitive_order=[("positive_polygon", 0), ("negative_circle", 0)],
        positive_subelement_count=1,
        negative_subelement_count=1,
        polygon_trace_count=0,
        box_count=0,
    )
    digest = sha256(compressed).hexdigest()
    record = {
        "layer": "L09",
        "net": "VDD",
        "asset": "geometry/l09.spdgeom.zlib",
        "asset_sha256": digest,
        "bbox_um": [0.0, 1000.0, 0.0, 1000.0],
    }
    index = evaluation_module._RetainedArtworkIndex([record], {record["asset"]: compressed})
    assert index.contains(layer="L09", net="VDD", x_um=500.0, y_um=500.0) == "inside"
    assert index.contains(layer="L09", net="VDD", x_um=0.0, y_um=500.0) == "boundary"
    assert index.covers_footprint(
        layer="L09", net="VDD", asset=record["asset"], digest=digest,
        x_um=500.0, y_um=500.0, width_um=100.0, height_um=100.0,
    ) is False
    assert evaluation_module._RetainedArtworkIndex(
        [record], {record["asset"]: compressed + b"tampered"}
    )._entries == ()


def test_layerwise_batch_uses_board_original_identity_without_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario()
    tuned = scenario.decaps[0].model_copy(update={"enabled": False})
    scenario = scenario.model_copy(
        update={"decaps": [tuned, scenario.decaps[1]]}
    )
    calls: list[ScenarioSpec] = []
    profile = evaluation_module.LAYERWISE_ADMITTANCE_PROFILE

    monkeypatch.setattr(
        evaluation_module,
        "preflight_evaluation_comparison",
        lambda _scenario, rail_ids, **_kwargs: EvaluationConnectivityPreflight(
            tuple(rail_ids), ()
        ),
    )
    monkeypatch.setattr(
        evaluation_module,
        "build_evaluation_project",
        lambda *_args, **_kwargs: _base_project(),
    )

    def fake_layerwise(actual, rail_id, target_ohm=None, modal_max_index=8, **_kwargs):
        calls.append(actual)
        view = _view(rail_id)
        view.solver_profile_key = profile.key
        view.solver_profile_label = profile.label
        view.solver_profile_badge = profile.badge
        view.solver_version = evaluation_module.SOLVER_VERSION
        state_digit = actual.design_fingerprint[0]
        view.solver_provenance = {
            **_complete_layerwise_view_provenance(),
            "status": "source_layerwise_production",
            "validation_status": "two_named_case_validation_required",
            "termination_manifest_sha256": state_digit * 64,
            "layerwise_identity_sha256": state_digit * 64,
        }
        key = evaluation_module.ScenarioResultKey.from_settings(
            design_fingerprint=actual.design_fingerprint,
            rail_id=rail_id,
            settings=evaluation_module._evaluation_settings(
                target_ohm,
                modal_max_index,
                profile.key,
                solver_provenance=view.solver_provenance,
            ),
            solver_version=view.solver_version,
        )
        return evaluation_module.ScenarioEvaluation(
            state=object(),
            view=view,
            result_key=key,
            scenario_revision=actual.revision,
        )

    monkeypatch.setattr(evaluation_module, "evaluate_scenario", fake_layerwise)

    batch = evaluate_comparison_batch(
        scenario,
        ["RAIL_VDD"],
        solver_profile=profile.key,
    )

    assert len(calls) == 2
    assert not batch.comparisons[0].baseline_from_cache
    assert batch.updated_scenario.evaluation_cache == {}
    original = evaluation_module._comparison_original_configuration(
        batch.updated_scenario
    )
    assert batch.comparisons[0].baseline.design_fingerprint == (
        original.design_fingerprint
    )
    batch.validate_for_scenario(scenario)

    repeated = evaluate_comparison_batch(
        batch.updated_scenario,
        ["RAIL_VDD"],
        attachments=batch.updated_attachments,
        solver_profile=profile.key,
    )
    assert repeated.updated_scenario.revision == batch.updated_scenario.revision
    assert repeated.updated_scenario.baseline_captures == (
        batch.updated_scenario.baseline_captures
    )
    assert repeated.updated_scenario.evaluation_cache == {}


def test_solver_version_0_8_2_recalculates_0_6_baseline_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert evaluation_module.SOLVER_VERSION == "modal-mvp-0.8.3"
    scenario = _scenario()
    tuned = scenario.decaps[0].model_copy(update={"enabled": False})
    scenario = ScenarioSpec.model_validate(
        {**scenario.model_dump(mode="python"), "decaps": [tuned, scenario.decaps[1]]}
    )
    calls: list[str] = []
    monkeypatch.setattr(evaluation_module, "evaluate_scenario", _fake_evaluator(calls))
    first = evaluate_comparison_batch(scenario, ["RAIL_VDD"])
    _current_key, metadata = next(iter(first.updated_scenario.evaluation_cache.items()))
    capture = first.updated_scenario.baseline_captures["RAIL_VDD"]
    stale_key = evaluation_module.ScenarioResultKey.from_settings(
        design_fingerprint=capture.evaluation_input_sha256,
        rail_id="RAIL_VDD",
        settings={"target_ohm": None, "modal_max_index": 8},
        solver_version="modal-mvp-0.6.0",
    )
    stale_payload = json.loads(first.updated_attachments[metadata.attachment_name])
    stale_payload["result_key"]["solver_version"] = stale_key.solver_version
    stale_payload["view"]["solver_version"] = stale_key.solver_version
    stale_content = json.dumps(stale_payload, sort_keys=True).encode("utf-8")
    stale_digest = sha256(stale_content).hexdigest()
    stale_name = f"results/baseline-{stale_key.cache_key}.json"
    stale_metadata = metadata.model_copy(
        update={
            "result_key": stale_key,
            "attachment_name": stale_name,
            "attachment_sha256": stale_digest,
        }
    )
    stale_hashes = dict(first.updated_scenario.attachment_hashes)
    del stale_hashes[metadata.attachment_name]
    stale_hashes[stale_name] = stale_digest
    stale_names = [
        stale_name if name == metadata.attachment_name else name
        for name in first.updated_scenario.attachment_names
    ]
    stale_scenario = ScenarioSpec.model_validate(
        {
            **first.updated_scenario.model_dump(mode="python"),
            "attachment_names": stale_names,
            "attachment_hashes": stale_hashes,
            "evaluation_cache": {stale_key.cache_key: stale_metadata},
        }
    )
    stale_attachments = dict(first.updated_attachments)
    del stale_attachments[metadata.attachment_name]
    stale_attachments[stale_name] = stale_content

    calls.clear()
    recalculated = evaluate_comparison_batch(
        stale_scenario,
        ["RAIL_VDD"],
        attachments=stale_attachments,
    )
    assert len(calls) == 2
    assert not recalculated.comparisons[0].baseline_from_cache

    calls.clear()
    reused = evaluate_comparison_batch(
        recalculated.updated_scenario,
        ["RAIL_VDD"],
        attachments=recalculated.updated_attachments,
    )
    assert len(calls) == 1
    assert reused.comparisons[0].baseline_from_cache


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


def test_baseline_capture_reuses_one_source_state_hash_without_full_revalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _scenario()
    source_state_hashes = 0
    real_hash_payload = scenario_module._hash_payload

    def counted_hash_payload(payload: object) -> str:
        nonlocal source_state_hashes
        if (
            isinstance(payload, dict)
            and "source_sha256" in payload
            and "decaps" in payload
        ):
            source_state_hashes += 1
        return real_hash_payload(payload)

    def reject_full_scenario_revalidation(*args: object, **kwargs: object) -> object:
        raise AssertionError("baseline capture must not fully revalidate ScenarioSpec")

    monkeypatch.setattr(scenario_module, "_hash_payload", counted_hash_payload)
    monkeypatch.setattr(
        ScenarioSpec,
        "model_validate",
        reject_full_scenario_revalidation,
    )

    captured = scenario.with_baseline_captures(("RAIL_VDD",))

    assert tuple(captured.baseline_captures) == ("RAIL_VDD",)
    assert source_state_hashes == 1


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


def test_present_malformed_source_plane_pair_proof_never_uses_synthetic_exemption() -> None:
    scenario = _scenario()
    for proof in ("garbage", {}, None):
        metadata = dict(scenario.base_project.metadata)
        metadata["spd_import"] = {
            **dict(metadata.get("spd_import", {})),
            "selected_plane_pair_provenance": proof,
        }
        project = scenario.base_project.model_copy(update={"metadata": metadata})
        result = preflight_evaluation_connectivity(
            scenario, ("RAIL_VDD",), _project=project
        )
        assert not result.is_clear
        assert all(
            item.reason.startswith("SOURCE_GRAPH_PROVENANCE_INVALID:")
            for item in result.blockers
        )


def test_direct_builder_gates_source_provenance_before_profile_or_alternate_transform() -> None:
    scenario = _scenario()
    metadata = dict(scenario.base_project.metadata)
    metadata["spd_import"] = {
        **dict(metadata.get("spd_import", {})),
        "selected_plane_pair_provenance": "malformed",
    }
    blocked = scenario.base_project.model_copy(update={"metadata": metadata})
    with pytest.raises(ScenarioEvaluationPreflightError) as exc_info:
        build_evaluation_project(
            scenario,
            _project=blocked,
            evaluation_rail_id="RAIL_VDD",
            solver_profile=LAYERWISE_ADMITTANCE_PROFILE,
            evaluation_policy=evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE,
        )
    assert all(
        item.reason.startswith("SOURCE_GRAPH_PROVENANCE_INVALID:")
        for item in exc_info.value.preflight.blockers
    )


def test_graph_contact_fallback_uses_proven_target_and_template_port_size() -> None:
    scenario = _scenario()
    landing = ScenarioViaLanding.model_validate(
        {
            "via_id": "V-GRAPH",
            "net": "VDD",
            "endpoint_node_id": "N-GRAPH",
            "padstack": "VIA",
            "x_um": 9000.0,
            "y_um": 7000.0,
            "graph_contact_evidence": [{
                "x_um": 1000.0,
                "y_um": 1100.0,
                "target_layer": "PWR1",
                "target_node_id": "N-TARGET",
                "candidate_count": 1,
                "candidate_contacts_sha256": "a" * 64,
                "selection_basis": "SOURCE_GRAPH",
                "source_sha256": scenario.source.sha256,
                "selected_distance_um": 0.0,
            }],
        }
    )
    template = scenario.base_project.via_templates[0]
    footprint = evaluation_module._terminal_footprint(landing, "PWR1", template)
    assert (footprint.x_um, footprint.y_um) == (1000.0, 1100.0)
    assert (footprint.width_um, footprint.height_um) == (
        template.finite_port_width_um,
        template.finite_port_height_um,
    )


def test_solver_fingerprint_binds_only_selected_plane_pair_provenance() -> None:
    scenario = _scenario()
    base = scenario.base_project
    metadata = dict(base.metadata)
    spd_import = dict(metadata["spd_import"])
    second_spd_import = dict(spd_import)
    second_spd_import["selected_plane_pair_provenance"] = {
        "RAIL_VDD": {
            "pwr_layer": "PWR1",
            "gnd_layer": "GND1",
            "source_sha256": scenario.source.sha256,
        }
    }
    first = base.model_copy(update={"metadata": {**metadata, "spd_import": spd_import}})
    spd_import["selected_plane_pair_provenance"] = {
        "RAIL_VDD": {
            "pwr_layer": "PWR2",
            "gnd_layer": "GND2",
            "source_sha256": scenario.source.sha256,
        }
    }
    second = base.model_copy(update={"metadata": {**metadata, "spd_import": second_spd_import}})
    assert evaluation_module._solver_project_fingerprint(first) != evaluation_module._solver_project_fingerprint(second)
    noisy = first.model_copy(update={"metadata": {**first.metadata, "ui_note": "ignored"}})
    assert evaluation_module._solver_project_fingerprint(first) == evaluation_module._solver_project_fingerprint(noisy)


def test_embedded_alternate_fixture_applies_scenario_project_and_template_provenance() -> None:
    scenario, attachments = _alternate_fixture()
    context = evaluation_module._alternate_context_for_rail(
        scenario, "RAIL_VDD", attachments=attachments, _cache={}
    )
    assert context is not None
    assert context.scenario.base_project is not scenario.base_project
    assert context.project.metadata["spd_import"]["evaluation_alternate_pair"]
    result = build_evaluation_project(
        scenario,
        evaluation_rail_id="RAIL_VDD",
        evaluation_policy=evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE,
        attachments=attachments,
        _alternate_cache={},
    )
    provenance = result.metadata["evaluation_alternate_pair_provenance"]
    assert provenance["candidate_pwr_layer"] == "PWR2"
    assert provenance["candidate_gnd_layer"] == "GND2"
    alternate_template_id = provenance["alternate_template_id"]
    assert any(item.template_id == alternate_template_id for item in result.via_templates)
    via_provenance = result.metadata["spd_via_template_provenance"]
    assert alternate_template_id in via_provenance
    assert "VT_ALLOWED" not in via_provenance
    assert via_provenance[alternate_template_id]["source_vertical_path_proven"] is False
    assert evaluation_module._via_template_ids_by_rail(result)["rail_vdd"] == alternate_template_id
    assert any(
        item.via_template_id == alternate_template_id
        for item in result.pins
        if item.kind == PinKind.DEVICE_BUMP
    )


def test_embedded_alternate_view_is_low_confidence_with_vertical_path_assumption(monkeypatch) -> None:
    scenario, attachments = _alternate_fixture()
    monkeypatch.setattr(
        evaluation_module.evaluation_services,
        "evaluate_workspace",
        lambda *args, **kwargs: _view(),
    )
    result = evaluate_scenario(
        scenario,
        "RAIL_VDD",
        attachments=attachments,
        evaluation_policy=evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE,
    )
    assert result.view.evaluation_policy == evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE
    assert result.view.confidence == "LOW"
    assert any("missing source vertical landing path" in item.casefold() for item in result.view.assumptions)


@pytest.mark.parametrize("outside_configuration", ["TUNED", "ORIGINAL"])
def test_mixed_strict_and_alternate_views_share_effective_result_key_policy(
    monkeypatch, outside_configuration: str
) -> None:
    scenario, attachments = _mixed_policy_alternate_fixture(
        outside_configuration=outside_configuration
    )
    def fake_workspace(state, rail_id, *args, **kwargs):
        view = _view(rail_id)
        view.solver_version = evaluation_module.SOLVER_VERSION
        return view

    monkeypatch.setattr(
        evaluation_module.evaluation_services, "evaluate_workspace", fake_workspace
    )
    batch = evaluate_comparison_batch(
        scenario,
        ["RAIL_VDD"],
        attachments=attachments,
        evaluation_policy=evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE,
    )
    comparison = batch.comparisons[0]
    strict_side = comparison.baseline if outside_configuration == "TUNED" else comparison.tuned
    alternate_side = comparison.tuned if outside_configuration == "TUNED" else comparison.baseline
    assert strict_side.view.evaluation_policy == evaluation_module.EVALUATION_POLICY_STRICT
    assert alternate_side.view.evaluation_policy == evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE
    assert comparison.baseline.result_key.settings_sha256 == comparison.tuned.result_key.settings_sha256, (
        strict_side.view.evaluation_policy,
        alternate_side.view.evaluation_policy,
        comparison.baseline.result_key.settings_sha256,
        comparison.tuned.result_key.settings_sha256,
    )
    batch.validate_for_scenario(scenario)


def test_comparison_batch_reuses_one_alternate_cache_and_working_attachments(monkeypatch) -> None:
    scenario, attachments = _alternate_fixture()
    cache_ids: list[int] = []
    attachment_sizes: list[int] = []
    real_build = evaluation_module.build_evaluation_project

    def wrapped_build(*args, **kwargs):
        cache = kwargs.get("_alternate_cache")
        if cache is not None:
            cache_ids.append(id(cache))
        attachment_sizes.append(len(kwargs.get("attachments") or {}))
        return real_build(*args, **kwargs)

    monkeypatch.setattr(evaluation_module, "build_evaluation_project", wrapped_build)
    monkeypatch.setattr(
        evaluation_module.evaluation_services,
        "evaluate_workspace",
        lambda state, rail_id, *args, **kwargs: _view(rail_id),
    )
    evaluate_comparison_batch(
        scenario,
        ["RAIL_VDD"],
        attachments=attachments,
        evaluation_policy=evaluation_module.EVALUATION_POLICY_EMBEDDED_ALTERNATE,
    )
    assert cache_ids
    assert len(set(cache_ids)) == 1
    assert max(attachment_sizes) >= len(attachments)
