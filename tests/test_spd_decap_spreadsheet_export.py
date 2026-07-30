from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from spd_decap_pi.spreadsheet_export import write_distribution_workbook


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
                f"{model_id}\nActual Changed",
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
