"""Large, reusable presentation window for PI comparison results."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from math import isfinite, log10
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ..version import APP_DISPLAY_NAME
from .comparison_plot import MultiRailComparisonPlot


_IMPEDANCE_UNITS = (
    (1.0e3, "kΩ"),
    (1.0, "Ω"),
    (1.0e-3, "mΩ"),
    (1.0e-6, "µΩ"),
    (1.0e-9, "nΩ"),
    (1.0e-12, "pΩ"),
)


def log_log_interpolate_impedance(
    frequency_hz: Sequence[float],
    magnitude_ohm: Sequence[float],
    query_frequency_hz: float,
) -> float | None:
    """Interpolate impedance in log-frequency/log-magnitude space.

    No extrapolation is performed. Invalid, non-positive, or duplicate source
    points return ``None`` so a presentation issue cannot fabricate a result.
    """

    try:
        query = float(query_frequency_hz)
    except (TypeError, ValueError):
        return None
    if not isfinite(query) or query <= 0.0 or len(frequency_hz) != len(magnitude_ohm):
        return None

    points: list[tuple[float, float]] = []
    try:
        for raw_frequency, raw_magnitude in zip(frequency_hz, magnitude_ohm):
            frequency = float(raw_frequency)
            magnitude = float(raw_magnitude)
            if (
                not isfinite(frequency)
                or not isfinite(magnitude)
                or frequency <= 0.0
                or magnitude <= 0.0
            ):
                return None
            points.append((frequency, magnitude))
    except (TypeError, ValueError):
        return None
    if not points:
        return None

    points.sort(key=lambda item: item[0])
    frequencies = [item[0] for item in points]
    if any(left == right for left, right in zip(frequencies, frequencies[1:])):
        return None
    if query < frequencies[0] or query > frequencies[-1]:
        return None

    upper = bisect_left(frequencies, query)
    if upper < len(points) and frequencies[upper] == query:
        return points[upper][1]
    if upper == 0 or upper == len(points):
        return None

    lower_frequency, lower_magnitude = points[upper - 1]
    upper_frequency, upper_magnitude = points[upper]
    fraction = (
        (log10(query) - log10(lower_frequency))
        / (log10(upper_frequency) - log10(lower_frequency))
    )
    return 10.0 ** (
        log10(lower_magnitude)
        + fraction * (log10(upper_magnitude) - log10(lower_magnitude))
    )


def impedance_transition_at_frequency(
    original_view: Any,
    tuned_view: Any,
    frequency_hz: float,
) -> str:
    """Return compact, unit-aware ``Original→Tuned`` impedance text."""

    original = log_log_interpolate_impedance(
        getattr(original_view, "frequency_hz", ()),
        getattr(original_view, "magnitude_ohm", ()),
        frequency_hz,
    )
    tuned = log_log_interpolate_impedance(
        getattr(tuned_view, "frequency_hz", ()),
        getattr(tuned_view, "magnitude_ohm", ()),
        frequency_hz,
    )
    available = [value for value in (original, tuned) if value is not None]
    if not available:
        return "N/A"

    reference = max(abs(value) for value in available)
    factor, unit = next(
        (
            (candidate_factor, candidate_unit)
            for candidate_factor, candidate_unit in _IMPEDANCE_UNITS
            if reference >= candidate_factor
        ),
        (1.0e-12, "pΩ"),
    )

    def format_value(value: float | None) -> str:
        return "N/A" if value is None else f"{value / factor:.3g}"

    return f"{format_value(original)}→{format_value(tuned)} {unit}"


class ComparisonResultsWindow(QMainWindow):
    """Non-modal, reusable large view of a comparison plot and table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("comparisonResultsWindow")
        self.setWindowTitle(f"{APP_DISPLAY_NAME} — Evaluation Results")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(1280, 820)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.setObjectName("largeResultSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        self.plot = MultiRailComparisonPlot(splitter)
        self.plot.setObjectName("largeResultComparisonPlot")
        self.plot.setMinimumHeight(420)
        self.table = QTableWidget(0, 0, splitter)
        self.table.setObjectName("largeResultComparisonTable")
        self.table.setMinimumHeight(150)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        splitter.addWidget(self.plot)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes((620, 180))
        self.setCentralWidget(splitter)

    def set_results(
        self,
        comparisons: Sequence[Any],
        *,
        rail_colors: Mapping[str, str],
        rail_labels: Mapping[str, str],
        source_table: QTableWidget,
    ) -> None:
        self.plot.set_comparisons(
            comparisons,
            rail_colors=rail_colors,
            rail_labels=rail_labels,
        )
        self._copy_table(source_table)

    def clear_results(self) -> None:
        self.plot.clear_comparisons()
        self.table.clearContents()
        self.table.setRowCount(0)

    def _copy_table(self, source: QTableWidget) -> None:
        self.table.clear()
        self.table.setColumnCount(source.columnCount())
        self.table.setRowCount(source.rowCount())
        self.table.setHorizontalHeaderLabels(
            [
                source.horizontalHeaderItem(column).text()
                if source.horizontalHeaderItem(column) is not None
                else ""
                for column in range(source.columnCount())
            ]
        )
        for row in range(source.rowCount()):
            for column in range(source.columnCount()):
                source_item = source.item(row, column)
                if source_item is None:
                    continue
                item = QTableWidgetItem(source_item.text())
                item.setTextAlignment(Qt.AlignmentFlag(source_item.textAlignment()))
                item.setToolTip(source_item.toolTip())
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()


__all__ = [
    "ComparisonResultsWindow",
    "impedance_transition_at_frequency",
    "log_log_interpolate_impedance",
]
