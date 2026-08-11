"""Excel workbook writers for auditable De-cap Distribution results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from pathlib import Path
from typing import Any

from xlsxwriter import Workbook
from xlsxwriter.exceptions import XlsxWriterException

from .distribution_workbook import (
    DISTRIBUTION_METADATA_TITLE,
    DISTRIBUTION_WORKBOOK_FORMAT_VERSION,
)


DECAP_CHANGE_HEADERS = (
    "Component",
    "REFDES",
    "Before NET",
    "After NET",
    "X (um)",
    "Y (um)",
)

CANDIDATE_AUDIT_HEADERS = (
    "RefDes",
    "Component Members",
    "Component Size",
    "Source",
    "Destination",
    "Model",
    "Distance (um)",
    "Eligible",
    "Selected",
    "Decision Code",
    "Decision Detail",
)

# Excel worksheets have 1,048,576 rows including the header row.  Keep the
# audit export fail-closed instead of letting xlsxwriter silently ignore rows
# after the worksheet boundary.
EXCEL_MAX_DATA_ROWS = 1_048_575


def _write_value(
    worksheet: Any,
    row: int,
    column: int,
    value: object,
    cell_format: Any,
) -> None:
    """Write identifiers as literal strings and counts/coordinates as numbers."""

    if value is None:
        worksheet.write_blank(row, column, None, cell_format)
        return
    if isinstance(value, bool):
        worksheet.write_boolean(row, column, value, cell_format)
        return
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not isfinite(numeric):
            raise ValueError("Excel export values must be finite")
        worksheet.write_number(row, column, value, cell_format)
        return
    # Explicit write_string plus strings_to_formulas=False prevents identifiers
    # beginning with =, +, -, or @ from becoming executable spreadsheet input.
    worksheet.write_string(row, column, str(value), cell_format)


def write_distribution_workbook(
    path: Path,
    decap_rows: Sequence[Sequence[object]],
    target_headers: Sequence[str],
    target_rows: Sequence[Sequence[object]],
    *,
    inventory_headers: Sequence[str] = (),
    inventory_rows: Sequence[Sequence[object]] = (),
    candidate_audit_headers: Sequence[str] = (),
    candidate_audit_rows: Sequence[Sequence[object]] = (),
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Write Decap results and the source Distribution target matrix to XLSX."""

    normalized_decaps = tuple(tuple(row) for row in decap_rows)
    normalized_target_headers = tuple(str(value) for value in target_headers)
    normalized_targets = tuple(tuple(row) for row in target_rows)
    normalized_inventory_headers = tuple(str(value) for value in inventory_headers)
    normalized_inventory = tuple(tuple(row) for row in inventory_rows)
    normalized_candidate_headers = tuple(str(value) for value in candidate_audit_headers)
    normalized_candidate_rows = tuple(tuple(row) for row in candidate_audit_rows)
    normalized_metadata = tuple(
        (str(key).strip(), value) for key, value in (metadata or {}).items()
    )
    if len(normalized_target_headers) < 1:
        raise ValueError("Distribution target headers cannot be empty")
    if any(len(row) != len(DECAP_CHANGE_HEADERS) for row in normalized_decaps):
        raise ValueError("Every Decap export row must contain six values")
    if any(len(row) != len(normalized_target_headers) for row in normalized_targets):
        raise ValueError("Distribution target rows must match their headers")
    if bool(normalized_inventory_headers) != bool(normalized_inventory):
        raise ValueError(
            "Inventory reconciliation headers and rows must be supplied together"
        )
    if any(
        len(row) != len(normalized_inventory_headers)
        for row in normalized_inventory
    ):
        raise ValueError("Inventory reconciliation rows must match their headers")
    if bool(normalized_candidate_headers) != bool(normalized_candidate_rows):
        raise ValueError(
            "Candidate audit headers and rows must be supplied together"
        )
    if len(normalized_candidate_rows) > EXCEL_MAX_DATA_ROWS:
        raise ValueError(
            "Candidate audit rows exceed the Excel worksheet limit "
            f"({EXCEL_MAX_DATA_ROWS:,} data rows)"
        )
    if any(
        len(row) != len(normalized_candidate_headers)
        for row in normalized_candidate_rows
    ):
        raise ValueError("Candidate audit rows must match their headers")
    if any(not key for key, _value in normalized_metadata):
        raise ValueError("Distribution metadata keys cannot be empty")
    if len({key.casefold() for key, _value in normalized_metadata}) != len(
        normalized_metadata
    ):
        raise ValueError("Distribution metadata keys must be unique")
    metadata_by_key = {
        key.casefold(): value for key, value in normalized_metadata
    }
    raw_format = metadata_by_key.get("format version")
    if raw_format not in (None, ""):
        try:
            numeric_format = float(raw_format)
        except (TypeError, ValueError):
            numeric_format = -1.0
        if (
            isfinite(numeric_format)
            and numeric_format.is_integer()
            and int(numeric_format) >= DISTRIBUTION_WORKBOOK_FORMAT_VERSION
            and "signal routing protection" not in metadata_by_key
        ):
            raise ValueError(
                "format 4 Distribution metadata must record Signal Routing Protection"
            )

    options = {
        "constant_memory": True,
        "strings_to_formulas": False,
        "strings_to_urls": False,
    }
    try:
        workbook_context = Workbook(path, options)
        with workbook_context as workbook:
            _populate_distribution_workbook(
                workbook,
                normalized_decaps,
                normalized_target_headers,
                normalized_targets,
                normalized_inventory_headers,
                normalized_inventory,
                normalized_candidate_headers,
                normalized_candidate_rows,
                normalized_metadata,
            )
    except XlsxWriterException as exc:
        raise OSError(str(exc)) from exc


def _populate_distribution_workbook(
    workbook: Workbook,
    normalized_decaps: tuple[tuple[object, ...], ...],
    normalized_target_headers: tuple[str, ...],
    normalized_targets: tuple[tuple[object, ...], ...],
    normalized_inventory_headers: tuple[str, ...],
    normalized_inventory: tuple[tuple[object, ...], ...],
    normalized_candidate_headers: tuple[str, ...],
    normalized_candidate_rows: tuple[tuple[object, ...], ...],
    normalized_metadata: tuple[tuple[str, object], ...],
) -> None:
        header = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#0F766E",
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
                "bottom": 1,
                "bottom_color": "#0B5D56",
            }
        )
        text_even = workbook.add_format(
            {"bottom": 1, "bottom_color": "#E2E8F0", "valign": "vcenter"}
        )
        text_odd = workbook.add_format(
            {
                "bg_color": "#F8FAFC",
                "bottom": 1,
                "bottom_color": "#E2E8F0",
                "valign": "vcenter",
            }
        )
        number_even = workbook.add_format(
            {
                "num_format": "#,##0.000",
                "align": "right",
                "bottom": 1,
                "bottom_color": "#E2E8F0",
            }
        )
        number_odd = workbook.add_format(
            {
                "num_format": "#,##0.000",
                "align": "right",
                "bg_color": "#F8FAFC",
                "bottom": 1,
                "bottom_color": "#E2E8F0",
            }
        )
        count_even = workbook.add_format(
            {
                "num_format": "#,##0",
                "align": "right",
                "bottom": 1,
                "bottom_color": "#E2E8F0",
            }
        )
        count_odd = workbook.add_format(
            {
                "num_format": "#,##0",
                "align": "right",
                "bg_color": "#F8FAFC",
                "bottom": 1,
                "bottom_color": "#E2E8F0",
            }
        )
        delta_even = workbook.add_format(
            {
                "num_format": "+#,##0;-#,##0;0",
                "align": "right",
                "bottom": 1,
                "bottom_color": "#E2E8F0",
            }
        )
        delta_odd = workbook.add_format(
            {
                "num_format": "+#,##0;-#,##0;0",
                "align": "right",
                "bg_color": "#F8FAFC",
                "bottom": 1,
                "bottom_color": "#E2E8F0",
            }
        )
        tolerance_even = workbook.add_format(
            {
                "num_format": '0.###"%"',
                "align": "right",
                "bottom": 1,
                "bottom_color": "#E2E8F0",
            }
        )
        tolerance_odd = workbook.add_format(
            {
                "num_format": '0.###"%"',
                "align": "right",
                "bg_color": "#F8FAFC",
                "bottom": 1,
                "bottom_color": "#E2E8F0",
            }
        )
        section_title = workbook.add_format(
            {
                "bold": True,
                "font_color": "#0F172A",
                "bg_color": "#CCFBF1",
                "bottom": 1,
                "bottom_color": "#0F766E",
            }
        )

        decap_sheet = workbook.add_worksheet("Decap Changes")
        decap_sheet.hide_gridlines(2)
        decap_sheet.freeze_panes(1, 0)
        decap_sheet.set_row(0, 30)
        decap_sheet.write_row(0, 0, DECAP_CHANGE_HEADERS, header)
        decap_sheet.set_column(0, 1, 18)
        decap_sheet.set_column(2, 3, 32)
        decap_sheet.set_column(4, 5, 15)
        for row_index, row_values in enumerate(normalized_decaps, start=1):
            text_format = text_odd if row_index % 2 == 0 else text_even
            number_format = number_odd if row_index % 2 == 0 else number_even
            for column, value in enumerate(row_values):
                _write_value(
                    decap_sheet,
                    row_index,
                    column,
                    value,
                    number_format if column >= 4 else text_format,
                )
        decap_sheet.autofilter(
            0,
            0,
            max(0, len(normalized_decaps)),
            len(DECAP_CHANGE_HEADERS) - 1,
        )

        target_sheet = workbook.add_worksheet("PWR NET Distribution Targets")
        target_sheet.hide_gridlines(2)
        target_sheet.freeze_panes(1, 1)
        target_sheet.set_row(0, 36)
        target_sheet.write_row(0, 0, normalized_target_headers, header)
        target_sheet.set_column(0, 0, 34)
        if len(normalized_target_headers) > 1:
            target_sheet.set_column(1, len(normalized_target_headers) - 1, 14)
        for row_index, row_values in enumerate(normalized_targets, start=1):
            odd = row_index % 2 == 0
            for column, value in enumerate(row_values):
                if column == 0:
                    cell_format = text_odd if odd else text_even
                elif str(normalized_target_headers[column]).endswith(
                    "Actual Delta"
                ):
                    cell_format = delta_odd if odd else delta_even
                elif str(normalized_target_headers[column]).endswith(
                    "Tolerance (%)"
                ):
                    cell_format = tolerance_odd if odd else tolerance_even
                else:
                    cell_format = count_odd if odd else count_even
                _write_value(target_sheet, row_index, column, value, cell_format)
        target_sheet.autofilter(
            0,
            0,
            max(0, len(normalized_targets)),
            len(normalized_target_headers) - 1,
        )
        metadata_row = len(normalized_targets) + 3
        if normalized_inventory:
            title_row = len(normalized_targets) + 3
            header_row = title_row + 1
            target_sheet.merge_range(
                title_row,
                0,
                title_row,
                len(normalized_inventory_headers) - 1,
                (
                    "Input Inventory Reconciliation - all Sheet 1 rows; fixed parts are "
                    "included in Physical Present but cannot change NET"
                ),
                section_title,
            )
            target_sheet.set_row(header_row, 36)
            target_sheet.write_row(
                header_row, 0, normalized_inventory_headers, header
            )
            for offset, row_values in enumerate(normalized_inventory, start=1):
                row_index = header_row + offset
                odd = offset % 2 == 0
                for column, value in enumerate(row_values):
                    if column == 0:
                        cell_format = text_odd if odd else text_even
                    elif column == len(normalized_inventory_headers) - 1:
                        cell_format = delta_odd if odd else delta_even
                    else:
                        cell_format = count_odd if odd else count_even
                    _write_value(
                        target_sheet, row_index, column, value, cell_format
                    )
            total_row = header_row + len(normalized_inventory) + 1
            totals = ["TOTAL"]
            for column in range(1, len(normalized_inventory_headers)):
                totals.append(
                    sum(
                        int(row[column])
                        for row in normalized_inventory
                        if isinstance(row[column], (int, float))
                    )
                )
            target_sheet.write_string(total_row, 0, "TOTAL", section_title)
            for column, value in enumerate(totals[1:], start=1):
                _write_value(
                    target_sheet,
                    total_row,
                    column,
                    value,
                    delta_even
                    if column == len(normalized_inventory_headers) - 1
                    else count_even,
                )
            target_sheet.set_column(
                1,
                max(
                    len(normalized_target_headers),
                    len(normalized_inventory_headers),
                )
                - 1,
                16,
            )
            metadata_row = total_row + 3

        if normalized_metadata:
            target_sheet.merge_range(
                metadata_row,
                0,
                metadata_row,
                1,
                DISTRIBUTION_METADATA_TITLE,
                section_title,
            )
            for offset, (key, value) in enumerate(normalized_metadata, start=1):
                row_index = metadata_row + offset
                target_sheet.write_string(row_index, 0, key, text_even)
                _write_value(target_sheet, row_index, 1, value, text_even)
            target_sheet.set_column(0, 0, 34)
            target_sheet.set_column(1, 1, 68)

        if normalized_candidate_headers:
            audit_sheet = workbook.add_worksheet("Candidate Audit")
            audit_sheet.hide_gridlines(2)
            audit_sheet.freeze_panes(1, 0)
            audit_sheet.set_row(0, 42)
            audit_sheet.write_row(0, 0, normalized_candidate_headers, header)
            audit_sheet.set_column(0, 0, 18)
            audit_sheet.set_column(1, 1, 34)
            audit_sheet.set_column(2, 2, 14)
            audit_sheet.set_column(3, 5, 18)
            audit_sheet.set_column(6, 6, 16)
            audit_sheet.set_column(7, 8, 12)
            audit_sheet.set_column(9, 9, 42)
            audit_sheet.set_column(10, 10, 88)
            for row_index, row_values in enumerate(normalized_candidate_rows, start=1):
                odd = row_index % 2 == 0
                for column, value in enumerate(row_values):
                    if isinstance(value, bool):
                        cell_format = text_odd if odd else text_even
                    elif column in {2}:
                        cell_format = count_odd if odd else count_even
                    elif column == 6:
                        cell_format = number_odd if odd else number_even
                    else:
                        cell_format = text_odd if odd else text_even
                    _write_value(audit_sheet, row_index, column, value, cell_format)
            audit_sheet.autofilter(
                0,
                0,
                max(0, len(normalized_candidate_rows)),
                len(normalized_candidate_headers) - 1,
            )


__all__ = [
    "CANDIDATE_AUDIT_HEADERS",
    "DECAP_CHANGE_HEADERS",
    "write_distribution_workbook",
]
