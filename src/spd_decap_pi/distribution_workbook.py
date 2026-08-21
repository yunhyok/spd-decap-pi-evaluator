"""Fail-closed import of reusable De-cap Distribution target workbooks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path
from typing import Iterable, Mapping
from zipfile import BadZipFile

from .routing_obstacles import ROUTING_POLICY_VERSION


DISTRIBUTION_TARGET_SHEET = "PWR NET Distribution Targets"
DISTRIBUTION_METADATA_TITLE = "Distribution Run Metadata"
DISTRIBUTION_LEGACY_OFF_WORKBOOK_FORMAT_VERSION = 3
DISTRIBUTION_WORKBOOK_FORMAT_VERSION = 5
DISTRIBUTION_VIA_PROJECTION_POLICY = "SOURCE_PROVEN_TARGET_LAYER_TRANSITION_V1"
DISTRIBUTION_TOLERANCE_SEMANTICS = "TARGET_RELATION_COUNTERFLOW_V1"

TargetKey = tuple[str, str]

_KNOWN_FIELDS = {
    "present": "present",
    "target": "target",
    "tolerance (%)": "tolerance",
    "role": "result",
    "turnover allowance": "result",
    "tolerance count": "result",
    "sent": "result",
    "received": "result",
    "actual delta": "result",
    "actual δ": "result",
    "actual changed": "result",
    "assignment failed": "result",
    "isolation gaps": "result",
}
_RAIL_ID_RE = re.compile(r"\(([^()]*)\)\s*$")
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")

# A normal target workbook is only a few hundred rows and columns.  These
# deliberately generous limits keep malformed or accidentally over-formatted
# worksheets from turning import into an unbounded CPU/memory operation.
_MAX_IMPORT_ROWS = 100_000
_MAX_IMPORT_COLUMNS = 4_096
_MAX_TARGET_ROWS = 50_000
_MAX_TARGET_CELLS = 500_000
# Keep replay validation aligned with
# distribution.MAX_DISTRIBUTION_GAP_PENALTY_UM without importing the SciPy-heavy
# optimizer from this lightweight workbook parser.
_MAX_GAP_PENALTY_UM = 1_000_000_000.0


class DistributionWorkbookError(ValueError):
    """A target workbook is malformed or unsafe to apply to the loaded SPD."""


@dataclass(frozen=True, slots=True)
class DistributionTargetImport:
    """Canonical targets merged onto the current scenario inventory."""

    targets: dict[TargetKey, int]
    tolerances: dict[TargetKey, float]
    workbook_present: dict[TargetKey, int]
    distance_mode: str | None
    optimization_policy: str | None
    effective_gap_penalty_um: float | None
    via_projection_policy: str
    format_version: int | None
    source_sha256: str | None
    input_design_fingerprint: str | None
    routing_protection_enabled: bool
    routing_scope: str | None
    routing_clearance_um: float | None
    routing_policy_version: str | None
    routing_asset_sha256: str | None
    routing_asset_content_sha256: str | None
    matched_target_cells: int
    defaulted_current_cells: int
    ignored_neutral_cells: int
    workbook_present_total: int | None
    current_present_total: int
    changed_present_cells: int
    warnings: tuple[str, ...]

    def summary(self, filename: str) -> str:
        lines = [
            f"Imported absolute Target/Tolerance values from {filename}.",
            f"Matched {self.matched_target_cells:,} target cell(s); "
            f"{self.defaulted_current_cells:,} current cell(s) defaulted to no change.",
        ]
        if self.workbook_present_total is not None:
            drift = self.current_present_total - self.workbook_present_total
            lines.append(
                "Present refreshed from the loaded SPD: "
                f"{self.workbook_present_total:,} -> {self.current_present_total:,} "
                f"({drift:+,}; {self.changed_present_cells:,} changed cell(s))."
            )
        else:
            lines.append(
                f"Present refreshed from the loaded SPD: {self.current_present_total:,}; "
                "the workbook did not contain a comparable Present matrix."
            )
        lines.extend(self.warnings)
        if self.distance_mode is None:
            lines.append(
                "Candidate order was not recorded in the workbook."
            )
        if self.optimization_policy is not None:
            penalty = (
                f"{self.effective_gap_penalty_um:g} µm"
                if self.effective_gap_penalty_um is not None
                else "derived from board diagonal on calculation"
            )
            lines.append(
                "Optimization policy restored: "
                f"{self.optimization_policy}; effective gap penalty {penalty}."
            )
        lines.append(
            "Via projection policy: TOP uses the source landing; each non-TOP "
            "target requires source-proven exact-layer transition evidence and "
            "its retained endpoint XY."
        )
        if self.routing_protection_enabled:
            lines.append(
                "Immutable signal-routing protection restored: ON, "
                f"clearance {self.routing_clearance_um:g} µm."
            )
        else:
            lines.append("Immutable signal-routing protection restored: OFF.")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _RawTargetCell:
    rail_id: str
    model_id: str
    target: int
    tolerance: float
    present: int | None


def _header_parts(value: object, *, column: int) -> tuple[str, str]:
    if value is None:
        raise DistributionWorkbookError(
            f"target sheet column {column} has an empty header"
        )
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\n" not in text:
        raise DistributionWorkbookError(
            f"target sheet column {column} has unsupported header {text!r}"
        )
    model_id, field = (part.strip() for part in text.rsplit("\n", 1))
    if not model_id or not field:
        raise DistributionWorkbookError(
            f"target sheet column {column} has an incomplete header"
        )
    semantic = _KNOWN_FIELDS.get(field.casefold())
    if semantic is None:
        raise DistributionWorkbookError(
            f"target sheet column {column} has unsupported field {field!r}"
        )
    return model_id, semantic


def _whole_number(value: object, *, label: str) -> int:
    if value is None or isinstance(value, bool):
        raise DistributionWorkbookError(f"{label} must be a nonnegative whole number")
    if isinstance(value, str) and value.lstrip().startswith("="):
        raise DistributionWorkbookError(f"{label} formulas are not supported")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise DistributionWorkbookError(
            f"{label} must be a nonnegative whole number"
        ) from None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise DistributionWorkbookError(f"{label} must be a nonnegative whole number")
    return int(number)


def _tolerance(value: object, *, label: str) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, bool):
        raise DistributionWorkbookError(f"{label} must be from 0 through 100")
    if isinstance(value, str) and value.lstrip().startswith("="):
        raise DistributionWorkbookError(f"{label} formulas are not supported")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise DistributionWorkbookError(
            f"{label} must be from 0 through 100"
        ) from None
    if not isfinite(number) or not 0.0 <= number <= 100.0:
        raise DistributionWorkbookError(f"{label} must be from 0 through 100")
    return number


def _rail_id(value: object, *, row: int) -> str:
    if value is None:
        raise DistributionWorkbookError(f"target sheet row {row} has no PWR NET")
    text = str(value).strip()
    match = _RAIL_ID_RE.search(text)
    if match is None or not match.group(1).strip():
        raise DistributionWorkbookError(
            f"target sheet row {row} must identify a rail as NET (rail_id)"
        )
    return match.group(1).strip()


def _parse_metadata(
    rows: Iterable[tuple[object, ...]],
) -> dict[str, object]:
    found_title = False
    metadata: dict[str, object] = {}
    for row_values in rows:
        key_value = row_values[0] if row_values else None
        if not found_title:
            if str(key_value).strip() == DISTRIBUTION_METADATA_TITLE:
                found_title = True
            continue
        value = row_values[1] if len(row_values) > 1 else None
        if key_value in (None, ""):
            break
        key = str(key_value).strip()
        folded = key.casefold()
        if folded in metadata:
            raise DistributionWorkbookError(f"duplicate metadata key {key!r}")
        metadata[folded] = value
    return metadata


def _bounded_sheet_dimensions(sheet: object) -> tuple[int, int]:
    max_row = int(getattr(sheet, "max_row", 0) or 0)
    max_column = int(getattr(sheet, "max_column", 0) or 0)
    if max_row > _MAX_IMPORT_ROWS or max_column > _MAX_IMPORT_COLUMNS:
        raise DistributionWorkbookError(
            "target sheet dimensions are unreasonable: "
            f"{max_row:,} row(s) x {max_column:,} column(s); maximum supported is "
            f"{_MAX_IMPORT_ROWS:,} x {_MAX_IMPORT_COLUMNS:,}"
        )
    return max_row, max_column


def _optional_whole_metadata(
    metadata: Mapping[str, object], key: str
) -> int | None:
    value = metadata.get(key.casefold())
    if value in (None, ""):
        return None
    return _whole_number(value, label=f"metadata {key}")


def load_distribution_targets(
    path: str | Path,
    *,
    rail_ids: tuple[str, ...],
    model_ids: tuple[str, ...],
    current_present: Mapping[TargetKey, int],
    current_source_sha256: str | None = None,
    current_design_fingerprint: str | None = None,
    current_routing_asset_sha256: str | None = None,
    current_routing_asset_content_sha256: str | None = None,
) -> DistributionTargetImport:
    """Read legacy/current target matrices and merge them onto current Present.

    Workbook Present and result columns are audit evidence only.  Targets are
    absolute user intent and are the only count values applied to the scenario.
    """

    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    workbook_path = Path(path)
    try:
        workbook = load_workbook(
            workbook_path,
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except (OSError, ValueError, BadZipFile, InvalidFileException) as exc:
        raise DistributionWorkbookError(f"could not read target workbook: {exc}") from exc
    try:
        if DISTRIBUTION_TARGET_SHEET not in workbook.sheetnames:
            raise DistributionWorkbookError(
                f"workbook does not contain {DISTRIBUTION_TARGET_SHEET!r}"
            )
        sheet = workbook[DISTRIBUTION_TARGET_SHEET]
        max_row, max_column = _bounded_sheet_dimensions(sheet)
        if max_row < 1 or max_column < 1:
            raise DistributionWorkbookError("target sheet is empty")
        header_values = next(
            sheet.iter_rows(
                min_row=1,
                max_row=1,
                min_col=1,
                max_col=max_column,
                values_only=True,
            ),
            (),
        )
        first_header = header_values[0] if header_values else None
        if str(first_header).strip().casefold() != "pwr net":
            raise DistributionWorkbookError("target matrix must start at A1 with PWR NET")

        fields: dict[tuple[str, str], tuple[str, int]] = {}
        target_models: dict[str, str] = {}
        for column, header in enumerate(header_values[1:], start=2):
            if header in (None, ""):
                continue
            model_id, semantic = _header_parts(header, column=column)
            if semantic == "result":
                continue
            model_key = model_id.casefold()
            key = (model_key, semantic)
            if key in fields:
                raise DistributionWorkbookError(
                    f"duplicate {semantic} column for component {model_id!r}"
                )
            fields[key] = (model_id, column)
            if semantic == "target":
                target_models[model_key] = model_id
        if not target_models:
            raise DistributionWorkbookError("target matrix has no Target columns")
        for model_key, semantic in fields:
            if model_key not in target_models:
                model_id = fields[(model_key, semantic)][0]
                raise DistributionWorkbookError(
                    f"component {model_id!r} has {semantic} but no Target column"
                )

        raw_cells: list[_RawTargetCell] = []
        seen_rails: set[str] = set()
        last_target_row = 1
        value_max_column = max(
            column
            for (_model_key, semantic), (_model_id, column) in fields.items()
            if semantic != "result"
        )
        for row, row_values in enumerate(
            sheet.iter_rows(
                min_row=2,
                max_row=max_row,
                min_col=1,
                max_col=value_max_column,
                values_only=True,
            ),
            start=2,
        ):
            label = row_values[0] if row_values else None
            if label in (None, ""):
                break
            target_row_count = row - 1
            if target_row_count > _MAX_TARGET_ROWS:
                raise DistributionWorkbookError(
                    "target matrix has too many PWR NET rows: "
                    f"more than {_MAX_TARGET_ROWS:,}"
                )
            if target_row_count * len(target_models) > _MAX_TARGET_CELLS:
                raise DistributionWorkbookError(
                    "target matrix has too many instruction cells: "
                    f"more than {_MAX_TARGET_CELLS:,}"
                )
            rail_id = _rail_id(label, row=row)
            rail_key = rail_id.casefold()
            if rail_key in seen_rails:
                raise DistributionWorkbookError(f"duplicate rail row {rail_id!r}")
            seen_rails.add(rail_key)
            last_target_row = row
            for model_key, model_id in target_models.items():
                target_column = fields[(model_key, "target")][1]
                target = _whole_number(
                    row_values[target_column - 1]
                    if len(row_values) >= target_column
                    else None,
                    label=f"row {row} {model_id} Target",
                )
                tolerance_column = fields.get((model_key, "tolerance"))
                tolerance = _tolerance(
                    row_values[tolerance_column[1] - 1]
                    if tolerance_column is not None
                    and len(row_values) >= tolerance_column[1]
                    else None,
                    label=f"row {row} {model_id} Tolerance",
                )
                present_column = fields.get((model_key, "present"))
                present = (
                    _whole_number(
                        row_values[present_column[1] - 1]
                        if len(row_values) >= present_column[1]
                        else None,
                        label=f"row {row} {model_id} Present",
                    )
                    if present_column is not None
                    else None
                )
                raw_cells.append(
                    _RawTargetCell(
                        rail_id=rail_id,
                        model_id=model_id,
                        target=target,
                        tolerance=tolerance,
                        present=present,
                    )
                )
        if not raw_cells:
            raise DistributionWorkbookError("target matrix has no PWR NET rows")
        metadata = _parse_metadata(
            sheet.iter_rows(
                min_row=last_target_row + 1,
                max_row=max_row,
                min_col=1,
                max_col=min(2, max_column),
                values_only=True,
            )
        )
    except DistributionWorkbookError:
        raise
    except Exception as exc:
        raise DistributionWorkbookError(
            f"could not parse target workbook: {exc}"
        ) from exc
    finally:
        workbook.close()

    format_version = _optional_whole_metadata(metadata, "Format Version")
    if (
        format_version is not None
        and not 1 <= format_version <= DISTRIBUTION_WORKBOOK_FORMAT_VERSION
    ):
        raise DistributionWorkbookError(
            f"unsupported workbook format {format_version}; supported formats are "
            f"1 through {DISTRIBUTION_WORKBOOK_FORMAT_VERSION}"
        )

    raw_tolerance_semantics = metadata.get("tolerance semantics")
    tolerance_semantics = (
        str(raw_tolerance_semantics).strip().upper()
        if raw_tolerance_semantics not in (None, "")
        else None
    )
    if format_version is not None and format_version >= 5:
        if tolerance_semantics != DISTRIBUTION_TOLERANCE_SEMANTICS:
            raise DistributionWorkbookError(
                "format 5 workbook is missing or has unsupported Tolerance "
                "Semantics metadata; expected "
                f"{DISTRIBUTION_TOLERANCE_SEMANTICS}"
            )

    raw_distance = metadata.get("distance mode")
    distance_mode: str | None = None
    if raw_distance not in (None, ""):
        distance_mode = str(raw_distance).strip().upper()
        if distance_mode not in {"NEAREST", "FARTHEST"}:
            raise DistributionWorkbookError(
                f"unsupported workbook Distance Mode {raw_distance!r}"
            )

    raw_policy = metadata.get("optimization policy")
    optimization_policy = (
        str(raw_policy).strip().upper() if raw_policy not in (None, "") else None
    )
    if optimization_policy is None:
        optimization_policy = "BALANCED_AUTO"
    if optimization_policy not in {"BALANCED_AUTO", "BALANCED_CUSTOM", "MIN_GAPS"}:
        raise DistributionWorkbookError(
            f"unsupported workbook Optimization Policy {raw_policy!r}"
        )
    raw_penalty = metadata.get("effective gap penalty (um)")
    effective_gap_penalty_um: float | None = None
    if raw_penalty not in (None, ""):
        try:
            effective_gap_penalty_um = float(raw_penalty)
        except (TypeError, ValueError):
            raise DistributionWorkbookError(
                "metadata Effective Gap Penalty (um) is invalid"
            ) from None
        if (
            not isfinite(effective_gap_penalty_um)
            or effective_gap_penalty_um < 0
            or effective_gap_penalty_um > _MAX_GAP_PENALTY_UM
        ):
            raise DistributionWorkbookError(
                "metadata Effective Gap Penalty (um) must be finite and from 0 "
                f"through {_MAX_GAP_PENALTY_UM:g}"
            )
    if (
        optimization_policy == "BALANCED_CUSTOM"
        and effective_gap_penalty_um is None
    ):
        raise DistributionWorkbookError(
            "BALANCED_CUSTOM workbook is missing Effective Gap Penalty (um)"
        )
    if (
        optimization_policy == "MIN_GAPS"
        and effective_gap_penalty_um not in (None, 0.0)
    ):
        raise DistributionWorkbookError(
            "MIN_GAPS workbook Effective Gap Penalty (um) must be 0 or omitted"
        )

    raw_via_projection_policy = metadata.get("via projection policy")
    if format_version is None or format_version < DISTRIBUTION_WORKBOOK_FORMAT_VERSION:
        raise DistributionWorkbookError(
            "legacy Distribution workbook requires re-export as format 5 with "
            "source-proven target-layer transition metadata"
        )
    if raw_via_projection_policy in (None, ""):
        raise DistributionWorkbookError(
            "format 5 workbook is missing Via Projection Policy; re-export the "
            "targets after reopening the source SPD"
        )
    via_projection_policy = (
        str(raw_via_projection_policy).strip().upper()
        if raw_via_projection_policy not in (None, "")
        else DISTRIBUTION_VIA_PROJECTION_POLICY
    )
    if via_projection_policy != DISTRIBUTION_VIA_PROJECTION_POLICY:
        raise DistributionWorkbookError(
            "unsupported workbook Via Projection Policy "
            f"{raw_via_projection_policy!r}; this release requires "
            f"{DISTRIBUTION_VIA_PROJECTION_POLICY}"
        )

    raw_source_sha256 = metadata.get("source spd sha-256")
    source_sha256: str | None = None
    if raw_source_sha256 not in (None, ""):
        source_sha256 = str(raw_source_sha256).strip().lower()
        if _SHA256_RE.fullmatch(source_sha256) is None:
            raise DistributionWorkbookError("metadata Source SPD SHA-256 is invalid")
        if (
            current_source_sha256 is not None
            and source_sha256 != current_source_sha256.strip().lower()
        ):
            raise DistributionWorkbookError(
                "target workbook belongs to a different source SPD (SHA-256 mismatch)"
            )

    raw_fingerprint = metadata.get("input design fingerprint")
    input_design_fingerprint = (
        str(raw_fingerprint).strip().lower()
        if raw_fingerprint not in (None, "")
        else None
    )
    if (
        input_design_fingerprint is not None
        and _SHA256_RE.fullmatch(input_design_fingerprint) is None
    ):
        raise DistributionWorkbookError("metadata Input Design Fingerprint is invalid")

    raw_routing_enabled = metadata.get("signal routing protection")
    routing_protection_enabled = False
    routing_scope: str | None = None
    routing_clearance_um: float | None = None
    routing_policy_version: str | None = None
    routing_asset_sha256: str | None = None
    routing_asset_content_sha256: str | None = None
    if raw_routing_enabled not in (None, ""):
        enabled_text = str(raw_routing_enabled).strip().upper()
        if enabled_text not in {"ON", "OFF"}:
            raise DistributionWorkbookError(
                "metadata Signal Routing Protection must be ON or OFF"
            )
        routing_protection_enabled = enabled_text == "ON"
    elif format_version is not None and format_version >= 4:
        raise DistributionWorkbookError(
            "format 4 or newer workbook is missing Signal Routing Protection "
            "metadata"
        )
    if routing_protection_enabled:
        raw_scope = metadata.get("routing protection scope")
        routing_scope = str(raw_scope).strip().upper() if raw_scope not in (None, "") else None
        if routing_scope != "SIGNAL_NET_ONLY":
            raise DistributionWorkbookError(
                "protected workbook has unsupported or missing Routing Protection Scope"
            )
        raw_mode = metadata.get("routing clearance mode")
        if str(raw_mode).strip().upper() != "FIXED_UM":
            raise DistributionWorkbookError(
                "protected workbook must use FIXED_UM Routing Clearance Mode"
            )
        raw_clearance = metadata.get("trace-to-via clearance (um)")
        try:
            routing_clearance_um = float(raw_clearance)
        except (TypeError, ValueError):
            raise DistributionWorkbookError(
                "protected workbook has invalid Trace-to-via Clearance (um)"
            ) from None
        if not isfinite(routing_clearance_um) or routing_clearance_um < 0:
            raise DistributionWorkbookError(
                "Trace-to-via Clearance (um) must be finite and >= 0"
            )
        raw_policy_version = metadata.get("routing policy version")
        routing_policy_version = (
            str(raw_policy_version).strip()
            if raw_policy_version not in (None, "")
            else None
        )
        if not routing_policy_version:
            raise DistributionWorkbookError(
                "protected workbook is missing Routing Policy Version"
            )
        if routing_policy_version != ROUTING_POLICY_VERSION:
            raise DistributionWorkbookError(
                "protected workbook uses an unsupported Routing Policy Version"
            )
        for metadata_key, label in (
            ("routing asset sha-256", "Routing Asset SHA-256"),
            ("routing asset content sha-256", "Routing Asset Content SHA-256"),
        ):
            raw_digest = metadata.get(metadata_key)
            digest = (
                str(raw_digest).strip().lower()
                if raw_digest not in (None, "")
                else None
            )
            if digest is None or _SHA256_RE.fullmatch(digest) is None:
                raise DistributionWorkbookError(
                    f"protected workbook has invalid or missing {label}"
                )
            if metadata_key == "routing asset sha-256":
                routing_asset_sha256 = digest
            else:
                routing_asset_content_sha256 = digest
        if (
            current_routing_asset_sha256 is not None
            and routing_asset_sha256 != current_routing_asset_sha256.strip().lower()
        ):
            raise DistributionWorkbookError(
                "target workbook routing asset differs from the loaded scenario"
            )
        if (
            current_routing_asset_content_sha256 is not None
            and routing_asset_content_sha256
            != current_routing_asset_content_sha256.strip().lower()
        ):
            raise DistributionWorkbookError(
                "target workbook routing content differs from the loaded scenario"
            )

    rail_by_key = {rail_id.casefold(): rail_id for rail_id in rail_ids}
    model_by_key = {model_id.casefold(): model_id for model_id in model_ids}
    canonical_present: dict[TargetKey, int] = {
        (rail_id, model_id): int(current_present.get((rail_id, model_id), 0))
        for rail_id in rail_ids
        for model_id in model_ids
    }
    targets = dict(canonical_present)
    tolerances = {key: 0.0 for key in canonical_present}
    workbook_present: dict[TargetKey, int] = {}
    matched = 0
    ignored_neutral: list[str] = []
    unmatched_active: list[str] = []
    legacy_directional_tolerance: list[str] = []
    for cell in raw_cells:
        rail_id = rail_by_key.get(cell.rail_id.casefold())
        model_id = model_by_key.get(cell.model_id.casefold())
        if rail_id is None or model_id is None:
            active = (
                cell.present is None
                or cell.target != cell.present
                or cell.tolerance > 0.0
            )
            label = f"{cell.rail_id}/{cell.model_id}"
            if active:
                unmatched_active.append(label)
            else:
                ignored_neutral.append(label)
            continue
        key = (rail_id, model_id)
        if (
            (format_version is None or format_version < 5)
            and cell.tolerance > 0.0
            and cell.target != canonical_present[key]
        ):
            legacy_directional_tolerance.append(
                f"{cell.rail_id}/{cell.model_id}"
            )
        targets[key] = cell.target
        tolerances[key] = cell.tolerance
        if cell.present is not None:
            workbook_present[key] = cell.present
        matched += 1

    if unmatched_active:
        preview = ", ".join(unmatched_active[:8])
        if len(unmatched_active) > 8:
            preview += f", and {len(unmatched_active) - 8} more"
        raise DistributionWorkbookError(
            "active workbook target cell(s) do not exist in the loaded SPD: " + preview
        )
    if legacy_directional_tolerance:
        preview = ", ".join(legacy_directional_tolerance[:8])
        if len(legacy_directional_tolerance) > 8:
            preview += (
                f", and {len(legacy_directional_tolerance) - 8} more"
            )
        raise DistributionWorkbookError(
            "legacy workbook contains nonzero Tolerance on Target-changing "
            "cell(s): "
            + preview
            + ". Formats 1-4 did not define directional counterflow; re-export "
            "a format 5 template and explicitly re-enter these tolerances."
        )
    if matched == 0:
        raise DistributionWorkbookError(
            "no workbook target cells match the loaded SPD rails and components"
        )

    warnings: list[str] = []
    if format_version is None:
        warnings.append("Legacy workbook: run metadata was not recorded.")
    if raw_policy in (None, ""):
        warnings.append(
            "Optimization policy was not recorded; BALANCED_AUTO was restored."
        )
    if format_version is not None and format_version < 4:
        warnings.append(
            "Legacy workbook: immutable signal-routing protection was not recorded "
            "and was restored OFF."
        )
    if source_sha256 is None:
        warnings.append(
            "Workbook source identity was not recorded; rail/component IDs were "
            "validated against the loaded SPD."
        )
    if ignored_neutral:
        warnings.append(
            f"Ignored {len(ignored_neutral):,} unmatched neutral target cell(s)."
        )
    if (
        input_design_fingerprint is not None
        and current_design_fingerprint is not None
        and input_design_fingerprint != current_design_fingerprint.strip().lower()
    ):
        warnings.append(
            "The source SPD matches, but the design fingerprint differs; targets were "
            "revalidated against the current scenario."
        )

    raw_present_values = [
        cell.present for cell in raw_cells if cell.present is not None
    ]
    workbook_total = sum(raw_present_values) if raw_present_values else None
    changed_present_cells = sum(
        workbook_value != canonical_present.get(key, 0)
        for key, workbook_value in workbook_present.items()
    )
    return DistributionTargetImport(
        targets=targets,
        tolerances=tolerances,
        workbook_present=workbook_present,
        distance_mode=distance_mode,
        optimization_policy=optimization_policy,
        effective_gap_penalty_um=effective_gap_penalty_um,
        via_projection_policy=via_projection_policy,
        format_version=format_version,
        source_sha256=source_sha256,
        input_design_fingerprint=input_design_fingerprint,
        routing_protection_enabled=routing_protection_enabled,
        routing_scope=routing_scope,
        routing_clearance_um=routing_clearance_um,
        routing_policy_version=routing_policy_version,
        routing_asset_sha256=routing_asset_sha256,
        routing_asset_content_sha256=routing_asset_content_sha256,
        matched_target_cells=matched,
        defaulted_current_cells=len(canonical_present) - matched,
        ignored_neutral_cells=len(ignored_neutral),
        workbook_present_total=workbook_total,
        current_present_total=sum(canonical_present.values()),
        changed_present_cells=changed_present_cells,
        warnings=tuple(warnings),
    )


__all__ = [
    "DISTRIBUTION_LEGACY_OFF_WORKBOOK_FORMAT_VERSION",
    "DISTRIBUTION_METADATA_TITLE",
    "DISTRIBUTION_TARGET_SHEET",
    "DISTRIBUTION_TOLERANCE_SEMANTICS",
    "DISTRIBUTION_WORKBOOK_FORMAT_VERSION",
    "DISTRIBUTION_VIA_PROJECTION_POLICY",
    "DistributionTargetImport",
    "DistributionWorkbookError",
    "load_distribution_targets",
]
