from __future__ import annotations

import csv
from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
import threading
from time import sleep
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QPointF, QRectF, QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import QBrush, QCloseEvent, QColor, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QFileDialog,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsRectItem,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTextBrowser,
    QWidget,
)

from test_io_spd import MINI_SPD
from test_spd_decap_evaluation import _scenario as _evaluation_scenario
from test_spd_decap_scenario_edits import (
    _diagram_scenario,
    _scenario as _shared_pad_scenario,
)
from spd_decap_pi import evaluation as evaluation_module
from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.domain import StackupLayer
from spd_decap_pi._core.services import EvaluationView
from spd_decap_pi._core.solver.profiles import (
    RESEARCH_UNIFORM_ADMITTANCE_PROFILE,
    solver_profile_static_identity_sha256,
)
from spd_decap_pi.distribution import (
    _distribution_plane_geometries,
    _distribution_rail_choices,
)
from spd_decap_pi.gui.worker import FunctionWorker
from spd_decap_pi.gui import main_window as main_window_module
from spd_decap_pi.gui.main_window import (
    MainWindow,
    _EvaluationRunManifest,
    _PlaneArtworkItem,
    _PlanePathBuilder,
    _PreparedScenarioBundle,
    _excel_safe_csv_cell,
    _job_compute_distribution,
    _job_load_scenario,
    _physical_power_plane_layer_labels,
    _prepare_plane_layer_cells,
    _job_preflight_evaluation,
    _modal_convergence_text,
    _rejected_comparison_convergence,
    _source_via_path_recovery_summary,
    _solver_provenance_for_view,
    _shared_pad_connection_summary,
    _short_plane_layer_labels,
    _tuned_decap_csv_rows,
)
from spd_decap_pi.gui.results_window import (
    ComparisonResultsWindow,
    impedance_transition_at_frequency,
    log_log_interpolate_impedance,
)
from spd_decap_pi.scenario import ScenarioDecap, ScenarioResultKey, ScenarioSpec
from spd_decap_pi.scenario_io import ScenarioBundle, save_scenario
from spd_decap_pi.spd_adapter import import_spd_scenario
from spd_decap_pi.version import APP_DISPLAY_NAME


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_preflight_worker_surfaces_raw_spd_refresh_guidance_for_old_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _evaluation_scenario()
    project = scenario.base_project
    cell = project.partitions[0].cells[0].model_copy(
        update={"x_max_um": 100.0, "y_max_um": 100.0}
    )
    project = project.model_copy(
        update={
            "app_version": "0.22.6",
            "partitions": [project.partitions[0].model_copy(update={"cells": [cell]})],
            "metadata": {
                **project.metadata,
                "spd_import": {
                    **project.metadata["spd_import"],
                    "source_sha256": scenario.source.sha256,
                },
            },
        }
    )
    scenario = scenario.model_copy(
        update={"normalized_project": project.model_dump(mode="python")}
    )
    preflight = evaluation_module.preflight_evaluation_connectivity(
        scenario, ("RAIL_VDD",)
    )
    monkeypatch.setattr(
        evaluation_module,
        "preflight_evaluation_comparison",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            evaluation_module.ScenarioEvaluationPreflightError(preflight)
        ),
    )
    with pytest.raises(evaluation_module.ScenarioEvaluationPreflightError) as captured:
        _job_preflight_evaluation(
            scenario,
            ("RAIL_VDD",),
            progress=lambda _value, _message: None,
            is_cancelled=lambda: False,
        )
    assert "SOURCE_GRAPH_PROVENANCE_REFRESH_REQUIRED" in str(captured.value)
    assert "Re-import the matching raw SPD in v0.22.7" in str(captured.value)


def _research_provenance() -> dict[str, object]:
    """Return a complete current-profile identity for GUI-only result fixtures."""

    evidence = "1" * 64
    return {
        "profile_key": "research_uniform_admittance",
        "profile_badge": "RESEARCH",
        "status": "source_only_research",
        "source_only": True,
        "powersi_used_for_parameters": False,
        "validation_status": "research_not_validated",
        "artwork_evidence_sha256": evidence,
        "research_identity_sha256": evidence,
        "static_compiler_algorithm_sha256": (
            solver_profile_static_identity_sha256(
                RESEARCH_UNIFORM_ADMITTANCE_PROFILE
            )
        ),
        "source_sha256": "2" * 64,
        "geometry_manifest_sha256": "3" * 64,
        "component_manifest_sha256": "4" * 64,
        "material_manifest_sha256": "5" * 64,
        "topology_certificate_sha256": "6" * 64,
    }


def test_function_worker_coalesces_progress_before_it_reaches_the_gui() -> None:
    delivered: list[tuple[int, str]] = []

    def noisy_job(*, progress, is_cancelled) -> str:
        for value in range(2_000):
            assert not is_cancelled()
            progress(value % 100, f"step {value}")
        progress(100, "complete")
        return "done"

    worker = FunctionWorker(noisy_job)
    worker.signals.progress.connect(
        lambda value, message: delivered.append((value, message))
    )
    worker.run()

    assert delivered[0] == (0, "step 0")
    assert delivered[-1] == (100, "complete")
    assert len(delivered) < 20


def test_incremental_plane_builder_yields_to_the_qt_event_loop_for_large_polygon() -> None:
    """A single huge PowerSI polygon must not monopolize a GUI render callback."""

    application = _application()
    geometry = {
        "positive_polygons_um": [
            [
                (float(index), float((index * index) % 97))
                for index in range(12_000)
            ]
        ],
        "negative_polygons_um": [],
        "positive_circles_um": [],
        "negative_circles_um": [],
        "primitive_order": [("positive_polygon", 0)],
    }
    builder = _PlanePathBuilder(geometry, QColor("#2563EB"))
    heartbeats: list[int] = []
    render_calls: list[int] = []
    loop = QEventLoop()

    heartbeat = QTimer()
    heartbeat.setInterval(1)
    heartbeat.timeout.connect(lambda: heartbeats.append(1))

    def render_step() -> None:
        render_calls.append(1)
        if builder.step(1, maximum_points=48):
            loop.quit()
            return
        QTimer.singleShot(0, render_step)

    heartbeat.start()
    QTimer.singleShot(0, render_step)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(3_000)
    loop.exec()
    heartbeat.stop()

    assert builder.done
    assert len(render_calls) > 100
    assert heartbeats
    assert builder.item() is not None


def test_function_worker_runs_jobs_off_the_qt_gui_thread() -> None:
    application = _application()
    main_thread_id = threading.get_ident()
    results: list[int] = []
    heartbeats: list[None] = []
    loop = QEventLoop(application)
    heartbeat = QTimer(application)
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: heartbeats.append(None))
    worker = FunctionWorker(
        lambda **_kwargs: sleep(0.12) or threading.get_ident()
    )
    worker.signals.result.connect(lambda result: results.append(result))
    worker.signals.finished.connect(loop.quit)

    heartbeat.start()
    QThreadPool.globalInstance().start(worker)
    QTimer.singleShot(5_000, loop.quit)
    loop.exec()
    heartbeat.stop()

    assert results and results == [results[0]]
    assert results[0] != main_thread_id
    assert len(heartbeats) >= 5


def test_cancel_after_worker_result_emission_does_not_apply_queued_result() -> None:
    """Cancellation wins even when the worker returned before Qt delivers result."""

    application = _application()
    window = MainWindow()
    applied: list[str] = []
    worker = FunctionWorker(lambda **_kwargs: "must not apply")
    try:
        window._run_worker(worker, applied.append, label="Testing cancellation")
        assert QThreadPool.globalInstance().waitForDone(3_000)
        # QRunnable signals are queued, but the GUI event queue is not pumped.
        window._cancel_worker()
        application.processEvents()
        assert applied == []
    finally:
        window.close()
        application.processEvents()


def test_distribution_worker_prepares_preview_and_export_before_gui_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from spd_decap_pi import distribution as distribution_module

    plan = object()
    preview = object()
    calls: list[str] = []
    progress: list[tuple[int, str]] = []
    monkeypatch.setattr(
        distribution_module,
        "compute_distribution_plan",
        lambda *_args, **kwargs: calls.append("plan") or plan,
    )
    monkeypatch.setattr(
        distribution_module,
        "apply_distribution_plan",
        lambda *_args, **_kwargs: calls.append("preview") or preview,
    )
    monkeypatch.setattr(
        distribution_module,
        "distribution_csv_rows",
        lambda _plan: calls.append("export")
        or (("Component", "REFDES"), ("CAP", "C1")),
    )
    monkeypatch.setattr(
        distribution_module,
        "build_distribution_power_projection",
        lambda *_args, **_kwargs: calls.append("projection") or object(),
    )
    monkeypatch.setattr(
        distribution_module,
        "validate_distribution_targets",
        lambda *_args, **_kwargs: calls.append("validate"),
    )

    result = _job_compute_distribution(
        object(),
        {},
        {},
        {},
        object(),
        progress=lambda value, message: progress.append((value, message)),
        is_cancelled=lambda: False,
    )

    assert calls == ["projection", "validate", "plan", "preview", "export"]
    assert result.plan is plan
    assert result.preview_scenario is preview
    assert result.export_rows == (("CAP", "C1"),)
    assert progress[-2:] == [
        (92, "Preparing atomic De-cap Distribution preview"),
        (100, "De-cap Distribution preview complete"),
    ]


def test_main_window_exposes_sibling_identity_and_evaluation_only_workflow() -> None:
    application = _application()
    window = MainWindow()
    try:
        labels = " ".join(
            item.text() for item in window.findChildren(QLabel) if item.text()
        )
        assert window.windowTitle() == APP_DISPLAY_NAME
        assert APP_DISPLAY_NAME in labels
        assert "Plot Analyst only" in labels
        assert "fills are read-only PowerSI artwork" in labels
        assert "dashed rectangles mark the solver" in labels
        # Distribution exposes a legitimate optimization-policy control; the
        # evaluation-only boundary is the explicit AI restriction and absence
        # of an optimization action in the AI controls.
        assert "Optimization policy" in labels
        assert (
            "AI receives solver-derived features and cannot change PWR assignments, "
            "enable decaps, or run optimization."
        ) in labels
        assert not any(
            "optimization" in item.text().casefold()
            for item in window.findChildren(QPushButton)
        )
        assert not window.evaluate_button.isEnabled()
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_evaluation_layout_uses_an_expanding_rail_list_and_detached_plot_button() -> None:
    application = _application()
    window = MainWindow()
    try:
        window.show()
        application.processEvents()
        assert window.size().width() >= 1500
        assert not hasattr(window, "plot")
        assert window.rail_list.maximumHeight() > 10_000
        assert window.rail_list.minimumHeight() == 140
        assert window.open_results_button.text() == "Open Result Plot"
        assert not window.open_results_button.isEnabled()
        assert window.export_tuned_csv_button.text() == "Export Tuned CSV..."
        assert not window.export_tuned_csv_button.isEnabled()
        assert not window.evaluation_alternate_pair_checkbox.isChecked()
        assert "fallback" in window.evaluation_alternate_pair_checkbox.text().casefold()
        assert "strict exact" in window.evaluation_alternate_pair_checkbox.toolTip().casefold()
        assert window.evaluation_modal_preset_combo.currentData() == 8
        assert window.evaluation_modal_preset_combo.currentText() == "Balanced (81 modes)"
        maximum_index = window.evaluation_modal_preset_combo.findData(12)
        assert maximum_index >= 0
        assert window.evaluation_modal_preset_combo.itemText(maximum_index) == (
            "Experimental m12 check (169 modes)"
        )
        assert "4,139 s" in window.evaluation_modal_preset_combo.toolTip()
        assert window.evaluation_solver_profile_combo.currentData() == (
            "legacy_modal_v017"
        )
        assert window.evaluation_solver_profile_combo.currentText() == "Legacy modal"
        research_index = window.evaluation_solver_profile_combo.findData(
            "research_uniform_admittance"
        )
        assert research_index >= 0
        assert window.evaluation_solver_profile_combo.itemText(research_index) == (
            "Experimental: actual-artwork uniform C00 "
            "(topology certificate required)"
        )
        assert "comparison-only" in window.evaluation_solver_profile_combo.toolTip()
        assert "LEGACY" in window.evaluation_solver_profile_status.text()
        notes = window.findChild(QTextBrowser, "evaluationNotes")
        assert notes is not None
        assert notes.toPlainText().startswith("Selected physics model: [LEGACY]")
        assert "actual-artwork uniform C00" in notes.toPlainText()
        assert "without falling back" in notes.toPlainText()
        assert "not a PowerSI or absolute-accuracy setting" in notes.toPlainText()
        assert "absolute sub-milliohm accuracy not certified" in notes.toPlainText()
    finally:
        window.close()
        application.processEvents()


def test_short_plane_layer_labels_follow_physical_stack_order() -> None:
    stackup = (
        StackupLayer(
            name="Signal$TOP",
            thickness_um=20.0,
            conductivity_s_m=5.8e7,
        ),
        StackupLayer(name="D1", thickness_um=100.0, dk=4.0),
        StackupLayer(
            name="Signal$PWR_A",
            thickness_um=20.0,
            conductivity_s_m=5.8e7,
        ),
        StackupLayer(name="D2", thickness_um=100.0, dk=4.0),
        StackupLayer(
            name="Signal$PWR_B",
            thickness_um=20.0,
            conductivity_s_m=5.8e7,
        ),
    )

    assert _short_plane_layer_labels(
        stackup,
        ("Signal$PWR_B", "Signal$TOP", "Signal$PWR_A", "Signal$PWR_A"),
    ) == (
        ("Signal$TOP", "T"),
        ("Signal$PWR_A", "P1"),
        ("Signal$PWR_B", "P2"),
    )
    assert _short_plane_layer_labels(
        stackup,
        ("Signal$PWR_B", "Signal$PWR_A"),
    ) == (
        ("Signal$PWR_A", "P1"),
        ("Signal$PWR_B", "P2"),
    )


def test_physical_power_plane_labels_cover_all_21_real_stack_layers() -> None:
    physical_names = (
        "Signal$L09(MAIN_POWER1)",
        "Signal$L11(MAIN_POWER2)",
        "Signal$L12(MAIN_POWER3)",
        "Signal$L14(MAIN_POWER4)",
        "Signal$L15(MAIN_POWER5)",
        "Signal$L22(MAIN_POWER2)",
        "Signal$L23(MAIN_POWER3)",
        "Signal$L25(MAIN_POWER4)",
        "Signal$L26(MAIN_POWER5)",
        "Signal$L30(OTHER_POWER1)",
        "Signal$L31(OTHER_POWER2)",
        "Signal$L33(OTHER_POWER3)",
        "Signal$L34(OTHER_POWER4)",
        "Signal$L36(OTHER_POWER5)",
        "Signal$L37(OTHER_POWER6)",
        "Signal$L39(OTHER_POWER7)",
        "Signal$L40(OTHER_POWER8)",
        "Signal$L42(OTHER_POWER9)",
        "Signal$L43(OTHER_POWER10)",
        "Signal$L45(OTHER_POWER11)",
        "Signal$L46(OTHER_POWER12)",
    )
    top_name = "Signal$TOP"
    layers = (
        StackupLayer(
            name=top_name,
            thickness_um=20.0,
            conductivity_s_m=5.8e7,
            pwr_nets=["VDD_CORE/0", "DGND"],
        ),
        *(
            StackupLayer(
                name=name,
                thickness_um=20.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD_CORE/0"],
            )
            for name in physical_names
        ),
    )
    records = [
        {
            "layer": name,
            "net": "VDD_CORE/0",
            "asset": f"geometry/{index:02d}.spdgeom.zlib",
            "asset_sha256": sha256(name.encode("utf-8")).hexdigest(),
            "uncompressed_bytes": 1,
        }
        for index, name in enumerate((top_name, *physical_names))
    ]
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "selected_power_nets": ["VDD_CORE/0"],
                "plane_geometries": records,
            }
        },
        rails=(),
        stackup_layers=layers,
        partitions=(),
    )

    assert _physical_power_plane_layer_labels(project) == tuple(
        (name, f"P{index}")
        for index, name in enumerate(physical_names, start=1)
    )


def test_loaded_spd_lists_every_physical_pwr_layer_not_only_solver_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    source = tmp_path / "three-power-layers.spd"
    shape_block = """.Shape Signal$PWRpkgshape
Polygon2::VDD_CORE/0+ -4mm -3mm 4mm -3mm
+ 4mm 3mm -4mm 3mm
Polygon3::VDD_CORE/0- ViaHole_A Sub-element -0.2mm -0.2mm 0.2mm -0.2mm 0.2mm 0.2mm -0.2mm 0.2mm
Circle4::VDD_CORE/0- ViaHole_A Sub-element 1mm 1mm 0.06mm
Polygon5::VDD_CORE/0+ Sub-element 2mm 2mm 2.2mm 2mm 2.2mm 2.2mm 2mm 2.2mm
.EndShape"""
    physical_shapes = """.Shape Signal$L05(SIG2)pkgshape
Polygon10::VDD_CORE/0+ -2mm -2mm 2mm -2mm 2mm 2mm -2mm 2mm
Polygon11::SIG_DATA+ -1mm -1mm 1mm -1mm 1mm 1mm -1mm 1mm
.EndShape
.Shape Signal$L09(MAIN_POWER1)pkgshape
Polygon20::VDD_CORE/0+ -4mm -3mm 4mm -3mm 4mm 3mm -4mm 3mm
.EndShape
.Shape Signal$L10(DGND)pkgshape
Polygon30::VDD_CORE/0+ -2mm -2mm 2mm -2mm 2mm 2mm -2mm 2mm
Polygon31::DGND+ -4mm -3mm 4mm -3mm 4mm 3mm -4mm 3mm
.EndShape
.Shape Signal$L11(MAIN_POWER2)pkgshape
Polygon21::VDD_CORE/0+ -4mm -3mm 4mm -3mm 4mm 3mm -4mm 3mm
.EndShape
.Shape Signal$L12(MAIN_POWER3)pkgshape
Polygon2::VDD_CORE/0+ -4mm -3mm 4mm -3mm
+ 4mm 3mm -4mm 3mm
Polygon3::VDD_CORE/0- ViaHole_A Sub-element -0.2mm -0.2mm 0.2mm -0.2mm 0.2mm 0.2mm -0.2mm 0.2mm
Circle4::VDD_CORE/0- ViaHole_A Sub-element 1mm 1mm 0.06mm
Polygon5::VDD_CORE/0+ Sub-element 2mm 2mm 2.2mm 2mm 2.2mm 2.2mm 2mm 2.2mm
.EndShape"""
    layer_block = """Signal$PWR Thickness = 20u Material = COPPER
Medium$D2 Thickness = 100um Material = ABF
Signal$GND Thickness = 20u Material = COPPER"""
    physical_layers = """Signal$L05(SIG2) Thickness = 20u Material = COPPER
Medium$D1B Thickness = 100um Material = ABF
Signal$L09(MAIN_POWER1) Thickness = 20u Material = COPPER
Medium$D2 Thickness = 100um Material = ABF
Signal$L10(DGND) Thickness = 20u Material = COPPER
Medium$D2B Thickness = 100um Material = ABF
Signal$L11(MAIN_POWER2) Thickness = 20u Material = COPPER
Medium$D3 Thickness = 100um Material = ABF
Signal$L12(MAIN_POWER3) Thickness = 20u Material = COPPER
Medium$D4 Thickness = 100um Material = ABF
Signal$GND Thickness = 20u Material = COPPER"""
    source.write_text(
        MINI_SPD.replace(shape_block, physical_shapes)
        .replace(layer_block, physical_layers)
        .replace(".PadDef Signal$PWR", ".PadDef Signal$L12(MAIN_POWER3)"),
        encoding="ascii",
    )
    imported = import_spd_scenario(source)
    project = imported.scenario.base_project

    assert [partition.layer for partition in project.partitions] == [
        "Signal$L12(MAIN_POWER3)"
    ]

    rail = project.rails[0]
    distribution_choices, _distribution_pairs = _distribution_rail_choices(
        imported.scenario,
        {rail.rail_id.casefold()},
    )
    wanted_pairs = {
        (net_key, pwr_layer_key)
        for net_key, pwr_layer_key, _gnd_layer_key in distribution_choices
    }
    distribution_planes = _distribution_plane_geometries(
        imported.scenario,
        imported.attachments,
        wanted_pairs=wanted_pairs,
    )
    distribution_layer_keys = {
        item.layer.casefold()
        for item in distribution_planes
        if item.net.casefold() == rail.net.casefold()
    }
    expected_distribution_layer_keys = {
        "signal$l05(sig2)",
        "signal$l09(main_power1)",
        "signal$l10(dgnd)",
        "signal$l11(main_power2)",
        "signal$l12(main_power3)",
    }
    assert {
        pwr_layer_key
        for net_key, pwr_layer_key, _gnd_layer_key in distribution_choices
        if net_key == rail.net.casefold()
    } == expected_distribution_layer_keys
    assert distribution_layer_keys == expected_distribution_layer_keys

    duplicate_project = project.model_copy(deep=True)
    duplicate_records = duplicate_project.metadata["spd_import"]["plane_geometries"]
    duplicate_records.append(
        dict(
            next(
                record
                for record in duplicate_records
                if record["layer"] == "Signal$L09(MAIN_POWER1)"
            )
        )
    )
    with pytest.raises(ValueError, match="duplicate layer/NET"):
        _physical_power_plane_layer_labels(duplicate_project)

    mismatched_project = project.model_copy(deep=True)
    mismatched_project.partitions[0].cells[0].source_geometry_sha256 = "0" * 64
    with pytest.raises(ValueError, match="does not bind retained artwork index"):
        _prepare_plane_layer_cells(
            mismatched_project,
            imported.attachments,
            "Signal$L12(MAIN_POWER3)",
            progress=lambda _value, _message: None,
            is_cancelled=lambda: False,
        )

    multi_project = project.model_copy(deep=True)
    multi_records = multi_project.metadata["spd_import"]["plane_geometries"]
    base_record = next(
        record
        for record in multi_records
        if record["layer"] == "Signal$L12(MAIN_POWER3)"
    )
    multi_attachments = dict(imported.attachments)
    payload = core_services.spd_plane_geometry_record_payload(
        base_record, multi_attachments
    )
    positive_polygons = [
        [list(point) for point in polygon]
        for polygon in payload["positive_polygons_um"]
    ]
    positive_polygons[0][0][0] += 0.001
    compressed, decoded_bytes = core_services._compress_spd_geometry_payload(
        layer=base_record["layer"],
        net=base_record["net"],
        positive_polygons=positive_polygons,
        negative_polygons=payload["negative_polygons_um"],
        positive_circles=payload["positive_circles_um"],
        negative_circles=payload["negative_circles_um"],
        primitive_order=payload["primitive_order"],
        positive_subelement_count=payload["positive_subelement_count"],
        negative_subelement_count=payload["negative_subelement_count"],
        polygon_trace_count=payload["polygon_trace_count"],
        box_count=payload["box_count"],
    )
    second_digest = sha256(compressed).hexdigest()
    second_asset = f"geometry/multi-{second_digest[:16]}.spdgeom.zlib"
    multi_attachments[second_asset] = compressed
    multi_records.append(
        {
            **base_record,
            "asset": second_asset,
            "asset_sha256": second_digest,
            "uncompressed_bytes": decoded_bytes,
            "compressed_bytes": len(compressed),
        }
    )
    multi_cells = _prepare_plane_layer_cells(
        multi_project,
        multi_attachments,
        "Signal$L12(MAIN_POWER3)",
        progress=lambda _value, _message: None,
        is_cancelled=lambda: False,
    )
    assert len(multi_cells) == 2
    assert sum(item.cell is not None for item in multi_cells) == 1

    legacy_project = project.model_copy(deep=True)
    legacy_project.metadata["spd_import"].pop("plane_geometries")
    with monkeypatch.context() as patch:
        patch.setattr(
            main_window_module,
            "_MAX_PHYSICAL_PWR_PATH_ELEMENTS_PER_LAYER",
            1,
        )
        with pytest.raises(ValueError, match="per-layer preview path limit"):
            _prepare_plane_layer_cells(
                legacy_project,
                imported.attachments,
                "Signal$L12(MAIN_POWER3)",
                progress=lambda _value, _message: None,
                is_cancelled=lambda: False,
            )

    window = MainWindow()
    try:
        window._accept_spd_import(imported)
        application.processEvents()

        def wait_for_plane_idle() -> None:
            timeout = QTimer(window)
            timeout.setSingleShot(True)
            loop = QEventLoop(window)
            timeout.timeout.connect(loop.quit)

            def finish_when_idle() -> None:
                if (
                    window._worker is None
                    and not window._plane_render_cells
                    and not window._load_all_plane_layers_requested
                ):
                    loop.quit()
                else:
                    QTimer.singleShot(5, finish_when_idle)

            QTimer.singleShot(0, finish_when_idle)
            timeout.start(5_000)
            loop.exec()
            assert window._worker is None
            assert not window._plane_render_cells

        assert tuple(window._plane_layer_checks) == (
            "signal$l09(main_power1)",
            "signal$l11(main_power2)",
            "signal$l12(main_power3)",
        )
        assert tuple(
            (checkbox.text(), checkbox.toolTip())
            for checkbox in window._plane_layer_checks.values()
        ) == (
            ("P1", "P1: Signal$L09(MAIN_POWER1)"),
            ("P2", "P2: Signal$L11(MAIN_POWER2)"),
            ("P3", "P3: Signal$L12(MAIN_POWER3)"),
        )
        artwork_layers = {
            str(item.data(2))
            for item in window.board._plane_items
            if isinstance(item, _PlaneArtworkItem)
        }
        assert artwork_layers == {"Signal$L09(MAIN_POWER1)"}
        assert window._plane_loaded_layer_keys == {"signal$l09(main_power1)"}
        assert "signal$l05(sig2)" not in window._plane_layer_checks
        assert "signal$l10(dgnd)" not in window._plane_layer_checks

        window._set_all_plane_layers_visible(True)
        wait_for_plane_idle()
        p2 = window._plane_layer_checks["signal$l11(main_power2)"]
        artwork_layers = {
            str(item.data(2))
            for item in window.board._plane_items
            if isinstance(item, _PlaneArtworkItem)
        }
        assert artwork_layers == {
            "Signal$L09(MAIN_POWER1)",
            "Signal$L11(MAIN_POWER2)",
            "Signal$L12(MAIN_POWER3)",
        }
        solver_layers = {
            str(item.data(2))
            for item in window.board._plane_items
            if item.data(0) == "solver_bounds"
        }
        assert solver_layers == {"Signal$L12(MAIN_POWER3)"}

        cached_elements = window._plane_cached_path_elements
        cached_items = len(window.board._plane_items)
        p2.setChecked(False)
        p2.setChecked(True)
        application.processEvents()
        assert window._worker is None
        assert window._plane_cached_path_elements == cached_elements
        assert len(window.board._plane_items) == cached_items
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_shared_pad_load_summary_exposes_blocked_connectivity_counts() -> None:
    compact, details = _shared_pad_connection_summary(_shared_pad_scenario())

    assert compact == "Pad/Via A 2 · D 1 · F 1 · U 0"
    assert "Direct: 1" in details
    assert "Unresolved (PWR edits blocked): 0" in details
    assert "Anchored clusters: 1" in details


def test_source_via_path_summary_discloses_zero_recovery_fallback() -> None:
    scenario = _shared_pad_scenario()
    project = scenario.base_project.model_copy(
        update={
            "metadata": {
                **scenario.base_project.metadata,
                "spd_via_path_recovery": {
                    "requested": 60_152,
                    "recovered": 0,
                    "fallback": 60_152,
                    "algorithm": "unique_monotonic_same_net_via_chain_v1",
                },
            }
        }
    )
    scenario = scenario.model_copy(update={"normalized_project": project})

    compact, details = _source_via_path_recovery_summary(scenario)

    assert compact == "Source Via paths: 0/60,152 recovered; 60,152 fallback"
    assert "No source segment R/L applied; legacy rail templates used" in details


def test_plane_layer_checkboxes_support_independent_multi_layer_visibility() -> None:
    application = _application()
    window = MainWindow()
    top_item = QGraphicsRectItem(0.0, 0.0, 10.0, 10.0)
    pwr_item = QGraphicsRectItem(20.0, 0.0, 10.0, 10.0)
    top_item.setData(2, "Signal$TOP")
    pwr_item.setData(2, "Signal$PWR")
    try:
        window.board.set_plane_items((top_item, pwr_item))
        window._plane_items_by_layer = {
            "signal$top": [top_item],
            "signal$pwr": [pwr_item],
        }
        window._rebuild_plane_layer_controls(
            (("Signal$TOP", "T"), ("Signal$PWR", "P1"))
        )
        top_toggle = window._plane_layer_checks["signal$top"]
        pwr_toggle = window._plane_layer_checks["signal$pwr"]

        assert top_toggle.isChecked()
        assert not pwr_toggle.isChecked()
        assert top_toggle.toolTip() == "T: Signal$TOP"
        assert pwr_toggle.toolTip() == "P1: Signal$PWR"
        item_ids = tuple(id(item) for item in window.board._plane_items)

        pwr_toggle.setChecked(True)
        assert top_item.isVisible()
        assert pwr_item.isVisible()
        top_toggle.setChecked(False)
        assert not top_item.isVisible()
        assert pwr_item.isVisible()
        pwr_toggle.setChecked(False)
        assert not top_item.isVisible()
        assert not pwr_item.isVisible()
        top_toggle.setChecked(True)
        assert top_item.isVisible()
        assert not pwr_item.isVisible()
        assert tuple(id(item) for item in window.board._plane_items) == item_ids
        assert not window._dirty

        window._set_all_plane_layers_visible(True)
        assert top_item.isVisible()
        assert pwr_item.isVisible()
        assert all(
            checkbox.isChecked()
            for checkbox in window._plane_layer_checks.values()
        )
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_reset_document_view_state_releases_previous_plane_artwork() -> None:
    application = _application()
    window = MainWindow()
    old_item = QGraphicsRectItem(0.0, 0.0, 10.0, 10.0)
    try:
        window.board.set_plane_items((old_item,))
        window._plane_items_by_net = {"vdd": [old_item]}
        window._plane_items_by_layer = {"signal$top": [old_item]}

        window._reset_document_view_state()

        assert window.board._plane_items == []
        assert window._plane_items_by_net == {}
        assert window._plane_items_by_layer == {}
        assert old_item.scene() is None
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_batched_plane_void_is_transparent_to_other_visible_layers() -> None:
    def rectangle(x: float, y: float, width: float, height: float) -> QPainterPath:
        path = QPainterPath()
        path.addRect(x, y, width, height)
        return path

    lower = _PlaneArtworkItem(
        (("positive_polygon", rectangle(4.0, 4.0, 56.0, 56.0)),),
        QColor("#00FF00"),
        {"positive_polygon"},
    )
    upper = _PlaneArtworkItem(
        (
            ("positive_polygon", rectangle(4.0, 4.0, 56.0, 56.0)),
            ("negative_polygon", rectangle(20.0, 20.0, 24.0, 24.0)),
        ),
        QColor("#FF0000"),
        {"positive_polygon", "negative_polygon"},
    )

    def rendered(*items: _PlaneArtworkItem) -> QImage:
        scene = QGraphicsScene()
        scene.setBackgroundBrush(QColor("#171a1f"))
        for z_value, item in enumerate(items):
            item.setZValue(float(z_value))
            scene.addItem(item)
        image = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#171a1f"))
        painter = QPainter(image)
        scene.render(
            painter,
            QRectF(0.0, 0.0, 64.0, 64.0),
            QRectF(0.0, 0.0, 64.0, 64.0),
        )
        painter.end()
        return image

    lower_only = rendered(
        _PlaneArtworkItem(
            (("positive_polygon", rectangle(4.0, 4.0, 56.0, 56.0)),),
            QColor("#00FF00"),
            {"positive_polygon"},
        )
    )
    upper_only = rendered(
        _PlaneArtworkItem(
            (
                ("positive_polygon", rectangle(4.0, 4.0, 56.0, 56.0)),
                ("negative_polygon", rectangle(20.0, 20.0, 24.0, 24.0)),
            ),
            QColor("#FF0000"),
            {"positive_polygon", "negative_polygon"},
        )
    )
    assert upper.cacheMode() == QGraphicsItem.CacheMode.DeviceCoordinateCache
    both = rendered(lower, upper)

    assert both.pixelColor(32, 32) == lower_only.pixelColor(32, 32)
    assert upper_only.pixelColor(32, 32) == QColor("#171a1f")
    assert both.pixelColor(10, 10) != lower_only.pixelColor(10, 10)


def test_right_side_sections_are_vertically_resizable_and_noncollapsible() -> None:
    application = _application()
    window = MainWindow()
    try:
        expected_minimums = {
            "selectionSectionSplitter": (170, 160),
            "evaluationSectionSplitter": (285, 180),
            "aiSectionSplitter": (210, 160),
        }
        for object_name, minimums in expected_minimums.items():
            splitter = window.findChild(QSplitter, object_name)
            assert splitter is not None
            assert splitter.orientation() == Qt.Orientation.Vertical
            assert splitter.count() == 2
            assert not splitter.childrenCollapsible()
            assert splitter.handleWidth() == 10
            assert "#94A3B8" in splitter.styleSheet()
            assert tuple(
                splitter.widget(index).minimumHeight() for index in range(2)
            ) == minimums
    finally:
        window.close()
        application.processEvents()


def test_evaluation_splitter_keeps_picker_and_result_tabs_usable_at_1200_by_700() -> None:
    application = _application()
    window = MainWindow()
    try:
        window.resize(1200, 700)
        tabs = window.findChild(QTabWidget)
        assert tabs is not None
        tabs.setCurrentIndex(1)
        window.show()
        application.processEvents()

        assert window.findChild(QSplitter, "evaluationResultSplitter") is None
        section_splitter = window.findChild(QSplitter, "evaluationSectionSplitter")
        assert section_splitter is not None
        controls_size, results_size = section_splitter.sizes()
        assert controls_size >= 230
        assert results_size >= 180
        assert window.rail_list.height() >= 140
        controls = window.findChild(QWidget, "evaluationControlsSection")
        assert controls is not None
        assert window.rail_list.width() >= int(controls.width() * 0.9)
        rail_label = window.findChild(QLabel, "evaluationRailLabel")
        assert rail_label is not None
        assert rail_label.isVisible()
        assert rail_label.text() == "PWR NETs"
        assert rail_label.buddy() is window.rail_list
        assert window.rail_list.accessibleName() == "PWR NETs"
        assert window.comparison_table.height() >= 60
        assert window.open_results_button.isVisible()
        assert (
            window.open_results_button.width()
            >= window.open_results_button.sizeHint().width()
        )
        assert (
            window.export_tuned_csv_button.width()
            >= window.export_tuned_csv_button.sizeHint().width()
        )

        section_splitter.setSizes((500, 200))
        application.processEvents()
        expanded_controls = tuple(section_splitter.sizes())
        expanded_list_height = window.rail_list.height()
        section_splitter.setSizes((260, 440))
        application.processEvents()
        compact_controls = tuple(section_splitter.sizes())
        compact_list_height = window.rail_list.height()
        assert expanded_controls[0] > compact_controls[0]
        assert expanded_controls[1] < compact_controls[1]
        assert expanded_list_height > compact_list_height
    finally:
        window.close()
        application.processEvents()


def test_detached_result_window_closes_with_the_main_window() -> None:
    application = _application()
    window = MainWindow()
    result_window = ComparisonResultsWindow(window)
    window._results_window = result_window
    try:
        window.show()
        result_window.show()
        application.processEvents()
        assert result_window.isVisible()

        window.close()
        application.processEvents()
        assert not window.isVisible()
        assert not result_window.isVisible()
    finally:
        result_window.close()
        window.close()
        application.processEvents()


def test_tuned_csv_rows_filter_final_assignments_and_escape_excel_formulas() -> None:
    scenario = SimpleNamespace(
        decaps=(
            SimpleNamespace(
                model_id="=MODEL()",
                refdes="+C1",
                current_net="-0V8",
                current_rail_id="RAIL_A",
                enabled=True,
            ),
            SimpleNamespace(
                model_id="CAP_B",
                refdes="C2",
                current_net="VDD_B",
                current_rail_id="rail_b",
                enabled=True,
            ),
            SimpleNamespace(
                model_id="CAP_DISABLED",
                refdes="C3",
                current_net="VDD_A",
                current_rail_id="RAIL_A",
                enabled=False,
            ),
            SimpleNamespace(
                model_id="CAP_OTHER",
                refdes="C4",
                current_net="VDD_C",
                current_rail_id="RAIL_C",
                enabled=True,
            ),
        )
    )

    assert _tuned_decap_csv_rows(scenario, ("rail_a", "RAIL_B")) == (
        ("'=MODEL()", "'+C1", "'-0V8"),
        ("CAP_B", "C2", "VDD_B"),
    )


def test_tuned_csv_rows_exclude_floating_dummy_from_validated_scenario() -> None:
    scenario = _shared_pad_scenario()

    rows = _tuned_decap_csv_rows(scenario, ("R1",))

    assert {row[1] for row in rows} == {"A", "B", "D", "X"}
    assert "F" not in {row[1] for row in rows}
    assert _excel_safe_csv_cell("@SUM(A1:A2)") == "'@SUM(A1:A2)"
    assert _excel_safe_csv_cell("\t=CMD") == "'\t=CMD"


def test_result_table_impedance_uses_log_log_interpolation_and_compact_units() -> None:
    class View:
        frequency_hz = [1.0e6, 100.0e6]

        def __init__(self, magnitude_ohm: list[float]) -> None:
            self.magnitude_ohm = magnitude_ohm

    original = View([0.01, 0.1])
    tuned = View([0.005, 0.05])

    assert log_log_interpolate_impedance(
        original.frequency_hz, original.magnitude_ohm, 10.0e6
    ) == pytest.approx(0.01 * (10.0 ** 0.5))
    assert (
        impedance_transition_at_frequency(original, tuned, 10.0e6)
        == "31.6→15.8 mΩ"
    )
    assert log_log_interpolate_impedance(
        original.frequency_hz, original.magnitude_ohm, 1.0e9
    ) is None
    assert impedance_transition_at_frequency(original, tuned, 1.0e9) == "N/A"


def test_loaded_spd_supports_pwr_net_search_and_disabled_electrical_state(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "gui.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    try:
        window._accept_spd_import(imported)
        assert window.board.record_count == 2
        assert window.evaluate_button.isEnabled()
        assert "eligibility" in window.status_text.text()
        assert "Mixed-reference witness selection" in window.status_text.text()
        assert "Mixed-reference GND recovery" in window.status_text.text()
        assert "SPD analysis:" in window.status_text.toolTip()
        assert "Spatial index build:" in window.status_text.toolTip()
        assert "Mixed-reference witness selection:" in window.status_text.toolTip()
        assert "Mixed-reference GND recovery:" in window.status_text.toolTip()
        assert "Board scene build:" in window.status_text.toolTip()
        assert "Pad/Via A " in window.status_text.text()
        assert "Unresolved (PWR edits blocked):" in window.status_text.toolTip()
        rail_item = window.rail_list.item(0)
        rail = window.scenario.base_project.rails[0]
        swatch = rail_item.icon().pixmap(QSize(12, 12)).toImage()
        assert not swatch.isNull()
        assert swatch.pixelColor(6, 6).name() == QColor(
            window.scenario.net_colors[rail.net]
        ).name()
        assert rail_item.foreground() == QBrush()
        artwork_items = [
            item
            for item in window.board._plane_items
            if isinstance(item, _PlaneArtworkItem)
        ]
        primitive_kinds = {
            kind
            for item in artwork_items
            for kind in item.primitive_kinds
        }
        primitive_kinds.update(
            item.data(0)
            for item in window.board._plane_items
            if not isinstance(item, _PlaneArtworkItem)
        )
        assert {
            "positive_polygon",
            "negative_polygon",
            "negative_circle",
            "solver_bounds",
        }.issubset(primitive_kinds)
        assert tuple(kind for kind, _path in artwork_items[0]._runs) == (
            "positive_polygon",
            "negative_polygon",
            "negative_circle",
            "positive_polygon",
        )
        assert set(window._plane_layer_checks) == {"signal$pwr"}
        plane_toggle = window._plane_layer_checks["signal$pwr"]
        assert plane_toggle.text() == "P1"
        assert plane_toggle.toolTip() == "P1: Signal$PWR"
        assert plane_toggle.isChecked()
        assert all(
            item.data(2) == "Signal$PWR"
            for item in window.board._plane_items
        )
        assert len(artwork_items) == 1
        assert window.board.bump_count == 2
        assert window.board.tooltip_text_at(QPointF(0.0, 0.0)) == "VDD_CORE/0"
        assert window.board.tooltip_text_at(QPointF(100.0, 0.0)) == "DGND"
        configured_color = QColor(window.scenario.net_colors[rail.net]).name()
        inactive_color = window.board.INACTIVE_NET_COLOR.name()
        assert (
            window.board._bump_scatters_by_net[rail.net.casefold()]
            .points()[0]
            .brush()
            .color()
            .name()
            == configured_color
        )
        assert (
            window.board._bump_scatters_by_net["dgnd"]
            .points()[0]
            .brush()
            .color()
            .name()
            == inactive_color
        )

        selected_before_layer_toggle = window.board.selected_refdes
        plane_item_ids = tuple(id(item) for item in window.board._plane_items)
        plane_toggle.setChecked(False)
        assert all(not item.isVisible() for item in window.board._plane_items)
        assert window.board.selected_refdes == selected_before_layer_toggle
        assert window.board.bump_count == 2
        plane_toggle.setChecked(True)
        assert all(item.isVisible() for item in window.board._plane_items)
        assert tuple(id(item) for item in window.board._plane_items) == plane_item_ids

        bump_item_ids = {
            key: id(item)
            for key, item in window.board._bump_scatters_by_net.items()
        }
        window._set_all_rails_checked(False)
        assert tuple(id(item) for item in window.board._plane_items) == plane_item_ids
        assert {
            key: id(item)
            for key, item in window.board._bump_scatters_by_net.items()
        } == bump_item_ids
        assert all(
            item.pen().color().name() == inactive_color
            for item in window.board._plane_items
            if item.data(1) is not None
        )
        assert all(
            scatter.points()[0].brush().color().name() == inactive_color
            for scatter in window.board._bump_scatters_by_net.values()
        )
        assert all(
            point.brush().color().name() == inactive_color
            for point in window.board._enabled_scatter.points()
        )

        window._set_all_rails_checked(True)
        assert tuple(id(item) for item in window.board._plane_items) == plane_item_ids
        assert any(
            item.pen().color().name() == configured_color
            for item in window.board._plane_items
            if item.data(1) is not None
        )
        assert window.board.color_for_net(rail.net).name() == configured_color
        tooltip = window.board.tooltip_text_at(QPointF(1_100.0, 2_000.0))
        assert tooltip is not None
        assert "PWR NET: VDD_CORE/0" in tooltip
        assert "Component: CAP_0402_100NF" in tooltip
        assert "REFDES: C1" in tooltip

        window.search_mode.setCurrentText("PWR NET")
        window.search_edit.setText("vdd_core")
        window.apply_search()
        assert window.board.selected_refdes == ("C1",)

        window._last_evaluation = object()
        window._last_scenario_evaluation = object()
        window._set_selected_enabled(False)
        assert tuple(id(item) for item in window.board._plane_items) == plane_item_ids
        assert window.scenario is not None
        c1 = next(item for item in window.scenario.decaps if item.refdes == "C1")
        assert c1.enabled is False
        assert window._dirty
        assert window._last_evaluation is None
        assert window._last_scenario_evaluation is None
        assert not window.ai_button.isEnabled()
        disabled_marks = {
            point.data() for point in window.board._disabled_x_scatter.points()
        }
        assert "C1" in disabled_marks
        disabled_tooltip = window.board.tooltip_text_at(QPointF(1_100.0, 2_000.0))
        assert disabled_tooltip is not None
        assert "State: Disabled" in disabled_tooltip
    finally:
        # Avoid the interactive unsaved-change close prompt in an offscreen test.
        window._dirty = False
        window.close()
        application.processEvents()


def test_reopened_scenario_keeps_source_via_recovery_disclosure(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "reopened-recovery.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    project = imported.scenario.base_project.model_copy(
        update={
            "metadata": {
                **imported.scenario.base_project.metadata,
                "spd_via_path_recovery": {
                    "requested": 3,
                    "recovered": 0,
                    "fallback": 3,
                    "algorithm": "unique_monotonic_same_net_via_chain_v1",
                },
            }
        }
    )
    scenario = ScenarioSpec.model_validate(
        {
            **imported.scenario.model_dump(mode="python"),
            "normalized_project": project,
        }
    )
    window = MainWindow()
    try:
        window._accept_scenario_bundle(
            tmp_path / "reopened-recovery.spdpi",
            ScenarioBundle(scenario=scenario, attachments=imported.attachments),
        )

        assert "Source Via paths: 0/3 recovered; 3 fallback" in window.status_text.text()
        assert "No source segment R/L applied; legacy rail templates used" in window.status_text.toolTip()
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_shared_pad_gui_exposes_dummy_island_rule_and_keeps_component_edits_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    window = MainWindow()
    opened_menus: list[QMenu] = []
    warnings: list[str] = []

    def capture_popup(menu: QMenu, *_args: object) -> None:
        opened_menus.append(menu)

    monkeypatch.setattr(QMenu, "popup", capture_popup)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(str(message)),
    )
    try:
        window._scenario = _shared_pad_scenario()
        window._attachments = {}
        window._refresh_all()
        window.board.set_selected_refdes(("D",))
        application.processEvents()

        assert "isolation-gap pad cell is required" in window.selection_summary.text()
        assert "De-cap Distribution" in window.selection_summary.text()
        assert "Ctrl+select a connected via-backed decap" in window.selection_summary.text()
        assert {point.data() for point in window.board._companion_scatter.points()} == {
            "A",
            "B",
        }
        tooltip = window.board.tooltip_text_at(QPointF(550.0, 100.0))
        assert tooltip is not None
        assert "Pad/Via: Dummy" in tooltip
        assert "CL1" in tooltip
        assert window.selection_table.item(0, 5).text().startswith("Dummy")
        assert window.selection_table.item(0, 6).text() == "V1, V2"

        window._show_decap_context_menu(("D",), QPointF(0.0, 0.0).toPoint())
        partial_menu = opened_menus.pop()
        safe_cluster_action = next(
            action
            for action in partial_menu.actions()
            if action.text().startswith("Select complete source cluster")
        )
        pwr_menu = next(
            action.menu()
            for action in partial_menu.actions()
            if action.text() == "Assign PWR NET"
        )
        assert pwr_menu is not None
        assert pwr_menu.actions()
        v1_action = next(
            action for action in pwr_menu.actions() if action.text().startswith("V1 ")
        )
        v2_blocked = next(
            action for action in pwr_menu.actions() if action.text().startswith("V2 ")
        )
        assert v1_action.isEnabled()
        assert not v2_blocked.isEnabled()
        assert "isolation-gap pad cell is required" in v2_blocked.text()
        restore_action = next(
            action
            for action in partial_menu.actions()
            if action.text().startswith("Restore selected to source state")
        )
        assert restore_action.isEnabled()

        safe_cluster_action.trigger()
        application.processEvents()
        assert window.board.selected_refdes == ("A", "B", "D")
        assert len(window.board._companion_scatter.points()) == 0

        window._show_decap_context_menu(
            window.board.selected_refdes,
            QPointF(0.0, 0.0).toPoint(),
        )
        complete_menu = opened_menus.pop()
        complete_pwr_menu = next(
            action.menu()
            for action in complete_menu.actions()
            if action.text() == "Assign PWR NET"
        )
        assert complete_pwr_menu is not None
        r2_action = next(
            action
            for action in complete_pwr_menu.actions()
            if action.text().startswith("V2 ")
        )
        assert r2_action.isEnabled()
        complete_restore = next(
            action
            for action in complete_menu.actions()
            if action.text().startswith("Restore selected to source state")
        )
        assert complete_restore.isEnabled()

        revision = window.scenario.revision
        r2_action.trigger()
        assert window.scenario.revision == revision + 1
        by_refdes = {item.refdes: item for item in window.scenario.decaps}
        assert all(by_refdes[refdes].current_rail_id == "R2" for refdes in "ABD")
        assert by_refdes["F"].current_rail_id == "R1"
        assert by_refdes["X"].current_rail_id == "R1"

        window.board.set_selected_refdes(("D",))
        window._show_decap_context_menu(("D",), QPointF(0.0, 0.0).toPoint())
        tuned_partial_menu = opened_menus.pop()
        blocked_restore = next(
            action
            for action in tuned_partial_menu.actions()
            if action.text().startswith("Restore selected to source state")
        )
        assert not blocked_restore.isEnabled()
        assert "isolation-gap pad cell is required" in blocked_restore.text()
        blocked_snapshot = window.scenario
        window._restore_selected(("D",))
        assert window.scenario is blocked_snapshot
        assert warnings and "isolation gap" in warnings[-1]

        topology = window.scenario.connection_analysis
        revision = window.scenario.revision
        window._assign_selected_model("M2", ("D",))
        assert window.scenario.revision == revision + 1
        by_refdes = {item.refdes: item for item in window.scenario.decaps}
        assert by_refdes["D"].model_id == "M2"
        assert by_refdes["A"].model_id == "M1"
        assert by_refdes["B"].model_id == "M1"
        assert window.scenario.connection_analysis == topology

        revision = window.scenario.revision
        window._set_selected_enabled(False, ("D",))
        assert window.scenario.revision == revision + 1
        by_refdes = {item.refdes: item for item in window.scenario.decaps}
        assert not by_refdes["D"].enabled
        assert by_refdes["A"].enabled and by_refdes["B"].enabled
        assert window.scenario.connection_analysis == topology

        revision = window.scenario.revision
        window._restore_selected(("A", "B", "D"))
        assert window.scenario.revision == revision + 1
        by_refdes = {item.refdes: item for item in window.scenario.decaps}
        assert all(by_refdes[refdes].current_rail_id == "R1" for refdes in "ABD")
        assert by_refdes["D"].model_id == "M1"
        assert by_refdes["D"].enabled
        assert window.scenario.connection_analysis == topology
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_shared_pad_gui_routes_partial_cross_net_edits_to_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    window = MainWindow()
    opened_menus: list[QMenu] = []
    monkeypatch.setattr(
        QMenu,
        "popup",
        lambda menu, *_args: opened_menus.append(menu),
    )
    try:
        window._scenario = _diagram_scenario()
        window._attachments = {}
        window._refresh_all()

        # A manual partial edit cannot encode the physical red-X separator.
        # It must fail closed and point users to the Distribution optimizer.
        window.board.set_selected_refdes(("A0",))
        application.processEvents()
        assert "isolation-gap pad cell is required" in (
            window.selection_summary.text()
        )
        assert "De-cap Distribution" in window.selection_summary.text()
        window._show_decap_context_menu(("A0",), QPointF(0.0, 0.0).toPoint())
        pwr_menu = next(
            action.menu()
            for action in opened_menus.pop().actions()
            if action.text() == "Assign PWR NET"
        )
        assert pwr_menu is not None
        r2_action = next(
            action for action in pwr_menu.actions() if action.text().startswith("V2 ")
        )
        assert not r2_action.isEnabled()
        assert "De-cap Distribution" in r2_action.text()

        # A whole-cluster relabel needs no separator and remains available.
        whole_cluster = ("A0", "D1", "A2", "D3", "A4")
        window.board.set_selected_refdes(whole_cluster)
        window._show_decap_context_menu(
            whole_cluster, QPointF(0.0, 0.0).toPoint()
        )
        pwr_menu = next(
            action.menu()
            for action in opened_menus.pop().actions()
            if action.text() == "Assign PWR NET"
        )
        assert pwr_menu is not None
        r2_action = next(
            action for action in pwr_menu.actions() if action.text().startswith("V2 ")
        )
        assert r2_action.isEnabled()
        r2_action.trigger()
        by_refdes = {item.refdes: item for item in window.scenario.decaps}
        assert all(
            by_refdes[refdes].current_rail_id == "R2"
            for refdes in whole_cluster
        )
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_evaluation_rail_context_menu_changes_net_color_and_preserves_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    source = tmp_path / "rail-color.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    try:
        window._accept_spd_import(imported)
        rail_item = window.rail_list.item(0)
        rail_id = str(rail_item.data(Qt.ItemDataRole.UserRole))
        rail = next(
            item
            for item in window.scenario.base_project.rails
            if item.rail_id == rail_id
        )
        original_color = window.scenario.net_colors[rail.net]
        monkeypatch.setattr(
            QColorDialog,
            "getColor",
            lambda *_args, **_kwargs: QColor(),
        )
        window._choose_net_color_for_net(rail.net)
        assert window.scenario.net_colors[rail.net] == original_color
        assert not window._dirty

        rail_item.setCheckState(Qt.CheckState.Checked)
        rail_item.setSelected(True)
        window.rail_list.setCurrentItem(rail_item)
        original_selection = window.board.selected_refdes
        opened_menus: list[QMenu] = []
        menu_actions: list[str] = []

        def capture_popup(menu: QMenu, *_args: object) -> None:
            opened_menus.append(menu)

        monkeypatch.setattr(QMenu, "popup", capture_popup)
        monkeypatch.setattr(
            QColorDialog,
            "getColor",
            lambda *_args, **_kwargs: QColor("#123456"),
        )
        position = window.rail_list.visualItemRect(rail_item).center()

        window._show_evaluation_rail_context_menu(position)
        action = opened_menus[0].actions()[0]
        menu_actions.append(action.text())
        action.trigger()

        assert window.rail_list.contextMenuPolicy() == (
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        assert menu_actions == [f"Change color for {rail.net}..."]
        assert window.scenario.net_colors[rail.net] == "#123456"
        assert window.board.color_for_net(rail.net).name() == "#123456"
        refreshed = next(
            window.rail_list.item(index)
            for index in range(window.rail_list.count())
            if window.rail_list.item(index).data(Qt.ItemDataRole.UserRole) == rail_id
        )
        assert refreshed.checkState() == Qt.CheckState.Checked
        assert refreshed.isSelected()
        assert window.rail_list.currentItem() is refreshed
        swatch = refreshed.icon().pixmap(QSize(12, 12)).toImage()
        assert swatch.pixelColor(6, 6).name() == "#123456"
        color_item = next(
            window.color_list.item(index)
            for index in range(window.color_list.count())
            if str(
                window.color_list.item(index).data(Qt.ItemDataRole.UserRole)
            ).casefold()
            == rail.net.casefold()
        )
        assert color_item.background().color().name() == "#123456"
        assert window.board.selected_refdes == original_selection
        assert window._dirty
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_new_document_resets_rail_focus_and_same_source_bump_cache(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "reload.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    try:
        window._accept_spd_import(imported)
        window._set_all_rails_checked(False)
        assert window._checked_rail_ids() == ()

        project = imported.scenario.base_project
        bump = next(item for item in project.pins if item.kind.value == "DEVICE_BUMP")
        moved_x_um = bump.x_um + 777.0
        moved_project = project.model_copy(
            update={
                "pins": [
                    item.model_copy(update={"x_um": moved_x_um})
                    if item.pin_id == bump.pin_id
                    else item
                    for item in project.pins
                ]
            }
        )
        moved_scenario = ScenarioSpec.model_validate(
            {
                **imported.scenario.model_dump(mode="python"),
                "normalized_project": moved_project,
            }
        )
        assert moved_scenario.source.sha256 == imported.scenario.source.sha256

        window._accept_scenario_bundle(
            tmp_path / "moved-bump.spdpi",
            ScenarioBundle(
                scenario=moved_scenario,
                attachments=imported.attachments,
            ),
        )

        assert window.rail_list.item(0).checkState() == Qt.CheckState.Checked
        assert window.board.tooltip_text_at(QPointF(moved_x_um, bump.y_um)) == bump.net
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_scenario_load_can_relink_only_an_identical_external_spd(tmp_path: Path) -> None:
    source = tmp_path / "original.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    scenario_path = tmp_path / "fixture.spdpi"
    save_scenario(
        imported.scenario,
        scenario_path,
        attachments=imported.attachments,
    )
    original_fingerprint = imported.scenario.design_fingerprint

    moved = tmp_path / "moved.spd"
    source.rename(moved)
    bundle = _job_load_scenario(
        scenario_path,
        moved,
        progress=lambda _value, _message: None,
        is_cancelled=lambda: False,
    )

    assert bundle.scenario.source.path == str(moved.resolve())
    assert bundle.scenario.design_fingerprint == original_fingerprint

    prepared = _job_load_scenario(
        scenario_path,
        moved,
        prepare_view=True,
        progress=lambda _value, _message: None,
        is_cancelled=lambda: False,
    )
    assert isinstance(prepared, _PreparedScenarioBundle)
    assert prepared.view.design_fingerprint == original_fingerprint
    assert prepared.view.plane_cells
    assert all(not hasattr(cell, "geometry") for cell in prepared.view.plane_cells)

    mismatched = tmp_path / "mismatched.spd"
    payload = bytearray(moved.read_bytes())
    payload[0] ^= 1
    mismatched.write_bytes(payload)
    with pytest.raises(ValueError, match="SHA-256"):
        _job_load_scenario(
            scenario_path,
            mismatched,
            progress=lambda _value, _message: None,
            is_cancelled=lambda: False,
        )


def test_stale_save_and_evaluation_results_are_not_accepted(tmp_path: Path) -> None:
    application = _application()
    source = tmp_path / "stale.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    try:
        window._accept_spd_import(imported)
        assert window.scenario is not None
        saved_fingerprint = window.scenario.design_fingerprint
        saved_revision = window.scenario.revision
        window.board.set_selected_refdes(("C1",))

        def disable(decap: ScenarioDecap) -> ScenarioDecap:
            return decap.model_copy(update={"enabled": False})

        window._update_selected(disable)
        window._scenario_saved(
            tmp_path / "stale.spdpi", saved_fingerprint, saved_revision
        )
        assert window._dirty
        assert "earlier snapshot" in window.status_text.text()

        class StaleEvaluation:
            def matches(self, _scenario: ScenarioSpec) -> bool:
                return False

        window._accept_evaluation(StaleEvaluation())
        assert window._last_evaluation is None
        assert not window.ai_button.isEnabled()
        assert "stale" in window.evaluation_summary.toPlainText().casefold()
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_evaluation_worker_receives_scenario_model_attachments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _application()
    source = tmp_path / "attachments.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    captured: dict[str, object] = {}
    try:
        window._accept_spd_import(imported)
        assert window.scenario is not None
        base = window.scenario.base_project
        second_rail = base.rails[0].model_copy(
            update={
                "rail_id": "RAIL_SECOND",
                "domain": "VDD_SECOND",
                "net": "VDD_SECOND",
            }
        )
        stackup = [
            layer.model_copy(
                update={"pwr_nets": [*layer.pwr_nets, "VDD_SECOND"]}
            )
            if layer.name == second_rail.pwr_layer
            else layer
            for layer in base.stackup_layers
        ]
        window._scenario = ScenarioSpec.model_validate(
            {
                **window.scenario.model_dump(mode="python"),
                "normalized_project": base.model_copy(
                    update={
                        "rails": [*base.rails, second_rail],
                        "stackup_layers": stackup,
                    }
                ),
            }
        )
        window._scenario_path = tmp_path / "automatic-baseline.spdpi"
        window._refresh_all()
        window._set_all_rails_checked(True)

        def capture(worker, on_result, **kwargs):
            captured["worker"] = worker
            captured["on_result"] = on_result
            captured.update(kwargs)

        monkeypatch.setattr(window, "_run_worker", capture)

        def run_through_preflight() -> None:
            captured.clear()
            window.run_evaluation()
            preflight_worker = captured["worker"]
            assert preflight_worker.function.__name__ == "_job_preflight_evaluation"
            connectivity = evaluation_module.preflight_evaluation_comparison(
                preflight_worker.args[0], preflight_worker.args[1]
            )
            captured["on_result"](connectivity)
            request, manifest = window._pending_evaluation_launch
            window._pending_evaluation_launch = None
            captured.clear()
            window._launch_evaluation_after_preflight(request, manifest)

        run_through_preflight()

        worker = captured["worker"]
        assert worker.kwargs["attachments"] == imported.attachments
        assert worker.kwargs["modal_max_index"] == 8
        assert worker.kwargs["solver_profile"] == "legacy_modal_v017"
        assert worker.function.__name__ == "evaluate_comparison_batch"
        assert worker.args[1] == (
            base.rails[0].rail_id,
            "RAIL_SECOND",
        )
        assert set(worker.args[0].baseline_captures) == {
            base.rails[0].rail_id,
            "RAIL_SECOND",
        }
        assert captured["label"] == "Evaluating 2 PWR NET(s)..."

        window.evaluation_modal_preset_combo.setCurrentIndex(
            window.evaluation_modal_preset_combo.findData(10)
        )
        run_through_preflight()
        assert captured["worker"].kwargs["modal_max_index"] == 10
        window.evaluation_modal_preset_combo.setCurrentIndex(
            window.evaluation_modal_preset_combo.findData(12)
        )
        run_through_preflight()
        assert captured["worker"].kwargs["modal_max_index"] == 12
        window.evaluation_solver_profile_combo.setCurrentIndex(
            window.evaluation_solver_profile_combo.findData(
                "research_uniform_admittance"
            )
        )
        run_through_preflight()
        assert captured["worker"].kwargs["solver_profile"] == (
            "research_uniform_admittance"
        )
        assert "RESEARCH / not PowerSI-validated" in (
            window.evaluation_summary.toPlainText()
        )
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_background_evaluation_preflight_uses_original_and_tuned_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "comparison-preflight.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    scenario = import_spd_scenario(source).scenario
    calls: list[tuple[ScenarioSpec, tuple[str, ...]]] = []
    progress_events: list[tuple[int, str]] = []
    sentinel = SimpleNamespace(rail_ids=(scenario.base_project.rails[0].rail_id,))

    def comparison_gate(candidate, rail_ids, **_kwargs):
        calls.append((candidate, tuple(rail_ids)))
        return sentinel

    monkeypatch.setattr(
        evaluation_module, "preflight_evaluation_comparison", comparison_gate
    )

    result = _job_preflight_evaluation(
        scenario,
        sentinel.rail_ids,
        progress=lambda value, message: progress_events.append((value, message)),
        is_cancelled=lambda: False,
    )

    assert result is sentinel
    assert calls == [(scenario, sentinel.rail_ids)]
    assert progress_events[0][0] == 5
    assert progress_events[-1] == (100, "Evaluation Analysis preflight complete")


def test_cancelled_worker_does_not_show_a_failure_or_leave_cancelling_status() -> None:
    application = _application()
    window = MainWindow()
    failures: list[str] = []
    try:
        worker = FunctionWorker(lambda **_kwargs: None)
        window._worker = worker
        window._worker_cancelable = True

        window._cancel_worker()
        window._worker_failed("cancel traceback", failures.append)
        window._worker_finished()

        assert failures == []
        assert window.status_text.text() == "Operation cancelled"
        assert window._worker is None
    finally:
        window.close()
        application.processEvents()


def test_cancelled_all_plane_load_unchecks_every_missing_layer() -> None:
    application = _application()
    window = MainWindow()
    try:
        first = QCheckBox("P1")
        second = QCheckBox("P2")
        first.setChecked(True)
        second.setChecked(True)
        window._plane_layer_checks = {"p1": first, "p2": second}
        window._active_plane_layer_key = "p1"
        window._load_all_plane_layers_requested = True
        window._worker = FunctionWorker(lambda **_kwargs: None)
        window._worker_cancel_requested = True

        window._worker_finished()

        assert not first.isChecked()
        assert not second.isChecked()
        assert window._hidden_plane_layer_keys == {"p1", "p2"}
        assert window._active_plane_layer_key is None
        assert window.status_text.text() == "Operation cancelled"
    finally:
        window.close()
        application.processEvents()


def test_close_invalidates_pending_plane_render_and_lazy_load(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "close-pending-plane.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    window = MainWindow()
    try:
        window._accept_spd_import(import_spd_scenario(source))
        window._plane_render_cells = (SimpleNamespace(),)  # type: ignore[assignment]
        window._load_all_plane_layers_requested = True
        window._plane_loaded_layer_keys.clear()
        key = next(iter(window._plane_layer_checks))
        window._plane_layer_checks[key].setChecked(True)
        token = window._plane_render_token
        event = QCloseEvent()

        window.closeEvent(event)
        window._load_next_missing_plane_layer()

        assert event.isAccepted()
        assert window._closing
        assert window._plane_render_token == token + 1
        assert not window._plane_render_cells
        assert not window._load_all_plane_layers_requested
        assert window._worker is None
    finally:
        window._dirty = False
        window.deleteLater()
        application.processEvents()


def test_solver_profile_change_clears_results_and_marks_research_as_opt_in() -> None:
    application = _application()
    window = MainWindow()
    try:
        window._comparison_batch = object()
        window.comparison_table.setRowCount(1)
        research_index = window.evaluation_solver_profile_combo.findData(
            "research_uniform_admittance"
        )

        window.evaluation_solver_profile_combo.setCurrentIndex(research_index)

        assert window._comparison_batch is None
        assert window.comparison_table.rowCount() == 0
        assert window.status_text.text() == (
            "Physics model changed; evaluation required"
        )
        assert "RESEARCH" in window.evaluation_solver_profile_status.text()
        assert "not PowerSI-validated" in (
            window.evaluation_solver_profile_status.text()
        )
        notes = window.findChild(QTextBrowser, "evaluationNotes")
        assert notes is not None
        assert notes.toPlainText().startswith("Selected physics model: [RESEARCH]")
        assert "topology certificate required" in notes.toPlainText()
        assert "not cached, persisted, saved, or reused" in notes.toPlainText()
    finally:
        window.close()
        application.processEvents()


def test_research_evaluation_error_is_actionable_without_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    window = MainWindow()
    shown: list[str] = []
    try:
        window.evaluation_solver_profile_combo.setCurrentIndex(
            window.evaluation_solver_profile_combo.findData(
                "research_uniform_admittance"
            )
        )
        monkeypatch.setattr(window, "_worker_error", shown.append)
        details = (
            "Traceback (most recent call last):\n"
            "ResearchProfileUnavailable: Research profile unavailable "
            "[TOPOLOGY_INCOMPLETE]: polygon connectivity is not proven. "
            "Switch to Legacy modal or repair source evidence."
        )

        window._evaluation_worker_error(details)

        assert shown == [details]
        assert window.evaluation_solver_profile_combo.currentData() == (
            "research_uniform_admittance"
        )
        assert window.status_text.text() == (
            "Research evaluation blocked by source evidence"
        )
        summary = window.evaluation_summary.toPlainText()
        assert "Legacy modal was not used as a fallback" in summary
        assert "TOPOLOGY_INCOMPLETE" in summary
        assert "repair" in summary.casefold()
    finally:
        window.close()
        application.processEvents()


def test_research_evaluation_worker_keeps_qt_heartbeat_responsive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    source = tmp_path / "research-worker.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    result_threads: list[int] = []
    accepted: list[object] = []
    heartbeats: list[None] = []
    try:
        window._accept_spd_import(imported)
        assert window.scenario is not None
        window._scenario_path = tmp_path / "research-worker.spdpi"
        window.evaluation_solver_profile_combo.setCurrentIndex(
            window.evaluation_solver_profile_combo.findData(
                "research_uniform_admittance"
            )
        )

        def fake_batch(
            *_args,
            solver_profile,
            progress,
            is_cancelled,
            **_kwargs,
        ):
            assert solver_profile == "research_uniform_admittance"
            result_threads.append(threading.get_ident())
            for step in range(6):
                if is_cancelled():
                    return object()
                progress(step * 15, f"research step {step}")
                sleep(0.02)
            progress(100, "research complete")
            return object()

        monkeypatch.setattr(evaluation_module, "evaluate_comparison_batch", fake_batch)
        monkeypatch.setattr(window, "_accept_evaluation", accepted.append)
        window.show()
        application.processEvents()
        heartbeat = QTimer(window)
        heartbeat.setInterval(5)
        heartbeat.timeout.connect(lambda: heartbeats.append(None))
        heartbeat.start()

        window.run_evaluation()
        assert window._worker is not None
        assert not window.evaluation_solver_profile_combo.isEnabled()
        assert not window.cancel_button.isHidden()

        timeout = QTimer(window)
        timeout.setSingleShot(True)
        loop = QEventLoop(window)
        timeout.timeout.connect(loop.quit)

        def finish_when_idle() -> None:
            if window._worker is None:
                loop.quit()
            else:
                QTimer.singleShot(5, finish_when_idle)

        QTimer.singleShot(5, finish_when_idle)
        timeout.start(5_000)
        loop.exec()
        heartbeat.stop()

        assert window._worker is None
        assert accepted
        assert result_threads == [result_threads[0]]
        assert result_threads[0] != threading.get_ident()
        assert len(heartbeats) >= 5
        assert window.evaluation_solver_profile_combo.isEnabled()
        assert window.cancel_button.isHidden()
        assert window.evaluation_solver_profile_combo.currentData() == (
            "research_uniform_admittance"
        )
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_research_solver_provenance_requires_source_only_and_no_powersi_fit() -> None:
    view = SimpleNamespace(
        solver_profile_key="research_uniform_admittance",
        solver_profile_label="Actual-artwork uniform mode",
        solver_profile_badge="RESEARCH",
        solver_version="research-test-1",
        solver_provenance=_research_provenance(),
    )

    presentation = _solver_provenance_for_view(view)

    assert presentation.source_only
    assert "[RESEARCH]" in presentation.banner_text
    assert "research-uniform-c00-source-only-v2" in presentation.banner_text
    assert "research-uniform-source-v1" in presentation.banner_text
    assert "evidence 111111111111…" in presentation.banner_text
    assert "modal backend research-test-1" in presentation.banner_text
    assert "not PowerSI-validated" in presentation.banner_text
    assert "PowerSI parameter fitting: never" in presentation.banner_text
    assert "Source SHA-256: " + "2" * 64 in presentation.details_text
    assert "Topology certificate SHA-256: " + "6" * 64 in (
        presentation.details_text
    )

    incomplete = SimpleNamespace(
        solver_profile_key=view.solver_profile_key,
        solver_profile_label=view.solver_profile_label,
        solver_profile_badge=view.solver_profile_badge,
        solver_version=view.solver_version,
        solver_provenance=dict(view.solver_provenance),
    )
    incomplete.solver_provenance.pop("topology_certificate_sha256")
    with pytest.raises(ValueError, match="topology_certificate_sha256"):
        _solver_provenance_for_view(incomplete)

    view.solver_provenance["powersi_used_for_parameters"] = True
    with pytest.raises(ValueError, match="comparison-only"):
        _solver_provenance_for_view(view)


def test_research_success_is_transient_and_exports_full_composite_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _application()
    source = tmp_path / "research-success.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    try:
        window._accept_spd_import(imported)
        assert window.scenario is not None
        window._scenario_path = tmp_path / "research-success.spdpi"
        window._dirty = False
        rail_id = window.scenario.base_project.rails[0].rail_id
        profile_index = window.evaluation_solver_profile_combo.findData(
            "research_uniform_admittance"
        )
        window.evaluation_solver_profile_combo.setCurrentIndex(profile_index)
        provenance = _research_provenance()
        convergence = {
            "converged": True,
            "frequency_converged": True,
            "frequency_budget_exhausted": False,
            "frequency_rms_delta_db": 0.01,
            "frequency_max_delta_db": 0.02,
            "modal_converged": True,
            "modal_rms_delta_db": 0.03,
            "modal_max_delta_db": 0.04,
        }
        view = EvaluationView(
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
            confidence="LOW",
            confidence_note="research fixture",
            confidence_bands=[],
            assumptions=[],
            solver_version=evaluation_module.SOLVER_VERSION,
            solver_diagnostics={},
            convergence=convergence,
            z_real_ohm=[0.02, 0.03],
            z_imag_ohm=[0.0, 0.0],
            solver_profile_key="research_uniform_admittance",
            solver_profile_label="Research: actual-artwork uniform mode",
            solver_profile_badge="RESEARCH",
            solver_provenance=dict(provenance),
        )
        design_fingerprint = window.scenario.design_fingerprint
        result_key = ScenarioResultKey.from_settings(
            design_fingerprint=design_fingerprint,
            rail_id=rail_id,
            settings={"solver_profile": "research_uniform_admittance"},
            solver_version=view.solver_version,
        )
        evaluation = SimpleNamespace(
            view=view,
            result_key=result_key,
            design_fingerprint=design_fingerprint,
        )
        comparison = SimpleNamespace(
            rail_id=rail_id,
            baseline=evaluation,
            tuned=evaluation,
            baseline_from_cache=False,
            configuration_unchanged=True,
        )
        scenario_before = window.scenario.model_dump(mode="json")
        attachments_before = dict(imported.attachments)
        batch = SimpleNamespace(
            comparisons=(comparison,),
            updated_scenario=window.scenario,
            updated_attachments=dict(imported.attachments),
            matches=lambda _scenario: True,
            validate_for_scenario=lambda _scenario: None,
        )
        window._active_evaluation_manifest = _EvaluationRunManifest(
            selected_rail_ids=(rail_id, "RAIL_BLOCKED"),
            runnable_rail_ids=(rail_id,),
            blocked_rail_ids=("RAIL_BLOCKED",),
            blocker_count=3,
            blocker_details="RAIL_BLOCKED [UNRESOLVED: C9, C10, C11]",
        )

        window._accept_evaluation(batch)

        assert window.comparison_table.item(0, 6).text() == (
            "Transient / not cached"
        )
        summary = window.evaluation_summary.toPlainText()
        assert "recomputed for this run; transient / not cached" in summary
        assert "reused" not in summary
        assert "newly evaluated and staged" not in summary
        assert "will be saved" not in summary
        assert "PARTIAL EVALUATION" in summary
        assert "Blocked and NOT evaluated: 1 (RAIL_BLOCKED)" in summary
        assert "RAIL_BLOCKED [UNRESOLVED: C9, C10, C11]" in summary
        assert "Evaluation complete (PARTIAL): 1 clear ran" in (
            window.status_text.text()
        )
        assert window.scenario.model_dump(mode="json") == scenario_before
        assert window._attachments == attachments_before
        assert window.scenario.evaluation_cache == {}
        assert not window._dirty
        result_window = window._results_window
        assert result_window is not None
        banner = result_window.provenance_label.text()
        assert "research-uniform-c00-source-only-v2" in banner
        assert "research-uniform-source-v1" in banner
        assert "evidence 111111111111…" in banner
        assert f"modal backend {evaluation_module.SOLVER_VERSION}" in banner
        assert "Topology certificate SHA-256: " + "6" * 64 in (
            result_window.provenance_label.toolTip()
        )
        assert "Source SHA-256: " + "2" * 64 in (
            window.comparison_table.item(0, 9).toolTip()
        )

        export_path = tmp_path / "research-tuned.csv"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            lambda *_args, **_kwargs: (str(export_path), "CSV files (*.csv)"),
        )
        window.export_tuned_csv_button.click()
        with export_path.open("r", encoding="utf-8-sig", newline="") as stream:
            exported = list(csv.DictReader(stream))
        assert len(exported) == 1
        row = exported[0]
        assert row["Compiler Algorithm ID"] == (
            "research-uniform-c00-source-only-v2"
        )
        assert row["Compiler Version"] == "research-uniform-source-v1"
        assert row["Research Identity SHA-256"] == "1" * 64
        assert row["Static Compiler Algorithm SHA-256"] == (
            solver_profile_static_identity_sha256(
                RESEARCH_UNIFORM_ADMITTANCE_PROFILE
            )
        )
        assert row["Source SHA-256"] == "2" * 64
        assert row["Geometry Manifest SHA-256"] == "3" * 64
        assert row["Component Manifest SHA-256"] == "4" * 64
        assert row["Material Manifest SHA-256"] == "5" * 64
        assert row["Topology Certificate SHA-256"] == "6" * 64
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_completed_comparison_populates_plot_ai_selector_and_auto_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _application()
    source = tmp_path / "completed.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    saved: list[Path] = []
    plot_renders: list[int] = []
    original_set_plot_results = ComparisonResultsWindow.set_plot_results

    def track_plot_render(self, comparisons, **kwargs):
        plot_renders.append(len(comparisons))
        return original_set_plot_results(self, comparisons, **kwargs)

    monkeypatch.setattr(
        ComparisonResultsWindow,
        "set_plot_results",
        track_plot_render,
    )
    try:
        window._accept_spd_import(imported)
        assert window.scenario is not None
        rail_id = window.scenario.base_project.rails[0].rail_id
        window._scenario_path = tmp_path / "completed.spdpi"

        def fake_evaluate(
            actual,
            requested_rail,
            target_ohm=None,
            modal_max_index=8,
            **_kwargs,
        ):
            view = EvaluationView(
                rail_id=requested_rail,
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
                solver_version=evaluation_module.SOLVER_VERSION,
                solver_diagnostics={},
                convergence={
                    "converged": True,
                    "frequency_converged": True,
                    "frequency_budget_exhausted": False,
                    "frequency_rms_delta_db": 0.01,
                    "frequency_max_delta_db": 0.02,
                    "modal_converged": True,
                    "modal_rms_delta_db": 0.03,
                    "modal_max_delta_db": 0.04,
                },
                z_real_ohm=[0.02, 0.03],
                z_imag_ohm=[0.0, 0.0],
            )
            key = ScenarioResultKey.from_settings(
                design_fingerprint=actual.design_fingerprint,
                rail_id=requested_rail,
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
        batch = evaluation_module.evaluate_comparison_batch(
            window.scenario,
            [rail_id],
            attachments=imported.attachments,
        )
        tuned = replace(
            batch.comparisons[0].tuned,
            view=replace(
                batch.comparisons[0].tuned.view,
                confidence="LOW",
                confidence_note="tuned fixture",
                convergence=batch.comparisons[0].tuned.view.convergence,
            ),
        )
        batch = replace(
            batch,
            comparisons=(
                replace(
                    batch.comparisons[0],
                    tuned=tuned,
                    configuration_unchanged=False,
                ),
            ),
        )

        window._accept_evaluation(batch)

        result_window = window._results_window
        assert result_window is not None
        assert plot_renders == [1]
        assert not result_window.isVisible()
        assert window.open_results_button.isEnabled()
        assert window.export_tuned_csv_button.isEnabled()
        assert len(result_window.plot.plot_widgets) == 1
        assert window.comparison_table.rowCount() == 1
        assert (
            window.comparison_table.editTriggers()
            == QTableWidget.EditTrigger.NoEditTriggers
        )
        assert window.comparison_table.item(0, 0).text() == rail_id
        assert window.comparison_table.horizontalHeaderItem(2).text() == (
            "|Z| @ 1 MHz Original→Tuned"
        )
        assert window.comparison_table.horizontalHeaderItem(3).text() == (
            "|Z| @ 10 MHz Original→Tuned"
        )
        assert window.comparison_table.horizontalHeaderItem(4).text() == (
            "|Z| @ 100 MHz Original→Tuned"
        )
        assert window.comparison_table.item(0, 2).text() == "30→30 mΩ"
        assert window.comparison_table.item(0, 3).text() == "N/A"
        assert window.comparison_table.item(0, 4).text() == "N/A"
        assert window.comparison_table.item(0, 6).text() == "Saved now"
        assert window.comparison_table.horizontalHeaderItem(7).text() == "Overall confidence Original→Tuned"
        assert window.comparison_table.horizontalHeaderItem(8).text() == (
            "Combined convergence Original→Tuned"
        )
        assert window.comparison_table.item(0, 7).text() == "MEDIUM: fixture → LOW: tuned fixture"
        assert window.comparison_table.item(0, 8).text() == (
            "Converged (frequency converged, Δmax 0.020 dB; "
            "modal converged, Δmax 0.040 dB) → Converged "
            "(frequency converged, Δmax 0.020 dB; modal converged, "
            "Δmax 0.040 dB)"
        )
        assert window.comparison_table.horizontalHeaderItem(9).text() == (
            "Solver provenance"
        )
        assert "[LEGACY] Legacy modal" in window.comparison_table.item(0, 9).text()
        assert evaluation_module.SOLVER_VERSION in (
            window.comparison_table.item(0, 9).text()
        )
        assert window.ai_rail_combo.count() == 1
        assert window.ai_rail_combo.currentData() == rail_id
        assert window._last_scenario_evaluation is batch.comparisons[0].tuned
        assert window._dirty
        assert window._auto_save_after_worker
        assert "one shared impedance view" in window.evaluation_summary.toPlainText()
        assert "absolute sub-milliohm accuracy not certified" in window.evaluation_summary.toPlainText()
        assert window.evaluation_summary.toPlainText().startswith(
            "Solver provenance: [LEGACY]"
        )
        assert not result_window.provenance_label.isHidden()
        assert "PowerSI parameter fitting: never" in (
            result_window.provenance_label.text()
        )
        notes = window.findChild(QTextBrowser, "evaluationNotes")
        assert notes is not None
        assert notes.toPlainText().startswith("Result provenance: [LEGACY]")

        evaluation_state = window.rail_list.item(0).checkState()
        plot_channel = result_window.plot.rail_checkboxes[rail_id]
        plot_channel.setChecked(False)
        assert window.rail_list.item(0).checkState() == evaluation_state
        assert result_window.plot.x_marker_checkbox is not None
        assert result_window.plot.y_marker_checkbox is not None
        result_window.plot.x_marker_checkbox.setChecked(True)
        result_window.plot.y_marker_checkbox.setChecked(True)
        result_window.plot.place_markers(1.0e6, 0.025)

        window.open_results_button.click()
        application.processEvents()
        assert result_window.isVisible()
        assert APP_DISPLAY_NAME in result_window.windowTitle()
        assert result_window.windowModality() == Qt.WindowModality.NonModal
        assert len(result_window.plot.plot_widgets) == 1
        assert result_window.table.rowCount() == 1
        assert result_window.table.item(0, 2).text() == "30→30 mΩ"
        assert (
            result_window.table.editTriggers()
            == QTableWidget.EditTrigger.NoEditTriggers
        )
        window.open_results_button.click()
        application.processEvents()
        assert window._results_window is result_window
        assert plot_renders == [1]

        export_path = tmp_path / "tuned-decaps"
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            lambda *_args, **_kwargs: (str(export_path), "CSV files (*.csv)"),
        )
        window.export_tuned_csv_button.click()
        actual_export_path = export_path.with_suffix(".csv")
        assert actual_export_path.read_bytes().startswith(b"\xef\xbb\xbf")
        with actual_export_path.open(
            "r", encoding="utf-8-sig", newline=""
        ) as stream:
            exported_rows = list(csv.DictReader(stream))
        assert exported_rows == [
            {
                "Component": "CAP_0402_100NF",
                "REFDES": "C1",
                "NET Name": "VDD_CORE/0",
                "Solver Profile": "Legacy modal",
                "Profile Badge": "LEGACY",
                "Solver Version": evaluation_module.SOLVER_VERSION,
                "Source-only Status": "No (legacy_regression)",
                "PowerSI Parameter Use": "None (comparison-only)",
                "Compiler Algorithm ID": "legacy-modal-v017-regression",
                "Compiler Version": "legacy-v0.17",
                "Artwork Evidence SHA-256": "",
                "Research Identity SHA-256": "",
                "Static Compiler Algorithm SHA-256": "",
                "Source SHA-256": "",
                "Geometry Manifest SHA-256": "",
                "Component Manifest SHA-256": "",
                "Material Manifest SHA-256": "",
                "Topology Certificate SHA-256": "",
            }
        ]
        assert window.status_text.text() == (
            "Exported 1 Tuned Decap(s) to tuned-decaps.csv "
            f"[LEGACY · {evaluation_module.SOLVER_VERSION}]"
        )

        monkeypatch.setattr(
            window,
            "save_scenario",
            lambda **_kwargs: saved.append(window._scenario_path) or True,
        )
        window._worker = FunctionWorker(lambda **_kwargs: None)
        window._worker_finished()
        application.processEvents()
        assert saved == [tmp_path / "completed.spdpi"]

        rail_net = window.scenario.base_project.rails[0].net
        first_color_item = next(
            window.color_list.item(index)
            for index in range(window.color_list.count())
            if window.color_list.item(index).data(Qt.ItemDataRole.UserRole) == rail_net
        )
        evaluation_rail_item = window.rail_list.item(0)
        evaluation_rail_item.setCheckState(Qt.CheckState.Unchecked)
        window.rail_list.setCurrentItem(evaluation_rail_item)
        monkeypatch.setattr(
            QColorDialog,
            "getColor",
            lambda *_args, **_kwargs: QColor("#FF0000"),
        )
        window._choose_net_color(first_color_item)
        assert plot_renders == [1, 1]
        plotted_pen = result_window.plot.plot_widgets[0].listDataItems()[0].opts["pen"]
        assert plotted_pen.color().name() == "#ff0000"
        refreshed_rail_item = window.rail_list.item(0)
        swatch = refreshed_rail_item.icon().pixmap(QSize(12, 12)).toImage()
        assert swatch.pixelColor(6, 6).name() == "#ff0000"
        assert refreshed_rail_item.foreground() == QBrush()
        assert refreshed_rail_item.checkState() == Qt.CheckState.Unchecked
        assert refreshed_rail_item.isSelected()
        assert window.rail_list.currentItem() is refreshed_rail_item
        assert not result_window.plot.rail_checkboxes[rail_id].isChecked()
        assert result_window.plot.marker_values == pytest.approx((1.0e6, 0.025))
        assert result_window.plot.x_marker_line is not None
        assert result_window.plot.y_marker_line is not None
        assert result_window.plot.x_marker_line.isVisible()
        assert result_window.plot.y_marker_line.isVisible()
        result_plot_pen = result_window.plot.plot_widgets[0].listDataItems()[0].opts[
            "pen"
        ]
        assert result_plot_pen.color().name() == "#ff0000"

        window.ai_output.setPlainText("analysis for the previously selected rail")
        window._ai_rail_changed()
        assert window.ai_output.toPlainText() == ""

        window.target_edit.setText("0.03")
        window.target_edit.textEdited.emit("0.03")
        assert window.comparison_table.rowCount() == 0
        assert result_window.plot.plot_widgets == ()
        assert result_window.table.rowCount() == 0
        assert result_window.provenance_label.isHidden()
        assert window._comparison_batch is None
        assert not window.open_results_button.isEnabled()
        assert not window.export_tuned_csv_button.isEnabled()
        assert not window.ai_rail_combo.isEnabled()
        assert window.status_text.text() == "Target changed; evaluation required"
        assert "Target impedance changed" in window.evaluation_summary.toPlainText()
    finally:
        if window._results_window is not None:
            window._results_window.close()
        window._dirty = False
        window.close()
        application.processEvents()


def test_nonconverged_rail_rejects_the_entire_batch_before_persistence_or_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _application()
    source = tmp_path / "rejected.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    warnings: list[str] = []
    try:
        window._accept_spd_import(imported)
        assert window.scenario is not None
        original_scenario = window.scenario
        original_attachments = dict(window._attachments)
        original_cache = dict(original_scenario.evaluation_cache)
        failed_view = SimpleNamespace(
            convergence={
                "converged": False,
                "frequency_converged": False,
                "frequency_budget_exhausted": True,
                "frequency_rms_delta_db": 0.12,
                "frequency_max_delta_db": 0.34,
                "modal_converged": True,
                "modal_rms_delta_db": 0.01,
                "modal_max_delta_db": 0.02,
            }
        )
        converged_view = SimpleNamespace(
            convergence={
                "converged": True,
                "frequency_converged": True,
                "frequency_budget_exhausted": False,
                "frequency_rms_delta_db": 0.01,
                "frequency_max_delta_db": 0.02,
                "modal_converged": True,
                "modal_rms_delta_db": 0.03,
                "modal_max_delta_db": 0.04,
            }
        )
        failed_rail = SimpleNamespace(
            rail_id="RAIL_FAILED",
            baseline=SimpleNamespace(view=failed_view),
            tuned=SimpleNamespace(view=converged_view),
        )
        converged_rail = SimpleNamespace(
            rail_id="RAIL_OK",
            baseline=SimpleNamespace(view=converged_view),
            tuned=SimpleNamespace(view=converged_view),
        )
        previous_result = SimpleNamespace(comparisons=(converged_rail,))
        window._comparison_batch = previous_result
        window._tuned_evaluations_by_rail = {"rail_ok": converged_rail.tuned}
        window._last_scenario_evaluation = converged_rail.tuned
        window._last_evaluation = converged_view
        window.comparison_table.setRowCount(1)
        window.ai_rail_combo.addItem("RAIL_OK", "RAIL_OK")
        window.ai_rail_combo.setEnabled(True)
        window.ai_button.setEnabled(True)
        window._auto_save_after_worker = True
        window._update_result_plot_button()
        assert window.open_results_button.isEnabled()
        assert window.export_tuned_csv_button.isEnabled()
        result = SimpleNamespace(
            matches=lambda _scenario: True,
            validate_for_scenario=lambda _scenario: None,
            comparisons=(failed_rail, converged_rail),
            updated_scenario=original_scenario.model_copy(
                update={"revision": original_scenario.revision + 1}
            ),
            updated_attachments={"results/should-not-be-used.json": b"new"},
        )
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            lambda *_args: warnings.append(str(_args[2])),
        )

        window._accept_evaluation(result)

        assert window.scenario is original_scenario
        assert window._attachments == original_attachments
        assert window.scenario.evaluation_cache == original_cache
        assert not window._dirty
        assert not window._auto_save_after_worker
        assert window._comparison_batch is None
        assert window.comparison_table.rowCount() == 0
        assert window.ai_rail_combo.count() == 0
        assert not window.ai_rail_combo.isEnabled()
        assert not window.open_results_button.isEnabled()
        assert not window.export_tuned_csv_button.isEnabled()
        assert not window.ai_button.isEnabled()
        assert warnings and "RAIL_FAILED / Original" in warnings[0]
        assert "frequency RMS 0.120 dB, max 0.340 dB" in warnings[0]
        assert "modal RMS 0.010 dB, max 0.020 dB" in warnings[0]
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_combined_convergence_text_and_gate_reject_frequency_only_failure() -> None:
    failed_view = SimpleNamespace(
        convergence={
            "converged": True,
            "frequency_converged": False,
            "frequency_budget_exhausted": True,
            "frequency_max_delta_db": 0.42,
            "modal_converged": True,
            "modal_max_delta_db": 0.01,
        }
    )
    comparison = SimpleNamespace(
        rail_id="VCPU0",
        baseline=SimpleNamespace(view=failed_view),
        tuned=SimpleNamespace(
            view=SimpleNamespace(
                convergence={
                    "converged": True,
                    "frequency_converged": True,
                    "modal_converged": True,
                }
            )
        ),
    )

    assert _modal_convergence_text(failed_view) == (
        "Not converged (frequency failed; budget exhausted, Δmax 0.420 dB; "
        "modal converged, Δmax 0.010 dB)"
    )
    assert _rejected_comparison_convergence((comparison,)) == (
        "VCPU0 / Original: combined convergence failed; frequency RMS N/A, "
        "max 0.420 dB; modal RMS N/A, max 0.010 dB.",
    )


def test_adaptive_modal_order_is_visible_and_ceiling_rejection_is_actionable() -> None:
    failed_view = SimpleNamespace(
        convergence={
            "converged": False,
            "frequency_converged": True,
            "frequency_max_delta_db": 0.01,
            "modal_converged": False,
            "modal_max_delta_db": 0.61,
            "start_mode_x": 8,
            "lower_mode_x": 12,
            "final_mode_x": 14,
            "ceiling_mode_x": 14,
            "modal_budget_exhausted": True,
        }
    )
    assert _modal_convergence_text(failed_view) == (
        "Not converged (frequency converged, Δmax 0.010 dB; "
        "modal failed, Δmax 0.610 dB; modal order 12→14 "
        "(start 8, ceiling 14; ceiling exhausted))"
    )
    comparison = SimpleNamespace(
        rail_id="VINT/1",
        baseline=SimpleNamespace(view=failed_view),
        tuned=SimpleNamespace(view=SimpleNamespace(convergence={
            "converged": True,
            "frequency_converged": True,
            "modal_converged": True,
        })),
    )
    message = _rejected_comparison_convergence((comparison,))[0]
    assert "Adaptive modal order 12→14 (start 8, ceiling 14) exhausted" in message


def test_restore_source_state_uses_the_frozen_fallback_baseline_model(
    tmp_path: Path,
) -> None:
    application = _application()
    source = tmp_path / "fallback-restore.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    try:
        scenario = imported.scenario
        c1 = next(item for item in scenario.decaps if item.refdes == "C1")
        fallback = c1.model_copy(update={"source_model_id": None})
        scenario = ScenarioSpec.model_validate(
            {
                **scenario.model_dump(mode="python"),
                "decaps": [
                    fallback if item.refdes == "C1" else item
                    for item in scenario.decaps
                ],
            }
        ).with_baseline_captures((c1.source_rail_id,))
        disabled = fallback.model_copy(update={"model_id": None, "enabled": False})
        scenario = ScenarioSpec.model_validate(
            {
                **scenario.model_dump(mode="python"),
                "decaps": [
                    disabled if item.refdes == "C1" else item
                    for item in scenario.decaps
                ],
            }
        )
        window._scenario = scenario
        window._attachments = imported.attachments
        window._refresh_all()
        window.board.set_selected_refdes(("C1",))

        window._restore_selected()

        assert window.scenario is not None
        restored = next(
            item for item in window.scenario.decaps if item.refdes == "C1"
        )
        assert restored.enabled
        assert restored.model_id == c1.model_id
    finally:
        window._dirty = False
        window.close()
        application.processEvents()
