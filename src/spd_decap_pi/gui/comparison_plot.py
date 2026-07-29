"""Scrollable small-multiple plots for Original/Tuned rail comparisons."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import log10

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
import pyqtgraph as pg

from ..evaluation import RailComparison


_BACKGROUND_COLOR = "#171A1F"
_DEFAULT_RAIL_COLOR = "#4DA3FF"
_TARGET_COLOR = "#F5B942"
_PLOT_MINIMUM_HEIGHT = 230


class MultiRailComparisonPlot(QWidget):
    """Render one impedance-only Original/Tuned plot for every selected rail.

    ``rail_colors`` is keyed by rail id.  ``rail_labels`` may supply a more
    user-facing PWR NET label while the stable rail id remains visible in the
    title.  The widget owns every generated :class:`~pyqtgraph.PlotWidget` and
    can be reused by calling :meth:`set_comparisons` again.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("multiRailComparisonPlot")
        self._plots: list[pg.PlotWidget] = []
        self._annotations: list[pg.TextItem] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("comparisonPlotScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout.addWidget(self._scroll)

        self._content = QWidget()
        self._content.setObjectName("comparisonPlotContent")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        self._content_layout.addStretch(1)
        self._scroll.setWidget(self._content)

    @property
    def plot_widgets(self) -> tuple[pg.PlotWidget, ...]:
        """Return the currently rendered rail plots in display order."""

        return tuple(self._plots)

    @property
    def annotation_texts(self) -> tuple[str, ...]:
        """Return user-visible in-plot annotations for focused GUI tests."""

        return tuple(item.toPlainText() for item in self._annotations)

    def clear_comparisons(self) -> None:
        """Remove every generated plot while keeping the scroll widget reusable."""

        self._annotations.clear()
        self._plots.clear()
        self._content.setMinimumHeight(_PLOT_MINIMUM_HEIGHT)
        while self._content_layout.count() > 1:
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def set_comparisons(
        self,
        comparisons: Sequence[RailComparison],
        *,
        rail_colors: Mapping[str, str],
        rail_labels: Mapping[str, str] | None = None,
        show_target: bool = True,
    ) -> None:
        """Replace the display with deterministic rail-ordered comparisons.

        Original and Tuned use the same supplied rail color so color retains
        its board-level PWR NET meaning.  Line style identifies configuration:
        Original is dashed, Tuned is solid, and Target is dotted.  When the two
        results have the same electrical fingerprint, a single curve and an
        explicit unchanged-configuration annotation replace duplicate curves.
        """

        rendered = tuple(comparisons)
        rail_keys = [item.rail_id.strip().casefold() for item in rendered]
        if any(not key for key in rail_keys):
            raise ValueError("rail comparison ids must be nonblank")
        if len(rail_keys) != len(set(rail_keys)):
            duplicate = next(
                item.rail_id
                for item in rendered
                if rail_keys.count(item.rail_id.strip().casefold()) > 1
            )
            raise ValueError(f"duplicate rail comparison {duplicate!r}")

        self.clear_comparisons()
        colors = {str(key).casefold(): value for key, value in rail_colors.items()}
        labels = {
            str(key).casefold(): str(value)
            for key, value in (rail_labels or {}).items()
        }
        first_plot: pg.PlotWidget | None = None

        for index, comparison in enumerate(rendered):
            key = comparison.rail_id.casefold()
            color = _valid_color(colors.get(key, _DEFAULT_RAIL_COLOR))
            label = labels.get(key, comparison.rail_id)
            title = (
                label
                if label.casefold() == key
                else f"{label} ({comparison.rail_id})"
            )
            unchanged = (
                comparison.configuration_unchanged
                or comparison.baseline.design_fingerprint
                == comparison.tuned.design_fingerprint
            )
            if unchanged:
                title += " - Unchanged configuration"

            plot = self._new_plot(title, index)
            if first_plot is None:
                first_plot = plot
            else:
                plot.setXLink(first_plot)
            # Parent the PlotWidget before adding data items.  Some pyqtgraph
            # versions otherwise resolve PlotDataItem.getViewBox() to the
            # wrapper PlotWidget while applying view-dependent options.
            self._content_layout.insertWidget(
                self._content_layout.count() - 1, plot
            )
            self._plots.append(plot)

            if unchanged:
                plot.plot(
                    comparison.tuned.view.frequency_hz,
                    comparison.tuned.view.magnitude_ohm,
                    pen=pg.mkPen(color, width=2.4, style=Qt.PenStyle.SolidLine),
                    name="Original = Tuned",
                    **_curve_options(),
                )
                annotation = pg.TextItem(
                    "Original = Tuned (unchanged configuration)",
                    color="#D9E2EC",
                    anchor=(0.0, 1.0),
                )
                annotation.setPos(*_annotation_position(comparison))
                plot.addItem(annotation)
                self._annotations.append(annotation)
            else:
                plot.plot(
                    comparison.baseline.view.frequency_hz,
                    comparison.baseline.view.magnitude_ohm,
                    pen=pg.mkPen(color, width=1.8, style=Qt.PenStyle.DashLine),
                    name="Original",
                    **_curve_options(),
                )
                plot.plot(
                    comparison.tuned.view.frequency_hz,
                    comparison.tuned.view.magnitude_ohm,
                    pen=pg.mkPen(color, width=2.4, style=Qt.PenStyle.SolidLine),
                    name="Tuned",
                    **_curve_options(),
                )

            target = comparison.tuned.view.target_curve_ohm
            if show_target and target:
                plot.plot(
                    comparison.tuned.view.frequency_hz,
                    target,
                    pen=pg.mkPen(
                        _TARGET_COLOR,
                        width=1.4,
                        style=Qt.PenStyle.DotLine,
                    ),
                    name="Target",
                    **_curve_options(),
                )

        minimum_content_height = max(
            _PLOT_MINIMUM_HEIGHT,
            len(self._plots) * (_PLOT_MINIMUM_HEIGHT + self._content_layout.spacing()),
        )
        self._content.setMinimumHeight(minimum_content_height)

    def _new_plot(self, title: str, index: int) -> pg.PlotWidget:
        plot = pg.PlotWidget(background=_BACKGROUND_COLOR)
        plot.setObjectName(f"railComparisonPlot{index}")
        plot.setMinimumHeight(_PLOT_MINIMUM_HEIGHT)
        plot.setLogMode(x=True, y=True)
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.setLabel("bottom", "Frequency", units="Hz")
        plot.setLabel("left", "|Z|", units="ohm")
        plot.setTitle(title, color="#E6EDF3", size="11pt")
        plot.addLegend(offset=(10, 10))
        return plot


def _valid_color(value: str) -> str:
    color = QColor(str(value))
    # Return a string rather than a QColor.  pyqtgraph may have selected a
    # different installed Qt binding before this module imports PySide6, and
    # QColor instances are not interchangeable across those bindings.
    return color.name() if color.isValid() else _DEFAULT_RAIL_COLOR


def _curve_options() -> dict[str, bool]:
    return {"antialias": True}


def _annotation_position(comparison: RailComparison) -> tuple[float, float]:
    frequencies = [
        float(value)
        for value in comparison.tuned.view.frequency_hz
        if float(value) > 0.0
    ]
    magnitudes = [
        float(value)
        for value in comparison.tuned.view.magnitude_ohm
        if float(value) > 0.0
    ]
    x = log10(frequencies[0]) if frequencies else 0.0
    y = log10(max(magnitudes)) if magnitudes else 0.0
    return x, y


__all__ = ["MultiRailComparisonPlot"]
