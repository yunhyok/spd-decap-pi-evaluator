from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsPolygonItem,
    QLabel,
)

from test_io_spd import MINI_SPD
from spd_decap_pi.gui.worker import FunctionWorker
from spd_decap_pi.gui.main_window import MainWindow, _job_load_scenario
from spd_decap_pi.scenario import ScenarioDecap, ScenarioSpec
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

        def capture(worker, _on_result, **kwargs):
            captured["worker"] = worker
            captured.update(kwargs)

        monkeypatch.setattr(window, "_run_worker", capture)
        window.run_evaluation()

        worker = captured["worker"]
        assert worker.kwargs["attachments"] == imported.attachments
        assert captured["label"] == "Evaluating scenario..."
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
