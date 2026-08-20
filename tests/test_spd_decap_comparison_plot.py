from __future__ import annotations

import os
from math import log10

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QWidget
import pyqtgraph as pg

from spd_decap_pi._core.services import EvaluationView
from spd_decap_pi.evaluation import RailComparison, ScenarioEvaluation
from spd_decap_pi.gui.comparison_plot import MultiRailComparisonPlot
from spd_decap_pi.scenario import ScenarioResultKey


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _view(rail_id: str, *, scale: float = 1.0) -> EvaluationView:
    return EvaluationView(
        rail_id=rail_id,
        frequency_hz=[1.0e3, 1.0e6, 1.0e9],
        magnitude_ohm=[0.010 * scale, 0.025 * scale, 0.015 * scale],
        phase_deg=[-10.0, -20.0, -30.0],
        target_ohm=0.020,
        target_curve_ohm=[0.020, 0.020, 0.020],
        max_violation_db=1.0,
        max_violation_frequency_hz=1.0e6,
        rms_violation_db=0.5,
        peak_magnitude_ohm=0.025 * scale,
        peak_frequency_hz=1.0e6,
        peak_prominence_db=2.0,
        peaks=[],
        cap_count=3,
        model_count=1,
        confidence="HIGH",
        confidence_note="fixture",
        confidence_bands=[],
        assumptions=[],
        solver_version="modal-mvp-0.1.0",
        solver_diagnostics={},
        convergence=None,
        z_real_ohm=[0.01, 0.02, 0.01],
        z_imag_ohm=[0.00, 0.01, 0.01],
    )


def _evaluation(rail_id: str, fingerprint: str, *, scale: float) -> ScenarioEvaluation:
    view = _view(rail_id, scale=scale)
    key = ScenarioResultKey.from_settings(
        design_fingerprint=fingerprint,
        rail_id=rail_id,
        settings={"target_ohm": 0.02, "modal_max_index": 8},
        solver_version=view.solver_version,
    )
    return ScenarioEvaluation(
        state=None,
        view=view,
        result_key=key,
        scenario_revision=0,
    )


def _comparison(rail_id: str, *, unchanged: bool = False) -> RailComparison:
    baseline_fingerprint = "a" * 64
    tuned_fingerprint = baseline_fingerprint if unchanged else "b" * 64
    return RailComparison(
        rail_id=rail_id,
        baseline=_evaluation(rail_id, baseline_fingerprint, scale=1.0),
        tuned=_evaluation(rail_id, tuned_fingerprint, scale=0.8),
        baseline_from_cache=False,
    )


def _curve_names(plot: pg.PlotWidget) -> list[str]:
    return [str(curve.name()) for curve in plot.listDataItems()]


def test_all_rails_overlay_one_axes_and_are_visible_by_default() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A"), _comparison("RAIL_B")),
            rail_colors={"RAIL_A": "#12AB34", "RAIL_B": "#9A45EF"},
            rail_labels={"RAIL_A": "VDD_A", "RAIL_B": "VDD_B"},
        )

        assert widget.findChild(QScrollArea, "comparisonPlotScrollArea") is not None
        assert len(widget.plot_widgets) == 1
        (plot,) = widget.plot_widgets
        assert plot.objectName() == "sharedRailComparisonPlot"
        curves = plot.listDataItems()
        assert len(curves) == 5
        assert _curve_names(plot) == [
            "VDD_A (RAIL_A) Original",
            "VDD_A (RAIL_A) Tuned",
            "VDD_B (RAIL_B) Original",
            "VDD_B (RAIL_B) Tuned",
            "Target",
        ]
        for offset, color in ((0, "#12ab34"), (2, "#9a45ef")):
            assert curves[offset].opts["pen"].style() == Qt.PenStyle.DashLine
            assert curves[offset + 1].opts["pen"].style() == Qt.PenStyle.SolidLine
            assert all(
                curve.opts["pen"].color().name() == color
                for curve in curves[offset : offset + 2]
            )
            assert curves[offset].opts["logMode"] == [True, True]
        assert curves[-1].opts["pen"].style() == Qt.PenStyle.DotLine
        assert curves[-1].opts["pen"].color().name() == "#f5b942"
        assert plot.plotItem.getAxis("left").labelText == "|Z|"
        assert "phase" not in " ".join(_curve_names(plot)).casefold()
        assert plot.plotItem.legend is None
        style_key = widget.findChild(QLabel, "plotLineStyleKey")
        assert style_key is not None
        assert all(word in style_key.text() for word in ("Original", "Tuned", "Target"))

        assert tuple(widget.rail_checkboxes) == ("RAIL_A", "RAIL_B")
        assert all(box.isChecked() for box in widget.rail_checkboxes.values())
        assert widget.rail_checkboxes["RAIL_A"].text() == "VDD_A (RAIL_A)"
        assert widget.rail_checkboxes["RAIL_B"].text() == "VDD_B (RAIL_B)"
        # Rail color is shown by a separate square; checkbox text remains on
        # the normal application palette for legibility.
        assert all(not box.styleSheet() for box in widget.rail_checkboxes.values())
        swatches = [
            widget.findChild(QWidget, f"railColorSwatch{index}")
            for index in range(2)
        ]
        assert all(swatch is not None for swatch in swatches)
    finally:
        widget.close()
        application.processEvents()


def test_shared_plot_fills_available_vertical_space() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.resize(1200, 700)
        widget.set_comparisons(
            (_comparison("RAIL_A"), _comparison("RAIL_B")),
            rail_colors={"RAIL_A": "#12AB34", "RAIL_B": "#9A45EF"},
        )
        widget.show()
        application.processEvents()

        (plot,) = widget.plot_widgets
        assert plot.height() >= 500
    finally:
        widget.close()
        application.processEvents()


def test_rail_checkbox_hides_only_that_rails_curves() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A"), _comparison("RAIL_B")),
            rail_colors={"RAIL_A": "#12AB34", "RAIL_B": "#9A45EF"},
            show_target=True,
        )
        (plot,) = widget.plot_widgets
        curves = plot.listDataItems()

        widget.rail_checkboxes["RAIL_A"].setChecked(False)
        application.processEvents()
        assert all(not curve.isVisible() for curve in curves[:2])
        assert all(curve.isVisible() for curve in curves[2:])

        widget.set_rail_visible("rail_a", True)
        assert all(curve.isVisible() for curve in curves)

        clear_channels = widget.findChild(QPushButton, "clearPlotChannelsButton")
        select_all = widget.findChild(QPushButton, "selectAllPlotChannelsButton")
        assert clear_channels is not None and select_all is not None
        clear_channels.click()
        assert all(not box.isChecked() for box in widget.rail_checkboxes.values())
        assert all(not curve.isVisible() for curve in curves)
        select_all.click()
        assert all(box.isChecked() for box in widget.rail_checkboxes.values())
        assert all(curve.isVisible() for curve in curves)
        with pytest.raises(KeyError, match="unknown rail"):
            widget.set_rail_visible("NO_SUCH_RAIL", False)
    finally:
        widget.close()
        application.processEvents()


def test_select_all_and_clear_rebuild_marker_bubbles_exactly_once() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A"), _comparison("RAIL_B"), _comparison("RAIL_C")),
            rail_colors={
                "RAIL_A": "#12AB34",
                "RAIL_B": "#9A45EF",
                "RAIL_C": "#EF4444",
            },
        )
        assert widget.x_marker_checkbox is not None
        widget.x_marker_checkbox.setChecked(True)
        widget.place_markers(1.0e6, 0.025)
        assert len(widget.x_marker_bubbles) == 6

        (plot,) = widget.plot_widgets
        curves = plot.listDataItems()
        clear_channels = widget.findChild(QPushButton, "clearPlotChannelsButton")
        select_all = widget.findChild(QPushButton, "selectAllPlotChannelsButton")
        assert clear_channels is not None and select_all is not None

        refreshes: list[int] = []
        original_refresh = widget._refresh_marker_bubbles

        def counting_refresh() -> None:
            refreshes.append(1)
            original_refresh()

        widget._refresh_marker_bubbles = counting_refresh

        clear_channels.click()
        # One batched rebuild, not one teardown/rebuild per rail checkbox.
        assert refreshes == [1]
        assert all(not box.isChecked() for box in widget.rail_checkboxes.values())
        assert all(not curve.isVisible() for curve in curves)
        assert widget.x_marker_bubbles == ()

        refreshes.clear()
        select_all.click()
        assert refreshes == [1]
        assert all(box.isChecked() for box in widget.rail_checkboxes.values())
        assert all(curve.isVisible() for curve in curves)
        assert len(widget.x_marker_bubbles) == 6
    finally:
        widget.__dict__.pop("_refresh_marker_bubbles", None)
        widget.close()
        application.processEvents()


def test_x_y_markers_place_disable_drag_and_clear() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A"),), rail_colors={"RAIL_A": "#0088CC"}
        )
        assert widget.x_marker_checkbox is not None
        assert widget.y_marker_checkbox is not None
        assert widget.x_marker_line is not None
        assert widget.y_marker_line is not None
        assert widget.x_marker_line.pen.style() == Qt.PenStyle.DashLine
        assert widget.y_marker_line.pen.style() == Qt.PenStyle.DashLine
        assert not widget.x_marker_line.isVisible()
        assert not widget.y_marker_line.isVisible()
        assert widget.marker_readout_text == "X: off    Y: off"

        widget.x_marker_checkbox.setChecked(True)
        widget.y_marker_checkbox.setChecked(True)
        widget.resize(900, 500)
        widget.show()
        application.processEvents()
        (plot,) = widget.plot_widgets

        class ClickEvent:
            def button(self) -> Qt.MouseButton:
                return Qt.MouseButton.LeftButton

            def scenePos(self) -> QPointF:
                return plot.plotItem.vb.mapViewToScene(
                    QPointF(6.0, log10(0.025))
                )

        # Exercise the click path: log10 ViewBox coordinates must be converted
        # back to physical Hz and ohm values for the marker readout.  Placement
        # is synchronous: the click itself moves the markers.
        widget._plot_clicked(ClickEvent())
        assert widget.marker_values == pytest.approx((1.0e6, 0.025))
        assert widget.x_marker_line.isVisible()
        assert widget.y_marker_line.isVisible()
        assert widget.x_marker_line.value() == pytest.approx(6.0)
        assert widget.y_marker_line.value() == pytest.approx(log10(0.025))
        assert "1 MHz" in widget.marker_readout_text
        assert "25 mohm" in widget.marker_readout_text
        assert len(widget.x_marker_bubbles) == 2
        assert {bubble.toPlainText() for bubble in widget.x_marker_bubbles} == {
            "O 25 mohm",
            "T 20 mohm",
        }
        assert len(widget.y_marker_bubbles) == 1
        assert widget.y_marker_bubbles[0].toPlainText() == "O 1 MHz"

        widget.x_marker_line.setValue(7.0)
        application.processEvents()
        assert widget.marker_values[0] == pytest.approx(1.0e7)
        assert "10 MHz" in widget.marker_readout_text

        widget.x_marker_checkbox.setChecked(False)
        assert not widget.x_marker_line.isVisible()
        assert widget.y_marker_line.isVisible()
        assert widget.x_marker_bubbles == ()
        assert len(widget.y_marker_bubbles) == 1
        assert "X: off" in widget.marker_readout_text

        widget.clear_markers()
        assert widget.marker_values == (None, None)
        assert not widget.x_marker_line.isVisible()
        assert not widget.y_marker_line.isVisible()
        assert widget.x_marker_bubbles == ()
        assert widget.y_marker_bubbles == ()
        assert "Y: click plot" in widget.marker_readout_text
    finally:
        widget.close()
        application.processEvents()


def test_marker_click_places_immediately_and_a_second_click_repositions() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A"),), rail_colors={"RAIL_A": "#0088CC"}
        )
        assert widget.x_marker_checkbox is not None
        assert widget.y_marker_checkbox is not None
        widget.x_marker_checkbox.setChecked(True)
        widget.y_marker_checkbox.setChecked(True)
        widget.resize(900, 500)
        widget.show()
        application.processEvents()
        (plot,) = widget.plot_widgets

        class ClickEvent:
            def __init__(self, log_x: float, log_y: float) -> None:
                self._point = QPointF(log_x, log_y)

            def button(self) -> Qt.MouseButton:
                return Qt.MouseButton.LeftButton

            def scenePos(self) -> QPointF:
                return plot.plotItem.vb.mapViewToScene(self._point)

            def double(self) -> bool:
                return False

        # No event-loop turn and no double-click interval: the click that the
        # user made is the click that places the marker.
        widget._plot_clicked(ClickEvent(6.0, log10(0.025)))
        assert widget.marker_values == pytest.approx((1.0e6, 0.025))

        # A second click repositions the marker instead of cancelling it.
        widget._plot_clicked(ClickEvent(7.0, log10(0.015)))
        assert widget.marker_values == pytest.approx((1.0e7, 0.015))
        assert widget.x_marker_line is not None
        assert widget.x_marker_line.value() == pytest.approx(7.0)
    finally:
        widget.close()
        application.processEvents()


def test_double_click_emits_signal_without_cancelling_marker_placement() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A"),), rail_colors={"RAIL_A": "#0088CC"}
        )
        assert widget.x_marker_checkbox is not None
        assert widget.y_marker_checkbox is not None
        widget.x_marker_checkbox.setChecked(True)
        widget.y_marker_checkbox.setChecked(True)
        widget.place_markers(1.0e6, 0.025)
        widget.resize(900, 500)
        widget.show()
        application.processEvents()
        (plot,) = widget.plot_widgets
        emissions: list[bool] = []
        widget.plotDoubleClicked.connect(lambda: emissions.append(True))

        class ClickEvent:
            def __init__(self, *, double: bool) -> None:
                self._double = double
                self.accepted = False

            def button(self) -> Qt.MouseButton:
                return Qt.MouseButton.LeftButton

            def scenePos(self) -> QPointF:
                return plot.plotItem.vb.mapViewToScene(QPointF(7.0, -1.8))

            def double(self) -> bool:
                return self._double

            def accept(self) -> None:
                self.accepted = True

        clicked_values = (1.0e7, 10.0**-1.8)
        # The click places markers immediately, without waiting out the
        # platform double-click interval.
        first_click = ClickEvent(double=False)
        widget._plot_clicked(first_click)
        assert widget.marker_values == pytest.approx(clicked_values)
        placed_bubble_texts = tuple(
            bubble.toPlainText()
            for bubble in (*widget.x_marker_bubbles, *widget.y_marker_bubbles)
        )

        # A registered double click still reports the gesture, and it must not
        # cancel or move the placement the same point already produced.
        double_click = ClickEvent(double=True)
        widget._plot_clicked(double_click)
        assert emissions == [True]
        assert double_click.accepted
        assert widget.marker_values == pytest.approx(clicked_values)
        assert tuple(
            bubble.toPlainText()
            for bubble in (*widget.x_marker_bubbles, *widget.y_marker_bubbles)
        ) == placed_bubble_texts
        assert not hasattr(widget, "_pending_marker_click")
    finally:
        widget.close()
        application.processEvents()


def test_real_viewport_double_click_emits_once_and_keeps_the_marker_placed() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A"),), rail_colors={"RAIL_A": "#0088CC"}
        )
        assert widget.x_marker_checkbox is not None
        assert widget.y_marker_checkbox is not None
        widget.x_marker_checkbox.setChecked(True)
        widget.y_marker_checkbox.setChecked(True)
        widget.place_markers(1.0e6, 0.025)
        widget.resize(900, 500)
        widget.show()
        application.processEvents()
        (plot,) = widget.plot_widgets
        emissions: list[bool] = []
        widget.plotDoubleClicked.connect(lambda: emissions.append(True))
        scene_position = plot.plotItem.vb.mapViewToScene(QPointF(7.0, -1.8))
        viewport_position = plot.mapFromScene(scene_position)

        global_position = plot.viewport().mapToGlobal(viewport_position)
        for event_type in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseButtonRelease,
        ):
            pressed_buttons = (
                Qt.MouseButton.NoButton
                if event_type == QEvent.Type.MouseButtonRelease
                else Qt.MouseButton.LeftButton
            )
            event = QMouseEvent(
                event_type,
                QPointF(viewport_position),
                QPointF(viewport_position),
                QPointF(global_position),
                Qt.MouseButton.LeftButton,
                pressed_buttons,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(plot.viewport(), event)
            application.processEvents()
        QTest.qWait(QApplication.doubleClickInterval() + 20)

        assert emissions == [True]
        # The press placed the markers at the clicked point (within viewport
        # pixel rounding) and the double click neither cancelled nor moved
        # them; nothing is left pending after the double-click interval.
        frequency_hz, impedance_ohm = widget.marker_values
        assert frequency_hz == pytest.approx(1.0e7, rel=0.05)
        assert impedance_ohm == pytest.approx(10.0**-1.8, rel=0.05)
    finally:
        widget.close()
        application.processEvents()


def test_marker_bubbles_use_log_interpolation_and_follow_curve_visibility() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A"), _comparison("RAIL_B")),
            rail_colors={"RAIL_A": "#12AB34", "RAIL_B": "#9A45EF"},
        )
        assert widget.x_marker_checkbox is not None
        assert widget.y_marker_checkbox is not None
        widget.x_marker_checkbox.setChecked(True)
        geometric_frequency = (1.0e3 * 1.0e6) ** 0.5
        widget.place_markers(geometric_frequency, 0.020)

        assert len(widget.x_marker_bubbles) == 4
        first_original = next(
            bubble
            for bubble in widget.x_marker_bubbles
            if bubble.toolTip() == "RAIL_A Original"
        )
        expected_impedance = (0.010 * 0.025) ** 0.5
        assert first_original.pos().x() == pytest.approx(log10(geometric_frequency))
        assert first_original.pos().y() == pytest.approx(log10(expected_impedance))

        widget.y_marker_checkbox.setChecked(True)
        widget.place_markers(geometric_frequency, 0.020)
        assert len(widget.y_marker_bubbles) >= 3
        assert all("Hz" in bubble.toPlainText() for bubble in widget.y_marker_bubbles)

        widget.set_rail_visible("RAIL_A", False)
        assert len(widget.x_marker_bubbles) == 2
        assert all(
            "RAIL_B" in bubble.toolTip() for bubble in widget.x_marker_bubbles
        )
        assert all(
            "RAIL_A" not in bubble.toolTip() for bubble in widget.y_marker_bubbles
        )

        assert widget.x_marker_line is not None
        assert widget.y_marker_line is not None
        widget.x_marker_line.setValue(log10(100.0))
        widget.y_marker_line.setValue(log10(0.100))
        assert widget.x_marker_bubbles == ()
        assert widget.y_marker_bubbles == ()
    finally:
        widget.close()
        application.processEvents()


def test_unchanged_configuration_deduplicates_curve_and_adds_annotation() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A", unchanged=True),),
            rail_colors={"RAIL_A": "#0088CC"},
            show_target=False,
        )

        (plot,) = widget.plot_widgets
        assert len(plot.listDataItems()) == 1
        assert _curve_names(plot) == ["RAIL_A Original = Tuned"]
        assert plot.listDataItems()[0].opts["pen"].style() == Qt.PenStyle.SolidLine
        assert widget.annotation_texts == ("RAIL_A: Original = Tuned",)
        assert any(isinstance(item, pg.TextItem) for item in plot.plotItem.items)

        widget.rail_checkboxes["RAIL_A"].setChecked(False)
        assert not plot.listDataItems()[0].isVisible()
        annotation = next(
            item for item in plot.plotItem.items if isinstance(item, pg.TextItem)
        )
        assert not annotation.isVisible()
    finally:
        widget.close()
        application.processEvents()


def test_distinct_targets_are_qualified_and_follow_their_rail_visibility() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    first = _comparison("RAIL_A")
    second = _comparison("RAIL_B")
    second.tuned.view.target_curve_ohm = [0.030, 0.030, 0.030]
    try:
        widget.set_comparisons(
            (first, second),
            rail_colors={"RAIL_A": "#12AB34", "RAIL_B": "#9A45EF"},
        )
        (plot,) = widget.plot_widgets
        by_name = {str(curve.name()): curve for curve in plot.listDataItems()}
        assert "RAIL_A Target" in by_name
        assert "RAIL_B Target" in by_name
        assert by_name["RAIL_A Target"].opts["pen"].style() == Qt.PenStyle.DotLine
        assert by_name["RAIL_B Target"].opts["pen"].style() == Qt.PenStyle.DotLine

        widget.set_rail_visible("RAIL_A", False)
        assert not by_name["RAIL_A Original"].isVisible()
        assert not by_name["RAIL_A Tuned"].isVisible()
        assert not by_name["RAIL_A Target"].isVisible()
        assert by_name["RAIL_B Original"].isVisible()
        assert by_name["RAIL_B Target"].isVisible()
    finally:
        widget.close()
        application.processEvents()


def test_shared_constant_target_spans_union_of_adaptive_rail_grids() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    first = _comparison("RAIL_A")
    second = _comparison("RAIL_B")
    second.tuned.view.frequency_hz = [1.0e2, 1.0e6, 1.0e10]
    try:
        widget.set_comparisons(
            (first, second),
            rail_colors={"RAIL_A": "#12AB34", "RAIL_B": "#9A45EF"},
        )
        (plot,) = widget.plot_widgets
        target = next(curve for curve in plot.listDataItems() if curve.name() == "Target")
        original_dataset = target.getOriginalDataset()
        assert original_dataset is not None
        x_values, y_values = original_dataset
        assert list(x_values) == pytest.approx([1.0e2, 1.0e10])
        assert list(y_values) == pytest.approx([0.020, 0.020])
    finally:
        widget.close()
        application.processEvents()


def test_color_only_rerender_preserves_plot_visibility_zoom_and_markers() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    comparisons = (_comparison("RAIL_A"), _comparison("RAIL_B"))
    try:
        widget.set_comparisons(
            comparisons,
            rail_colors={"RAIL_A": "#12AB34", "RAIL_B": "#9A45EF"},
        )
        (plot,) = widget.plot_widgets
        widget.set_rail_visible("RAIL_A", False)
        assert widget.x_marker_checkbox is not None
        assert widget.y_marker_checkbox is not None
        widget.x_marker_checkbox.setChecked(True)
        widget.y_marker_checkbox.setChecked(True)
        widget.place_markers(2.0e6, 0.015)
        plot.setXRange(4.0, 8.0, padding=0)
        plot.setYRange(-3.0, -1.0, padding=0)

        widget.set_comparisons(
            comparisons,
            rail_colors={"RAIL_A": "#FF0000", "RAIL_B": "#00FFFF"},
        )
        assert widget.plot_widgets == (plot,)
        assert not widget.rail_checkboxes["RAIL_A"].isChecked()
        assert widget.rail_checkboxes["RAIL_B"].isChecked()
        assert widget.marker_values == pytest.approx((2.0e6, 0.015))
        assert widget.x_marker_line is not None and widget.x_marker_line.isVisible()
        assert widget.y_marker_line is not None and widget.y_marker_line.isVisible()
        assert plot.plotItem.vb.viewRange()[0] == pytest.approx([4.0, 8.0])
        assert plot.plotItem.vb.viewRange()[1] == pytest.approx([-3.0, -1.0])
        assert plot.listDataItems()[0].opts["pen"].color().name() == "#ff0000"
    finally:
        widget.close()
        application.processEvents()


def test_render_api_is_transactional_and_handles_invalid_colors_and_empty_state() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    comparison = _comparison("RAIL_A")
    try:
        widget.set_comparisons(
            (comparison,), rail_colors={"RAIL_A": "not-a-color"}
        )
        old_plot = widget.plot_widgets[0]
        assert old_plot.listDataItems()[0].opts["pen"].color().name() == "#4da3ff"

        with pytest.raises(ValueError, match="duplicate rail comparison"):
            widget.set_comparisons(
                (comparison, comparison), rail_colors={"RAIL_A": "#FFFFFF"}
            )
        assert widget.plot_widgets == (old_plot,)

        widget.set_comparisons((), rail_colors={})
        application.processEvents()
        assert widget.plot_widgets == ()
        assert widget.rail_checkboxes == {}
        content = widget.findChild(QWidget, "comparisonPlotContent")
        assert content is not None
        assert content.minimumHeight() == 230
        with pytest.raises(RuntimeError, match="without a comparison plot"):
            widget.place_markers(1.0e6, 0.02)
    finally:
        widget.close()
        application.processEvents()
