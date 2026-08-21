"""Large, reusable presentation window for PI comparison results."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Mapping, Sequence
from math import isfinite, log10
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
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

# Comparison tables intentionally remain wider than a narrow side pane.  Their
# horizontal scrollbar is the reachable overflow boundary; unbounded Qt size
# hints must never resize the containing window or splitter.
COMPARISON_COLUMN_MAX_WIDTH = 360


def size_comparison_table_columns(
    table: QTableWidget,
    *,
    max_width: int = COMPARISON_COLUMN_MAX_WIDTH,
) -> None:
    """Apply bounded, scrollable sizing to a comparison table.

    Cell text is left intact.  Long cells receive a full-text tooltip so the
    bounded column width does not hide information from keyboard/mouse users.
    """

    bounded_width = max(120, int(max_width))
    table.setMinimumWidth(0)
    table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    header = table.horizontalHeader()
    for column in range(table.columnCount()):
        width = max(header.sectionSizeHint(column), table.sizeHintForColumn(column))
        header.resizeSection(column, min(bounded_width, max(80, width)))
        for row in range(table.rowCount()):
            item = table.item(row, column)
            if item is not None and len(item.text()) > 48 and not item.toolTip():
                item.setToolTip(item.text())


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

        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 8)
        central_layout.setSpacing(6)
        self.provenance_label = QLabel(central)
        self.provenance_label.setObjectName("resultSolverProvenance")
        self.provenance_label.setWordWrap(True)
        self.provenance_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.provenance_label.hide()
        central_layout.addWidget(self.provenance_label)

        splitter = QSplitter(Qt.Orientation.Vertical, central)
        splitter.setObjectName("largeResultSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        splitter.setStyleSheet(
            """
            QSplitter::handle:vertical {
                background: #94A3B8;
                border-top: 1px solid #CBD5E1;
                border-bottom: 1px solid #475569;
                margin: 2px 0px;
            }
            QSplitter::handle:vertical:hover,
            QSplitter::handle:vertical:pressed {
                background: #3B82F6;
            }
            """
        )
        self.plot = MultiRailComparisonPlot(splitter)
        self.plot.setObjectName("largeResultComparisonPlot")
        self.plot.setMinimumHeight(420)
        self.table = QTableWidget(0, 0, splitter)
        self.table.setObjectName("largeResultComparisonTable")
        self.table.setMinimumHeight(150)
        self.table.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        splitter.addWidget(self.plot)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes((620, 180))
        central_layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

    def set_provenance(
        self,
        text: str,
        *,
        research: bool,
        details: str | None = None,
    ) -> None:
        """Show immutable result-derived solver identity above the plot."""

        normalized = str(text).strip()
        if not normalized:
            raise ValueError("result solver provenance must be nonblank")
        self.provenance_label.setText(normalized)
        self.provenance_label.setToolTip(str(details or normalized).strip())
        self.provenance_label.setStyleSheet(
            (
                "color: #d6a64f; background: #2a2415; border: 1px solid #806b2c; "
                "padding: 6px; font-weight: 700;"
            )
            if research
            else (
                "color: #b9d6ee; background: #172431; border: 1px solid #3a607c; "
                "padding: 6px; font-weight: 600;"
            )
        )
        self.provenance_label.show()

    def set_results(
        self,
        comparisons: Sequence[Any],
        *,
        rail_colors: Mapping[str, str],
        rail_labels: Mapping[str, str],
        source_table: QTableWidget,
    ) -> None:
        self.set_plot_results(
            comparisons,
            rail_colors=rail_colors,
            rail_labels=rail_labels,
        )
        self.copy_table_from(source_table)

    def set_plot_results(
        self,
        comparisons: Sequence[Any],
        *,
        rail_colors: Mapping[str, str],
        rail_labels: Mapping[str, str],
    ) -> None:
        """Render and validate the detached plot without copying its table."""

        self.plot.set_comparisons(
            comparisons,
            rail_colors=rail_colors,
            rail_labels=rail_labels,
        )

    def copy_table_from(self, source_table: QTableWidget) -> None:
        """Copy an already accepted comparison table without rerendering the plot."""

        self._copy_table(source_table)

    def clear_results(self) -> None:
        self.plot.clear_comparisons()
        self.table.clearContents()
        self.table.setRowCount(0)
        self.provenance_label.clear()
        self.provenance_label.hide()

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
        size_comparison_table_columns(self.table)


__all__ = [
    "COMPARISON_COLUMN_MAX_WIDTH",
    "ComparisonResultsWindow",
    "impedance_transition_at_frequency",
    "log_log_interpolate_impedance",
    "size_comparison_table_columns",
]
