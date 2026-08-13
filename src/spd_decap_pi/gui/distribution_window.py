"""Detached, non-modal controls for De-cap Distribution target workbooks."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    detachedBatchCommitted = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("distributionTargetsWindow")
        self.setWindowTitle(f"{APP_DISPLAY_NAME} - Distribution Targets")
        self.setAccessibleName("Distribution target workbook window")
        self.resize(980, 620)
        self._matrix_revision = 0
        layout = QVBoxLayout(self)
        instructions = QLabel(
            "Double-click any Distribution table cell to open this window. Edit "
            "Target or Tolerance (%) directly. Ctrl/Shift-select editable cells "
            "of the same field and type once to fill them together. The optional "
            "XLSX workflow remains available, and all values are validated against "
            "the currently loaded SPD."
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
            "Edit Target or Tolerance (%) directly. Same-field multi-selection "
            "fills from one committed edit; XLSX remains optional."
        )
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
        )
        self.table.itemChanged.connect(self._detached_item_changed)
        self.table.itemDelegate().commitData.connect(
            self._detached_editor_committed
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
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
        self._matrix_revision += 1
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

    @property
    def matrix_revision(self) -> int:
        """Return the identity of the currently displayed canonical matrix."""

        return self._matrix_revision

    @staticmethod
    def _item_descriptor(
        item: QTableWidgetItem,
    ) -> tuple[tuple[str, str], str] | None:
        raw_key = item.data(Qt.ItemDataRole.UserRole)
        field = item.data(Qt.ItemDataRole.UserRole + 1)
        if (
            not item.flags() & Qt.ItemFlag.ItemIsEditable
            or not isinstance(raw_key, tuple)
            or len(raw_key) != 2
            or field not in {"target", "tolerance"}
        ):
            return None
        return (str(raw_key[0]), str(raw_key[1])), str(field)

    def _selected_batch_keys(
        self,
        source: QTableWidgetItem,
        source_key: tuple[str, str],
        field: str,
    ) -> tuple[tuple[str, str], ...]:
        keys = [source_key]
        if source.isSelected():
            for candidate in self.table.selectedItems():
                descriptor = self._item_descriptor(candidate)
                if descriptor is None or descriptor[1] != field:
                    continue
                key = descriptor[0]
                if key not in keys:
                    keys.append(key)
        return tuple(keys)

    def _edit_payload(
        self,
        item: QTableWidgetItem,
        *,
        committed_text: str | None = None,
    ) -> tuple[str, str, tuple[tuple[str, str], ...], int] | None:
        descriptor = self._item_descriptor(item)
        if descriptor is None or not self.table.isEnabled():
            return None
        source_key, field = descriptor
        keys = self._selected_batch_keys(item, source_key, field)
        text = item.text() if committed_text is None else committed_text
        return field, text, keys, self._matrix_revision

    def _detached_item_changed(self, item: QTableWidgetItem) -> None:
        payload = self._edit_payload(item)
        if payload is not None:
            self.detachedBatchCommitted.emit(payload)

    def _detached_editor_committed(self, editor: QWidget) -> None:
        # commitData precedes model copying. Use the editor text and stable
        # key/field descriptors so an unchanged source can still bulk-fill;
        # the owner resynchronizes the same table/model before Qt copies an
        # equivalent value into the current index.
        item = self.table.currentItem()
        payload = (
            self._edit_payload(item, committed_text=str(editor.property("text")))
            if item is not None
            else None
        )
        if payload is None:
            return
        self.detachedBatchCommitted.emit(payload)

    def set_original_board_checked(self, checked: bool) -> None:
        previous = self.original_board_checkbox.blockSignals(True)
        try:
            self.original_board_checkbox.setChecked(checked)
        finally:
            self.original_board_checkbox.blockSignals(previous)

    def clear_for_no_document(self) -> None:
        """Discard a detached snapshot when its parent changes documents."""

        self.table.clear()
        self._matrix_revision += 1
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
