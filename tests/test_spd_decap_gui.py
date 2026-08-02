from __future__ import annotations

import csv
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsRectItem,
    QLabel,
    QMenu,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTextBrowser,
)

from test_io_spd import MINI_SPD
from test_spd_decap_scenario_edits import (
    _diagram_scenario,
    _scenario as _shared_pad_scenario,
)
from spd_decap_pi import evaluation as evaluation_module
from spd_decap_pi._core.domain import StackupLayer
from spd_decap_pi._core.services import EvaluationView
from spd_decap_pi.gui.worker import FunctionWorker
from spd_decap_pi.gui.main_window import (
    MainWindow,
    _PlaneArtworkItem,
    _excel_safe_csv_cell,
    _job_load_scenario,
    _source_via_path_recovery_summary,
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
        assert window.evaluation_modal_preset_combo.currentData() == 8
        assert window.evaluation_modal_preset_combo.currentText() == "Balanced (81 modes)"
        notes = window.findChild(QTextBrowser, "evaluationNotes")
        assert notes is not None
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
            "evaluationSectionSplitter": (230, 180),
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
        assert "SPD analysis:" in window.status_text.toolTip()
        assert "Spatial index build:" in window.status_text.toolTip()
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
        assert worker.kwargs["modal_max_index"] == 8
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
        window.run_evaluation()
        assert captured["worker"].kwargs["modal_max_index"] == 10
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
        tuned = replace(
            batch.comparisons[0].tuned,
            view=replace(
                batch.comparisons[0].tuned.view,
                confidence="LOW",
                confidence_note="tuned fixture",
                convergence={"modal_converged": False, "modal_max_delta_db": 1.25},
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
        assert window.comparison_table.horizontalHeaderItem(8).text() == "Modal convergence Original→Tuned"
        assert window.comparison_table.item(0, 7).text() == "MEDIUM: fixture → LOW: tuned fixture"
        assert window.comparison_table.item(0, 8).text() == "Not reported → Not converged (Δmax 1.250 dB)"
        assert window.ai_rail_combo.count() == 1
        assert window.ai_rail_combo.currentData() == rail_id
        assert window._last_scenario_evaluation is batch.comparisons[0].tuned
        assert window._dirty
        assert window._auto_save_after_worker
        assert "one shared impedance view" in window.evaluation_summary.toPlainText()
        assert "absolute sub-milliohm accuracy not certified" in window.evaluation_summary.toPlainText()

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
            }
        ]
        assert window.status_text.text() == (
            "Exported 1 Tuned Decap(s) to tuned-decaps.csv"
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
