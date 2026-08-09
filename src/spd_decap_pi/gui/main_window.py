"""Desktop shell for read-only SPD decap scenario evaluation."""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QThreadPool, QTimer
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCloseEvent,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
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
    QScrollArea,
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
    DEFAULT_EVALUATION_MODAL_MAX_INDEX,
    EVALUATION_MODAL_PRESETS,
    WorkspaceState,
    cap_spice_subcircuit_names,
    import_cap_spice,
    plane_cell_source_geometry,
    scoped_blas_threads,
)
from spd_decap_pi._core.domain import PinKind, ProjectSpec
from spd_decap_pi._core.solver.profiles import (
    LEGACY_MODAL_PROFILE,
    RESEARCH_UNIFORM_ADMITTANCE_PROFILE,
    solver_profile_static_identity_sha256,
)
from spd_decap_pi._core.solver.research_uniform_profile import (
    PROFILE_COMPILER_VERSION as RESEARCH_PROFILE_COMPILER_VERSION,
)

from ..scenario import DecapConnectionKind, DecapPadState, ScenarioDecap, ScenarioSpec
from ..routing_obstacles import SignalTraceAvoidancePolicy
from ..scenario_edits import (
    ProposedRailAssignmentAnalysis,
    ScenarioEditError,
    analyze_cluster_selection,
    analyze_rail_assignment,
    analyze_restore_selection,
    assign_model_atomic,
    assign_rail_atomic,
    assignment_options_for_selection,
    restore_source_atomic,
    selection_with_required_cluster_members,
    set_enabled_atomic,
)
from ..scenario_io import (
    ScenarioBundle,
    load_scenario_with_recovery,
    save_scenario,
)
from ..spd_adapter import ScenarioImport, import_spd_scenario, verify_scenario_source
from ..version import APP_DISPLAY_NAME, __version__
from .board_view import DecapBoardView
from .distribution_window import DistributionTargetsWindow
from .results_window import (
    ComparisonResultsWindow,
    impedance_transition_at_frequency,
)
from .worker import FunctionWorker


_DISTRIBUTION_FIELD_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_DISTRIBUTION_TARGET_FIELD = "target"
_DISTRIBUTION_TOLERANCE_FIELD = "tolerance"


class _DistributionNumericItem(QTableWidgetItem):
    """A table item whose live editable text sorts as a numeric value."""

    @staticmethod
    def _sort_key(item: QTableWidgetItem) -> tuple[int, float | str]:
        try:
            value = float(item.text().replace(",", ""))
            if isfinite(value):
                return (0, value)
        except ValueError:
            pass
        # Keep temporarily invalid editor text sortable and visibly after valid
        # numeric cells rather than silently treating it as zero.
        return (1, item.text().casefold())

    def __lt__(self, other: QTableWidgetItem) -> bool:
        return self._sort_key(self) < self._sort_key(other)


_EXPLORATORY_FIDELITY_WARNING = (
    "Exploratory: rectangular PWR bbox, continuous DGND, single-rail Zii; "
    "absolute sub-milliohm accuracy not certified."
)

_LEGACY_SOLVER_PROFILE_KEY = "legacy_modal_v017"
_RESEARCH_SOLVER_PROFILE_KEY = "research_uniform_admittance"
_SOLVER_PROFILE_ITEMS: tuple[tuple[str, str], ...] = (
    ("Legacy modal", _LEGACY_SOLVER_PROFILE_KEY),
    (
        "Experimental: actual-artwork uniform C00 (topology certificate required)",
        _RESEARCH_SOLVER_PROFILE_KEY,
    ),
)

_RESEARCH_HASH_FIELDS = (
    "artwork_evidence_sha256",
    "research_identity_sha256",
    "static_compiler_algorithm_sha256",
    "source_sha256",
    "geometry_manifest_sha256",
    "component_manifest_sha256",
    "material_manifest_sha256",
    "topology_certificate_sha256",
)


@dataclass(frozen=True, slots=True)
class _SolverProvenancePresentation:
    """Result-derived solver identity rendered consistently across the GUI."""

    key: str
    label: str
    badge: str
    solver_version: str
    source_only: bool
    source_only_status: str
    validation_status: str
    powersi_used_for_parameters: bool
    compiler_algorithm_id: str
    compiler_version: str
    artwork_evidence_sha256: str = ""
    research_identity_sha256: str = ""
    static_compiler_algorithm_sha256: str = ""
    source_sha256: str = ""
    geometry_manifest_sha256: str = ""
    component_manifest_sha256: str = ""
    material_manifest_sha256: str = ""
    topology_certificate_sha256: str = ""

    @property
    def banner_text(self) -> str:
        source_text = "Yes" if self.source_only else "No"
        validation = (
            "RESEARCH / not PowerSI-validated"
            if self.validation_status == "research_not_validated"
            else "Legacy regression baseline"
        )
        identity = (
            f"compiler {self.compiler_algorithm_id} / {self.compiler_version} · "
            f"evidence {self.research_identity_sha256[:12]}… · "
            if self.badge == "RESEARCH"
            else ""
        )
        return (
            f"[{self.badge}] {self.label} · {identity}"
            f"modal backend {self.solver_version} · "
            f"Source-only: {source_text} ({self.source_only_status}) · {validation} · "
            "PowerSI parameter fitting: never"
        )

    @property
    def details_text(self) -> str:
        """Return copyable full identities for tooltip/result audit details."""

        if self.badge != "RESEARCH":
            return self.banner_text
        return "\n".join(
            (
                self.banner_text,
                f"Compiler algorithm ID: {self.compiler_algorithm_id}",
                f"Compiler version: {self.compiler_version}",
                f"Artwork evidence SHA-256: {self.artwork_evidence_sha256}",
                f"Research identity SHA-256: {self.research_identity_sha256}",
                f"Static compiler/algorithm SHA-256: {self.static_compiler_algorithm_sha256}",
                f"Source SHA-256: {self.source_sha256}",
                f"Geometry manifest SHA-256: {self.geometry_manifest_sha256}",
                f"Component manifest SHA-256: {self.component_manifest_sha256}",
                f"Material manifest SHA-256: {self.material_manifest_sha256}",
                f"Topology certificate SHA-256: {self.topology_certificate_sha256}",
            )
        )


def _solver_provenance_for_view(view: Any) -> _SolverProvenancePresentation:
    """Normalize new provenance fields while keeping old cached views readable."""

    raw = getattr(view, "solver_provenance", {})
    provenance: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    key = str(
        getattr(view, "solver_profile_key", None)
        or provenance.get("profile_key")
        or _LEGACY_SOLVER_PROFILE_KEY
    ).strip()
    if key not in {_LEGACY_SOLVER_PROFILE_KEY, _RESEARCH_SOLVER_PROFILE_KEY}:
        raise ValueError(f"Evaluation result has unknown solver profile {key!r}.")
    research = key == _RESEARCH_SOLVER_PROFILE_KEY
    label = str(
        getattr(view, "solver_profile_label", None)
        or ("Actual-artwork uniform mode" if research else "Legacy modal")
    ).strip()
    badge = str(
        getattr(view, "solver_profile_badge", None)
        or provenance.get("profile_badge")
        or ("RESEARCH" if research else "LEGACY")
    ).strip().upper()
    solver_version = str(getattr(view, "solver_version", "unknown")).strip()
    expected_badge = "RESEARCH" if research else "LEGACY"
    if not label or badge != expected_badge or not solver_version:
        raise ValueError("Evaluation result has incomplete solver identity provenance.")
    powersi_used = provenance.get("powersi_used_for_parameters") is True
    if powersi_used:
        raise ValueError(
            "Evaluation result provenance is invalid: PowerSI data must remain "
            "comparison-only and cannot be used for solver parameter fitting."
        )
    # An old/default legacy view has no explicit provenance.  Keep it readable,
    # but do not promote an absent field into a source-only claim.  Research is
    # stricter: every required provenance gate must be present and exact.
    if research and (
        provenance.get("profile_key") != _RESEARCH_SOLVER_PROFILE_KEY
        or provenance.get("profile_badge") != "RESEARCH"
        or provenance.get("source_only") is not True
        or provenance.get("validation_status") != "research_not_validated"
        or provenance.get("powersi_used_for_parameters") is not False
    ):
        raise ValueError(
            "Research evaluation result is missing explicit source-only, "
            "validation, or PowerSI-parameter provenance."
        )
    research_hashes: dict[str, str] = {}
    if research:
        for name in _RESEARCH_HASH_FIELDS:
            value = str(provenance.get(name, "")).strip().lower()
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(
                    "Research evaluation result is missing complete "
                    f"{name} provenance."
                )
            research_hashes[name] = value
        if (
            research_hashes["artwork_evidence_sha256"]
            != research_hashes["research_identity_sha256"]
        ):
            raise ValueError(
                "Research evaluation result has mismatched artwork and research "
                "evidence identities."
            )
        expected_static = solver_profile_static_identity_sha256(
            RESEARCH_UNIFORM_ADMITTANCE_PROFILE
        )
        if research_hashes["static_compiler_algorithm_sha256"] != expected_static:
            raise ValueError(
                "Research evaluation result was produced by a different compiler "
                "algorithm identity."
            )
        declared_algorithm = provenance.get("compiler_algorithm_id")
        if declared_algorithm not in {
            None,
            RESEARCH_UNIFORM_ADMITTANCE_PROFILE.compiler_algorithm_id,
        }:
            raise ValueError(
                "Research evaluation result has an unexpected compiler algorithm ID."
            )
        declared_compiler = provenance.get("compiler_version")
        if declared_compiler not in {None, RESEARCH_PROFILE_COMPILER_VERSION}:
            raise ValueError(
                "Research evaluation result has an unexpected compiler version."
            )
    source_only = provenance.get("source_only", False) is True
    source_only_status = str(
        provenance.get("status")
        or ("source_only_research" if research else "legacy_regression")
    ).strip()
    validation_status = str(
        provenance.get("validation_status")
        or ("research_not_validated" if research else "legacy_regression")
    ).strip()
    if research and not source_only:
        raise ValueError(
            "Research evaluation result is missing its source-only provenance gate."
        )
    if research and validation_status != "research_not_validated":
        raise ValueError(
            "Research evaluation result must remain marked as not PowerSI-validated."
        )
    return _SolverProvenancePresentation(
        key=key,
        label=label,
        badge=badge,
        solver_version=solver_version,
        source_only=source_only,
        source_only_status=source_only_status,
        validation_status=validation_status,
        powersi_used_for_parameters=False,
        compiler_algorithm_id=(
            RESEARCH_UNIFORM_ADMITTANCE_PROFILE.compiler_algorithm_id
            if research
            else LEGACY_MODAL_PROFILE.compiler_algorithm_id
        ),
        compiler_version=(
            RESEARCH_PROFILE_COMPILER_VERSION if research else "legacy-v0.17"
        ),
        **research_hashes,
    )


def _comparison_solver_provenance(
    comparisons: tuple[Any, ...],
) -> _SolverProvenancePresentation:
    """Require one unambiguous solver identity across an accepted batch."""

    presentations = tuple(
        _solver_provenance_for_view(view)
        for comparison in comparisons
        for evaluation in (
            getattr(comparison, "baseline", None),
            getattr(comparison, "tuned", None),
        )
        if (view := getattr(evaluation, "view", None)) is not None
    )
    if not presentations:
        raise ValueError("Evaluation result is missing solver provenance.")
    first = presentations[0]
    if any(item != first for item in presentations[1:]):
        raise ValueError(
            "Evaluation result mixes solver profile, version, or source-only status; "
            "the batch was rejected."
        )
    return first


@dataclass(frozen=True, slots=True)
class _PreparedDistributionPreview:
    """Worker-prepared, still-unapplied data for one atomic distribution preview."""

    plan: Any
    preview_scenario: ScenarioSpec
    export_rows: tuple[Any, ...]
    power_projection: Any | None = None


@dataclass(frozen=True, slots=True)
class _DistributionModelBalance:
    """One model's numeric Distribution capacity, kept separate from its log."""

    model_id: str
    donor: int
    receiver: int
    balance: int
    fixed: int = 0
    proof_pending: int = 0
    exchange_cells: int = 0
    exchange_capacity: int = 0

    @property
    def compact_text(self) -> str:
        """The deliberately terse, one-line table-adjacent presentation."""

        return (
            f"{self.model_id}: Donor {self.donor:,} | "
            f"Receiver {self.receiver:,} | Balance {self.balance:+,}"
        )


@dataclass(frozen=True, slots=True)
class _DistributionBalanceState:
    """Structured validation state for both controls and separate UI renderers."""

    valid: bool
    has_changes: bool
    balances: tuple[_DistributionModelBalance, ...]
    narrative: str

    @property
    def compact_text(self) -> str:
        return " | ".join(item.compact_text for item in self.balances)

    def legacy_tuple(self) -> tuple[bool, bool, str]:
        """Preserve the former narrow tuple API for plugins and focused tests."""

        return self.valid, self.has_changes, self.narrative


@dataclass(frozen=True, slots=True)
class _PreparedPlaneCell:
    """Reentrant Qt paths prepared off-thread; GUI only creates scene items."""

    cell: Any
    net: str
    layer: str
    runs: tuple[tuple[str, QPainterPath], ...]
    primitive_kinds: frozenset[str]
    artwork_bounds: QRectF | None


@dataclass(frozen=True, slots=True)
class _PreparedDocumentView:
    """CPU-only document preview work completed before GUI item creation."""

    source_sha256: str
    plane_cells: tuple[_PreparedPlaneCell, ...]
    layer_labels: tuple[tuple[str, str], ...]
    distribution_counts: dict[tuple[str, str], int]
    distribution_assignable_counts: dict[tuple[str, str], int]
    design_fingerprint: str
    project: ProjectSpec
    bumps: tuple[Any, ...]
    decap_records: tuple[Any, ...]
    source_decap_records: tuple[Any, ...]
    model_keys: frozenset[str]
    connection_summary: tuple[str, str]
    recovery_summary: tuple[str, str]


@dataclass(frozen=True, slots=True)
class _PreparedScenarioImport:
    imported: ScenarioImport
    view: _PreparedDocumentView


@dataclass(frozen=True, slots=True)
class _PreparedScenarioBundle:
    bundle: ScenarioBundle
    view: _PreparedDocumentView


@dataclass(frozen=True, slots=True)
class _EvaluationRunRequest:
    """Immutable UI selections carried through the background preflight."""

    scenario: ScenarioSpec
    rail_ids: tuple[str, ...]
    target_ohm: float | None
    modal_max_index: int
    solver_profile: str
    attachments: dict[str, bytes]


@dataclass(frozen=True, slots=True)
class _EvaluationRunManifest:
    """Explicit selected/run/blocked scope for one Evaluation Analysis run."""

    selected_rail_ids: tuple[str, ...]
    runnable_rail_ids: tuple[str, ...]
    blocked_rail_ids: tuple[str, ...]
    blocker_count: int
    blocker_details: str

    @property
    def is_partial(self) -> bool:
        return bool(self.blocked_rail_ids)

    def summary_lines(self) -> tuple[str, ...]:
        return (
            "Evaluation scope manifest:",
            f"Selected PWR NETs: {len(self.selected_rail_ids):,}.",
            (
                f"Clear and evaluated: {len(self.runnable_rail_ids):,} "
                f"({', '.join(self.runnable_rail_ids)})."
            ),
            (
                f"Blocked and NOT evaluated: {len(self.blocked_rail_ids):,} "
                f"({', '.join(self.blocked_rail_ids) or 'none'})."
            ),
            f"Connectivity/modelability blockers: {self.blocker_count:,}.",
            "Blocked details:",
            self.blocker_details if self.blocked_rail_ids else "None.",
        )


def _distribution_inventory_counts_for_preview(
    scenario: ScenarioSpec,
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
    """Compute both distribution inventories without touching a QWidget."""

    try:
        from ..distribution import distribution_inventory_counts
    except ImportError:
        present: dict[tuple[str, str], int] = {}
        for decap in scenario.decaps:
            if decap.enabled and decap.model_id is not None:
                key = (decap.current_rail_id, decap.model_id)
                present[key] = present.get(key, 0) + 1
        # Without the Distribution module a safe preview is unavailable; do not
        # overstate donor capacity.
        return present, {}
    return distribution_inventory_counts(scenario)


def _source_model_ids_by_refdes(scenario: ScenarioSpec) -> dict[str, str]:
    """Return source-model bindings, including legacy baseline fallbacks."""

    result = {
        decap.refdes.casefold(): decap.source_model_id
        for decap in scenario.decaps
        if decap.source_model_id is not None
    }
    for capture in scenario.baseline_captures.values():
        for binding in capture.model_bindings:
            result.setdefault(binding.refdes.casefold(), binding.model_id)
    return result


def _prepare_source_board_decaps(scenario: ScenarioSpec) -> tuple[Any, ...]:
    """Build immutable source-SPD display records for the board view."""

    source_models = _source_model_ids_by_refdes(scenario)
    return DecapBoardView.prepare_decaps(
        {
            "refdes": decap.refdes,
            "x_um": decap.x_um,
            "y_um": decap.y_um,
            "current_net": decap.source_net,
            "enabled": decap.source_mounted,
            "model_id": source_models.get(decap.refdes.casefold()),
            "footprint": decap.footprint,
        }
        for decap in scenario.decaps
    )


def _prepared_plane_paths(
    geometry: dict[str, Any],
) -> tuple[tuple[tuple[str, QPainterPath], ...], frozenset[str], QRectF | None]:
    """Build reentrant QPainterPath values without creating graphics items."""

    builder = _PlanePathBuilder(geometry, QColor())
    while not builder.step(builder.primitive_count, maximum_points=100_000):
        pass
    runs, primitive_kinds = builder.paths()
    if not runs:
        return (), primitive_kinds, None
    bounds = QRectF()
    for _kind, path in runs:
        path_bounds = path.boundingRect()
        bounds = path_bounds if bounds.isNull() else bounds.united(path_bounds)
    return runs, primitive_kinds, bounds.adjusted(-1.0, -1.0, 1.0, 1.0)


def _prepare_document_view(
    scenario: ScenarioSpec,
    attachments: dict[str, bytes],
    *,
    progress: Callable[[int, str], None],
    is_cancelled: Callable[[], bool],
    progress_start: int = 65,
    progress_end: int = 96,
) -> _PreparedDocumentView:
    """Decode source geometry and inventory in a worker; never create Qt items."""

    project = scenario.base_project
    rail_by_domain = {item.domain: item for item in project.rails}
    cell_count = sum(len(partition.cells) for partition in project.partitions)
    prepared: list[_PreparedPlaneCell] = []
    completed = 0
    progress(progress_start, "Preparing source plane geometry")
    for partition in project.partitions:
        domain_by_cell = {
            cell_id: domain for domain, cell_id in partition.domain_to_cell.items()
        }
        for cell in partition.cells:
            if is_cancelled():
                raise RuntimeError("document opening cancelled")
            domain = domain_by_cell.get(cell.cell_id, cell.cell_id)
            rail = rail_by_domain.get(domain)
            net = rail.net if rail is not None else domain
            geometry = plane_cell_source_geometry(
                cell, attachments, expected_layer=partition.layer
            )
            runs, primitive_kinds, artwork_bounds = _prepared_plane_paths(
                dict(geometry)
            )
            prepared.append(
                _PreparedPlaneCell(
                    cell,
                    str(net),
                    partition.layer,
                    runs,
                    primitive_kinds,
                    artwork_bounds,
                )
            )
            completed += 1
            if cell_count:
                progress(
                    progress_start
                    + round((progress_end - progress_start - 2) * completed / cell_count),
                    f"Prepared {completed:,}/{cell_count:,} plane cells",
                )
    if is_cancelled():
        raise RuntimeError("document opening cancelled")
    progress(progress_end - 1, "Preparing component distribution")
    counts, assignable_counts = _distribution_inventory_counts_for_preview(scenario)
    progress(progress_end, "Ready to render board")
    return _PreparedDocumentView(
        source_sha256=scenario.source.sha256,
        plane_cells=tuple(prepared),
        layer_labels=_short_plane_layer_labels(
            project.stackup_layers, (partition.layer for partition in project.partitions)
        ),
        distribution_counts=counts,
        distribution_assignable_counts=assignable_counts,
        # This can serialize thousands of decaps.  Compute it here so opening
        # a large current-schema scenario does not repeat that work on Qt's
        # event loop thread.
        design_fingerprint=scenario.design_fingerprint,
        project=project,
        bumps=DecapBoardView.prepare_bumps(
            item
            for item in scenario.normalized_project.get("pins", ())
            if str(item.get("kind", "")) == PinKind.DEVICE_BUMP.value
        ),
        decap_records=DecapBoardView.prepare_decaps(scenario.decaps),
        source_decap_records=_prepare_source_board_decaps(scenario),
        model_keys=frozenset(item.model_id.casefold() for item in project.cap_models),
        connection_summary=_shared_pad_connection_summary(scenario),
        recovery_summary=_source_via_path_recovery_summary_from_project(project),
    )


def _modal_convergence_text(view: Any) -> str:
    """Summarize combined numerical convergence without implying model accuracy."""

    convergence = getattr(view, "convergence", None)
    if not isinstance(convergence, dict):
        return "Not reported"
    state = "Converged" if _combined_converged(convergence) else "Not converged"
    frequency_state = (
        "frequency converged"
        if convergence.get("frequency_converged") is True
        else "frequency failed"
    )
    if convergence.get("frequency_budget_exhausted") is True:
        frequency_state += "; budget exhausted"
    modal_state = (
        "modal converged"
        if convergence.get("modal_converged") is True
        else "modal failed"
    )
    frequency_max = _convergence_delta_text(
        convergence.get("frequency_max_delta_db")
    )
    modal_max = _convergence_delta_text(convergence.get("modal_max_delta_db"))
    return (
        f"{state} ({frequency_state}, Δmax {frequency_max}; "
        f"{modal_state}, Δmax {modal_max})"
    )


def _convergence_delta_text(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.3f} dB"
    return "N/A"


def _combined_converged(convergence: Any) -> bool:
    """Require every reported numerical convergence gate to pass explicitly."""

    return isinstance(convergence, dict) and all(
        convergence.get(key) is True
        for key in ("converged", "frequency_converged", "modal_converged")
    )


def _rejected_comparison_convergence(
    comparisons: tuple[Any, ...],
) -> tuple[str, ...]:
    """Return complete per-configuration evidence for a fail-closed batch gate."""

    rejected: list[str] = []
    for comparison in comparisons:
        rail_id = str(getattr(comparison, "rail_id", "unknown rail"))
        for configuration, evaluation in (
            ("Original", getattr(comparison, "baseline", None)),
            ("Tuned", getattr(comparison, "tuned", None)),
        ):
            view = getattr(evaluation, "view", None)
            convergence = getattr(view, "convergence", None)
            if _combined_converged(convergence):
                continue
            values = convergence if isinstance(convergence, dict) else {}
            rejected.append(
                f"{rail_id} / {configuration}: combined convergence "
                f"{'missing' if not isinstance(convergence, dict) else 'failed'}; "
                "frequency RMS "
                f"{_convergence_delta_text(values.get('frequency_rms_delta_db'))}, "
                f"max {_convergence_delta_text(values.get('frequency_max_delta_db'))}; "
                "modal RMS "
                f"{_convergence_delta_text(values.get('modal_rms_delta_db'))}, "
                f"max {_convergence_delta_text(values.get('modal_max_delta_db'))}."
            )
    return tuple(rejected)


def _whole_decap_tolerance(present: int, tolerance_percent: float) -> int:
    return int(
        (
            Decimal(present)
            * Decimal(str(tolerance_percent))
            / Decimal(100)
        ).to_integral_value(rounding=ROUND_FLOOR)
    )


class _PlaneArtworkItem(QGraphicsItem):
    """One batched, recolorable PowerSI artwork item for a plane cell."""

    def __init__(
        self,
        runs: tuple[tuple[str, QPainterPath], ...],
        color: QColor,
        primitive_kinds: set[str],
        bounds: QRectF | None = None,
    ) -> None:
        super().__init__()
        self._runs = runs
        self._color = QColor(color)
        self.primitive_kinds = frozenset(primitive_kinds)
        # Device-coordinate caching gives this item an isolated transparent
        # backing surface.  Negative PowerSI primitives can therefore clear
        # only this cell's copper instead of painting over other visible
        # layers in the shared scene.
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        if bounds is None:
            bounds = QRectF()
            for _kind, path in runs:
                path_bounds = path.boundingRect()
                bounds = path_bounds if bounds.isNull() else bounds.united(path_bounds)
            bounds = bounds.adjusted(-1.0, -1.0, 1.0, 1.0)
        self._bounds = QRectF(bounds)

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt virtual name
        return QRectF(self._bounds)

    def paint(
        self,
        painter: QPainter,
        _option: Any,
        _widget: QWidget | None = None,
    ) -> None:
        positive_fill = QColor(self._color)
        positive_fill.setAlpha(46)
        for kind, path in self._runs:
            negative = kind.startswith("negative_")
            if negative:
                painter.save()
                painter.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Clear
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(Qt.BrushStyle.SolidPattern)
                painter.drawPath(path)
                painter.restore()
            pen = QPen(self._color)
            pen.setCosmetic(True)
            if negative:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(
                Qt.BrushStyle.NoBrush if negative else QBrush(positive_fill)
            )
            painter.drawPath(path)

    def set_display_color(self, color: QColor) -> None:
        normalized = QColor(color)
        if normalized == self._color:
            return
        self._color = normalized
        self.update()

    def pen(self) -> QPen:
        pen = QPen(self._color)
        pen.setCosmetic(True)
        return pen


class _PlanePathBuilder:
    """Incrementally turn normalized primitives into reentrant QPainterPaths."""

    def __init__(self, geometry: dict[str, Any] | Any, color: QColor) -> None:
        self._polygons = {
            "positive_polygon": geometry["positive_polygons_um"],
            "negative_polygon": geometry["negative_polygons_um"],
        }
        self._circles = {
            "positive_circle": geometry["positive_circles_um"],
            "negative_circle": geometry["negative_circles_um"],
        }
        order = list(geometry["primitive_order"])
        self._order = order or [
            *(("positive_polygon", index) for index in range(len(self._polygons["positive_polygon"]))),
            *(("positive_circle", index) for index in range(len(self._circles["positive_circle"]))),
            *(("negative_polygon", index) for index in range(len(self._polygons["negative_polygon"]))),
            *(("negative_circle", index) for index in range(len(self._circles["negative_circle"]))),
        ]
        self._color = QColor(color)
        self._index = 0
        self._runs: list[tuple[str, QPainterPath]] = []
        self._primitive_kinds: set[str] = set()
        self._active_kind: str | None = None
        self._active_path: QPainterPath | None = None
        self._pending_polygon: tuple[str, Any] | None = None
        self._pending_area_index = 0
        self._pending_area = 0.0
        self._pending_build_index = 0
        self._pending_direction = 1
        self._pending_built = 0

    @property
    def done(self) -> bool:
        return self._index >= len(self._order) and self._pending_polygon is None

    @property
    def primitive_count(self) -> int:
        return len(self._order)

    def step(self, maximum_primitives: int, maximum_points: int = 2048) -> bool:
        """Build bounded primitive and vertex counts, returning completion state."""

        completed_primitives = 0
        remaining_points = max(1, maximum_points)
        while completed_primitives < max(1, maximum_primitives):
            if self.done:
                return True
            if self._pending_polygon is not None:
                kind, values = self._pending_polygon
                point_count = len(values)
                while self._pending_area_index < point_count and remaining_points:
                    index = self._pending_area_index
                    x0, y0 = values[index]
                    x1, y1 = values[(index + 1) % point_count]
                    self._pending_area += float(x0) * float(y1) - float(x1) * float(y0)
                    self._pending_area_index += 1
                    remaining_points -= 1
                if self._pending_area_index < point_count:
                    return False
                if self._pending_built == 0:
                    if self._pending_area < 0.0:
                        self._pending_build_index = point_count - 1
                        self._pending_direction = -1
                    self._path_for(kind).moveTo(
                        float(values[self._pending_build_index][0]),
                        float(values[self._pending_build_index][1]),
                    )
                    self._pending_build_index += self._pending_direction
                    self._pending_built = 1
                path = self._path_for(kind)
                while self._pending_built < point_count and remaining_points:
                    x, y = values[self._pending_build_index]
                    path.lineTo(float(x), float(y))
                    self._pending_build_index += self._pending_direction
                    self._pending_built += 1
                    remaining_points -= 1
                if self._pending_built < point_count:
                    return False
                path.closeSubpath()
                self._primitive_kinds.add(kind)
                self._pending_polygon = None
                self._pending_area_index = 0
                self._pending_area = 0.0
                self._pending_built = 0
                completed_primitives += 1
                if not remaining_points:
                    return False
                continue
            kind, index = self._order[self._index]
            self._index += 1
            if kind in self._polygons:
                values = self._polygons[kind]
                if index < 0 or index >= len(values):
                    continue
                if len(values[index]) < 3:
                    continue
                self._pending_polygon = (kind, values[index])
                self._pending_area_index = 0
                self._pending_area = 0.0
                self._pending_build_index = 0
                self._pending_direction = 1
                self._pending_built = 0
                continue
            elif kind in self._circles:
                values = self._circles[kind]
                if index < 0 or index >= len(values):
                    continue
                center_x, center_y, radius = values[index]
                self._path_for(kind).addEllipse(
                    float(center_x - radius),
                    float(center_y - radius),
                    float(2.0 * radius),
                    float(2.0 * radius),
                )
                self._primitive_kinds.add(kind)
            completed_primitives += 1
            if not remaining_points:
                return False
        return self.done

    def item(self) -> _PlaneArtworkItem | None:
        if not self.done:
            raise RuntimeError("cannot create source artwork before path construction finishes")
        if not self._runs:
            return None
        result = _PlaneArtworkItem(tuple(self._runs), self._color, self._primitive_kinds)
        result.setData(0, "source_geometry")
        return result

    def paths(self) -> tuple[tuple[tuple[str, QPainterPath], ...], frozenset[str]]:
        if not self.done:
            raise RuntimeError("cannot access source paths before construction finishes")
        return tuple(self._runs), frozenset(self._primitive_kinds)

    def _path_for(self, kind: str) -> QPainterPath:
        if self._active_path is None or kind != self._active_kind:
            self._active_kind = kind
            self._active_path = QPainterPath()
            self._active_path.setFillRule(Qt.FillRule.WindingFill)
            self._runs.append((kind, self._active_path))
        return self._active_path


def _excel_safe_csv_cell(value: object) -> str:
    """Keep spreadsheet programs from interpreting identifiers as formulas."""

    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return f"'{text}"
    return text


def _tuned_decap_csv_rows(
    scenario: ScenarioSpec,
    evaluated_rail_ids: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    """Return enabled final assignments for the evaluated PWR rails."""

    evaluated_rail_keys = {rail_id.casefold() for rail_id in evaluated_rail_ids}
    connected_refdes = getattr(
        scenario,
        "electrically_connected_refdes",
        tuple(decap.refdes for decap in scenario.decaps),
    )
    connected_keys = {
        refdes.casefold() for refdes in connected_refdes
    }
    return tuple(
        (
            _excel_safe_csv_cell(decap.model_id or ""),
            _excel_safe_csv_cell(decap.refdes),
            _excel_safe_csv_cell(decap.current_net),
        )
        for decap in scenario.decaps
        if decap.enabled
        and decap.refdes.casefold() in connected_keys
        and decap.current_rail_id.casefold() in evaluated_rail_keys
    )


def _connection_label(scenario: ScenarioSpec, refdes: str) -> str:
    analysis = scenario.connection_analysis
    if analysis is None:
        return "Analysis required"
    # Scenario validation keeps the persisted dict keyed by exact REFDES.  The
    # direct lookup is important for 10k+ component boards; a per-row linear
    # scan would turn every board refresh into quadratic work.
    connection = analysis.connections.get(refdes)
    if connection is None:
        connection = next(
            (
                item
                for item in analysis.connections.values()
                if item.refdes.casefold() == refdes.casefold()
            ),
            None,
        )
    if connection is None:
        return "Unresolved"
    cluster_label = ""
    if connection.cluster_id:
        suffix = connection.cluster_id.rsplit(":", 1)[-1][:8]
        cluster_label = suffix if suffix.casefold().startswith("cl") else f"CL {suffix}"
    return {
        DecapConnectionKind.DIRECT: "Direct via",
        DecapConnectionKind.SHARED_ANCHOR: f"Via anchor · {cluster_label}",
        DecapConnectionKind.SHARED_DUMMY: f"Dummy · {cluster_label}",
        DecapConnectionKind.FLOATING_DUMMY: "Floating dummy",
        DecapConnectionKind.UNRESOLVED: "Unresolved",
        DecapConnectionKind.OUT_OF_SCOPE: "Out of scope",
    }[connection.kind]


def _eligible_pwr_label(scenario: ScenarioSpec, decap: ScenarioDecap) -> str:
    """Describe the rail choices used by the actual atomic edit service."""

    eligibility = decap.eligibility
    analysis = scenario.connection_analysis
    if analysis is not None:
        connection = analysis.connections.get(decap.refdes)
        if connection is not None and connection.cluster_id is not None:
            cluster = next(
                (
                    item
                    for item in analysis.clusters
                    if item.cluster_id == connection.cluster_id
                ),
                None,
            )
            eligibility = cluster.eligibility if cluster is not None else {}
        elif connection is not None and connection.kind in {
            DecapConnectionKind.FLOATING_DUMMY,
            DecapConnectionKind.UNRESOLVED,
            DecapConnectionKind.OUT_OF_SCOPE,
        }:
            eligibility = {}
    return ", ".join(
        item.net for item in eligibility.values() if item.allowed
    ) or "None"


def _proposal_block_label(analysis: ProposedRailAssignmentAnalysis) -> str:
    """Return a short, user-facing reason for a rejected atomic PWR edit."""

    if analysis.shared_power_via_conflicts:
        return "one physical PWR VIA would be shared across different NETs"
    if analysis.invalid_islands:
        island = analysis.invalid_islands[0]
        if island.reason == "NO_PWR_VIA_ANCHOR":
            return "dummy-only island has no PWR VIA"
        if island.reason == "ACTIVE_PAD_SHORT":
            return (
                "an isolation-gap pad cell is required; use De-cap Distribution"
            )
        if island.reason == "MIXED_RAIL_IDS_ON_SAME_NET":
            return "one physically shorted NET segment cannot use mixed rail IDs"
        return "target NET is not valid at every serving PWR VIA"
    if analysis.ineligible_refdes:
        return "target NET has no exact plane under a PWR VIA"
    return "post-change shared-pad topology is invalid"


def _shared_pad_connection_summary(scenario: ScenarioSpec) -> tuple[str, str]:
    """Return compact and detailed source-connectivity load summaries."""

    analysis = scenario.connection_analysis
    if analysis is None:
        message = "Pad/Via analysis unavailable; PWR edits fail closed"
        return message, message
    counts = {
        kind: sum(
            connection.kind == kind for connection in analysis.connections.values()
        )
        for kind in DecapConnectionKind
    }
    anchored_clusters = sum(
        bool(cluster.anchor_refdes) for cluster in analysis.clusters
    )
    compact = (
        f"Pad/Via A {counts[DecapConnectionKind.SHARED_ANCHOR]:,} · "
        f"D {counts[DecapConnectionKind.SHARED_DUMMY]:,} · "
        f"F {counts[DecapConnectionKind.FLOATING_DUMMY]:,} · "
        f"U {counts[DecapConnectionKind.UNRESOLVED]:,}"
    )
    detail = " | ".join(
        (
            "Pad/Via source connectivity",
            f"Direct: {counts[DecapConnectionKind.DIRECT]:,}",
            f"Shared anchors: {counts[DecapConnectionKind.SHARED_ANCHOR]:,}",
            f"Shared dummies: {counts[DecapConnectionKind.SHARED_DUMMY]:,}",
            f"Floating dummies: {counts[DecapConnectionKind.FLOATING_DUMMY]:,}",
            f"Unresolved (PWR edits blocked): "
            f"{counts[DecapConnectionKind.UNRESOLVED]:,}",
            f"Out of scope: {counts[DecapConnectionKind.OUT_OF_SCOPE]:,}",
            f"Anchored clusters: {anchored_clusters:,}",
        )
    )
    return compact, detail


def _source_via_path_recovery_summary(scenario: ScenarioSpec) -> tuple[str, str]:
    """Return deterministic source-Via applicability disclosure for the UI."""

    return _source_via_path_recovery_summary_from_project(scenario.base_project)


def _source_via_path_recovery_summary_from_project(
    project: ProjectSpec,
) -> tuple[str, str]:
    """Return recovery disclosure from an already-validated project."""

    raw = project.metadata.get("spd_via_path_recovery", {})
    if not isinstance(raw, dict):
        message = "Source Via paths: unavailable"
        return message, message

    def count(name: str) -> int:
        value = raw.get(name, 0)
        return value if isinstance(value, int) and value >= 0 else 0

    requested = count("requested")
    recovered = count("recovered")
    fallback = count("fallback")
    compact = (
        f"Source Via paths: {recovered:,}/{requested:,} recovered; "
        f"{fallback:,} fallback"
    )
    details = [compact]
    if requested and not recovered:
        details.append(
            "No source segment R/L applied; legacy rail templates used"
        )
    elif recovered:
        details.append(
            "Recovered source paths use selected-plane pad geometry and "
            "source-proven vertical segment R/L where applicable"
        )
    return compact, "; ".join(details)


def _short_plane_layer_labels(
    stackup_layers: Any,
    partition_layers: Any,
) -> tuple[tuple[str, str], ...]:
    """Return physical-order full names with compact T/P1/P2 display labels."""

    available: dict[str, str] = {}
    for value in partition_layers:
        name = str(value).strip()
        if name:
            available.setdefault(name.casefold(), name)
    if not available:
        return ()

    ordered: list[str] = []
    ordered_keys: set[str] = set()
    top_conductor_key: str | None = None
    for layer in stackup_layers:
        name = str(getattr(layer, "name", "")).strip()
        if not name:
            continue
        key = name.casefold()
        if top_conductor_key is None and bool(getattr(layer, "is_conductor", False)):
            top_conductor_key = key
        if key in available and key not in ordered_keys:
            ordered.append(available[key])
            ordered_keys.add(key)
    ordered.extend(
        name for key, name in available.items() if key not in ordered_keys
    )

    result: list[tuple[str, str]] = []
    plane_number = 1
    for name in ordered:
        if name.casefold() == top_conductor_key:
            label = "T"
        else:
            label = f"P{plane_number}"
            plane_number += 1
        result.append((name, label))
    return tuple(result)


def _job_load_scenario(
    path: Path,
    source_override: Path | None = None,
    prepare_view: bool = False,
    *,
    progress: Callable[[int, str], None],
    is_cancelled: Callable[[], bool],
) -> ScenarioBundle | _PreparedScenarioBundle:
    progress(5, "Reading .spdpi scenario")
    bundle = load_scenario_with_recovery(path)
    progress(35, "Validating external SPD identity")
    resolved = verify_scenario_source(
        bundle.scenario,
        source_override,
        progress=lambda value, message: progress(35 + round(value * 0.30), message),
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
    if not prepare_view:
        return bundle
    view = _prepare_document_view(
        bundle.scenario,
        bundle.attachments,
        progress=progress,
        is_cancelled=is_cancelled,
    )
    return _PreparedScenarioBundle(bundle, view)


def _job_import_spd(
    path: Path,
    *,
    progress: Callable[[int, str], None],
    is_cancelled: Callable[[], bool],
) -> _PreparedScenarioImport:
    """Import and complete expensive pure preview preparation off the GUI thread."""

    imported = import_spd_scenario(
        path, progress=lambda value, message: progress(round(value * 0.65), message),
        is_cancelled=is_cancelled,
    )
    view = _prepare_document_view(
        imported.scenario,
        imported.attachments,
        progress=progress,
        is_cancelled=is_cancelled,
    )
    return _PreparedScenarioImport(imported, view)


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


def _job_preflight_evaluation(
    scenario: ScenarioSpec,
    rail_ids: tuple[str, ...],
    *,
    progress: Callable[[int, str], None],
    is_cancelled: Callable[[], bool],
) -> Any:
    """Run the potentially expensive geometry/connectivity gate off-thread."""

    if is_cancelled():
        raise RuntimeError("evaluation preflight cancelled")
    progress(5, "Checking Evaluation Analysis connectivity and geometry")
    from ..evaluation import preflight_evaluation_comparison

    result = preflight_evaluation_comparison(
        scenario,
        rail_ids,
        progress=lambda value, message: progress(
            5 + round(min(max(value, 0), 100) * 0.9), message
        ),
        is_cancelled=is_cancelled,
    )
    if is_cancelled():
        raise RuntimeError("evaluation preflight cancelled")
    progress(100, "Evaluation Analysis preflight complete")
    return result


def _job_compute_distribution(
    scenario: ScenarioSpec,
    attachments: dict[str, bytes],
    targets: dict[tuple[str, str], int],
    tolerances: dict[tuple[str, str], float],
    distance_mode: Any,
    routing_policy: SignalTraceAvoidancePolicy = SignalTraceAvoidancePolicy(),
    *,
    progress: Callable[[int, str], None],
    is_cancelled: Callable[[], bool],
) -> Any:
    """Run the CPU-bound distribution planner behind the shared GUI worker."""

    from ..distribution import (
        build_distribution_power_projection,
        compute_distribution_plan,
        validate_distribution_targets,
    )

    if is_cancelled():
        raise RuntimeError("distribution calculation cancelled")
    progress(3, "Rebuilding exact Distribution PWR proof from retained artwork")
    with scoped_blas_threads():
        power_projection = build_distribution_power_projection(
            scenario,
            attachments,
            targets=targets,
            tolerances=tolerances,
            routing_policy=routing_policy,
            progress=lambda value, message: progress(
                3 + round(float(value) * 0.27), message
            ),
            is_cancelled=is_cancelled,
        )
        if is_cancelled():
            raise RuntimeError("distribution calculation cancelled")
        progress(31, "Validating exact De-cap Distribution donor capacity")
        validate_distribution_targets(
            scenario,
            targets,
            tolerances,
            power_projection=power_projection,
        )
        result = compute_distribution_plan(
            scenario,
            targets,
            distance_mode,
            tolerances=tolerances,
            power_projection=power_projection,
            routing_policy=routing_policy,
            progress=lambda value, message: progress(
                32 + round(float(value) * 0.60), message
            ),
            is_cancelled=is_cancelled,
        )
        if is_cancelled():
            raise RuntimeError("distribution calculation cancelled")
        progress(92, "Preparing atomic De-cap Distribution preview")
        from ..distribution import apply_distribution_plan, distribution_csv_rows

        preview = apply_distribution_plan(
            scenario,
            result,
            power_projection=power_projection,
        )
        exported = tuple(distribution_csv_rows(result))
        export_rows = (
            exported[1:]
            if exported
            and isinstance(exported[0], (tuple, list))
            and tuple(exported[0])[:2] == ("Component", "REFDES")
            else exported
        )
    if is_cancelled():
        raise RuntimeError("distribution calculation cancelled")
    progress(100, "De-cap Distribution preview complete")
    return _PreparedDistributionPreview(
        result,
        preview,
        export_rows,
        power_projection,
    )


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
        self._pending_evaluation_launch: tuple[
            _EvaluationRunRequest, _EvaluationRunManifest
        ] | None = None
        self._active_evaluation_manifest: _EvaluationRunManifest | None = None
        self._evaluation_state: WorkspaceState | None = None
        self._last_evaluation: Any | None = None
        self._last_scenario_evaluation: Any | None = None
        self._tuned_evaluations_by_rail: dict[str, Any] = {}
        self._comparison_batch: Any | None = None
        self._results_window: ComparisonResultsWindow | None = None
        self._distribution_window: DistributionTargetsWindow | None = None
        self._distribution_show_original_board = False
        self._source_board_records: tuple[Any, ...] = ()
        self._source_board_cache_key: tuple[str, int] | None = None
        self._source_board_comparison_cache: bool | None = None
        self._decap_context_menu: QMenu | None = None
        self._active_board_net_keys: frozenset[str] | None = None
        self._rendered_bump_source_sha256: str | None = None
        self._rendered_plane_source_sha256: str | None = None
        self._rendered_plane_net_colors: dict[str, str] = {}
        self._plane_items_by_net: dict[str, list[QGraphicsItem]] = {}
        self._plane_items_by_layer: dict[str, list[QGraphicsItem]] = {}
        self._plane_render_token = 0
        self._plane_render_cells: tuple[_PreparedPlaneCell, ...] = ()
        self._plane_render_index = 0
        self._plane_render_builder: _PlanePathBuilder | None = None
        self._plane_layer_checks: dict[str, QCheckBox] = {}
        self._hidden_plane_layer_keys: set[str] = set()
        self._plane_layer_selection_initialized = False
        self._distribution_basis_fingerprint: str | None = None
        self._distribution_present_counts: dict[tuple[str, str], int] = {}
        self._distribution_assignable_counts: dict[tuple[str, str], int] = {}
        self._distribution_targets: dict[tuple[str, str], int] = {}
        self._distribution_tolerances: dict[tuple[str, str], float] = {}
        self._distribution_rail_ids: tuple[str, ...] = ()
        self._distribution_model_ids: tuple[str, ...] = ()
        self._distribution_invalid_cells: set[tuple[str, str]] = set()
        self._distribution_invalid_tolerance_cells: set[tuple[str, str]] = set()
        self._distribution_plan: Any | None = None
        self._distribution_preview_scenario: ScenarioSpec | None = None
        self._distribution_power_projection: Any | None = None
        self._distribution_export_rows: tuple[Any, ...] = ()
        self._distribution_table_updating = False
        self._distribution_import_notice: str | None = None
        self._distribution_status_notice: str | None = None

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
        self.source_board_checkbox = QCheckBox("Show source SPD assignments")
        self.source_board_checkbox.setObjectName("showSourceSpdBoardAssignments")
        self.source_board_checkbox.setAccessibleName(
            "Show source SPD board assignments"
        )
        self.source_board_checkbox.setToolTip(
            "Compare the immutable source-SPD assignment with the current board. "
            "Source view is display-only; physical X/Y positions do not move."
        )
        self.source_board_checkbox.toggled.connect(
            self._show_original_distribution_board
        )
        self.board_assignment_view_label = QLabel("Board: Current")
        self.board_assignment_view_label.setObjectName("boardAssignmentViewStatus")
        search_row.addWidget(self.search_mode)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.search_button)
        search_row.addWidget(self.fit_button)
        search_row.addWidget(self.source_board_checkbox)
        search_row.addWidget(self.board_assignment_view_label)
        root_layout.addLayout(search_row)

        self.plane_layer_bar = QWidget()
        self.plane_layer_bar.setObjectName("planeLayerBar")
        plane_layer_bar_layout = QHBoxLayout(self.plane_layer_bar)
        plane_layer_bar_layout.setContentsMargins(0, 0, 0, 0)
        plane_layer_bar_layout.setSpacing(6)
        layer_heading = QLabel("PWR planes")
        layer_heading.setStyleSheet("font-weight: 700;")
        plane_layer_bar_layout.addWidget(layer_heading)
        self.all_plane_layers_button = QPushButton("All")
        self.all_plane_layers_button.setObjectName("showAllPlaneLayersButton")
        self.all_plane_layers_button.setFixedWidth(44)
        self.all_plane_layers_button.clicked.connect(
            lambda: self._set_all_plane_layers_visible(True)
        )
        self.no_plane_layers_button = QPushButton("None")
        self.no_plane_layers_button.setObjectName("hideAllPlaneLayersButton")
        self.no_plane_layers_button.setFixedWidth(48)
        self.no_plane_layers_button.clicked.connect(
            lambda: self._set_all_plane_layers_visible(False)
        )
        plane_layer_bar_layout.addWidget(self.all_plane_layers_button)
        plane_layer_bar_layout.addWidget(self.no_plane_layers_button)
        self.plane_layer_scroll = QScrollArea()
        self.plane_layer_scroll.setObjectName("planeLayerScrollArea")
        self.plane_layer_scroll.setWidgetResizable(True)
        self.plane_layer_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.plane_layer_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.plane_layer_scroll.setFixedHeight(46)
        self.plane_layer_host = QWidget()
        self.plane_layer_layout = QHBoxLayout(self.plane_layer_host)
        self.plane_layer_layout.setContentsMargins(6, 0, 6, 0)
        self.plane_layer_layout.setSpacing(10)
        self.plane_layer_layout.addStretch(1)
        self.plane_layer_scroll.setWidget(self.plane_layer_host)
        plane_layer_bar_layout.addWidget(self.plane_layer_scroll, 1)
        self.plane_layer_bar.hide()
        root_layout.addWidget(self.plane_layer_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.board = DecapBoardView()
        self.board.selectionChanged.connect(self._selection_changed)
        self.board.contextMenuRequested.connect(self._show_decap_context_menu)
        splitter.addWidget(self.board)

        self.side_tabs = QTabWidget()
        self.side_tabs.setObjectName("scenarioSideTabs")
        self.side_tabs.setMinimumWidth(600)
        self.side_tabs.addTab(self._build_selection_tab(), "Selection")
        self.side_tabs.addTab(self._build_evaluation_tab(), "Evaluation")
        self.side_tabs.addTab(self._build_ai_tab(), "AI Assist")
        self.side_tabs.addTab(
            self._build_distribution_tab(), "De-cap Distribution"
        )
        splitter.addWidget(self.side_tabs)
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

    @staticmethod
    def _section_splitter(object_name: str) -> QSplitter:
        """Create a vertical splitter whose drag handle reads as a section bar."""

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName(object_name)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        splitter.setStyleSheet(
            """
            QSplitter::handle:vertical {
                background: #94A3B8;
                border-top: 1px solid #CBD5E1;
                border-bottom: 1px solid #475569;
                margin: 2px 0px;
            }
            QSplitter::handle:vertical:hover,
            QSplitter::handle:vertical:pressed {
                background: #3B82F6;
            }
            """
        )
        return splitter

    def _build_selection_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = self._section_splitter("selectionSectionSplitter")

        selection_section = QWidget()
        selection_section.setObjectName("selectionTableSection")
        selection_section.setMinimumHeight(170)
        selection_layout = QVBoxLayout(selection_section)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        self.selection_summary = QLabel("No decap selected")
        self.selection_summary.setWordWrap(True)
        selection_layout.addWidget(self.selection_summary)
        self.selection_table = QTableWidget(0, 7)
        self.selection_table.setHorizontalHeaderLabels(
            (
                "REFDES",
                "State",
                "PWR NET",
                "Model",
                "Footprint",
                "Pad/Via",
                "Eligible PWR",
            )
        )
        self.selection_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.selection_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.selection_table.horizontalHeader().setStretchLastSection(True)
        selection_layout.addWidget(self.selection_table, 1)

        color_section = QWidget()
        color_section.setObjectName("selectionColorSection")
        color_section.setMinimumHeight(160)
        color_layout = QVBoxLayout(color_section)
        color_layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel(
            "Left click selects one; Ctrl+click toggles multiple; drag selects many; "
            "right click changes PWR/model or "
            "enabled state. Hover a decap for its PWR NET, component, REFDES and state; "
            "a PWR change is allowed only when every resulting same-NET short-pad "
            "segment retains a physical PWR VIA; VIA-less dummy islands are blocked. "
            "Amber marks other members of the same source cluster and the context menu "
            "can select the complete cluster as a safe shortcut; "
            "a red X marks disabled decaps. Shift+drag pans and the wheel zooms. "
            "Assignment eligibility uses exact SPD copper at the physical PWR pad; plane "
            "fills are read-only PowerSI artwork and dashed rectangles mark the solver "
            "bounding-box approximation."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa4b2;")
        color_layout.addWidget(hint)

        colors_group = QGroupBox("PWR NET colors")
        colors_layout = QVBoxLayout(colors_group)
        self.color_list = QListWidget()
        self.color_list.itemDoubleClicked.connect(self._choose_net_color)
        colors_layout.addWidget(self.color_list)
        colors_layout.addWidget(QLabel("Double-click a net to change its color."))
        color_layout.addWidget(colors_group, 1)

        splitter.addWidget(selection_section)
        splitter.addWidget(color_section)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes((390, 260))
        layout.addWidget(splitter, 1)
        return page

    def _build_evaluation_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        section_splitter = self._section_splitter("evaluationSectionSplitter")

        controls_section = QWidget()
        controls_section.setObjectName("evaluationControlsSection")
        controls_section.setMinimumHeight(285)
        controls_layout = QVBoxLayout(controls_section)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_heading = QLabel("PWR NET Selection & Evaluation")
        controls_heading.setObjectName("evaluationControlsHeading")
        controls_heading.setStyleSheet("font-weight: 700;")
        controls_layout.addWidget(controls_heading)
        form = QFormLayout()
        self.rail_list = QListWidget()
        self.rail_list.setObjectName("evaluationRailList")
        self.rail_list.setMinimumHeight(140)
        self.rail_list.setIconSize(QSize(12, 12))
        self.rail_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rail_list.itemChanged.connect(self._evaluation_rail_check_changed)
        self.rail_list.customContextMenuRequested.connect(
            self._show_evaluation_rail_context_menu
        )
        self.rail_list.setToolTip(
            "Check PWR NETs to evaluate. Right-click a NET to change its color."
        )
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
        self.evaluation_solver_profile_combo = QComboBox()
        self.evaluation_solver_profile_combo.setObjectName(
            "evaluationSolverProfileCombo"
        )
        for label, key in _SOLVER_PROFILE_ITEMS:
            self.evaluation_solver_profile_combo.addItem(label, key)
        self.evaluation_solver_profile_combo.setCurrentIndex(
            self.evaluation_solver_profile_combo.findData(
                _LEGACY_SOLVER_PROFILE_KEY
            )
        )
        self.evaluation_solver_profile_combo.setToolTip(
            "Legacy modal is the default regression path. The actual-artwork "
            "uniform-C00 profile is an EXPERIMENTAL research opt-in and requires "
            "a complete topology certificate: it replaces "
            "only the uniform C00 plane term, keeps nonuniform rectangular-bbox "
            "modes, and refuses to run when source topology evidence is incomplete. "
            "PowerSI data remains comparison-only and is never used for fitting."
        )
        self.evaluation_solver_profile_combo.currentIndexChanged.connect(
            self._evaluation_solver_profile_changed
        )
        self.evaluation_solver_profile_status = QLabel()
        self.evaluation_solver_profile_status.setObjectName(
            "evaluationSolverProfileStatus"
        )
        self.evaluation_solver_profile_status.setWordWrap(True)
        profile_picker = QWidget()
        profile_picker_layout = QVBoxLayout(profile_picker)
        profile_picker_layout.setContentsMargins(0, 0, 0, 0)
        profile_picker_layout.setSpacing(3)
        profile_picker_layout.addWidget(self.evaluation_solver_profile_combo)
        profile_picker_layout.addWidget(self.evaluation_solver_profile_status)
        self.evaluation_modal_preset_combo = QComboBox()
        self.evaluation_modal_preset_combo.setObjectName(
            "evaluationModalPresetCombo"
        )
        for preset in EVALUATION_MODAL_PRESETS:
            self.evaluation_modal_preset_combo.addItem(
                f"{preset.label} ({preset.mode_count} modes)", preset.max_index
            )
        default_index = self.evaluation_modal_preset_combo.findData(
            DEFAULT_EVALUATION_MODAL_MAX_INDEX
        )
        if default_index >= 0:
            self.evaluation_modal_preset_combo.setCurrentIndex(default_index)
        self.evaluation_modal_preset_combo.setToolTip(
            "Changes only the internal rectangular modal convergence/runtime. "
            "Experimental m12 check uses PowerSI evidence for comparison only; the "
            "2026-07-29 benchmark took 4,139 s and more modes worsened external "
            "correlation in that case, which does not justify selecting a lower order."
        )
        self.evaluation_modal_preset_combo.currentIndexChanged.connect(
            self._evaluation_modal_preset_changed
        )
        form.addRow("PWR NETs", rail_picker)
        form.addRow("Common target impedance (ohm)", self.target_edit)
        form.addRow("Physics model", profile_picker)
        form.addRow("Numerical convergence preset", self.evaluation_modal_preset_combo)
        controls_layout.addLayout(form)
        self._update_evaluation_solver_profile_help()
        self.evaluate_button = QPushButton("Run Original + Tuned evaluation")
        self.evaluate_button.setObjectName("evaluateScenarioButton")
        self.evaluate_button.clicked.connect(self.run_evaluation)
        controls_layout.addWidget(self.evaluate_button)

        results_section = QWidget()
        results_section.setObjectName("evaluationResultsSection")
        results_section.setMinimumHeight(180)
        results_layout = QVBoxLayout(results_section)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_heading = QLabel("Evaluation Results")
        results_heading.setObjectName("evaluationResultsHeading")
        results_heading.setStyleSheet("font-weight: 700;")
        results_layout.addWidget(results_heading)
        results_actions = QHBoxLayout()
        self.open_results_button = QPushButton("Open Result Plot")
        self.open_results_button.setObjectName("openResultPlotButton")
        self.open_results_button.setToolTip(
            "Open the impedance comparison plot and its table in a separate, "
            "resizable non-modal window."
        )
        self.open_results_button.clicked.connect(self._show_results_window)
        self.open_results_button.setEnabled(False)
        self.export_tuned_csv_button = QPushButton("Export Tuned CSV...")
        self.export_tuned_csv_button.setObjectName("exportTunedDecapsCsvButton")
        self.export_tuned_csv_button.setToolTip(
            "Export enabled Decaps from the evaluated Tuned PWR NETs as "
            "Component, REFDES and NET Name."
        )
        self.export_tuned_csv_button.clicked.connect(self._export_tuned_decaps_csv)
        self.export_tuned_csv_button.setEnabled(False)
        results_actions.addStretch(1)
        results_actions.addWidget(self.export_tuned_csv_button)
        results_actions.addWidget(self.open_results_button)
        results_layout.addLayout(results_actions)
        self.comparison_table = QTableWidget(0, 10)
        self.comparison_table.setObjectName("evaluationComparisonTable")
        self.comparison_table.setHorizontalHeaderLabels(
            (
                "PWR NET",
                "Caps Original→Tuned",
                "|Z| @ 1 MHz Original→Tuned",
                "|Z| @ 10 MHz Original→Tuned",
                "|Z| @ 100 MHz Original→Tuned",
                "Max violation Original→Tuned",
                "Baseline",
                "Overall confidence Original→Tuned",
                "Combined convergence Original→Tuned",
                "Solver provenance",
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
        self.evaluation_notes = QTextBrowser()
        self.evaluation_notes.setObjectName("evaluationNotes")
        self.evaluation_notes.setStyleSheet("color: #d6a64f;")
        self._update_evaluation_notes()
        result_details.addTab(self.evaluation_notes, "Notes")
        results_layout.addWidget(result_details, 1)

        section_splitter.addWidget(controls_section)
        section_splitter.addWidget(results_section)
        section_splitter.setStretchFactor(0, 1)
        section_splitter.setStretchFactor(1, 3)
        section_splitter.setSizes((405, 335))
        layout.addWidget(section_splitter, 1)
        return page

    def _build_ai_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = self._section_splitter("aiSectionSplitter")

        controls_section = QWidget()
        controls_section.setObjectName("aiControlsSection")
        controls_section.setMinimumHeight(210)
        controls_layout = QVBoxLayout(controls_section)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        intro = QLabel(
            "Evidence-grounded Plot Analyst only. AI receives solver-derived features "
            "and cannot change PWR assignments, enable decaps, or run optimization."
        )
        intro.setWordWrap(True)
        controls_layout.addWidget(intro)
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
        controls_layout.addLayout(form)
        self.ai_button = QPushButton("Analyze latest plot")
        self.ai_button.setObjectName("aiPlotAnalystButton")
        self.ai_button.clicked.connect(self.run_ai_assist)
        controls_layout.addWidget(self.ai_button)

        self.ai_output = QTextBrowser()
        self.ai_output.setObjectName("aiOutputSection")
        self.ai_output.setMinimumHeight(160)
        splitter.addWidget(controls_section)
        splitter.addWidget(self.ai_output)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes((260, 420))
        layout.addWidget(splitter, 1)
        return page

    def _build_distribution_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("decapDistributionTab")
        layout = QVBoxLayout(page)
        splitter = self._section_splitter("distributionSectionSplitter")

        targets_section = QWidget()
        targets_section.setObjectName("distributionTargetsSection")
        targets_section.setMinimumHeight(280)
        targets_layout = QVBoxLayout(targets_section)
        targets_layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel("PWR NET Distribution Targets")
        heading.setStyleSheet("font-weight: 700;")
        targets_layout.addWidget(heading)
        intro = QLabel(
            "Present is the enabled, model-assigned physical inventory. A cap with "
            "unresolved or floating connectivity is included in Present but remains "
            "fixed on its current PWR NET; the donor check reports only verified "
            "movable capacity. For a donor, "
            "Target is the minimum count to retain (maximum give capacity); for a "
            "receiver it is the requested final count. Unused donor capacity stays "
            "on its current PWR NET. When Target equals Present, Tolerance 0% "
            "excludes the cell; a positive tolerance lets it give and receive the "
            "same number of decaps up to floor(Present × Tolerance / 100). "
            "Ctrl/Shift-select multiple cells of the same field and type once to "
            "fill them together."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #9aa4b2;")
        targets_layout.addWidget(intro)
        alternate_plane_note = QLabel(
            "Alternate PWR plane: VIA STACK CHANGE REQUIRED — exact target-plane "
            "copper is assessed at the immutable PWR landing XY; plane artwork "
            "remains unchanged."
        )
        alternate_plane_note.setObjectName("alternatePwrPlaneRoutingNote")
        alternate_plane_note.setWordWrap(True)
        alternate_plane_note.setStyleSheet("color: #9aa4b2;")
        alternate_plane_note.setToolTip(
            "This is a filled-Cu microvia-stack retarget/rebuild planning result, "
            "and does not prove that the existing via barrel already reaches that layer."
        )
        targets_layout.addWidget(alternate_plane_note)

        self.distribution_table = QTableWidget(0, 1)
        self.distribution_table.setObjectName("distributionTargetTable")
        self.distribution_table.setAccessibleName("Distribution target matrix")
        self.distribution_table.setToolTip(
            "Double-click a cell to open the detached Distribution XLSX window."
        )
        self.distribution_table.setHorizontalHeaderLabels(("PWR NET",))
        self.distribution_table.setAlternatingRowColors(True)
        self.distribution_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectItems
        )
        self.distribution_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.distribution_table.setMinimumHeight(150)
        self.distribution_table.itemChanged.connect(
            self._distribution_target_changed
        )
        self.distribution_table.itemDoubleClicked.connect(
            lambda _item: self._show_distribution_window()
        )
        self.distribution_table.itemDelegate().commitData.connect(
            self._distribution_editor_committed
        )
        targets_layout.addWidget(self.distribution_table, 1)

        self.distribution_validation_label = QLabel(
            "Open an SPD or scenario to define distribution targets."
        )
        self.distribution_validation_label.setObjectName(
            "distributionValidationStatus"
        )
        self.distribution_validation_label.setWordWrap(False)
        self.distribution_validation_label.setToolTip(
            "Per-model numeric Distribution balance. Detailed validation and "
            "preview messages are shown below."
        )
        targets_layout.addWidget(self.distribution_validation_label)

        option_row = QHBoxLayout()
        option_row.addWidget(QLabel("Candidate order"))
        self.distribution_distance_combo = QComboBox()
        self.distribution_distance_combo.setObjectName(
            "distributionDistanceMode"
        )
        self.distribution_distance_combo.addItem(
            "Nearest to receiving PWR NET bumps", "NEAREST"
        )
        self.distribution_distance_combo.addItem(
            "Farthest from receiving PWR NET bumps", "FARTHEST"
        )
        self.distribution_distance_combo.currentIndexChanged.connect(
            self._distribution_option_changed
        )
        option_row.addWidget(self.distribution_distance_combo, 1)
        self.calculate_distribution_button = QPushButton("Calculate Preview")
        self.calculate_distribution_button.setObjectName(
            "calculateDistributionButton"
        )
        self.calculate_distribution_button.clicked.connect(
            self._calculate_distribution
        )
        targets_layout.addLayout(option_row)
        routing_row = QHBoxLayout()
        self.distribution_protect_signal_routing_checkbox = QCheckBox(
            "Experimental: protect immutable signal routing clearances"
        )
        self.distribution_protect_signal_routing_checkbox.setObjectName(
            "distributionProtectSignalRouting"
        )
        self.distribution_protect_signal_routing_checkbox.setChecked(False)
        self.distribution_protect_signal_routing_checkbox.setToolTip(
            "When enabled, BLOCKED and UNKNOWN signal-Trace/via-column candidates "
            "are removed before optimization. This is a provisional research "
            "classifier; initial scope is SIGNAL Trace only."
        )
        self.distribution_protect_signal_routing_checkbox.toggled.connect(
            self._distribution_routing_option_changed
        )
        routing_row.addWidget(self.distribution_protect_signal_routing_checkbox)
        routing_row.addStretch(1)
        routing_row.addWidget(QLabel("Trace-to-via clearance"))
        self.distribution_trace_clearance_edit = QLineEdit()
        self.distribution_trace_clearance_edit.setObjectName(
            "distributionTraceClearanceUm"
        )
        self.distribution_trace_clearance_edit.setAccessibleName(
            "Trace-to-via clearance in micrometres"
        )
        self.distribution_trace_clearance_edit.setPlaceholderText("enter value")
        self.distribution_trace_clearance_edit.setMaximumWidth(110)
        self.distribution_trace_clearance_edit.setEnabled(False)
        self.distribution_trace_clearance_edit.textChanged.connect(
            self._distribution_routing_clearance_changed
        )
        routing_row.addWidget(self.distribution_trace_clearance_edit)
        routing_row.addWidget(QLabel("µm"))
        targets_layout.addLayout(routing_row)
        routing_scope_note = QLabel(
            "Initial protection scope: width-resolved SIGNAL Trace objects. "
            "Routed PWR/GND, signal vias, pins and fanout pads are not yet certified."
        )
        routing_scope_note.setObjectName("distributionSignalRoutingScopeNote")
        routing_scope_note.setWordWrap(True)
        routing_scope_note.setStyleSheet("color: #9aa4b2;")
        targets_layout.addWidget(routing_scope_note)
        calculate_row = QHBoxLayout()
        self.import_distribution_targets_button = QPushButton("Import Targets...")
        self.import_distribution_targets_button.setObjectName(
            "importDistributionTargetsButton"
        )
        self.import_distribution_targets_button.setToolTip(
            "Import absolute Target/Tolerance values from a prior Distribution "
            "workbook. Present is always refreshed from the loaded SPD."
        )
        self.import_distribution_targets_button.clicked.connect(
            self._import_distribution_targets
        )
        calculate_row.addWidget(self.import_distribution_targets_button)
        calculate_row.addStretch(1)
        calculate_row.addWidget(self.calculate_distribution_button)
        targets_layout.addLayout(calculate_row)

        result_section = QWidget()
        result_section.setObjectName("distributionResultsSection")
        result_section.setMinimumHeight(180)
        result_layout = QVBoxLayout(result_section)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_heading = QLabel("Distribution Status / Preview Log")
        result_heading.setStyleSheet("font-weight: 700;")
        result_layout.addWidget(result_heading)
        self.distribution_summary = QTextBrowser()
        self.distribution_summary.setObjectName("distributionPreviewSummary")
        self.distribution_summary.setPlainText(
            "Enter Target counts and optional exchange tolerances, then calculate "
            "a preview. Numeric capacity is "
            "checked before the physical plane, Via, shared-pad, and bump-distance "
            "selection begins."
        )
        result_layout.addWidget(self.distribution_summary, 1)

        result_buttons = QHBoxLayout()
        self.apply_distribution_button = QPushButton("Apply Preview")
        self.apply_distribution_button.setObjectName("applyDistributionButton")
        self.apply_distribution_button.clicked.connect(
            self._apply_distribution_preview
        )
        self.export_distribution_csv_button = QPushButton(
            "Export Full Decap..."
        )
        self.export_distribution_csv_button.setObjectName(
            "exportDistributionCsvButton"
        )
        self.export_distribution_csv_button.clicked.connect(
            self._export_distribution_csv
        )
        self.export_distribution_csv_button.setToolTip(
            "Excel includes Decap results plus PWR NET Distribution Targets "
            "on sheet 2; CSV contains the Decap result table."
        )
        self.save_distribution_button = QPushButton(
            "Save Distributed .spdpi..."
        )
        self.save_distribution_button.setObjectName(
            "saveDistributionScenarioButton"
        )
        self.save_distribution_button.clicked.connect(
            self._save_distribution_scenario
        )
        result_buttons.addStretch(1)
        result_buttons.addWidget(self.apply_distribution_button)
        result_buttons.addWidget(self.export_distribution_csv_button)
        result_layout.addLayout(result_buttons)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self.save_distribution_button)
        result_layout.addLayout(save_row)

        splitter.addWidget(targets_section)
        splitter.addWidget(result_section)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes((440, 280))
        layout.addWidget(splitter, 1)
        return page

    def _distribution_potential_assignable_counts(
        self,
    ) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
        """Cheap load-safe upper bound for legacy GND-only unresolved clusters.

        Opening a large bundle must not rebuild exact artwork.  This pass uses
        only persisted source topology/Via metadata so the UI can enable
        Calculate when the apparent shortage may be repaired.  The worker then
        performs the authoritative retained-artwork proof and repeats numeric
        validation before planning.
        """

        potential = dict(self._distribution_assignable_counts)
        pending: dict[tuple[str, str], int] = {}
        scenario = self._scenario
        analysis = scenario.connection_analysis if scenario is not None else None
        if scenario is None or analysis is None:
            return potential, pending
        decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
        connection_by_key = {
            item.refdes.casefold(): item
            for item in analysis.connections.values()
        }
        prefix = "gnd top component has no source via anchor:"
        for cluster in analysis.clusters:
            if (
                str(getattr(cluster.state, "value", cluster.state)).upper()
                != "UNRESOLVED"
                or len(cluster.member_refdes) < 2
                or not cluster.layer
                or not cluster.power_net
                or not cluster.ground_net
                or not cluster.source_graph_is_connected(cluster.power_edges)
            ):
                continue
            members: list[tuple[Any, Any]] = []
            power_via_count = 0
            ground_via_count = 0
            valid = True
            for refdes in cluster.member_refdes:
                key = refdes.casefold()
                decap = decap_by_key.get(key)
                connection = connection_by_key.get(key)
                reasons = tuple(
                    item.strip().casefold()
                    for item in str(getattr(connection, "reason", "") or "").split(";")
                    if item.strip()
                )
                if (
                    decap is None
                    or connection is None
                    or connection.kind != DecapConnectionKind.UNRESOLVED
                    or connection.cluster_id is None
                    or connection.cluster_id.casefold()
                    != cluster.cluster_id.casefold()
                    or not reasons
                    or not all(item.startswith(prefix) for item in reasons)
                    or decap.pwr_pad.layer is None
                    or decap.gnd_pad.layer is None
                    or decap.pwr_pad.layer.casefold() != cluster.layer.casefold()
                    or decap.gnd_pad.layer.casefold() != cluster.layer.casefold()
                    or decap.source_net.casefold() != cluster.power_net.casefold()
                    or not decap.pwr_pad.padstack
                    or not decap.gnd_pad.padstack
                ):
                    valid = False
                    break
                power_via_count += len(connection.power_vias)
                ground_via_count += len(connection.ground_vias)
                members.append((decap, connection))
            if not valid or power_via_count == 0 or ground_via_count == 0:
                continue
            for decap, _connection in members:
                if not decap.enabled or decap.model_id is None:
                    continue
                cell = (decap.current_rail_id, decap.model_id)
                potential[cell] = potential.get(cell, 0) + 1
                pending[cell] = pending.get(cell, 0) + 1
        return potential, pending

    def _distribution_balance_state(self) -> _DistributionBalanceState:
        """Build numeric balances once, then render strip and log independently."""

        if self._scenario is None or not self._distribution_present_counts:
            return _DistributionBalanceState(
                False, False, (), "No distribution inventory is loaded."
            )
        if self._distribution_invalid_cells:
            return _DistributionBalanceState(
                False,
                True,
                (),
                "Target must be a nonnegative whole number in every edited cell.",
            )
        if self._distribution_invalid_tolerance_cells:
            return _DistributionBalanceState(
                False,
                True,
                (),
                "Tolerance must be a finite percentage from 0 through 100.",
            )

        potential_assignable, _pending_by_cell = (
            self._distribution_potential_assignable_counts()
        )
        balances: list[_DistributionModelBalance] = []
        details: list[str] = []
        shortages: list[str] = []
        has_changes = False
        has_exchange_participants = False
        total_demand = 0
        total_pending_capacity = 0
        for model_id in self._distribution_model_ids:
            capacity = 0
            fixed_capacity = 0
            pending_capacity = 0
            demand = 0
            exchange_cells = 0
            exchange_capacity = 0
            model_changed = False
            for rail_id in self._distribution_rail_ids:
                key = (rail_id, model_id)
                present = self._distribution_present_counts.get(key, 0)
                target = self._distribution_targets.get(key, present)
                if target < present:
                    physical_capacity = present - target
                    verified_assignable = self._distribution_assignable_counts.get(
                        key, 0
                    )
                    assignable = potential_assignable.get(key, verified_assignable)
                    usable_capacity = min(physical_capacity, assignable)
                    verified_capacity = min(
                        physical_capacity, verified_assignable
                    )
                    capacity += usable_capacity
                    pending_capacity += max(
                        usable_capacity - verified_capacity, 0
                    )
                    fixed_capacity += physical_capacity - usable_capacity
                    model_changed = True
                elif target > present:
                    demand += target - present
                    model_changed = True
                elif self._distribution_tolerances.get(key, 0.0) > 0.0:
                    exchange_cells += 1
                    exchange_capacity += _whole_decap_tolerance(
                        present, self._distribution_tolerances[key]
                    )
            has_changes = has_changes or model_changed
            has_exchange_participants = (
                has_exchange_participants or exchange_cells > 0
            )
            total_demand += demand
            total_pending_capacity += pending_capacity
            unused = capacity - demand
            balance = _DistributionModelBalance(
                model_id=model_id,
                donor=capacity,
                receiver=demand,
                balance=unused,
                fixed=fixed_capacity,
                proof_pending=pending_capacity,
                exchange_cells=exchange_cells,
                exchange_capacity=exchange_capacity,
            )
            balances.append(balance)
            detail = (
                f"{model_id}: donor {capacity:,}, receiver {demand:,}, "
                f"balance {unused:+,}"
            )
            if fixed_capacity:
                detail += f", fixed/unassignable {fixed_capacity:,}"
            if pending_capacity:
                detail += f", exact retained-artwork proof pending {pending_capacity:,}"
            if exchange_cells:
                detail += (
                    f", exchange {exchange_cells:,} cell(s) / "
                    f"{exchange_capacity:,} decap(s)"
                )
            details.append(detail)
            if demand > capacity:
                detail = f"{model_id} is short by {demand - capacity:,}"
                if fixed_capacity:
                    detail += (
                        f" ({fixed_capacity:,} donor decap(s) are "
                        "fixed/unassignable)"
                )
                shortages.append(detail)

        if shortages:
            return _DistributionBalanceState(
                False,
                has_changes,
                tuple(balances),
                "Invalid numeric balance.\n"
                + "\n".join(shortages)
                + "\n\n"
                + "\n".join(details),
            )
        if not has_changes:
            if has_exchange_participants:
                return _DistributionBalanceState(
                    False,
                    False,
                    tuple(balances),
                    "Exchange participants are defined, but no donor/receiver "
                    "demand exists; no redistribution is needed.",
                )
            return _DistributionBalanceState(
                False,
                False,
                tuple(balances),
                "No changes requested; every Target equals Present.",
            )
        if total_demand == 0:
            return _DistributionBalanceState(
                False,
                False,
                tuple(balances),
                "No receiver demand is defined; unused donor capacity remains "
                "on its current PWR NET.\n\n"
                + "\n".join(details),
            )
        narrative_prefix = (
            "Exact retained-artwork proof pending for one or more donor cells.\n\n"
            if total_pending_capacity
            else "Numeric donor/receiver balance is valid.\n\n"
        )
        return _DistributionBalanceState(
            True,
            True,
            tuple(balances),
            narrative_prefix + "\n".join(details),
        )

    def _distribution_numeric_state(self) -> tuple[bool, bool, str]:
        """Compatibility wrapper for legacy internal callers and plugins."""

        return self._distribution_balance_state().legacy_tuple()

    def _distribution_routing_policy(self) -> SignalTraceAvoidancePolicy:
        if not self.distribution_protect_signal_routing_checkbox.isChecked():
            return SignalTraceAvoidancePolicy.disabled()
        raw = self.distribution_trace_clearance_edit.text().strip()
        if not raw:
            raise ValueError(
                "Enter Trace-to-via clearance in µm when signal-routing "
                "protection is enabled."
            )
        try:
            clearance_um = float(raw)
        except ValueError as exc:
            raise ValueError("Trace-to-via clearance must be numeric.") from exc
        return SignalTraceAvoidancePolicy.fixed(clearance_um)

    def _distribution_routing_option_changed(self, checked: bool) -> None:
        self.distribution_trace_clearance_edit.setEnabled(
            checked and self._scenario is not None and self._worker is None
        )
        if self._distribution_plan is not None:
            self._clear_distribution_preview(
                "Signal-routing protection changed; calculate a new preview."
            )
        self._update_distribution_validation()

    def _distribution_routing_clearance_changed(self, _text: str) -> None:
        if self._distribution_plan is not None:
            self._clear_distribution_preview(
                "Trace-to-via clearance changed; calculate a new preview."
            )
        self._update_distribution_validation()

    def _update_distribution_validation(self) -> None:
        balance_state = self._distribution_balance_state()
        valid = balance_state.valid
        narrative = balance_state.narrative
        distance_mode = self.distribution_distance_combo.currentData()
        if distance_mode not in {"NEAREST", "FARTHEST"}:
            valid = False
            narrative = (
                "Select Candidate order (Nearest or Farthest) before calculation.\n\n"
                + narrative
            )
        try:
            routing_policy = self._distribution_routing_policy()
        except ValueError as exc:
            valid = False
            narrative = f"{exc}\n\n" + narrative
        else:
            if (
                routing_policy.enabled
                and self._scenario is not None
                and self._scenario.routing_obstacle_asset is None
            ):
                valid = False
                narrative = (
                    "This scenario has no immutable signal-routing asset. Reopen "
                    "the verified source SPD with this version, or turn protection "
                    "OFF.\n\n" + narrative
                )
        if self._distribution_status_notice:
            narrative += f"\n\n{self._distribution_status_notice}"
        if self._distribution_import_notice:
            narrative += f"\n\n{self._distribution_import_notice}"
        self.distribution_validation_label.setText(balance_state.compact_text)
        self.distribution_validation_label.setStyleSheet(
            "color: #047857;" if valid else "color: #B45309;"
        )
        self.distribution_summary.setPlainText(narrative)
        self._update_distribution_controls(balance_state)

    def _update_distribution_controls(
        self,
        numeric_state: tuple[bool, bool, str] | _DistributionBalanceState | None = None,
    ) -> None:
        loaded = self._scenario is not None
        idle = self._worker is None
        if numeric_state is None:
            numeric_state = self._distribution_balance_state()
        if isinstance(numeric_state, _DistributionBalanceState):
            numeric_valid = numeric_state.valid
            has_changes = numeric_state.has_changes
        else:
            numeric_valid, has_changes, _message = numeric_state
        distance_ready = self.distribution_distance_combo.currentData() in {
            "NEAREST",
            "FARTHEST",
        }
        try:
            routing_policy = self._distribution_routing_policy()
            routing_ready = not (
                routing_policy.enabled
                and self._scenario is not None
                and self._scenario.routing_obstacle_asset is None
            )
        except ValueError:
            routing_ready = False
        self.calculate_distribution_button.setEnabled(
            loaded
            and idle
            and numeric_valid
            and has_changes
            and distance_ready
            and routing_ready
        )
        self.import_distribution_targets_button.setEnabled(loaded and idle)
        self.distribution_protect_signal_routing_checkbox.setEnabled(
            loaded and idle
        )
        self.distribution_trace_clearance_edit.setEnabled(
            loaded
            and idle
            and self.distribution_protect_signal_routing_checkbox.isChecked()
        )

        can_apply = False
        if loaded and idle and self._distribution_plan is not None:
            plan_fingerprint = str(
                getattr(self._distribution_plan, "input_design_fingerprint", "")
            )
            can_apply = (
                plan_fingerprint == self._distribution_basis_fingerprint
                and self._distribution_preview_scenario is not None
                and self._distribution_preview_scenario.design_fingerprint
                != self._scenario.design_fingerprint
            )
        self.apply_distribution_button.setEnabled(can_apply)
        has_result = (
            loaded
            and idle
            and self._distribution_preview_scenario is not None
            and bool(self._distribution_export_rows)
        )
        self.export_distribution_csv_button.setEnabled(has_result)
        self.save_distribution_button.setEnabled(has_result)

    def _clear_distribution_preview(
        self,
        reason: str | None = None,
        *,
        update_controls: bool = True,
    ) -> None:
        had_result = bool(
            self._distribution_plan is not None
            or self._distribution_preview_scenario is not None
            or self._distribution_power_projection is not None
            or self._distribution_export_rows
        )
        self._distribution_plan = None
        self._distribution_preview_scenario = None
        self._distribution_power_projection = None
        self._distribution_export_rows = ()
        if had_result:
            self._reset_distribution_assignment_failures()
        if reason is not None:
            self._distribution_status_notice = reason
            self.distribution_summary.setPlainText(reason)
        if update_controls:
            self._update_distribution_controls()

    def _reset_distribution_state(self) -> None:
        self._distribution_basis_fingerprint = None
        self._distribution_present_counts.clear()
        self._distribution_assignable_counts.clear()
        self._distribution_targets.clear()
        self._distribution_tolerances.clear()
        self._distribution_rail_ids = ()
        self._distribution_model_ids = ()
        self._distribution_invalid_cells.clear()
        self._distribution_invalid_tolerance_cells.clear()
        self._distribution_plan = None
        self._distribution_preview_scenario = None
        self._distribution_power_projection = None
        self._distribution_export_rows = ()
        self._distribution_import_notice = None
        self._distribution_status_notice = None
        if hasattr(self, "distribution_distance_combo"):
            previous_block = self.distribution_distance_combo.blockSignals(True)
            try:
                self.distribution_distance_combo.setCurrentIndex(0)
            finally:
                self.distribution_distance_combo.blockSignals(previous_block)
        if hasattr(self, "distribution_protect_signal_routing_checkbox"):
            previous_check_block = (
                self.distribution_protect_signal_routing_checkbox.blockSignals(True)
            )
            previous_clearance_block = (
                self.distribution_trace_clearance_edit.blockSignals(True)
            )
            try:
                self.distribution_protect_signal_routing_checkbox.setChecked(False)
                self.distribution_trace_clearance_edit.clear()
                self.distribution_trace_clearance_edit.setEnabled(False)
            finally:
                self.distribution_protect_signal_routing_checkbox.blockSignals(
                    previous_check_block
                )
                self.distribution_trace_clearance_edit.blockSignals(
                    previous_clearance_block
                )
        self._distribution_table_updating = True
        try:
            self.distribution_table.clear()
            self.distribution_table.setRowCount(0)
            self.distribution_table.setColumnCount(1)
            self.distribution_table.setHorizontalHeaderLabels(("PWR NET",))
        finally:
            self._distribution_table_updating = False
        self.distribution_validation_label.setText(
            "Open an SPD or scenario to define distribution targets."
        )
        self.distribution_validation_label.setStyleSheet("color: #9aa4b2;")
        self.distribution_summary.setPlainText(
            "Enter Target counts and optional exchange tolerances, then calculate "
            "a preview."
        )
        self._update_distribution_controls()

    def _canonical_distribution_counts(
        self,
        raw_counts: Any,
        rail_ids: tuple[str, ...],
        model_ids: tuple[str, ...],
    ) -> dict[tuple[str, str], int]:
        values = getattr(raw_counts, "counts", raw_counts)
        if not isinstance(values, dict):
            raise TypeError("distribution inventory must be keyed by rail and model")
        rail_names = {rail_id.casefold(): rail_id for rail_id in rail_ids}
        model_names = {model_id.casefold(): model_id for model_id in model_ids}
        result = {
            (rail_id, model_id): 0
            for rail_id in rail_ids
            for model_id in model_ids
        }
        for raw_key, raw_value in values.items():
            if not isinstance(raw_key, tuple) or len(raw_key) != 2:
                raise TypeError("distribution inventory keys must be (rail_id, model_id)")
            rail = rail_names.get(str(raw_key[0]).casefold())
            model = model_names.get(str(raw_key[1]).casefold())
            if rail is None or model is None:
                continue
            value = int(raw_value)
            if value < 0:
                raise ValueError("distribution inventory cannot be negative")
            result[(rail, model)] = value
        return result

    def _populate_distribution_table(
        self,
        scenario: ScenarioSpec,
        *,
        preserve_result: bool = False,
        prepared_counts: dict[tuple[str, str], int] | None = None,
        prepared_assignable_counts: dict[tuple[str, str], int] | None = None,
        prepared_fingerprint: str | None = None,
        project: ProjectSpec | None = None,
    ) -> None:
        self._distribution_import_notice = None
        if self.distribution_distance_combo.currentIndex() < 0:
            previous_combo_block = self.distribution_distance_combo.blockSignals(True)
            try:
                self.distribution_distance_combo.setCurrentIndex(0)
            finally:
                self.distribution_distance_combo.blockSignals(previous_combo_block)
        preserved_tolerances = (
            {
                (rail_id.casefold(), model_id.casefold()): value
                for (rail_id, model_id), value in self._distribution_tolerances.items()
            }
            if preserve_result
            else {}
        )
        preserved_targets = (
            {
                (rail_id.casefold(), model_id.casefold()): value
                for (rail_id, model_id), value in self._distribution_targets.items()
            }
            if preserve_result
            else {}
        )
        if prepared_counts is None or prepared_assignable_counts is None:
            raw_counts, raw_assignable_counts = _distribution_inventory_counts_for_preview(
                scenario
            )
        else:
            raw_counts = prepared_counts
            raw_assignable_counts = prepared_assignable_counts
        self._scenario = scenario
        project = project if project is not None else scenario.base_project
        rails = tuple(project.rails)
        models = tuple(project.cap_models)
        rail_ids = tuple(rail.rail_id for rail in rails)
        model_ids = tuple(model.model_id for model in models)
        counts = self._canonical_distribution_counts(raw_counts, rail_ids, model_ids)
        assignable_counts = self._canonical_distribution_counts(
            raw_assignable_counts, rail_ids, model_ids
        )
        self._distribution_rail_ids = rail_ids
        self._distribution_model_ids = model_ids
        self._distribution_present_counts = counts
        self._distribution_assignable_counts = assignable_counts
        self._distribution_targets = {
            (rail_id, model_id): int(
                preserved_targets.get(
                    (rail_id.casefold(), model_id.casefold()),
                    counts[(rail_id, model_id)],
                )
            )
            for rail_id in rail_ids
            for model_id in model_ids
        }
        self._distribution_tolerances = {
            (rail_id, model_id): float(
                preserved_tolerances.get(
                    (rail_id.casefold(), model_id.casefold()), 0.0
                )
            )
            for rail_id in rail_ids
            for model_id in model_ids
        }
        self._distribution_invalid_cells.clear()
        self._distribution_invalid_tolerance_cells.clear()
        self._distribution_basis_fingerprint = (
            prepared_fingerprint
            if prepared_fingerprint is not None
            else scenario.design_fingerprint
        )
        if not preserve_result:
            self._distribution_plan = None
            self._distribution_preview_scenario = None
            self._distribution_power_projection = None
            self._distribution_export_rows = ()
            self.distribution_summary.setPlainText(
                "Enter Target counts and calculate a preview. Numeric capacity is "
                "checked before physical assignment."
            )

        headers = ["PWR NET"]
        for model in models:
            headers.extend(
                (
                    f"{model.model_id}\nPresent",
                    f"{model.model_id}\nTarget",
                    f"{model.model_id}\nTolerance (%)",
                    f"{model.model_id}\nAssignment Failed",
                )
            )
        self._distribution_table_updating = True
        previous_block = self.distribution_table.blockSignals(True)
        try:
            self.distribution_table.clear()
            self.distribution_table.setSortingEnabled(False)
            self.distribution_table.setRowCount(len(rails))
            self.distribution_table.setColumnCount(len(headers))
            self.distribution_table.setHorizontalHeaderLabels(headers)
            for row, rail in enumerate(rails):
                rail_item = QTableWidgetItem(f"{rail.net} ({rail.rail_id})")
                rail_item.setData(Qt.ItemDataRole.UserRole, rail.rail_id)
                rail_item.setFlags(
                    rail_item.flags() & ~Qt.ItemFlag.ItemIsEditable
                )
                rail_item.setToolTip(
                    f"{rail.net} | PWR {rail.pwr_layer} | GND {rail.gnd_layer}"
                )
                self.distribution_table.setItem(row, 0, rail_item)
                for model_index, model in enumerate(models):
                    key = (rail.rail_id, model.model_id)
                    present = counts.get(key, 0)
                    present_column = 1 + model_index * 4
                    target_column = present_column + 1
                    tolerance_column = present_column + 2
                    failure_column = present_column + 3
                    present_item = _DistributionNumericItem(f"{present:d}")
                    present_item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    present_item.setFlags(
                        present_item.flags() & ~Qt.ItemFlag.ItemIsEditable
                    )
                    present_item.setToolTip(
                        "Enabled, model-assigned physical inventory. Verified DIRECT/"
                        "SHARED parts may move; floating or unresolved parts are counted "
                        "but fixed on their current NET."
                    )
                    target_item = _DistributionNumericItem(
                        f"{self._distribution_targets[key]:d}"
                    )
                    target_item.setData(Qt.ItemDataRole.UserRole, key)
                    target_item.setData(
                        _DISTRIBUTION_FIELD_ROLE, _DISTRIBUTION_TARGET_FIELD
                    )
                    target_item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    target_item.setToolTip(
                        "Below Present = maximum give capacity; above Present = "
                        "receive demand; equal uses Tolerance to include or exclude"
                    )
                    tolerance = self._distribution_tolerances[key]
                    tolerance_item = _DistributionNumericItem(f"{tolerance:g}")
                    tolerance_item.setData(Qt.ItemDataRole.UserRole, key)
                    tolerance_item.setData(
                        _DISTRIBUTION_FIELD_ROLE,
                        _DISTRIBUTION_TOLERANCE_FIELD,
                    )
                    tolerance_item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    tolerance_item.setToolTip(
                        "Used only when Target equals Present. 0% = excluded; "
                        "positive = equal give/receive turnover limited to "
                        "floor(Present × Tolerance / 100)."
                    )
                    failure_item = _DistributionNumericItem("0")
                    failure_item.setData(Qt.ItemDataRole.UserRole, key)
                    failure_item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    failure_item.setFlags(
                        failure_item.flags() & ~Qt.ItemFlag.ItemIsEditable
                    )
                    failure_item.setToolTip(
                        "Unfulfilled receiver demand: Target minus Actual. Donor "
                        "unused capacity and optional exchange turnover are not failures."
                    )
                    self.distribution_table.setItem(
                        row, present_column, present_item
                    )
                    self.distribution_table.setItem(row, target_column, target_item)
                    self.distribution_table.setItem(
                        row, tolerance_column, tolerance_item
                    )
                    self.distribution_table.setItem(
                        row, failure_column, failure_item
                    )
                    self._style_distribution_target_item(target_item, key)
                    self._style_distribution_tolerance_item(tolerance_item, key)
            self.distribution_table.setColumnWidth(0, 220)
            for column in range(1, len(headers)):
                self.distribution_table.setColumnWidth(column, 92)
        finally:
            self.distribution_table.blockSignals(previous_block)
            self._distribution_table_updating = False
        self.distribution_table.setSortingEnabled(True)
        self._update_distribution_validation()
        self._sync_distribution_window()

    def _refresh_distribution_tab(
        self,
        prepared_counts: dict[tuple[str, str], int] | None = None,
        prepared_assignable_counts: dict[tuple[str, str], int] | None = None,
        prepared_fingerprint: str | None = None,
        project: ProjectSpec | None = None,
    ) -> None:
        scenario = self._scenario
        if scenario is None:
            self._reset_distribution_state()
            return
        fingerprint = (
            prepared_fingerprint
            if prepared_fingerprint is not None
            else scenario.design_fingerprint
        )
        if (
            fingerprint == self._distribution_basis_fingerprint
            and self.distribution_table.rowCount()
        ):
            self._update_distribution_controls()
            return
        self._populate_distribution_table(
            scenario,
            prepared_counts=prepared_counts,
            prepared_assignable_counts=prepared_assignable_counts,
            prepared_fingerprint=prepared_fingerprint,
            project=project,
        )

    def _style_distribution_target_item(
        self, item: QTableWidgetItem, key: tuple[str, str]
    ) -> None:
        previous_block = self.distribution_table.blockSignals(True)
        try:
            if key in self._distribution_invalid_cells:
                item.setBackground(QColor("#FECACA"))
                return
            present = self._distribution_present_counts.get(key, 0)
            target = self._distribution_targets.get(key, present)
            if target < present:
                item.setBackground(QColor("#DBEAFE"))
            elif target > present:
                item.setBackground(QColor("#FEF3C7"))
            elif self._distribution_tolerances.get(key, 0.0) > 0.0:
                item.setBackground(QColor("#EDE9FE"))
            else:
                item.setBackground(QColor("#E5E7EB"))
        finally:
            self.distribution_table.blockSignals(previous_block)

    def _style_distribution_tolerance_item(
        self, item: QTableWidgetItem, key: tuple[str, str]
    ) -> None:
        previous_block = self.distribution_table.blockSignals(True)
        try:
            if key in self._distribution_invalid_tolerance_cells:
                item.setBackground(QColor("#FECACA"))
                return
            present = self._distribution_present_counts.get(key, 0)
            target = self._distribution_targets.get(key, present)
            tolerance = self._distribution_tolerances.get(key, 0.0)
            if target == present and tolerance > 0.0:
                item.setBackground(QColor("#EDE9FE"))
            else:
                item.setBackground(QColor("#E5E7EB"))
        finally:
            self.distribution_table.blockSignals(previous_block)

    def _style_distribution_edit_pair(
        self, item: QTableWidgetItem, key: tuple[str, str]
    ) -> None:
        field = item.data(_DISTRIBUTION_FIELD_ROLE)
        if field == _DISTRIBUTION_TARGET_FIELD:
            target_item = item
            tolerance_item = self.distribution_table.item(
                item.row(), item.column() + 1
            )
        else:
            target_item = self.distribution_table.item(
                item.row(), item.column() - 1
            )
            tolerance_item = item
        if target_item is not None:
            self._style_distribution_target_item(target_item, key)
        if tolerance_item is not None:
            self._style_distribution_tolerance_item(tolerance_item, key)

    def _selected_distribution_target_items(
        self, item: QTableWidgetItem
    ) -> tuple[QTableWidgetItem, ...]:
        # Editing the current item does not clear an ExtendedSelection in Qt.
        # Fill only editable cells of the same field. A mixed Target/Tolerance
        # selection must never copy an integer target into a percentage cell.
        if not item.isSelected():
            return (item,)
        field = item.data(_DISTRIBUTION_FIELD_ROLE)
        if field not in {
            _DISTRIBUTION_TARGET_FIELD,
            _DISTRIBUTION_TOLERANCE_FIELD,
        }:
            return (item,)
        target_items = tuple(
            candidate
            for candidate in self.distribution_table.selectedItems()
            if candidate.flags() & Qt.ItemFlag.ItemIsEditable
            and isinstance(
                candidate.data(Qt.ItemDataRole.UserRole), tuple
            )
            and len(candidate.data(Qt.ItemDataRole.UserRole)) == 2
            and candidate.data(_DISTRIBUTION_FIELD_ROLE) == field
        )
        if (
            len(target_items) < 2
            or all(candidate is not item for candidate in target_items)
        ):
            return (item,)
        return target_items

    def _apply_distribution_target_text(
        self,
        item: QTableWidgetItem,
        target_items: tuple[QTableWidgetItem, ...],
    ) -> None:
        text = item.text().strip()
        field = item.data(_DISTRIBUTION_FIELD_ROLE)
        value: int | float | None
        if field == _DISTRIBUTION_TARGET_FIELD:
            try:
                value = int(text)
                if value < 0 or text.startswith(("+", "-")):
                    raise ValueError
            except ValueError:
                value = None
        elif field == _DISTRIBUTION_TOLERANCE_FIELD:
            try:
                value = float(text)
                if not isfinite(value) or not 0.0 <= value <= 100.0:
                    raise ValueError
            except ValueError:
                value = None
        else:
            return

        previous_updating = self._distribution_table_updating
        self._distribution_table_updating = True
        previous_block = self.distribution_table.blockSignals(True)
        try:
            for target_item in target_items:
                target_key = target_item.data(Qt.ItemDataRole.UserRole)
                key = (str(target_key[0]), str(target_key[1]))
                if target_item is not item and target_item.text() != item.text():
                    target_item.setText(item.text())
                if field == _DISTRIBUTION_TARGET_FIELD:
                    if value is None:
                        self._distribution_invalid_cells.add(key)
                    else:
                        self._distribution_invalid_cells.discard(key)
                        self._distribution_targets[key] = int(value)
                else:
                    if value is None:
                        self._distribution_invalid_tolerance_cells.add(key)
                    else:
                        self._distribution_invalid_tolerance_cells.discard(key)
                        self._distribution_tolerances[key] = float(value)
                self._style_distribution_edit_pair(target_item, key)
        finally:
            self.distribution_table.blockSignals(previous_block)
            self._distribution_table_updating = previous_updating

    def _distribution_targets_edited(self) -> None:
        self._clear_distribution_preview(
            "Targets or tolerances changed; calculate a new De-cap Distribution "
            "preview.",
            update_controls=False,
        )
        self._update_distribution_validation()
        self._sync_distribution_window()

    def _distribution_target_changed(self, item: QTableWidgetItem) -> None:
        if self._distribution_table_updating:
            return
        if not item.flags() & Qt.ItemFlag.ItemIsEditable:
            return
        raw_key = item.data(Qt.ItemDataRole.UserRole)
        if (
            not isinstance(raw_key, tuple)
            or len(raw_key) != 2
            or item.data(_DISTRIBUTION_FIELD_ROLE)
            not in {
                _DISTRIBUTION_TARGET_FIELD,
                _DISTRIBUTION_TOLERANCE_FIELD,
            }
        ):
            return
        target_items = self._selected_distribution_target_items(item)
        self._apply_distribution_target_text(item, target_items)
        self._distribution_targets_edited()

    def _distribution_editor_committed(self, _editor: QWidget) -> None:
        # itemChanged is not emitted when the editor commits text identical to
        # the current source cell.  Defer until the view has copied editor data
        # so an unchanged source can still fill differently-valued peer cells.
        item = self.distribution_table.currentItem()
        if item is None:
            return
        target_items = self._selected_distribution_target_items(item)
        if len(target_items) < 2:
            return
        QTimer.singleShot(
            0,
            lambda source=item, targets=target_items: (
                self._fill_selected_distribution_targets_after_commit(
                    source, targets
                )
            ),
        )

    def _fill_selected_distribution_targets_after_commit(
        self,
        item: QTableWidgetItem,
        target_items: tuple[QTableWidgetItem, ...],
    ) -> None:
        if self._distribution_table_updating:
            return
        try:
            if item.tableWidget() is not self.distribution_table:
                return
        except RuntimeError:
            return
        raw_key = item.data(Qt.ItemDataRole.UserRole)
        if (
            not item.flags() & Qt.ItemFlag.ItemIsEditable
            or not isinstance(raw_key, tuple)
            or len(raw_key) != 2
        ):
            return
        try:
            if any(
                candidate.tableWidget() is not self.distribution_table
                for candidate in target_items
            ):
                return
            peers_already_match = all(
                candidate.text() == item.text() for candidate in target_items
            )
        except RuntimeError:
            return
        if peers_already_match:
            return
        self._apply_distribution_target_text(item, target_items)
        self._distribution_targets_edited()

    def _reset_distribution_assignment_failures(self) -> None:
        if not hasattr(self, "distribution_table"):
            return
        self._distribution_table_updating = True
        sorting_enabled = self.distribution_table.isSortingEnabled()
        header = self.distribution_table.horizontalHeader()
        sort_section = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        self.distribution_table.setSortingEnabled(False)
        previous_block = self.distribution_table.blockSignals(True)
        try:
            for row in range(self.distribution_table.rowCount()):
                for column in range(4, self.distribution_table.columnCount(), 4):
                    item = self.distribution_table.item(row, column)
                    if item is not None:
                        item.setText("0")
                        item.setBackground(QBrush())
        finally:
            self.distribution_table.blockSignals(previous_block)
            self.distribution_table.setSortingEnabled(sorting_enabled)
            if sorting_enabled and sort_section >= 0:
                self.distribution_table.sortItems(sort_section, sort_order)
            self._distribution_table_updating = False

    def _distribution_matrix_values(self) -> tuple[tuple[str, ...], tuple[tuple[object, ...], ...]]:
        headers = tuple(
            self.distribution_table.horizontalHeaderItem(column).text()
            for column in range(self.distribution_table.columnCount())
        )
        rows = tuple(
            tuple(
                self.distribution_table.item(row, column).text()
                if self.distribution_table.item(row, column) is not None
                else ""
                for column in range(self.distribution_table.columnCount())
            )
            for row in range(self.distribution_table.rowCount())
        )
        return headers, rows

    def _distribution_request_fingerprint(self) -> str:
        """Fingerprint every planner input editable from either Distribution view."""

        scenario = self._scenario
        if scenario is None:
            return ""
        target_items = tuple(
            sorted(
                (str(rail_id).casefold(), str(model_id).casefold(), int(value))
                for (rail_id, model_id), value in self._distribution_targets.items()
            )
        )
        tolerance_items = tuple(
            sorted(
                (
                    str(rail_id).casefold(),
                    str(model_id).casefold(),
                    float(value),
                )
                for (rail_id, model_id), value in self._distribution_tolerances.items()
            )
        )
        legacy_inputs: tuple[object, ...] = (
            scenario.design_fingerprint,
            scenario.revision,
            target_items,
            tolerance_items,
            self.distribution_distance_combo.currentData(),
        )
        try:
            routing_policy = self._distribution_routing_policy()
        except ValueError:
            request_inputs = (
                *legacy_inputs,
                (
                    "INVALID",
                    self.distribution_protect_signal_routing_checkbox.isChecked(),
                    self.distribution_trace_clearance_edit.text().strip(),
                ),
            )
        else:
            if not routing_policy.enabled:
                request_inputs = legacy_inputs
            else:
                reference = scenario.routing_obstacle_asset
                asset_identity = (
                    None
                    if reference is None
                    else (
                        reference.attachment_name,
                        reference.attachment_sha256,
                        reference.content_sha256,
                        reference.schema_version,
                        reference.compiler_policy,
                    )
                )
                request_inputs = (
                    *legacy_inputs,
                    routing_policy.fingerprint,
                    asset_identity,
                )
        payload = repr(request_inputs).encode("utf-8")
        return sha256(payload).hexdigest()

    def _distribution_routing_metadata(self, plan: Any | None = None) -> dict[str, object]:
        summary = getattr(plan, "routing_summary", None) if plan is not None else None
        if summary is not None:
            policy = summary.policy
            result: dict[str, object] = {
                "Signal Routing Protection": "ON",
                "Routing Protection Scope": policy.scope.value,
                "Routing Policy Version": policy.policy_version,
                "Routing Clearance Mode": policy.clearance_mode.value,
                "Routing Asset SHA-256": summary.asset_attachment_sha256,
                "Routing Asset Content SHA-256": summary.asset_content_sha256,
                "Routing Checked Candidates": summary.checked_count,
                "Routing Safe Candidates": summary.safe_count,
                "Routing Blocked Candidates": summary.blocked_count,
                "Routing Unknown Candidates": summary.unknown_count,
                "Routing Compiler Policy": summary.compiler_policy,
                "Routing Production Ready": summary.production_ready,
            }
            if policy.clearance_um is not None:
                result["Trace-to-via Clearance (um)"] = policy.clearance_um
            return result
        policy = self._distribution_routing_policy()
        if not policy.enabled:
            return {}
        result = {
            "Signal Routing Protection": "ON",
            "Routing Protection Scope": policy.scope.value,
            "Routing Policy Version": policy.policy_version,
            "Routing Clearance Mode": policy.clearance_mode.value,
        }
        if policy.clearance_um is not None:
            result["Trace-to-via Clearance (um)"] = policy.clearance_um
        scenario = self._scenario
        reference = scenario.routing_obstacle_asset if scenario is not None else None
        if reference is None:
            raise ValueError(
                "Signal-routing protection cannot be exported without a routing asset."
            )
        result.update(
            {
                "Routing Asset SHA-256": reference.attachment_sha256,
                "Routing Asset Content SHA-256": reference.content_sha256,
                "Routing Compiler Policy": reference.compiler_policy,
                "Routing Production Ready": reference.production_ready,
            }
        )
        return result

    def _distribution_workbook_contract(
        self, plan: Any | None = None
    ) -> tuple[int, dict[str, object]]:
        from ..distribution_workbook import (
            DISTRIBUTION_LEGACY_OFF_WORKBOOK_FORMAT_VERSION,
            DISTRIBUTION_WORKBOOK_FORMAT_VERSION,
        )

        routing_metadata = self._distribution_routing_metadata(plan)
        return (
            (
                DISTRIBUTION_WORKBOOK_FORMAT_VERSION
                if routing_metadata
                else DISTRIBUTION_LEGACY_OFF_WORKBOOK_FORMAT_VERSION
            ),
            routing_metadata,
        )

    def _sync_distribution_window(self) -> None:
        self._sync_board_assignment_controls()
        if self._distribution_window is None:
            return
        self._distribution_window.set_document_active(
            self._scenario is not None,
            busy=self._worker is not None,
            source_comparison_available=self._source_board_comparison_available(),
        )
        headers, rows = self._distribution_matrix_values()
        self._distribution_window.set_matrix(headers, rows)
        self._distribution_window.set_original_board_checked(
            self._distribution_show_original_board
        )

    def _source_board_comparison_available(self) -> bool:
        if self._source_board_comparison_cache is not None:
            return self._source_board_comparison_cache
        scenario = self._scenario
        if scenario is None:
            return False
        source_models = _source_model_ids_by_refdes(scenario)
        available = any(
            decap.current_net.casefold() != decap.source_net.casefold()
            or decap.current_rail_id.casefold() != decap.source_rail_id.casefold()
            or (decap.model_id or "").casefold()
            != (source_models.get(decap.refdes.casefold()) or "").casefold()
            or decap.enabled != decap.source_mounted
            or decap.pad_state != DecapPadState.NORMAL
            for decap in scenario.decaps
        )
        self._source_board_comparison_cache = available
        return available

    def _sync_board_assignment_controls(self) -> None:
        if not hasattr(self, "source_board_checkbox"):
            return
        available = self._source_board_comparison_available()
        if not available:
            self._distribution_show_original_board = False
        previous = self.source_board_checkbox.blockSignals(True)
        try:
            self.source_board_checkbox.setChecked(
                self._distribution_show_original_board
            )
        finally:
            self.source_board_checkbox.blockSignals(previous)
        self.source_board_checkbox.setEnabled(
            available and self._worker is None
        )
        if self._scenario is None:
            text = "Board: No document"
            style = "color: #9aa4b2;"
        elif self._distribution_show_original_board:
            text = "Board: Source SPD (read-only)"
            style = "color: #d6a64f; font-weight: 700;"
        elif available:
            text = "Board: Current / distributed"
            style = "color: #7aa2c7; font-weight: 600;"
        else:
            text = "Board: Current (matches source)"
            style = "color: #9aa4b2;"
        self.board_assignment_view_label.setText(text)
        self.board_assignment_view_label.setStyleSheet(style)

    def _show_distribution_window(self) -> None:
        if self._scenario is None:
            return
        if self._distribution_window is None:
            self._distribution_window = DistributionTargetsWindow(self)
            self._distribution_window.importRequested.connect(
                self._import_distribution_targets
            )
            self._distribution_window.exportTemplateRequested.connect(
                self._export_distribution_template
            )
            self._distribution_window.originalBoardToggled.connect(
                self._show_original_distribution_board
            )
        self._sync_distribution_window()
        self._distribution_window.show()
        self._distribution_window.raise_()
        self._distribution_window.activateWindow()

    def _export_distribution_template(self) -> None:
        scenario = self._scenario
        if scenario is None:
            return
        default_name = f"{Path(scenario.source.name).stem}_distribution_targets.xlsx"
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export De-cap Distribution Target Template",
            default_name,
            "Excel workbook (*.xlsx);;All files (*)",
        )
        if not filename:
            return
        path = Path(filename).with_suffix(".xlsx")
        headers, rows = self._distribution_matrix_values()
        try:
            from ..spreadsheet_export import write_distribution_workbook

            raw_distance_mode = self.distribution_distance_combo.currentData()
            format_version, routing_metadata = self._distribution_workbook_contract()
            metadata: dict[str, object] = {
                "Format Version": format_version,
                "Application Version": __version__,
                "Source SPD Name": scenario.source.name,
                "Source SPD SHA-256": scenario.source.sha256,
                "Input Design Fingerprint": scenario.design_fingerprint,
            }
            if raw_distance_mode in {"NEAREST", "FARTHEST"}:
                metadata["Distance Mode"] = raw_distance_mode
            metadata.update(routing_metadata)
            write_distribution_workbook(path, (), headers, rows, metadata=metadata)
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(
                self,
                APP_DISPLAY_NAME,
                f"The De-cap Distribution target template could not be written.\n\n{exc}",
            )
            self.status_text.setText("De-cap Distribution template export failed")
            return
        self.status_text.setText(f"Exported Distribution target template to {path.name}")

    def _import_distribution_targets(self) -> None:
        scenario = self._scenario
        if scenario is None:
            return
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Import De-cap Distribution Targets",
            "",
            "Excel workbook (*.xlsx);;All files (*)",
        )
        if not filename:
            return
        try:
            from ..distribution_workbook import (
                DistributionWorkbookError,
                load_distribution_targets,
            )

            imported = load_distribution_targets(
                filename,
                rail_ids=self._distribution_rail_ids,
                model_ids=self._distribution_model_ids,
                current_present=dict(self._distribution_present_counts),
                current_source_sha256=scenario.source.sha256,
                current_design_fingerprint=scenario.design_fingerprint,
                current_routing_asset_sha256=(
                    scenario.routing_obstacle_asset.attachment_sha256
                    if scenario.routing_obstacle_asset is not None
                    else None
                ),
                current_routing_asset_content_sha256=(
                    scenario.routing_obstacle_asset.content_sha256
                    if scenario.routing_obstacle_asset is not None
                    else None
                ),
            )
            if (
                imported.routing_protection_enabled
                and scenario.routing_obstacle_asset is None
            ):
                raise DistributionWorkbookError(
                    "protected target workbook requires a routing asset, but the "
                    "loaded scenario has none"
                )
        except (OSError, ValueError) as exc:
            # DistributionWorkbookError is a ValueError; keep this boundary broad
            # enough for filesystem and dependency-level workbook failures.
            QMessageBox.critical(
                self,
                APP_DISPLAY_NAME,
                f"The Distribution targets could not be imported.\n\n{exc}",
            )
            self.status_text.setText("De-cap Distribution target import failed")
            return

        self._clear_distribution_preview(update_controls=False)
        self._distribution_targets = dict(imported.targets)
        self._distribution_tolerances = dict(imported.tolerances)
        self._distribution_invalid_cells.clear()
        self._distribution_invalid_tolerance_cells.clear()

        self._distribution_table_updating = True
        previous_block = self.distribution_table.blockSignals(True)
        try:
            for row in range(self.distribution_table.rowCount()):
                rail_item = self.distribution_table.item(row, 0)
                if rail_item is None:
                    continue
                rail_id = str(rail_item.data(Qt.ItemDataRole.UserRole))
                for model_index, model_id in enumerate(self._distribution_model_ids):
                    key = (rail_id, model_id)
                    target_item = self.distribution_table.item(
                        row, 2 + model_index * 4
                    )
                    tolerance_item = self.distribution_table.item(
                        row, 3 + model_index * 4
                    )
                    if target_item is not None:
                        target_item.setText(str(self._distribution_targets[key]))
                        self._style_distribution_target_item(target_item, key)
                    if tolerance_item is not None:
                        tolerance_item.setText(
                            f"{self._distribution_tolerances[key]:g}"
                        )
                        self._style_distribution_tolerance_item(tolerance_item, key)
        finally:
            self.distribution_table.blockSignals(previous_block)
            self._distribution_table_updating = False

        previous_combo_block = self.distribution_distance_combo.blockSignals(True)
        try:
            if imported.distance_mode is None:
                self.distribution_distance_combo.setCurrentIndex(-1)
                self.distribution_distance_combo.setPlaceholderText(
                    "Select candidate order (not recorded in workbook)"
                )
            else:
                index = self.distribution_distance_combo.findData(
                    imported.distance_mode
                )
                if index < 0:
                    raise DistributionWorkbookError(
                        f"unsupported Candidate order {imported.distance_mode!r}"
                    )
                self.distribution_distance_combo.setCurrentIndex(index)
        finally:
            self.distribution_distance_combo.blockSignals(previous_combo_block)

        previous_check_block = (
            self.distribution_protect_signal_routing_checkbox.blockSignals(True)
        )
        previous_clearance_block = self.distribution_trace_clearance_edit.blockSignals(
            True
        )
        try:
            self.distribution_protect_signal_routing_checkbox.setChecked(
                imported.routing_protection_enabled
            )
            self.distribution_trace_clearance_edit.setText(
                ""
                if imported.routing_clearance_um is None
                else f"{imported.routing_clearance_um:g}"
            )
        finally:
            self.distribution_protect_signal_routing_checkbox.blockSignals(
                previous_check_block
            )
            self.distribution_trace_clearance_edit.blockSignals(
                previous_clearance_block
            )

        summary = imported.summary(Path(filename).name)
        self._distribution_import_notice = summary
        self.distribution_summary.setPlainText(
            summary
            + "\n\nReview the refreshed donor/receiver balance, then calculate a new "
            "preview. Previous result columns were ignored."
        )
        self._reset_distribution_assignment_failures()
        self._update_distribution_validation()
        self._sync_distribution_window()
        self.status_text.setText(
            f"Imported Distribution targets from {Path(filename).name}"
        )

    def _distribution_option_changed(self, _index: int) -> None:
        if self._distribution_plan is not None:
            self._clear_distribution_preview(
                "Candidate order changed; calculate a new preview."
            )
        self._update_distribution_validation()

    def _calculate_distribution(self) -> None:
        if self._scenario is None:
            return
        balance_state = self._distribution_balance_state()
        numeric_valid = balance_state.valid
        has_changes = balance_state.has_changes
        message = balance_state.narrative
        if not numeric_valid or not has_changes:
            QMessageBox.warning(self, APP_DISPLAY_NAME, message)
            return
        try:
            from .. import distribution as distribution_module

            validate = getattr(
                distribution_module, "validate_distribution_targets", None
            )
            exact_proof_pending = any(
                item.proof_pending for item in balance_state.balances
            )
            if validate is not None and not exact_proof_pending:
                validate(
                    self._scenario,
                    dict(self._distribution_targets),
                    dict(self._distribution_tolerances),
                )
            distance_mode = distribution_module.DistributionDistanceMode(
                str(self.distribution_distance_combo.currentData()).upper()
            )
            routing_policy = self._distribution_routing_policy()
        except (TypeError, ValueError) as exc:
            diagnostic_details = tuple(getattr(exc, "diagnostics", ()))
            detail_text = "; ".join(
                str(getattr(item, "message", item)) for item in diagnostic_details
            )
            error_text = str(exc)
            if detail_text:
                error_text += f"\n\n{detail_text}"
            QMessageBox.warning(self, APP_DISPLAY_NAME, error_text)
            self._distribution_status_notice = f"Invalid input: {error_text}"
            self._update_distribution_validation()
            return
        request_fingerprint = self._distribution_request_fingerprint()
        worker = FunctionWorker(
            _job_compute_distribution,
            self._scenario,
            dict(self._attachments),
            dict(self._distribution_targets),
            dict(self._distribution_tolerances),
            distance_mode,
            routing_policy,
        )
        self._run_worker(
            worker,
            lambda result, expected=request_fingerprint: (
                self._accept_distribution_plan_if_current(result, expected)
            ),
            label="Calculating De-cap Distribution preview...",
            # HiGHS cannot be interrupted inside one solve call, but the
            # planner checks cancellation between factor blocks and stages.
            # Keep Cancel available so a long real-board run stops at the next
            # safe boundary instead of forcing the user to wait for every stage.
            cancelable=True,
        )

    @staticmethod
    def _plan_status_text(plan: Any) -> str:
        status = getattr(plan, "status", "UNKNOWN")
        return str(getattr(status, "value", status)).upper()

    @staticmethod
    def _plan_cell_value(cell: Any, *names: str, default: Any = None) -> Any:
        for name in names:
            if isinstance(cell, dict) and name in cell:
                return cell[name]
            if hasattr(cell, name):
                return getattr(cell, name)
        return default

    def _distribution_plan_summary(self, plan: Any) -> str:
        status = self._plan_status_text(plan)
        diagnostics = tuple(getattr(plan, "diagnostics", ()))
        evaluation_blocked = any(
            str(getattr(item, "code", "")).upper()
            == "PREEXISTING_UNRESOLVED_EVALUATION_RAILS"
            for item in diagnostics
        )
        changes = tuple(
            getattr(plan, "changes", getattr(plan, "moves", ()))
        )
        sacrifices = tuple(getattr(plan, "sacrifices", ()))
        lines = [
            f"Status: {status} (count/topology)",
            "VIA STACK CHANGE REQUIRED — exact target plane exists at immutable "
            "PWR landing XY; plane artwork unchanged",
            (
                "PDN evaluation (modified/touched rails): BLOCKED on inherited "
                "unresolved/out-of-scope connections"
                if evaluation_blocked
                else "PDN evaluation (modified/touched rails): no inherited "
                "unresolved/out-of-scope connection blockers"
            ),
            f"Selected PWR NET changes: {len(changes):,} decap(s)",
            f"Isolation-gap sacrifices: {len(sacrifices):,} decap cell(s)",
        ]
        routing_summary = getattr(plan, "routing_summary", None)
        if routing_summary is None:
            lines.append("Immutable signal-routing protection: OFF")
        else:
            clearance = routing_summary.policy.clearance_um
            clearance_text = (
                f"{clearance:g} µm"
                if clearance is not None
                else (
                    f"{routing_summary.policy.trace_width_multiplier:g}× trace width"
                )
            )
            lines.extend(
                (
                    "Immutable signal-routing protection: ON "
                    f"({routing_summary.policy.scope.value}, clearance "
                    f"{clearance_text})",
                    "Routing candidates: "
                    f"checked {routing_summary.checked_count:,}, "
                    f"safe {routing_summary.safe_count:,}, "
                    f"blocked {routing_summary.blocked_count:,}, "
                    f"unknown {routing_summary.unknown_count:,}",
                    (
                        "Routing evidence: production-ready"
                        if routing_summary.production_ready
                        else "Routing evidence: provisional research proxy; "
                        + routing_summary.scope_limitation
                    ),
                )
            )
        cells = getattr(plan, "cells", ())
        if isinstance(cells, dict):
            cells = cells.values()
        for cell in cells:
            requested = int(
                self._plan_cell_value(
                    cell, "requested", "requested_count", default=0
                )
                or 0
            )
            fulfilled = int(
                self._plan_cell_value(
                    cell, "fulfilled", "fulfilled_count", default=0
                )
                or 0
            )
            shortfall = int(
                self._plan_cell_value(
                    cell, "shortfall", "shortfall_count", default=0
                )
                or 0
            )
            model_id = self._plan_cell_value(
                cell, "model_id", "component", default="?"
            )
            rail_id = self._plan_cell_value(cell, "rail_id", default="?")
            raw_role = self._plan_cell_value(cell, "role", default="")
            role = str(getattr(raw_role, "value", raw_role)).upper()
            if role == "EXCHANGE":
                tolerance = float(
                    self._plan_cell_value(
                        cell, "tolerance_percent", default=0.0
                    )
                    or 0.0
                )
                tolerance_count = int(
                    self._plan_cell_value(
                        cell, "tolerance_count", default=requested
                    )
                    or 0
                )
                sent = int(
                    self._plan_cell_value(cell, "sent_count", default=fulfilled)
                    or 0
                )
                received = int(
                    self._plan_cell_value(cell, "received_count", default=sent)
                    or 0
                )
                sacrificed = int(
                    self._plan_cell_value(
                        cell, "sacrificed_count", default=0
                    )
                    or 0
                )
                lines.append(
                    f"{model_id} / {rail_id} exchange: tolerance "
                    f"{tolerance:g}% ({tolerance_count:,}), sent {sent:,}, "
                    f"received {received:,}, "
                    f"net {received - sent - sacrificed:+,}; "
                    f"sacrificed {sacrificed:,}"
                )
                continue
            if not requested and not fulfilled and not shortfall:
                continue
            if role == "DONOR":
                unused = max(requested - fulfilled, 0)
                sacrificed = int(
                    self._plan_cell_value(
                        cell, "sacrificed_count", default=0
                    )
                    or 0
                )
                moved = int(
                    self._plan_cell_value(cell, "sent_count", default=0) or 0
                )
                lines.append(
                    f"{model_id} / {rail_id} donor: give capacity "
                    f"{requested:,}, used {fulfilled:,}, unused {unused:,}; "
                    f"moved {moved:,} + sacrificed {sacrificed:,}"
                )
            elif role == "RECEIVER":
                lines.append(
                    f"{model_id} / {rail_id} receiver: requested "
                    f"{requested:,}, fulfilled {fulfilled:,}, "
                    f"shortfall {shortfall:,}"
                )
        if diagnostics:
            lines.append("Diagnostics:")
            lines.extend(
                f"- {getattr(item, 'message', item)}" for item in diagnostics[:20]
            )
            if len(diagnostics) > 20:
                lines.append(f"- ... {len(diagnostics) - 20:,} more")
        if status == "PARTIAL":
            lines.append(
                "Only physically valid assignments are included. Apply Preview "
                "will commit those available changes; the listed shortfall remains."
            )
        return "\n".join(lines)

    def _distribution_assignment_failure_map(
        self, plan: Any
    ) -> dict[tuple[str, str], int]:
        raw = getattr(plan, "assignment_failed", None)
        if raw is None:
            raw = getattr(plan, "assignment_failures", None)
        result: dict[tuple[str, str], int] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(key, tuple) and len(key) == 2:
                    result[(str(key[0]), str(key[1]))] = max(int(value), 0)
        cells = getattr(plan, "cells", ())
        if isinstance(cells, dict):
            cells = cells.values()
        for cell in cells:
            rail_id = self._plan_cell_value(cell, "rail_id")
            model_id = self._plan_cell_value(cell, "model_id", "component")
            failed = self._plan_cell_value(
                cell, "assignment_failed", "shortfall", "shortfall_count"
            )
            if rail_id is not None and model_id is not None and failed is not None:
                result[(str(rail_id), str(model_id))] = max(int(failed), 0)
        return result

    def _render_distribution_assignment_failures(self, plan: Any) -> None:
        failures = {
            (rail.casefold(), model.casefold()): value
            for (rail, model), value in (
                self._distribution_assignment_failure_map(plan).items()
            )
        }
        self._distribution_table_updating = True
        sorting_enabled = self.distribution_table.isSortingEnabled()
        header = self.distribution_table.horizontalHeader()
        sort_section = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        self.distribution_table.setSortingEnabled(False)
        previous_block = self.distribution_table.blockSignals(True)
        try:
            for row in range(self.distribution_table.rowCount()):
                for column in range(4, self.distribution_table.columnCount(), 4):
                    item = self.distribution_table.item(row, column)
                    if item is None:
                        continue
                    raw_key = item.data(Qt.ItemDataRole.UserRole)
                    if not isinstance(raw_key, tuple) or len(raw_key) != 2:
                        continue
                    value = failures.get(
                        (str(raw_key[0]).casefold(), str(raw_key[1]).casefold()),
                        0,
                    )
                    item.setText(str(value))
                    if value > 0:
                        item.setBackground(QColor("#FECACA"))
                    else:
                        item.setBackground(QBrush())
        finally:
            self.distribution_table.blockSignals(previous_block)
            self.distribution_table.setSortingEnabled(sorting_enabled)
            if sorting_enabled and sort_section >= 0:
                self.distribution_table.sortItems(sort_section, sort_order)
            self._distribution_table_updating = False

    def _accept_distribution_plan(self, result: Any) -> None:
        scenario = self._scenario
        if scenario is None:
            return
        prepared = (
            result if isinstance(result, _PreparedDistributionPreview) else None
        )
        plan = prepared.plan if prepared is not None else result
        if (
            str(getattr(plan, "input_design_fingerprint", ""))
            != scenario.design_fingerprint
            or int(getattr(plan, "input_revision", -1)) != scenario.revision
        ):
            self.status_text.setText("Discarded stale De-cap Distribution preview")
            return
        try:
            if prepared is not None:
                preview = prepared.preview_scenario
                export_rows = prepared.export_rows
                power_projection = prepared.power_projection
            else:
                # Direct callers (including legacy plugins/tests) remain supported;
                # normal GUI runs prepare this CPU work in the worker.
                from ..distribution import apply_distribution_plan, distribution_csv_rows

                preview = apply_distribution_plan(scenario, plan)
                power_projection = None
                exported = tuple(distribution_csv_rows(plan))
                export_rows = (
                    exported[1:]
                    if exported
                    and isinstance(exported[0], (tuple, list))
                    and tuple(exported[0])[:2] == ("Component", "REFDES")
                    else exported
                )
        except (TypeError, ValueError) as exc:
            QMessageBox.critical(
                self,
                APP_DISPLAY_NAME,
                f"The De-cap Distribution preview failed validation.\n\n{exc}",
            )
            self.status_text.setText("De-cap Distribution preview validation failed")
            return
        self._distribution_plan = plan
        self._distribution_status_notice = None
        self._distribution_preview_scenario = preview
        self._distribution_power_projection = power_projection
        self._distribution_export_rows = export_rows
        self._render_distribution_assignment_failures(plan)
        self.distribution_summary.setPlainText(
            self._distribution_plan_summary(plan)
        )
        evaluation_blocked = any(
            str(getattr(item, "code", "")).upper()
            == "PREEXISTING_UNRESOLVED_EVALUATION_RAILS"
            for item in tuple(getattr(plan, "diagnostics", ()))
        )
        status_suffix = (
            "; PDN evaluation blocked on inherited unresolved connections"
            if evaluation_blocked
            else ""
        )
        self.status_text.setText(
            "De-cap Distribution preview: "
            f"{self._plan_status_text(plan)} (count/topology){status_suffix}"
        )
        self._update_distribution_controls()

    def _accept_distribution_plan_if_current(
        self, result: Any, expected_request_fingerprint: str
    ) -> None:
        """Reject a planner result if targets, tolerances, or order changed."""

        if expected_request_fingerprint != self._distribution_request_fingerprint():
            self._distribution_status_notice = (
                "Targets, tolerances, or Candidate order changed while the preview "
                "was calculating. The result was discarded."
            )
            self.status_text.setText("Discarded stale De-cap Distribution preview")
            self._update_distribution_validation()
            return
        self._accept_distribution_plan(result)

    def _apply_distribution_preview(self) -> None:
        scenario = self._scenario
        plan = self._distribution_plan
        preview = self._distribution_preview_scenario
        if scenario is None or plan is None or preview is None:
            return
        if (
            str(getattr(plan, "input_design_fingerprint", ""))
            != scenario.design_fingerprint
            or int(getattr(plan, "input_revision", -1)) != scenario.revision
        ):
            QMessageBox.warning(
                self,
                APP_DISPLAY_NAME,
                "The scenario changed after this preview. Calculate it again.",
            )
            self._clear_distribution_preview("Stale preview discarded.")
            return
        if preview.design_fingerprint == scenario.design_fingerprint:
            QMessageBox.information(
                self, APP_DISPLAY_NAME, "The preview contains no applicable changes."
            )
            return

        try:
            from ..distribution import apply_distribution_plan

            preview = apply_distribution_plan(
                scenario,
                plan,
                power_projection=self._distribution_power_projection,
            )
        except (TypeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                APP_DISPLAY_NAME,
                "The scenario changed after this preview. Calculate it "
                f"again.\n\n{exc}",
            )
            self._clear_distribution_preview("Stale preview discarded.")
            return

        summary = self.distribution_summary.toPlainText()
        self._distribution_preview_scenario = preview
        self._scenario = preview
        self._dirty = True
        self._invalidate_evaluation(
            "De-cap Distribution applied; run Original + Tuned evaluation again."
        )
        self._populate_distribution_table(preview, preserve_result=True)
        self._render_distribution_assignment_failures(plan)
        self._refresh_all()
        self.distribution_summary.setPlainText(
            f"{summary}\nApplied atomically at scenario revision {preview.revision}."
        )
        self.status_text.setText(
            f"Applied De-cap Distribution at revision {preview.revision}"
        )
        self._update_distribution_controls()

    @staticmethod
    def _distribution_export_values(row: Any) -> tuple[Any, ...]:
        if isinstance(row, dict):
            getter = row.get
        else:
            getter = lambda name, default=None: getattr(row, name, default)
        component = getter("component", getter("model_id", ""))
        refdes = getter("refdes", "")
        before_net = getter(
            "before_net", getter("previous_net", getter("original_net", ""))
        )
        after_net = getter(
            "after_net", getter("new_net", getter("distributed_net", ""))
        )
        x_um = getter("x_um", getter("x", 0.0))
        y_um = getter("y_um", getter("y", 0.0))
        if not refdes and isinstance(row, (tuple, list)) and len(row) == 6:
            return tuple(row)
        return component, refdes, before_net, after_net, x_um, y_um

    def _export_distribution_csv(self) -> None:
        plan = self._distribution_plan
        if (
            self._scenario is None
            or plan is None
            or not self._distribution_export_rows
        ):
            return
        default_name = (
            f"{Path(self._scenario.source.name).stem}_decap_distribution.xlsx"
        )
        filename, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Full De-cap Distribution Results",
            default_name,
            "Excel workbook (*.xlsx);;CSV files (*.csv);;All files (*)",
        )
        if not filename:
            return
        path = Path(filename)
        suffix = path.suffix.casefold()
        if suffix not in {".csv", ".xlsx"}:
            suffix = ".csv" if selected_filter.startswith("CSV") else ".xlsx"
            path = path.with_suffix(suffix)
        decap_rows = tuple(
            self._distribution_export_values(raw_row)
            for raw_row in self._distribution_export_rows
        )
        try:
            if suffix == ".csv":
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream, lineterminator="\n")
                    writer.writerow(
                        (
                            "Component",
                            "REFDES",
                            "Before NET",
                            "After NET",
                            "X (um)",
                            "Y (um)",
                        )
                    )
                    for row in decap_rows:
                        component, refdes, before_net, after_net, x_um, y_um = row
                        writer.writerow(
                            (
                                _excel_safe_csv_cell(component or ""),
                                _excel_safe_csv_cell(refdes),
                                _excel_safe_csv_cell(before_net),
                                _excel_safe_csv_cell(after_net),
                                float(x_um),
                                float(y_um),
                            )
                        )
            else:
                from ..distribution import (
                    distribution_inventory_table,
                    distribution_target_table,
                )
                from ..spreadsheet_export import write_distribution_workbook
                target_headers, target_rows = distribution_target_table(plan)
                inventory_headers, inventory_rows = distribution_inventory_table(plan)
                raw_distance_mode = getattr(plan, "distance_mode", "")
                distance_mode = str(
                    getattr(raw_distance_mode, "value", raw_distance_mode)
                ).upper()
                format_version, routing_metadata = (
                    self._distribution_workbook_contract(plan)
                )
                write_distribution_workbook(
                    path,
                    decap_rows,
                    target_headers,
                    target_rows,
                    inventory_headers=inventory_headers,
                    inventory_rows=inventory_rows,
                    metadata={
                        "Format Version": format_version,
                        "Application Version": __version__,
                        "Source SPD Name": self._scenario.source.name,
                        "Source SPD SHA-256": self._scenario.source.sha256,
                        "Input Design Fingerprint": str(
                            getattr(plan, "input_design_fingerprint", "")
                        ),
                        "Input Revision": int(
                            getattr(plan, "input_revision", self._scenario.revision)
                        ),
                        "Distance Mode": distance_mode,
                        "Present Inventory Total": sum(
                            int(getattr(cell, "present_count", 0))
                            for cell in getattr(plan, "cells", ())
                        ),
                        **routing_metadata,
                    },
                )
        except (OSError, TypeError, ValueError) as exc:
            kind = "Excel workbook" if suffix == ".xlsx" else "CSV"
            QMessageBox.critical(
                self,
                APP_DISPLAY_NAME,
                f"The De-cap Distribution {kind} could not be written.\n\n{exc}",
            )
            self.status_text.setText(f"De-cap Distribution {kind} export failed")
            return
        sheet_note = " with 2 sheets" if suffix == ".xlsx" else ""
        self.status_text.setText(
            f"Exported {len(decap_rows):,} Decap(s) to {path.name}{sheet_note}"
        )

    def _save_distribution_scenario(self) -> None:
        if self._scenario is None or self._distribution_preview_scenario is None:
            return
        scenario_to_save = self._distribution_preview_scenario
        plan = self._distribution_plan
        if plan is not None:
            current_fingerprint = self._scenario.design_fingerprint
            if (
                current_fingerprint
                == str(getattr(plan, "input_design_fingerprint", ""))
                and self._scenario.revision
                == int(getattr(plan, "input_revision", -1))
            ):
                try:
                    from ..distribution import apply_distribution_plan

                    scenario_to_save = apply_distribution_plan(
                        self._scenario,
                        plan,
                        power_projection=self._distribution_power_projection,
                    )
                except (TypeError, ValueError) as exc:
                    QMessageBox.warning(
                        self,
                        APP_DISPLAY_NAME,
                        "The scenario changed after this preview. Calculate it "
                        f"again.\n\n{exc}",
                    )
                    return
            elif (
                current_fingerprint
                == str(getattr(plan, "output_design_fingerprint", ""))
                and self._scenario.revision
                == int(getattr(plan, "output_revision", -1))
            ):
                # The preview has already been applied. Preserve current UI-only
                # scenario state such as NET colors and selected REFDES.
                scenario_to_save = self._scenario
            else:
                QMessageBox.warning(
                    self,
                    APP_DISPLAY_NAME,
                    "The scenario changed after this preview. Calculate it again.",
                )
                return
        default_name = f"{Path(self._scenario.source.name).stem}_distributed.spdpi"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Distributed SPD Decap PI Scenario",
            default_name,
            "SPD PI Scenario (*.spdpi)",
        )
        if not filename:
            return
        path = Path(filename)
        if not path.suffix:
            path = path.with_suffix(".spdpi")
        if path.suffix.casefold() != ".spdpi":
            QMessageBox.warning(
                self, APP_DISPLAY_NAME, "Scenario file extension must be .spdpi."
            )
            return
        worker = FunctionWorker(
            _job_save_scenario,
            scenario_to_save,
            path,
            dict(self._attachments),
        )
        saving_current = scenario_to_save is self._scenario
        saved_fingerprint = scenario_to_save.design_fingerprint
        saved_revision = scenario_to_save.revision

        def on_saved(saved_path: Path) -> None:
            if saving_current:
                self._scenario_saved(
                    saved_path, saved_fingerprint, saved_revision
                )
            else:
                self.status_text.setText(
                    f"Saved distributed scenario {saved_path.name}"
                )

        self._run_worker(
            worker,
            on_saved,
            label="Saving distributed scenario...",
            cancelable=False,
        )

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
            self.evaluation_solver_profile_combo,
            self.evaluation_modal_preset_combo,
            self.add_model_action,
            self.save_action,
            self.save_as_action,
            self.distribution_table,
            self.distribution_distance_combo,
            self.distribution_protect_signal_routing_checkbox,
        ):
            widget.setEnabled(loaded)
        self.ai_rail_combo.setEnabled(loaded and bool(self._tuned_evaluations_by_rail))
        self.ai_button.setEnabled(loaded and self._last_evaluation is not None)
        self.plane_layer_bar.setEnabled(loaded and bool(self._plane_layer_checks))
        self._update_result_plot_button()
        self._update_distribution_controls()
        self._sync_distribution_window()

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
            self.evaluation_solver_profile_combo,
            self.evaluation_modal_preset_combo,
            self.plane_layer_bar,
            self.distribution_table,
            self.distribution_distance_combo,
            self.distribution_protect_signal_routing_checkbox,
            self.distribution_trace_clearance_edit,
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
        self._update_result_plot_button()
        self._update_distribution_controls()
        self._sync_distribution_window()

    def _update_result_plot_button(self) -> None:
        comparisons = (
            tuple(getattr(self._comparison_batch, "comparisons", ()))
            if self._comparison_batch is not None
            else ()
        )
        enabled = (
            self._worker is None
            and self._scenario is not None
            and bool(comparisons)
        )
        self.open_results_button.setEnabled(enabled)
        self.export_tuned_csv_button.setEnabled(enabled)

    def _invalidate_evaluation(
        self,
        reason: str = "Scenario changed; run evaluation again.",
        *,
        project: ProjectSpec | None = None,
        model_keys: frozenset[str] | None = None,
    ) -> None:
        self._pending_evaluation_launch = None
        self._active_evaluation_manifest = None
        self._evaluation_state = None
        self._last_evaluation = None
        self._last_scenario_evaluation = None
        self._tuned_evaluations_by_rail.clear()
        self._comparison_batch = None
        self.ai_rail_combo.clear()
        self.ai_rail_combo.setEnabled(False)
        self._update_result_plot_button()
        self.comparison_table.setRowCount(0)
        if self._results_window is not None:
            self._results_window.clear_results()
        if self._scenario is not None:
            available_models = (
                model_keys
                if model_keys is not None
                else {
                    item.model_id.casefold()
                    for item in (project if project is not None else self._scenario.base_project).cap_models
                }
            )
            connected = {
                refdes.casefold()
                for refdes in self._scenario.electrically_connected_refdes
            }
            unmodeled = sum(
                item.enabled
                and item.refdes.casefold() in connected
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
        self.export_tuned_csv_button.setToolTip(
            "Export enabled Decaps from the evaluated Tuned PWR NETs as "
            "Component, REFDES and NET Name, with solver provenance."
        )
        self._update_evaluation_notes()
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

        def accept_result(result: Any) -> None:
            # QRunnable emits before its queued result reaches the GUI.  A user
            # may press Cancel during that gap, so never mutate from a stale
            # result even if the worker itself finished just before cancellation.
            if (
                self._worker is worker
                and not worker.cancelled
                and not self._worker_cancel_requested
            ):
                on_result(result)

        worker.signals.result.connect(accept_result)
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

    def _evaluation_worker_error(self, details: str) -> None:
        """Expose research evidence blockers without changing the selected model."""

        final_line = next(
            (line for line in reversed(details.strip().splitlines()) if line.strip()),
            "Evaluation failed",
        )
        profile_key = self._selected_evaluation_solver_profile()
        profile_label = self.evaluation_solver_profile_combo.currentText()
        if profile_key == _RESEARCH_SOLVER_PROFILE_KEY:
            self.evaluation_summary.setPlainText(
                "Research evaluation did not run. Legacy modal was not used as a "
                "fallback, and the open source/scenario was preserved.\n\n"
                + final_line
                + "\n\nRepair the reported source/topology evidence or explicitly "
                "select Legacy modal and run again."
            )
            self.status_text.setText("Research evaluation blocked by source evidence")
        else:
            self.evaluation_summary.setPlainText(
                f"Evaluation failed with physics model {profile_label}.\n\n"
                + final_line
            )
            self.status_text.setText("Evaluation failed")
        self._worker_error(details)

    def _evaluation_preflight_worker_error(self, details: str) -> None:
        final_line = next(
            (line for line in reversed(details.strip().splitlines()) if line.strip()),
            "Evaluation Analysis preflight failed",
        )
        self.evaluation_summary.setPlainText(
            "Evaluation Analysis did not start because its connectivity/geometry "
            "preflight failed. No selected PWR NET was evaluated.\n\n" + final_line
        )
        self.status_text.setText("Evaluation Analysis preflight failed")
        self._worker_error(details)

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
        pending_evaluation = (
            self._pending_evaluation_launch if not cancelled else None
        )
        self._pending_evaluation_launch = None
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
            ("Opening", "Saving", "Evaluating", "Analyzing", "Calculating")
        ):
            self.status_text.setText("Ready")
        if auto_save and self._scenario is not None and self._scenario_path is not None:
            QTimer.singleShot(0, self._auto_save_scenario)
        if pending_evaluation is not None:
            request, manifest = pending_evaluation
            # The preflight runnable is fully released at this point, so the
            # evaluation runnable can be chained synchronously.  Leaving this
            # to a zero-delay timer exposes a transient ``_worker is None``
            # state to the UI/test heartbeat and can make the two-stage
            # operation look complete before Evaluation has even started.
            self._launch_evaluation_after_preflight(request, manifest)

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
        worker = FunctionWorker(_job_import_spd, Path(filename))
        self._run_worker(worker, self._accept_spd_import, label="Opening SPD...")

    def _accept_spd_import(
        self, prepared_import: _PreparedScenarioImport | ScenarioImport
    ) -> None:
        if isinstance(prepared_import, _PreparedScenarioImport):
            imported = prepared_import.imported
            prepared_view = prepared_import.view
        else:
            # Retain the direct hook for focused GUI tests and integrations.
            imported = prepared_import
            prepared_view = None
        self._reset_document_view_state()
        self._scenario = imported.scenario
        self._attachments = imported.attachments
        self._scenario_path = None
        self._dirty = False
        self._invalidate_evaluation(
            "Run an evaluation to generate a PI plot.",
            project=prepared_view.project if prepared_view is not None else None,
            model_keys=prepared_view.model_keys if prepared_view is not None else None,
        )
        self.progress_bar.setValue(99)
        self.status_text.setText("Rendering board planes and component markers...")
        self.progress_bar.repaint()
        self.status_text.repaint()
        board_started = perf_counter()
        self._refresh_all(prepared_view)
        board_s = perf_counter() - board_started
        fit_started = perf_counter()
        self.board.fit_board()
        fit_s = perf_counter() - fit_started
        warnings = sum(
            str(getattr(item, "severity", "")).casefold() == "warning"
            for item in imported.diagnostics
        )
        timings = imported.timings
        visible_total_s = timings.total_s + board_s + fit_s
        connection_status, connection_details = (
            prepared_view.connection_summary
            if prepared_view is not None
            else _shared_pad_connection_summary(imported.scenario)
        )
        recovery_status, recovery_details = (
            prepared_view.recovery_summary
            if prepared_view is not None
            else _source_via_path_recovery_summary(imported.scenario)
        )
        self.status_text.setText(
            f"Loaded {len(imported.scenario.decaps):,} top-side decaps in "
            f"{visible_total_s:.1f}s (eligibility {timings.eligibility_s:.1f}s, "
            f"Mixed-reference witness selection "
            f"{timings.mixed_witness_selection_s:.1f}s, "
            f"Mixed-reference GND recovery {timings.ground_recovery_s:.1f}s, "
            f"board {board_s + fit_s:.1f}s; {warnings} warning(s)) | "
            f"{connection_status} | {recovery_status}"
        )
        self.status_text.setToolTip(
            " | ".join(
                (
                    f"SPD analysis: {timings.analyze_s:.3f}s",
                    f"Geometry normalization/compression: {timings.plan_s:.3f}s",
                    f"Spatial index build: {timings.index_s:.3f}s",
                    f"Source Via path recovery: {timings.recovery_s:.3f}s",
                    f"Mixed-reference witness selection: "
                    f"{timings.mixed_witness_selection_s:.3f}s",
                    f"Mixed-reference GND recovery: {timings.ground_recovery_s:.3f}s",
                    f"Exact eligibility: {timings.eligibility_s:.3f}s",
                    f"Scenario validation: {timings.finalize_s:.3f}s",
                    f"Board scene build: {board_s:.3f}s",
                    f"Fit board: {fit_s:.3f}s",
                    f"Visible total: {visible_total_s:.3f}s",
                    connection_details,
                    recovery_details,
                )
            )
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
        worker = FunctionWorker(
            _job_load_scenario, path, source_override, prepare_view=True
        )
        self._run_worker(
            worker,
            lambda prepared_bundle: self._accept_scenario_bundle(
                path, prepared_bundle, relinked=source_override is not None
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
        self,
        path: Path,
        prepared_bundle: _PreparedScenarioBundle | ScenarioBundle,
        *,
        relinked: bool = False,
    ) -> None:
        if isinstance(prepared_bundle, _PreparedScenarioBundle):
            bundle = prepared_bundle.bundle
            prepared_view = prepared_bundle.view
        else:
            bundle = prepared_bundle
            prepared_view = None
        self._reset_document_view_state()
        self._scenario = bundle.scenario
        self._attachments = bundle.attachments
        self._scenario_path = path
        self._dirty = bundle.recovered_from is not None or relinked
        self._invalidate_evaluation(
            "Run an evaluation to generate a PI plot.",
            project=prepared_view.project if prepared_view is not None else None,
            model_keys=prepared_view.model_keys if prepared_view is not None else None,
        )
        self._refresh_all(prepared_view)
        self.board.fit_board()
        message = f"Opened {path.name}"
        if bundle.recovered_from is not None:
            message += f" (recovered from {bundle.recovered_from.name}; save required)"
        elif relinked:
            message += " (external SPD relinked; save required)"
        connection_status, connection_details = (
            prepared_view.connection_summary
            if prepared_view is not None
            else _shared_pad_connection_summary(bundle.scenario)
        )
        recovery_status, recovery_details = (
            prepared_view.recovery_summary
            if prepared_view is not None
            else _source_via_path_recovery_summary(bundle.scenario)
        )
        self.status_text.setText(
            f"{message} | {connection_status} | {recovery_status}"
        )
        self.status_text.setToolTip(
            " | ".join((connection_details, recovery_details))
        )

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

    def _reset_document_view_state(self) -> None:
        """Drop rendering and picker state that must not cross documents."""

        self.status_text.setToolTip("")
        self._distribution_show_original_board = False
        self._source_board_records = ()
        self._source_board_cache_key = None
        self._source_board_comparison_cache = None
        if hasattr(self, "source_board_checkbox"):
            previous = self.source_board_checkbox.blockSignals(True)
            try:
                self.source_board_checkbox.setChecked(False)
                self.source_board_checkbox.setEnabled(False)
            finally:
                self.source_board_checkbox.blockSignals(previous)
            self.board_assignment_view_label.setText("Board: No document")
            self.board_assignment_view_label.setStyleSheet("color: #9aa4b2;")
        if self._distribution_window is not None:
            self._distribution_window.clear_for_no_document()
        self._plane_render_token += 1
        self._plane_render_cells = ()
        self._plane_render_index = 0
        self._plane_render_builder = None
        self._reset_distribution_state()
        self.board.clear_plane_items()
        signals_were_blocked = self.rail_list.blockSignals(True)
        self.rail_list.clear()
        self.rail_list.blockSignals(signals_were_blocked)
        self._active_board_net_keys = None
        self._rendered_bump_source_sha256 = None
        self._rendered_plane_source_sha256 = None
        self._rendered_plane_net_colors.clear()
        self._plane_items_by_net.clear()
        self._plane_items_by_layer.clear()
        self._clear_plane_layer_controls(reset_hidden=True)

    def _refresh_all(self, prepared_view: _PreparedDocumentView | None = None) -> None:
        scenario = self._scenario
        self._source_board_comparison_cache = None
        if scenario is None:
            self.board.clear_board()
            self._reset_distribution_state()
            self._active_board_net_keys = None
            self._rendered_bump_source_sha256 = None
            self._rendered_plane_source_sha256 = None
            self._rendered_plane_net_colors.clear()
            self._plane_items_by_net.clear()
            self._plane_items_by_layer.clear()
            self._clear_plane_layer_controls(reset_hidden=True)
            self._set_loaded_state(False)
            return
        self.source_label.setText(
            f"{scenario.source.name} | {scenario.source.size_bytes / (1024**2):,.1f} MiB | "
            f"SHA-256 {scenario.source.sha256[:12]}... | rev {scenario.revision}"
        )
        source_board_cache_key = (scenario.source.sha256, len(scenario.decaps))
        if self._source_board_cache_key != source_board_cache_key:
            self._source_board_records = ()
            self._source_board_cache_key = source_board_cache_key
        # Import accepts both batched board layers, then performs one explicit
        # fit.  Avoid the otherwise redundant fit after decaps and again after
        # bumps, which is visible on large SPDs.
        if prepared_view is not None:
            self._source_board_records = prepared_view.source_decap_records
        self._refresh_distribution_board_decaps(prepared_view)
        if prepared_view is not None and prepared_view.project.rails:
            # Establish the default focus before staged scatter creation so a
            # later rail-list refresh does not synchronously repaint 10k+ dots.
            self.board.set_active_nets((prepared_view.project.rails[0].net,))
        self.board.set_connection_labels(
            {
                decap.refdes: _connection_label(scenario, decap.refdes)
                for decap in scenario.decaps
            }
        )
        if self._rendered_bump_source_sha256 != scenario.source.sha256:
            self.board.set_bumps(
                prepared_view.bumps if prepared_view is not None else (
                    item
                    for item in scenario.normalized_project.get("pins", ())
                    if str(item.get("kind", "")) == PinKind.DEVICE_BUMP.value
                ),
                fit=False,
                normalized=prepared_view is not None,
                staged=prepared_view is not None,
            )
            self._rendered_bump_source_sha256 = scenario.source.sha256
        self.board.set_selected_refdes(scenario.selected_refdes)
        self._refresh_colors()
        self._refresh_rails(prepared_view.project if prepared_view is not None else None)
        self._refresh_distribution_tab(
            prepared_view.distribution_counts if prepared_view is not None else None,
            (
                prepared_view.distribution_assignable_counts
                if prepared_view is not None
                else None
            ),
            prepared_view.design_fingerprint if prepared_view is not None else None,
            prepared_view.project if prepared_view is not None else None,
        )
        plane_colors = {
            str(net).casefold(): str(color).casefold()
            for net, color in scenario.net_colors.items()
        }
        if self._rendered_plane_source_sha256 != scenario.source.sha256:
            self._refresh_plane_preview(prepared_view)
            self._rendered_plane_source_sha256 = scenario.source.sha256
        else:
            changed_color_keys = {
                key
                for key in set(self._rendered_plane_net_colors) | set(plane_colors)
                if self._rendered_plane_net_colors.get(key) != plane_colors.get(key)
            }
            if changed_color_keys:
                self._restyle_plane_items(changed_color_keys)
        self._rendered_plane_net_colors = plane_colors
        self._refresh_selection_table()
        if self._worker is None:
            self._set_loaded_state(True)
        else:
            self._set_busy(True)

    def _source_display_decap(
        self,
        decap: ScenarioDecap,
        source_models: Mapping[str, str] | None = None,
    ) -> ScenarioDecap:
        scenario = self._scenario
        assert scenario is not None
        bindings = (
            _source_model_ids_by_refdes(scenario)
            if source_models is None
            else source_models
        )
        source_model_id = bindings.get(decap.refdes.casefold())
        return decap.model_copy(
            update={
                "current_net": decap.source_net,
                "current_rail_id": decap.source_rail_id,
                "model_id": source_model_id,
                "enabled": decap.source_mounted,
                "pad_state": DecapPadState.NORMAL,
            }
        )

    def _board_decaps_for_distribution_display(self) -> tuple[Any, ...]:
        scenario = self._scenario
        if scenario is None:
            return ()
        if not self._distribution_show_original_board:
            return tuple(scenario.decaps)
        if not self._source_board_records:
            self._source_board_records = _prepare_source_board_decaps(scenario)
            self._source_board_cache_key = (
                scenario.source.sha256,
                len(scenario.decaps),
            )
        return self._source_board_records

    def _refresh_distribution_board_decaps(
        self, prepared_view: _PreparedDocumentView | None = None
    ) -> None:
        assert self._scenario is not None
        showing_original = self._distribution_show_original_board
        if showing_original:
            records = self._board_decaps_for_distribution_display()
            normalized = True
            staged = False
        elif prepared_view is not None:
            records = prepared_view.decap_records
            normalized = True
            staged = True
        else:
            records = self._scenario.decaps
            normalized = False
            staged = False
        self.board.set_decaps(
            records,
            self._scenario.net_colors,
            fit=False,
            normalized=normalized,
            staged=staged,
        )

    def _show_original_distribution_board(self, show_original: bool) -> None:
        show_original = bool(show_original) and self._source_board_comparison_available()
        if self._distribution_show_original_board == show_original:
            self._sync_distribution_window()
            return
        self._distribution_show_original_board = show_original
        if self._scenario is not None:
            self._refresh_distribution_board_decaps()
            self.board.set_selected_refdes(self._scenario.selected_refdes)
            self._refresh_selection_table()
        self._sync_distribution_window()

    def _refresh_plane_preview(
        self, prepared_view: _PreparedDocumentView | None = None
    ) -> None:
        """Start bounded GUI-thread artwork construction from prepared primitives."""

        assert self._scenario is not None
        if (
            prepared_view is None
            or prepared_view.source_sha256 != self._scenario.source.sha256
        ):
            prepared_view = _prepare_document_view(
                self._scenario,
                self._attachments,
                progress=lambda _value, _message: None,
                is_cancelled=lambda: False,
            )
        project = prepared_view.project
        self._plane_render_token += 1
        self._plane_render_cells = prepared_view.plane_cells
        self._plane_render_index = 0
        self._plane_render_builder = None
        self.board.clear_plane_items()
        self._plane_items_by_net = {}
        self._plane_items_by_layer = {}
        self._rebuild_plane_layer_controls(prepared_view.layer_labels)
        if not self._plane_render_cells:
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
            self.board.append_plane_items((rectangle,))
            return
        token = self._plane_render_token
        self._render_plane_chunk(token)

    def _render_plane_chunk(self, token: int) -> None:
        """Render about one event-loop frame of plane primitives, then yield."""

        if token != self._plane_render_token or self._scenario is None:
            return
        started = perf_counter()
        added: list[QGraphicsItem] = []
        while self._plane_render_index < len(self._plane_render_cells):
            prepared = self._plane_render_cells[self._plane_render_index]
            source_item: QGraphicsItem | None = None
            if prepared.runs:
                source_item = _PlaneArtworkItem(
                    prepared.runs,
                    QColor(self.board.display_color_for_net(prepared.net)),
                    set(prepared.primitive_kinds),
                    prepared.artwork_bounds,
                )
            cell_items = self._plane_cell_items(prepared, source_item)
            added.extend(cell_items)
            net_items = self._plane_items_by_net.setdefault(
                prepared.net.casefold(), []
            )
            layer_items = self._plane_items_by_layer.setdefault(
                prepared.layer.casefold(), []
            )
            net_items.extend(cell_items)
            layer_items.extend(cell_items)
            self._plane_render_index += 1
            if perf_counter() - started >= 0.012:
                break
        if added:
            self.board.append_plane_items(added)
            self._apply_plane_layer_visibility()
        if self._plane_render_index >= len(self._plane_render_cells):
            self._plane_render_cells = ()
            self._plane_render_builder = None
            self.board.fit_board()
            if self.status_text.text().startswith("Rendering PWR artwork"):
                self.status_text.setText("PWR artwork ready")
            return
        self.status_text.setText(
            f"Rendering PWR artwork {self._plane_render_index:,}/"
            f"{len(self._plane_render_cells):,} cells..."
        )
        QTimer.singleShot(0, lambda: self._render_plane_chunk(token))

    def _plane_cell_items(
        self,
        prepared: _PreparedPlaneCell,
        source_item: QGraphicsItem | None,
    ) -> list[QGraphicsItem]:
        """Create one completed cell's graphics on the GUI thread."""

        cell = prepared.cell
        color = self.board.display_color_for_net(prepared.net)
        result: list[QGraphicsItem] = []
        if source_item is not None:
            source_item.setData(1, prepared.net)
            source_item.setData(2, prepared.layer)
            result.append(source_item)
        fill = QColor(color)
        fill.setAlpha(24)
        rectangle = QGraphicsRectItem(
            cell.x_min_um,
            cell.y_min_um,
            cell.x_max_um - cell.x_min_um,
            cell.y_max_um - cell.y_min_um,
        )
        pen = QPen(color)
        pen.setCosmetic(True)
        if source_item is not None or cell.solver_geometry == "spd_bounding_box":
            pen.setStyle(Qt.PenStyle.DashLine)
        rectangle.setPen(pen)
        rectangle.setBrush(QBrush() if source_item is not None else QBrush(fill))
        rectangle.setData(0, "solver_bounds" if source_item is not None else "plane_cell")
        rectangle.setData(1, prepared.net)
        rectangle.setData(2, prepared.layer)
        rectangle.setZValue(-30.0)
        result.append(rectangle)
        return result

    def _clear_plane_layer_controls(self, *, reset_hidden: bool = False) -> None:
        while self.plane_layer_layout.count() > 1:
            entry = self.plane_layer_layout.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.deleteLater()
        self._plane_layer_checks.clear()
        if reset_hidden:
            self._hidden_plane_layer_keys.clear()
            self._plane_layer_selection_initialized = False
        self.plane_layer_bar.hide()

    def _rebuild_plane_layer_controls(
        self,
        layer_labels: tuple[tuple[str, str], ...],
    ) -> None:
        self._clear_plane_layer_controls()
        available_keys = {name.casefold() for name, _label in layer_labels}
        if layer_labels and not self._plane_layer_selection_initialized:
            first_key = layer_labels[0][0].casefold()
            self._hidden_plane_layer_keys = available_keys - {first_key}
            self._plane_layer_selection_initialized = True
        else:
            self._hidden_plane_layer_keys.intersection_update(available_keys)
        for full_name, label in layer_labels:
            key = full_name.casefold()
            checkbox = QCheckBox(label)
            checkbox.setObjectName(f"planeLayerToggle_{label}")
            checkbox.setToolTip(f"{label}: {full_name}")
            checkbox.setChecked(key not in self._hidden_plane_layer_keys)
            checkbox.toggled.connect(
                lambda checked, layer_key=key: self._plane_layer_toggled(
                    layer_key,
                    checked,
                )
            )
            self.plane_layer_layout.insertWidget(
                self.plane_layer_layout.count() - 1,
                checkbox,
            )
            self._plane_layer_checks[key] = checkbox
        self.plane_layer_bar.setVisible(bool(layer_labels))
        self.plane_layer_bar.setEnabled(bool(layer_labels) and self._worker is None)

    def _plane_layer_toggled(self, layer_key: str, visible: bool) -> None:
        if visible:
            self._hidden_plane_layer_keys.discard(layer_key)
        else:
            self._hidden_plane_layer_keys.add(layer_key)
        for item in self._plane_items_by_layer.get(layer_key, ()):
            item.setVisible(visible)

    def _set_all_plane_layers_visible(self, visible: bool) -> None:
        for checkbox in self._plane_layer_checks.values():
            previous = checkbox.blockSignals(True)
            checkbox.setChecked(visible)
            checkbox.blockSignals(previous)
        self._hidden_plane_layer_keys = (
            set() if visible else set(self._plane_layer_checks)
        )
        self._apply_plane_layer_visibility()

    def _apply_plane_layer_visibility(self) -> None:
        for key, items in self._plane_items_by_layer.items():
            visible = key not in self._hidden_plane_layer_keys
            for item in items:
                item.setVisible(visible)

    @staticmethod
    def _source_plane_items(
        geometry: dict[str, Any] | Any,
        color: QColor,
    ) -> list[QGraphicsItem]:
        """Batch normalized PowerSI artwork while preserving source run order."""
        builder = _PlanePathBuilder(geometry, color)
        while not builder.step(builder.primitive_count, maximum_points=100_000_000):
            pass
        item = builder.item()
        return [] if item is None else [item]

    def _restyle_plane_items(self, changed_net_keys: set[str] | None = None) -> None:
        """Restyle existing plane graphics without rebuilding SPD primitives."""

        board_fill = QColor("#171a1f")
        items = (
            self.board._plane_items
            if changed_net_keys is None
            else [
                item
                for key in changed_net_keys
                for item in self._plane_items_by_net.get(key, ())
            ]
        )
        for item in items:
            net_value = item.data(1)
            if net_value is None:
                continue
            net = str(net_value)
            kind = str(item.data(0) or "")
            color = self.board.display_color_for_net(net)
            if isinstance(item, _PlaneArtworkItem):
                item.set_display_color(color)
                continue
            pen = QPen(color)
            pen.setCosmetic(True)
            if kind.startswith("negative_") or kind == "solver_bounds":
                pen.setStyle(Qt.PenStyle.DashLine)
            item.setPen(pen)  # type: ignore[attr-defined]
            if kind.startswith("negative_"):
                item.setBrush(QBrush(board_fill))  # type: ignore[attr-defined]
            elif kind.startswith("positive_"):
                fill = QColor(color)
                fill.setAlpha(46)
                item.setBrush(QBrush(fill))  # type: ignore[attr-defined]
            elif kind == "solver_bounds":
                item.setBrush(QBrush())  # type: ignore[attr-defined]
            elif kind == "plane_cell":
                fill = QColor(color)
                fill.setAlpha(24)
                item.setBrush(QBrush(fill))  # type: ignore[attr-defined]

    def _refresh_colors(self) -> None:
        assert self._scenario is not None
        self.color_list.clear()
        for net, color in sorted(self._scenario.net_colors.items(), key=lambda item: item[0].casefold()):
            item = QListWidgetItem(net)
            item.setData(Qt.ItemDataRole.UserRole, net)
            item.setBackground(QBrush(QColor(color)))
            item.setForeground(QBrush(QColor("#ffffff")))
            self.color_list.addItem(item)

    def _refresh_rails(self, project: ProjectSpec | None = None) -> None:
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
        signals_were_blocked = self.rail_list.blockSignals(True)
        self.rail_list.clear()
        restored_current_item: QListWidgetItem | None = None
        for rail in (project if project is not None else self._scenario.base_project).rails:
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
        self.rail_list.blockSignals(signals_were_blocked)
        self._refresh_board_net_focus(restyle_planes=False)

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
        signals_were_blocked = self.rail_list.blockSignals(True)
        for index in range(self.rail_list.count()):
            self.rail_list.item(index).setCheckState(state)
        self.rail_list.blockSignals(signals_were_blocked)
        self._refresh_board_net_focus()

    def _evaluation_rail_check_changed(self, _item: QListWidgetItem) -> None:
        self._refresh_board_net_focus()

    def _show_evaluation_rail_context_menu(self, position: QPoint) -> None:
        item = self.rail_list.itemAt(position)
        if item is None or self._scenario is None or not self.rail_list.isEnabled():
            return
        rail_id = str(item.data(Qt.ItemDataRole.UserRole))
        rail = next(
            (
                candidate
                for candidate in self._scenario.base_project.rails
                if candidate.rail_id.casefold() == rail_id.casefold()
            ),
            None,
        )
        if rail is None:
            return
        self.rail_list.setCurrentItem(item)
        menu = QMenu(self.rail_list)
        change_color = menu.addAction(
            self._net_color_swatch(rail.net),
            f"Change color for {rail.net}...",
        )
        change_color.triggered.connect(
            lambda _checked=False, net=rail.net: self._choose_net_color_for_net(net)
        )
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(self.rail_list.viewport().mapToGlobal(position))

    def _refresh_board_net_focus(self, *, restyle_planes: bool = True) -> None:
        if self._scenario is None:
            self.board.set_active_nets(None)
            self._active_board_net_keys = None
            return
        checked_rail_keys = {
            rail_id.casefold() for rail_id in self._checked_rail_ids()
        }
        active = frozenset(
            str(rail.get("net", "")).strip().casefold()
            for rail in self._scenario.normalized_project.get("rails", ())
            if str(rail.get("rail_id", "")).strip().casefold()
            in checked_rail_keys
            and str(rail.get("net", "")).strip()
        )
        previous = self._active_board_net_keys
        self.board.set_active_nets(active)
        self._active_board_net_keys = active
        if not restyle_planes or previous == active:
            return
        changed = None if previous is None else set(previous.symmetric_difference(active))
        self._restyle_plane_items(changed)

    def _target_input_changed(self, _text: str) -> None:
        if self._comparison_batch is not None:
            self._invalidate_evaluation(
                "Target impedance changed; run Original + Tuned evaluation again."
            )
            self.status_text.setText("Target changed; evaluation required")

    def _evaluation_modal_preset_changed(self, _index: int) -> None:
        if self._comparison_batch is not None:
            self._invalidate_evaluation(
                "Numerical convergence preset changed; run Original + Tuned evaluation again."
            )
            self.status_text.setText("Numerical convergence preset changed; evaluation required")

    def _evaluation_solver_profile_changed(self, _index: int) -> None:
        """Invalidate results when the selected physics contract changes."""

        self._update_evaluation_solver_profile_help()
        self._update_evaluation_notes()
        if self._comparison_batch is not None:
            self._invalidate_evaluation(
                "Physics model changed; run Original + Tuned evaluation again."
            )
            self.status_text.setText("Physics model changed; evaluation required")

    def _selected_evaluation_solver_profile(self) -> str:
        value = self.evaluation_solver_profile_combo.currentData()
        if value in {
            _LEGACY_SOLVER_PROFILE_KEY,
            _RESEARCH_SOLVER_PROFILE_KEY,
        }:
            return str(value)
        return _LEGACY_SOLVER_PROFILE_KEY

    def _update_evaluation_solver_profile_help(self) -> None:
        research = (
            self._selected_evaluation_solver_profile()
            == _RESEARCH_SOLVER_PROFILE_KEY
        )
        if research:
            self.evaluation_solver_profile_status.setText(
                "EXPERIMENTAL RESEARCH · topology certificate required · "
                "transient / not cached · not PowerSI-validated"
            )
            self.evaluation_solver_profile_status.setStyleSheet(
                "color: #d6a64f; font-weight: 700;"
            )
        else:
            self.evaluation_solver_profile_status.setText(
                "LEGACY · default regression path"
            )
            self.evaluation_solver_profile_status.setStyleSheet(
                "color: #7aa2c7; font-weight: 600;"
            )

    def _update_evaluation_notes(
        self,
        provenance: _SolverProvenancePresentation | None = None,
    ) -> None:
        if not hasattr(self, "evaluation_notes"):
            return
        if provenance is not None:
            first_line = "Result provenance: " + provenance.banner_text
        elif (
            self._selected_evaluation_solver_profile()
            == _RESEARCH_SOLVER_PROFILE_KEY
        ):
            first_line = (
                "Selected physics model: [RESEARCH] Actual-artwork uniform mode · "
                "EXPERIMENTAL / not PowerSI-validated · topology certificate required."
            )
        else:
            first_line = (
                "Selected physics model: [LEGACY] Legacy modal · default regression path."
            )
        research = (
            provenance.badge == "RESEARCH"
            if provenance is not None
            else self._selected_evaluation_solver_profile()
            == _RESEARCH_SOLVER_PROFILE_KEY
        )
        cache_note = (
            "Research Original is recomputed for each run and remains transient: "
            "it is not cached, persisted, saved, or reused. "
            if research
            else (
                "Original results are cached inside the scenario and compared with "
                "the current Tuned state. "
            )
        )
        self.evaluation_notes.setPlainText(
            first_line
            + "\n\n"
            + "Each checked PWR NET is solved sequentially in a background worker. "
            + cache_note
            + "Phase is not plotted. Open Result Plot shows the "
            "shared impedance plot in a large, non-modal window; Plot Channels and "
            "X/Y markers only change that display. Export Tuned CSV writes the final "
            "enabled assignments plus result-derived solver provenance. "
            "The research profile replaces only the actual-artwork uniform C00 plane "
            "term; nonuniform modes retain the disclosed rectangular bounding-box "
            "approximation. Incomplete topology or source/reference evidence blocks "
            "research evaluation without falling back to Legacy modal. PowerSI data "
            "is comparison-only and is never used to fit R, L, C, or solver parameters. "
            "Numerical convergence preset changes only internal rectangular modal "
            "convergence/runtime; Experimental m12 check is an opt-in m10-to-m12 "
            "check, not a PowerSI or absolute-accuracy setting. A batch is accepted "
            "only when every Original and Tuned result reports combined convergence. "
            "Results are single-rail Zii without inter-rail coupling.\n\n"
            + _EXPLORATORY_FIDELITY_WARNING
        )

    def _selected_evaluation_modal_max_index(self) -> int:
        value = self.evaluation_modal_preset_combo.currentData()
        if isinstance(value, bool) or not isinstance(value, int):
            return DEFAULT_EVALUATION_MODAL_MAX_INDEX
        return value

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
        if self._distribution_show_original_board and self._scenario is not None:
            source_models = _source_model_ids_by_refdes(self._scenario)
            displayed = [
                self._source_display_decap(item, source_models) for item in selected
            ]
        else:
            displayed = selected
        if not selected or self._scenario is None:
            self.selection_summary.setText("No decap selected")
            self.board.set_required_companion_refdes(())
        elif self._distribution_show_original_board:
            self.selection_summary.setText(
                "Source SPD view (read-only) | "
                f"{len(displayed):,} selected | "
                f"{sum(item.enabled for item in displayed):,} enabled"
            )
            self.board.set_required_companion_refdes(())
        else:
            coverage = analyze_cluster_selection(
                self._scenario, (item.refdes for item in selected)
            )
            selection_snapshot = tuple(item.refdes for item in selected)
            selected_keys = {item.casefold() for item in selection_snapshot}
            expanded_selection = selection_with_required_cluster_members(
                self._scenario, selection_snapshot
            )
            source_cluster_companions = tuple(
                item for item in expanded_selection if item.casefold() not in selected_keys
            )
            summary = (
                f"{len(selected):,} selected | "
                f"{sum(item.enabled for item in displayed):,} enabled"
            )
            if coverage.blocked_decaps:
                summary += f" | PWR edit blocked: {coverage.blocked_decaps[0].reason}"
            elif source_cluster_companions:
                try:
                    valid_rail_keys = {
                        item.casefold()
                        for item in assignment_options_for_selection(
                            self._scenario, selection_snapshot
                        )
                    }
                    current_rail_keys = {
                        item.current_rail_id.casefold() for item in selected
                    }
                    has_alternative = any(
                        rail.rail_id.casefold() in valid_rail_keys
                        and rail.rail_id.casefold() not in current_rail_keys
                        for rail in self._scenario.base_project.rails
                    )
                    if has_alternative:
                        summary += " | Partial source cluster; valid NET choices are enabled"
                    else:
                        blocked_label = None
                        for rail in self._scenario.base_project.rails:
                            if rail.rail_id.casefold() in current_rail_keys:
                                continue
                            proposal = analyze_rail_assignment(
                                self._scenario, selection_snapshot, rail.rail_id
                            )
                            if not proposal.valid:
                                blocked_label = _proposal_block_label(proposal)
                                break
                        if blocked_label is not None:
                            summary += (
                                f" | Other PWR NETs blocked: {blocked_label}; "
                                "Ctrl+select a connected via-backed decap"
                            )
                except ScenarioEditError as exc:
                    summary += f" | PWR edit blocked: {exc}"
            self.selection_summary.setText(summary)
            self.board.set_required_companion_refdes(source_cluster_companions)
        self.selection_table.setRowCount(len(displayed))
        for row, decap in enumerate(displayed):
            values = (
                decap.refdes,
                "Enabled" if decap.enabled else "Disabled",
                decap.current_net,
                decap.model_id or "Unassigned",
                decap.footprint,
                _connection_label(self._scenario, decap.refdes),
                (
                    "Read-only source view"
                    if self._distribution_show_original_board
                    else _eligible_pwr_label(self._scenario, decap)
                ),
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
                if query
                in (
                    item.source_net
                    if self._distribution_show_original_board
                    else item.current_net
                ).casefold()
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
        if self._distribution_show_original_board:
            self.status_text.setText(
                "Source SPD board view is read-only; switch to Current / distributed "
                "view to edit assignments"
            )
            return
        selected_keys = {item.casefold() for item in selected_refdes}
        selected = [
            item
            for item in self._scenario.decaps
            if item.refdes.casefold() in selected_keys
        ]
        selection_snapshot = tuple(item.refdes for item in selected)
        selection_keys = {item.casefold() for item in selection_snapshot}
        expanded_selection = selection_with_required_cluster_members(
            self._scenario, selection_snapshot
        )
        source_cluster_companions = tuple(
            item for item in expanded_selection if item.casefold() not in selection_keys
        )
        if self._decap_context_menu is not None:
            self._decap_context_menu.deleteLater()
        menu = QMenu(self)
        # QMenu.popup() is asynchronous.  Retain the current menu (and its
        # submenus) until the next invocation instead of relying on the local
        # Python wrapper's lifetime.
        self._decap_context_menu = menu
        if source_cluster_companions:
            select_cluster = menu.addAction(
                "Select complete source cluster "
                f"(safe shortcut, +{len(source_cluster_companions)})"
            )
            select_cluster.triggered.connect(
                lambda _checked=False, refs=selection_snapshot: self.board.set_selected_refdes(
                    selection_with_required_cluster_members(self._scenario, refs)
                    if self._scenario is not None
                    else refs
                )
            )
            menu.addSeparator()
        enable = menu.addAction(
            "Disable selected" if any(item.enabled for item in selected) else "Enable selected"
        )
        target_enabled = not any(item.enabled for item in selected)
        enable.triggered.connect(
            lambda _checked=False, enabled=target_enabled, refs=selection_snapshot: (
                self._set_selected_enabled(enabled, refs)
            )
        )

        # Construct submenus explicitly with a parent.  PySide's
        # ``addMenu(str)`` overload may drop the submenu wrapper as soon as an
        # asynchronous popup call returns, which leaves its action unusable.
        pwr_menu = QMenu("Assign PWR NET", menu)
        menu.addMenu(pwr_menu)
        try:
            common_rail_ids = {
                item.casefold()
                for item in assignment_options_for_selection(
                    self._scenario, selection_snapshot
                )
            }
            blocked_reason = None
        except ScenarioEditError as exc:
            common_rail_ids = set()
            blocked_reason = str(exc)
        if blocked_reason is not None:
            unavailable = pwr_menu.addAction(
                blocked_reason
            )
            unavailable.setEnabled(False)
        else:
            for rail in self._scenario.base_project.rails:
                rail_label = f"{rail.net} ({rail.pwr_layer} / {rail.gnd_layer})"
                rail_key = rail.rail_id.casefold()
                if rail_key in common_rail_ids:
                    action = pwr_menu.addAction(rail_label)
                    action.triggered.connect(
                        lambda _checked=False, rail_id=rail.rail_id, refs=selection_snapshot: (
                            self._assign_selected_rail(rail_id, refs)
                        )
                    )
                    continue
                proposal = analyze_rail_assignment(
                    self._scenario, selection_snapshot, rail.rail_id
                )
                reason = _proposal_block_label(proposal)
                unavailable = pwr_menu.addAction(
                    f"{rail_label} — blocked: {reason}"
                )
                unavailable.setToolTip(reason)
                unavailable.setEnabled(False)

        model_menu = QMenu("Assign decap model", menu)
        menu.addMenu(model_menu)
        footprints = {item.footprint.casefold() for item in selected}
        for model in self._scenario.base_project.cap_models:
            action = model_menu.addAction(f"{model.model_id} [{model.footprint}]")
            action.setEnabled(
                len(footprints) == 1 and model.footprint.casefold() in footprints
            )
            action.triggered.connect(
                lambda _checked=False, model_id=model.model_id, refs=selection_snapshot: (
                    self._assign_selected_model(model_id, refs)
                )
            )
        model_menu.addSeparator()
        clear_model = model_menu.addAction("Unassign model")
        clear_model.triggered.connect(
            lambda _checked=False, refs=selection_snapshot: self._assign_selected_model(
                None, refs
            )
        )

        restore_label = "Restore selected to source state"
        restore_enabled = False
        restore_reason = None
        try:
            restore_analysis = analyze_restore_selection(
                self._scenario, selection_snapshot
            )
            restore_enabled = restore_analysis.valid
            if not restore_enabled:
                restore_reason = _proposal_block_label(restore_analysis)
        except ScenarioEditError as exc:
            restore_reason = str(exc)
        if restore_reason is not None:
            restore_label += f" — blocked: {restore_reason}"
        restore = menu.addAction(restore_label)
        restore.setEnabled(restore_enabled)
        if restore_reason is not None:
            restore.setToolTip(restore_reason)
        restore.triggered.connect(
            lambda _checked=False, refs=selection_snapshot: self._restore_selected(refs)
        )
        menu.popup(global_position)

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

    def _commit_atomic_edit(self, updated: ScenarioSpec) -> None:
        if self._scenario is None or updated is self._scenario:
            self.status_text.setText("No scenario change was required")
            return
        self._scenario = updated
        self._dirty = True
        self._invalidate_evaluation()
        self._refresh_all()

    def _edit_refdes(
        self, selected_refdes: tuple[str, ...] | None
    ) -> tuple[str, ...]:
        return (
            self.board.selected_refdes
            if selected_refdes is None
            else selected_refdes
        )

    def _assign_selected_rail(
        self,
        rail_id: str,
        selected_refdes: tuple[str, ...] | None = None,
    ) -> None:
        if self._scenario is None:
            return
        try:
            updated = assign_rail_atomic(
                self._scenario, self._edit_refdes(selected_refdes), rail_id
            )
            self._commit_atomic_edit(updated)
        except ScenarioEditError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))

    def _assign_selected_model(
        self,
        model_id: str | None,
        selected_refdes: tuple[str, ...] | None = None,
    ) -> None:
        if self._scenario is None:
            return
        try:
            updated = assign_model_atomic(
                self._scenario, self._edit_refdes(selected_refdes), model_id
            )
            self._commit_atomic_edit(updated)
        except ScenarioEditError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))

    def _set_selected_enabled(
        self,
        enabled: bool,
        selected_refdes: tuple[str, ...] | None = None,
    ) -> None:
        if self._scenario is None:
            return
        try:
            updated = set_enabled_atomic(
                self._scenario, self._edit_refdes(selected_refdes), enabled
            )
            self._commit_atomic_edit(updated)
        except ScenarioEditError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))

    def _restore_selected(
        self, selected_refdes: tuple[str, ...] | None = None
    ) -> None:
        if self._scenario is None:
            return
        try:
            updated = restore_source_atomic(
                self._scenario, self._edit_refdes(selected_refdes)
            )
            self._commit_atomic_edit(updated)
        except ScenarioEditError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))

    def _choose_net_color(self, item: QListWidgetItem) -> None:
        if self._scenario is None:
            return
        self._choose_net_color_for_net(str(item.data(Qt.ItemDataRole.UserRole)))

    def _choose_net_color_for_net(self, net: str) -> None:
        if self._scenario is None:
            return
        matching_keys = [
            key for key in self._scenario.net_colors if key.casefold() == net.casefold()
        ]
        initial = (
            QColor(self._scenario.net_colors[matching_keys[0]])
            if matching_keys
            else self.board.color_for_net(net)
        )
        color = QColorDialog.getColor(initial, self, f"Color for {net}")
        if not color.isValid():
            return
        colors = dict(self._scenario.net_colors)
        for key in matching_keys:
            colors.pop(key, None)
        colors[net] = color.name().upper()
        self._scenario = ScenarioSpec.model_validate(
            {**self._scenario.model_dump(mode="json"), "net_colors": colors}
        )
        self._dirty = True
        self._refresh_all()
        if self._comparison_batch is not None:
            try:
                self._refresh_results_window(self._comparison_batch.comparisons)
            except (KeyError, RuntimeError, TypeError, ValueError):
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
        modal_max_index = self._selected_evaluation_modal_max_index()
        solver_profile = self._selected_evaluation_solver_profile()
        request = _EvaluationRunRequest(
            scenario=self._scenario,
            rail_ids=tuple(rail_ids),
            target_ohm=target,
            modal_max_index=modal_max_index,
            solver_profile=solver_profile,
            attachments=dict(self._attachments),
        )
        self._pending_evaluation_launch = None
        worker = FunctionWorker(
            _job_preflight_evaluation,
            request.scenario,
            request.rail_ids,
        )
        self._run_worker(
            worker,
            lambda connectivity: self._accept_evaluation_preflight(
                connectivity, request
            ),
            label=(
                f"Checking Evaluation Analysis for "
                f"{len(request.rail_ids):,} PWR NET(s)..."
            ),
            on_error=self._evaluation_preflight_worker_error,
        )

    def _accept_evaluation_preflight(
        self, connectivity: Any, request: _EvaluationRunRequest
    ) -> None:
        """Present an exact partial-run manifest and require explicit consent."""

        if self._scenario is not request.scenario:
            self.status_text.setText("Discarded stale Evaluation Analysis preflight")
            return
        selected = tuple(str(item) for item in connectivity.rail_ids)
        blocked_keys = {
            str(blocker.rail_id).casefold() for blocker in connectivity.blockers
        }
        runnable = tuple(
            rail_id for rail_id in selected if rail_id.casefold() not in blocked_keys
        )
        blocked = tuple(
            rail_id for rail_id in selected if rail_id.casefold() in blocked_keys
        )
        details = str(connectivity.message())
        manifest = _EvaluationRunManifest(
            selected_rail_ids=selected,
            runnable_rail_ids=runnable,
            blocked_rail_ids=blocked,
            blocker_count=len(connectivity.blockers),
            blocker_details=details,
        )

        if not runnable:
            self._invalidate_evaluation(
                "Evaluation blocked before Original/Tuned processing.\n\n"
                + "\n".join(manifest.summary_lines())
            )
            summary = (
                f"Evaluation cannot start: {manifest.blocker_count:,} decap "
                f"connectivity/modelability blocker(s) affect {len(blocked):,} of "
                f"{len(selected):,} selected PWR rail(s)."
            )
            warning = QMessageBox(self)
            warning.setIcon(QMessageBox.Icon.Warning)
            warning.setWindowTitle(APP_DISPLAY_NAME)
            warning.setText(summary)
            warning.setInformativeText(
                "Use Show Details to review the affected rails and components. "
                "The complete list is also available in Evaluation Summary."
            )
            warning.setDetailedText(details)
            warning.setStandardButtons(QMessageBox.StandardButton.Ok)
            warning.exec()
            self.status_text.setText(
                "Evaluation blocked: 0 clear PWR NETs; no worker started"
            )
            return

        if blocked:
            prompt = QMessageBox(self)
            prompt.setIcon(QMessageBox.Icon.Warning)
            prompt.setWindowTitle(APP_DISPLAY_NAME)
            prompt.setText(
                "Run a partial Evaluation Analysis?"
            )
            prompt.setInformativeText(
                f"{len(runnable):,} clear PWR NET(s) will run; "
                f"{len(blocked):,} blocked PWR NET(s) will NOT run. "
                "No selected rail will be silently dropped. Use Show Details "
                "to review the exact run/blocked manifest."
            )
            prompt.setDetailedText("\n".join(manifest.summary_lines()))
            prompt.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            prompt.setDefaultButton(QMessageBox.StandardButton.No)
            if prompt.exec() != QMessageBox.StandardButton.Yes:
                self.evaluation_summary.setPlainText(
                    "Partial Evaluation Analysis cancelled by user; no new "
                    "results were produced.\n\n"
                    + "\n".join(manifest.summary_lines())
                )
                self.status_text.setText(
                    f"Partial evaluation cancelled: {len(runnable):,} clear / "
                    f"{len(blocked):,} blocked"
                )
                return

        # The preflight worker still owns the busy state while its result signal
        # is being handled.  Defer the evaluation worker until finished clears
        # that state, avoiding a GUI-thread preflight and a worker-chain race.
        self._pending_evaluation_launch = (request, manifest)

    def _launch_evaluation_after_preflight(
        self, request: _EvaluationRunRequest, manifest: _EvaluationRunManifest
    ) -> None:
        if self._scenario is not request.scenario:
            self.status_text.setText("Discarded stale Evaluation Analysis request")
            return

        from ..evaluation import (
            baseline_fallback_model_refdes,
            evaluate_comparison_batch,
        )

        rail_ids = manifest.runnable_rail_ids
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
                self.evaluation_summary.setPlainText(
                    "Evaluation Analysis cancelled before baseline capture.\n\n"
                    + "\n".join(manifest.summary_lines())
                )
                self.status_text.setText("Evaluation Analysis cancelled")
                return
        if self._scenario_path is None:
            path = self._request_scenario_save_path()
            if path is None:
                self.evaluation_summary.setPlainText(
                    "Evaluation Analysis cancelled before choosing a scenario "
                    "save path.\n\n" + "\n".join(manifest.summary_lines())
                )
                self.status_text.setText("Evaluation Analysis cancelled")
                return
            self._scenario_path = path

        if manifest.is_partial:
            self._invalidate_evaluation(
                "Starting explicitly confirmed partial Evaluation Analysis."
            )
        self._active_evaluation_manifest = manifest
        worker = FunctionWorker(
            evaluate_comparison_batch,
            prepared,
            rail_ids,
            target_ohm=request.target_ohm,
            modal_max_index=request.modal_max_index,
            solver_profile=request.solver_profile,
            attachments=dict(request.attachments),
        )
        profile_prefix = (
            "RESEARCH / not PowerSI-validated · "
            if request.solver_profile == _RESEARCH_SOLVER_PROFILE_KEY
            else "LEGACY · "
        )
        self.evaluation_summary.setPlainText(
            f"Evaluating Original and Tuned configurations for "
            f"{len(rail_ids):,} PWR NET(s) with "
            f"{profile_prefix}{self.evaluation_solver_profile_combo.currentText()} and "
            f"{self.evaluation_modal_preset_combo.currentText()} numerical convergence. "
            "All computation runs in the background; Cancel remains available.\n\n"
            + "\n".join(manifest.summary_lines())
        )
        self._run_worker(
            worker,
            self._accept_evaluation,
            label=f"Evaluating {len(rail_ids):,} PWR NET(s)...",
            on_error=self._evaluation_worker_error,
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

        rejected_convergence = _rejected_comparison_convergence(comparisons)
        if rejected_convergence:
            message = (
                "Evaluation rejected: every Original and Tuned result must report "
                "combined frequency and modal convergence. No scenario, cache, "
                "or autosave mutation was accepted; prior plot, export, and AI state "
                "was cleared.\n\n"
                + "\n".join(rejected_convergence)
            )
            self._auto_save_after_worker = False
            self._invalidate_evaluation(message)
            QMessageBox.warning(self, APP_DISPLAY_NAME, message)
            self.status_text.setText("Rejected nonconverged evaluation batch")
            return

        current = self._scenario
        try:
            provenance = _comparison_solver_provenance(comparisons)
            rail_labels = self._render_comparisons(
                comparisons,
                provenance=provenance,
            )
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
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
        if updated_scenario.baseline_captures != current.baseline_captures:
            # Legacy scenarios can acquire immutable source-model bindings at
            # the first accepted baseline capture. Rebuild only that source
            # display cache; ordinary current/distribution edits keep it hot.
            self._source_board_records = ()
            self._source_board_cache_key = None
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
        self._update_result_plot_button()
        self.export_tuned_csv_button.setToolTip(
            "Export enabled Decaps from the evaluated Tuned PWR NETs with "
            "result-derived solver provenance.\n\n" + provenance.details_text
        )

        self.comparison_table.setRowCount(len(comparisons))
        for row, comparison in enumerate(comparisons):
            baseline = comparison.baseline.view
            tuned = comparison.tuned.view
            net = rail_labels[comparison.rail_id]
            values = (
                net,
                f"{baseline.cap_count:,} → {tuned.cap_count:,}",
                impedance_transition_at_frequency(baseline, tuned, 1.0e6),
                impedance_transition_at_frequency(baseline, tuned, 10.0e6),
                impedance_transition_at_frequency(baseline, tuned, 100.0e6),
                f"{baseline.max_violation_db:.3f} → {tuned.max_violation_db:.3f} dB",
                (
                    "Transient / not cached"
                    if provenance.badge == "RESEARCH"
                    else (
                        "Reused" if comparison.baseline_from_cache else "Saved now"
                    )
                ),
                (
                    f"{baseline.confidence}: {baseline.confidence_note}"
                    f" → {tuned.confidence}: {tuned.confidence_note}"
                ),
                (
                    f"{_modal_convergence_text(baseline)}"
                    f" → {_modal_convergence_text(tuned)}"
                ),
                provenance.banner_text,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 9:
                    item.setToolTip(provenance.details_text)
                self.comparison_table.setItem(row, column, item)
        self.comparison_table.resizeColumnsToContents()
        assert self._results_window is not None
        self._results_window.copy_table_from(self.comparison_table)

        self.ai_rail_combo.blockSignals(True)
        self.ai_rail_combo.clear()
        for comparison in comparisons:
            self.ai_rail_combo.addItem(
                f"{rail_labels[comparison.rail_id]} ({comparison.rail_id})",
                comparison.rail_id,
            )
        self.ai_rail_combo.blockSignals(False)
        self._ai_rail_changed()

        research = provenance.badge == "RESEARCH"
        if research:
            baseline_note = (
                "Original baseline: recomputed for this run; transient / not cached "
                "or persisted."
            )
            save_note = (
                "Research result curves are session-only and are not written to the "
                "scenario baseline cache."
            )
        else:
            cached_count = sum(item.baseline_from_cache for item in comparisons)
            newly_saved = len(comparisons) - cached_count
            baseline_note = (
                f"Original baseline: {cached_count:,} reused, {newly_saved:,} newly "
                "evaluated and staged."
            )
            save_note = (
                f"Original results will be saved automatically to {self._scenario_path.name}."
                if newly_saved
                else f"Original results are stored in {self._scenario_path.name}."
            )
        manifest = self._active_evaluation_manifest
        partial_scope_lines: tuple[str, ...] = ()
        if manifest is not None and manifest.is_partial:
            partial_scope_lines = (
                "",
                "PARTIAL EVALUATION — results do not cover every selected PWR NET.",
                *manifest.summary_lines(),
            )
        self.evaluation_summary.setPlainText(
            "\n".join(
                (
                    "Solver provenance: " + provenance.banner_text,
                    f"Compared {len(comparisons):,} PWR NET(s): Original vs Tuned.",
                    baseline_note,
                    "Result plot window: one shared impedance view; all PWR NETs start visible and can be filtered independently.",
                    "Tuned Decap CSV: enabled final assignments from the evaluated PWR NETs.",
                    (
                        "Numerical convergence preset: "
                        f"{self.evaluation_modal_preset_combo.currentText()}. "
                        "It changes internal rectangular modal convergence/runtime only; "
                        "it is not a PowerSI or absolute-accuracy setting."
                    ),
                    _EXPLORATORY_FIDELITY_WARNING,
                    save_note,
                    "Select a Tuned result in AI Assist when analysis is needed.",
                    *partial_scope_lines,
                )
            )
        )
        self._update_evaluation_notes(provenance)
        self._refresh_all()
        if manifest is not None and manifest.is_partial:
            self.status_text.setText(
                f"Evaluation complete (PARTIAL): {len(comparisons):,} clear ran; "
                f"{len(manifest.blocked_rail_ids):,} blocked NOT evaluated"
            )
        else:
            self.status_text.setText(
                f"Evaluation complete: {len(comparisons):,} PWR NET(s)"
            )

    def _render_comparisons(
        self,
        comparisons: tuple[Any, ...],
        *,
        provenance: _SolverProvenancePresentation,
    ) -> dict[str, str]:
        rail_colors, rail_labels = self._comparison_plot_metadata(comparisons)
        if self._results_window is None:
            self._results_window = ComparisonResultsWindow(self)
        self._results_window.set_provenance(
            provenance.banner_text,
            research=provenance.badge == "RESEARCH",
            details=provenance.details_text,
        )
        # Validate the exact detached plot before accepting completed results.
        # The window stays hidden until the user presses the explicit button.
        self._results_window.set_plot_results(
            comparisons,
            rail_colors=rail_colors,
            rail_labels=rail_labels,
        )
        return rail_labels

    def _comparison_plot_metadata(
        self, comparisons: tuple[Any, ...]
    ) -> tuple[dict[str, str], dict[str, str]]:
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
        return rail_colors, rail_labels

    def _show_results_window(self) -> None:
        comparisons = tuple(
            getattr(self._comparison_batch, "comparisons", ())
            if self._comparison_batch is not None
            else ()
        )
        if not comparisons or self._scenario is None:
            return
        try:
            if self._results_window is None:
                self._results_window = ComparisonResultsWindow(self)
                self._refresh_results_window(comparisons)
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.critical(
                self,
                APP_DISPLAY_NAME,
                f"The result plot could not be opened.\n\n{exc}",
            )
            self.status_text.setText("Result plot validation failed")
            return
        self._results_window.show()
        self._results_window.raise_()
        self._results_window.activateWindow()

    def _export_tuned_decaps_csv(self) -> None:
        if self._scenario is None or self._comparison_batch is None:
            return
        comparisons = tuple(getattr(self._comparison_batch, "comparisons", ()))
        if not comparisons:
            return
        rows = _tuned_decap_csv_rows(
            self._scenario,
            tuple(str(comparison.rail_id) for comparison in comparisons),
        )
        try:
            provenance = _comparison_solver_provenance(comparisons)
        except ValueError as exc:
            QMessageBox.critical(self, APP_DISPLAY_NAME, str(exc))
            self.status_text.setText("Tuned Decap CSV provenance validation failed")
            return
        evaluated_keys = {
            str(comparison.rail_id).casefold() for comparison in comparisons
        }
        connected_keys = {
            refdes.casefold()
            for refdes in self._scenario.electrically_connected_refdes
        }
        excluded = sum(
            item.enabled
            and item.current_rail_id.casefold() in evaluated_keys
            and item.refdes.casefold() not in connected_keys
            for item in self._scenario.decaps
        )
        default_name = f"{Path(self._scenario.source.name).stem}_tuned_decaps.csv"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Tuned Decaps CSV",
            default_name,
            "CSV files (*.csv);;All files (*)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.casefold() != ".csv":
            path = path.with_suffix(".csv")
        try:
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(
                    (
                        "Component",
                        "REFDES",
                        "NET Name",
                        "Solver Profile",
                        "Profile Badge",
                        "Solver Version",
                        "Source-only Status",
                        "PowerSI Parameter Use",
                        "Compiler Algorithm ID",
                        "Compiler Version",
                        "Artwork Evidence SHA-256",
                        "Research Identity SHA-256",
                        "Static Compiler Algorithm SHA-256",
                        "Source SHA-256",
                        "Geometry Manifest SHA-256",
                        "Component Manifest SHA-256",
                        "Material Manifest SHA-256",
                        "Topology Certificate SHA-256",
                    )
                )
                source_only = "Yes" if provenance.source_only else "No"
                writer.writerows(
                    (
                        *row,
                        provenance.label,
                        provenance.badge,
                        provenance.solver_version,
                        f"{source_only} ({provenance.source_only_status})",
                        "None (comparison-only)",
                        provenance.compiler_algorithm_id,
                        provenance.compiler_version,
                        provenance.artwork_evidence_sha256,
                        provenance.research_identity_sha256,
                        provenance.static_compiler_algorithm_sha256,
                        provenance.source_sha256,
                        provenance.geometry_manifest_sha256,
                        provenance.component_manifest_sha256,
                        provenance.material_manifest_sha256,
                        provenance.topology_certificate_sha256,
                    )
                    for row in rows
                )
        except OSError as exc:
            QMessageBox.critical(
                self,
                APP_DISPLAY_NAME,
                f"The Tuned Decap CSV could not be written.\n\n{exc}",
            )
            self.status_text.setText("Tuned Decap CSV export failed")
            return
        suffix = (
            f"; excluded {excluded:,} floating/unresolved item(s)"
            if excluded
            else ""
        )
        self.status_text.setText(
            f"Exported {len(rows):,} Tuned Decap(s) to {path.name} "
            f"[{provenance.badge} · {provenance.solver_version}]{suffix}"
        )

    def _refresh_results_window(
        self, comparisons: tuple[Any, ...] | None = None
    ) -> None:
        if self._results_window is None:
            return
        rendered = comparisons
        if rendered is None:
            rendered = tuple(
                getattr(self._comparison_batch, "comparisons", ())
                if self._comparison_batch is not None
                else ()
            )
        if not rendered or self._scenario is None:
            self._results_window.clear_results()
            return
        provenance = _comparison_solver_provenance(rendered)
        self._results_window.set_provenance(
            provenance.banner_text,
            research=provenance.badge == "RESEARCH",
            details=provenance.details_text,
        )
        rail_colors, rail_labels = self._comparison_plot_metadata(rendered)
        self._results_window.set_results(
            rendered,
            rail_colors=rail_colors,
            rail_labels=rail_labels,
            source_table=self.comparison_table,
        )

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
        if self._results_window is not None:
            self._results_window.close()
        if self._distribution_window is not None:
            self._distribution_window.close()
        event.accept()


__all__ = ["MainWindow"]
