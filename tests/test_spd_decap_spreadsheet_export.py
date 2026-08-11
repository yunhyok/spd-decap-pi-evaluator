from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook
import pytest

import spd_decap_pi.spreadsheet_export as spreadsheet_export
from spd_decap_pi.distribution_workbook import load_distribution_targets
from spd_decap_pi.spreadsheet_export import (
    CANDIDATE_AUDIT_HEADERS,
    EXCEL_MAX_DATA_ROWS,
    write_distribution_workbook,
)


def test_distribution_workbook_has_two_typed_formula_safe_sheets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distribution.xlsx"
    write_distribution_workbook(
        path,
        (
            ("=MODEL", "+C1", "@BEFORE", "-AFTER", 1.25, -2.5),
            ("M1", "C2", "V1", "V1", 3.0, 4.0),
        ),
        (
            "PWR NET",
            "M1\nPresent",
            "M1\nTarget",
            "M1\nTolerance (%)",
            "M1\nActual Delta",
        ),
        (
            ("V1 (R1)", 2, 1, 1.25, -1),
            ("V2 (R2)", 0, 1, 0.0, 1),
        ),
    )

    workbook = load_workbook(path, data_only=False)
    try:
        assert workbook.sheetnames == [
            "Decap Changes",
            "PWR NET Distribution Targets",
        ]
        decaps = workbook["Decap Changes"]
        assert tuple(cell.value for cell in decaps[1]) == (
            "Component",
            "REFDES",
            "Before NET",
            "After NET",
            "X (um)",
            "Y (um)",
        )
        assert tuple(cell.value for cell in decaps[2]) == (
            "=MODEL",
            "+C1",
            "@BEFORE",
            "-AFTER",
            1.25,
            -2.5,
        )
        assert all(decaps.cell(2, column).data_type == "s" for column in range(1, 5))
        assert all(decaps.cell(2, column).data_type == "n" for column in range(5, 7))
        assert decaps.freeze_panes == "A2"
        assert decaps.auto_filter.ref == "A1:F3"

        targets = workbook["PWR NET Distribution Targets"]
        assert tuple(cell.value for cell in targets[1]) == (
            "PWR NET",
            "M1\nPresent",
            "M1\nTarget",
            "M1\nTolerance (%)",
            "M1\nActual Delta",
        )
        assert tuple(cell.value for cell in targets[2]) == (
            "V1 (R1)",
            2,
            1,
            1.25,
            -1,
        )
        assert tuple(cell.value for cell in targets[3]) == (
            "V2 (R2)",
            0,
            1,
            0,
            1,
        )
        assert targets.cell(2, 4).number_format == '0.###"%"'
        assert targets.freeze_panes == "B2"
        assert targets.auto_filter.ref == "A1:E3"
    finally:
        workbook.close()


def test_distribution_workbook_rejects_misaligned_target_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid.xlsx"
    try:
        write_distribution_workbook(path, (), ("PWR NET", "Target"), (("R1",),))
    except ValueError as exc:
        assert "match their headers" in str(exc)
    else:
        raise AssertionError("misaligned target row was accepted")
    assert not path.exists()


def test_distribution_workbook_optionally_writes_candidate_audit_sheet(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distribution-with-audit.xlsx"
    write_distribution_workbook(
        path,
        (),
        ("PWR NET", "M1\nTarget"),
        (("REF_CLK (R1)", 15),),
        candidate_audit_headers=CANDIDATE_AUDIT_HEADERS,
        candidate_audit_rows=(
            (
                "C793-C805",
                "C793, C794",
                13,
                "SRC",
                "REF_CLK",
                "M1",
                12.5,
                True,
                False,
                "NO_ZERO_GAP_EXACT_COUNT_COMBINATION",
                "component size 13 cannot be part of any zero-gap exact-count subset",
            ),
        ),
    )
    workbook = load_workbook(path, data_only=False)
    try:
        assert workbook.sheetnames == [
            "Decap Changes",
            "PWR NET Distribution Targets",
            "Candidate Audit",
        ]
        audit = workbook["Candidate Audit"]
        assert audit.cell(1, 1).value == "RefDes"
        assert audit.cell(2, 1).value == "C793-C805"
        assert audit.cell(2, 10).value == "NO_ZERO_GAP_EXACT_COUNT_COMBINATION"
        assert audit.freeze_panes == "A2"
    finally:
        workbook.close()
    imported = load_distribution_targets(
        path,
        rail_ids=("R1",),
        model_ids=("M1",),
        current_present={("R1", "M1"): 4},
    )
    assert imported.targets == {("R1", "M1"): 15}


def test_distribution_workbook_formats_distribution_columns_for_multiple_models(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distribution-five-column-groups.xlsx"
    headers = ["PWR NET"]
    row: list[object] = ["V1 (R1)"]
    for model_id, tolerance, delta, gaps in (
        ("M1", 1.25, -2, 1),
        ("M2", 2.5, 3, 0),
    ):
        headers.extend(
            (
                f"{model_id}\nPresent",
                f"{model_id}\nTarget",
                f"{model_id}\nTolerance (%)",
                f"{model_id}\nActual Delta",
                f"{model_id}\nAssignment Failed",
                f"{model_id}\nIsolation Gaps",
            )
        )
        row.extend((10, 8, tolerance, delta, 4, gaps))
    write_distribution_workbook(path, (), tuple(headers), (tuple(row),))

    workbook = load_workbook(path, data_only=False)
    try:
        targets = workbook["PWR NET Distribution Targets"]
        assert targets.cell(2, 4).number_format == '0.###"%"'
        assert targets.cell(2, 5).number_format == "+#,##0;-#,##0;0"
        assert targets.cell(2, 6).number_format == "#,##0"
        assert targets.cell(2, 7).number_format == "#,##0"
        assert targets.cell(2, 10).number_format == '0.###"%"'
        assert targets.cell(2, 11).number_format == "+#,##0;-#,##0;0"
        assert targets.cell(2, 12).number_format == "#,##0"
        assert targets.cell(2, 13).number_format == "#,##0"
    finally:
        workbook.close()


def test_candidate_audit_row_limit_is_checked_before_xlsxwriter_truncation(
    tmp_path: Path,
) -> None:
    row = ("C1", "C1", 1, "SRC", "DST", "M1", 1.0, True, False, "X", "detail")
    path = tmp_path / "overflow.xlsx"
    with pytest.raises(ValueError, match="worksheet limit"):
        write_distribution_workbook(
            path,
            (),
            ("PWR NET",),
            (("R1",),),
            candidate_audit_headers=CANDIDATE_AUDIT_HEADERS,
            candidate_audit_rows=(row,) * (EXCEL_MAX_DATA_ROWS + 1),
        )
    assert not path.exists()


def test_candidate_audit_row_limit_boundary_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Avoid writing a million physical rows in a unit test; validation occurs
    # before population and this verifies the exact Excel boundary is allowed.
    monkeypatch.setattr(spreadsheet_export, "_populate_distribution_workbook", lambda *args: None)
    path = tmp_path / "boundary.xlsx"
    row = ("C1", "C1", 1, "SRC", "DST", "M1", 1.0, True, False, "X", "detail")
    write_distribution_workbook(
        path,
        (),
        ("PWR NET",),
        (("R1",),),
        candidate_audit_headers=CANDIDATE_AUDIT_HEADERS,
        candidate_audit_rows=(row,) * EXCEL_MAX_DATA_ROWS,
    )
    assert path.exists()
