from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget
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


def _legend_names(plot: pg.PlotWidget) -> list[str]:
    legend = plot.plotItem.legend
    assert legend is not None
    return [label.text for _sample, label in legend.items]


def test_renders_scrollable_impedance_small_multiples_with_stable_styles() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_comparison("RAIL_A"), _comparison("RAIL_B")),
            rail_colors={"RAIL_A": "#12AB34", "RAIL_B": "#9A45EF"},
            rail_labels={"RAIL_A": "VDD_A", "RAIL_B": "VDD_B"},
        )

        assert widget.findChild(QScrollArea, "comparisonPlotScrollArea") is not None
        assert len(widget.plot_widgets) == 2
        for plot, color, label in zip(
            widget.plot_widgets,
            ("#12ab34", "#9a45ef"),
            ("VDD_A", "VDD_B"),
        ):
            curves = plot.listDataItems()
            assert len(curves) == 3
            assert _legend_names(plot) == ["Original", "Tuned", "Target"]
            assert curves[0].opts["pen"].style() == Qt.PenStyle.DashLine
            assert curves[1].opts["pen"].style() == Qt.PenStyle.SolidLine
            assert curves[2].opts["pen"].style() == Qt.PenStyle.DotLine
            assert curves[0].opts["pen"].color().name() == color
            assert curves[1].opts["pen"].color().name() == color
            assert curves[0].opts["logMode"] == [True, True]
            assert curves[1].opts["logMode"] == [True, True]
            assert label in plot.plotItem.titleLabel.text
            assert plot.plotItem.getAxis("left").labelText == "|Z|"
            assert "phase" not in " ".join(_legend_names(plot)).casefold()
            assert "phase" not in plot.plotItem.getAxis("left").labelText.casefold()
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
        assert _legend_names(plot) == ["Original = Tuned"]
        assert plot.listDataItems()[0].opts["pen"].style() == Qt.PenStyle.SolidLine
        assert "Unchanged configuration" in plot.plotItem.titleLabel.text
        assert widget.annotation_texts == (
            "Original = Tuned (unchanged configuration)",
        )
        assert any(isinstance(item, pg.TextItem) for item in plot.plotItem.items)
    finally:
        widget.close()
        application.processEvents()


def test_render_api_replaces_old_plots_and_rejects_duplicate_rails() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    comparison = _comparison("RAIL_A")
    try:
        widget.set_comparisons(
            (comparison,), rail_colors={"RAIL_A": "not-a-color"}
        )
        old_plot = widget.plot_widgets[0]
        assert old_plot.listDataItems()[0].opts["pen"].color().name() == "#4da3ff"

        try:
            widget.set_comparisons(
                (comparison, comparison), rail_colors={"RAIL_A": "#FFFFFF"}
            )
        except ValueError as exc:
            assert "duplicate rail comparison" in str(exc)
        else:  # pragma: no cover - explicit failure message is clearer than pytest here
            raise AssertionError("duplicate rail comparisons must be rejected")
        assert widget.plot_widgets == (old_plot,)

        widget.set_comparisons((), rail_colors={})
        application.processEvents()
        assert widget.plot_widgets == ()
        content = widget.findChild(QWidget, "comparisonPlotContent")
        assert content is not None
        assert content.minimumHeight() == 230
    finally:
        widget.close()
        application.processEvents()
