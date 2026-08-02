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
from time import monotonic
from typing import Callable, Mapping

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, OptimizeResult, linprog, milp
from scipy.sparse import coo_matrix, csr_matrix, vstack

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


def _preexisting_evaluation_blocker_diagnostic(
    scenario: ScenarioSpec,
    touched_rail_keys: set[str],
) -> DistributionDiagnostic | None:
    """Describe inherited connection evidence that still blocks rail evaluation.

    Distribution deliberately counts fixed unresolved parts while excluding them
    from donor capacity.  A count/topology plan can therefore be valid even when
    the input scenario was already unevaluable on one of the rails that the plan
    touches.  Keep that distinction explicit without weakening the evaluator's
    fail-closed landing validation.
    """

    analysis = scenario.connection_analysis
    if analysis is None or not touched_rail_keys:
        return None
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    blockers_by_rail: dict[str, list[str]] = defaultdict(list)
    blocking_kinds = {
        DecapConnectionKind.UNRESOLVED,
        DecapConnectionKind.OUT_OF_SCOPE,
    }
    for connection in analysis.connections.values():
        if connection.kind not in blocking_kinds:
            continue
        decap = decap_by_key.get(connection.refdes.casefold())
        if decap is None:
            continue
        rail_key = decap.current_rail_id.casefold()
        if rail_key in touched_rail_keys:
            blockers_by_rail[rail_key].append(decap.refdes)
    if not blockers_by_rail:
        return None

    rail_by_key = {
        item.rail_id.casefold(): item.rail_id for item in scenario.base_project.rails
    }
    detail_items: list[str] = []
    for rail_key in sorted(blockers_by_rail):
        refdes = sorted(blockers_by_rail[rail_key], key=str.casefold)
        examples = ", ".join(refdes[:2])
        if len(refdes) > 2:
            examples += f", +{len(refdes) - 2:,} more"
        detail_items.append(
            f"{rail_by_key.get(rail_key, rail_key)} ({len(refdes):,}: {examples})"
        )
    visible_details = "; ".join(detail_items[:5])
    if len(detail_items) > 5:
        visible_details += f"; +{len(detail_items) - 5:,} more rail(s)"
    blocker_count = sum(map(len, blockers_by_rail.values()))
    rail_count = len(blockers_by_rail)
    only_rail_id = (
        rail_by_key.get(next(iter(blockers_by_rail))) if rail_count == 1 else None
    )
    return DistributionDiagnostic(
        code="PREEXISTING_UNRESOLVED_EVALUATION_RAILS",
        message=(
            f"count/topology preview is valid, but {rail_count:,} touched rail(s) "
            f"contain {blocker_count:,} pre-existing unresolved or out-of-scope "
            "Decap connection(s); PDN evaluation remains blocked on those rails "
            f"until source PWR-via/plane evidence is resolved: {visible_details}"
        ),
        rail_id=only_rail_id,
        requested_count=blocker_count,
        actual_count=0,
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
            "verified source SPD to build V4 finite-pad/ordered-boolean "
            "TOP-copper evidence before "
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


def _milp_with_optional_start(
    c: np.ndarray,
    *,
    integrality: np.ndarray,
    bounds: Bounds,
    constraints: LinearConstraint | tuple[()],
    options: Mapping[str, object],
    start: np.ndarray | None = None,
) -> OptimizeResult:
    """Run HiGHS with a MIP start when SciPy's bundled API exposes it.

    ``scipy.optimize.milp`` deliberately has no ``x0`` argument.  The prior
    lexicographic stage is nevertheless a valuable feasible incumbent for the
    next stage.  Recent SciPy wheels bundle the same HiGHS API with
    ``setSolution``; use it opportunistically and fall back to the public
    wrapper on older/incompatible wheels.  Solver semantics remain identical.
    """

    if start is None or not np.any(integrality):
        return milp(
            c,
            integrality=integrality,
            bounds=bounds,
            constraints=constraints,
            options=dict(options),
        )
    try:
        from scipy.optimize._highspy import _core as highs_core

        if isinstance(constraints, LinearConstraint):
            matrix = constraints.A.tocsc()
            row_lower = np.asarray(constraints.lb, dtype=float)
            row_upper = np.asarray(constraints.ub, dtype=float)
        else:
            matrix = csr_matrix((0, len(c)), dtype=float).tocsc()
            row_lower = np.zeros(0, dtype=float)
            row_upper = np.zeros(0, dtype=float)

        lp = highs_core.HighsLp()
        lp.num_col_ = len(c)
        lp.num_row_ = matrix.shape[0]
        lp.a_matrix_.num_col_ = len(c)
        lp.a_matrix_.num_row_ = matrix.shape[0]
        lp.a_matrix_.format_ = highs_core.MatrixFormat.kColwise
        lp.col_cost_ = np.asarray(c, dtype=float)
        lp.col_lower_ = np.asarray(bounds.lb, dtype=float)
        lp.col_upper_ = np.asarray(bounds.ub, dtype=float)
        lp.row_lower_ = row_lower
        lp.row_upper_ = row_upper
        lp.a_matrix_.start_ = np.asarray(matrix.indptr, dtype=np.int32)
        lp.a_matrix_.index_ = np.asarray(matrix.indices, dtype=np.int32)
        lp.a_matrix_.value_ = np.asarray(matrix.data, dtype=float)
        lp.integrality_ = [
            highs_core.HighsVarType(int(value)) for value in integrality
        ]

        highs = highs_core._Highs()
        highs.setOptionValue("output_flag", False)
        highs.setOptionValue(
            "presolve", "on" if bool(options.get("presolve", True)) else "off"
        )
        highs.setOptionValue(
            "time_limit", float(options.get("time_limit", inf))
        )
        highs.setOptionValue(
            "mip_rel_gap", float(options.get("mip_rel_gap", 0.0))
        )
        if highs.passModel(lp) == highs_core.HighsStatus.kError:
            raise RuntimeError("HiGHS rejected the MILP model")
        start_values = np.asarray(start, dtype=float)
        start_status = highs.setSolution(
            len(start_values),
            np.arange(len(start_values), dtype=np.int32),
            start_values,
        )
        if start_status == highs_core.HighsStatus.kError:
            raise RuntimeError("HiGHS rejected the MIP start")
        highs.run()
        model_status = highs.getModelStatus()
        status = {
            highs_core.HighsModelStatus.kOptimal: 0,
            highs_core.HighsModelStatus.kTimeLimit: 1,
            highs_core.HighsModelStatus.kIterationLimit: 1,
            highs_core.HighsModelStatus.kInfeasible: 2,
            highs_core.HighsModelStatus.kUnbounded: 3,
        }.get(model_status, 4)
        solution = highs.getSolution()
        info = highs.getInfo()
        x_value = (
            np.asarray(solution.col_value, dtype=float)
            if solution.value_valid
            else None
        )
        fun_value = (
            float(info.objective_function_value)
            if x_value is not None
            else None
        )
        return OptimizeResult(
            status=status,
            success=status == 0,
            message=highs.modelStatusToString(model_status),
            x=x_value,
            fun=fun_value,
            mip_gap=(float(info.mip_gap) if x_value is not None else None),
            mip_node_count=int(info.mip_node_count),
        )
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return milp(
            c,
            integrality=integrality,
            bounds=bounds,
            constraints=constraints,
            options=dict(options),
        )


def _is_feasible_milp_start(
    start: np.ndarray,
    *,
    integrality: np.ndarray,
    bounds: Bounds,
    constraints: LinearConstraint | tuple[()],
    tolerance: float = 1.0e-6,
) -> bool:
    """Return whether a prior incumbent satisfies the current local model.

    Lazy topology cuts are appended between solves.  A solution that was
    feasible before such a cut is not automatically a valid incumbent for the
    next solve, so it must not be used by the LP-bound shortcut until it has
    been checked against the updated rows.
    """

    candidate = np.asarray(start, dtype=float)
    if candidate.ndim != 1 or candidate.shape != np.asarray(bounds.lb).shape:
        return False
    if not np.all(np.isfinite(candidate)):
        return False
    if np.any(candidate < np.asarray(bounds.lb) - tolerance) or np.any(
        candidate > np.asarray(bounds.ub) + tolerance
    ):
        return False
    integer_mask = np.isin(np.asarray(integrality), (1, 3))
    if np.any(
        np.abs(candidate[integer_mask] - np.rint(candidate[integer_mask]))
        > tolerance
    ):
        return False
    if isinstance(constraints, LinearConstraint):
        activity = np.asarray(constraints.A @ candidate, dtype=float).reshape(-1)
        if np.any(activity < np.asarray(constraints.lb) - tolerance) or np.any(
            activity > np.asarray(constraints.ub) + tolerance
        ):
            return False
    return True


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
            "verified source SPD to build V4 finite-pad/ordered-boolean "
            "TOP-copper evidence before "
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

    builder = _MilpBuilder()
    x: dict[tuple[str, str], int] = {}
    isolation_gap: dict[str, int] = {}
    selectable_gap_variables: dict[int, str] = {}
    allowed_labels: dict[str, tuple[str, ...]] = {}
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
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

    # A destination label can survive the rooted-flow constraints only inside
    # a source-pad component that already contains a PWR-via anchor compatible
    # with that rail.  Compute that necessary reachability once so via-less
    # dummy members do not receive every destination label and leave HiGHS to
    # rediscover millions of impossible states.
    shared_reachable_by_cluster_rail: dict[tuple[str, str], set[str]] = {}
    for cluster in analysis.clusters:
        if cluster.state != SharedPadClusterState.ANCHORED:
            continue
        member_keys = tuple(item.casefold() for item in cluster.member_refdes)
        adjacency: dict[str, set[str]] = {key: set() for key in member_keys}
        for raw_left, raw_right in cluster.power_edges:
            left, right = raw_left.casefold(), raw_right.casefold()
            adjacency[left].add(right)
            adjacency[right].add(left)
        for rail_key in destination_rail_keys:
            if not bumps_by_rail.get(rail_key):
                continue
            eligible: set[str] = set()
            roots: list[str] = []
            for ref_key in member_keys:
                connection = connection_by_refdes[ref_key]
                if not connection.power_vias:
                    eligible.add(ref_key)
                    continue
                if shared_anchor_allows(connection, cluster, rail_key):
                    eligible.add(ref_key)
                    roots.append(ref_key)
            reachable = set(roots)
            pending = list(roots)
            while pending:
                current = pending.pop()
                for neighbor in adjacency[current]:
                    if neighbor in eligible and neighbor not in reachable:
                        reachable.add(neighbor)
                        pending.append(neighbor)
            if reachable:
                shared_reachable_by_cluster_rail[
                    (cluster.cluster_id.casefold(), rail_key)
                ] = reachable

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
                        reachable = shared_reachable_by_cluster_rail.get(
                            (cluster.cluster_id.casefold(), rail_key), set()
                        )
                        if ref_key not in reachable:
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

    # Distance is needed only for labels that survived exact eligibility and
    # shared-anchor reachability pruning.  The previous all-decap/all-receiver
    # Cartesian product was a measurable cost on large SPD files.
    distance_by_ref_rail: dict[tuple[str, str], float] = {}
    for ref_key, decap in assignable_by_key.items():
        current_rail_key = decap.current_rail_id.casefold()
        for rail_key in allowed_labels[ref_key]:
            if rail_key == current_rail_key:
                continue
            bumps = bumps_by_rail[rail_key]
            distance_by_ref_rail[(ref_key, rail_key)] = min(
                hypot(decap.x_um - bump.x_um, decap.y_um - bump.y_um)
                for bump in bumps
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

    _notify(progress, 20, "Building shared-pad isolation constraints")
    _check_cancelled(is_cancelled)
    shared_cut_contexts: list[
        tuple[
            SharedPadCluster,
            tuple[str, ...],
            dict[str, set[str]],
            dict[str, tuple[str, ...]],
        ]
    ] = []
    for cluster in analysis.clusters:
        if cluster.state != SharedPadClusterState.ANCHORED:
            continue
        member_keys = tuple(item.casefold() for item in cluster.member_refdes)
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

        # Active endpoints must carry the same label.  In each direction only
        # the opposite endpoint's gap relaxes containment: x(left,r) can differ
        # from x(right,r) only when right itself is removed.  This is both exact
        # and markedly tighter in the LP relaxation than subtracting both gap
        # variables, while still reducing an edge from O(labels^2) to O(labels).
        for raw_left, raw_right in cluster.power_edges:
            left, right = raw_left.casefold(), raw_right.casefold()
            edge_rails = set(allowed_labels[left]).union(allowed_labels[right])
            for rail_key in edge_rails:
                forward = {
                    isolation_gap[right]: -1.0,
                }
                reverse = {isolation_gap[left]: -1.0}
                left_variable = x.get((left, rail_key))
                right_variable = x.get((right, rail_key))
                if left_variable is not None:
                    forward[left_variable] = 1.0
                    reverse[left_variable] = -1.0
                if right_variable is not None:
                    forward[right_variable] = -1.0
                    reverse[right_variable] = 1.0
                builder.constraint(forward, upper=0.0)
                builder.constraint(reverse, upper=0.0)

        adjacency: dict[str, set[str]] = {key: set() for key in member_keys}
        for raw_left, raw_right in cluster.power_edges:
            left, right = raw_left.casefold(), raw_right.casefold()
            adjacency[left].add(right)
            adjacency[right].add(left)

        # All verified clusters in the supplied design are simple paths.  On a
        # path, a selected label at node i is rooted exactly when at least one
        # route to the nearest compatible anchor on the left or right contains
        # no selected gap.  Forbid every pair of left/right blockers directly;
        # this is an exact polynomial formulation without large flow blocks or
        # repeated incumbent-only connectivity cuts.  Non-path future inputs
        # remain protected by the exact lazy validator below.
        cluster_has_selectable_gap = any(
            isolation_gap[ref_key] in selectable_gap_variables
            for ref_key in member_keys
        )
        is_path = (
            len(cluster.power_edges) == max(len(member_keys) - 1, 0)
            and all(len(adjacency[ref_key]) <= 2 for ref_key in member_keys)
        )
        path_order: list[str] = []
        if is_path and member_keys:
            start = min(
                (
                    ref_key
                    for ref_key in member_keys
                    if len(adjacency[ref_key]) <= 1
                ),
                default=member_keys[0],
            )
            previous: str | None = None
            current: str | None = start
            while current is not None:
                path_order.append(current)
                next_items = sorted(adjacency[current] - ({previous} if previous else set()))
                previous, current = (
                    current,
                    next_items[0] if next_items else None,
                )
            is_path = len(path_order) == len(member_keys)

        if cluster_has_selectable_gap and is_path:
            for rail_key in rail_keys:
                potential_run: list[str] = []
                runs: list[list[str]] = []
                for ref_key in path_order:
                    if (ref_key, rail_key) in x:
                        potential_run.append(ref_key)
                    elif potential_run:
                        runs.append(potential_run)
                        potential_run = []
                if potential_run:
                    runs.append(potential_run)

                for run in runs:
                    root_indices = [
                        index
                        for index, ref_key in enumerate(run)
                        if connections[ref_key].power_vias
                        and shared_anchor_allows(
                            connections[ref_key], cluster, rail_key
                        )
                    ]
                    root_index_set = set(root_indices)
                    for index, ref_key in enumerate(run):
                        if index in root_index_set:
                            continue
                        left_root = next(
                            (
                                root_index
                                for root_index in reversed(root_indices)
                                if root_index < index
                            ),
                            None,
                        )
                        right_root = next(
                            (
                                root_index
                                for root_index in root_indices
                                if root_index > index
                            ),
                            None,
                        )
                        left_blockers = (
                            []
                            if left_root is None
                            else [
                                isolation_gap[run[item_index]]
                                for item_index in range(left_root, index)
                                if builder.variables[
                                    isolation_gap[run[item_index]]
                                ].upper
                                > 0.0
                            ]
                        )
                        right_blockers = (
                            []
                            if right_root is None
                            else [
                                isolation_gap[run[item_index]]
                                for item_index in range(index + 1, right_root + 1)
                                if builder.variables[
                                    isolation_gap[run[item_index]]
                                ].upper
                                > 0.0
                            ]
                        )
                        # A side with an anchor and no possible blocker is
                        # always open, so this node is already guaranteed root.
                        if (
                            left_root is not None and not left_blockers
                        ) or (
                            right_root is not None and not right_blockers
                        ):
                            continue
                        node_variable = x[(ref_key, rail_key)]
                        if left_root is None and right_root is None:
                            builder.constraint({node_variable: 1.0}, upper=0.0)
                        elif left_root is None:
                            for blocker in right_blockers:
                                builder.constraint(
                                    {node_variable: 1.0, blocker: 1.0},
                                    upper=1.0,
                                )
                        elif right_root is None:
                            for blocker in left_blockers:
                                builder.constraint(
                                    {node_variable: 1.0, blocker: 1.0},
                                    upper=1.0,
                                )
                        else:
                            for left_blocker in left_blockers:
                                for right_blocker in right_blockers:
                                    builder.constraint(
                                        {
                                            node_variable: 1.0,
                                            left_blocker: 1.0,
                                            right_blocker: 1.0,
                                        },
                                        upper=2.0,
                                    )
        shared_cut_contexts.append(
            (
                cluster,
                member_keys,
                adjacency,
                {
                    via_id: tuple(owners)
                    for via_id, owners in owners_by_via.items()
                    if len(owners) > 1
                },
            )
        )

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

    connectivity_cut_signatures: set[
        tuple[str, str, tuple[str, ...], str | None]
    ] = set()

    def add_connectivity_cut(
        cluster: SharedPadCluster,
        rail_key: str,
        component: set[str],
        adjacency: Mapping[str, set[str]],
        *,
        required_refdes: str | None = None,
    ) -> bool:
        ordered_component = tuple(sorted(component))
        signature = (
            cluster.cluster_id.casefold(),
            rail_key,
            ordered_component,
            required_refdes,
        )
        if signature in connectivity_cut_signatures:
            return False
        boundary = {
            neighbor
            for ref_key in component
            for neighbor in adjacency[ref_key]
            if neighbor not in component and (neighbor, rail_key) in x
        }
        if required_refdes is None:
            # No member of this set can be a root.  Therefore selecting even
            # one of them requires at least one selected boundary vertex.  The
            # former |S|-1 cut excluded only the exact incumbent and caused
            # hundreds of near-identical cut rounds on the real design.
            coefficients = {
                x[(ref_key, rail_key)]: 1.0 for ref_key in component
            }
            for ref_key in boundary:
                variable = x[(ref_key, rail_key)]
                coefficients[variable] = (
                    coefficients.get(variable, 0.0) - float(len(component))
                )
            builder.constraint(coefficients, upper=0.0)
        else:
            # Owners of one physical Via are already constrained to one label.
            # If an owner in this component remains active, its component must
            # cross the boundary to reach the other active owners.
            coefficients = {x[(required_refdes, rail_key)]: 1.0}
            for ref_key in boundary:
                variable = x[(ref_key, rail_key)]
                coefficients[variable] = coefficients.get(variable, 0.0) - 1.0
            builder.constraint(coefficients, upper=0.0)
        connectivity_cut_signatures.add(signature)
        return True

    def add_invalid_topology_cuts(solution: np.ndarray) -> int:
        added = 0
        for cluster, member_keys, adjacency, owners_by_via in shared_cut_contexts:
            label_by_refdes: dict[str, str | None] = {}
            for ref_key in member_keys:
                if solution[isolation_gap[ref_key]] > 0.5:
                    label_by_refdes[ref_key] = None
                    continue
                selected = [
                    rail_key
                    for rail_key in allowed_labels[ref_key]
                    if solution[x[(ref_key, rail_key)]] > 0.5
                ]
                if len(selected) != 1:
                    raise DistributionError(
                        "INTERNAL_OPTIMIZER_SOLUTION",
                        f"optimizer returned an invalid label partition for "
                        f"{cluster.cluster_id}/{ref_key}",
                    )
                label_by_refdes[ref_key] = selected[0]

            components: list[set[str]] = []
            component_index_by_refdes: dict[str, int] = {}
            remaining = {
                ref_key
                for ref_key, rail_key in label_by_refdes.items()
                if rail_key is not None
            }
            while remaining:
                root = min(remaining)
                remaining.remove(root)
                rail_key = label_by_refdes[root]
                assert rail_key is not None
                component = {root}
                pending = [root]
                while pending:
                    current = pending.pop()
                    for neighbor in adjacency[current]:
                        neighbor_rail = label_by_refdes[neighbor]
                        if neighbor_rail is None:
                            continue
                        if neighbor_rail != rail_key:
                            raise DistributionError(
                                "INTERNAL_OPTIMIZER_SOLUTION",
                                f"optimizer left active unlike labels across "
                                f"{cluster.cluster_id}:{current}/{neighbor}",
                            )
                        if neighbor in remaining:
                            remaining.remove(neighbor)
                            component.add(neighbor)
                            pending.append(neighbor)
                component_index = len(components)
                components.append(component)
                for ref_key in component:
                    component_index_by_refdes[ref_key] = component_index

                has_compatible_anchor = any(
                    connection_by_refdes[ref_key].power_vias
                    and shared_anchor_allows(
                        connection_by_refdes[ref_key], cluster, rail_key
                    )
                    for ref_key in component
                )
                if not has_compatible_anchor and add_connectivity_cut(
                    cluster, rail_key, component, adjacency
                ):
                    added += 1

            # Duplicated ownership is one physical Via, so all of its active
            # owners must remain in one connected same-label component.
            for owners in owners_by_via.values():
                active_owners = [
                    ref_key
                    for ref_key in owners
                    if label_by_refdes[ref_key] is not None
                ]
                owner_components = {
                    component_index_by_refdes[ref_key]
                    for ref_key in active_owners
                }
                if len(owner_components) <= 1:
                    continue
                for component_index in owner_components:
                    component = components[component_index]
                    rail_key = label_by_refdes[next(iter(component))]
                    assert rail_key is not None
                    component_owners = sorted(
                        ref_key
                        for ref_key in active_owners
                        if component_index_by_refdes[ref_key] == component_index
                    )
                    if add_connectivity_cut(
                        cluster,
                        rail_key,
                        component,
                        adjacency,
                        required_refdes=component_owners[0],
                    ):
                        added += 1
        return added

    # Build exact independent factor-graph blocks once.  Four board/model
    # domains dominate the real 11k-decap design; solving them separately keeps
    # the same additive lexicographic optimum without forcing one huge branch
    # tree.  Tiny components are batched to avoid excessive solver startup.
    parent = list(range(len(builder.variables)))
    component_size = [1] * len(builder.variables)

    def find_root(variable: int) -> int:
        while parent[variable] != variable:
            parent[variable] = parent[parent[variable]]
            variable = parent[variable]
        return variable

    def union_variables(left: int, right: int) -> None:
        left_root = find_root(left)
        right_root = find_root(right)
        if left_root == right_root:
            return
        if component_size[left_root] < component_size[right_root]:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        component_size[left_root] += component_size[right_root]

    for coefficients, _lower, _upper in builder.rows:
        variables = tuple(coefficients)
        if not variables:
            continue
        first = variables[0]
        for variable in variables[1:]:
            union_variables(first, variable)

    raw_components: dict[int, list[int]] = defaultdict(list)
    for variable in range(len(builder.variables)):
        raw_components[find_root(variable)].append(variable)
    large_components: list[tuple[int, ...]] = []
    small_variables: list[int] = []
    for variables in raw_components.values():
        if len(variables) >= 1_000:
            large_components.append(tuple(variables))
        else:
            small_variables.extend(variables)
    solver_groups = tuple(
        sorted(large_components, key=len, reverse=True)
        + ([tuple(sorted(small_variables))] if small_variables else [])
    )
    group_by_variable: dict[int, int] = {
        variable: group_index
        for group_index, variables in enumerate(solver_groups)
        for variable in variables
    }
    stage_fallback_flags: set[str] = set()
    large_shared_problem = len(builder.variables) >= 50_000

    def solve_decomposed(
        objective: np.ndarray,
        *,
        time_limit_s: float,
        initial_solution: np.ndarray | None = None,
        feasibility_tiebreak: np.ndarray | None = None,
        feasible_fallback_stage: str | None = None,
        ignored_row_indices: set[int] | None = None,
    ) -> np.ndarray:
        if not builder.variables:
            for coefficients, lower, upper in builder.rows:
                if coefficients or not lower <= 0.0 <= upper:
                    raise DistributionError(
                        "OPTIMIZER_FAILED",
                        "distribution optimizer contains an infeasible constant row",
                    )
            return np.zeros(0, dtype=float)
        c, integrality, bounds, constraints = builder.scipy_inputs(objective)
        rows_by_group: list[list[int]] = [[] for _ in solver_groups]
        for row_index, (coefficients, lower, upper) in enumerate(builder.rows):
            if ignored_row_indices and row_index in ignored_row_indices:
                continue
            row_groups = {group_by_variable[item] for item in coefficients}
            if not row_groups:
                if not lower <= 0.0 <= upper:
                    raise DistributionError(
                        "OPTIMIZER_FAILED",
                        "distribution optimizer contains an infeasible constant row",
                    )
                continue
            if len(row_groups) != 1:
                raise DistributionError(
                    "INTERNAL_OPTIMIZER_DECOMPOSITION",
                    "distribution constraint unexpectedly spans independent solver groups",
                )
            rows_by_group[next(iter(row_groups))].append(row_index)

        solution = np.zeros(len(builder.variables), dtype=float)
        for group_index, (variables, row_indices) in enumerate(
            zip(solver_groups, rows_by_group, strict=True), start=1
        ):
            _check_cancelled(is_cancelled)
            group_deadline = monotonic() + time_limit_s
            variable_indices = np.asarray(variables, dtype=np.int64)
            row_index_array = np.asarray(row_indices, dtype=np.int64)
            local_constraints: LinearConstraint | tuple[()] = ()
            if row_indices:
                local_constraints = LinearConstraint(
                    constraints.A[row_index_array, :][:, variable_indices],
                    constraints.lb[row_index_array],
                    constraints.ub[row_index_array],
                )
            local_c = c[variable_indices]
            local_integrality = integrality[variable_indices]
            local_bounds = Bounds(
                bounds.lb[variable_indices], bounds.ub[variable_indices]
            )
            local_start = (
                None
                if initial_solution is None
                else np.asarray(initial_solution[variable_indices], dtype=float)
            )
            if local_start is not None and not _is_feasible_milp_start(
                local_start,
                integrality=local_integrality,
                bounds=local_bounds,
                constraints=local_constraints,
            ):
                local_start = None
            local_feasibility_tiebreak = (
                np.zeros_like(local_c)
                if feasibility_tiebreak is None
                else np.asarray(
                    feasibility_tiebreak[variable_indices], dtype=float
                )
            )

            def remaining_time() -> float:
                return max(group_deadline - monotonic(), 0.0)

            def run_milp(
                run_c: np.ndarray,
                run_integrality: np.ndarray,
                run_constraints: LinearConstraint | tuple[()],
                *,
                limit: float,
                start: np.ndarray | None = None,
            ):
                return _milp_with_optional_start(
                    run_c,
                    integrality=run_integrality,
                    bounds=local_bounds,
                    constraints=run_constraints,
                    options={
                        "presolve": True,
                        "time_limit": float(max(limit, 1.0e-3)),
                        "mip_rel_gap": 0.0,
                    },
                    start=start,
                )

            result = None
            nonzero_objective = local_c[local_c != 0.0]
            unit_integer_objective = bool(nonzero_objective.size) and bool(
                np.all(np.abs(nonzero_objective) == 1.0)
            )
            # For the count-valued lexicographic stages, first solve the LP.
            # Its ceiling is a rigorous integer lower bound.  Testing that
            # single objective level as a feasibility MIP avoids a very large
            # branch-and-bound search when the relaxation is already exact;
            # this is the dominant speedup on the supplied 11k-decap design.
            if unit_integer_objective:
                lp_limit = min(
                    remaining_time(),
                    max(1.0, min(30.0, time_limit_s * 0.25)),
                )
                lp_result = run_milp(
                    local_c,
                    np.zeros_like(local_integrality),
                    local_constraints,
                    limit=lp_limit,
                )
                if (
                    lp_result.status == 0
                    and lp_result.fun is not None
                    and isfinite(float(lp_result.fun))
                    and remaining_time() > 0.0
                ):
                    integer_bound = int(
                        np.ceil(float(lp_result.fun) - 1.0e-7)
                    )
                    if isinstance(local_constraints, LinearConstraint):
                        forced_matrix = vstack(
                            (
                                local_constraints.A,
                                csr_matrix(local_c.reshape(1, -1)),
                            ),
                            format="csc",
                        )
                        forced_lower = np.append(
                            local_constraints.lb, float(integer_bound)
                        )
                        forced_upper = np.append(
                            local_constraints.ub, float(integer_bound)
                        )
                    else:
                        forced_matrix = csr_matrix(local_c.reshape(1, -1))
                        forced_lower = np.asarray(
                            [float(integer_bound)], dtype=float
                        )
                        forced_upper = np.asarray(
                            [float(integer_bound)], dtype=float
                        )
                    incumbent_bound: int | None = None
                    if local_start is not None:
                        start_objective = float(np.dot(local_c, local_start))
                        rounded_start = int(round(start_objective))
                        if abs(start_objective - rounded_start) <= 1.0e-6:
                            incumbent_bound = rounded_start

                    level_probes = 0
                    while remaining_time() > 0.0 and level_probes < 32:
                        if (
                            incumbent_bound is not None
                            and integer_bound >= incumbent_bound
                        ):
                            result = OptimizeResult(
                                status=0,
                                success=True,
                                message="prior lexicographic incumbent is optimal",
                                x=local_start,
                                fun=float(incumbent_bound),
                            )
                            break
                        forced_lower[-1] = float(integer_bound)
                        forced_upper[-1] = float(integer_bound)
                        forced_constraints = LinearConstraint(
                            forced_matrix, forced_lower, forced_upper
                        )
                        forced_result = run_milp(
                            local_feasibility_tiebreak,
                            local_integrality,
                            forced_constraints,
                            limit=min(remaining_time(), 5.0),
                        )
                        # At a fixed objective level, any feasible incumbent
                        # proves that level is attainable even if HiGHS reached
                        # its time limit before reporting a zero-objective proof.
                        if forced_result.x is not None and forced_result.status in {
                            0,
                            1,
                        }:
                            result = OptimizeResult(
                                status=0,
                                success=True,
                                message=(
                                    "feasible at the proven primary objective "
                                    "level"
                                ),
                                x=np.asarray(forced_result.x, dtype=float),
                                fun=float(
                                    np.dot(
                                        local_feasibility_tiebreak,
                                        forced_result.x,
                                    )
                                ),
                            )
                            break
                        if forced_result.status != 2:
                            break
                        integer_bound += 1
                        level_probes += 1

                    if result is None and remaining_time() > 0.0:
                        # Every rejected equality level is a proof that the
                        # integer objective is at least the next value.  Keep
                        # that bound in the ordinary exact-MIP fallback.
                        forced_lower[-1] = float(integer_bound)
                        forced_upper[-1] = inf
                        local_constraints = LinearConstraint(
                            forced_matrix, forced_lower, forced_upper
                        )

            if result is None and remaining_time() > 0.0:
                result = run_milp(
                    local_c,
                    local_integrality,
                    local_constraints,
                    limit=remaining_time(),
                    start=local_start,
                )
            if result is None:
                raise DistributionError(
                    "OPTIMIZER_TIMEOUT",
                    f"distribution optimizer block {group_index}/"
                    f"{len(solver_groups)} exhausted its time limit",
                )
            if (
                result.status == 1
                and result.x is not None
                and feasible_fallback_stage is not None
            ):
                stage_fallback_flags.add(feasible_fallback_stage)
            elif result.status != 0 or result.x is None:
                code = "OPTIMIZER_TIMEOUT" if result.status == 1 else "OPTIMIZER_FAILED"
                raise DistributionError(
                    code,
                    f"distribution optimizer block {group_index}/{len(solver_groups)} "
                    f"did not prove an optimal solution: {result.message}",
                )
            solution[variable_indices] = result.x
        return solution

    def constrain_group_totals(
        variables: set[int] | dict[int, tuple[str, str]],
        solution: np.ndarray,
    ) -> set[int]:
        by_group: dict[int, dict[int, float]] = defaultdict(dict)
        for variable in variables:
            by_group[group_by_variable[variable]][variable] = 1.0
        row_indices: set[int] = set()
        for coefficients in by_group.values():
            optimum = float(
                round(sum(solution[variable] for variable in coefficients))
            )
            row_indices.add(len(builder.rows))
            builder.constraint(coefficients, lower=optimum, upper=optimum)
        return row_indices

    def solve_with_topology_cuts(
        objective: np.ndarray,
        *,
        progress_percent: int,
        stage: str,
        initial_solution: np.ndarray | None = None,
        feasibility_tiebreak: np.ndarray | None = None,
        solver_time_limit_s: float | None = None,
        feasible_fallback_stage: str | None = None,
        ignored_row_indices: set[int] | None = None,
    ) -> np.ndarray:
        while True:
            _check_cancelled(is_cancelled)
            solution = solve_decomposed(
                objective,
                time_limit_s=(
                    time_limit_s
                    if solver_time_limit_s is None
                    else solver_time_limit_s
                ),
                initial_solution=initial_solution,
                feasibility_tiebreak=feasibility_tiebreak,
                feasible_fallback_stage=feasible_fallback_stage,
                ignored_row_indices=ignored_row_indices,
            )
            added = add_invalid_topology_cuts(solution)
            if added == 0:
                return solution
            initial_solution = solution
            _notify(
                progress,
                progress_percent,
                f"{stage} ({len(connectivity_cut_signatures):,} topology cuts)",
            )

    direct_only = all(
        connection_by_refdes[ref_key].kind == DecapConnectionKind.DIRECT
        for ref_key, _rail_key in move_variables.values()
    )
    selected_move_variables: set[int]
    selected_gap_variables: set[int]
    use_direct_flow = direct_only and (
        bool(exchange_cells) or len(move_variables) >= 1_000
    )
    if use_direct_flow:
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
            "Maximizing receiver demand",
        )
        _check_cancelled(is_cancelled)
        # Keep the priorities as explicit exact stages.  The former single
        # weighted objective was mathematically lexicographic, but its roughly
        # population-squared coefficient range made large shared-pad models
        # numerically difficult before HiGHS could find even a primal point.
        fulfillment_objective = np.zeros(len(builder.variables), dtype=float)
        for variable in receiver_move_variables:
            fulfillment_objective[variable] = -1.0
        gap_tiebreak = np.zeros(len(builder.variables), dtype=float)
        for variable in selectable_gap_variables:
            gap_tiebreak[variable] = 1.0
        first_solution = solve_with_topology_cuts(
            fulfillment_objective,
            progress_percent=35,
            stage="Maximizing receiver demand",
            feasibility_tiebreak=gap_tiebreak,
        )
        fulfilled_optimum = int(
            round(
                sum(
                    first_solution[variable]
                    for variable in receiver_move_variables
                )
            )
        )
        constrain_group_totals(receiver_move_variables, first_solution)

        provisional_gap_count = int(
            round(
                sum(
                    first_solution[variable]
                    for variable in selectable_gap_variables
                )
            )
        )
        _notify(
            progress,
            50,
            "Minimizing isolation-gap sacrifices "
            f"(provisional {provisional_gap_count:,})",
        )
        _check_cancelled(is_cancelled)
        if selectable_gap_variables:
            gap_objective = np.zeros(len(builder.variables), dtype=float)
            for variable in selectable_gap_variables:
                gap_objective[variable] = 1.0
            first_solution = solve_with_topology_cuts(
                gap_objective,
                progress_percent=50,
                stage="Minimizing isolation-gap sacrifices",
                initial_solution=first_solution,
                solver_time_limit_s=min(
                    time_limit_s, 5.0 if large_shared_problem else 30.0
                ),
                feasible_fallback_stage="gap",
            )
            sacrifice_optimum = int(
                round(
                    sum(
                        first_solution[variable]
                        for variable in selectable_gap_variables
                    )
                )
            )
        else:
            sacrifice_optimum = 0
        if "gap" in stage_fallback_flags:
            diagnostics.append(
                DistributionDiagnostic(
                    code="GAP_OPTIMIZATION_FALLBACK",
                    message=(
                        "maximum receiver fulfillment and all shared-pad safety "
                        "rules were preserved, but minimum isolation-gap count "
                        "was not proven within the optimization time limit; the "
                        "best valid incumbent is shown"
                    ),
                    requested_count=provisional_gap_count,
                    actual_count=sacrifice_optimum,
                )
            )
        gap_total_row_indices = constrain_group_totals(
            selectable_gap_variables, first_solution
        )

        _notify(progress, 62, "Minimizing active PWR NET relabels")
        _check_cancelled(is_cancelled)
        if move_variables:
            move_objective = np.zeros(len(builder.variables), dtype=float)
            for variable in move_variables:
                move_objective[variable] = 1.0
            first_solution = solve_with_topology_cuts(
                move_objective,
                progress_percent=62,
                stage="Minimizing active PWR NET relabels",
                initial_solution=first_solution,
            )
            move_optimum = int(
                round(
                    sum(first_solution[variable] for variable in move_variables)
                )
            )
        else:
            move_optimum = 0
        constrain_group_totals(move_variables, first_solution)

        _notify(
            progress,
            75,
            f"Applying {mode.value.lower()} bump-distance ordering",
        )
        _check_cancelled(is_cancelled)
        joint_distance_proven = False
        joint_distance_applied = False
        secondary: np.ndarray | None = None
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
                joint_distance_proven = True
                joint_distance_applied = True
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
                final_solution = solve_with_topology_cuts(
                    secondary,
                    progress_percent=75,
                    stage=f"Applying {mode.value.lower()} bump-distance ordering",
                    initial_solution=first_solution,
                    solver_time_limit_s=min(
                        time_limit_s, 10.0 if large_shared_problem else 30.0
                    ),
                    feasible_fallback_stage="distance_joint",
                )
                # A time-limited solver can return a valid distance incumbent.
                # Keep the better of that incumbent and the lexicographic start
                # explicitly because SciPy's public MILP fallback cannot accept
                # the start on every supported wheel.
                if float(np.dot(secondary, first_solution)) < float(
                    np.dot(secondary, final_solution)
                ):
                    final_solution = first_solution
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
                joint_distance_proven = (
                    "distance_joint" not in stage_fallback_flags
                )
                joint_distance_applied = True
                if not joint_distance_proven:
                    diagnostics.append(
                        DistributionDiagnostic(
                            code="DISTANCE_JOINT_OPTIMIZATION_FALLBACK",
                            message=(
                                "the joint assignment/separator distance "
                                f"optimum was not proven; the best valid "
                                f"{mode.value.lower()} distance incumbent is "
                                "carried into fixed-separator refinement"
                            ),
                            requested_count=fulfilled_optimum,
                            actual_count=fulfilled_optimum,
                        )
                    )
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
            if direct_only:
                diagnostics.append(
                    DistributionDiagnostic(
                        code="DISTANCE_OPTIMIZATION_FALLBACK",
                        message=(
                            f"maximum feasible count {fulfilled_optimum} and "
                            f"minimum turnover {move_optimum} were preserved, "
                            f"but the {mode.value.lower()} distance optimum was "
                            "not proven; the primary feasible selection is shown"
                        ),
                        requested_count=fulfilled_optimum,
                        actual_count=fulfilled_optimum,
                    )
                )
            else:
                diagnostics.append(
                    DistributionDiagnostic(
                        code="DISTANCE_JOINT_OPTIMIZATION_DEFERRED",
                        message=(
                            "the joint assignment/separator distance solve did "
                            "not return a valid incumbent; distance ordering is "
                            "deferred until the exact separator positions are "
                            "fixed"
                        ),
                        requested_count=fulfilled_optimum,
                        actual_count=fulfilled_optimum,
                    )
                )

        if direct_only:
            selected_gap_variables = set()

        # The first gap stage must search over both the assignment and gap
        # choices, and can therefore time out with a safe but over-sacrificed
        # incumbent on a large board.  Once the move set is fixed, re-open only
        # the original-label/gap choice at every source-authorized pad and
        # minimize the conservative MILP separator set.  Ignoring the earlier
        # per-block gap-total rows permits both removal and relocation of
        # separators while all inventory, shared-pad rooting, and shared-Via
        # constraints remain active.  A later atomic-validator-backed pass can
        # still restore pads that this conservative graph model retained.
        if selectable_gap_variables:
            _notify(
                progress,
                82,
                "Refining minimum separator pads for the fixed assignment",
            )
            _check_cancelled(is_cancelled)
            original_gap_variables = set(selected_gap_variables)
            refinement_assignment_row_indices: set[int] = set()

            def constrain_refinement_assignment(
                coefficients: Mapping[int, float],
                *,
                lower: float = -inf,
                upper: float = inf,
            ) -> None:
                refinement_assignment_row_indices.add(len(builder.rows))
                builder.constraint(coefficients, lower=lower, upper=upper)

            fixed_move_rail_by_refdes = {
                ref_key: rail_key
                for variable, (ref_key, rail_key) in move_variables.items()
                if variable in selected_move_variables
            }
            refinement_start = np.zeros(len(builder.variables), dtype=float)
            for ref_key, labels in allowed_labels.items():
                selected_rail_key = fixed_move_rail_by_refdes.get(
                    ref_key, decap_by_key[ref_key].current_rail_id.casefold()
                )
                gap_variable = isolation_gap[ref_key]
                gap_selected = gap_variable in original_gap_variables or (
                    decap_by_key[ref_key].pad_state
                    == DecapPadState.ISOLATION_GAP
                )
                refinement_start[gap_variable] = float(gap_selected)
                for rail_key in labels:
                    variable = x[(ref_key, rail_key)]
                    refinement_start[variable] = float(
                        not gap_selected and rail_key == selected_rail_key
                    )

                if ref_key in fixed_move_rail_by_refdes:
                    constrain_refinement_assignment(
                        {gap_variable: 1.0}, lower=0.0, upper=0.0
                    )
                    for rail_key in labels:
                        value = float(rail_key == selected_rail_key)
                        constrain_refinement_assignment(
                            {x[(ref_key, rail_key)]: 1.0},
                            lower=value,
                            upper=value,
                        )
                elif (
                    decap_by_key[ref_key].pad_state
                    == DecapPadState.ISOLATION_GAP
                ):
                    for rail_key in labels:
                        constrain_refinement_assignment(
                            {x[(ref_key, rail_key)]: 1.0},
                            lower=0.0,
                            upper=0.0,
                        )
                elif gap_variable in selectable_gap_variables:
                    current_rail_key = decap_by_key[
                        ref_key
                    ].current_rail_id.casefold()
                    for rail_key in labels:
                        if rail_key != current_rail_key:
                            constrain_refinement_assignment(
                                {x[(ref_key, rail_key)]: 1.0},
                                lower=0.0,
                                upper=0.0,
                            )
                else:
                    for rail_key in labels:
                        value = float(rail_key == selected_rail_key)
                        constrain_refinement_assignment(
                            {x[(ref_key, rail_key)]: 1.0},
                            lower=value,
                            upper=value,
                        )

            refinement_objective = np.zeros(
                len(builder.variables), dtype=float
            )
            refinement_tiebreak = np.zeros(
                len(builder.variables), dtype=float
            )
            for variable in selectable_gap_variables:
                refinement_objective[variable] = 1.0
                # Prefer retaining an already selected physical split when the
                # exact minimum cardinality has multiple equivalent locations.
                refinement_tiebreak[variable] = float(
                    variable not in original_gap_variables
                )
            try:
                refined_solution = solve_with_topology_cuts(
                    refinement_objective,
                    progress_percent=82,
                    stage=(
                        "Refining minimum separator pads for the fixed "
                        "assignment"
                    ),
                    initial_solution=refinement_start,
                    feasibility_tiebreak=refinement_tiebreak,
                    solver_time_limit_s=min(time_limit_s, 30.0),
                    ignored_row_indices=gap_total_row_indices,
                )
            except DistributionError as exc:
                if exc.code not in {"OPTIMIZER_TIMEOUT", "OPTIMIZER_FAILED"}:
                    raise
                diagnostics.append(
                    DistributionDiagnostic(
                        code="GAP_REFINEMENT_FALLBACK",
                        message=(
                            "the fixed-assignment minimum separator count was "
                            "not proven; the previously validated gap set is "
                            "preserved"
                        ),
                        requested_count=len(original_gap_variables),
                        actual_count=len(original_gap_variables),
                    )
                )
            else:
                selected_gap_variables = {
                    variable
                    for variable in selectable_gap_variables
                    if refined_solution[variable] > 0.5
                }
                if len(selected_gap_variables) < len(original_gap_variables):
                    diagnostics.append(
                        DistributionDiagnostic(
                            code="FIXED_ASSIGNMENT_GAPS_REFINED",
                            message=(
                                "the fixed move assignment was preserved while "
                                "the conservative MILP separator set was "
                                "reduced; an atomic safety pass will restore "
                                "any remaining pads that do not separate "
                                "different PWR NET contexts"
                            ),
                            requested_count=len(original_gap_variables),
                            actual_count=len(selected_gap_variables),
                        )
                    )

                # Gap refinement deliberately froze the earlier assignment so
                # it could minimize separators in a much smaller fixed-
                # assignment model.
                # That assignment must not become the final answer: fix only
                # the resulting physical separator positions, remove both the
                # provisional per-block gap totals and temporary assignment
                # rows, and optimize NEAREST/FARTHEST again while retaining the
                # already constrained fulfillment and minimum move totals.
                # A direct-only assignment has already been optimized by the
                # exact flow solver.  Selectable gaps can still exist in an
                # unrelated shared cluster with no eligible move, so only run
                # this MILP refinement when a shared distance objective exists.
                if secondary is not None:
                    for variable in selectable_gap_variables:
                        value = float(variable in selected_gap_variables)
                        builder.constraint(
                            {variable: 1.0}, lower=value, upper=value
                        )
                    ignored_distance_rows = (
                        gap_total_row_indices
                        | refinement_assignment_row_indices
                    )
                    _notify(
                        progress,
                        87,
                        "Applying bump-distance ordering with fixed separators",
                    )
                    _check_cancelled(is_cancelled)
                    try:
                        fixed_separator_solution = solve_with_topology_cuts(
                            secondary,
                            progress_percent=87,
                            stage=(
                                "Applying bump-distance ordering with fixed "
                                "separators"
                            ),
                            initial_solution=refined_solution,
                            solver_time_limit_s=min(
                                time_limit_s,
                                10.0 if large_shared_problem else 30.0,
                            ),
                            feasible_fallback_stage=(
                                "distance_fixed_separator"
                            ),
                            ignored_row_indices=ignored_distance_rows,
                        )
                    except DistributionError as exc:
                        if exc.code not in {
                            "OPTIMIZER_TIMEOUT",
                            "OPTIMIZER_FAILED",
                        }:
                            raise
                        # Returning the pre-distance assignment here would
                        # silently violate the requested NEAREST/FARTHEST
                        # policy. Fail closed when no valid incumbent exists.
                        raise DistributionError(
                            "DISTANCE_OPTIMIZER_FAILED",
                            "fixed-separator distance optimization did not "
                            "return a valid incumbent; no arbitrary assignment "
                            "was emitted",
                            diagnostics=tuple(diagnostics),
                        ) from exc

                    # The refined assignment is a valid incumbent under the
                    # fixed separators. Preserve it if a public-API timeout
                    # returned a worse solution, otherwise use the best
                    # mode-specific result.
                    if float(np.dot(secondary, refined_solution)) < float(
                        np.dot(secondary, fixed_separator_solution)
                    ):
                        fixed_separator_solution = refined_solution
                    selected_move_variables = {
                        variable
                        for variable in move_variables
                        if fixed_separator_solution[variable] > 0.5
                    }
                    selected_gap_variables = {
                        variable
                        for variable in selectable_gap_variables
                        if fixed_separator_solution[variable] > 0.5
                    }
                    fixed_separator_fallback = (
                        "distance_fixed_separator" in stage_fallback_flags
                    )
                    joint_distance_applied = True
                    if fixed_separator_fallback:
                        diagnostics.append(
                            DistributionDiagnostic(
                                code="DISTANCE_FIXED_SEPARATOR_FALLBACK",
                                message=(
                                    "fixed separator positions were honored "
                                    f"and the best valid {mode.value.lower()} "
                                    "distance incumbent was applied, but its "
                                    "conditional optimum was not proven"
                                ),
                                requested_count=fulfilled_optimum,
                                actual_count=fulfilled_optimum,
                            )
                        )
                    elif not joint_distance_proven:
                        diagnostics.append(
                            DistributionDiagnostic(
                                code="DISTANCE_FIXED_SEPARATOR_APPLIED",
                                message=(
                                    f"the exact {mode.value.lower()} distance "
                                    "optimum was applied for the selected fixed "
                                    "separator positions; this is a conditional, "
                                    "not joint separator-position proof"
                                ),
                                requested_count=fulfilled_optimum,
                                actual_count=fulfilled_optimum,
                            )
                        )

            if not joint_distance_applied and selected_move_variables:
                raise DistributionError(
                    "DISTANCE_OPTIMIZER_FAILED",
                    "separator refinement could not establish a distance-ordered "
                    "assignment; no arbitrary assignment was emitted",
                    diagnostics=tuple(diagnostics),
                )

    assignments: dict[str, str] = {}
    distance_for_move: dict[str, float] = {}
    for variable, (ref_key, rail_key) in move_variables.items():
        if variable not in selected_move_variables:
            continue
        decap = decap_by_key[ref_key]
        assignments[decap.refdes] = rail_by_key[rail_key].rail_id
        distance_for_move[ref_key] = distance_by_ref_rail[(ref_key, rail_key)]

    # A timed gap-minimization fallback can retain separator candidates that
    # no longer separate two NET contexts.  Such donor-side gaps are always
    # dominated: restoring them increases donor inventory, preserves every
    # receiver move, and reconnects only the same source rail.  Prune them to a
    # fixed point, then let the atomic topology validator fail closed if a
    # non-local shared-Via relation makes any restoration unsafe.
    original_selected_gap_count = len(selected_gap_variables)
    selected_gap_keys = {
        selectable_gap_variables[variable]
        for variable in selected_gap_variables
    }
    fixed_gap_keys = {
        item.refdes.casefold()
        for item in scenario.decaps
        if item.pad_state == DecapPadState.ISOLATION_GAP
    }
    assignment_rail_by_key = {
        refdes.casefold(): rail_id.casefold()
        for refdes, rail_id in assignments.items()
    }
    while selected_gap_keys:
        removable: set[str] = set()
        all_gap_keys = fixed_gap_keys | selected_gap_keys
        for cluster in analysis.clusters:
            member_keys = {item.casefold() for item in cluster.member_refdes}
            cluster_gap_keys = member_keys & all_gap_keys
            if not cluster_gap_keys:
                continue
            adjacency: dict[str, set[str]] = {
                ref_key: set() for ref_key in member_keys
            }
            for raw_left, raw_right in cluster.power_edges:
                left, right = raw_left.casefold(), raw_right.casefold()
                adjacency[left].add(right)
                adjacency[right].add(left)
            remaining_gaps = set(cluster_gap_keys)
            while remaining_gaps:
                root = min(remaining_gaps)
                remaining_gaps.remove(root)
                group = {root}
                pending = [root]
                while pending:
                    current = pending.pop()
                    for neighbor in adjacency[current]:
                        if neighbor in remaining_gaps:
                            remaining_gaps.remove(neighbor)
                            group.add(neighbor)
                            pending.append(neighbor)
                if group & fixed_gap_keys:
                    continue
                if not all(
                    role_by_cell[
                        (
                            decap_by_key[ref_key].current_rail_id.casefold(),
                            str(decap_by_key[ref_key].model_id).casefold(),
                        )
                    ]
                    == DistributionCellRole.DONOR
                    for ref_key in group
                ):
                    continue
                active_neighbors = {
                    neighbor
                    for ref_key in group
                    for neighbor in adjacency[ref_key]
                    if neighbor not in all_gap_keys
                }
                surrounding_rails = {
                    decap_by_key[ref_key].current_rail_id.casefold()
                    for ref_key in group
                } | {
                    assignment_rail_by_key.get(
                        ref_key,
                        decap_by_key[ref_key].current_rail_id.casefold(),
                    )
                    for ref_key in active_neighbors
                }
                if len(surrounding_rails) == 1:
                    removable.update(group)
        if not removable:
            break
        selected_gap_keys.difference_update(removable)

    pruned_gap_count = original_selected_gap_count - len(selected_gap_keys)
    if pruned_gap_count:
        pruned_gap_refdes = tuple(
            decap_by_key[ref_key].refdes for ref_key in sorted(selected_gap_keys)
        )
        try:
            assign_rails_and_isolation_gaps_atomic(
                scenario, assignments, pruned_gap_refdes
            )
        except ScenarioEditError:
            selected_gap_keys = {
                selectable_gap_variables[variable]
                for variable in selected_gap_variables
            }
        else:
            selected_gap_variables = {
                variable
                for variable in selected_gap_variables
                if selectable_gap_variables[variable] in selected_gap_keys
            }
            diagnostics.append(
                DistributionDiagnostic(
                    code="REDUNDANT_GAPS_PRUNED",
                    message=(
                        f"restored {pruned_gap_count:,} separator pad(s) that "
                        "did not separate different PWR NET contexts; the "
                        "complete scenario passed atomic topology validation "
                        "afterward"
                    ),
                    requested_count=original_selected_gap_count,
                    actual_count=len(selected_gap_keys),
                )
            )
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

    touched_rail_keys = {
        rail_id.casefold()
        for move in move_rows
        for rail_id in (move.previous_rail_id, move.new_rail_id)
    }
    touched_rail_keys.update(
        item.previous_rail_id.casefold() for item in sacrifice_rows
    )
    evaluation_blocker = _preexisting_evaluation_blocker_diagnostic(
        scenario,
        touched_rail_keys,
    )
    if evaluation_blocker is not None:
        diagnostics.append(evaluation_blocker)

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
