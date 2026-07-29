"""Shared impedance plot for Original/Tuned PWR NET comparisons."""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from math import isfinite, log10

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg

from ..evaluation import RailComparison


_BACKGROUND_COLOR = "#171A1F"
_DEFAULT_RAIL_COLOR = "#4DA3FF"
_TARGET_COLOR = "#F5B942"
_X_MARKER_COLOR = "#F5B942"
_Y_MARKER_COLOR = "#5DD6C0"
_PLOT_MINIMUM_HEIGHT = 230


class MultiRailComparisonPlot(QWidget):
    """Overlay all selected PWR NET results on one impedance-only log/log axes.

    Rail color identifies the PWR NET.  Line style identifies the result:
    Original is dashed, Tuned is solid, and Target is dotted.  Plot visibility
    is independent UI state and never changes the evaluation selection.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("multiRailComparisonPlot")
        self._plot: pg.PlotWidget | None = None
        self._has_comparisons = False
        self._result_identity: tuple[tuple[str, str, str], ...] | None = None
        self._controls: QWidget | None = None
        self._annotations: list[pg.TextItem] = []
        self._rail_items: dict[str, list[object]] = {}
        self._shared_target_items: list[tuple[object, tuple[str, ...]]] = []
        self._rail_checkboxes: dict[str, QCheckBox] = {}
        self._rail_ids: dict[str, str] = {}

        self._x_marker_checkbox: QCheckBox | None = None
        self._y_marker_checkbox: QCheckBox | None = None
        self._marker_readout: QLabel | None = None
        self._x_marker_line: pg.InfiniteLine | None = None
        self._y_marker_line: pg.InfiniteLine | None = None
        self._x_marker_value_hz: float | None = None
        self._y_marker_value_ohm: float | None = None

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
        # Keep a zero-growth sentinel so controls/plot can be inserted before
        # it without reserving half of the available height as blank space.
        self._content_layout.addStretch(0)
        self._scroll.setWidget(self._content)
        self._content.setMinimumHeight(_PLOT_MINIMUM_HEIGHT)

    @property
    def plot_widgets(self) -> tuple[pg.PlotWidget, ...]:
        """Return the one shared plot, or an empty tuple without results."""

        if self._has_comparisons and self._plot is not None:
            return (self._plot,)
        return ()

    @property
    def annotation_texts(self) -> tuple[str, ...]:
        return tuple(item.toPlainText() for item in self._annotations)

    @property
    def rail_checkboxes(self) -> Mapping[str, QCheckBox]:
        """Return rail-id keyed, result-only visibility controls."""

        return {
            self._rail_ids[key]: checkbox
            for key, checkbox in self._rail_checkboxes.items()
        }

    @property
    def x_marker_checkbox(self) -> QCheckBox | None:
        return self._x_marker_checkbox

    @property
    def y_marker_checkbox(self) -> QCheckBox | None:
        return self._y_marker_checkbox

    @property
    def x_marker_line(self) -> pg.InfiniteLine | None:
        return self._x_marker_line

    @property
    def y_marker_line(self) -> pg.InfiniteLine | None:
        return self._y_marker_line

    @property
    def marker_values(self) -> tuple[float | None, float | None]:
        """Return marker positions as physical ``(frequency_hz, impedance_ohm)``."""

        return self._x_marker_value_hz, self._y_marker_value_ohm

    @property
    def marker_readout_text(self) -> str:
        return self._marker_readout.text() if self._marker_readout is not None else ""

    def clear_comparisons(self) -> None:
        """Clear a result batch and reset visibility and marker UI state."""

        self._remove_controls()
        self._clear_plot_items()
        self._reset_dynamic_references()
        self._has_comparisons = False
        self._result_identity = None
        if self._plot is not None:
            self._plot.hide()
        self._content.setMinimumHeight(_PLOT_MINIMUM_HEIGHT)

    def set_comparisons(
        self,
        comparisons: Sequence[RailComparison],
        *,
        rail_colors: Mapping[str, str],
        rail_labels: Mapping[str, str] | None = None,
        show_target: bool = True,
    ) -> None:
        """Render comparisons on one axes, preserving state for color-only redraws."""

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

        # Validate and normalize before touching the current useful display.
        colors = {
            str(key).strip().casefold(): _valid_color(value)
            for key, value in rail_colors.items()
        }
        labels = {
            str(key).strip().casefold(): str(value).strip()
            for key, value in (rail_labels or {}).items()
        }
        if not rendered:
            self.clear_comparisons()
            return

        identity = _comparison_identity(rendered)
        preserve_state = self._has_comparisons and identity == self._result_identity
        saved_visibility = {
            key: checkbox.isChecked()
            for key, checkbox in self._rail_checkboxes.items()
        }
        saved_marker_modes = (
            bool(self._x_marker_checkbox and self._x_marker_checkbox.isChecked()),
            bool(self._y_marker_checkbox and self._y_marker_checkbox.isChecked()),
        )
        saved_marker_values = (
            self._x_marker_value_hz,
            self._y_marker_value_ohm,
        )
        saved_view_range = (
            self._plot.plotItem.vb.viewRange()
            if preserve_state and self._plot is not None
            else None
        )

        self._remove_controls()
        self._clear_plot_items()
        self._reset_dynamic_references()

        if self._plot is None:
            self._plot = self._new_plot()
            self._content_layout.insertWidget(
                self._content_layout.count() - 1, self._plot, 1
            )
        self._plot.show()

        controls = self._new_controls(rendered, colors, labels)
        self._controls = controls
        plot_index = self._content_layout.indexOf(self._plot)
        self._content_layout.insertWidget(max(plot_index, 0), controls)
        self._create_marker_items(self._plot)

        display_labels: dict[str, str] = {}
        for index, comparison in enumerate(rendered):
            key = comparison.rail_id.strip().casefold()
            color = colors.get(key, _DEFAULT_RAIL_COLOR)
            label = labels.get(key) or comparison.rail_id
            display_label = (
                label
                if label.casefold() == key
                else f"{label} ({comparison.rail_id})"
            )
            display_labels[key] = display_label
            unchanged = (
                comparison.configuration_unchanged
                or comparison.baseline.design_fingerprint
                == comparison.tuned.design_fingerprint
            )
            rail_items = self._rail_items[key]

            if unchanged:
                curve = self._plot.plot(
                    comparison.tuned.view.frequency_hz,
                    comparison.tuned.view.magnitude_ohm,
                    pen=pg.mkPen(color, width=2.4, style=Qt.PenStyle.SolidLine),
                    name=f"{display_label} Original = Tuned",
                    **_curve_options(),
                )
                rail_items.append(curve)
                annotation = pg.TextItem(
                    f"{display_label}: Original = Tuned",
                    color=color,
                    anchor=(0.0, 1.0),
                )
                x, y = _annotation_position(comparison)
                annotation.setPos(x, y - (index * 0.08))
                self._plot.addItem(annotation)
                rail_items.append(annotation)
                self._annotations.append(annotation)
            else:
                original = self._plot.plot(
                    comparison.baseline.view.frequency_hz,
                    comparison.baseline.view.magnitude_ohm,
                    pen=pg.mkPen(color, width=1.8, style=Qt.PenStyle.DashLine),
                    name=f"{display_label} Original",
                    **_curve_options(),
                )
                tuned = self._plot.plot(
                    comparison.tuned.view.frequency_hz,
                    comparison.tuned.view.magnitude_ohm,
                    pen=pg.mkPen(color, width=2.4, style=Qt.PenStyle.SolidLine),
                    name=f"{display_label} Tuned",
                    **_curve_options(),
                )
                rail_items.extend((original, tuned))

        if show_target:
            self._add_target_curves(rendered, colors, display_labels)

        self._has_comparisons = True
        self._result_identity = identity
        if preserve_state:
            for key, visible in saved_visibility.items():
                checkbox = self._rail_checkboxes.get(key)
                if checkbox is not None:
                    checkbox.setChecked(visible)
            self._restore_marker_state(saved_marker_modes, saved_marker_values)
            if saved_view_range is not None:
                self._plot.setXRange(*saved_view_range[0], padding=0)
                self._plot.setYRange(*saved_view_range[1], padding=0)
        else:
            self._plot.enableAutoRange()
            self._plot.autoRange()
            self._sync_marker_visibility()
            self._update_marker_readout()
        controls_height = max(controls.sizeHint().height(), 88)
        self._content.setMinimumHeight(
            _PLOT_MINIMUM_HEIGHT + controls_height + self._content_layout.spacing()
        )

    def set_rail_visible(self, rail_id: str, visible: bool) -> None:
        key = str(rail_id).strip().casefold()
        checkbox = self._rail_checkboxes.get(key)
        if checkbox is None:
            raise KeyError(f"unknown rail {rail_id!r}")
        checkbox.setChecked(bool(visible))
        self._apply_rail_visibility(key)

    def place_markers(self, frequency_hz: float, impedance_ohm: float) -> None:
        """Place enabled markers using physical, non-logarithmic coordinates."""

        if not self._has_comparisons or self._plot is None:
            raise RuntimeError("cannot place markers without a comparison plot")
        frequency = float(frequency_hz)
        impedance = float(impedance_ohm)
        if not isfinite(frequency) or frequency <= 0.0:
            raise ValueError("X marker frequency must be finite and positive")
        if not isfinite(impedance) or impedance <= 0.0:
            raise ValueError("Y marker impedance must be finite and positive")

        if self._x_marker_checkbox is not None and self._x_marker_checkbox.isChecked():
            self._x_marker_value_hz = frequency
            if self._x_marker_line is not None:
                self._x_marker_line.setPos(log10(frequency))
        if self._y_marker_checkbox is not None and self._y_marker_checkbox.isChecked():
            self._y_marker_value_ohm = impedance
            if self._y_marker_line is not None:
                self._y_marker_line.setPos(log10(impedance))
        self._sync_marker_visibility()
        self._update_marker_readout()

    def clear_markers(self) -> None:
        self._x_marker_value_hz = None
        self._y_marker_value_ohm = None
        self._sync_marker_visibility()
        self._update_marker_readout()

    def _new_controls(
        self,
        comparisons: Sequence[RailComparison],
        colors: Mapping[str, str],
        labels: Mapping[str, str],
    ) -> QWidget:
        controls = QWidget(self._content)
        controls.setObjectName("comparisonPlotControls")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(8, 5, 8, 5)
        controls_layout.setSpacing(4)

        channel_toolbar = QWidget(controls)
        toolbar_layout = QHBoxLayout(channel_toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(6)
        toolbar_layout.addWidget(QLabel("Plot channels", channel_toolbar))
        select_all = QPushButton("Select all", channel_toolbar)
        select_all.setObjectName("selectAllPlotChannelsButton")
        select_all.clicked.connect(lambda: self._set_all_rails_visible(True))
        clear_channels = QPushButton("Clear", channel_toolbar)
        clear_channels.setObjectName("clearPlotChannelsButton")
        clear_channels.clicked.connect(lambda: self._set_all_rails_visible(False))
        toolbar_layout.addWidget(select_all)
        toolbar_layout.addWidget(clear_channels)
        toolbar_layout.addStretch(1)
        style_key = QLabel(
            "Line style: Original --  Tuned \u2501\u2501  Target ...", channel_toolbar
        )
        style_key.setObjectName("plotLineStyleKey")
        toolbar_layout.addWidget(style_key)
        controls_layout.addWidget(channel_toolbar)

        channel_scroll = QScrollArea(controls)
        channel_scroll.setObjectName("plotChannelScrollArea")
        channel_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        channel_scroll.setWidgetResizable(False)
        channel_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        channel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        channel_scroll.setFixedHeight(38)
        visibility_row = QWidget(channel_scroll)
        visibility_row.setObjectName("railVisibilityControls")
        visibility_layout = QHBoxLayout(visibility_row)
        visibility_layout.setContentsMargins(0, 0, 0, 0)
        visibility_layout.setSpacing(12)
        for index, comparison in enumerate(comparisons):
            key = comparison.rail_id.strip().casefold()
            color = colors.get(key, _DEFAULT_RAIL_COLOR)
            label = labels.get(key) or comparison.rail_id
            checkbox_label = (
                label
                if label.casefold() == key
                else f"{label} ({comparison.rail_id})"
            )
            item = QWidget(visibility_row)
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(3)
            swatch = QLabel("\u25a0", item)
            swatch.setObjectName(f"railColorSwatch{index}")
            swatch.setToolTip(f"{comparison.rail_id}: {color}")
            swatch.setStyleSheet(f"color: {color};")
            checkbox = QCheckBox(checkbox_label, item)
            checkbox.setObjectName(f"railVisibilityCheckBox{index}")
            checkbox.setToolTip(f"Show or hide {comparison.rail_id} plot results")
            checkbox.setChecked(True)
            checkbox.toggled.connect(
                lambda _checked, rail_key=key: self._apply_rail_visibility(rail_key)
            )
            item_layout.addWidget(swatch)
            item_layout.addWidget(checkbox)
            visibility_layout.addWidget(item)
            self._rail_checkboxes[key] = checkbox
            self._rail_ids[key] = comparison.rail_id
            self._rail_items[key] = []
        visibility_layout.addStretch(1)
        visibility_row.adjustSize()
        channel_scroll.setWidget(visibility_row)
        controls_layout.addWidget(channel_scroll)

        marker_row = QWidget(controls)
        marker_row.setObjectName("markerControls")
        marker_layout = QHBoxLayout(marker_row)
        marker_layout.setContentsMargins(0, 0, 0, 0)
        marker_layout.setSpacing(8)
        self._x_marker_checkbox = QCheckBox("X marker", marker_row)
        self._x_marker_checkbox.setObjectName("xMarkerCheckBox")
        self._x_marker_checkbox.setToolTip(
            "Enable and click the plot to mark a frequency"
        )
        self._y_marker_checkbox = QCheckBox("Y marker", marker_row)
        self._y_marker_checkbox.setObjectName("yMarkerCheckBox")
        self._y_marker_checkbox.setToolTip(
            "Enable and click the plot to mark an impedance"
        )
        self._x_marker_checkbox.toggled.connect(self._marker_mode_changed)
        self._y_marker_checkbox.toggled.connect(self._marker_mode_changed)
        clear_button = QPushButton("Clear markers", marker_row)
        clear_button.setObjectName("clearMarkersButton")
        clear_button.clicked.connect(self.clear_markers)
        self._marker_readout = QLabel(marker_row)
        self._marker_readout.setObjectName("markerReadoutLabel")
        self._marker_readout.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        marker_layout.addWidget(self._x_marker_checkbox)
        marker_layout.addWidget(self._y_marker_checkbox)
        marker_layout.addWidget(clear_button)
        marker_layout.addWidget(self._marker_readout, 1)
        controls_layout.addWidget(marker_row)
        self._update_marker_readout()
        return controls

    def _new_plot(self) -> pg.PlotWidget:
        plot = pg.PlotWidget(background=_BACKGROUND_COLOR)
        plot.setObjectName("sharedRailComparisonPlot")
        plot.setMinimumHeight(_PLOT_MINIMUM_HEIGHT)
        plot.setLogMode(x=True, y=True)
        plot.showGrid(x=True, y=True, alpha=0.2)
        plot.setLabel("bottom", "Frequency", units="Hz")
        plot.setLabel("left", "|Z|", units="ohm")
        plot.setTitle("PWR NET Impedance Comparison", color="#E6EDF3", size="11pt")
        plot.scene().sigMouseClicked.connect(self._plot_clicked)
        return plot

    def _create_marker_items(self, plot: pg.PlotWidget) -> None:
        self._x_marker_line = pg.InfiniteLine(
            angle=90,
            movable=True,
            pen=pg.mkPen(_X_MARKER_COLOR, width=1.6),
            hoverPen=pg.mkPen(_X_MARKER_COLOR, width=2.5),
        )
        self._x_marker_line.setObjectName("xMarkerLine")
        self._x_marker_line.setZValue(100)
        self._x_marker_line.sigPositionChanged.connect(self._x_marker_moved)
        plot.addItem(self._x_marker_line, ignoreBounds=True)

        self._y_marker_line = pg.InfiniteLine(
            angle=0,
            movable=True,
            pen=pg.mkPen(_Y_MARKER_COLOR, width=1.6),
            hoverPen=pg.mkPen(_Y_MARKER_COLOR, width=2.5),
        )
        self._y_marker_line.setObjectName("yMarkerLine")
        self._y_marker_line.setZValue(100)
        self._y_marker_line.sigPositionChanged.connect(self._y_marker_moved)
        plot.addItem(self._y_marker_line, ignoreBounds=True)
        self._sync_marker_visibility()

    def _add_target_curves(
        self,
        comparisons: Sequence[RailComparison],
        colors: Mapping[str, str],
        display_labels: Mapping[str, str],
    ) -> None:
        assert self._plot is not None
        entries = [
            (comparison, _target_signature(comparison))
            for comparison in comparisons
            if comparison.tuned.view.target_curve_ohm
        ]
        if not entries:
            return
        groups: dict[Hashable, list[RailComparison]] = {}
        for comparison, signature in entries:
            groups.setdefault(signature, []).append(comparison)

        for signature, group in groups.items():
            source = group[0]
            keys = tuple(
                comparison.rail_id.strip().casefold() for comparison in group
            )
            is_only_group = len(groups) == 1 and len(group) == len(entries)
            if is_only_group:
                name = "Target"
                color = _TARGET_COLOR
            else:
                name = " / ".join(display_labels[key] for key in keys) + " Target"
                color = colors.get(keys[0], _DEFAULT_RAIL_COLOR)
            target_frequency = source.tuned.view.frequency_hz
            target_values = source.tuned.view.target_curve_ohm
            if signature[0] == "constant":
                positive_frequencies = [
                    float(frequency)
                    for comparison in group
                    for frequency in comparison.tuned.view.frequency_hz
                    if isfinite(float(frequency)) and float(frequency) > 0.0
                ]
                if positive_frequencies:
                    minimum = min(positive_frequencies)
                    maximum = max(positive_frequencies)
                    target_frequency = [minimum, maximum]
                    target_values = [float(signature[1]), float(signature[1])]
            target = self._plot.plot(
                target_frequency,
                target_values,
                pen=pg.mkPen(color, width=1.4, style=Qt.PenStyle.DotLine),
                name=name,
                **_curve_options(),
            )
            if len(keys) == 1:
                self._rail_items[keys[0]].append(target)
            else:
                self._shared_target_items.append((target, keys))

    def _restore_marker_state(
        self,
        modes: tuple[bool, bool],
        values: tuple[float | None, float | None],
    ) -> None:
        self._x_marker_value_hz, self._y_marker_value_ohm = values
        if self._x_marker_line is not None and values[0] is not None:
            self._x_marker_line.setPos(log10(values[0]))
        if self._y_marker_line is not None and values[1] is not None:
            self._y_marker_line.setPos(log10(values[1]))
        if self._x_marker_checkbox is not None:
            self._x_marker_checkbox.setChecked(modes[0])
        if self._y_marker_checkbox is not None:
            self._y_marker_checkbox.setChecked(modes[1])
        self._sync_marker_visibility()
        self._update_marker_readout()

    def _remove_controls(self) -> None:
        if self._controls is not None:
            self._controls.setParent(None)
            self._controls.deleteLater()
            self._controls = None

    def _clear_plot_items(self) -> None:
        if self._plot is None:
            return
        self._plot.clear()
        legend = self._plot.plotItem.legend
        if legend is not None:
            legend.clear()

    def _reset_dynamic_references(self) -> None:
        self._annotations.clear()
        self._rail_items.clear()
        self._shared_target_items.clear()
        self._rail_checkboxes.clear()
        self._rail_ids.clear()
        self._x_marker_checkbox = None
        self._y_marker_checkbox = None
        self._marker_readout = None
        self._x_marker_line = None
        self._y_marker_line = None
        self._x_marker_value_hz = None
        self._y_marker_value_ohm = None

    def _set_all_rails_visible(self, visible: bool) -> None:
        for checkbox in self._rail_checkboxes.values():
            checkbox.setChecked(visible)

    def _apply_rail_visibility(self, rail_key: str) -> None:
        checkbox = self._rail_checkboxes.get(rail_key)
        if checkbox is None:
            return
        for item in self._rail_items.get(rail_key, ()):
            item.setVisible(checkbox.isChecked())
        for item, member_keys in self._shared_target_items:
            item.setVisible(
                any(
                    self._rail_checkboxes[key].isChecked()
                    for key in member_keys
                    if key in self._rail_checkboxes
                )
            )

    def _marker_mode_changed(self, _checked: bool) -> None:
        self._sync_marker_visibility()
        self._update_marker_readout()

    def _sync_marker_visibility(self) -> None:
        if self._x_marker_line is not None:
            self._x_marker_line.setVisible(
                bool(
                    self._x_marker_checkbox
                    and self._x_marker_checkbox.isChecked()
                    and self._x_marker_value_hz is not None
                )
            )
        if self._y_marker_line is not None:
            self._y_marker_line.setVisible(
                bool(
                    self._y_marker_checkbox
                    and self._y_marker_checkbox.isChecked()
                    and self._y_marker_value_ohm is not None
                )
            )

    def _plot_clicked(self, event: object) -> None:
        if not self._has_comparisons or self._plot is None:
            return
        button = getattr(event, "button", lambda: None)()
        if button != Qt.MouseButton.LeftButton:
            return
        scene_pos = getattr(event, "scenePos", lambda: None)()
        view_box = self._plot.plotItem.vb
        if scene_pos is None or not view_box.sceneBoundingRect().contains(scene_pos):
            return
        log_point = view_box.mapSceneToView(scene_pos)
        try:
            # pyqtgraph ViewBox coordinates are log10 values in log mode.
            frequency = 10.0 ** float(log_point.x())
            impedance = 10.0 ** float(log_point.y())
        except (OverflowError, TypeError, ValueError):
            return
        if isfinite(frequency) and isfinite(impedance):
            self.place_markers(frequency, impedance)

    def _x_marker_moved(self) -> None:
        if self._x_marker_line is None or not self._x_marker_line.isVisible():
            return
        try:
            value = 10.0 ** float(self._x_marker_line.value())
        except (OverflowError, TypeError, ValueError):
            return
        if isfinite(value) and value > 0.0:
            self._x_marker_value_hz = value
            self._update_marker_readout()

    def _y_marker_moved(self) -> None:
        if self._y_marker_line is None or not self._y_marker_line.isVisible():
            return
        try:
            value = 10.0 ** float(self._y_marker_line.value())
        except (OverflowError, TypeError, ValueError):
            return
        if isfinite(value) and value > 0.0:
            self._y_marker_value_ohm = value
            self._update_marker_readout()

    def _update_marker_readout(self) -> None:
        if self._marker_readout is None:
            return
        x_enabled = bool(
            self._x_marker_checkbox and self._x_marker_checkbox.isChecked()
        )
        y_enabled = bool(
            self._y_marker_checkbox and self._y_marker_checkbox.isChecked()
        )
        x_text = (
            _format_frequency(self._x_marker_value_hz)
            if x_enabled and self._x_marker_value_hz is not None
            else ("click plot" if x_enabled else "off")
        )
        y_text = (
            _format_impedance(self._y_marker_value_ohm)
            if y_enabled and self._y_marker_value_ohm is not None
            else ("click plot" if y_enabled else "off")
        )
        self._marker_readout.setText(f"X: {x_text}    Y: {y_text}")


def _comparison_identity(
    comparisons: Sequence[RailComparison],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            comparison.rail_id.strip().casefold(),
            comparison.baseline.result_key.cache_key,
            comparison.tuned.result_key.cache_key,
        )
        for comparison in comparisons
    )


def _target_signature(comparison: RailComparison) -> Hashable:
    frequencies = tuple(float(value) for value in comparison.tuned.view.frequency_hz)
    values = tuple(float(value) for value in comparison.tuned.view.target_curve_ohm)
    if values and all(value == values[0] for value in values[1:]):
        # A constant target is the same function even if adaptive solver grids
        # differ between rails.
        return ("constant", values[0])
    return ("sampled", tuple(zip(frequencies, values, strict=False)))


def _valid_color(value: object) -> str:
    color = QColor(str(value))
    return color.name() if color.isValid() else _DEFAULT_RAIL_COLOR


def _curve_options() -> dict[str, object]:
    return {"antialias": True, "connect": "finite"}


def _annotation_position(comparison: RailComparison) -> tuple[float, float]:
    frequencies = [
        float(value)
        for value in comparison.tuned.view.frequency_hz
        if isfinite(float(value)) and float(value) > 0.0
    ]
    magnitudes = [
        float(value)
        for value in comparison.tuned.view.magnitude_ohm
        if isfinite(float(value)) and float(value) > 0.0
    ]
    x = log10(frequencies[0]) if frequencies else 0.0
    y = log10(max(magnitudes)) if magnitudes else 0.0
    return x, y


def _format_frequency(value_hz: float) -> str:
    for scale, suffix in (
        (1.0e9, "GHz"),
        (1.0e6, "MHz"),
        (1.0e3, "kHz"),
    ):
        if value_hz >= scale:
            return f"{value_hz / scale:.4g} {suffix}"
    return f"{value_hz:.4g} Hz"


def _format_impedance(value_ohm: float) -> str:
    if value_ohm < 1.0e-3:
        return f"{value_ohm * 1.0e6:.4g} uohm"
    if value_ohm < 1.0:
        return f"{value_ohm * 1.0e3:.4g} mohm"
    return f"{value_ohm:.4g} ohm"


__all__ = ["MultiRailComparisonPlot"]
