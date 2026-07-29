from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QGraphicsEllipseItem,
    QGraphicsPolygonItem,
    QLabel,
    QTableWidget,
)

from test_io_spd import MINI_SPD
from spd_decap_pi import evaluation as evaluation_module
from spd_decap_pi._core.services import EvaluationView
from spd_decap_pi.gui.worker import FunctionWorker
from spd_decap_pi.gui.main_window import MainWindow, _job_load_scenario
from spd_decap_pi.scenario import ScenarioDecap, ScenarioResultKey, ScenarioSpec
from spd_decap_pi.scenario_io import save_scenario
from spd_decap_pi.spd_adapter import import_spd_scenario
from spd_decap_pi.version import APP_DISPLAY_NAME


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


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
        assert "Optimization" not in labels
        assert not window.evaluate_button.isEnabled()
    finally:
        window.close()
        application.processEvents()


def test_evaluation_layout_preserves_a_usable_plot_height() -> None:
    application = _application()
    window = MainWindow()
    try:
        window.show()
        application.processEvents()
        assert window.size().width() >= 1500
        assert window.plot.height() >= 260
    finally:
        window.close()
        application.processEvents()


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
        primitive_kinds = {item.data(0) for item in window.board._plane_items}
        assert {
            "positive_polygon",
            "negative_polygon",
            "negative_circle",
            "solver_bounds",
        }.issubset(primitive_kinds)
        assert any(
            isinstance(item, QGraphicsPolygonItem)
            for item in window.board._plane_items
        )
        assert any(
            isinstance(item, QGraphicsEllipseItem)
            for item in window.board._plane_items
        )
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

        def capture(worker, _on_result, **kwargs):
            captured["worker"] = worker
            captured.update(kwargs)

        monkeypatch.setattr(window, "_run_worker", capture)
        window.run_evaluation()

        worker = captured["worker"]
        assert worker.kwargs["attachments"] == imported.attachments
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
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


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


def test_completed_comparison_populates_plot_ai_selector_and_auto_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _application()
    source = tmp_path / "completed.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    imported = import_spd_scenario(source)
    window = MainWindow()
    saved: list[Path] = []
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
                convergence=None,
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

        window._accept_evaluation(batch)

        assert len(window.plot.plot_widgets) == 1
        assert window.comparison_table.rowCount() == 1
        assert (
            window.comparison_table.editTriggers()
            == QTableWidget.EditTrigger.NoEditTriggers
        )
        assert window.comparison_table.item(0, 0).text() == rail_id
        assert window.comparison_table.item(0, 6).text() == "Saved now"
        assert window.ai_rail_combo.count() == 1
        assert window.ai_rail_combo.currentData() == rail_id
        assert window._last_scenario_evaluation is batch.comparisons[0].tuned
        assert window._dirty
        assert window._auto_save_after_worker
        assert "impedance only" in window.evaluation_summary.toPlainText()

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
        monkeypatch.setattr(
            QColorDialog,
            "getColor",
            lambda *_args, **_kwargs: QColor("#FF0000"),
        )
        window._choose_net_color(first_color_item)
        plotted_pen = window.plot.plot_widgets[0].listDataItems()[0].opts["pen"]
        assert plotted_pen.color().name() == "#ff0000"

        window.ai_output.setPlainText("analysis for the previously selected rail")
        window._ai_rail_changed()
        assert window.ai_output.toPlainText() == ""

        window.target_edit.setText("0.03")
        window.target_edit.textEdited.emit("0.03")
        assert window.plot.plot_widgets == ()
        assert window.comparison_table.rowCount() == 0
        assert window._comparison_batch is None
        assert not window.ai_rail_combo.isEnabled()
        assert window.status_text.text() == "Target changed; evaluation required"
        assert "Target impedance changed" in window.evaluation_summary.toPlainText()
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


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
