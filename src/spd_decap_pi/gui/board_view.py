"""High-volume interactive top-side decap board view.

The widget intentionally renders decaps in a small, fixed number of
``ScatterPlotItem`` batches and bumps in one batch per NET.  A board with tens
of thousands of capacitors and bumps therefore does not create one
``QGraphicsItem`` per component.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math
from typing import Any

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QRubberBand,
    QWidget,
)
import pyqtgraph as pg


_MISSING = object()


@dataclass(frozen=True, slots=True)
class _BoardDecap:
    refdes: str
    x_um: float
    y_um: float
    current_net: str
    enabled: bool
    component: str
    footprint: str


@dataclass(frozen=True, slots=True)
class _BoardBump:
    net: str
    x_um: float
    y_um: float


class _BoardViewBox(pg.ViewBox):
    """ViewBox that reserves plain dragging for marquee selection."""

    leftClicked = Signal(object, bool)
    marqueeFinished = Signal(object, bool)
    rightClicked = Signal(object, object)

    def __init__(self) -> None:
        super().__init__(enableMenu=False)
        self.setMouseMode(pg.ViewBox.PanMode)
        self.setAspectLocked(lock=True, ratio=1.0)

    def mouseClickEvent(self, event: Any) -> None:  # noqa: N802 - Qt API
        button = event.button()
        view_position = self.mapSceneToView(event.scenePos())
        modifiers = event.modifiers()
        if button == Qt.MouseButton.LeftButton:
            additive = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            self.leftClicked.emit(view_position, additive)
            event.accept()
            return
        if button == Qt.MouseButton.RightButton:
            screen_position = event.screenPos()
            if hasattr(screen_position, "toPoint"):
                screen_position = screen_position.toPoint()
            else:
                screen_position = QPoint(
                    round(screen_position.x()), round(screen_position.y())
                )
            self.rightClicked.emit(view_position, screen_position)
            event.accept()
            return
        super().mouseClickEvent(event)

    def mouseDragEvent(self, event: Any, axis: int | None = None) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            # Right-drag scaling conflicts with the board context-menu gesture.
            event.accept()
            return

        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # The normal ViewBox left-drag behavior is panning.  Expose it only
            # behind Shift so a plain drag remains an unambiguous selection.
            super().mouseDragEvent(event, axis=axis)
            return

        event.accept()
        if event.isFinish():
            self.rbScaleBox.hide()
            parent_rect = QRectF(event.buttonDownPos(event.button()), event.pos())
            view_rect = self.childGroup.mapRectFromParent(parent_rect).normalized()
            additive = bool(
                event.modifiers() & Qt.KeyboardModifier.ControlModifier
            )
            self.marqueeFinished.emit(view_rect, additive)
            return
        self.updateScaleBox(event.buttonDownPos(event.button()), event.pos())


class DecapBoardView(pg.PlotWidget):
    """Interactive, batched board map for top-side decap scenario editing.

    ``records`` may contain mappings, dataclasses, Pydantic models, or other
    attribute objects.  Each record must provide ``refdes``, ``current_net``,
    ``enabled`` and either top-level ``x_um``/``y_um`` values or a ``center``
    object containing those coordinates.
    """

    selectionChanged = Signal(tuple)
    contextMenuRequested = Signal(tuple, QPoint)

    POINT_SIZE_PX = 10.0
    SELECTED_SIZE_PX = 17.0
    HIT_RADIUS_PX = 8.0
    BUMP_SIZE_PX = 5.5
    BUMP_HIT_RADIUS_PX = 5.0
    SELECTED_COLOR = QColor("#ffeb3b")
    REQUIRED_COMPANION_COLOR = QColor("#f59e0b")
    DISABLED_FILL = QColor("#73777d")
    DISABLED_X_COLOR = QColor("#ff5252")
    DISABLED_X_SIZE_PX = 12.0
    FALLBACK_NET_COLOR = QColor("#5ba8ff")
    INACTIVE_NET_COLOR = QColor("#6b7280")

    def __init__(
        self,
        records: Iterable[object] = (),
        net_colors: Mapping[str, object] | None = None,
        parent: QWidget | None = None,
        *,
        bumps: Iterable[object] = (),
    ) -> None:
        # PySide may clear Python attributes assigned before the QWidget C++
        # constructor runs, so retain the ViewBox locally until after ``super``.
        view_box = _BoardViewBox()
        plot_item = pg.PlotItem(viewBox=view_box, enableMenu=False)
        super().__init__(parent=parent, plotItem=plot_item, background="#171a1f")
        self._view_box = view_box

        self.setObjectName("decapBoardView")
        self.setWhatsThis(
            "Left-click: select | Ctrl+click: toggle | Drag: box select | "
            "Shift+drag: pan | Wheel: zoom | Right-click: edit selection"
        )
        self.plotItem.hideButtons()
        self.plotItem.showGrid(x=True, y=True, alpha=0.12)
        self.plotItem.setLabel("bottom", "X", units="µm")
        self.plotItem.setLabel("left", "Y", units="µm")
        self.plotItem.getAxis("bottom").enableAutoSIPrefix(False)
        self.plotItem.getAxis("left").enableAutoSIPrefix(False)
        self.plotItem.setDownsampling(auto=True, mode="peak")
        self.viewport().setMouseTracking(True)

        self._gesture_button = Qt.MouseButton.NoButton
        self._gesture_start = QPoint()
        self._gesture_last = QPoint()
        self._gesture_additive = False
        self._gesture_shift_pan = False
        self._gesture_dragged = False
        self._rubber_band = QRubberBand(
            QRubberBand.Shape.Rectangle, self.viewport()
        )

        self._plane_items: list[QGraphicsItem] = []
        self._records: tuple[_BoardDecap, ...] = ()
        self._bumps: tuple[_BoardBump, ...] = ()
        self._refdes_index: dict[str, int] = {}
        self._selection_keys: set[str] = set()
        self._companion_keys: set[str] = set()
        self._connection_labels: dict[str, str] = {}
        self._net_colors: dict[str, QColor] = {}
        self._active_net_keys: set[str] | None = None
        self._x_values = np.empty(0, dtype=np.float64)
        self._y_values = np.empty(0, dtype=np.float64)
        self._bump_x_values = np.empty(0, dtype=np.float64)
        self._bump_y_values = np.empty(0, dtype=np.float64)
        self._enabled_values = np.empty(0, dtype=np.bool_)
        self._hover_target: tuple[str, int] | None = None
        self._bump_scatters_by_net: dict[str, pg.ScatterPlotItem] = {}
        self._bump_net_names: dict[str, str] = {}
        self._enabled_scatter = pg.ScatterPlotItem(
            name="Enabled decaps",
            pxMode=True,
            hoverable=False,
            tip=None,
        )
        self._disabled_scatter = pg.ScatterPlotItem(
            name="Disabled decaps",
            pxMode=True,
            hoverable=False,
            tip=None,
        )
        self._disabled_x_scatter = pg.ScatterPlotItem(
            name="Disabled decap X marks",
            pxMode=True,
            hoverable=False,
            tip=None,
        )
        self._selected_scatter = pg.ScatterPlotItem(
            name="Selected decaps",
            pxMode=True,
            hoverable=False,
            tip=None,
        )
        self._companion_scatter = pg.ScatterPlotItem(
            name="Required shared-pad companions",
            pxMode=True,
            hoverable=False,
            tip=None,
        )
        self._enabled_scatter.setZValue(10)
        self._disabled_scatter.setZValue(11)
        self._disabled_x_scatter.setZValue(12)
        self._selected_scatter.setZValue(20)
        self._companion_scatter.setZValue(19)
        self.plotItem.addItem(self._enabled_scatter)
        self.plotItem.addItem(self._disabled_scatter)
        self.plotItem.addItem(self._disabled_x_scatter)
        self.plotItem.addItem(self._selected_scatter)
        self.plotItem.addItem(self._companion_scatter)

        self._view_box.leftClicked.connect(self._handle_left_click)
        self._view_box.marqueeFinished.connect(self._handle_marquee)
        self._view_box.rightClicked.connect(self._handle_right_click)

        self.set_net_colors(net_colors or {})
        self.set_decaps(records)
        self.set_bumps(bumps)
        # PlotWidget installs PlotItem.clear as an instance attribute.  Restore
        # board semantics so callers cannot accidentally remove the fixed
        # scatter layers and leave this widget unusable.
        self.clear = self.clear_board  # type: ignore[method-assign]

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self._clear_hover_tooltip()
        if event.button() not in (
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.RightButton,
        ):
            super().mousePressEvent(event)
            return
        self._gesture_button = event.button()
        self._gesture_start = event.position().toPoint()
        self._gesture_last = self._gesture_start
        self._gesture_additive = bool(
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )
        self._gesture_shift_pan = bool(
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        )
        self._gesture_dragged = False
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._gesture_button == Qt.MouseButton.NoButton:
            self._update_hover_tooltip(event.position().toPoint())
            super().mouseMoveEvent(event)
            return

        current = event.position().toPoint()
        distance = (current - self._gesture_start).manhattanLength()
        if distance >= QApplication.startDragDistance():
            self._gesture_dragged = True

        if self._gesture_button == Qt.MouseButton.LeftButton and self._gesture_dragged:
            if self._gesture_shift_pan:
                previous_view = self._viewport_to_view(self._gesture_last)
                current_view = self._viewport_to_view(current)
                self._view_box.translateBy(
                    x=previous_view.x() - current_view.x(),
                    y=previous_view.y() - current_view.y(),
                )
                self._view_box.sigRangeChangedManually.emit([True, True])
            else:
                self._rubber_band.setGeometry(
                    QRect(self._gesture_start, current).normalized()
                )
                self._rubber_band.show()
        self._gesture_last = current
        event.accept()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        self._clear_hover_tooltip()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() != self._gesture_button:
            super().mouseReleaseEvent(event)
            return

        current = event.position().toPoint()
        button = self._gesture_button
        dragged = self._gesture_dragged
        shift_pan = self._gesture_shift_pan
        additive = self._gesture_additive
        self._reset_gesture()

        if button == Qt.MouseButton.LeftButton:
            if dragged and shift_pan:
                event.accept()
                return
            if dragged:
                start_view = self._viewport_to_view(self._gesture_start)
                end_view = self._viewport_to_view(current)
                self._handle_marquee(QRectF(start_view, end_view), additive)
            else:
                self._handle_left_click(self._viewport_to_view(current), additive)
            event.accept()
            return

        if button == Qt.MouseButton.RightButton and not dragged:
            self._handle_right_click(
                self._viewport_to_view(current), event.globalPosition().toPoint()
            )
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta == 0:
            event.ignore()
            return
        scale = 1.02 ** (-float(delta) / 8.0)
        center = self._viewport_to_view(event.position().toPoint())
        self._view_box.scaleBy((scale, scale), center=center)
        self._view_box.sigRangeChangedManually.emit([True, True])
        event.accept()

    def _reset_gesture(self) -> None:
        self._rubber_band.hide()
        self._gesture_button = Qt.MouseButton.NoButton
        self._gesture_dragged = False
        self._gesture_shift_pan = False

    def _viewport_to_view(self, position: QPoint) -> QPointF:
        return self._view_box.mapSceneToView(self.mapToScene(position))

    @property
    def selected_refdes(self) -> tuple[str, ...]:
        """Selected reference designators in stable board-record order."""

        return tuple(
            record.refdes
            for record in self._records
            if record.refdes.casefold() in self._selection_keys
        )

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def bump_count(self) -> int:
        return len(self._bumps)

    def color_for_net(self, net: str) -> QColor:
        """Return the exact color currently used to render ``net``."""

        return QColor(self._color_for_net(net))

    def display_color_for_net(self, net: str) -> QColor:
        """Return the focused board color, greying an inactive NET."""

        return QColor(self._display_color_for_net(net))

    def set_decaps(
        self,
        records: Iterable[object],
        net_colors: Mapping[str, object] | None = None,
        *,
        fit: bool = True,
        normalized: bool = False,
        staged: bool = False,
    ) -> None:
        """Replace the rendered decap records while preserving valid selection."""

        self._clear_hover_tooltip()
        self._base_render_token = getattr(self, "_base_render_token", 0) + 1
        # A direct replacement invalidates any queued staged append and must
        # not leave later focus changes believing a render is still pending.
        self._base_render_pending = False
        normalized = tuple(records) if normalized else self.prepare_decaps(records)
        refdes_index: dict[str, int] = {}
        for index, record in enumerate(normalized):
            key = record.refdes.casefold()
            if key in refdes_index:
                first = normalized[refdes_index[key]].refdes
                raise ValueError(
                    "duplicate case-insensitive REFDES values are ambiguous: "
                    f"{first!r} and {record.refdes!r}"
                )
            refdes_index[key] = index

        old_selection = self.selected_refdes
        had_records = bool(self._records)
        self._records = normalized
        self._refdes_index = refdes_index
        self._x_values = np.asarray(
            [record.x_um for record in normalized], dtype=np.float64
        )
        self._y_values = np.asarray(
            [record.y_um for record in normalized], dtype=np.float64
        )
        self._enabled_values = np.asarray(
            [record.enabled for record in normalized], dtype=np.bool_
        )
        self._selection_keys.intersection_update(refdes_index)
        self._companion_keys.intersection_update(refdes_index)

        colors_changed = False
        if net_colors is not None:
            normalized_colors = self._normalize_net_colors(net_colors)
            colors_changed = normalized_colors != self._net_colors
            self._net_colors = normalized_colors
        if staged:
            self._render_base_layers_staged(self._base_render_token)
        else:
            self._render_base_layers()
        if colors_changed:
            self._restyle_bump_layers()
        self._render_selection_layer()
        self._render_companion_layer()

        if self.selected_refdes != old_selection:
            self.selectionChanged.emit(self.selected_refdes)
        if fit and normalized and not had_records:
            self.fit_board()

    def clear_decaps(self) -> None:
        """Remove all decaps without disturbing plane graphics."""

        self.set_decaps(())

    def set_connection_labels(self, labels: Mapping[str, str]) -> None:
        """Set source-connectivity text displayed in decap hover details."""

        normalized = {
            str(refdes).strip().casefold(): str(label).strip()
            for refdes, label in labels.items()
            if str(refdes).strip() and str(label).strip()
        }
        if normalized == self._connection_labels:
            return
        self._connection_labels = normalized
        self._clear_hover_tooltip()

    def set_required_companion_refdes(self, refdes: Iterable[str]) -> None:
        """Highlight unselected members required for one atomic cluster edit."""

        keys = {
            str(item).strip().casefold()
            for item in refdes
            if str(item).strip()
        }.intersection(self._refdes_index)
        if keys == self._companion_keys:
            return
        self._companion_keys = keys
        self._render_companion_layer()

    def set_bumps(
        self,
        records: Iterable[object],
        *,
        fit: bool = True,
        normalized: bool = False,
        staged: bool = False,
    ) -> None:
        """Replace the fixed, batched Device-bump layer."""

        normalized = tuple(records) if normalized else self.prepare_bumps(records)
        if normalized == self._bumps:
            return
        self._clear_hover_tooltip()
        had_bumps = bool(self._bumps)
        for scatter in self._bump_scatters_by_net.values():
            self.plotItem.removeItem(scatter)
        self._bump_scatters_by_net.clear()
        self._bump_net_names.clear()
        self._bumps = normalized
        self._bump_x_values = np.asarray(
            [record.x_um for record in normalized], dtype=np.float64
        )
        self._bump_y_values = np.asarray(
            [record.y_um for record in normalized], dtype=np.float64
        )
        indices_by_net: dict[str, list[int]] = {}
        for index, record in enumerate(normalized):
            key = record.net.casefold()
            indices_by_net.setdefault(key, []).append(index)
            self._bump_net_names.setdefault(key, record.net)
        keys = sorted(indices_by_net)
        self._bump_render_token = getattr(self, "_bump_render_token", 0) + 1
        token = self._bump_render_token
        if staged:
            def append(index: int) -> None:
                if token != self._bump_render_token or index >= len(keys):
                    return
                key = keys[index]
                self._add_bump_scatter(key, indices_by_net[key])
                QTimer.singleShot(0, lambda: append(index + 1))

            QTimer.singleShot(0, lambda: append(0))
        else:
            for key in keys:
                self._add_bump_scatter(key, indices_by_net[key])
        if fit and normalized and not had_bumps:
            self.fit_board()

    def clear_bumps(self) -> None:
        """Remove all Device bumps without disturbing decaps or planes."""

        self.set_bumps(())

    def _add_bump_scatter(self, key: str, source_indices: list[int]) -> None:
        indices = np.asarray(source_indices, dtype=np.int64)
        net = self._bump_net_names[key]
        color = self._display_color_for_net(net)
        scatter = pg.ScatterPlotItem(
            name=f"Device bumps: {net}", pxMode=True, hoverable=False, tip=None
        )
        scatter.setZValue(5)
        scatter.setData(
            x=self._bump_x_values[indices],
            y=self._bump_y_values[indices],
            data=[net] * len(indices),
            brush=pg.mkBrush(color),
            pen=pg.mkPen(QColor(color).darker(135), width=0.8),
            size=self.BUMP_SIZE_PX,
            symbol="d",
            pxMode=True,
        )
        self.plotItem.addItem(scatter)
        self._bump_scatters_by_net[key] = scatter

    def set_active_nets(self, nets: Iterable[str] | None) -> None:
        """Keep selected NETs colored and render every other NET in grey."""

        normalized = (
            None
            if nets is None
            else {
                str(net).strip().casefold()
                for net in nets
                if str(net).strip()
            }
        )
        if normalized == self._active_net_keys:
            return
        self._active_net_keys = normalized
        if getattr(self, "_base_render_pending", False):
            # Bumps may already be visible (or be appended asynchronously).
            # Restyle the visible portion; each later append reads the current
            # active NET set.  Chunks already appended keep the colors they
            # were built with, so flag a full recolor for staged completion.
            self._active_nets_dirty = True
            self._restyle_bump_layers()
            return
        self._render_base_layers()
        self._restyle_bump_layers()

    def set_net_colors(self, net_colors: Mapping[str, object]) -> None:
        """Set case-insensitive per-net colors and refresh the base layers."""

        self._net_colors = self._normalize_net_colors(net_colors)
        if hasattr(self, "_enabled_scatter"):
            self._render_base_layers()
            self._restyle_bump_layers()

    def set_selected_refdes(self, refdes: Iterable[str]) -> None:
        """Replace selection with exact, case-insensitive REFDES matches."""

        keys = {
            str(value).strip().casefold()
            for value in refdes
            if str(value).strip().casefold() in self._refdes_index
        }
        self._set_selection_keys(keys)

    def select_refdes(
        self,
        query: str | Iterable[str],
        *,
        additive: bool = False,
    ) -> tuple[str, ...]:
        """Select REFDES matches and return the resulting ordered selection.

        A string performs a case-insensitive substring search, preferring the
        naturally equivalent exact result.  An iterable performs exact matches.
        This makes the same API useful for both search-box and table selection.
        """

        if isinstance(query, str):
            normalized_query = query.strip().casefold()
            if not normalized_query:
                matches: set[str] = set()
            elif normalized_query in self._refdes_index:
                matches = {normalized_query}
            else:
                matches = {
                    record.refdes.casefold()
                    for record in self._records
                    if normalized_query in record.refdes.casefold()
                }
        else:
            matches = {
                str(value).strip().casefold()
                for value in query
                if str(value).strip().casefold() in self._refdes_index
            }

        self._set_selection_keys(
            self._selection_keys | matches if additive else matches
        )
        return self.selected_refdes

    def center_on_refdes(self, query: str) -> bool:
        """Center the current view on an exact or first substring REFDES match."""

        key = self._first_matching_key(query)
        if key is None:
            return False
        record = self._records[self._refdes_index[key]]
        x_range, y_range = self._view_box.viewRange()
        width = abs(float(x_range[1] - x_range[0]))
        height = abs(float(y_range[1] - y_range[0]))
        if (
            not math.isfinite(width)
            or not math.isfinite(height)
            or width <= 0
            or height <= 0
        ):
            self.fit_board()
            x_range, y_range = self._view_box.viewRange()
            width = abs(float(x_range[1] - x_range[0]))
            height = abs(float(y_range[1] - y_range[0]))
        self._view_box.setRange(
            xRange=(record.x_um - width / 2.0, record.x_um + width / 2.0),
            yRange=(record.y_um - height / 2.0, record.y_um + height / 2.0),
            padding=0.0,
        )
        return True

    def fit_board(self) -> None:
        """Fit all current plane and decap batches in the viewport."""

        items: list[QGraphicsItem] = [
            item for item in self._plane_items if item.isVisible()
        ]
        if self._records:
            items.extend((self._enabled_scatter, self._disabled_scatter))
        if self._bumps:
            items.extend(self._bump_scatters_by_net.values())
        if items:
            self._view_box.autoRange(padding=0.06, items=items)

    def set_plane_items(self, items: Iterable[QGraphicsItem]) -> None:
        """Replace board-plane graphics and keep them behind decap markers."""

        self.clear_plane_items()
        self.append_plane_items(items)

    def append_plane_items(self, items: Iterable[QGraphicsItem]) -> None:
        """Append a render chunk of board-plane graphics behind decap markers."""

        seen: set[int] = set()
        for item in items:
            if id(item) in seen:
                continue
            seen.add(id(item))
            item.setZValue(-100)
            self.plotItem.addItem(item)
            self._plane_items.append(item)

    def clear_plane_items(self) -> None:
        """Remove all plane graphics while preserving decaps and selection."""

        for item in self._plane_items:
            try:
                self.plotItem.removeItem(item)
            except RuntimeError:
                # The item's C++ owner may already have deleted it.
                pass
        self._plane_items.clear()

    def clear_board(self) -> None:
        """Remove plane graphics, decaps, and selection from the board."""

        self.clear_plane_items()
        self.clear_decaps()
        self.clear_bumps()

    def _render_base_layers(self) -> None:
        if not self._records:
            self._enabled_scatter.setData(x=[], y=[])
            self._disabled_scatter.setData(x=[], y=[])
            self._disabled_x_scatter.setData(x=[], y=[])
            return

        enabled_indices = np.flatnonzero(self._enabled_values)
        disabled_indices = np.flatnonzero(~self._enabled_values)
        self._set_base_scatter(self._enabled_scatter, enabled_indices, enabled=True)
        self._set_base_scatter(
            self._disabled_scatter, disabled_indices, enabled=False
        )
        self._set_disabled_x_scatter(disabled_indices)

    def _render_base_layers_staged(self, token: int) -> None:
        """Append modest decap batches so initial large-board paint yields."""

        enabled_indices = np.flatnonzero(self._enabled_values)
        disabled_indices = np.flatnonzero(~self._enabled_values)
        batches = (
            (self._enabled_scatter, enabled_indices, True),
            (self._disabled_scatter, disabled_indices, False),
        )
        self._enabled_scatter.setData(x=[], y=[])
        self._disabled_scatter.setData(x=[], y=[])
        # The previous document's X marks index the replaced coordinate arrays.
        self._disabled_x_scatter.setData(x=[], y=[])
        self._base_render_pending = True
        self._active_nets_dirty = False

        def append(batch_index: int, offset: int) -> None:
            if token != self._base_render_token:
                return
            if batch_index >= len(batches):
                self._base_render_pending = False
                if self._active_nets_dirty:
                    # An active NET change was coalesced while chunks were in
                    # flight; repaint every batch with the current colors.
                    self._active_nets_dirty = False
                    self._render_base_layers()
                else:
                    self._set_disabled_x_scatter(disabled_indices)
                self._render_selection_layer()
                self._render_companion_layer()
                return
            scatter, indices, enabled = batches[batch_index]
            chunk = indices[offset : offset + 1_000]
            if chunk.size:
                scatter.addPoints(**self._base_scatter_payload(chunk, enabled=enabled))
            next_offset = offset + len(chunk)
            if next_offset < len(indices):
                QTimer.singleShot(0, lambda: append(batch_index, next_offset))
            else:
                QTimer.singleShot(0, lambda: append(batch_index + 1, 0))

        QTimer.singleShot(0, lambda: append(0, 0))

    def _restyle_bump_layers(self) -> None:
        for key, scatter in self._bump_scatters_by_net.items():
            color = self._display_color_for_net(self._bump_net_names[key])
            scatter.setBrush(pg.mkBrush(color))
            scatter.setPen(pg.mkPen(QColor(color).darker(135), width=0.8))

    def _set_base_scatter(
        self,
        scatter: pg.ScatterPlotItem,
        indices: np.ndarray,
        *,
        enabled: bool,
    ) -> None:
        if indices.size == 0:
            scatter.setData(x=[], y=[])
            return
        scatter.setData(**self._base_scatter_payload(indices, enabled=enabled))

    def _base_scatter_payload(
        self, indices: np.ndarray, *, enabled: bool
    ) -> dict[str, Any]:
        colors = [
            self._display_color_for_net(self._records[index].current_net)
            for index in indices
        ]
        shared_disabled_brush = pg.mkBrush(self.DISABLED_FILL)
        styles: dict[int, tuple[Any, Any]] = {}
        for color in colors:
            key = int(color.rgba())
            if key not in styles:
                styles[key] = (
                    pg.mkBrush(color) if enabled else shared_disabled_brush,
                    pg.mkPen(
                        QColor(color).darker(130) if enabled else color,
                        width=1.0 if enabled else 1.8,
                    ),
                )
        brushes = [styles[int(color.rgba())][0] for color in colors]
        pens = [styles[int(color.rgba())][1] for color in colors]
        return {
            "x": self._x_values[indices],
            "y": self._y_values[indices],
            "data": [self._records[index].refdes for index in indices],
            "brush": brushes,
            "pen": pens,
            "size": self.POINT_SIZE_PX,
            "symbol": "o",
            "pxMode": True,
        }

    def _set_disabled_x_scatter(self, indices: np.ndarray) -> None:
        if indices.size == 0:
            self._disabled_x_scatter.setData(x=[], y=[])
            return
        self._disabled_x_scatter.setData(
            x=self._x_values[indices],
            y=self._y_values[indices],
            data=[self._records[index].refdes for index in indices],
            brush=pg.mkBrush(None),
            pen=pg.mkPen(self.DISABLED_X_COLOR, width=2.2),
            size=self.DISABLED_X_SIZE_PX,
            symbol="x",
            pxMode=True,
        )

    def _render_selection_layer(self) -> None:
        indices = np.asarray(
            [
                index
                for index, record in enumerate(self._records)
                if record.refdes.casefold() in self._selection_keys
            ],
            dtype=np.int64,
        )
        if indices.size == 0:
            self._selected_scatter.setData(x=[], y=[])
            return
        self._selected_scatter.setData(
            x=self._x_values[indices],
            y=self._y_values[indices],
            data=[self._records[index].refdes for index in indices],
            brush=pg.mkBrush(None),
            pen=pg.mkPen(self.SELECTED_COLOR, width=3.0),
            size=self.SELECTED_SIZE_PX,
            symbol="o",
            pxMode=True,
        )

    def _render_companion_layer(self) -> None:
        indices = np.asarray(
            [
                index
                for index, record in enumerate(self._records)
                if record.refdes.casefold() in self._companion_keys
            ],
            dtype=np.int64,
        )
        if indices.size == 0:
            self._companion_scatter.setData(x=[], y=[])
            return
        self._companion_scatter.setData(
            x=self._x_values[indices],
            y=self._y_values[indices],
            data=[self._records[index].refdes for index in indices],
            brush=pg.mkBrush(None),
            pen=pg.mkPen(self.REQUIRED_COMPANION_COLOR, width=2.4),
            size=self.SELECTED_SIZE_PX + 5.0,
            symbol="o",
            pxMode=True,
        )

    def _handle_left_click(self, position: QPointF, additive: bool) -> None:
        index = self._hit_index(position)
        if index is None:
            if not additive:
                self._set_selection_keys(set())
            return
        key = self._records[index].refdes.casefold()
        if additive:
            selection = set(self._selection_keys)
            if key in selection:
                selection.remove(key)
            else:
                selection.add(key)
        else:
            selection = {key}
        self._set_selection_keys(selection)

    def _handle_marquee(self, rectangle: QRectF, additive: bool) -> None:
        if not self._records:
            if not additive:
                self._set_selection_keys(set())
            return
        normalized = rectangle.normalized()
        mask = (
            (self._x_values >= normalized.left())
            & (self._x_values <= normalized.right())
            & (self._y_values >= normalized.top())
            & (self._y_values <= normalized.bottom())
        )
        matches = {
            self._records[index].refdes.casefold()
            for index in np.flatnonzero(mask)
        }
        self._set_selection_keys(
            self._selection_keys | matches if additive else matches
        )

    def _handle_right_click(self, position: QPointF, global_position: QPoint) -> None:
        index = self._hit_index(position)
        if index is None:
            return
        key = self._records[index].refdes.casefold()
        if key not in self._selection_keys:
            self._set_selection_keys({key})
        self.contextMenuRequested.emit(self.selected_refdes, global_position)

    def _set_selection_keys(self, keys: set[str]) -> None:
        valid = keys.intersection(self._refdes_index)
        if valid == self._selection_keys:
            return
        self._selection_keys = valid
        self._render_selection_layer()
        self.selectionChanged.emit(self.selected_refdes)

    def _hit_index(self, position: QPointF) -> int | None:
        if not self._records:
            return None
        pixel_x, pixel_y = self._view_box.viewPixelSize()
        pixel_x = self._safe_pixel_size(pixel_x, axis=0)
        pixel_y = self._safe_pixel_size(pixel_y, axis=1)
        radius_x = pixel_x * self.HIT_RADIUS_PX
        radius_y = pixel_y * self.HIT_RADIUS_PX
        distance = (
            ((self._x_values - position.x()) / radius_x) ** 2
            + ((self._y_values - position.y()) / radius_y) ** 2
        )
        index = int(np.argmin(distance))
        return index if distance[index] <= 1.0 else None

    def tooltip_text_at(self, position: QPointF) -> str | None:
        """Return decap details or, for a Device bump, only its NET name."""

        index = self._hit_index(position)
        if index is not None:
            return self._tooltip_text(self._records[index])
        bump_index = self._hit_bump_index(position)
        if bump_index is not None:
            return self._bumps[bump_index].net
        return None

    def _tooltip_text(self, record: _BoardDecap) -> str:
        lines = [
            f"PWR NET: {record.current_net or 'Unassigned'}",
            f"Component: {record.component}",
            f"REFDES: {record.refdes}",
            f"Footprint: {record.footprint}",
            f"State: {'Enabled' if record.enabled else 'Disabled'}",
        ]
        connection = self._connection_labels.get(record.refdes.casefold())
        if connection:
            lines.append(f"Pad/Via: {connection}")
        return "\n".join(lines)

    def _update_hover_tooltip(self, viewport_position: QPoint) -> None:
        position = self._viewport_to_view(viewport_position)
        decap_index = self._hit_index(position)
        bump_index = None if decap_index is not None else self._hit_bump_index(position)
        target = (
            ("decap", decap_index)
            if decap_index is not None
            else (("bump", bump_index) if bump_index is not None else None)
        )
        if target == self._hover_target:
            return
        self._hover_target = target
        if target is None:
            text = ""
        elif target[0] == "decap":
            text = self._tooltip_text(self._records[target[1]])
        else:
            text = self._bumps[target[1]].net
        self._view_box.setToolTip(text)

    def _clear_hover_tooltip(self) -> None:
        self._hover_target = None
        self._view_box.setToolTip("")

    def _hit_bump_index(self, position: QPointF) -> int | None:
        if not self._bumps:
            return None
        pixel_x, pixel_y = self._view_box.viewPixelSize()
        pixel_x = self._safe_pixel_size(pixel_x, axis=0)
        pixel_y = self._safe_pixel_size(pixel_y, axis=1)
        radius_x = pixel_x * self.BUMP_HIT_RADIUS_PX
        radius_y = pixel_y * self.BUMP_HIT_RADIUS_PX
        distance = (
            ((self._bump_x_values - position.x()) / radius_x) ** 2
            + ((self._bump_y_values - position.y()) / radius_y) ** 2
        )
        index = int(np.argmin(distance))
        return index if distance[index] <= 1.0 else None

    def _safe_pixel_size(self, value: object, *, axis: int) -> float:
        try:
            numeric = abs(float(value))
        except (TypeError, ValueError):
            numeric = 0.0
        if math.isfinite(numeric) and numeric > 0.0:
            return numeric
        ranges = self._view_box.viewRange()[axis]
        span = abs(float(ranges[1] - ranges[0]))
        viewport_extent = (
            self.viewport().width() if axis == 0 else self.viewport().height()
        )
        return max(span / max(viewport_extent, 1), np.finfo(np.float64).eps)

    def _first_matching_key(self, query: str) -> str | None:
        normalized = query.strip().casefold()
        if not normalized:
            return None
        if normalized in self._refdes_index:
            return normalized
        return next(
            (
                record.refdes.casefold()
                for record in self._records
                if normalized in record.refdes.casefold()
            ),
            None,
        )

    def _color_for_net(self, net: str) -> QColor:
        key = net.casefold()
        color = self._net_colors.get(key)
        if color is not None:
            return QColor(color)
        if not key:
            return QColor(self.FALLBACK_NET_COLOR)
        # Stable FNV-1a hashing avoids Python's per-process hash randomization.
        value = 2_166_136_261
        for byte in key.encode("utf-8"):
            value ^= byte
            value = (value * 16_777_619) & 0xFFFFFFFF
        return QColor.fromHsv(value % 360, 170, 225)

    def _display_color_for_net(self, net: str) -> QColor:
        if (
            self._active_net_keys is not None
            and net.casefold() not in self._active_net_keys
        ):
            return QColor(self.INACTIVE_NET_COLOR)
        return self._color_for_net(net)

    @classmethod
    def prepare_decaps(cls, records: Iterable[object]) -> tuple[_BoardDecap, ...]:
        """Pure record normalization suitable for a document worker."""

        return tuple(cls._normalize_record(record) for record in records)

    @classmethod
    def prepare_bumps(cls, records: Iterable[object]) -> tuple[_BoardBump, ...]:
        """Pure bump normalization suitable for a document worker."""

        return tuple(cls._normalize_bump(record) for record in records)

    @classmethod
    def _normalize_record(cls, record: object) -> _BoardDecap:
        refdes = str(cls._field(record, "refdes")).strip()
        if not refdes:
            raise ValueError("decap REFDES cannot be blank")
        try:
            x_value = cls._field(record, "x_um")
            y_value = cls._field(record, "y_um")
        except (AttributeError, KeyError):
            center = cls._field(record, "center")
            x_value = cls._field(center, "x_um")
            y_value = cls._field(center, "y_um")
        x_um = float(x_value)
        y_um = float(y_value)
        if not math.isfinite(x_um) or not math.isfinite(y_um):
            raise ValueError(f"decap {refdes!r} coordinates must be finite")
        current_net = str(cls._field(record, "current_net")).strip()
        enabled = bool(cls._field(record, "enabled"))
        model_id = str(cls._optional_field(record, "model_id") or "").strip()
        source_model_id = str(
            cls._optional_field(record, "source_model_id") or ""
        ).strip()
        footprint = str(cls._optional_field(record, "footprint") or "").strip()
        component = model_id or source_model_id or "Model not assigned"
        return _BoardDecap(
            refdes,
            x_um,
            y_um,
            current_net,
            enabled,
            component,
            footprint or "Unknown",
        )

    @classmethod
    def _normalize_bump(cls, record: object) -> _BoardBump:
        net = str(cls._field(record, "net")).strip()
        if not net:
            raise ValueError("bump NET cannot be blank")
        x_um = float(cls._field(record, "x_um"))
        y_um = float(cls._field(record, "y_um"))
        if not math.isfinite(x_um) or not math.isfinite(y_um):
            raise ValueError(f"bump on NET {net!r} has non-finite coordinates")
        return _BoardBump(net, x_um, y_um)

    @staticmethod
    def _field(record: object, name: str) -> object:
        if isinstance(record, Mapping):
            value = record.get(name, _MISSING)
        else:
            value = getattr(record, name, _MISSING)
        if value is _MISSING:
            raise AttributeError(f"decap record is missing {name!r}")
        return value

    @staticmethod
    def _optional_field(record: object, name: str) -> object | None:
        if isinstance(record, Mapping):
            return record.get(name)
        return getattr(record, name, None)

    @staticmethod
    def _normalize_net_colors(
        net_colors: Mapping[str, object],
    ) -> dict[str, QColor]:
        normalized: dict[str, QColor] = {}
        for net, raw_color in net_colors.items():
            key = str(net).strip().casefold()
            try:
                color = QColor(pg.mkColor(raw_color))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid color for net {net!r}: {raw_color!r}") from exc
            if not color.isValid():
                raise ValueError(f"invalid color for net {net!r}: {raw_color!r}")
            normalized[key] = color
        return normalized


BoardView = DecapBoardView

__all__ = ["BoardView", "DecapBoardView"]
