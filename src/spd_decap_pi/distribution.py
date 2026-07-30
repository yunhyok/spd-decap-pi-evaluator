"""Exact, atomic De-cap Distribution planning for SPD scenarios.

The planner keeps component models and population state unchanged.  It only
relabels verified PWR connections, using a sparse mixed-integer model so a
distance-greedy choice cannot strand a shared-pad dummy or consume a donor
needed by a more constrained receiver.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from enum import StrEnum
from math import hypot, inf, isfinite
from numbers import Real
from typing import Callable, Mapping

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import coo_matrix

from ._core.domain import PinKind, TerminalKind
from .scenario import (
    DecapConnectionKind,
    DecapPadState,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioSpec,
    SHARED_PAD_ANALYSIS_VERSION,
    SharedPadCluster,
    SharedPadClusterState,
)
from .scenario_edits import (
    ScenarioEditError,
    assign_rails_and_isolation_gaps_atomic,
)


TargetKey = tuple[str, str]  # (rail_id, model_id)
ToleranceKey = TargetKey
ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


class DistributionDistanceMode(StrEnum):
    NEAREST = "NEAREST"
    FARTHEST = "FARTHEST"


class DistributionPlanStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"


class DistributionCellRole(StrEnum):
    UNCHANGED = "UNCHANGED"
    DONOR = "DONOR"
    RECEIVER = "RECEIVER"
    EXCHANGE = "EXCHANGE"


@dataclass(frozen=True, slots=True)
class DistributionDiagnostic:
    code: str
    message: str
    rail_id: str | None = None
    model_id: str | None = None
    requested_count: int | None = None
    actual_count: int | None = None


class DistributionError(ValueError):
    """Fail-closed planning or stale-plan error with UI-ready diagnostics."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostics: tuple[DistributionDiagnostic, ...] = (),
    ) -> None:
        self.code = code
        self.diagnostics = diagnostics
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DistributionCellResult:
    rail_id: str
    net: str
    model_id: str
    present_count: int
    target_count: int
    actual_count: int
    role: DistributionCellRole
    requested_count: int
    fulfilled_count: int
    shortfall_count: int
    tolerance_percent: float = 0.0
    tolerance_count: int = 0
    sent_count: int = 0
    received_count: int = 0
    sacrificed_count: int = 0

    @property
    def changed_count(self) -> int:
        """Gross participating rows, including count-neutral exchanges."""

        return self.sent_count + self.received_count + self.sacrificed_count


@dataclass(frozen=True, slots=True)
class DistributionMove:
    refdes: str
    model_id: str
    previous_rail_id: str
    previous_net: str
    new_rail_id: str
    new_net: str
    x_um: float
    y_um: float
    bump_distance_um: float


@dataclass(frozen=True, slots=True)
class DistributionSacrifice:
    """One physical decap cell removed to isolate unlike active PWR regions."""

    refdes: str
    model_id: str
    previous_rail_id: str
    previous_net: str
    x_um: float
    y_um: float


@dataclass(frozen=True, slots=True)
class DistributionExportRow:
    component: str
    refdes: str
    previous_net: str
    new_net: str
    x_um: float
    y_um: float


@dataclass(frozen=True, slots=True)
class DistributionInventoryRow:
    component: str
    export_rows: int
    physical_present: int
    assignable: int
    fixed_floating_dummy: int
    fixed_unresolved: int
    fixed_out_of_scope: int
    fixed_other: int
    disabled_or_dnp: int
    missing_or_unknown: int

    @property
    def accounted_rows(self) -> int:
        return (
            self.physical_present
            + self.disabled_or_dnp
            + self.missing_or_unknown
        )

    @property
    def reconciliation_delta(self) -> int:
        return self.export_rows - self.accounted_rows


@dataclass(frozen=True, slots=True)
class DistributionPlan:
    input_design_fingerprint: str
    input_revision: int
    output_design_fingerprint: str
    output_revision: int
    distance_mode: DistributionDistanceMode
    status: DistributionPlanStatus
    requested_count: int
    fulfilled_count: int
    shortfall_count: int
    cells: tuple[DistributionCellResult, ...]
    moves: tuple[DistributionMove, ...]
    sacrifices: tuple[DistributionSacrifice, ...]
    export_rows: tuple[DistributionExportRow, ...]
    inventory_rows: tuple[DistributionInventoryRow, ...] = ()
    diagnostics: tuple[DistributionDiagnostic, ...] = ()

    @property
    def changed_count(self) -> int:
        return len(self.moves) + len(self.sacrifices)

    @property
    def assignment_map(self) -> dict[str, str]:
        return {item.refdes: item.new_rail_id for item in self.moves}

    @property
    def isolation_gap_refdes(self) -> tuple[str, ...]:
        return tuple(item.refdes for item in self.sacrifices)


DISTRIBUTION_CSV_HEADER = (
    "Component",
    "REFDES",
    "Before NET",
    "After NET",
    "X (um)",
    "Y (um)",
)


def distribution_csv_rows(
    plan: DistributionPlan,
) -> tuple[tuple[object, object, object, object, object, object], ...]:
    """Return the requested six-column, all-decap CSV table including header."""

    body = tuple(
        (
            item.component,
            item.refdes,
            item.previous_net,
            item.new_net,
            item.x_um,
            item.y_um,
        )
        for item in plan.export_rows
    )
    return (DISTRIBUTION_CSV_HEADER, *body)


def distribution_target_table(
    plan: DistributionPlan,
) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
    """Return the immutable Present/Target/Tolerance/Actual matrix from a plan."""

    rail_order: list[tuple[str, str]] = []
    model_order: list[str] = []
    rail_keys: set[str] = set()
    model_keys: set[str] = set()
    cells: dict[tuple[str, str], DistributionCellResult] = {}
    for cell in plan.cells:
        rail_key = cell.rail_id.casefold()
        model_key = cell.model_id.casefold()
        key = (rail_key, model_key)
        if key in cells:
            raise ValueError(
                f"duplicate Distribution target cell {cell.rail_id}/{cell.model_id}"
            )
        cells[key] = cell
        if rail_key not in rail_keys:
            rail_keys.add(rail_key)
            rail_order.append((cell.rail_id, cell.net))
        if model_key not in model_keys:
            model_keys.add(model_key)
            model_order.append(cell.model_id)

    headers = ["PWR NET"]
    for model_id in model_order:
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

    rows: list[tuple[object, ...]] = []
    for rail_id, net in rail_order:
        values: list[object] = [f"{net} ({rail_id})"]
        for model_id in model_order:
            key = (rail_id.casefold(), model_id.casefold())
            cell = cells.get(key)
            if cell is None:
                raise ValueError(
                    f"missing Distribution target cell {rail_id}/{model_id}"
                )
            values.extend(
                (
                    cell.present_count,
                    cell.target_count,
                    cell.tolerance_percent,
                    cell.actual_count - cell.present_count,
                    cell.changed_count,
                    cell.sacrificed_count,
                )
            )
        rows.append(tuple(values))
    return tuple(headers), tuple(rows)


DISTRIBUTION_INVENTORY_HEADERS = (
    "Component",
    "Sheet 1 Rows",
    "Physical Present",
    "Assignable",
    "Fixed Floating Dummy",
    "Fixed Unresolved",
    "Fixed Out of Scope",
    "Fixed Other",
    "Disabled / DNP",
    "Missing / Unknown Model or Rail",
    "Reconciliation Delta",
)


def distribution_inventory_table(
    plan: DistributionPlan,
) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
    """Return the all-row reconciliation audit stored with a plan."""

    rows = tuple(
        (
            item.component,
            item.export_rows,
            item.physical_present,
            item.assignable,
            item.fixed_floating_dummy,
            item.fixed_unresolved,
            item.fixed_out_of_scope,
            item.fixed_other,
            item.disabled_or_dnp,
            item.missing_or_unknown,
            item.reconciliation_delta,
        )
        for item in plan.inventory_rows
    )
    return DISTRIBUTION_INVENTORY_HEADERS, rows


@dataclass(slots=True)
class _Variable:
    lower: float
    upper: float
    integral: int


class _MilpBuilder:
    def __init__(self) -> None:
        self.variables: list[_Variable] = []
        self.rows: list[tuple[dict[int, float], float, float]] = []

    def variable(
        self,
        *,
        lower: float = 0.0,
        upper: float = 1.0,
        integral: bool = False,
    ) -> int:
        index = len(self.variables)
        self.variables.append(_Variable(lower, upper, int(integral)))
        return index

    def constraint(
        self,
        coefficients: Mapping[int, float],
        *,
        lower: float = -inf,
        upper: float = inf,
    ) -> None:
        cleaned = {
            index: float(value)
            for index, value in coefficients.items()
            if value != 0.0
        }
        self.rows.append((cleaned, float(lower), float(upper)))

    def scipy_inputs(
        self,
        objective: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, Bounds, LinearConstraint]:
        row_indices: list[int] = []
        column_indices: list[int] = []
        values: list[float] = []
        row_lower: list[float] = []
        row_upper: list[float] = []
        for row_index, (coefficients, lower, upper) in enumerate(self.rows):
            for column_index, value in coefficients.items():
                row_indices.append(row_index)
                column_indices.append(column_index)
                values.append(value)
            row_lower.append(lower)
            row_upper.append(upper)
        matrix = coo_matrix(
            (values, (row_indices, column_indices)),
            shape=(len(self.rows), len(self.variables)),
            dtype=float,
        ).tocsc()
        return (
            objective,
            np.asarray([item.integral for item in self.variables], dtype=np.uint8),
            Bounds(
                np.asarray([item.lower for item in self.variables], dtype=float),
                np.asarray([item.upper for item in self.variables], dtype=float),
            ),
            LinearConstraint(
                matrix,
                np.asarray(row_lower, dtype=float),
                np.asarray(row_upper, dtype=float),
            ),
        )


def _notify(progress: ProgressCallback | None, percent: int, text: str) -> None:
    if progress is not None:
        progress(percent, text)


def _check_cancelled(is_cancelled: CancelCallback | None) -> None:
    if is_cancelled is not None and is_cancelled():
        raise DistributionError("CANCELLED", "De-cap Distribution was cancelled")


def _casefold_item(
    items: Mapping[str, object], key: str
) -> object | None:
    folded = key.casefold()
    return next(
        (item for raw, item in items.items() if raw.casefold() == folded),
        None,
    )


def _allowed_rail(eligibility: Mapping[str, object], rail_id: str) -> bool:
    item = _casefold_item(eligibility, rail_id)
    return bool(item is not None and getattr(item, "allowed", False))


def _canonical_targets(
    scenario: ScenarioSpec,
    targets: Mapping[TargetKey, int],
    present: Mapping[tuple[str, str], int],
) -> tuple[
    dict[tuple[str, str], int],
    dict[str, object],
    dict[str, object],
]:
    rail_by_key = {
        item.rail_id.casefold(): item for item in scenario.base_project.rails
    }
    model_by_key = {
        item.model_id.casefold(): item for item in scenario.base_project.cap_models
    }
    canonical: dict[tuple[str, str], int] = {}
    seen: set[tuple[str, str]] = set()
    for raw_key, raw_target in targets.items():
        if not isinstance(raw_key, tuple) or len(raw_key) != 2:
            raise DistributionError(
                "TARGET_KEY_INVALID",
                "distribution target keys must be (rail_id, model_id) tuples",
            )
        raw_rail_id, raw_model_id = raw_key
        rail = rail_by_key.get(str(raw_rail_id).strip().casefold())
        model = model_by_key.get(str(raw_model_id).strip().casefold())
        if rail is None:
            raise DistributionError(
                "RAIL_UNKNOWN", f"unknown PWR rail {raw_rail_id!r}"
            )
        if model is None:
            raise DistributionError(
                "MODEL_UNKNOWN", f"unknown component model {raw_model_id!r}"
            )
        key = (rail.rail_id.casefold(), model.model_id.casefold())
        if key in seen:
            raise DistributionError(
                "TARGET_DUPLICATE",
                f"duplicate distribution target for {rail.rail_id}/{model.model_id}",
            )
        seen.add(key)
        if isinstance(raw_target, bool) or not isinstance(raw_target, (int, np.integer)):
            raise DistributionError(
                "TARGET_INVALID",
                f"target for {rail.rail_id}/{model.model_id} must be an integer",
            )
        target = int(raw_target)
        if target < 0:
            raise DistributionError(
                "TARGET_INVALID",
                f"target for {rail.rail_id}/{model.model_id} cannot be negative",
            )
        canonical[key] = target

    for rail_key in rail_by_key:
        for model_key in model_by_key:
            key = (rail_key, model_key)
            canonical.setdefault(key, int(present.get(key, 0)))
    return canonical, rail_by_key, model_by_key


def _canonical_tolerances(
    tolerances: Mapping[ToleranceKey, float] | None,
    rail_by_key: Mapping[str, object],
    model_by_key: Mapping[str, object],
) -> dict[tuple[str, str], float]:
    canonical = {
        (rail_key, model_key): 0.0
        for rail_key in rail_by_key
        for model_key in model_by_key
    }
    if tolerances is None:
        return canonical

    seen: set[tuple[str, str]] = set()
    for raw_key, raw_tolerance in tolerances.items():
        if not isinstance(raw_key, tuple) or len(raw_key) != 2:
            raise DistributionError(
                "TOLERANCE_KEY_INVALID",
                "distribution tolerance keys must be (rail_id, model_id) tuples",
            )
        raw_rail_id, raw_model_id = raw_key
        rail = rail_by_key.get(str(raw_rail_id).strip().casefold())
        model = model_by_key.get(str(raw_model_id).strip().casefold())
        if rail is None:
            raise DistributionError(
                "RAIL_UNKNOWN", f"unknown PWR rail {raw_rail_id!r}"
            )
        if model is None:
            raise DistributionError(
                "MODEL_UNKNOWN", f"unknown component model {raw_model_id!r}"
            )
        key = (
            str(getattr(rail, "rail_id")).casefold(),
            str(getattr(model, "model_id")).casefold(),
        )
        if key in seen:
            raise DistributionError(
                "TOLERANCE_DUPLICATE",
                f"duplicate distribution tolerance for "
                f"{getattr(rail, 'rail_id')}/{getattr(model, 'model_id')}",
            )
        seen.add(key)
        if isinstance(raw_tolerance, bool) or not isinstance(
            raw_tolerance, (Real, Decimal)
        ):
            raise DistributionError(
                "TOLERANCE_INVALID",
                f"tolerance for {getattr(rail, 'rail_id')}/"
                f"{getattr(model, 'model_id')} must be a finite percentage",
            )
        try:
            decimal_value = Decimal(str(raw_tolerance))
        except (InvalidOperation, ValueError) as exc:
            raise DistributionError(
                "TOLERANCE_INVALID",
                f"tolerance for {getattr(rail, 'rail_id')}/"
                f"{getattr(model, 'model_id')} must be a finite percentage",
            ) from exc
        if not decimal_value.is_finite() or not Decimal("0") <= decimal_value <= Decimal("100"):
            raise DistributionError(
                "TOLERANCE_INVALID",
                f"tolerance for {getattr(rail, 'rail_id')}/"
                f"{getattr(model, 'model_id')} must be between 0 and 100 percent",
            )
        canonical[key] = float(decimal_value)
    return canonical


def distribution_tolerance_count(present: int, tolerance_percent: float) -> int:
    """Return the conservative whole-decap exchange allowance for a cell."""

    if isinstance(present, bool) or not isinstance(present, (int, np.integer)):
        raise ValueError("Present count must be a whole number")
    if present < 0:
        raise ValueError("Present count cannot be negative")
    if isinstance(tolerance_percent, bool) or not isinstance(
        tolerance_percent, (Real, Decimal)
    ):
        raise ValueError("Tolerance must be a finite percentage")
    try:
        value = Decimal(str(tolerance_percent))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Tolerance must be a finite percentage") from exc
    if not value.is_finite() or not Decimal("0") <= value <= Decimal("100"):
        raise ValueError("Tolerance must be between 0 and 100 percent")
    return int(
        (Decimal(int(present)) * value / Decimal(100)).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )


def _cell_role(
    present: int, target: int, tolerance_percent: float = 0.0
) -> DistributionCellRole:
    if target < present:
        return DistributionCellRole.DONOR
    if target > present:
        return DistributionCellRole.RECEIVER
    if tolerance_percent > 0.0:
        return DistributionCellRole.EXCHANGE
    return DistributionCellRole.UNCHANGED


@dataclass(frozen=True, slots=True)
class _DistributionInventory:
    physical: tuple[ScenarioDecap, ...]
    assignable: tuple[ScenarioDecap, ...]
    present_by_cell: dict[tuple[str, str], int]
    assignable_by_cell: dict[tuple[str, str], int]
    reconciliation_rows: tuple[DistributionInventoryRow, ...]


def _distribution_inventory(scenario: ScenarioSpec) -> _DistributionInventory:
    project = scenario.base_project
    rail_by_key = {item.rail_id.casefold(): item for item in project.rails}
    model_by_key = {item.model_id.casefold(): item for item in project.cap_models}
    connected = {item.casefold() for item in scenario.electrically_connected_refdes}
    connection_by_key = (
        {
            item.refdes.casefold(): item
            for item in scenario.connection_analysis.connections.values()
        }
        if scenario.connection_analysis is not None
        else {}
    )
    physical: list[ScenarioDecap] = []
    assignable: list[ScenarioDecap] = []
    present_by_cell: dict[tuple[str, str], int] = defaultdict(int)
    assignable_by_cell: dict[tuple[str, str], int] = defaultdict(int)
    audit: dict[str, dict[str, int | str]] = {}

    def audit_row(decap: ScenarioDecap) -> dict[str, int | str]:
        raw_model = (decap.model_id or "").strip()
        model = model_by_key.get(raw_model.casefold()) if raw_model else None
        component = model.model_id if model is not None else raw_model or "(No model)"
        key = component.casefold()
        return audit.setdefault(
            key,
            {
                "component": component,
                "export_rows": 0,
                "physical_present": 0,
                "assignable": 0,
                "fixed_floating_dummy": 0,
                "fixed_unresolved": 0,
                "fixed_out_of_scope": 0,
                "fixed_other": 0,
                "disabled_or_dnp": 0,
                "missing_or_unknown": 0,
            },
        )

    for decap in scenario.decaps:
        row = audit_row(decap)
        row["export_rows"] = int(row["export_rows"]) + 1
        if not decap.enabled:
            row["disabled_or_dnp"] = int(row["disabled_or_dnp"]) + 1
            continue
        rail = rail_by_key.get(decap.current_rail_id.casefold())
        model = (
            model_by_key.get(decap.model_id.casefold())
            if decap.model_id is not None
            else None
        )
        if rail is None or model is None:
            row["missing_or_unknown"] = int(row["missing_or_unknown"]) + 1
            continue
        physical.append(decap)
        cell = (rail.rail_id.casefold(), model.model_id.casefold())
        present_by_cell[cell] += 1
        row["physical_present"] = int(row["physical_present"]) + 1
        ref_key = decap.refdes.casefold()
        if ref_key in connected:
            assignable.append(decap)
            assignable_by_cell[cell] += 1
            row["assignable"] = int(row["assignable"]) + 1
            continue
        connection = connection_by_key.get(ref_key)
        if connection is None:
            category = "fixed_other"
        elif connection.kind == DecapConnectionKind.FLOATING_DUMMY:
            category = "fixed_floating_dummy"
        elif connection.kind == DecapConnectionKind.UNRESOLVED:
            category = "fixed_unresolved"
        elif connection.kind == DecapConnectionKind.OUT_OF_SCOPE:
            category = "fixed_out_of_scope"
        else:
            category = "fixed_other"
        row[category] = int(row[category]) + 1

    reconciliation_rows = tuple(
        DistributionInventoryRow(
            component=str(row["component"]),
            export_rows=int(row["export_rows"]),
            physical_present=int(row["physical_present"]),
            assignable=int(row["assignable"]),
            fixed_floating_dummy=int(row["fixed_floating_dummy"]),
            fixed_unresolved=int(row["fixed_unresolved"]),
            fixed_out_of_scope=int(row["fixed_out_of_scope"]),
            fixed_other=int(row["fixed_other"]),
            disabled_or_dnp=int(row["disabled_or_dnp"]),
            missing_or_unknown=int(row["missing_or_unknown"]),
        )
        for row in sorted(audit.values(), key=lambda item: str(item["component"]).casefold())
    )
    return _DistributionInventory(
        physical=tuple(physical),
        assignable=tuple(assignable),
        present_by_cell=dict(present_by_cell),
        assignable_by_cell=dict(assignable_by_cell),
        reconciliation_rows=reconciliation_rows,
    )


def distribution_present_counts(scenario: ScenarioSpec) -> dict[TargetKey, int]:
    """Return the full canonical rail×model Present matrix used by the planner."""

    project = scenario.base_project
    rail_by_key = {item.rail_id.casefold(): item for item in project.rails}
    model_by_key = {item.model_id.casefold(): item for item in project.cap_models}
    result: dict[TargetKey, int] = {
        (rail.rail_id, model.model_id): 0
        for rail in project.rails
        for model in project.cap_models
    }
    inventory = _distribution_inventory(scenario)
    for decap in inventory.physical:
        rail = rail_by_key.get(decap.current_rail_id.casefold())
        model = model_by_key.get(decap.model_id.casefold())
        if rail is None or model is None:
            continue
        key = (rail.rail_id, model.model_id)
        result[key] += 1
    return result


def _numeric_shortage_diagnostics(
    target_by_cell: Mapping[tuple[str, str], int],
    present: Mapping[tuple[str, str], int],
    model_by_key: Mapping[str, object],
    assignable_by_cell: Mapping[tuple[str, str], int],
) -> tuple[DistributionDiagnostic, ...]:
    issues: list[DistributionDiagnostic] = []
    for model_key, model in model_by_key.items():
        supply = sum(
            min(
                max(int(present.get((rail_key, model_key), 0)) - target, 0),
                int(assignable_by_cell.get((rail_key, model_key), 0)),
            )
            for (rail_key, cell_model_key), target in target_by_cell.items()
            if cell_model_key == model_key
        )
        demand = sum(
            max(target - int(present.get((rail_key, model_key), 0)), 0)
            for (rail_key, cell_model_key), target in target_by_cell.items()
            if cell_model_key == model_key
        )
        if supply < demand:
            issues.append(
                DistributionDiagnostic(
                    code="NUMERIC_SUPPLY_SHORTAGE",
                    message=(
                        f"{getattr(model, 'model_id')}: donor capacity {supply} is "
                        f"smaller than receiver demand {demand} "
                        f"(shortage {demand - supply})"
                    ),
                    model_id=str(getattr(model, "model_id")),
                    requested_count=demand,
                    actual_count=supply,
                )
            )
    return tuple(issues)


def validate_distribution_targets(
    scenario: ScenarioSpec,
    targets: Mapping[TargetKey, int],
    tolerances: Mapping[ToleranceKey, float] | None = None,
) -> None:
    """Validate targets, exchange tolerances, and hard numeric supply."""

    if scenario.connection_analysis is None:
        raise DistributionError(
            "CONNECTION_ANALYSIS_REQUIRED",
            "verified shared-pad connectivity is required for distribution",
        )
    if scenario.connection_analysis.version != SHARED_PAD_ANALYSIS_VERSION:
        raise DistributionError(
            "CONNECTION_ANALYSIS_UPGRADE_REQUIRED",
            "this scenario uses legacy shared-pad connectivity; reopen the "
            "verified source SPD to build V3 TOP-copper path evidence before "
            "running De-cap Distribution",
        )

    canonical_present = distribution_present_counts(scenario)
    inventory = _distribution_inventory(scenario)
    present = {
        (rail_id.casefold(), model_id.casefold()): count
        for (rail_id, model_id), count in canonical_present.items()
    }
    target_by_cell, rail_by_key, model_by_key = _canonical_targets(
        scenario, targets, present
    )
    _canonical_tolerances(tolerances, rail_by_key, model_by_key)
    issues = _numeric_shortage_diagnostics(
        target_by_cell,
        present,
        model_by_key,
        inventory.assignable_by_cell,
    )
    if issues:
        raise DistributionError(
            "NUMERIC_SUPPLY_SHORTAGE",
            "one or more component models do not have enough numeric donor capacity",
            diagnostics=issues,
        )


def _solve(
    builder: _MilpBuilder,
    objective: np.ndarray,
    *,
    time_limit_s: float,
) -> np.ndarray:
    c, integrality, bounds, constraints = builder.scipy_inputs(objective)
    result = milp(
        c,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={
            "presolve": True,
            "time_limit": float(time_limit_s),
            "mip_rel_gap": 0.0,
        },
    )
    if result.status != 0 or result.x is None:
        code = "OPTIMIZER_TIMEOUT" if result.status == 1 else "OPTIMIZER_FAILED"
        raise DistributionError(
            code,
            "distribution optimizer did not prove an optimal solution: "
            + str(result.message),
        )
    return np.asarray(result.x, dtype=float)


def _direct_distance_selection(
    move_variables: Mapping[int, tuple[str, str]],
    counted_by_key: Mapping[str, ScenarioDecap],
    present: Mapping[tuple[str, str], int],
    target_by_cell: Mapping[tuple[str, str], int],
    distance_by_ref_rail: Mapping[tuple[str, str], float],
    fulfilled_count: int,
    mode: DistributionDistanceMode,
    *,
    time_limit_s: float,
) -> set[int]:
    """Solve the direct-only second stage as an integral min-cost flow LP.

    Its bipartite donor-decap/receiver matrix is totally unimodular, avoiding
    branch-and-bound over thousands of otherwise independent binary choices.
    """

    if fulfilled_count == 0:
        return set()
    edges = sorted(
        move_variables.items(),
        key=lambda item: (item[1][0], item[1][1]),
    )
    edge_count = len(edges)
    objective = np.asarray(
        [
            round(distance_by_ref_rail[key] * 1000.0)
            * (1.0 if mode == DistributionDistanceMode.NEAREST else -1.0)
            for _variable, key in edges
        ],
        dtype=float,
    )

    inequality_rows: list[tuple[list[int], float]] = []
    edges_by_refdes: dict[str, list[int]] = defaultdict(list)
    edges_by_donor: dict[tuple[str, str], list[int]] = defaultdict(list)
    edges_by_receiver: dict[tuple[str, str], list[int]] = defaultdict(list)
    for edge_index, (_variable, (ref_key, rail_key)) in enumerate(edges):
        decap = counted_by_key[ref_key]
        model_key = str(decap.model_id).casefold()
        edges_by_refdes[ref_key].append(edge_index)
        edges_by_donor[(decap.current_rail_id.casefold(), model_key)].append(
            edge_index
        )
        edges_by_receiver[(rail_key, model_key)].append(edge_index)
    inequality_rows.extend((indices, 1.0) for indices in edges_by_refdes.values())
    for cell, indices in edges_by_donor.items():
        capacity = int(present.get(cell, 0)) - int(target_by_cell[cell])
        inequality_rows.append((indices, float(capacity)))
    for cell, indices in edges_by_receiver.items():
        demand = int(target_by_cell[cell]) - int(present.get(cell, 0))
        inequality_rows.append((indices, float(demand)))

    row_indices: list[int] = []
    column_indices: list[int] = []
    values: list[float] = []
    upper: list[float] = []
    for row_index, (indices, limit) in enumerate(inequality_rows):
        for edge_index in indices:
            row_indices.append(row_index)
            column_indices.append(edge_index)
            values.append(1.0)
        upper.append(limit)
    a_ub = coo_matrix(
        (values, (row_indices, column_indices)),
        shape=(len(inequality_rows), edge_count),
        dtype=float,
    ).tocsr()
    a_eq = coo_matrix(
        (
            np.ones(edge_count, dtype=float),
            (np.zeros(edge_count, dtype=int), np.arange(edge_count)),
        ),
        shape=(1, edge_count),
    ).tocsr()
    result = linprog(
        objective,
        A_ub=a_ub,
        b_ub=np.asarray(upper, dtype=float),
        A_eq=a_eq,
        b_eq=np.asarray([float(fulfilled_count)]),
        bounds=(0.0, 1.0),
        method="highs",
        options={"presolve": True, "time_limit": float(time_limit_s)},
    )
    if result.status != 0 or result.x is None:
        code = "OPTIMIZER_TIMEOUT" if result.status == 1 else "OPTIMIZER_FAILED"
        raise DistributionError(
            code,
            "direct distance optimizer did not prove an optimal solution: "
            + str(result.message),
        )
    selected_indices = {
        index for index, value in enumerate(result.x) if value > 0.5
    }
    if len(selected_indices) != fulfilled_count or any(
        1.0e-7 < value < 1.0 - 1.0e-7 for value in result.x
    ):
        raise DistributionError(
            "OPTIMIZER_FAILED",
            "direct distance optimizer returned a non-integral flow",
        )
    return {edges[index][0] for index in selected_indices}


def _direct_exchange_selection(
    move_variables: Mapping[int, tuple[str, str]],
    counted_by_key: Mapping[str, ScenarioDecap],
    present: Mapping[tuple[str, str], int],
    target_by_cell: Mapping[tuple[str, str], int],
    tolerance_count_by_cell: Mapping[tuple[str, str], int],
    role_by_cell: Mapping[tuple[str, str], DistributionCellRole],
    distance_by_ref_rail: Mapping[tuple[str, str], float],
    mode: DistributionDistanceMode,
    *,
    time_limit_s: float,
) -> tuple[set[int], int, int, bool]:
    """Solve a direct-only exchange request as an integral network flow.

    Conceptually each source cell feeds its own unit-capacity decap nodes, each
    eligible move is an arc to a destination cell, exchange cells conserve
    flow, and receiver cells drain it. Eliminating the source-to-decap arcs
    gives the sparse rows below while preserving the integral network-flow
    polytope. Fixing the two lexicographic optimum values selects faces of that
    same integral polytope, so HiGHS LP solutions remain whole assignments.
    """

    edges = sorted(
        move_variables.items(), key=lambda item: (item[1][0], item[1][1])
    )
    edge_count = len(edges)
    if edge_count == 0:
        return set(), 0, 0, False

    edges_by_refdes: dict[str, list[int]] = defaultdict(list)
    outgoing_by_cell: dict[tuple[str, str], list[int]] = defaultdict(list)
    incoming_by_cell: dict[tuple[str, str], list[int]] = defaultdict(list)
    receiver_indices: list[int] = []
    for edge_index, (_variable, (ref_key, rail_key)) in enumerate(edges):
        decap = counted_by_key[ref_key]
        model_key = str(decap.model_id).casefold()
        source_cell = (decap.current_rail_id.casefold(), model_key)
        destination_cell = (rail_key, model_key)
        edges_by_refdes[ref_key].append(edge_index)
        outgoing_by_cell[source_cell].append(edge_index)
        incoming_by_cell[destination_cell].append(edge_index)
        if role_by_cell[destination_cell] == DistributionCellRole.RECEIVER:
            receiver_indices.append(edge_index)

    inequality_rows: list[tuple[list[int], float]] = [
        (indices, 1.0) for indices in edges_by_refdes.values()
    ]
    for cell, indices in outgoing_by_cell.items():
        role = role_by_cell[cell]
        if role == DistributionCellRole.DONOR:
            capacity = int(present.get(cell, 0)) - int(target_by_cell[cell])
        elif role == DistributionCellRole.EXCHANGE:
            capacity = int(tolerance_count_by_cell[cell])
        else:  # Defensive: allowed-label construction excludes other sources.
            capacity = 0
        inequality_rows.append((indices, float(capacity)))
    for cell, indices in incoming_by_cell.items():
        role = role_by_cell[cell]
        if role == DistributionCellRole.RECEIVER:
            capacity = int(target_by_cell[cell]) - int(present.get(cell, 0))
        elif role == DistributionCellRole.EXCHANGE:
            capacity = int(tolerance_count_by_cell[cell])
        else:  # Defensive: allowed-label construction excludes other targets.
            capacity = 0
        inequality_rows.append((indices, float(capacity)))

    ub_row: list[int] = []
    ub_column: list[int] = []
    ub_value: list[float] = []
    ub_limit: list[float] = []
    for row_index, (indices, limit) in enumerate(inequality_rows):
        for edge_index in indices:
            ub_row.append(row_index)
            ub_column.append(edge_index)
            ub_value.append(1.0)
        ub_limit.append(limit)
    a_ub = coo_matrix(
        (ub_value, (ub_row, ub_column)),
        shape=(len(inequality_rows), edge_count),
        dtype=float,
    ).tocsr()
    b_ub = np.asarray(ub_limit, dtype=float)

    exchange_rows: list[tuple[dict[int, float], float]] = []
    exchange_cells = sorted(
        key
        for key, role in role_by_cell.items()
        if role == DistributionCellRole.EXCHANGE
    )
    for cell in exchange_cells:
        coefficients: dict[int, float] = {}
        for edge_index in outgoing_by_cell.get(cell, ()):
            coefficients[edge_index] = coefficients.get(edge_index, 0.0) + 1.0
        for edge_index in incoming_by_cell.get(cell, ()):
            coefficients[edge_index] = coefficients.get(edge_index, 0.0) - 1.0
        exchange_rows.append((coefficients, 0.0))

    def equality_inputs(
        extra_rows: tuple[tuple[dict[int, float], float], ...] = (),
    ) -> tuple[object | None, np.ndarray | None]:
        rows = [*exchange_rows, *extra_rows]
        if not rows:
            return None, None
        row_indices: list[int] = []
        column_indices: list[int] = []
        values: list[float] = []
        limits: list[float] = []
        for row_index, (coefficients, limit) in enumerate(rows):
            for column_index, value in coefficients.items():
                if value == 0.0:
                    continue
                row_indices.append(row_index)
                column_indices.append(column_index)
                values.append(value)
            limits.append(limit)
        matrix = coo_matrix(
            (values, (row_indices, column_indices)),
            shape=(len(rows), edge_count),
            dtype=float,
        ).tocsr()
        return matrix, np.asarray(limits, dtype=float)

    def solve_lp(
        objective: np.ndarray,
        *,
        extra_rows: tuple[tuple[dict[int, float], float], ...] = (),
    ) -> np.ndarray:
        a_eq, b_eq = equality_inputs(extra_rows)
        result = linprog(
            objective,
            A_ub=a_ub,
            b_ub=b_ub,
            A_eq=a_eq,
            b_eq=b_eq,
            bounds=(0.0, 1.0),
            method="highs",
            options={"presolve": True, "time_limit": float(time_limit_s)},
        )
        if result.status != 0 or result.x is None:
            code = "OPTIMIZER_TIMEOUT" if result.status == 1 else "OPTIMIZER_FAILED"
            raise DistributionError(
                code,
                "direct exchange optimizer did not prove an optimal solution: "
                + str(result.message),
            )
        return np.asarray(result.x, dtype=float)

    fulfillment_weight = len(counted_by_key) + 1
    primary = np.ones(edge_count, dtype=float)
    primary[receiver_indices] -= float(fulfillment_weight)
    primary_solution = solve_lp(primary)
    if any(1.0e-7 < value < 1.0 - 1.0e-7 for value in primary_solution):
        raise DistributionError(
            "OPTIMIZER_FAILED",
            "direct exchange optimizer returned a non-integral primary flow",
        )
    fulfilled_optimum = int(round(sum(primary_solution[i] for i in receiver_indices)))
    move_optimum = int(round(sum(primary_solution)))
    primary_selected = {
        edges[index][0]
        for index, value in enumerate(primary_solution)
        if value > 0.5
    }

    receiver_row = ({index: 1.0 for index in receiver_indices}, float(fulfilled_optimum))
    move_row = ({index: 1.0 for index in range(edge_count)}, float(move_optimum))
    distance_objective = np.asarray(
        [
            round(distance_by_ref_rail[key] * 1000.0)
            * (1.0 if mode == DistributionDistanceMode.NEAREST else -1.0)
            for _variable, key in edges
        ],
        dtype=float,
    )
    try:
        distance_solution = solve_lp(
            distance_objective, extra_rows=(receiver_row, move_row)
        )
    except DistributionError as exc:
        if exc.code not in {"OPTIMIZER_TIMEOUT", "OPTIMIZER_FAILED"}:
            raise
        return primary_selected, fulfilled_optimum, move_optimum, True
    if any(1.0e-7 < value < 1.0 - 1.0e-7 for value in distance_solution):
        return primary_selected, fulfilled_optimum, move_optimum, True
    selected = {
        edges[index][0]
        for index, value in enumerate(distance_solution)
        if value > 0.5
    }
    if len(selected) != move_optimum:
        return primary_selected, fulfilled_optimum, move_optimum, True
    return selected, fulfilled_optimum, move_optimum, False


def compute_distribution_plan(
    scenario: ScenarioSpec,
    targets: Mapping[TargetKey, int],
    distance_mode: DistributionDistanceMode | str = DistributionDistanceMode.NEAREST,
    *,
    tolerances: Mapping[ToleranceKey, float] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    time_limit_s: float = 120.0,
) -> DistributionPlan:
    """Compute the maximum physically valid distribution without mutating input.

    Numeric donor shortage is a hard error.  Geometry/topology shortage is a
    valid PARTIAL plan whose first optimization stage maximizes fulfilled
    receiver demand.  The second stage applies the selected bump-distance
    ordering with a deterministic canonical-rank tie break.
    """

    try:
        mode = DistributionDistanceMode(str(distance_mode).upper())
    except ValueError as exc:
        raise DistributionError(
            "DISTANCE_MODE_INVALID", f"unknown distance mode {distance_mode!r}"
        ) from exc
    if not isfinite(time_limit_s) or time_limit_s <= 0:
        raise DistributionError(
            "TIME_LIMIT_INVALID", "optimizer time limit must be positive"
        )
    if scenario.connection_analysis is None:
        raise DistributionError(
            "CONNECTION_ANALYSIS_REQUIRED",
            "verified shared-pad connectivity is required for distribution",
        )
    if scenario.connection_analysis.version != SHARED_PAD_ANALYSIS_VERSION:
        raise DistributionError(
            "CONNECTION_ANALYSIS_UPGRADE_REQUIRED",
            "this scenario uses legacy shared-pad connectivity; reopen the "
            "verified source SPD to build V3 TOP-copper path evidence before "
            "running De-cap Distribution",
        )

    _notify(progress, 2, "Validating distribution targets")
    _check_cancelled(is_cancelled)
    inventory = _distribution_inventory(scenario)
    physical = inventory.physical
    assignable = inventory.assignable
    present = dict(inventory.present_by_cell)
    target_by_cell, rail_by_key, model_by_key = _canonical_targets(
        scenario, targets, present
    )
    tolerance_by_cell = _canonical_tolerances(
        tolerances, rail_by_key, model_by_key
    )
    tolerance_count_by_cell = {
        key: (
            distribution_tolerance_count(
                int(present.get(key, 0)), tolerance_by_cell[key]
            )
            if target_by_cell[key] == int(present.get(key, 0))
            else 0
        )
        for key in target_by_cell
    }
    role_by_cell = {
        key: _cell_role(
            int(present.get(key, 0)), target, tolerance_by_cell[key]
        )
        for key, target in target_by_cell.items()
    }

    numeric_issues = _numeric_shortage_diagnostics(
        target_by_cell,
        present,
        model_by_key,
        inventory.assignable_by_cell,
    )
    if numeric_issues:
        raise DistributionError(
            "NUMERIC_SUPPLY_SHORTAGE",
            "one or more component models do not have enough numeric donor capacity",
            diagnostics=numeric_issues,
        )

    _notify(progress, 8, "Indexing exact plane and bump eligibility")
    _check_cancelled(is_cancelled)
    analysis = scenario.connection_analysis
    connection_by_refdes = {
        item.refdes.casefold(): item for item in analysis.connections.values()
    }
    cluster_by_id = {
        item.cluster_id.casefold(): item for item in analysis.clusters
    }
    cluster_by_refdes: dict[str, SharedPadCluster] = {}
    for cluster in analysis.clusters:
        for refdes in cluster.member_refdes:
            cluster_by_refdes[refdes.casefold()] = cluster

    bumps_by_rail: dict[str, tuple[object, ...]] = {}
    diagnostics: list[DistributionDiagnostic] = []
    receiver_cells = {
        key for key, role in role_by_cell.items() if role == DistributionCellRole.RECEIVER
    }
    exchange_cells = {
        key for key, role in role_by_cell.items() if role == DistributionCellRole.EXCHANGE
    }
    destination_cells = receiver_cells | exchange_cells
    destination_rail_keys = {
        rail_key for rail_key, _model_key in destination_cells
    }
    for rail_key in destination_rail_keys:
        rail = rail_by_key[rail_key]
        bumps = tuple(
            item
            for item in scenario.base_project.pins
            if item.kind == PinKind.DEVICE_BUMP
            and item.terminal == TerminalKind.PWR
            and item.net.casefold() == rail.net.casefold()
        )
        bumps_by_rail[rail_key] = bumps
        if not bumps:
            diagnostics.append(
                DistributionDiagnostic(
                    code="MISSING_TARGET_BUMP",
                    message=(
                        f"{rail.rail_id}: no PWR bump is available for distance "
                        "ranking; this destination cannot receive a decap"
                    ),
                    rail_id=rail.rail_id,
                )
            )
    for rail_key, model_key in sorted(exchange_cells):
        if tolerance_count_by_cell[(rail_key, model_key)] == 0:
            rail = rail_by_key[rail_key]
            model = model_by_key[model_key]
            diagnostics.append(
                DistributionDiagnostic(
                    code="TOLERANCE_ROUNDS_TO_ZERO",
                    message=(
                        f"{rail.rail_id}/{model.model_id}: tolerance "
                        f"{tolerance_by_cell[(rail_key, model_key)]:g}% rounds "
                        "down to 0 whole decaps, so this cell cannot exchange"
                    ),
                    rail_id=rail.rail_id,
                    model_id=model.model_id,
                    requested_count=0,
                    actual_count=0,
                )
            )

    distance_by_ref_rail: dict[tuple[str, str], float] = {}
    for decap in assignable:
        for rail_key in destination_rail_keys:
            bumps = bumps_by_rail.get(rail_key, ())
            if bumps:
                distance_by_ref_rail[(decap.refdes.casefold(), rail_key)] = min(
                    hypot(decap.x_um - bump.x_um, decap.y_um - bump.y_um)
                    for bump in bumps
                )

    builder = _MilpBuilder()
    x: dict[tuple[str, str], int] = {}
    isolation_gap: dict[str, int] = {}
    selectable_gap_variables: dict[int, str] = {}
    allowed_labels: dict[str, tuple[str, ...]] = {}
    physical_by_key = {item.refdes.casefold(): item for item in physical}
    assignable_keys = {item.refdes.casefold() for item in assignable}
    assignable_by_key = {item.refdes.casefold(): item for item in assignable}

    def shared_anchor_allows(
        connection: ScenarioDecapConnection,
        cluster: SharedPadCluster,
        rail_key: str,
    ) -> bool:
        if not connection.power_vias:
            return True
        for landing in connection.power_vias:
            eligibility = _casefold_item(
                cluster.via_eligibility, landing.via_id
            )
            if not isinstance(eligibility, Mapping) or not _allowed_rail(
                eligibility, rail_by_key[rail_key].rail_id
            ):
                return False
        return True

    for decap in scenario.decaps:
        ref_key = decap.refdes.casefold()
        current_rail_key = decap.current_rail_id.casefold()
        labels = [current_rail_key]
        if ref_key in assignable_keys:
            model_key = str(decap.model_id).casefold()
            current_cell = (current_rail_key, model_key)
            if role_by_cell[current_cell] in {
                DistributionCellRole.DONOR,
                DistributionCellRole.EXCHANGE,
            }:
                connection = connection_by_refdes[ref_key]
                for rail_key, destination_model_key in sorted(destination_cells):
                    if destination_model_key != model_key:
                        continue
                    if rail_key == current_rail_key:
                        continue
                    if not bumps_by_rail.get(rail_key):
                        continue
                    if connection.kind == DecapConnectionKind.DIRECT:
                        if not _allowed_rail(
                            decap.eligibility, rail_by_key[rail_key].rail_id
                        ):
                            continue
                    elif connection.cluster_id is not None:
                        cluster = cluster_by_id[connection.cluster_id.casefold()]
                        if not shared_anchor_allows(connection, cluster, rail_key):
                            continue
                    else:
                        continue
                    labels.append(rail_key)
        canonical_labels = tuple(dict.fromkeys(labels))
        allowed_labels[ref_key] = canonical_labels
        for rail_key in canonical_labels:
            x[(ref_key, rail_key)] = builder.variable(integral=True)
        gap_lower = gap_upper = 0.0
        if decap.pad_state == DecapPadState.ISOLATION_GAP:
            gap_lower = gap_upper = 1.0
        else:
            cluster = cluster_by_refdes.get(ref_key)
            model_key = str(decap.model_id).casefold() if decap.model_id else ""
            current_cell = (current_rail_key, model_key)
            if (
                ref_key in assignable_keys
                and cluster is not None
                and ref_key
                in {
                    item.casefold() for item in cluster.isolation_gap_refdes
                }
                and role_by_cell.get(current_cell)
                in {DistributionCellRole.DONOR, DistributionCellRole.EXCHANGE}
            ):
                gap_upper = 1.0
        gap_variable = builder.variable(
            lower=gap_lower,
            upper=gap_upper,
            integral=True,
        )
        isolation_gap[ref_key] = gap_variable
        if gap_upper > gap_lower:
            selectable_gap_variables[gap_variable] = ref_key
        builder.constraint(
            {
                **{
                    x[(ref_key, rail_key)]: 1.0
                    for rail_key in canonical_labels
                },
                gap_variable: 1.0,
            },
            lower=1.0,
            upper=1.0,
        )

    # Final component-by-rail count bounds.  Receiver equality is deliberately
    # not imposed: physical shortage must yield a maximum PARTIAL plan.
    for (rail_key, model_key), target in target_by_cell.items():
        coefficients = {
            x[(ref_key, rail_key)]: 1.0
            for ref_key, decap in physical_by_key.items()
            if decap.model_id is not None
            and decap.model_id.casefold() == model_key
            and (ref_key, rail_key) in x
        }
        role = role_by_cell[(rail_key, model_key)]
        if role == DistributionCellRole.DONOR:
            builder.constraint(coefficients, lower=float(target))
        elif role == DistributionCellRole.RECEIVER:
            builder.constraint(coefficients, upper=float(target))
        else:
            value = float(present.get((rail_key, model_key), 0))
            builder.constraint(coefficients, lower=value, upper=value)

    # An equal Present/Target cell with non-zero tolerance is a count-neutral
    # exchange node.  Its final equality above makes received == sent; this
    # bound limits the gross turnover to the conservative whole-decap allowance.
    for (rail_key, model_key) in exchange_cells:
        outgoing = {
            x[(ref_key, destination_rail_key)]: 1.0
            for ref_key, decap in assignable_by_key.items()
            if decap.model_id is not None
            and decap.model_id.casefold() == model_key
            and decap.current_rail_id.casefold() == rail_key
            for destination_rail_key in allowed_labels[ref_key]
            if destination_rail_key != rail_key
        }
        incoming = {
            x[(ref_key, rail_key)]: 1.0
            for ref_key, decap in assignable_by_key.items()
            if decap.model_id is not None
            and decap.model_id.casefold() == model_key
            and decap.current_rail_id.casefold() != rail_key
            and (ref_key, rail_key) in x
        }
        sacrificed = {
            isolation_gap[ref_key]: 1.0
            for ref_key, decap in assignable_by_key.items()
            if decap.model_id is not None
            and decap.model_id.casefold() == model_key
            and decap.current_rail_id.casefold() == rail_key
            and isolation_gap[ref_key] in selectable_gap_variables
        }
        allowance = float(tolerance_count_by_cell[(rail_key, model_key)])
        builder.constraint(
            {**outgoing, **sacrificed},
            upper=allowance,
        )
        builder.constraint(incoming, upper=allowance)

    _notify(progress, 20, "Building shared-pad anchor-flow constraints")
    _check_cancelled(is_cancelled)
    for cluster in analysis.clusters:
        if cluster.state != SharedPadClusterState.ANCHORED:
            continue
        member_keys = tuple(item.casefold() for item in cluster.member_refdes)
        member_set = set(member_keys)
        rail_keys = sorted(
            {
                rail_key
                for ref_key in member_keys
                for rail_key in allowed_labels[ref_key]
            }
        )
        connections = {
            ref_key: connection_by_refdes[ref_key] for ref_key in member_keys
        }

        # A shared physical PWR Via may belong to only one derived component.
        owners_by_via: dict[str, list[str]] = defaultdict(list)
        for ref_key, connection in connections.items():
            for landing in connection.power_vias:
                owners_by_via[landing.via_id.casefold()].append(ref_key)
        for owners in owners_by_via.values():
            if len(owners) < 2:
                continue
            first = owners[0]
            for other in owners[1:]:
                for rail_key in rail_keys:
                    coefficients: dict[int, float] = {}
                    if (first, rail_key) in x:
                        coefficients[x[(first, rail_key)]] = 1.0
                    if (other, rail_key) in x:
                        coefficients[x[(other, rail_key)]] = -1.0
                    if coefficients:
                        builder.constraint(coefficients, lower=0.0, upper=0.0)

            # Equality prevents different labels, while this extra commodity
            # prevents one duplicated physical Via from being claimed by two
            # disconnected components that happen to use the same label.
            owner_set = set(owners)
            path_capacity = float(len(owners) - 1)
            for rail_key in rail_keys:
                if (first, rail_key) not in x:
                    continue
                path_flow: dict[tuple[str, str], int] = {}
                for raw_left, raw_right in cluster.power_edges:
                    left, right = raw_left.casefold(), raw_right.casefold()
                    if (left, rail_key) not in x or (right, rail_key) not in x:
                        continue
                    for start, end in ((left, right), (right, left)):
                        variable = builder.variable(upper=path_capacity)
                        path_flow[(start, end)] = variable
                        builder.constraint(
                            {variable: 1.0, x[(start, rail_key)]: -path_capacity},
                            upper=0.0,
                        )
                        builder.constraint(
                            {variable: 1.0, x[(end, rail_key)]: -path_capacity},
                            upper=0.0,
                        )
                for ref_key in member_keys:
                    if (ref_key, rail_key) not in x:
                        continue
                    coefficients: dict[int, float] = {}
                    for (start, end), variable in path_flow.items():
                        if end == ref_key:
                            coefficients[variable] = coefficients.get(variable, 0.0) + 1.0
                        if start == ref_key:
                            coefficients[variable] = coefficients.get(variable, 0.0) - 1.0
                    if ref_key == first:
                        coefficients[x[(ref_key, rail_key)]] = float(len(owners) - 1)
                    elif ref_key in owner_set:
                        coefficients[x[(ref_key, rail_key)]] = -1.0
                    builder.constraint(coefficients, lower=0.0, upper=0.0)

        # Unlike assignments cannot coexist across a still-active physical
        # edge.  The only legal boundary between PWR NETs is a selected
        # isolation-gap vertex, whose partition forces every x value to zero.
        for raw_left, raw_right in cluster.power_edges:
            left, right = raw_left.casefold(), raw_right.casefold()
            for left_rail in allowed_labels[left]:
                for right_rail in allowed_labels[right]:
                    if left_rail == right_rail:
                        continue
                    builder.constraint(
                        {
                            x[(left, left_rail)]: 1.0,
                            x[(right, right_rail)]: 1.0,
                        },
                        upper=1.0,
                    )

        # Rooted single-commodity flow: every same-rail PWR component must
        # consume one unit per member from one or more exact Via anchors.
        capacity = float(max(len(member_keys), 1))
        for rail_key in rail_keys:
            active_members = [
                ref_key for ref_key in member_keys if (ref_key, rail_key) in x
            ]
            if not active_members:
                continue
            directed_flow: dict[tuple[str, str], int] = {}
            for raw_left, raw_right in cluster.power_edges:
                left, right = raw_left.casefold(), raw_right.casefold()
                if left not in member_set or right not in member_set:
                    continue
                if (left, rail_key) not in x or (right, rail_key) not in x:
                    continue
                for start, end in ((left, right), (right, left)):
                    variable = builder.variable(upper=capacity)
                    directed_flow[(start, end)] = variable
                    builder.constraint(
                        {variable: 1.0, x[(start, rail_key)]: -capacity},
                        upper=0.0,
                    )
                    builder.constraint(
                        {variable: 1.0, x[(end, rail_key)]: -capacity},
                        upper=0.0,
                    )
            root_flow: dict[str, int] = {}
            for ref_key in active_members:
                connection = connections[ref_key]
                if not connection.power_vias:
                    continue
                if not shared_anchor_allows(connection, cluster, rail_key):
                    continue
                variable = builder.variable(upper=capacity)
                root_flow[ref_key] = variable
                builder.constraint(
                    {variable: 1.0, x[(ref_key, rail_key)]: -capacity},
                    upper=0.0,
                )
            for ref_key in active_members:
                coefficients: dict[int, float] = {
                    x[(ref_key, rail_key)]: -1.0
                }
                if ref_key in root_flow:
                    coefficients[root_flow[ref_key]] = 1.0
                for (start, end), variable in directed_flow.items():
                    if end == ref_key:
                        coefficients[variable] = coefficients.get(variable, 0.0) + 1.0
                    if start == ref_key:
                        coefficients[variable] = coefficients.get(variable, 0.0) - 1.0
                builder.constraint(coefficients, lower=0.0, upper=0.0)

    move_variables: dict[int, tuple[str, str]] = {}
    receiver_move_variables: set[int] = set()
    for ref_key, decap in assignable_by_key.items():
        current_rail_key = decap.current_rail_id.casefold()
        for rail_key in allowed_labels[ref_key]:
            if rail_key == current_rail_key:
                continue
            destination_role = role_by_cell[
                (rail_key, decap.model_id.casefold())
            ]
            if destination_role not in {
                DistributionCellRole.RECEIVER,
                DistributionCellRole.EXCHANGE,
            }:
                continue
            variable = x[(ref_key, rail_key)]
            move_variables[variable] = (ref_key, rail_key)
            if destination_role == DistributionCellRole.RECEIVER:
                receiver_move_variables.add(variable)

    direct_only = all(
        connection_by_refdes[ref_key].kind == DecapConnectionKind.DIRECT
        for ref_key, _rail_key in move_variables.values()
    )
    selected_move_variables: set[int]
    selected_gap_variables: set[int]
    if direct_only and exchange_cells:
        _notify(
            progress,
            35,
            "Solving exact direct exchange flow and receiver fulfillment",
        )
        _check_cancelled(is_cancelled)
        (
            selected_move_variables,
            fulfilled_optimum,
            move_optimum,
            distance_fallback,
        ) = _direct_exchange_selection(
            move_variables,
            assignable_by_key,
            present,
            target_by_cell,
            tolerance_count_by_cell,
            role_by_cell,
            distance_by_ref_rail,
            mode,
            time_limit_s=time_limit_s,
        )
        selected_gap_variables = set()
        if distance_fallback:
            diagnostics.append(
                DistributionDiagnostic(
                    code="DISTANCE_OPTIMIZATION_FALLBACK",
                    message=(
                        f"maximum feasible count {fulfilled_optimum} and minimum "
                        f"turnover {move_optimum} were preserved, but the "
                        f"{mode.value.lower()} distance optimum was not proven; "
                        "the primary flow selection is shown"
                    ),
                    requested_count=fulfilled_optimum,
                    actual_count=fulfilled_optimum,
                )
            )
    else:
        _notify(
            progress,
            35,
            "Maximizing receiver demand with minimum exchange turnover",
        )
        _check_cancelled(is_cancelled)
        primary = np.zeros(len(builder.variables), dtype=float)
        # Exact lexicographic weighting: maximize receiver fulfillment, then
        # minimize sacrificed separator cells, then minimize active relabels.
        # Each higher-priority unit outweighs the full lower-priority range.
        population = max(len(assignable_by_key), 1)
        sacrifice_weight = population + 1
        fulfillment_weight = (population + 1) ** 2
        for variable in move_variables:
            primary[variable] = 1.0
        for variable in selectable_gap_variables:
            primary[variable] = float(sacrifice_weight)
        for variable in receiver_move_variables:
            primary[variable] -= float(fulfillment_weight)
        first_solution = _solve(builder, primary, time_limit_s=time_limit_s)
        fulfilled_optimum = int(
            round(
                sum(
                    first_solution[variable]
                    for variable in receiver_move_variables
                )
            )
        )
        move_optimum = int(
            round(sum(first_solution[variable] for variable in move_variables))
        )
        sacrifice_optimum = int(
            round(
                sum(
                    first_solution[variable]
                    for variable in selectable_gap_variables
                )
            )
        )
        builder.constraint(
            {variable: 1.0 for variable in receiver_move_variables},
            lower=float(fulfilled_optimum),
            upper=float(fulfilled_optimum),
        )
        builder.constraint(
            {variable: 1.0 for variable in move_variables},
            lower=float(move_optimum),
            upper=float(move_optimum),
        )
        builder.constraint(
            {variable: 1.0 for variable in selectable_gap_variables},
            lower=float(sacrifice_optimum),
            upper=float(sacrifice_optimum),
        )

        _notify(
            progress,
            65,
            f"Applying {mode.value.lower()} bump-distance ordering",
        )
        _check_cancelled(is_cancelled)
        try:
            if direct_only:
                selected_move_variables = _direct_distance_selection(
                    move_variables,
                    assignable_by_key,
                    present,
                    target_by_cell,
                    distance_by_ref_rail,
                    fulfilled_optimum,
                    mode,
                    time_limit_s=time_limit_s,
                )
            else:
                # Integer micrometre-thousandths avoid tiny floating tie terms
                # that can delay proof of a numerically marginal MIP optimum.
                secondary = np.zeros(len(builder.variables), dtype=float)
                tie_variable_count = (
                    len(move_variables) + len(selectable_gap_variables)
                )
                tie_scale = (
                    tie_variable_count * (tie_variable_count + 1) // 2 + 1
                )
                canonical_rank = 1
                for variable, (ref_key, rail_key) in sorted(
                    move_variables.items(),
                    key=lambda item: (item[1][0], item[1][1]),
                ):
                    distance_units = round(
                        distance_by_ref_rail[(ref_key, rail_key)] * 1000.0
                    )
                    secondary[variable] = (
                        float(distance_units * tie_scale + canonical_rank)
                        if mode == DistributionDistanceMode.NEAREST
                        else float(-distance_units * tie_scale + canonical_rank)
                    )
                    canonical_rank += 1
                for variable, _ref_key in sorted(
                    selectable_gap_variables.items(), key=lambda item: item[1]
                ):
                    secondary[variable] = float(canonical_rank)
                    canonical_rank += 1
                final_solution = _solve(
                    builder, secondary, time_limit_s=time_limit_s
                )
                selected_move_variables = {
                    variable
                    for variable in move_variables
                    if final_solution[variable] > 0.5
                }
                selected_gap_variables = {
                    variable
                    for variable in selectable_gap_variables
                    if final_solution[variable] > 0.5
                }
        except DistributionError as exc:
            if exc.code not in {"OPTIMIZER_TIMEOUT", "OPTIMIZER_FAILED"}:
                raise
            # The primary solve already proved maximum fulfillment and minimum
            # turnover. Preserve its valid assignment if only distance fails.
            selected_move_variables = {
                variable
                for variable in move_variables
                if first_solution[variable] > 0.5
            }
            selected_gap_variables = {
                variable
                for variable in selectable_gap_variables
                if first_solution[variable] > 0.5
            }
            diagnostics.append(
                DistributionDiagnostic(
                    code="DISTANCE_OPTIMIZATION_FALLBACK",
                    message=(
                        f"maximum feasible count {fulfilled_optimum} and minimum "
                        f"turnover {move_optimum} were preserved, but "
                        f"the {mode.value.lower()} distance optimum was not proven; "
                        "the primary feasible selection is shown"
                    ),
                    requested_count=fulfilled_optimum,
                    actual_count=fulfilled_optimum,
                )
            )

        if direct_only:
            selected_gap_variables = set()

    assignments: dict[str, str] = {}
    distance_for_move: dict[str, float] = {}
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    for variable, (ref_key, rail_key) in move_variables.items():
        if variable not in selected_move_variables:
            continue
        decap = decap_by_key[ref_key]
        assignments[decap.refdes] = rail_by_key[rail_key].rail_id
        distance_for_move[ref_key] = distance_by_ref_rail[(ref_key, rail_key)]
    selected_gap_refdes = tuple(
        decap_by_key[selectable_gap_variables[variable]].refdes
        for variable in sorted(
            selected_gap_variables,
            key=lambda item: selectable_gap_variables[item],
        )
    )

    _notify(progress, 85, "Validating the complete post-distribution scenario")
    _check_cancelled(is_cancelled)
    try:
        preview = assign_rails_and_isolation_gaps_atomic(
            scenario,
            assignments,
            selected_gap_refdes,
        )
    except ScenarioEditError as exc:
        raise DistributionError(
            "INTERNAL_PLAN_INVALID",
            "optimizer output failed final shared-pad validation: " + str(exc),
        ) from exc

    actual = dict(_distribution_inventory(preview).present_by_cell)

    sent_by_cell: dict[tuple[str, str], int] = defaultdict(int)
    received_by_cell: dict[tuple[str, str], int] = defaultdict(int)
    sacrificed_by_cell: dict[tuple[str, str], int] = defaultdict(int)
    for variable in selected_move_variables:
        ref_key, destination_rail_key = move_variables[variable]
        decap = assignable_by_key[ref_key]
        model_key = str(decap.model_id).casefold()
        sent_by_cell[(decap.current_rail_id.casefold(), model_key)] += 1
        received_by_cell[(destination_rail_key, model_key)] += 1
    for refdes in selected_gap_refdes:
        decap = decap_by_key[refdes.casefold()]
        if decap.model_id is None:
            continue
        sacrificed_by_cell[
            (decap.current_rail_id.casefold(), decap.model_id.casefold())
        ] += 1

    cells: list[DistributionCellResult] = []
    physical_shortage: list[DistributionDiagnostic] = []
    requested_total = 0
    fulfilled_total = 0
    for rail_key, rail in rail_by_key.items():
        for model_key, model in model_by_key.items():
            key = (rail_key, model_key)
            present_count = int(present.get(key, 0))
            target_count = int(target_by_cell[key])
            actual_count = int(actual.get(key, 0))
            role = role_by_cell[key]
            sent_count = int(sent_by_cell.get(key, 0))
            received_count = int(received_by_cell.get(key, 0))
            sacrificed_count = int(sacrificed_by_cell.get(key, 0))
            if role == DistributionCellRole.RECEIVER:
                requested = target_count - present_count
                fulfilled = actual_count - present_count
                shortfall = target_count - actual_count
                requested_total += requested
                fulfilled_total += fulfilled
                if shortfall:
                    physical_shortage.append(
                        DistributionDiagnostic(
                            code="PHYSICAL_CAPACITY_SHORTAGE",
                            message=(
                                f"{rail.rail_id}/{model.model_id}: requested "
                                f"{requested}, assigned {fulfilled}, shortfall {shortfall}"
                            ),
                            rail_id=rail.rail_id,
                            model_id=model.model_id,
                            requested_count=requested,
                            actual_count=fulfilled,
                        )
                    )
            elif role == DistributionCellRole.DONOR:
                requested = present_count - target_count
                fulfilled = present_count - actual_count
                shortfall = 0
            elif role == DistributionCellRole.EXCHANGE:
                requested = int(tolerance_count_by_cell[key])
                fulfilled = sent_count + sacrificed_count
                shortfall = 0
            else:
                requested = fulfilled = shortfall = 0
            cells.append(
                DistributionCellResult(
                    rail_id=rail.rail_id,
                    net=rail.net,
                    model_id=model.model_id,
                    present_count=present_count,
                    target_count=target_count,
                    actual_count=actual_count,
                    role=role,
                    requested_count=requested,
                    fulfilled_count=fulfilled,
                    shortfall_count=shortfall,
                    tolerance_percent=float(tolerance_by_cell[key]),
                    tolerance_count=int(tolerance_count_by_cell[key]),
                    sent_count=sent_count,
                    received_count=received_count,
                    sacrificed_count=sacrificed_count,
                )
            )

    move_rows: list[DistributionMove] = []
    sacrifice_rows: list[DistributionSacrifice] = []
    export_rows: list[DistributionExportRow] = []
    assignment_by_key = {
        refdes.casefold(): rail_id for refdes, rail_id in assignments.items()
    }
    new_gap_keys = {item.casefold() for item in selected_gap_refdes}
    gap_keys = {
        item.refdes.casefold()
        for item in preview.decaps
        if item.pad_state == DecapPadState.ISOLATION_GAP
    }
    for decap in scenario.decaps:
        ref_key = decap.refdes.casefold()
        new_rail_id = assignment_by_key.get(ref_key)
        new_rail = (
            rail_by_key[new_rail_id.casefold()] if new_rail_id is not None else None
        )
        new_net = (
            "UNUSED (ISOLATION GAP)"
            if ref_key in gap_keys
            else new_rail.net
            if new_rail is not None
            else decap.current_net
        )
        export_rows.append(
            DistributionExportRow(
                component=decap.model_id or "",
                refdes=decap.refdes,
                previous_net=decap.current_net,
                new_net=new_net,
                x_um=decap.x_um,
                y_um=decap.y_um,
            )
        )
        if new_rail is None:
            if ref_key in new_gap_keys:
                sacrifice_rows.append(
                    DistributionSacrifice(
                        refdes=decap.refdes,
                        model_id=decap.model_id or "",
                        previous_rail_id=decap.current_rail_id,
                        previous_net=decap.current_net,
                        x_um=decap.x_um,
                        y_um=decap.y_um,
                    )
                )
            continue
        move_rows.append(
            DistributionMove(
                refdes=decap.refdes,
                model_id=decap.model_id or "",
                previous_rail_id=decap.current_rail_id,
                previous_net=decap.current_net,
                new_rail_id=new_rail.rail_id,
                new_net=new_rail.net,
                x_um=decap.x_um,
                y_um=decap.y_um,
                bump_distance_um=distance_for_move[ref_key],
            )
        )

    shortfall_total = requested_total - fulfilled_total
    status = (
        DistributionPlanStatus.FULL
        if shortfall_total == 0
        else DistributionPlanStatus.PARTIAL
    )
    _notify(progress, 100, f"Distribution plan ready ({status.value})")
    return DistributionPlan(
        input_design_fingerprint=scenario.design_fingerprint,
        input_revision=scenario.revision,
        output_design_fingerprint=preview.design_fingerprint,
        output_revision=preview.revision,
        distance_mode=mode,
        status=status,
        requested_count=requested_total,
        fulfilled_count=fulfilled_total,
        shortfall_count=shortfall_total,
        cells=tuple(cells),
        moves=tuple(move_rows),
        sacrifices=tuple(sacrifice_rows),
        export_rows=tuple(export_rows),
        inventory_rows=inventory.reconciliation_rows,
        diagnostics=tuple((*diagnostics, *physical_shortage)),
    )


def apply_distribution_plan(
    scenario: ScenarioSpec,
    plan: DistributionPlan,
) -> ScenarioSpec:
    """Apply one verified plan once, rejecting stale or tampered inputs."""

    if (
        scenario.design_fingerprint != plan.input_design_fingerprint
        or scenario.revision != plan.input_revision
    ):
        raise DistributionError(
            "PLAN_STALE",
            "scenario changed after this distribution plan was calculated",
        )
    try:
        result = assign_rails_and_isolation_gaps_atomic(
            scenario,
            plan.assignment_map,
            plan.isolation_gap_refdes,
        )
    except ScenarioEditError as exc:
        raise DistributionError(
            "PLAN_INVALID", "distribution plan can no longer be applied: " + str(exc)
        ) from exc
    if (
        result.design_fingerprint != plan.output_design_fingerprint
        or result.revision != plan.output_revision
    ):
        raise DistributionError(
            "PLAN_TAMPERED",
            "distribution plan output identity does not match its assignments",
        )
    return result


__all__ = [
    "DISTRIBUTION_CSV_HEADER",
    "DISTRIBUTION_INVENTORY_HEADERS",
    "DistributionCellResult",
    "DistributionCellRole",
    "DistributionDiagnostic",
    "DistributionDistanceMode",
    "DistributionError",
    "DistributionExportRow",
    "DistributionInventoryRow",
    "DistributionMove",
    "DistributionPlan",
    "DistributionPlanStatus",
    "DistributionSacrifice",
    "TargetKey",
    "ToleranceKey",
    "apply_distribution_plan",
    "compute_distribution_plan",
    "distribution_present_counts",
    "distribution_csv_rows",
    "distribution_inventory_table",
    "distribution_target_table",
    "distribution_tolerance_count",
    "validate_distribution_targets",
]
