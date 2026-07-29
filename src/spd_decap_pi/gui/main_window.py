"""Desktop shell for read-only SPD decap scenario evaluation."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import traceback
from typing import Any, Callable

from PySide6.QtCore import QPoint, QPointF, QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCloseEvent,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from spd_decap_pi._core.services import (
    WorkspaceState,
    cap_spice_subcircuit_names,
    import_cap_spice,
    plane_cell_source_geometry,
)

from ..scenario import ScenarioDecap, ScenarioSpec
from ..scenario_io import (
    ScenarioBundle,
    load_scenario_with_recovery,
    save_scenario,
)
from ..spd_adapter import ScenarioImport, import_spd_scenario, verify_scenario_source
from ..version import APP_DISPLAY_NAME
from .board_view import DecapBoardView
from .comparison_plot import MultiRailComparisonPlot
from .worker import FunctionWorker


def _job_load_scenario(
    path: Path,
    source_override: Path | None = None,
    *,
    progress: Callable[[int, str], None],
    is_cancelled: Callable[[], bool],
) -> ScenarioBundle:
    progress(5, "Reading .spdpi scenario")
    bundle = load_scenario_with_recovery(path)
    progress(35, "Validating external SPD identity")
    resolved = verify_scenario_source(
        bundle.scenario,
        source_override,
        progress=lambda value, message: progress(35 + round(value * 0.65), message),
        is_cancelled=is_cancelled,
    )
    if str(resolved) != bundle.scenario.source.path:
        source = bundle.scenario.source.model_copy(update={"path": str(resolved)})
        scenario = ScenarioSpec.model_validate(
            {**bundle.scenario.model_dump(mode="json"), "source": source}
        )
        bundle = ScenarioBundle(
            scenario=scenario,
            attachments=bundle.attachments,
            recovered_from=bundle.recovered_from,
            recovery_reason=bundle.recovery_reason,
        )
    return bundle


def _job_save_scenario(
    scenario: ScenarioSpec,
    path: Path,
    attachments: dict[str, bytes],
    *,
    progress: Callable[[int, str], None],
    is_cancelled: Callable[[], bool],
) -> Path:
    if is_cancelled():
        raise RuntimeError("scenario save cancelled")
    progress(10, "Writing validated .spdpi bundle")
    result = save_scenario(scenario, path, attachments=attachments)
    progress(100, "Scenario saved")
    return result


class MainWindow(QMainWindow):
    """Interactive decap reassignment and evaluation window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("spdDecapMainWindow")
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1500, 900)

        self._scenario: ScenarioSpec | None = None
        self._attachments: dict[str, bytes] = {}
        self._scenario_path: Path | None = None
        self._dirty = False
        self._worker: FunctionWorker | None = None
        self._worker_cancelable = False
        self._worker_cancel_requested = False
        self._auto_save_after_worker = False
        self._evaluation_state: WorkspaceState | None = None
        self._last_evaluation: Any | None = None
        self._last_scenario_evaluation: Any | None = None
        self._tuned_evaluations_by_rail: dict[str, Any] = {}
        self._comparison_batch: Any | None = None

        self._build_actions()
        self._build_ui()
        self._set_loaded_state(False)

    @property
    def scenario(self) -> ScenarioSpec | None:
        return self._scenario

    @property
    def board_view(self) -> DecapBoardView:
        return self.board

    def _build_actions(self) -> None:
        self.open_spd_action = QAction("Open SPD...", self)
        self.open_spd_action.setObjectName("openSpdAction")
        self.open_spd_action.triggered.connect(self.open_spd)
        self.open_scenario_action = QAction("Open Scenario...", self)
        self.open_scenario_action.triggered.connect(self.open_scenario)
        self.save_action = QAction("Save Scenario", self)
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.triggered.connect(self.save_scenario)
        self.save_as_action = QAction("Save Scenario As...", self)
        self.save_as_action.triggered.connect(lambda: self.save_scenario(save_as=True))
        self.add_model_action = QAction("Add Decap Model...", self)
        self.add_model_action.triggered.connect(self.add_decap_model)

    def _build_ui(self) -> None:
        toolbar = QToolBar("Scenario", self)
        toolbar.setObjectName("scenarioToolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for action in (
            self.open_spd_action,
            self.open_scenario_action,
            self.save_action,
            self.save_as_action,
            self.add_model_action,
        ):
            toolbar.addAction(action)

        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)

        identity_row = QHBoxLayout()
        identity = QLabel(APP_DISPLAY_NAME)
        identity.setObjectName("appIdentityLabel")
        identity.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.source_label = QLabel("Open a PowerSI .spd or .spdpi scenario")
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        identity_row.addWidget(identity)
        identity_row.addStretch(1)
        identity_row.addWidget(self.source_label)
        root_layout.addLayout(identity_row)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search / select"))
        self.search_mode = QComboBox()
        self.search_mode.addItems(("REFDES", "PWR NET"))
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("scenarioSearchEdit")
        self.search_edit.setPlaceholderText("REFDES or PWR NET substring")
        self.search_edit.returnPressed.connect(self.apply_search)
        self.search_button = QPushButton("Select")
        self.search_button.clicked.connect(self.apply_search)
        self.fit_button = QPushButton("Fit board")
        self.fit_button.clicked.connect(self._fit_board)
        search_row.addWidget(self.search_mode)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.search_button)
        search_row.addWidget(self.fit_button)
        root_layout.addLayout(search_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.board = DecapBoardView()
        self.board.selectionChanged.connect(self._selection_changed)
        self.board.contextMenuRequested.connect(self._show_decap_context_menu)
        splitter.addWidget(self.board)

        side_tabs = QTabWidget()
        side_tabs.setMinimumWidth(600)
        side_tabs.addTab(self._build_selection_tab(), "Selection")
        side_tabs.addTab(self._build_evaluation_tab(), "Evaluation")
        side_tabs.addTab(self._build_ai_tab(), "AI Assist")
        splitter.addWidget(side_tabs)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

        status = QStatusBar(self)
        self.setStatusBar(status)
        self.status_text = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(230)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_worker)
        self.progress_bar.hide()
        self.cancel_button.hide()
        status.addWidget(self.status_text, 1)
        status.addPermanentWidget(self.progress_bar)
        status.addPermanentWidget(self.cancel_button)

    def _build_selection_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.selection_summary = QLabel("No decap selected")
        self.selection_summary.setWordWrap(True)
        layout.addWidget(self.selection_summary)
        self.selection_table = QTableWidget(0, 6)
        self.selection_table.setHorizontalHeaderLabels(
            ("REFDES", "State", "PWR NET", "Model", "Footprint", "Eligible PWR")
        )
        self.selection_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.selection_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.selection_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.selection_table, 1)

        hint = QLabel(
            "Left click selects; drag selects many; right click changes PWR/model or "
            "enabled state. Hover a decap for its PWR NET, component, REFDES and state; "
            "a red X marks disabled decaps. Shift+drag pans and the wheel zooms. "
            "Assignment eligibility uses exact SPD copper at the physical PWR pad; plane "
            "fills are read-only PowerSI artwork and dashed rectangles mark the solver "
            "bounding-box approximation."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa4b2;")
        layout.addWidget(hint)

        colors_group = QGroupBox("PWR NET colors")
        colors_layout = QVBoxLayout(colors_group)
        self.color_list = QListWidget()
        self.color_list.itemDoubleClicked.connect(self._choose_net_color)
        colors_layout.addWidget(self.color_list)
        colors_layout.addWidget(QLabel("Double-click a net to change its color."))
        layout.addWidget(colors_group)
        return page

    def _build_evaluation_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.rail_list = QListWidget()
        self.rail_list.setObjectName("evaluationRailList")
        self.rail_list.setMaximumHeight(145)
        self.rail_list.setIconSize(QSize(12, 12))
        rail_buttons = QHBoxLayout()
        self.select_all_rails_button = QPushButton("Select all")
        self.select_all_rails_button.clicked.connect(
            lambda: self._set_all_rails_checked(True)
        )
        self.clear_rails_button = QPushButton("Clear")
        self.clear_rails_button.clicked.connect(
            lambda: self._set_all_rails_checked(False)
        )
        rail_buttons.addWidget(self.select_all_rails_button)
        rail_buttons.addWidget(self.clear_rails_button)
        rail_buttons.addStretch(1)
        rail_picker = QWidget()
        rail_picker_layout = QVBoxLayout(rail_picker)
        rail_picker_layout.setContentsMargins(0, 0, 0, 0)
        rail_picker_layout.addWidget(self.rail_list)
        rail_picker_layout.addLayout(rail_buttons)
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("Optional target, e.g. 0.02")
        self.target_edit.textEdited.connect(self._target_input_changed)
        form.addRow("PWR NETs", rail_picker)
        form.addRow("Common target impedance (ohm)", self.target_edit)
        layout.addLayout(form)
        self.evaluate_button = QPushButton("Run Original + Tuned evaluation")
        self.evaluate_button.setObjectName("evaluateScenarioButton")
        self.evaluate_button.clicked.connect(self.run_evaluation)
        layout.addWidget(self.evaluate_button)
        self.plot = MultiRailComparisonPlot()
        self.plot.setMinimumHeight(260)
        self.comparison_table = QTableWidget(0, 7)
        self.comparison_table.setObjectName("evaluationComparisonTable")
        self.comparison_table.setHorizontalHeaderLabels(
            (
                "PWR NET",
                "Caps Original→Tuned",
                "Peak Original",
                "Peak Tuned",
                "Δ Peak",
                "Max violation Original→Tuned",
                "Baseline",
            )
        )
        self.comparison_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.comparison_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.evaluation_summary = QTextBrowser()
        result_details = QTabWidget()
        result_details.setObjectName("evaluationResultDetails")
        result_details.addTab(self.comparison_table, "Comparison table")
        result_details.addTab(self.evaluation_summary, "Summary")
        result_splitter = QSplitter(Qt.Orientation.Vertical)
        result_splitter.setObjectName("evaluationResultSplitter")
        result_splitter.addWidget(self.plot)
        result_splitter.addWidget(result_details)
        result_splitter.setStretchFactor(0, 3)
        result_splitter.setStretchFactor(1, 2)
        result_splitter.setSizes((360, 220))
        layout.addWidget(result_splitter, 1)
        limitation = QLabel(
            "Each checked PWR NET is solved sequentially. Original results are cached "
            "inside the scenario and compared with the current Tuned state; phase is not "
            "plotted. The shared plot starts with every result visible; Plot Channels and "
            "X/Y markers only change the display. Evaluation reuses the existing modal PI "
            "engine. Non-rectangular PWR "
            "artwork is solved with its disclosed rectangular bbox; DGND is continuous; "
            "results are single-rail Zii without inter-rail coupling."
        )
        limitation.setWordWrap(True)
        limitation.setStyleSheet("color: #d6a64f;")
        layout.addWidget(limitation)
        return page

    def _build_ai_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel(
            "Evidence-grounded Plot Analyst only. AI receives solver-derived features "
            "and cannot change PWR assignments, enable decaps, or run optimization."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.ai_endpoint = QLineEdit("http://127.0.0.1:11434")
        self.ai_model = QLineEdit()
        self.ai_model.setPlaceholderText("Blank = deterministic evidence report")
        self.ai_allow_remote = QCheckBox("Allow LAN/remote endpoint for this session")
        self.ai_rail_combo = QComboBox()
        self.ai_rail_combo.setObjectName("aiEvaluationRailCombo")
        self.ai_rail_combo.currentIndexChanged.connect(self._ai_rail_changed)
        form.addRow("Tuned result rail", self.ai_rail_combo)
        form.addRow("Local endpoint", self.ai_endpoint)
        form.addRow("Model", self.ai_model)
        form.addRow("", self.ai_allow_remote)
        layout.addLayout(form)
        self.ai_button = QPushButton("Analyze latest plot")
        self.ai_button.setObjectName("aiPlotAnalystButton")
        self.ai_button.clicked.connect(self.run_ai_assist)
        layout.addWidget(self.ai_button)
        self.ai_output = QTextBrowser()
        layout.addWidget(self.ai_output, 1)
        return page

    def _set_loaded_state(self, loaded: bool) -> None:
        for widget in (
            self.search_mode,
            self.search_edit,
            self.search_button,
            self.fit_button,
            self.evaluate_button,
            self.rail_list,
            self.select_all_rails_button,
            self.clear_rails_button,
            self.target_edit,
            self.add_model_action,
            self.save_action,
            self.save_as_action,
        ):
            widget.setEnabled(loaded)
        self.ai_rail_combo.setEnabled(loaded and bool(self._tuned_evaluations_by_rail))
        self.ai_button.setEnabled(loaded and self._last_evaluation is not None)

    def _set_busy(self, busy: bool) -> None:
        for action in (
            self.open_spd_action,
            self.open_scenario_action,
            self.save_action,
            self.save_as_action,
            self.add_model_action,
        ):
            action.setEnabled(not busy and (self._scenario is not None or action in (self.open_spd_action, self.open_scenario_action)))
        for widget in (
            self.board,
            self.search_mode,
            self.search_edit,
            self.search_button,
            self.fit_button,
            self.color_list,
            self.evaluate_button,
            self.rail_list,
            self.select_all_rails_button,
            self.clear_rails_button,
            self.target_edit,
        ):
            widget.setEnabled(not busy and self._scenario is not None)
        self.ai_button.setEnabled(
            not busy
            and self._scenario is not None
            and self._last_evaluation is not None
        )
        self.ai_rail_combo.setEnabled(
            not busy
            and self._scenario is not None
            and bool(self._tuned_evaluations_by_rail)
        )

    def _invalidate_evaluation(self, reason: str = "Scenario changed; run evaluation again.") -> None:
        self._evaluation_state = None
        self._last_evaluation = None
        self._last_scenario_evaluation = None
        self._tuned_evaluations_by_rail.clear()
        self._comparison_batch = None
        self.ai_rail_combo.clear()
        self.ai_rail_combo.setEnabled(False)
        self.plot.clear_comparisons()
        self.comparison_table.setRowCount(0)
        if self._scenario is not None:
            available_models = {
                item.model_id.casefold()
                for item in self._scenario.base_project.cap_models
            }
            unmodeled = sum(
                item.enabled
                and (
                    item.model_id is None
                    or item.model_id.casefold() not in available_models
                )
                for item in self._scenario.decaps
            )
            if unmodeled:
                reason += (
                    f"\nMounted enabled decaps without assigned models: {unmodeled:,}. "
                    "Assign models before evaluating their PWR rail."
                )
        self.evaluation_summary.setPlainText(reason)
        self.ai_output.clear()
        self.ai_button.setEnabled(False)

    def _run_worker(
        self,
        worker: FunctionWorker,
        on_result: Callable[[Any], None],
        *,
        label: str,
        on_error: Callable[[str], None] | None = None,
        cancelable: bool = True,
    ) -> None:
        if self._worker is not None:
            QMessageBox.information(self, APP_DISPLAY_NAME, "Another operation is running.")
            return
        self._worker = worker
        self._worker_cancelable = cancelable
        self._worker_cancel_requested = False
        self._set_busy(True)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.cancel_button.setVisible(cancelable)
        self.status_text.setText(label)
        worker.signals.progress.connect(self._worker_progress)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(
            lambda details: self._worker_failed(details, on_error or self._worker_error)
        )
        worker.signals.finished.connect(self._worker_finished)
        QThreadPool.globalInstance().start(worker)

    def _worker_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(value)
        self.status_text.setText(message)

    def _worker_error(self, details: str) -> None:
        final_line = next(
            (line for line in reversed(details.strip().splitlines()) if line.strip()),
            "Operation failed",
        )
        box = QMessageBox(QMessageBox.Icon.Critical, APP_DISPLAY_NAME, final_line, parent=self)
        box.setDetailedText(details)
        box.exec()

    def _worker_failed(
        self, details: str, handler: Callable[[str], None]
    ) -> None:
        if self._worker_cancel_requested:
            self.status_text.setText("Operation cancelled")
            return
        handler(details)

    def _worker_finished(self) -> None:
        cancelled = self._worker_cancel_requested
        auto_save = self._auto_save_after_worker and not cancelled
        self._auto_save_after_worker = False
        self._worker = None
        self._worker_cancelable = False
        self._worker_cancel_requested = False
        self.progress_bar.hide()
        self.cancel_button.hide()
        self._set_busy(False)
        if cancelled:
            self.status_text.setText("Operation cancelled")
        elif self.status_text.text().startswith(
            ("Opening", "Saving", "Evaluating", "Analyzing")
        ):
            self.status_text.setText("Ready")
        if auto_save and self._scenario is not None and self._scenario_path is not None:
            QTimer.singleShot(0, self._auto_save_scenario)

    def _cancel_worker(self) -> None:
        if self._worker is not None and self._worker_cancelable:
            self._worker_cancel_requested = True
            self._worker.cancel()
            self.status_text.setText("Cancelling...")

    def open_spd(self) -> None:
        if not self._can_replace_document():
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open PowerSI SPD", "", "PowerSI SPD (*.spd);;All files (*)"
        )
        if not filename:
            return
        worker = FunctionWorker(import_spd_scenario, Path(filename))
        self._run_worker(worker, self._accept_spd_import, label="Opening SPD...")

    def _accept_spd_import(self, imported: ScenarioImport) -> None:
        self._scenario = imported.scenario
        self._attachments = imported.attachments
        self._scenario_path = None
        self._dirty = False
        self._invalidate_evaluation("Run an evaluation to generate a PI plot.")
        self._refresh_all()
        self.board.fit_board()
        warnings = sum(
            str(getattr(item, "severity", "")).casefold() == "warning"
            for item in imported.diagnostics
        )
        self.status_text.setText(
            f"Loaded {len(imported.scenario.decaps):,} top-side decaps ({warnings} warning(s))"
        )

    def open_scenario(self) -> None:
        if not self._can_replace_document():
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open SPD Decap PI Scenario", "", "SPD PI Scenario (*.spdpi)"
        )
        if not filename:
            return
        self._start_scenario_load(Path(filename))

    def _start_scenario_load(
        self, path: Path, source_override: Path | None = None
    ) -> None:
        worker = FunctionWorker(_job_load_scenario, path, source_override)
        self._run_worker(
            worker,
            lambda bundle: self._accept_scenario_bundle(
                path, bundle, relinked=source_override is not None
            ),
            label="Opening scenario...",
            on_error=lambda details: self._scenario_load_error(path, details),
            cancelable=False,
        )

    def _scenario_load_error(self, path: Path, details: str) -> None:
        relocatable = any(
            marker in details
            for marker in (
                "FileNotFoundError",
                "SPD source size mismatch",
                "SPD source SHA-256 does not match",
            )
        )
        if not relocatable:
            self._worker_error(details)
            return
        choice = QMessageBox.question(
            self,
            APP_DISPLAY_NAME,
            "The external SPD is missing or no longer matches. Locate the exact "
            "SPD used by this scenario?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice != QMessageBox.StandardButton.Yes:
            self._worker_error(details)
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Locate matching PowerSI SPD", "", "PowerSI SPD (*.spd)"
        )
        if filename:
            QTimer.singleShot(
                25,
                lambda: self._start_scenario_load(path, Path(filename)),
            )

    def _accept_scenario_bundle(
        self, path: Path, bundle: ScenarioBundle, *, relinked: bool = False
    ) -> None:
        self._scenario = bundle.scenario
        self._attachments = bundle.attachments
        self._scenario_path = path
        self._dirty = bundle.recovered_from is not None or relinked
        self._invalidate_evaluation("Run an evaluation to generate a PI plot.")
        self._refresh_all()
        self.board.fit_board()
        message = f"Opened {path.name}"
        if bundle.recovered_from is not None:
            message += f" (recovered from {bundle.recovered_from.name}; save required)"
        elif relinked:
            message += " (external SPD relinked; save required)"
        self.status_text.setText(message)

    def _request_scenario_save_path(self) -> Path | None:
        assert self._scenario is not None
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save SPD Decap PI Scenario",
            f"{Path(self._scenario.source.name).stem}.spdpi",
            "SPD PI Scenario (*.spdpi)",
        )
        if not filename:
            return None
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".spdpi")
        if path.suffix.casefold() != ".spdpi":
            QMessageBox.warning(
                self, APP_DISPLAY_NAME, "Scenario file extension must be .spdpi."
            )
            return None
        return path

    def save_scenario(
        self,
        *,
        save_as: bool = False,
        _on_error: Callable[[str], None] | None = None,
    ) -> bool:
        if self._scenario is None:
            return False
        path = None if save_as else self._scenario_path
        if path is None:
            path = self._request_scenario_save_path()
            if path is None:
                return False
        saved_scenario = self._scenario
        saved_fingerprint = saved_scenario.design_fingerprint
        saved_revision = saved_scenario.revision
        worker = FunctionWorker(
            _job_save_scenario,
            saved_scenario,
            path,
            dict(self._attachments),
        )
        self._run_worker(
            worker,
            lambda saved_path: self._scenario_saved(
                saved_path, saved_fingerprint, saved_revision
            ),
            label="Saving scenario...",
            on_error=_on_error,
            cancelable=False,
        )
        return True

    def _auto_save_scenario(self) -> None:
        def failed(details: str) -> None:
            self._dirty = True
            path = self._scenario_path
            name = path.name if path is not None else ".spdpi scenario"
            message = (
                f"Automatic save failed for {name}. Original results remain in "
                "memory; use Save Scenario to retry."
            )
            summary = self.evaluation_summary.toPlainText()
            if message not in summary:
                self.evaluation_summary.setPlainText(
                    f"{summary}\n{message}".strip()
                )
            self._worker_error(details)
            self.status_text.setText("Automatic Original-result save failed")

        self.save_scenario(_on_error=failed)

    def _scenario_saved(
        self, path: Path, saved_fingerprint: str, saved_revision: int
    ) -> None:
        self._scenario_path = path
        current = self._scenario
        if (
            current is not None
            and current.design_fingerprint == saved_fingerprint
            and current.revision == saved_revision
        ):
            self._dirty = False
            summary = self.evaluation_summary.toPlainText()
            if "will be saved automatically" in summary:
                self.evaluation_summary.setPlainText(
                    summary.replace(
                        f"will be saved automatically to {path.name}",
                        f"are saved in {path.name}",
                    )
                )
            self.status_text.setText(f"Saved {path.name}")
            return
        self._dirty = True
        self.status_text.setText(
            f"Saved an earlier snapshot to {path.name}; current changes remain unsaved"
        )

    def _can_replace_document(self) -> bool:
        if not self._dirty:
            return True
        choice = QMessageBox.question(
            self,
            APP_DISPLAY_NAME,
            "The current scenario has unsaved changes. Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return choice == QMessageBox.StandardButton.Discard

    def _refresh_all(self) -> None:
        scenario = self._scenario
        if scenario is None:
            self.board.clear_board()
            self._set_loaded_state(False)
            return
        self.source_label.setText(
            f"{scenario.source.name} | {scenario.source.size_bytes / (1024**2):,.1f} MiB | "
            f"SHA-256 {scenario.source.sha256[:12]}... | rev {scenario.revision}"
        )
        self.board.set_decaps(scenario.decaps, scenario.net_colors)
        self.board.set_selected_refdes(scenario.selected_refdes)
        self._refresh_plane_preview()
        self._refresh_colors()
        self._refresh_rails()
        self._refresh_selection_table()
        if self._worker is None:
            self._set_loaded_state(True)
        else:
            self._set_busy(True)

    def _refresh_plane_preview(self) -> None:
        assert self._scenario is not None
        project = self._scenario.base_project
        rail_by_domain = {item.domain: item for item in project.rails}
        items: list[QGraphicsItem] = []
        for partition in project.partitions:
            domain_by_cell = {
                cell_id: domain for domain, cell_id in partition.domain_to_cell.items()
            }
            for cell in partition.cells:
                domain = domain_by_cell.get(cell.cell_id, cell.cell_id)
                rail = rail_by_domain.get(domain)
                color = QColor(
                    self._scenario.net_colors.get(
                        rail.net if rail is not None else domain, "#5D6D7E"
                    )
                )
                fill = QColor(color)
                fill.setAlpha(24)
                source_geometry = plane_cell_source_geometry(
                    cell,
                    self._attachments,
                    expected_layer=partition.layer,
                )
                source_items = self._source_plane_items(source_geometry, color)
                if source_items:
                    items.extend(source_items)
                rectangle = QGraphicsRectItem(
                    cell.x_min_um,
                    cell.y_min_um,
                    cell.x_max_um - cell.x_min_um,
                    cell.y_max_um - cell.y_min_um,
                )
                pen = QPen(color)
                pen.setCosmetic(True)
                if source_items or cell.solver_geometry == "spd_bounding_box":
                    pen.setStyle(Qt.PenStyle.DashLine)
                rectangle.setPen(pen)
                rectangle.setBrush(QBrush() if source_items else QBrush(fill))
                rectangle.setData(0, "solver_bounds" if source_items else "plane_cell")
                rectangle.setZValue(-30.0)
                items.append(rectangle)
        if not items:
            outline = project.outline
            rectangle = QGraphicsRectItem(
                outline.origin_x_um,
                outline.origin_y_um,
                outline.width_um,
                outline.height_um,
            )
            pen = QPen(QColor("#728197"))
            pen.setCosmetic(True)
            rectangle.setPen(pen)
            rectangle.setData(0, "board_outline")
            rectangle.setZValue(-30.0)
            items.append(rectangle)
        self.board.set_plane_items(items)

    @staticmethod
    def _source_plane_items(
        geometry: dict[str, Any] | Any,
        color: QColor,
    ) -> list[QGraphicsItem]:
        """Render normalized PowerSI add/subtract artwork in source order.

        Positive primitives use the rail color. Negative primitives paint the
        board background back in, so voids remain immediately visible while
        the dashed solver rectangle stays available as a disclosed numerical
        approximation.
        """

        polygons = {
            "positive_polygon": geometry["positive_polygons_um"],
            "negative_polygon": geometry["negative_polygons_um"],
        }
        circles = {
            "positive_circle": geometry["positive_circles_um"],
            "negative_circle": geometry["negative_circles_um"],
        }
        order = list(geometry["primitive_order"])
        if not order:
            order = [
                *(('positive_polygon', index) for index in range(len(polygons['positive_polygon']))),
                *(('positive_circle', index) for index in range(len(circles['positive_circle']))),
                *(('negative_polygon', index) for index in range(len(polygons['negative_polygon']))),
                *(('negative_circle', index) for index in range(len(circles['negative_circle']))),
            ]
        if not order:
            return []

        positive_fill = QColor(color)
        positive_fill.setAlpha(46)
        board_fill = QColor("#171a1f")
        result: list[QGraphicsItem] = []
        for sequence, (kind, index) in enumerate(order):
            negative = kind.startswith("negative_")
            if kind in polygons:
                values = polygons[kind]
                if index < 0 or index >= len(values):
                    continue
                item: QGraphicsItem = QGraphicsPolygonItem(
                    QPolygonF([QPointF(float(x), float(y)) for x, y in values[index]])
                )
            elif kind in circles:
                values = circles[kind]
                if index < 0 or index >= len(values):
                    continue
                center_x, center_y, radius = values[index]
                item = QGraphicsEllipseItem(
                    float(center_x - radius),
                    float(center_y - radius),
                    float(2.0 * radius),
                    float(2.0 * radius),
                )
            else:
                continue

            pen = QPen(color)
            pen.setCosmetic(True)
            if negative:
                pen.setStyle(Qt.PenStyle.DashLine)
            item.setPen(pen)  # type: ignore[attr-defined]
            item.setBrush(QBrush(board_fill if negative else positive_fill))  # type: ignore[attr-defined]
            item.setData(0, kind)
            item.setZValue(-20.0 + sequence * 0.001)
            result.append(item)
        return result

    def _refresh_colors(self) -> None:
        assert self._scenario is not None
        self.color_list.clear()
        for net, color in sorted(self._scenario.net_colors.items(), key=lambda item: item[0].casefold()):
            item = QListWidgetItem(net)
            item.setData(Qt.ItemDataRole.UserRole, net)
            item.setBackground(QBrush(QColor(color)))
            item.setForeground(QBrush(QColor("#ffffff")))
            self.color_list.addItem(item)

    def _refresh_rails(self) -> None:
        assert self._scenario is not None
        had_items = self.rail_list.count() > 0
        checked = {
            str(self.rail_list.item(index).data(Qt.ItemDataRole.UserRole)).casefold()
            for index in range(self.rail_list.count())
            if self.rail_list.item(index).checkState() == Qt.CheckState.Checked
        }
        selected = {
            str(self.rail_list.item(index).data(Qt.ItemDataRole.UserRole)).casefold()
            for index in range(self.rail_list.count())
            if self.rail_list.item(index).isSelected()
        }
        current_item = self.rail_list.currentItem()
        current_rail_id = (
            str(current_item.data(Qt.ItemDataRole.UserRole)).casefold()
            if current_item is not None
            else None
        )
        self.rail_list.clear()
        restored_current_item: QListWidgetItem | None = None
        for rail in self._scenario.base_project.rails:
            item = QListWidgetItem(f"{rail.net} ({rail.rail_id})")
            item.setData(Qt.ItemDataRole.UserRole, rail.rail_id)
            item.setIcon(self._net_color_swatch(rail.net))
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(
                Qt.CheckState.Checked
                if rail.rail_id.casefold() in checked
                else Qt.CheckState.Unchecked
            )
            self.rail_list.addItem(item)
            if rail.rail_id.casefold() in selected:
                item.setSelected(True)
            if rail.rail_id.casefold() == current_rail_id:
                restored_current_item = item
        if restored_current_item is not None:
            self.rail_list.setCurrentItem(restored_current_item)
        if not had_items and self.rail_list.count():
            self.rail_list.item(0).setCheckState(Qt.CheckState.Checked)

    def _net_color_swatch(self, net: str) -> QIcon:
        """Create a bordered box using the board's case-insensitive net color."""

        size = self.rail_list.iconSize()
        swatch = QPixmap(size)
        swatch.fill(Qt.GlobalColor.transparent)
        painter = QPainter(swatch)
        painter.setPen(QPen(QColor("#667085"), 1))
        painter.setBrush(QBrush(self.board.color_for_net(net)))
        painter.drawRect(0, 0, max(size.width() - 1, 0), max(size.height() - 1, 0))
        painter.end()
        return QIcon(swatch)

    def _checked_rail_ids(self) -> tuple[str, ...]:
        return tuple(
            str(self.rail_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.rail_list.count())
            if self.rail_list.item(index).checkState() == Qt.CheckState.Checked
        )

    def _set_all_rails_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.rail_list.count()):
            self.rail_list.item(index).setCheckState(state)

    def _target_input_changed(self, _text: str) -> None:
        if self._comparison_batch is not None:
            self._invalidate_evaluation(
                "Target impedance changed; run Original + Tuned evaluation again."
            )
            self.status_text.setText("Target changed; evaluation required")

    def _selection_changed(self, selected: tuple[str, ...]) -> None:
        if self._scenario is None:
            return
        if [item.casefold() for item in self._scenario.selected_refdes] != [
            item.casefold() for item in selected
        ]:
            self._scenario = ScenarioSpec.model_validate(
                {
                    **self._scenario.model_dump(mode="json"),
                    "selected_refdes": list(selected),
                }
            )
        self._refresh_selection_table()

    def _selected_decaps(self) -> list[ScenarioDecap]:
        if self._scenario is None:
            return []
        keys = {item.casefold() for item in self.board.selected_refdes}
        return [item for item in self._scenario.decaps if item.refdes.casefold() in keys]

    def _refresh_selection_table(self) -> None:
        selected = self._selected_decaps()
        self.selection_summary.setText(
            "No decap selected"
            if not selected
            else f"{len(selected):,} selected | {sum(item.enabled for item in selected):,} enabled"
        )
        self.selection_table.setRowCount(len(selected))
        for row, decap in enumerate(selected):
            eligible = ", ".join(
                item.net for item in decap.eligibility.values() if item.allowed
            ) or "None"
            values = (
                decap.refdes,
                "Enabled" if decap.enabled else "Disabled",
                decap.current_net,
                decap.model_id or "Unassigned",
                decap.footprint,
                eligible,
            )
            for column, value in enumerate(values):
                self.selection_table.setItem(row, column, QTableWidgetItem(value))

    def apply_search(self) -> None:
        if self._scenario is None:
            return
        query = self.search_edit.text().strip().casefold()
        if not query:
            self.board.set_selected_refdes(())
            return
        if self.search_mode.currentText() == "REFDES":
            selected = self.board.select_refdes(query)
        else:
            matches = [
                item.refdes
                for item in self._scenario.decaps
                if query in item.current_net.casefold()
            ]
            self.board.set_selected_refdes(matches)
            selected = self.board.selected_refdes
        if len(selected) == 1:
            self.board.center_on_refdes(selected[0])
        self.status_text.setText(f"Selected {len(selected):,} matching decap(s)")

    def _fit_board(self) -> None:
        self.board.fit_board()

    def _show_decap_context_menu(
        self, selected_refdes: tuple[str, ...], global_position: QPoint
    ) -> None:
        if self._scenario is None or not selected_refdes:
            return
        selected = self._selected_decaps()
        menu = QMenu(self)
        enable = menu.addAction(
            "Disable selected" if any(item.enabled for item in selected) else "Enable selected"
        )
        enable.triggered.connect(
            lambda: self._set_selected_enabled(not any(item.enabled for item in selected))
        )

        pwr_menu = menu.addMenu("Assign PWR NET")
        common_rail_ids = {
            rail_id.casefold()
            for rail_id, eligibility in selected[0].eligibility.items()
            if eligibility.allowed
        }
        for decap in selected[1:]:
            common_rail_ids.intersection_update(
                rail_id.casefold()
                for rail_id, eligibility in decap.eligibility.items()
                if eligibility.allowed
            )
        for rail in self._scenario.base_project.rails:
            if rail.rail_id.casefold() not in common_rail_ids:
                continue
            action = pwr_menu.addAction(f"{rail.net} ({rail.pwr_layer} / {rail.gnd_layer})")
            action.triggered.connect(
                lambda _checked=False, rail_id=rail.rail_id: self._assign_selected_rail(rail_id)
            )
        if not common_rail_ids:
            unavailable = pwr_menu.addAction("No common exact-under-pad PWR plane")
            unavailable.setEnabled(False)

        model_menu = menu.addMenu("Assign decap model")
        footprints = {item.footprint.casefold() for item in selected}
        for model in self._scenario.base_project.cap_models:
            action = model_menu.addAction(f"{model.model_id} [{model.footprint}]")
            action.setEnabled(
                len(footprints) == 1 and model.footprint.casefold() in footprints
            )
            action.triggered.connect(
                lambda _checked=False, model_id=model.model_id: self._assign_selected_model(model_id)
            )
        model_menu.addSeparator()
        clear_model = model_menu.addAction("Unassign model")
        clear_model.triggered.connect(lambda: self._assign_selected_model(None))

        restore = menu.addAction("Restore selected to source state")
        restore.triggered.connect(self._restore_selected)
        menu.exec(global_position)

    def _update_selected(
        self, updater: Callable[[ScenarioDecap], ScenarioDecap]
    ) -> None:
        if self._scenario is None:
            return
        keys = {item.casefold() for item in self.board.selected_refdes}
        decaps = [
            updater(item) if item.refdes.casefold() in keys else item
            for item in self._scenario.decaps
        ]
        self._scenario = ScenarioSpec.model_validate(
            {
                **self._scenario.model_dump(mode="json"),
                "decaps": decaps,
                "revision": self._scenario.revision + 1,
            }
        )
        self._dirty = True
        self._invalidate_evaluation()
        self._refresh_all()

    def _assign_selected_rail(self, rail_id: str) -> None:
        def update(decap: ScenarioDecap) -> ScenarioDecap:
            eligibility = decap.eligibility.get(rail_id)
            if eligibility is None or not eligibility.allowed:
                raise ValueError(f"{decap.refdes}: selected rail is not physically eligible")
            return ScenarioDecap.model_validate(
                {
                    **decap.model_dump(mode="json"),
                    "current_rail_id": eligibility.rail_id,
                    "current_net": eligibility.net,
                }
            )

        try:
            self._update_selected(update)
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))

    def _assign_selected_model(self, model_id: str | None) -> None:
        assert self._scenario is not None
        model = next(
            (item for item in self._scenario.base_project.cap_models if item.model_id == model_id),
            None,
        )

        def update(decap: ScenarioDecap) -> ScenarioDecap:
            if model is not None and model.footprint.casefold() != decap.footprint.casefold():
                raise ValueError(
                    f"{decap.refdes}: {model.model_id} footprint {model.footprint} "
                    f"does not match {decap.footprint}"
                )
            return ScenarioDecap.model_validate(
                {
                    **decap.model_dump(mode="json"),
                    "model_id": model_id,
                    "enabled": decap.enabled,
                }
            )

        try:
            self._update_selected(update)
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))

    def _set_selected_enabled(self, enabled: bool) -> None:
        assert self._scenario is not None
        models = {
            item.model_id.casefold(): item for item in self._scenario.base_project.cap_models
        }

        def update(decap: ScenarioDecap) -> ScenarioDecap:
            if enabled:
                eligibility = decap.eligibility.get(decap.current_rail_id)
                if eligibility is None or not eligibility.allowed:
                    raise ValueError(f"{decap.refdes}: current PWR assignment is not eligible")
                if decap.model_id is None or decap.model_id.casefold() not in models:
                    raise ValueError(f"{decap.refdes}: assign a decap model before enabling")
                model = models[decap.model_id.casefold()]
                if model.footprint.casefold() != decap.footprint.casefold():
                    raise ValueError(f"{decap.refdes}: model footprint mismatch")
            return ScenarioDecap.model_validate(
                {**decap.model_dump(mode="json"), "enabled": enabled}
            )

        try:
            self._update_selected(update)
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))

    def _restore_selected(self) -> None:
        def update(decap: ScenarioDecap) -> ScenarioDecap:
            model_id = decap.source_model_id
            if model_id is None and self._scenario is not None:
                capture = next(
                    (
                        item
                        for key, item in self._scenario.baseline_captures.items()
                        if key.casefold() == decap.source_rail_id.casefold()
                    ),
                    None,
                )
                if capture is not None:
                    binding = next(
                        (
                            item
                            for item in capture.model_bindings
                            if item.refdes.casefold() == decap.refdes.casefold()
                        ),
                        None,
                    )
                    if binding is not None:
                        model_id = binding.model_id
            return ScenarioDecap.model_validate(
                {
                    **decap.model_dump(mode="json"),
                    "current_net": decap.source_net,
                    "current_rail_id": decap.source_rail_id,
                    "model_id": model_id,
                    "enabled": decap.source_mounted,
                }
            )

        self._update_selected(update)

    def _choose_net_color(self, item: QListWidgetItem) -> None:
        if self._scenario is None:
            return
        net = str(item.data(Qt.ItemDataRole.UserRole))
        initial = QColor(self._scenario.net_colors[net])
        color = QColorDialog.getColor(initial, self, f"Color for {net}")
        if not color.isValid():
            return
        colors = dict(self._scenario.net_colors)
        colors[net] = color.name().upper()
        self._scenario = ScenarioSpec.model_validate(
            {**self._scenario.model_dump(mode="json"), "net_colors": colors}
        )
        self._dirty = True
        self._refresh_all()
        if self._comparison_batch is not None:
            try:
                self._render_comparisons(self._comparison_batch.comparisons)
            except (KeyError, TypeError, ValueError):
                self._invalidate_evaluation(
                    "PWR NET colors changed and the prior comparison became invalid."
                )

    def add_decap_model(self) -> None:
        if self._scenario is None:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Add passive decap model",
            "",
            "SPICE model (*.lib *.cir *.sp *.spice *.mod);;All files (*)",
        )
        if not filename:
            return
        path = Path(filename)
        try:
            names = cap_spice_subcircuit_names(path)
            subckt_name = None
            if len(names) > 1:
                subckt_name, accepted = QInputDialog.getItem(
                    self, "Select .SUBCKT", "Passive two-terminal model", names, 0, False
                )
                if not accepted:
                    return
            selected = self._selected_decaps()
            default_footprint = selected[0].footprint if selected else "GENERIC"
            footprint, accepted = QInputDialog.getText(
                self, "Model footprint", "Footprint", text=default_footprint
            )
            if not accepted or not footprint.strip():
                return
            state = WorkspaceState(
                project=self._scenario.base_project,
                attachments=dict(self._attachments),
            )
            import_cap_spice(
                state,
                path,
                footprint=footprint.strip(),
                inventory=max(len(self._scenario.decaps), 1),
                subckt_name=subckt_name or None,
            )
            self._attachments = state.attachments
            hashes = {
                name: sha256(payload).hexdigest()
                for name, payload in self._attachments.items()
            }
            self._scenario = ScenarioSpec.model_validate(
                {
                    **self._scenario.model_dump(mode="json"),
                    "normalized_project": state.project,
                    "attachment_names": sorted(self._attachments, key=str.casefold),
                    "attachment_hashes": hashes,
                    "revision": self._scenario.revision + 1,
                }
            )
            self._dirty = True
            self._invalidate_evaluation()
            self._refresh_all()
            self.status_text.setText(f"Added decap model from {path.name}")
        except Exception as exc:
            QMessageBox.critical(self, APP_DISPLAY_NAME, str(exc))

    def run_evaluation(self) -> None:
        if self._scenario is None:
            return
        rail_ids = self._checked_rail_ids()
        if not rail_ids:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "Select at least one PWR NET.")
            return
        target_text = self.target_edit.text().strip()
        try:
            target = float(target_text) if target_text else None
            if target is not None and target <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "Target impedance must be positive.")
            return

        from ..evaluation import (
            baseline_fallback_model_refdes,
            evaluate_comparison_batch,
        )

        fallback_refdes = baseline_fallback_model_refdes(self._scenario, rail_ids)
        try:
            prepared = self._scenario.with_baseline_captures(rail_ids)
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        if fallback_refdes:
            preview = ", ".join(fallback_refdes[:12])
            suffix = "..." if len(fallback_refdes) > 12 else ""
            choice = QMessageBox.question(
                self,
                APP_DISPLAY_NAME,
                f"The SPD does not provide electrical model assignments for "
                f"{len(fallback_refdes):,} originally mounted decap(s). Their current "
                "model assignments must be frozen as the immutable Original baseline "
                f"({preview}{suffix}).\n\nContinue with this one-time baseline capture?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        if self._scenario_path is None:
            path = self._request_scenario_save_path()
            if path is None:
                return
            self._scenario_path = path

        worker = FunctionWorker(
            evaluate_comparison_batch,
            prepared,
            rail_ids,
            target_ohm=target,
            attachments=dict(self._attachments),
        )
        self.evaluation_summary.setPlainText(
            f"Evaluating Original and Tuned configurations for "
            f"{len(rail_ids):,} PWR NET(s)..."
        )
        self._run_worker(
            worker,
            self._accept_evaluation,
            label=f"Evaluating {len(rail_ids):,} PWR NET(s)...",
        )

    def _accept_evaluation(self, result: Any) -> None:
        matches = getattr(result, "matches", None)
        if (
            self._scenario is None
            or not callable(matches)
            or not bool(matches(self._scenario))
        ):
            self._invalidate_evaluation(
                "The completed result was stale and was discarded; run evaluation again."
            )
            self.status_text.setText("Discarded stale evaluation result")
            return
        validate = getattr(result, "validate_for_scenario", None)
        try:
            if not callable(validate):
                raise ValueError("comparison batch validator is missing")
            validate(self._scenario)
        except (TypeError, ValueError) as exc:
            self._invalidate_evaluation(
                "The completed comparison batch failed identity validation."
            )
            QMessageBox.critical(self, APP_DISPLAY_NAME, str(exc))
            self.status_text.setText("Discarded invalid evaluation result")
            return
        comparisons = tuple(getattr(result, "comparisons", ()))
        updated_scenario = getattr(result, "updated_scenario", None)
        updated_attachments = getattr(result, "updated_attachments", None)
        if (
            not comparisons
            or not isinstance(updated_scenario, ScenarioSpec)
            or not isinstance(updated_attachments, dict)
        ):
            self._invalidate_evaluation(
                "The evaluation worker returned an incomplete comparison batch."
            )
            self.status_text.setText("Discarded invalid evaluation result")
            return

        current = self._scenario
        try:
            rail_labels = self._render_comparisons(comparisons)
        except (KeyError, TypeError, ValueError) as exc:
            self._invalidate_evaluation(
                "The comparison plot could not validate the completed batch."
            )
            QMessageBox.critical(self, APP_DISPLAY_NAME, str(exc))
            self.status_text.setText("Discarded invalid comparison plot data")
            return

        persistent_change = (
            updated_scenario.model_dump(mode="json")
            != current.model_dump(mode="json")
            or updated_attachments != self._attachments
        )
        self._scenario = updated_scenario
        self._attachments = dict(updated_attachments)
        self._dirty = self._dirty or persistent_change
        self._auto_save_after_worker = self._dirty
        self._comparison_batch = result
        self._evaluation_state = None
        self._tuned_evaluations_by_rail = {
            comparison.rail_id.casefold(): comparison.tuned
            for comparison in comparisons
        }

        self.comparison_table.setRowCount(len(comparisons))
        for row, comparison in enumerate(comparisons):
            baseline = comparison.baseline.view
            tuned = comparison.tuned.view
            net = rail_labels[comparison.rail_id]
            values = (
                net,
                f"{baseline.cap_count:,} → {tuned.cap_count:,}",
                f"{baseline.peak_magnitude_ohm * 1_000:.3f} mΩ",
                f"{tuned.peak_magnitude_ohm * 1_000:.3f} mΩ",
                f"{(tuned.peak_magnitude_ohm - baseline.peak_magnitude_ohm) * 1_000:+.3f} mΩ",
                f"{baseline.max_violation_db:.3f} → {tuned.max_violation_db:.3f} dB",
                "Reused" if comparison.baseline_from_cache else "Saved now",
            )
            for column, value in enumerate(values):
                self.comparison_table.setItem(
                    row, column, QTableWidgetItem(str(value))
                )
        self.comparison_table.resizeColumnsToContents()

        self.ai_rail_combo.blockSignals(True)
        self.ai_rail_combo.clear()
        for comparison in comparisons:
            self.ai_rail_combo.addItem(
                f"{rail_labels[comparison.rail_id]} ({comparison.rail_id})",
                comparison.rail_id,
            )
        self.ai_rail_combo.blockSignals(False)
        self._ai_rail_changed()

        cached_count = sum(item.baseline_from_cache for item in comparisons)
        newly_saved = len(comparisons) - cached_count
        save_note = (
            f"Original results will be saved automatically to {self._scenario_path.name}."
            if newly_saved
            else f"Original results are stored in {self._scenario_path.name}."
        )
        self.evaluation_summary.setPlainText(
            "\n".join(
                (
                    f"Compared {len(comparisons):,} PWR NET(s): Original vs Tuned.",
                    f"Original baseline: {cached_count:,} reused, {newly_saved:,} newly evaluated and staged.",
                    "Plot: one shared impedance view; all PWR NETs start visible and can be filtered independently.",
                    save_note,
                    "Select a Tuned result in AI Assist when analysis is needed.",
                )
            )
        )
        self._refresh_all()
        self.status_text.setText(
            f"Evaluation complete: {len(comparisons):,} PWR NET(s)"
        )

    def _render_comparisons(self, comparisons: tuple[Any, ...]) -> dict[str, str]:
        assert self._scenario is not None
        rail_by_id = {
            item.rail_id.casefold(): item
            for item in self._scenario.base_project.rails
        }
        rail_colors = {
            comparison.rail_id: self.board.color_for_net(
                rail_by_id[comparison.rail_id.casefold()].net
            ).name()
            for comparison in comparisons
        }
        rail_labels = {
            comparison.rail_id: rail_by_id[comparison.rail_id.casefold()].net
            for comparison in comparisons
        }
        self.plot.set_comparisons(
            comparisons,
            rail_colors=rail_colors,
            rail_labels=rail_labels,
        )
        return rail_labels

    def _ai_rail_changed(self, _index: int | None = None) -> None:
        self.ai_output.clear()
        rail_id = self.ai_rail_combo.currentData()
        evaluation = (
            self._tuned_evaluations_by_rail.get(str(rail_id).casefold())
            if rail_id
            else None
        )
        self._last_scenario_evaluation = evaluation
        self._last_evaluation = getattr(evaluation, "view", None)
        self._evaluation_state = None
        self.ai_button.setEnabled(
            self._worker is None
            and self._scenario is not None
            and evaluation is not None
        )

    def run_ai_assist(self) -> None:
        if self._last_scenario_evaluation is None or self._last_evaluation is None:
            QMessageBox.information(self, APP_DISPLAY_NAME, "Run an evaluation first.")
            return

        from ..evaluation import (
            analyze_scenario_with_local_llm,
            rehydrate_scenario_evaluation,
        )

        assert self._scenario is not None
        compact = self._last_scenario_evaluation
        try:
            evaluation = rehydrate_scenario_evaluation(
                self._scenario,
                compact,
                attachments=dict(self._attachments),
            )
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        rail_id = compact.view.rail_id
        evaluation_fingerprint = evaluation.evaluation_fingerprint
        worker = FunctionWorker(
            analyze_scenario_with_local_llm,
            evaluation,
            self.ai_endpoint.text().strip(),
            self.ai_model.text().strip(),
            allow_remote=self.ai_allow_remote.isChecked(),
        )
        self._run_worker(
            worker,
            lambda result: self._accept_ai_analysis(
                result, rail_id, evaluation_fingerprint
            ),
            label=f"Analyzing Tuned result for {rail_id}...",
        )

    def _accept_ai_analysis(
        self, result: Any, rail_id: str, evaluation_fingerprint: str
    ) -> None:
        evaluation = self._tuned_evaluations_by_rail.get(rail_id.casefold())
        if (
            self._scenario is None
            or evaluation is None
            or evaluation.evaluation_fingerprint != evaluation_fingerprint
            or not evaluation.matches(self._scenario)
        ):
            self.ai_output.clear()
            self.status_text.setText("Discarded stale AI analysis")
            return
        source = getattr(getattr(result, "source", None), "value", getattr(result, "source", ""))
        text = getattr(result, "text", str(result))
        self.ai_output.setPlainText(f"Source: {source}\n\n{text}")
        self.status_text.setText("AI plot analysis complete")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._worker is not None:
            if self._worker_cancelable:
                choice = QMessageBox.question(
                    self,
                    APP_DISPLAY_NAME,
                    "An operation is still running. Cancel it? The window will remain "
                    "open until the worker finishes.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if choice == QMessageBox.StandardButton.Yes:
                    self._cancel_worker()
            else:
                QMessageBox.information(
                    self,
                    APP_DISPLAY_NAME,
                    "A save or scenario verification is still running. Wait for it to finish "
                    "before closing the window.",
                )
            event.ignore()
            return
        if not self._can_replace_document():
            event.ignore()
            return
        event.accept()


__all__ = ["MainWindow"]
