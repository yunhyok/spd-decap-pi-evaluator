from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsRectItem
import pyqtgraph as pg

from spd_decap_pi.gui.board_view import DecapBoardView


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _records() -> list[object]:
    return [
        {
            "refdes": "C1",
            "x_um": 0.0,
            "y_um": 0.0,
            "current_net": "VDD_A",
            "enabled": True,
            "model_id": "CAP_100NF_0402",
            "footprint": "0402",
        },
        {
            "refdes": "C2",
            "x_um": 100.0,
            "y_um": 0.0,
            "current_net": "vdd_b",
            "enabled": False,
            "source_model_id": "SOURCE_CAP_1UF",
            "footprint": "0201",
        },
        SimpleNamespace(
            refdes="C10",
            center=SimpleNamespace(x_um=200.0, y_um=100.0),
            current_net="VDD_A",
            enabled=True,
        ),
    ]


def _show(widget: DecapBoardView) -> QApplication:
    application = _application()
    widget.resize(640, 440)
    widget.show()
    application.processEvents()
    return application


def _viewport_position(widget: DecapBoardView, x_um: float, y_um: float) -> QPoint:
    scene_position = widget._view_box.mapViewToScene(QPointF(x_um, y_um))
    return widget.mapFromScene(scene_position)


def test_batched_layers_encode_enabled_disabled_and_selection_precedence() -> None:
    application = _application()
    board = DecapBoardView(
        _records(),
        {"vdd_a": "#ef5350", "VDD_B": "#42a5f5"},
    )
    try:
        _show(board)

        assert board.record_count == 3
        assert len(board._enabled_scatter.points()) == 2
        assert len(board._disabled_scatter.points()) == 1
        assert len(board._disabled_x_scatter.points()) == 1
        assert len(board._selected_scatter.points()) == 0

        enabled = {point.data(): point for point in board._enabled_scatter.points()}
        disabled = board._disabled_scatter.points()[0]
        assert enabled["C1"].brush().color().name() == "#ef5350"
        assert disabled.data() == "C2"
        assert disabled.brush().color().name() == board.DISABLED_FILL.name()
        assert disabled.pen().color().name() == "#42a5f5"
        disabled_x = board._disabled_x_scatter.points()[0]
        assert disabled_x.data() == "C2"
        assert disabled_x.symbol() == "x"
        assert disabled_x.pen().color().name() == board.DISABLED_X_COLOR.name()

        board.set_selected_refdes(["c2"])
        selected = board._selected_scatter.points()[0]
        assert selected.data() == "C2"
        assert selected.pen().color().name() == board.SELECTED_COLOR.name()
        assert selected.size() == board.SELECTED_SIZE_PX
    finally:
        board.close()
        application.processEvents()


def test_hover_text_reports_net_component_refdes_footprint_and_state() -> None:
    application = _application()
    board = DecapBoardView(_records())
    try:
        _show(board)

        enabled_text = board.tooltip_text_at(QPointF(0.0, 0.0))
        assert enabled_text is not None
        assert "PWR NET: VDD_A" in enabled_text
        assert "Component: CAP_100NF_0402" in enabled_text
        assert "REFDES: C1" in enabled_text
        assert "Footprint: 0402" in enabled_text
        assert "State: Enabled" in enabled_text

        disabled_text = board.tooltip_text_at(QPointF(100.0, 0.0))
        assert disabled_text is not None
        assert "PWR NET: vdd_b" in disabled_text
        assert "Component: SOURCE_CAP_1UF" in disabled_text
        assert "REFDES: C2" in disabled_text
        assert "State: Disabled" in disabled_text

        assert board.tooltip_text_at(QPointF(10_000.0, 10_000.0)) is None
        for scatter in (
            board._enabled_scatter,
            board._disabled_scatter,
            board._disabled_x_scatter,
            board._selected_scatter,
        ):
            assert scatter.opts["hoverable"] is False
            assert scatter.opts["tip"] is None

        QTest.mouseMove(board.viewport(), _viewport_position(board, 0.0, 0.0))
        application.processEvents()
        assert "PWR NET: VDD_A" in board._view_box.toolTip()
        assert "REFDES: C1" in board._view_box.toolTip()

        QTest.mouseMove(board.viewport(), _viewport_position(board, 100.0, 0.0))
        application.processEvents()
        assert "PWR NET: vdd_b" in board._view_box.toolTip()
        assert "REFDES: C2" in board._view_box.toolTip()

        board.clear_board()
        assert board._view_box.toolTip() == ""
        board.set_decaps(
            [
                {
                    "refdes": "C9",
                    "x_um": 50.0,
                    "y_um": 50.0,
                    "current_net": "NEW_NET",
                    "enabled": False,
                    "model_id": "CAP_NEW",
                    "footprint": "0402",
                }
            ]
        )
        assert board._view_box.toolTip() == ""
        QTest.mouseMove(board.viewport(), _viewport_position(board, 50.0, 50.0))
        application.processEvents()
        assert "PWR NET: NEW_NET" in board._view_box.toolTip()
        assert "REFDES: C9" in board._view_box.toolTip()

    finally:
        board.close()
        application.processEvents()


def test_case_insensitive_selection_search_centering_and_validation() -> None:
    application = _application()
    board = DecapBoardView(_records())
    changes: list[tuple[str, ...]] = []
    board.selectionChanged.connect(changes.append)
    try:
        _show(board)

        board.set_selected_refdes(["c2", "missing"])
        assert board.selected_refdes == ("C2",)
        assert changes[-1] == ("C2",)

        assert board.select_refdes("c", additive=False) == ("C1", "C2", "C10")
        assert board.select_refdes("C10", additive=False) == ("C10",)
        board.select_refdes(["c1"], additive=True)
        assert board.selected_refdes == ("C1", "C10")

        board._view_box.setRange(xRange=(-50.0, 50.0), yRange=(-40.0, 40.0))
        assert board.center_on_refdes("c10") is True
        x_range, y_range = board._view_box.viewRange()
        assert sum(x_range) / 2.0 == pytest.approx(200.0)
        assert sum(y_range) / 2.0 == pytest.approx(100.0)
        assert board.center_on_refdes("R404") is False

        with pytest.raises(ValueError, match="case-insensitive REFDES"):
            board.set_decaps(
                [
                    {
                        "refdes": "C1",
                        "x_um": 0,
                        "y_um": 0,
                        "current_net": "VDD",
                        "enabled": True,
                    },
                    {
                        "refdes": "c1",
                        "x_um": 1,
                        "y_um": 1,
                        "current_net": "VDD",
                        "enabled": True,
                    },
                ]
            )
    finally:
        board.close()
        application.processEvents()


def test_click_marquee_and_context_handlers_preserve_atomic_multiselection() -> None:
    application = _application()
    board = DecapBoardView(_records())
    context_requests: list[tuple[tuple[str, ...], QPoint]] = []
    board.contextMenuRequested.connect(
        lambda selected, point: context_requests.append((selected, point))
    )
    try:
        _show(board)

        board._handle_left_click(QPointF(0.0, 0.0), additive=False)
        board._handle_left_click(QPointF(100.0, 0.0), additive=True)
        assert board.selected_refdes == ("C1", "C2")

        board._handle_right_click(QPointF(100.0, 0.0), QPoint(10, 20))
        assert board.selected_refdes == ("C1", "C2")
        assert context_requests[-1] == (("C1", "C2"), QPoint(10, 20))

        board._handle_right_click(QPointF(200.0, 100.0), QPoint(30, 40))
        assert board.selected_refdes == ("C10",)
        assert context_requests[-1] == (("C10",), QPoint(30, 40))

        board._handle_marquee(QRectF(-1.0, -1.0, 102.0, 2.0), additive=False)
        assert board.selected_refdes == ("C1", "C2")
        board._handle_marquee(QRectF(199.0, 99.0, 2.0, 2.0), additive=True)
        assert board.selected_refdes == ("C1", "C2", "C10")

        board._handle_left_click(QPointF(10_000.0, 10_000.0), additive=False)
        assert board.selected_refdes == ()
    finally:
        board.close()
        application.processEvents()


def test_offscreen_mouse_click_ctrl_toggle_and_right_click_signal() -> None:
    application = _application()
    board = DecapBoardView(_records())
    _show(board)
    requests: list[tuple[str, ...]] = []
    board.contextMenuRequested.connect(lambda selected, _point: requests.append(selected))
    try:
        board.fit_board()
        application.processEvents()

        QTest.mouseClick(
            board.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_viewport_position(board, 0.0, 0.0),
        )
        QTest.mouseClick(
            board.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier,
            pos=_viewport_position(board, 100.0, 0.0),
        )
        application.processEvents()
        assert board.selected_refdes == ("C1", "C2")

        QTest.mouseClick(
            board.viewport(),
            Qt.MouseButton.RightButton,
            pos=_viewport_position(board, 100.0, 0.0),
        )
        application.processEvents()
        assert requests[-1] == ("C1", "C2")

        QTest.mouseClick(
            board.viewport(),
            Qt.MouseButton.LeftButton,
            pos=QPoint(4, 4),
        )
        application.processEvents()
        assert board.selected_refdes == ()
    finally:
        board.close()
        application.processEvents()


def test_offscreen_drag_pan_and_cursor_anchored_wheel_zoom() -> None:
    application = _application()
    board = DecapBoardView(_records())
    _show(board)
    try:
        board.fit_board()
        application.processEvents()

        drag_start = _viewport_position(board, -10.0, -10.0)
        drag_end = _viewport_position(board, 110.0, 10.0)
        QTest.mousePress(
            board.viewport(), Qt.MouseButton.LeftButton, pos=drag_start
        )
        QTest.mouseMove(board.viewport(), drag_end, delay=10)
        QTest.mouseRelease(
            board.viewport(), Qt.MouseButton.LeftButton, pos=drag_end
        )
        application.processEvents()
        assert board.selected_refdes == ("C1", "C2")

        x_before = board._view_box.viewRange()[0]
        pan_start = board.viewport().rect().center()
        pan_end = pan_start + QPoint(60, 0)
        QTest.mousePress(
            board.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier,
            pos=pan_start,
        )
        QTest.mouseMove(board.viewport(), pan_end, delay=10)
        QTest.mouseRelease(
            board.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier,
            pos=pan_end,
        )
        application.processEvents()
        x_after = board._view_box.viewRange()[0]
        assert sum(x_after) / 2.0 < sum(x_before) / 2.0

        local_anchor = QPointF(250.0, 180.0)
        global_anchor = board.viewport().mapToGlobal(local_anchor.toPoint())
        view_anchor_before = board._viewport_to_view(local_anchor.toPoint())
        width_before = x_after[1] - x_after[0]
        wheel_event = QWheelEvent(
            local_anchor,
            QPointF(global_anchor),
            QPoint(),
            QPoint(0, 120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        application.sendEvent(board.viewport(), wheel_event)
        application.processEvents()
        view_anchor_after = board._viewport_to_view(local_anchor.toPoint())
        width_after = (
            board._view_box.viewRange()[0][1] - board._view_box.viewRange()[0][0]
        )
        assert view_anchor_after.x() == pytest.approx(view_anchor_before.x())
        assert view_anchor_after.y() == pytest.approx(view_anchor_before.y())
        assert width_after < width_before
    finally:
        board.close()
        application.processEvents()


def test_plane_items_are_replaced_and_cleared_without_touching_decaps() -> None:
    application = _application()
    board = DecapBoardView(_records())
    first = QGraphicsRectItem(QRectF(-10.0, -10.0, 30.0, 30.0))
    second = QGraphicsRectItem(QRectF(50.0, 50.0, 30.0, 30.0))
    try:
        board.set_plane_items([first, second, first])
        assert board._plane_items == [first, second]
        assert first.zValue() == -100
        assert second.zValue() == -100
        assert len(board._enabled_scatter.points()) == 2

        board.clear_plane_items()
        assert board._plane_items == []
        assert len(board._enabled_scatter.points()) == 2
        assert len(board._disabled_scatter.points()) == 1
        assert len(board._disabled_x_scatter.points()) == 1

        board.set_plane_items([first])
        board.clear()
        assert board.record_count == 0
        assert board._plane_items == []
        assert board.selected_refdes == ()
        assert board._enabled_scatter in board.plotItem.items
        assert board._disabled_x_scatter in board.plotItem.items
    finally:
        board.close()
        application.processEvents()


def test_ten_thousand_decaps_use_four_fixed_scatter_graphics_items() -> None:
    application = _application()
    records = [
        {
            "refdes": f"C{index}",
            "x_um": float(index % 200),
            "y_um": float(index // 200),
            "current_net": f"VDD_{index % 4}",
            "enabled": index % 3 != 0,
        }
        for index in range(10_001)
    ]
    board = DecapBoardView(records)
    try:
        scatter_items = [
            item
            for item in board.plotItem.items
            if isinstance(item, pg.ScatterPlotItem)
        ]
        assert scatter_items == [
            board._enabled_scatter,
            board._disabled_scatter,
            board._disabled_x_scatter,
            board._selected_scatter,
        ]
        assert sum(len(item.points()) for item in scatter_items[:2]) == 10_001
        assert len(board._disabled_x_scatter.points()) == 3_334
        assert len(board._selected_scatter.points()) == 0

        board.set_selected_refdes(["c0", "C5000", "c10000"])
        assert board.selected_refdes == ("C0", "C5000", "C10000")
        assert len(board._selected_scatter.points()) == 3
        assert len(
            [
                item
                for item in board.plotItem.items
                if isinstance(item, pg.ScatterPlotItem)
            ]
        ) == 4
    finally:
        board.close()
        application.processEvents()


def test_invalid_record_coordinates_and_colors_fail_early() -> None:
    application = _application()
    with pytest.raises(ValueError, match="coordinates must be finite"):
        DecapBoardView(
            [
                {
                    "refdes": "C1",
                    "x_um": float("nan"),
                    "y_um": 0,
                    "current_net": "VDD",
                    "enabled": True,
                }
            ]
        )
    board = DecapBoardView()
    try:
        with pytest.raises(ValueError, match="invalid color"):
            board.set_net_colors({"VDD": object()})
    finally:
        board.close()
        application.processEvents()
