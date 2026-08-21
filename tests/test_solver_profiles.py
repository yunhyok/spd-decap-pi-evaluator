from __future__ import annotations

from hashlib import sha256
import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from spd_decap_pi._core import services
from spd_decap_pi._core.domain import (
    ConfidenceLevel,
    FrequencySettings,
    MLOOutline,
    PinKind,
    PinRecord,
    PlaneCell,
    PlanePartitionSpec,
    ProjectSpec,
    RailSpec,
    StackupLayer,
    TargetPoint,
    TerminalKind,
    ViaLoopTemplate,
    ViaPathKind,
)
from spd_decap_pi._core.services import WorkspaceState
from spd_decap_pi._core.solver.evaluator import (
    build_project_evaluation_request,
    compile_evaluation_kernel,
    compile_project_evaluation_template,
    evaluate_rail,
)
from spd_decap_pi._core.solver.profiles import (
    APPLICATION_DEFAULT_SOLVER_PROFILE_KEY,
    DEFAULT_SOLVER_PROFILE_KEY,
    RESEARCH_UNIFORM_ADMITTANCE_PROFILE,
)
from spd_decap_pi._core.solver.research_uniform_profile import (
    ResearchProfileUnavailable,
    build_uniform_c00_source_model,
)
from spd_decap_pi.evaluation import _evaluation_settings, evaluate_comparison_batch
from spd_decap_pi.scenario import (
    SHARED_PAD_ANALYSIS_VERSION,
    ScenarioResultKey,
    ScenarioSpec,
    SharedPadConnectionAnalysis,
    SourceIdentity,
)


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _geometry_asset(layer: str, net: str, polygon: list[list[float]]) -> tuple[bytes, str]:
    content, _size = services._compress_spd_geometry_payload(
        layer=layer,
        net=net,
        positive_polygons=(polygon,),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=1,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )
    return content, sha256(content).hexdigest()


def _geometry_asset_many(
    layer: str, net: str, polygons: tuple[list[list[float]], ...]
) -> tuple[bytes, str]:
    content, _size = services._compress_spd_geometry_payload(
        layer=layer,
        net=net,
        positive_polygons=polygons,
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=tuple(("positive_polygon", index) for index in range(len(polygons))),
        positive_subelement_count=len(polygons),
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )
    return content, sha256(content).hexdigest()


def _component_hash(layer: str, net: str, asset_sha256: str) -> str:
    return sha256(
        _canonical(
            [
                {
                    "layer": layer.casefold(),
                    "net": net.casefold(),
                    "asset_sha256": asset_sha256,
                }
            ]
        )
    ).hexdigest()


def _manufactured_project(*, include_certificate: bool = True) -> tuple[ProjectSpec, dict[str, bytes]]:
    source_sha256 = "a" * 64
    pwr_asset, pwr_hash = _geometry_asset(
        "PWR", "VDD", [[0.0, 0.0], [500.0, 0.0], [500.0, 1000.0], [0.0, 1000.0]]
    )
    gnd_asset, gnd_hash = _geometry_asset(
        "GND", "DGND", [[0.0, 0.0], [1000.0, 0.0], [1000.0, 1000.0], [0.0, 1000.0]]
    )
    records = [
        {
            "layer": "PWR",
            "net": "VDD",
            "asset": "geometry/pwr.json.zlib",
            "asset_sha256": pwr_hash,
        },
        {
            "layer": "GND",
            "net": "DGND",
            "asset": "geometry/gnd.json.zlib",
            "asset_sha256": gnd_hash,
        },
    ]
    spd_import: dict[str, object] = {
        "source_name": "manufactured.spd",
        "source_sha256": source_sha256,
        "plane_geometries": records,
        "selected_plane_pair_provenance": {
            "VDD": {
                "rail_net": "VDD",
                "pwr_layer": "PWR",
                "gnd_layer": "GND",
                "source_sha256": source_sha256,
            }
        },
    }
    if include_certificate:
        spd_import["uniform_component_connectivity_certificate"] = {
            "version": "uniform-component-connectivity-v1",
            "source_sha256": source_sha256,
            "rail_id": "VDD/0",
            "layer_order": ["pwr", "gnd"],
            "selected_net": "vdd",
            "reference_net": "dgnd",
            "selected_component_sha256": _component_hash("PWR", "VDD", pwr_hash),
            "reference_component_sha256": _component_hash("GND", "DGND", gnd_hash),
            "source_terminal_component_proven": True,
            "reference_terminal_component_proven": True,
            "unresolved_codes": [],
        }
    project = ProjectSpec(
        name="manufactured uniform profile",
        outline=MLOOutline(width_um=1000.0, height_um=1000.0),
        split_gap_um=0.0,
        frequency=FrequencySettings(
            start_hz=1.0e5,
            stop_hz=1.0e7,
            points=9,
            critical_start_hz=1.0e5,
            critical_stop_hz=1.0e7,
        ),
        stackup_layers=[
            StackupLayer(
                name="PWR",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD"],
            ),
            StackupLayer(name="D1", thickness_um=100.0, dk=4.0, df=0.01),
            StackupLayer(
                name="GND",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["DGND"],
            ),
        ],
        rails=[
            RailSpec(
                rail_id="VDD/0",
                family="VDD",
                domain="VDD0",
                net="VDD",
                site="SITE0",
                pwr_layer="PWR",
                gnd_layer="GND",
                target_mask=[TargetPoint(frequency_hz=1.0e5, impedance_ohm=0.1)],
            )
        ],
        pins=[
            PinRecord(
                refdes="U1",
                pin="P1",
                net="VDD",
                x_um=200.0,
                y_um=500.0,
                kind=PinKind.DEVICE_BUMP,
                terminal=TerminalKind.PWR,
                domain="VDD0",
                site="SITE0",
            ),
            PinRecord(
                refdes="U1",
                pin="G1",
                net="DGND",
                x_um=250.0,
                y_um=500.0,
                kind=PinKind.DEVICE_BUMP,
                terminal=TerminalKind.GND,
                site="SITE0",
            ),
        ],
        partitions=[
            PlanePartitionSpec(
                layer="PWR",
                rows=1,
                columns=1,
                domain_to_cell={"VDD0": "CELL0"},
                cells=[
                    PlaneCell(
                        cell_id="CELL0",
                        row=0,
                        column=0,
                        x_min_um=0.0,
                        x_max_um=1000.0,
                        y_min_um=0.0,
                        y_max_um=1000.0,
                    )
                ],
                split_gap_um=0.0,
                confidence=ConfidenceLevel.HIGH,
                confirmed=True,
            )
        ],
        via_templates=[
            ViaLoopTemplate(
                template_id="DEVICE_LOOP",
                pwr_reference_layer="PWR",
                gnd_reference_layer="GND",
                path_kind=ViaPathKind.DIRECT,
                finite_port_width_um=50.0,
                finite_port_height_um=50.0,
                loop_resistance_ohm=0.001,
                loop_inductance_h=0.2e-9,
            )
        ],
        metadata={
            "plane_pair_confirmed": True,
            "device_pairing_confirmed": True,
            "spd_import": spd_import,
        },
    )
    return project, {
        "geometry/pwr.json.zlib": pwr_asset,
        "geometry/gnd.json.zlib": gnd_asset,
    }


def test_profile_identity_is_immutable_default_and_cache_distinct() -> None:
    assert DEFAULT_SOLVER_PROFILE_KEY == "legacy_modal_v017"
    assert APPLICATION_DEFAULT_SOLVER_PROFILE_KEY == "layerwise_admittance_v1"
    legacy = ScenarioResultKey.from_settings(
        design_fingerprint="b" * 64,
        rail_id="VDD/0",
        settings=_evaluation_settings(None, 8, "legacy_modal_v017"),
        solver_version="fixture",
    )
    research = ScenarioResultKey.from_settings(
        design_fingerprint="b" * 64,
        rail_id="VDD/0",
        settings=_evaluation_settings(None, 8, "research_uniform_admittance"),
        solver_version="fixture",
    )
    assert legacy.settings_sha256 != research.settings_sha256
    assert legacy.cache_key != research.cache_key


def test_manufactured_source_profile_replaces_only_modal_c00_and_records_provenance() -> None:
    project, attachments = _manufactured_project()
    template = compile_project_evaluation_template(project, "VDD/0")
    source = build_uniform_c00_source_model(project, attachments, "VDD/0", template)
    legacy_request = build_project_evaluation_request(
        project, "VDD/0", max_mode_x=2, max_mode_y=2, template=template
    )
    research_request = build_project_evaluation_request(
        project,
        "VDD/0",
        max_mode_x=2,
        max_mode_y=2,
        template=template,
        solver_profile_key=RESEARCH_UNIFORM_ADMITTANCE_PROFILE.key,
        uniform_c00_source=source,
    )
    legacy = compile_evaluation_kernel(legacy_request)
    research = compile_evaluation_kernel(research_request)
    zero = research.solver.mode_index(0, 0)
    nonuniform = [index for index in range(research.solver.mode_count) if index != zero]
    assert not np.allclose(
        legacy.prepared_device.plane_admittance[:, zero],
        research.prepared_device.plane_admittance[:, zero],
    )
    np.testing.assert_array_equal(
        legacy.prepared_device.plane_admittance[:, nonuniform],
        research.prepared_device.plane_admittance[:, nonuniform],
    )
    # The bridge uses the solver-owned uniform C00 API, so branch terms (Device
    # bump/via paths and every decap shunt candidate) remain byte-identical.
    assert research.prepared_device.legacy_uniform_c00_term is not None
    term = research.prepared_device.legacy_uniform_c00_term
    np.testing.assert_array_equal(
        term.legacy_admittance_s,
        legacy.prepared_device.plane_admittance[:, zero],
    )
    np.testing.assert_array_equal(
        term.uniform_projection,
        np.eye(research.solver.mode_count)[zero],
    )
    assert research.solver.basis_vector(research_request.device.branches[0].port)[zero] == 1.0
    for legacy_branch, research_branch in zip(
        legacy.prepared_device.branch_data,
        research.prepared_device.branch_data,
        strict=True,
    ):
        for left, right in zip(legacy_branch, research_branch, strict=True):
            if isinstance(left, np.ndarray):
                np.testing.assert_array_equal(left, right)
            else:
                assert left == right
    assert np.min(research.prepared_device.plane_admittance[:, zero].real) >= 0.0

    outcome = evaluate_rail(research_request, kernel=research)
    assert outcome.solver_profile_key == "research_uniform_admittance"
    assert outcome.solver_provenance["source_only"] is True
    assert outcome.solver_provenance["powersi_used_for_parameters"] is False
    assert outcome.solver_provenance["validation_status"] == "research_not_validated"

    view = services.evaluate_workspace(
        WorkspaceState(project=project, attachments=attachments),
        "VDD/0",
        modal_max_index=6,
        solver_profile="research_uniform_admittance",
    )
    assert view.solver_profile_badge == "RESEARCH"
    assert view.solver_profile_label == "Research: actual-artwork uniform mode"
    assert view.solver_provenance["artwork_evidence_sha256"] == source.evidence_sha256
    assert view.solver_provenance["research_identity_sha256"] == source.evidence_sha256
    for field in (
        "static_compiler_algorithm_sha256",
        "source_sha256",
        "component_manifest_sha256",
        "material_manifest_sha256",
        "geometry_manifest_sha256",
    ):
        assert len(view.solver_provenance[field]) == 64


def test_research_profile_never_falls_back_without_topology_certificate() -> None:
    project, attachments = _manufactured_project(include_certificate=False)
    template = compile_project_evaluation_template(project, "VDD/0")
    with pytest.raises(ResearchProfileUnavailable) as caught:
        build_uniform_c00_source_model(project, attachments, "VDD/0", template)
    assert caught.value.code == "TOPOLOGY_CERTIFICATE_MISSING"
    assert "Legacy modal" in str(caught.value)


def test_workspace_forwards_progress_and_cancel_into_converged_solver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, attachments = _manufactured_project()
    events: list[tuple[int, str]] = []
    captured: dict[str, object] = {}

    def cancel_probe() -> bool:
        return False

    def fake_converged(
        *_args,
        progress,
        is_cancelled,
        **kwargs,
    ):
        captured["is_cancelled"] = is_cancelled
        captured["max_refinement_iterations"] = kwargs[
            "max_refinement_iterations"
        ]
        captured["max_new_frequency_points"] = kwargs[
            "max_new_frequency_points"
        ]
        progress(50, "inner converged solve")
        raise RuntimeError("stop after callback proof")

    monkeypatch.setattr(
        services, "evaluate_project_rail_converged", fake_converged
    )

    with pytest.raises(RuntimeError, match="stop after callback proof"):
        services.evaluate_workspace(
            WorkspaceState(project=project, attachments=attachments),
            "VDD/0",
            progress=lambda value, message: events.append((value, message)),
            is_cancelled=cancel_probe,
            solver_profile="legacy_modal_v017",
        )

    assert captured["is_cancelled"] is cancel_probe
    assert captured["max_refinement_iterations"] == 3
    assert captured["max_new_frequency_points"] == 64
    assert (57, "inner converged solve") in events


def test_layerwise_workspace_never_runs_without_mounted_termination_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, attachments = _manufactured_project()
    source = SimpleNamespace(substrate=object())
    from spd_decap_pi._core.solver import layerwise_network

    monkeypatch.setattr(
        layerwise_network,
        "build_layerwise_uniform_source_model",
        lambda *_args, **_kwargs: source,
    )

    with pytest.raises(services.EvaluationReadinessError) as caught:
        services.evaluate_workspace(
            WorkspaceState(project=project, attachments=attachments),
            "VDD/0",
            solver_profile="layerwise_admittance_v1",
        )

    assert caught.value.code == "LAYERWISE_TERMINATION_MANIFEST_REQUIRED"


def test_layerwise_records_shared_m12_schema_ceiling_without_stamping_modes() -> None:
    assert services.evaluation_modal_convergence_ceiling(
        8, "layerwise_admittance_v1"
    ) == 12
    assert services.evaluation_modal_convergence_ceiling(
        10, "layerwise_admittance_v1"
    ) == 12
    assert services.evaluation_modal_convergence_ceiling(
        6, "layerwise_admittance_v1"
    ) == 14
    assert services.evaluation_modal_convergence_ceiling(
        8, "legacy_modal_v017"
    ) == 14


def test_missing_topology_certificate_precedes_device_pairing_diagnostic() -> None:
    project, attachments = _manufactured_project(include_certificate=False)
    template = replace(
        compile_project_evaluation_template(project, "VDD/0"),
        pairing_confident=False,
    )
    with pytest.raises(ResearchProfileUnavailable) as caught:
        build_uniform_c00_source_model(project, attachments, "VDD/0", template)
    assert caught.value.code == "TOPOLOGY_CERTIFICATE_MISSING"


def test_research_identity_binds_material_and_static_compiler_manifest() -> None:
    project, attachments = _manufactured_project()
    template = compile_project_evaluation_template(project, "VDD/0")
    source = build_uniform_c00_source_model(project, attachments, "VDD/0", template)
    changed_layers = list(project.stackup_layers)
    changed_layers[1] = changed_layers[1].model_copy(update={"dk": 4.2})
    changed_project = project.model_copy(update={"stackup_layers": changed_layers})
    changed_template = compile_project_evaluation_template(changed_project, "VDD/0")
    changed_source = build_uniform_c00_source_model(
        changed_project, attachments, "VDD/0", changed_template
    )

    assert source.evidence_sha256 != changed_source.evidence_sha256
    settings = _evaluation_settings(
        None,
        6,
        "research_uniform_admittance",
        solver_provenance=source.provenance,
    )
    changed_settings = _evaluation_settings(
        None,
        6,
        "research_uniform_admittance",
        solver_provenance=changed_source.provenance,
    )
    assert settings["research_identity"]["material_manifest_sha256"] != changed_settings["research_identity"]["material_manifest_sha256"]
    assert ScenarioResultKey.from_settings(
        design_fingerprint="b" * 64,
        rail_id="VDD/0",
        settings=settings,
        solver_version="fixture",
    ).cache_key != ScenarioResultKey.from_settings(
        design_fingerprint="b" * 64,
        rail_id="VDD/0",
        settings=changed_settings,
        solver_version="fixture",
    ).cache_key


def test_research_profile_rejects_all_same_name_disconnected_islands() -> None:
    project, attachments = _manufactured_project()
    fractured_asset, fractured_hash = _geometry_asset_many(
        "PWR",
        "VDD",
        (
            [[0.0, 0.0], [400.0, 0.0], [400.0, 1000.0], [0.0, 1000.0]],
            [[600.0, 0.0], [1000.0, 0.0], [1000.0, 1000.0], [600.0, 1000.0]],
        ),
    )
    payload = project.model_dump(mode="python")
    metadata = dict(payload["metadata"])
    spd_import = dict(metadata["spd_import"])
    records = [dict(record) for record in spd_import["plane_geometries"]]
    records[0].update(
        asset="geometry/pwr-fractured.json.zlib", asset_sha256=fractured_hash
    )
    spd_import["plane_geometries"] = records
    certificate = dict(spd_import["uniform_component_connectivity_certificate"])
    certificate["selected_component_sha256"] = _component_hash(
        "PWR", "VDD", fractured_hash
    )
    spd_import["uniform_component_connectivity_certificate"] = certificate
    metadata["spd_import"] = spd_import
    payload["metadata"] = metadata
    fractured_project = ProjectSpec.model_validate(payload)
    fractured_attachments = {
        **attachments,
        "geometry/pwr-fractured.json.zlib": fractured_asset,
    }
    template = compile_project_evaluation_template(fractured_project, "VDD/0")
    with pytest.raises(ResearchProfileUnavailable) as caught:
        build_uniform_c00_source_model(
            fractured_project, fractured_attachments, "VDD/0", template
        )
    assert caught.value.code == "DISCONNECTED_NET_ISLANDS"


def test_research_comparison_is_atomic_and_never_writes_or_reuses_baseline_cache() -> None:
    project, attachments = _manufactured_project()
    scenario = ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixtures/manufactured.spd",
            name="manufactured.spd",
            size_bytes=1,
            sha256="a" * 64,
        ),
        normalized_project=project,
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="a" * 64,
            connections={},
        ),
        attachment_names=sorted(attachments),
        attachment_hashes={
            name: sha256(content).hexdigest() for name, content in attachments.items()
        },
    )
    original_scenario = scenario.model_dump(mode="python")
    original_attachments = dict(attachments)
    result = evaluate_comparison_batch(
        scenario,
        ("VDD/0",),
        modal_max_index=6,
        attachments=attachments,
        solver_profile="research_uniform_admittance",
    )
    assert scenario.model_dump(mode="python") == original_scenario
    assert attachments == original_attachments
    assert result.updated_scenario.evaluation_cache == {}
    assert result.updated_attachments == original_attachments
    comparison = result.comparisons[0]
    assert comparison.baseline_from_cache is False
    assert (
        comparison.baseline.view.solver_provenance
        == comparison.tuned.view.solver_provenance
    )
    assert (
        comparison.baseline.result_key.settings_sha256
        == comparison.tuned.result_key.settings_sha256
    )
    result.validate_for_scenario(scenario)

    no_certificate, no_certificate_attachments = _manufactured_project(
        include_certificate=False
    )
    blocked = ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixtures/blocked.spd",
            name="blocked.spd",
            size_bytes=1,
            sha256="a" * 64,
        ),
        normalized_project=no_certificate,
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="a" * 64,
            connections={},
        ),
        attachment_names=sorted(no_certificate_attachments),
        attachment_hashes={
            name: sha256(content).hexdigest()
            for name, content in no_certificate_attachments.items()
        },
    )
    blocked_before = blocked.model_dump(mode="python")
    attachments_before = dict(no_certificate_attachments)
    with pytest.raises(ResearchProfileUnavailable) as caught:
        evaluate_comparison_batch(
            blocked,
            ("VDD/0",),
            modal_max_index=6,
            attachments=no_certificate_attachments,
            solver_profile="research_uniform_admittance",
        )
    assert caught.value.code == "TOPOLOGY_CERTIFICATE_MISSING"
    assert blocked.model_dump(mode="python") == blocked_before
    assert no_certificate_attachments == attachments_before
