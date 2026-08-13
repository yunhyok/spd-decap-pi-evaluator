"""Detached, non-modal controls for De-cap Distribution target workbooks."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..version import APP_DISPLAY_NAME


class DistributionTargetsWindow(QWidget):
    """A retained non-modal view of the main target matrix.

    The main window owns canonical state and validation. Target and Tolerance
    cells are editable here and route changes back to that owner; XLSX import
    and export remain available as optional workflows.
    """

    importRequested = Signal()
    exportTemplateRequested = Signal()
    originalBoardToggled = Signal(bool)
    detachedCellChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("distributionTargetsWindow")
        self.setWindowTitle(f"{APP_DISPLAY_NAME} - Distribution Targets")
        self.setAccessibleName("Distribution target workbook window")
        self.resize(980, 620)
        layout = QVBoxLayout(self)
        instructions = QLabel(
            "Double-click any Distribution table cell to open this window. Edit "
            "Target or Tolerance (%) directly, or use the optional XLSX workflow; "
            "targets are validated against the currently loaded SPD."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.alternate_plane_note = QLabel(
            "Alternate PWR plane: VIA STACK CHANGE REQUIRED — exact target-plane "
            "copper is assessed at the immutable PWR landing XY; plane artwork "
            "remains unchanged."
        )
        self.alternate_plane_note.setObjectName("alternatePwrPlaneRoutingNote")
        self.alternate_plane_note.setWordWrap(True)
        self.alternate_plane_note.setToolTip(
            "This is a filled-Cu microvia-stack retarget/rebuild planning result, "
            "and does not prove that the existing via barrel already reaches "
            "that layer."
        )
        layout.addWidget(self.alternate_plane_note)
        self.table = QTableWidget(0, 1)
        self.table.setObjectName("detachedDistributionTargetTable")
        self.table.setAccessibleName("Distribution target matrix")
        self.table.setToolTip(
            "Edit Target or Tolerance (%) directly; XLSX remains optional."
        )
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
        )
        self.table.itemChanged.connect(self.detachedCellChanged.emit)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        layout.addWidget(self.table, 1)
        self.original_board_checkbox = QCheckBox(
            "Show source SPD assignments on the board"
        )
        self.original_board_checkbox.setObjectName("showOriginalDistributionBoard")
        self.original_board_checkbox.setAccessibleName(
            "Show source SPD board assignments"
        )
        self.original_board_checkbox.setToolTip(
            "Compare the immutable source-SPD assignment with the current board. "
            "Source view is display-only; physical X/Y positions do not move."
        )
        self.original_board_checkbox.toggled.connect(self.originalBoardToggled)
        layout.addWidget(self.original_board_checkbox)
        buttons = QHBoxLayout()
        self.import_targets_button = QPushButton("Import Targets...")
        self.import_targets_button.setObjectName("detachedImportDistributionTargetsButton")
        self.import_targets_button.setAccessibleName("Import distribution XLSX")
        self.import_targets_button.setToolTip(
            "Import an edited Distribution XLSX and immediately update the main "
            "target matrix."
        )
        self.import_targets_button.clicked.connect(self.importRequested)
        self.export_template_button = QPushButton("Export XLSX Template...")
        self.export_template_button.setObjectName("exportDistributionTemplateButton")
        self.export_template_button.setAccessibleName("Export distribution XLSX template")
        self.export_template_button.setToolTip(
            "Export the current target matrix before calculation or preview."
        )
        self.export_template_button.clicked.connect(self.exportTemplateRequested)
        buttons.addWidget(self.import_targets_button)
        buttons.addWidget(self.export_template_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    def set_matrix(
        self, headers: Sequence[str], rows: Sequence[Sequence[object]]
    ) -> None:
        self.table.setUpdatesEnabled(False)
        previous_block = self.table.blockSignals(True)
        try:
            self.table.clear()
            self.table.setColumnCount(len(headers))
            self.table.setRowCount(len(rows))
            self.table.setHorizontalHeaderLabels(tuple(headers))
            for row, values in enumerate(rows):
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(str(value)))
            self.table.resizeColumnsToContents()
        finally:
            self.table.blockSignals(previous_block)
            self.table.setUpdatesEnabled(True)

    def set_original_board_checked(self, checked: bool) -> None:
        previous = self.original_board_checkbox.blockSignals(True)
        try:
            self.original_board_checkbox.setChecked(checked)
        finally:
            self.original_board_checkbox.blockSignals(previous)

    def clear_for_no_document(self) -> None:
        """Discard a detached snapshot when its parent changes documents."""

        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.table.setEnabled(False)
        self.import_targets_button.setEnabled(False)
        self.export_template_button.setEnabled(False)
        self.set_original_board_checked(False)
        self.original_board_checkbox.setEnabled(False)
        self.hide()

    def set_document_active(
        self,
        active: bool,
        *,
        busy: bool = False,
        source_comparison_available: bool = False,
    ) -> None:
        """Synchronize document lifetime and main-window worker exclusivity."""

        enabled = active and not busy
        self.table.setEnabled(enabled)
        self.import_targets_button.setEnabled(enabled)
        self.export_template_button.setEnabled(enabled)
        self.original_board_checkbox.setEnabled(
            enabled and source_comparison_available
        )

    def set_cell_metadata(
        self,
        metadata: Sequence[Sequence[tuple[object, ...]]],
    ) -> None:
        previous = self.table.blockSignals(True)
        try:
            for row, values in enumerate(metadata):
                for column, payload in enumerate(values):
                    item = self.table.item(row, column)
                    if item is None:
                        continue
                    (
                        key,
                        field,
                        flags,
                        background,
                        foreground,
                        alignment,
                        tooltip,
                    ) = payload
                    item.setData(Qt.ItemDataRole.UserRole, key)
                    item.setData(Qt.ItemDataRole.UserRole + 1, field)
                    item.setFlags(flags)
                    item.setBackground(background)
                    item.setForeground(foreground)
                    item.setTextAlignment(Qt.AlignmentFlag(alignment))
                    item.setToolTip(tooltip)
        finally:
            self.table.blockSignals(previous)
