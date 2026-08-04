"""Scenario-only adapter for the existing MLO PDN evaluation engine.

The adapter deliberately exposes evaluation and evidence-grounded plot analysis
only.  It creates a transient :class:`~spd_decap_pi._core.domain.ProjectSpec`; neither the
normalized SPD project stored in a scenario nor the source SPD is mutated.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Callable, Mapping, Sequence

from spd_decap_pi._core import services as evaluation_services
from spd_decap_pi._core.domain import (
    CapModel,
    PinKind,
    PinRecord,
    PlacementAssignment,
    ProjectSpec,
    RailSpec,
    SharedPadCapacitorComponentSpec,
    SharedPadClusterSpec,
    SharedPadGroundComponentSpec,
    SharedPadPowerComponentSpec,
    SharedPadViaPath,
    TerminalKind,
    TopologyKind,
    TopologyMap,
    ViaLoopTemplate,
)
from spd_decap_pi._core.services import (
    DEFAULT_EVALUATION_MODAL_MAX_INDEX,
    EvaluationView,
    LocalAIAnalysisView,
    WorkspaceState,
    scoped_blas_threads,
)
from spd_decap_pi._core.solver import SOLVER_VERSION
from spd_decap_pi._core.via_model import ViaModelError, estimate_via_segment_rl

from .scenario import (
    CachedEvaluationMetadata,
    DecapConnectionKind,
    DecapPadState,
    EvaluationRole,
    RailEligibility,
    SHARED_PAD_ANALYSIS_VERSION,
    ScenarioDecap,
    ScenarioResultKey,
    ScenarioSpec,
    SharedPadClusterState,
    derive_shared_pad_current_components,
    mixed_reference_ground_witness_failures,
    shared_pad_component_eligibility,
)


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
PLOT_ANALYST_MODE = "Plot Analyst"
EVALUATION_ATTACHMENT_FORMAT = "spd-decap-evaluation-0.1"
MAX_EVALUATION_ATTACHMENT_BYTES = 16 * 1024 * 1024
MAX_SHARED_PAD_VIA_PATHS = 128


class ScenarioEvaluationBuildError(ValueError):
    """Actionable failure while adapting scenario state to the solver domain."""

    def __init__(self, code: str, message: str, *, refdes: str | None = None) -> None:
        self.code = code
        self.refdes = refdes
        prefix = f"{refdes}: " if refdes else ""
        super().__init__(f"{prefix}{message}")


def _require_current_shared_pad_analysis(scenario: ScenarioSpec) -> None:
    """Block stale connectivity classifications before they reach the solver."""

    analysis = scenario.connection_analysis
    if analysis is not None and analysis.version != SHARED_PAD_ANALYSIS_VERSION:
        raise ScenarioEvaluationBuildError(
            "CONNECTION_ANALYSIS_UPGRADE_REQUIRED",
            "this scenario uses legacy shared-pad connectivity; reopen the "
            "verified source SPD to build V5 finite-pad/ordered-boolean "
            "TOP-copper evidence before evaluation",
        )


@dataclass(frozen=True, slots=True)
class EvaluationConnectivityBlocker:
    """One source-connectivity classification that blocks a selected rail."""

    rail_id: str
    refdes: str
    kind: DecapConnectionKind
    reason: str


@dataclass(frozen=True, slots=True)
class EvaluationConnectivityPreflight:
    """Structured connectivity gate for one canonical selected-rail set."""

    rail_ids: tuple[str, ...]
    blockers: tuple[EvaluationConnectivityBlocker, ...]

    @property
    def is_clear(self) -> bool:
        return not self.blockers

    def message(self, *, max_refdes_per_group: int = 6) -> str:
        """Return a compact, actionable UI/error description without hiding rails."""

        if self.is_clear:
            return "Selected PWR rails have no unresolved decap connectivity blockers."
        grouped: dict[tuple[str, DecapConnectionKind, str], list[str]] = {}
        for blocker in self.blockers:
            grouped.setdefault(
                (blocker.rail_id, blocker.kind, blocker.reason), []
            ).append(blocker.refdes)
        rails: dict[str, list[str]] = {rail_id: [] for rail_id in self.rail_ids}
        for (rail_id, kind, reason), refdes in grouped.items():
            ordered = sorted(refdes, key=str.casefold)
            examples = ", ".join(ordered[:max_refdes_per_group])
            if len(ordered) > max_refdes_per_group:
                examples += f", +{len(ordered) - max_refdes_per_group:,} more"
            rails[rail_id].append(
                f"{kind.value}: {examples} ({reason})"
            )
        details = "; ".join(
            f"{rail_id} [{'; '.join(rails[rail_id])}]"
            for rail_id in self.rail_ids
            if rails[rail_id]
        )
        return (
            f"Evaluation is blocked by {len(self.blockers):,} decap connection "
            f"classification(s) on {sum(bool(items) for items in rails.values()):,} "
            f"of {len(self.rail_ids):,} selected PWR rail(s): {details}"
        )


class ScenarioEvaluationPreflightError(ScenarioEvaluationBuildError):
    """Raised before baseline/cache/solver work when selected rails are blocked."""

    def __init__(self, preflight: EvaluationConnectivityPreflight) -> None:
        self.preflight = preflight
        super().__init__("EVALUATION_CONNECTIVITY_BLOCKED", preflight.message())


class ScenarioEvaluationCacheError(ValueError):
    """A stored result is present but cannot be trusted for comparison."""


@dataclass(frozen=True, slots=True)
class ScenarioEvaluation:
    """One evaluated scenario plus deterministic cache/staleness identity."""

    state: WorkspaceState | None
    view: EvaluationView
    result_key: ScenarioResultKey
    scenario_revision: int

    @property
    def design_fingerprint(self) -> str:
        return self.result_key.design_fingerprint

    @property
    def evaluation_fingerprint(self) -> str:
        """Hash including design, rail, numerical settings, and solver version."""

        return self.result_key.cache_key

    def matches(self, scenario: ScenarioSpec, *, require_revision: bool = False) -> bool:
        """Return whether this result still describes ``scenario``.

        Revision is optional because selection/color edits may advance a UI
        revision without changing the electrical design fingerprint.
        """

        if scenario.design_fingerprint != self.design_fingerprint:
            return False
        return not require_revision or scenario.revision == self.scenario_revision

    def compact(self) -> "ScenarioEvaluation":
        """Drop the transient solver project while retaining plot evidence."""

        return replace(self, state=None)


@dataclass(frozen=True, slots=True)
class RailComparison:
    rail_id: str
    baseline: ScenarioEvaluation
    tuned: ScenarioEvaluation
    baseline_from_cache: bool
    configuration_unchanged: bool = False


@dataclass(frozen=True, slots=True)
class ScenarioEvaluationBatch:
    """Atomic multi-rail Original/Tuned comparison returned by one worker."""

    comparisons: tuple[RailComparison, ...]
    updated_scenario: ScenarioSpec
    updated_attachments: dict[str, bytes]
    requested_design_fingerprint: str
    requested_revision: int

    @property
    def selected_rail_ids(self) -> tuple[str, ...]:
        return tuple(item.rail_id for item in self.comparisons)

    def matches(self, scenario: ScenarioSpec) -> bool:
        return (
            scenario.design_fingerprint == self.requested_design_fingerprint
            and scenario.revision == self.requested_revision
        )

    def validate_for_scenario(self, scenario: ScenarioSpec) -> None:
        """Fail closed unless every nested result matches the batch envelope."""

        if not self.matches(scenario):
            raise ScenarioEvaluationCacheError(
                "comparison batch no longer matches the current scenario"
            )
        if not self.comparisons:
            raise ScenarioEvaluationCacheError("comparison batch is empty")
        if self.updated_scenario.design_fingerprint != scenario.design_fingerprint:
            raise ScenarioEvaluationCacheError(
                "comparison batch updated scenario changed the tuned design"
            )
        if self.updated_scenario.revision not in {
            scenario.revision,
            scenario.revision + 1,
        }:
            raise ScenarioEvaluationCacheError(
                "comparison batch updated scenario has an invalid revision"
            )
        for key, capture in scenario.baseline_captures.items():
            if self.updated_scenario.baseline_captures.get(key) != capture:
                raise ScenarioEvaluationCacheError(
                    "comparison batch attempted to replace an existing baseline capture"
                )
        for key, metadata in scenario.evaluation_cache.items():
            if self.updated_scenario.evaluation_cache.get(key) != metadata:
                raise ScenarioEvaluationCacheError(
                    "comparison batch attempted to replace an existing cached result"
                )
        _validated_scenario_attachments(
            self.updated_scenario, self.updated_attachments
        )

        available = {
            item.rail_id.casefold(): item.rail_id
            for item in scenario.base_project.rails
        }
        seen: set[str] = set()
        for comparison in self.comparisons:
            rail_key = comparison.rail_id.casefold()
            if rail_key not in available or rail_key in seen:
                raise ScenarioEvaluationCacheError(
                    f"comparison batch contains invalid or duplicate rail "
                    f"{comparison.rail_id!r}"
                )
            seen.add(rail_key)
            baseline = comparison.baseline
            tuned = comparison.tuned
            _validate_evaluation_view(baseline.view)
            _validate_evaluation_view(tuned.view)
            if (
                baseline.view.rail_id.casefold() != rail_key
                or tuned.view.rail_id.casefold() != rail_key
                or baseline.result_key.rail_id.casefold() != rail_key
                or tuned.result_key.rail_id.casefold() != rail_key
            ):
                raise ScenarioEvaluationCacheError(
                    f"comparison result rail identity disagrees for {comparison.rail_id!r}"
                )
            if not tuned.matches(scenario):
                raise ScenarioEvaluationCacheError(
                    f"Tuned result for {comparison.rail_id!r} is stale"
                )
            if (
                baseline.result_key.settings_sha256
                != tuned.result_key.settings_sha256
                or baseline.result_key.solver_version
                != tuned.result_key.solver_version
                or baseline.view.solver_version
                != baseline.result_key.solver_version
                or tuned.view.solver_version != tuned.result_key.solver_version
                or baseline.view.target_ohm != tuned.view.target_ohm
            ):
                raise ScenarioEvaluationCacheError(
                    f"Original/Tuned settings disagree for {comparison.rail_id!r}"
                )
            capture = next(
                (
                    item
                    for key, item in self.updated_scenario.baseline_captures.items()
                    if key.casefold() == rail_key
                ),
                None,
            )
            if (
                capture is None
                or baseline.design_fingerprint
                != capture.evaluation_input_sha256
            ):
                raise ScenarioEvaluationCacheError(
                    f"Original result for {comparison.rail_id!r} has no matching capture"
                )
            metadata = self.updated_scenario.evaluation_cache.get(
                baseline.evaluation_fingerprint
            )
            if (
                metadata is None
                or metadata.role != EvaluationRole.BASELINE
                or metadata.baseline_capture_sha256
                != capture.capture_fingerprint
            ):
                raise ScenarioEvaluationCacheError(
                    f"Original result for {comparison.rail_id!r} is not persisted"
                )
            persisted = _decode_baseline_evaluation(
                _attachment_content(
                    self.updated_attachments, metadata.attachment_name
                ),
                expected_key=baseline.result_key,
                expected_capture_fingerprint=capture.capture_fingerprint,
                scenario_revision=self.updated_scenario.revision,
            )
            if persisted.view != baseline.view:
                raise ScenarioEvaluationCacheError(
                    f"displayed Original result for {comparison.rail_id!r} "
                    "differs from its persisted attachment"
                )
            if comparison.configuration_unchanged and baseline.view != tuned.view:
                raise ScenarioEvaluationCacheError(
                    f"unchanged comparison for {comparison.rail_id!r} has different results"
                )


def _canonical_json(data: object) -> bytes:
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _evaluation_settings(
    target_ohm: float | None, modal_max_index: int
) -> dict[str, float | int | None]:
    return {
        "target_ohm": target_ohm,
        "modal_max_index": modal_max_index,
    }


def _expected_result_key(
    design_fingerprint: str,
    rail_id: str,
    *,
    target_ohm: float | None,
    modal_max_index: int,
) -> ScenarioResultKey:
    return ScenarioResultKey.from_settings(
        design_fingerprint=design_fingerprint,
        rail_id=rail_id,
        settings=_evaluation_settings(target_ohm, modal_max_index),
        solver_version=SOLVER_VERSION,
    )


def _canonical_rail_ids(
    scenario: ScenarioSpec, rail_ids: Sequence[str]
) -> tuple[str, ...]:
    requested = {str(item).strip().casefold() for item in rail_ids if str(item).strip()}
    if not requested:
        raise ScenarioEvaluationBuildError(
            "EVALUATION_RAILS_EMPTY", "select at least one PWR rail"
        )
    available = {
        item.rail_id.casefold(): item.rail_id for item in scenario.base_project.rails
    }
    unknown = requested - set(available)
    if unknown:
        raise ScenarioEvaluationBuildError(
            "EVALUATION_RAIL_UNKNOWN",
            "unknown PWR rail selection(s): " + ", ".join(sorted(unknown)),
        )
    return tuple(
        item.rail_id
        for item in scenario.base_project.rails
        if item.rail_id.casefold() in requested
    )


def preflight_evaluation_connectivity(
    scenario: ScenarioSpec, rail_ids: Sequence[str]
) -> EvaluationConnectivityPreflight:
    """Aggregate fail-closed source-connectivity blockers for selected rails.

    This intentionally follows the evaluation builder's fail-closed ordering:
    unresolved or out-of-scope source connectivity on a selected rail blocks
    before population or isolation-gap materialization is considered. The
    returned rail IDs are canonical project spellings and the full blocker list
    is retained for callers that need more than the compact UI text.
    """

    canonical_rails = _canonical_rail_ids(scenario, rail_ids)
    _require_current_shared_pad_analysis(scenario)
    analysis = scenario.connection_analysis
    if analysis is None:
        return EvaluationConnectivityPreflight(canonical_rails, ())
    canonical_by_key = {
        rail_id.casefold(): rail_id for rail_id in canonical_rails
    }
    rail_order = {rail_id.casefold(): index for index, rail_id in enumerate(canonical_rails)}
    connections = {
        item.refdes.casefold(): item for item in analysis.connections.values()
    }
    mixed_witness_failures = mixed_reference_ground_witness_failures(scenario)
    blockers: list[EvaluationConnectivityBlocker] = []
    for rail_id, reason in mixed_witness_failures.items():
        if rail_id.casefold() not in canonical_by_key:
            continue
        blockers.append(
            EvaluationConnectivityBlocker(
                rail_id=canonical_by_key[rail_id.casefold()],
                refdes="<mixed-reference GND>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=reason,
            )
        )
    blocking_kinds = {
        DecapConnectionKind.UNRESOLVED,
        DecapConnectionKind.OUT_OF_SCOPE,
    }
    for decap in scenario.decaps:
        if decap.current_rail_id.casefold() not in canonical_by_key:
            continue
        connection = connections.get(decap.refdes.casefold())
        if connection is None:
            continue
        if connection.kind in blocking_kinds:
            blockers.append(
                EvaluationConnectivityBlocker(
                    rail_id=canonical_by_key[decap.current_rail_id.casefold()],
                    refdes=decap.refdes,
                    kind=connection.kind,
                    reason=connection.reason
                    or "Decap pad/via connectivity is unresolved",
                )
            )
            continue
        if connection.kind != DecapConnectionKind.DIRECT:
            continue
        unique_power_vias = {
            item.via_id.casefold() for item in connection.power_vias
        }
        unique_ground_vias = {
            item.via_id.casefold() for item in connection.ground_vias
        }
        if (
            not decap.enabled
            and len(unique_power_vias) == 1
            and len(unique_ground_vias) == 1
        ):
            continue
        rail = next(
            (
                item
                for item in scenario.base_project.rails
                if item.rail_id.casefold() == decap.current_rail_id.casefold()
            ),
            None,
        )
        eligibility = next(
            (
                item
                for rail_id, item in decap.eligibility.items()
                if rail_id.casefold() == decap.current_rail_id.casefold()
            ),
            None,
        )
        reason: str | None = None
        if rail is None:
            reason = "evaluation modelability: current rail is absent"
        elif eligibility is None:
            reason = "evaluation modelability: current-rail eligibility is missing"
        elif not eligibility.allowed:
            reason = (
                "evaluation modelability: current-rail eligibility is blocked"
                + (f" ({eligibility.reason})" if eligibility.reason else "")
            )
        elif (
            eligibility.rail_id.casefold() != rail.rail_id.casefold()
            or eligibility.net.casefold() != rail.net.casefold()
            or eligibility.pwr_layer.casefold() != rail.pwr_layer.casefold()
            or eligibility.gnd_layer.casefold() != rail.gnd_layer.casefold()
        ):
            reason = "evaluation modelability: eligibility does not match the selected rail pair"
        elif not eligibility.via_template_id:
            reason = "evaluation modelability: current-rail via template is missing"
        else:
            template = next(
                (
                    item
                    for item in scenario.base_project.via_templates
                    if item.template_id.casefold()
                    == eligibility.via_template_id.casefold()
                ),
                None,
            )
            if template is None:
                reason = "evaluation modelability: current-rail via template is absent"
            elif (
                template.pwr_reference_layer.casefold() != rail.pwr_layer.casefold()
                or template.gnd_reference_layer.casefold() != rail.gnd_layer.casefold()
            ):
                reason = "evaluation modelability: via template does not match the selected rail pair"
        if reason is not None:
            blockers.append(
                EvaluationConnectivityBlocker(
                    rail_id=canonical_by_key[decap.current_rail_id.casefold()],
                    refdes=decap.refdes,
                    kind=DecapConnectionKind.DIRECT,
                    reason=reason,
                )
            )
    return EvaluationConnectivityPreflight(
        canonical_rails,
        tuple(
            sorted(
                blockers,
                key=lambda item: (
                    rail_order[item.rail_id.casefold()],
                    item.kind.value,
                    item.reason.casefold(),
                    item.refdes.casefold(),
                ),
            )
        ),
    )


def _solver_project_fingerprint(project: ProjectSpec) -> str:
    """Hash numerical rail inputs while excluding provenance-only metadata."""

    payload = project.model_dump(mode="json")
    payload.pop("metadata", None)
    return sha256(_canonical_json(payload)).hexdigest()


def baseline_fallback_model_refdes(
    scenario: ScenarioSpec, rail_ids: Sequence[str]
) -> tuple[str, ...]:
    """Return original mounted parts whose current model would be frozen."""

    canonical = _canonical_rail_ids(scenario, rail_ids)
    uncaptured = {
        rail_id.casefold()
        for rail_id in canonical
        if not any(
            key.casefold() == rail_id.casefold()
            for key in scenario.baseline_captures
        )
    }
    connected = {
        refdes.casefold() for refdes in scenario.electrically_connected_refdes
    }
    return tuple(
        item.refdes
        for item in sorted(scenario.decaps, key=lambda entry: entry.refdes.casefold())
        if item.source_mounted
        and item.refdes.casefold() in connected
        and item.source_rail_id.casefold() in uncaptured
        and item.source_model_id is None
        and item.model_id is not None
    )


def _validate_evaluation_view(view: EvaluationView) -> None:
    if not isinstance(view.rail_id, str) or not view.rail_id.strip():
        raise ScenarioEvaluationCacheError(
            "cached evaluation rail id must be a nonblank string"
        )
    if not isinstance(view.solver_version, str) or not view.solver_version.strip():
        raise ScenarioEvaluationCacheError(
            "cached evaluation solver version must be a nonblank string"
        )
    arrays = {
        "magnitude_ohm": view.magnitude_ohm,
        "phase_deg": view.phase_deg,
        "target_curve_ohm": view.target_curve_ohm,
        "z_real_ohm": view.z_real_ohm,
        "z_imag_ohm": view.z_imag_ohm,
    }
    frequencies = view.frequency_hz
    if not isinstance(frequencies, list) or any(
        type(value) not in (int, float) for value in frequencies
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation frequencies must be a numeric JSON array"
        )
    for name, values in arrays.items():
        if not isinstance(values, list) or any(
            type(value) not in (int, float) for value in values
        ):
            raise ScenarioEvaluationCacheError(
                f"cached evaluation {name} must be a numeric JSON array"
            )
    if len(frequencies) < 2:
        raise ScenarioEvaluationCacheError(
            "cached evaluation requires at least two frequency samples"
        )
    if any(len(values) != len(frequencies) for values in arrays.values()):
        raise ScenarioEvaluationCacheError(
            "cached evaluation arrays do not share one frequency grid"
        )
    if any(
        not isfinite(float(value)) or float(value) <= 0.0 for value in frequencies
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation frequencies must be finite and positive"
        )
    if any(
        float(right) <= float(left)
        for left, right in zip(frequencies, frequencies[1:])
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation frequencies must be strictly increasing"
        )
    for name, values in arrays.items():
        if any(not isfinite(float(value)) for value in values):
            raise ScenarioEvaluationCacheError(
                f"cached evaluation {name} contains non-finite values"
            )
    if any(float(value) < 0.0 for value in view.magnitude_ohm):
        raise ScenarioEvaluationCacheError(
            "cached evaluation magnitude cannot be negative"
        )
    if any(float(value) <= 0.0 for value in view.target_curve_ohm):
        raise ScenarioEvaluationCacheError(
            "cached evaluation target impedance must be positive"
        )
    scalar_values = (
        view.target_ohm,
        view.max_violation_db,
        view.max_violation_frequency_hz,
        view.rms_violation_db,
        view.peak_magnitude_ohm,
        view.peak_frequency_hz,
        view.peak_prominence_db,
    )
    if any(type(value) not in (int, float) for value in scalar_values):
        raise ScenarioEvaluationCacheError(
            "cached evaluation metrics must be JSON numbers"
        )
    if any(not isfinite(float(value)) for value in scalar_values):
        raise ScenarioEvaluationCacheError(
            "cached evaluation metrics contain non-finite values"
        )
    if (
        float(view.target_ohm) <= 0.0
        or float(view.max_violation_frequency_hz) <= 0.0
        or float(view.peak_frequency_hz) <= 0.0
        or float(view.peak_magnitude_ohm) < 0.0
        or float(view.rms_violation_db) < 0.0
        or float(view.peak_prominence_db) < 0.0
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation metrics are outside their physical bounds"
        )
    if (
        type(view.cap_count) is not int
        or type(view.model_count) is not int
        or view.cap_count < 0
        or view.model_count < 0
        or view.model_count > view.cap_count
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation cap/model counts are invalid"
        )
    if not isinstance(view.confidence, str) or not isinstance(
        view.confidence_note, str
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation confidence fields must be strings"
        )
    if not isinstance(view.assumptions, list) or any(
        not isinstance(value, str) for value in view.assumptions
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation assumptions must be a string array"
        )
    if not isinstance(view.peaks, list) or any(
        not isinstance(value, dict) for value in view.peaks
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation peaks must be an object array"
        )
    if not isinstance(view.confidence_bands, list) or any(
        not isinstance(value, dict) for value in view.confidence_bands
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation confidence bands must be an object array"
        )
    if not isinstance(view.solver_diagnostics, dict) or (
        view.convergence is not None and not isinstance(view.convergence, dict)
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation diagnostics have an invalid structure"
        )


def _serialize_baseline_evaluation(
    evaluation: ScenarioEvaluation, capture_fingerprint: str
) -> bytes:
    return _canonical_json(
        {
            "format": EVALUATION_ATTACHMENT_FORMAT,
            "role": EvaluationRole.BASELINE.value,
            "baseline_capture_sha256": capture_fingerprint,
            "result_key": evaluation.result_key.model_dump(mode="json"),
            "view": evaluation.view.as_dict(),
        }
    )


def _decode_baseline_evaluation(
    content: bytes,
    *,
    expected_key: ScenarioResultKey,
    expected_capture_fingerprint: str,
    scenario_revision: int,
) -> ScenarioEvaluation:
    if len(content) > MAX_EVALUATION_ATTACHMENT_BYTES:
        raise ScenarioEvaluationCacheError("cached evaluation attachment is too large")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        raw = json.loads(
            content.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ScenarioEvaluationCacheError(
            "cached evaluation attachment is not strict JSON"
        ) from exc
    if not isinstance(raw, dict) or set(raw) != {
        "format",
        "role",
        "baseline_capture_sha256",
        "result_key",
        "view",
    }:
        raise ScenarioEvaluationCacheError(
            "cached evaluation attachment has an incompatible schema"
        )
    if raw["format"] != EVALUATION_ATTACHMENT_FORMAT:
        raise ScenarioEvaluationCacheError("cached evaluation format is unsupported")
    if raw["role"] != EvaluationRole.BASELINE.value:
        raise ScenarioEvaluationCacheError("cached evaluation has the wrong role")
    if raw["baseline_capture_sha256"] != expected_capture_fingerprint:
        raise ScenarioEvaluationCacheError(
            "cached evaluation belongs to a different baseline capture"
        )
    try:
        result_key = ScenarioResultKey.model_validate(raw["result_key"])
    except Exception as exc:
        raise ScenarioEvaluationCacheError(
            "cached evaluation result identity is invalid"
        ) from exc
    if result_key != expected_key:
        raise ScenarioEvaluationCacheError(
            "cached evaluation result identity is stale or incompatible"
        )
    view_payload = raw["view"]
    expected_view_fields = set(EvaluationView.__dataclass_fields__)
    if not isinstance(view_payload, dict) or set(view_payload) != expected_view_fields:
        raise ScenarioEvaluationCacheError(
            "cached evaluation view has an incompatible schema"
        )
    try:
        view = EvaluationView(**view_payload)
    except (TypeError, ValueError) as exc:
        raise ScenarioEvaluationCacheError("cached evaluation view is invalid") from exc
    _validate_evaluation_view(view)
    if view.rail_id.casefold() != expected_key.rail_id.casefold():
        raise ScenarioEvaluationCacheError("cached evaluation rail identity is wrong")
    if view.solver_version != expected_key.solver_version:
        raise ScenarioEvaluationCacheError("cached evaluation solver version is stale")
    return ScenarioEvaluation(
        state=None,
        view=view,
        result_key=result_key,
        scenario_revision=scenario_revision,
    )


def _load_baseline_evaluation(
    scenario: ScenarioSpec,
    attachments: Mapping[str, bytes],
    rail_id: str,
    *,
    target_ohm: float | None,
    modal_max_index: int,
) -> ScenarioEvaluation | None:
    capture = next(
        item
        for key, item in scenario.baseline_captures.items()
        if key.casefold() == rail_id.casefold()
    )
    expected_key = _expected_result_key(
        capture.evaluation_input_sha256,
        rail_id,
        target_ohm=target_ohm,
        modal_max_index=modal_max_index,
    )
    metadata = scenario.evaluation_cache.get(expected_key.cache_key)
    if metadata is None:
        return None
    if metadata.role != EvaluationRole.BASELINE:
        raise ScenarioEvaluationCacheError(
            f"stored result for {rail_id!r} has the wrong role"
        )
    if metadata.baseline_capture_sha256 != capture.capture_fingerprint:
        raise ScenarioEvaluationCacheError(
            f"stored baseline for {rail_id!r} belongs to another capture"
        )
    content = _attachment_content(
        attachments,
        metadata.attachment_name,
        missing_message=f"stored baseline attachment for {rail_id!r} is missing",
    )
    digest = sha256(content).hexdigest()
    if digest != metadata.attachment_sha256:
        raise ScenarioEvaluationCacheError(
            f"stored baseline attachment for {rail_id!r} failed SHA-256 validation"
        )
    return _decode_baseline_evaluation(
        content,
        expected_key=expected_key,
        expected_capture_fingerprint=capture.capture_fingerprint,
        scenario_revision=scenario.revision,
    )


def _cache_baseline_evaluation(
    scenario: ScenarioSpec,
    attachments: Mapping[str, bytes],
    rail_id: str,
    evaluation: ScenarioEvaluation,
) -> tuple[ScenarioSpec, dict[str, bytes]]:
    capture = next(
        item
        for key, item in scenario.baseline_captures.items()
        if key.casefold() == rail_id.casefold()
    )
    if evaluation.design_fingerprint != capture.evaluation_input_sha256:
        raise ScenarioEvaluationCacheError(
            "refusing to cache a result that does not match the original configuration"
        )
    content = _serialize_baseline_evaluation(
        evaluation, capture.capture_fingerprint
    )
    if len(content) > MAX_EVALUATION_ATTACHMENT_BYTES:
        raise ScenarioEvaluationCacheError(
            "generated baseline evaluation attachment is too large"
        )
    attachment_name = f"results/baseline-{evaluation.evaluation_fingerprint}.json"
    digest = sha256(content).hexdigest()
    updated_attachments = dict(attachments)
    existing = updated_attachments.get(attachment_name)
    if existing is not None and existing != content:
        raise ScenarioEvaluationCacheError(
            f"refusing to replace unrelated attachment {attachment_name!r}"
        )
    updated_attachments[attachment_name] = content
    cache = dict(scenario.evaluation_cache)
    metadata = CachedEvaluationMetadata(
        result_key=evaluation.result_key,
        attachment_name=attachment_name,
        attachment_sha256=digest,
        role=EvaluationRole.BASELINE,
        baseline_capture_sha256=capture.capture_fingerprint,
        summary={
            "configuration": "Original",
            "rail_id": evaluation.view.rail_id,
            "cap_count": evaluation.view.cap_count,
            "peak_magnitude_ohm": evaluation.view.peak_magnitude_ohm,
            "peak_frequency_hz": evaluation.view.peak_frequency_hz,
            "max_violation_db": evaluation.view.max_violation_db,
        },
    )
    cache[metadata.cache_key] = metadata
    hashes = dict(scenario.attachment_hashes)
    hashes[attachment_name] = digest
    updated_scenario = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "attachment_names": sorted(updated_attachments, key=str.casefold),
            "attachment_hashes": hashes,
            "evaluation_cache": cache,
        }
    )
    return updated_scenario, updated_attachments


def _validated_scenario_attachments(
    scenario: ScenarioSpec, attachments: Mapping[str, bytes] | None
) -> dict[str, bytes]:
    """Return canonical bytes only when they match the scenario declaration."""

    supplied = dict(attachments or {})
    supplied_by_key: dict[str, tuple[str, bytes]] = {}
    for raw_name, content in supplied.items():
        name = str(raw_name)
        key = name.casefold()
        if key in supplied_by_key:
            raise ScenarioEvaluationCacheError(
                f"duplicate attachment name {name!r}"
            )
        if not isinstance(content, bytes):
            raise ScenarioEvaluationCacheError(
                f"attachment {name!r} must be immutable bytes"
            )
        supplied_by_key[key] = (name, content)

    declared_hashes = {
        name.casefold(): digest
        for name, digest in scenario.attachment_hashes.items()
    }
    expected = {
        name.casefold(): (name, declared_hashes[name.casefold()])
        for name in scenario.attachment_names
    }
    missing = set(expected) - set(supplied_by_key)
    extra = set(supplied_by_key) - set(expected)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(
                "missing " + ", ".join(expected[key][0] for key in sorted(missing))
            )
        if extra:
            details.append(
                "unexpected "
                + ", ".join(supplied_by_key[key][0] for key in sorted(extra))
            )
        raise ScenarioEvaluationCacheError(
            "scenario attachment set mismatch: " + "; ".join(details)
        )

    canonical: dict[str, bytes] = {}
    for key, (expected_name, expected_hash) in expected.items():
        _supplied_name, content = supplied_by_key[key]
        if sha256(content).hexdigest() != expected_hash:
            raise ScenarioEvaluationCacheError(
                f"scenario attachment {expected_name!r} failed SHA-256 validation"
            )
        canonical[expected_name] = content
    return canonical


def _attachment_content(
    attachments: Mapping[str, bytes],
    attachment_name: str,
    *,
    missing_message: str | None = None,
) -> bytes:
    key = attachment_name.casefold()
    matches = [
        content for name, content in attachments.items() if name.casefold() == key
    ]
    if len(matches) != 1:
        raise ScenarioEvaluationCacheError(
            missing_message
            or f"evaluation attachment {attachment_name!r} is missing or ambiguous"
        )
    return matches[0]


def _lookup_casefold(items: list[Any], attribute: str) -> dict[str, Any]:
    return {str(getattr(item, attribute)).casefold(): item for item in items}


def _eligibility_for(decap: ScenarioDecap) -> RailEligibility:
    eligibility = decap.eligibility.get(decap.current_rail_id)
    if eligibility is None:
        folded = decap.current_rail_id.casefold()
        eligibility = next(
            (item for key, item in decap.eligibility.items() if key.casefold() == folded),
            None,
        )
    if eligibility is None:
        raise ScenarioEvaluationBuildError(
            "ELIGIBILITY_MISSING",
            f"current rail {decap.current_rail_id!r} has no assignment eligibility",
            refdes=decap.refdes,
        )
    if not eligibility.allowed:
        reason = eligibility.reason or "the PWR plane is not present below the pad"
        raise ScenarioEvaluationBuildError(
            "RAIL_INELIGIBLE",
            f"current rail {decap.current_rail_id!r} is ineligible: {reason}",
            refdes=decap.refdes,
        )
    return eligibility


def _validated_assignment(
    decap: ScenarioDecap,
    *,
    rail_by_id: Mapping[str, RailSpec],
    model_by_id: Mapping[str, CapModel],
    via_by_id: Mapping[str, ViaLoopTemplate],
) -> tuple[RailSpec, RailEligibility, CapModel | None, ViaLoopTemplate]:
    rail = rail_by_id.get(decap.current_rail_id.casefold())
    if rail is None:
        raise ScenarioEvaluationBuildError(
            "CURRENT_RAIL_UNKNOWN",
            f"current rail {decap.current_rail_id!r} is absent from the SPD project",
            refdes=decap.refdes,
        )
    if decap.current_net.casefold() != rail.net.casefold():
        raise ScenarioEvaluationBuildError(
            "CURRENT_NET_MISMATCH",
            f"current net {decap.current_net!r} does not match rail net {rail.net!r}",
            refdes=decap.refdes,
        )

    eligibility = _eligibility_for(decap)
    if eligibility.rail_id.casefold() != rail.rail_id.casefold():
        raise ScenarioEvaluationBuildError(
            "ELIGIBILITY_RAIL_MISMATCH",
            f"eligibility rail {eligibility.rail_id!r} does not match current rail "
            f"{rail.rail_id!r}",
            refdes=decap.refdes,
        )
    if eligibility.net.casefold() != rail.net.casefold():
        raise ScenarioEvaluationBuildError(
            "ELIGIBILITY_NET_MISMATCH",
            f"eligibility net {eligibility.net!r} does not match rail net {rail.net!r}",
            refdes=decap.refdes,
        )
    if (
        eligibility.pwr_layer.casefold() != rail.pwr_layer.casefold()
        or eligibility.gnd_layer.casefold() != rail.gnd_layer.casefold()
    ):
        raise ScenarioEvaluationBuildError(
            "ELIGIBILITY_LAYER_MISMATCH",
            "eligible PWR/DGND layers do not match the current rail plane pair",
            refdes=decap.refdes,
        )
    if not eligibility.via_template_id:
        raise ScenarioEvaluationBuildError(
            "VIA_TEMPLATE_MISSING",
            f"eligible rail {rail.rail_id!r} has no resolved via-loop template",
            refdes=decap.refdes,
        )
    via = via_by_id.get(eligibility.via_template_id.casefold())
    if via is None:
        raise ScenarioEvaluationBuildError(
            "VIA_TEMPLATE_UNKNOWN",
            f"via-loop template {eligibility.via_template_id!r} is absent from the project",
            refdes=decap.refdes,
        )
    if (
        via.pwr_reference_layer.casefold() != rail.pwr_layer.casefold()
        or via.gnd_reference_layer.casefold() != rail.gnd_layer.casefold()
    ):
        raise ScenarioEvaluationBuildError(
            "VIA_TEMPLATE_LAYER_MISMATCH",
            f"via-loop template {via.template_id!r} does not reference the rail plane pair",
            refdes=decap.refdes,
        )

    model: CapModel | None = None
    if decap.enabled and decap.model_id is not None:
        model = model_by_id.get(decap.model_id.casefold())
        if model is None:
            raise ScenarioEvaluationBuildError(
                "MODEL_UNKNOWN",
                f"Decap model {decap.model_id!r} is absent from the model library",
                refdes=decap.refdes,
            )
        if model.footprint.casefold() != decap.footprint.casefold():
            raise ScenarioEvaluationBuildError(
                "FOOTPRINT_MISMATCH",
                f"model footprint {model.footprint!r} does not match component footprint "
                f"{decap.footprint!r}",
                refdes=decap.refdes,
            )
    elif decap.enabled:
        raise ScenarioEvaluationBuildError(
            "MODEL_REQUIRED",
            "enabled Decap requires an assigned electrical model",
            refdes=decap.refdes,
        )
    return rail, eligibility, model, via


def _ground_net(project: ProjectSpec, rail: RailSpec) -> str:
    layer = next(
        (item for item in project.stackup_layers if item.name == rail.gnd_layer),
        None,
    )
    aliases = {item.casefold() for item in project.gnd_aliases}
    configured = (
        rail.mixed_reference_certificate.gnd_net
        if rail.mixed_reference_certificate is not None
        else None
    )
    matches = [
        item
        for item in (layer.pwr_nets if layer is not None else ())
        if item.casefold() in aliases
        and (configured is None or item.casefold() == configured.casefold())
    ]
    if len(matches) == 1:
        return matches[0]
    raise ScenarioEvaluationBuildError(
        "GROUND_NET_UNRESOLVED",
        f"rail {rail.rail_id!r} requires exactly one configured ground NET on {rail.gnd_layer!r}",
    )


def _source_terminal_estimate(
    segments: Sequence[Any], stackup_layers: Sequence[Any]
) -> tuple[float, float, tuple[Any, ...]]:
    """Return source-proven terminal R/L plus every segment classification."""
    resistance = 0.0
    inductance = 0.0
    models: list[Any] = []
    for segment in segments:
        try:
            model = estimate_via_segment_rl(
                length_um=segment.length_um,
                drill_diameter_um=segment.drill_diameter_um,
                padstack_material=getattr(segment, "padstack_material", None),
                start_layer=getattr(segment, "start_layer", None),
                end_layer=getattr(segment, "end_layer", None),
                stackup_layers=stackup_layers,
            )
        except ViaModelError as exc:
            raise ScenarioEvaluationBuildError(
                "SOURCE_VIA_PATH_INVALID",
                str(exc),
            ) from exc
        resistance += model.resistance_ohm
        inductance += model.inductance_h
        models.append(model)
    if not segments or not (isfinite(resistance) and isfinite(inductance)):
        raise ScenarioEvaluationBuildError(
            "SOURCE_VIA_PATH_INVALID",
            "source-proven Via path has no usable vertical segment data",
        )
    return max(resistance, 0.0), max(inductance, 0.0), tuple(models)


def _source_terminal_rl(
    segments: Sequence[Any], stackup_layers: Sequence[Any] = ()
) -> tuple[float, float]:
    """Compatibility view of the source-proven terminal R/L estimate."""

    resistance, inductance, _models = _source_terminal_estimate(segments, stackup_layers)
    return resistance, inductance


def _shared_pad_path_from_landing(
    *,
    path_id: str,
    terminal: TerminalKind,
    landing: Any,
    target_layer: str,
    via: ViaLoopTemplate,
    stackup_layers: Sequence[Any] = (),
) -> SharedPadViaPath:
    """Materialize compact source evidence or name the rail-template fallback."""

    evidence = landing.evidence_for_layer(target_layer)
    fields: dict[str, Any] = {
        "path_id": path_id,
        "terminal": terminal,
        "x_um": landing.x_um,
        "y_um": landing.y_um,
        "via_template_id": via.template_id,
        "source_via_id": landing.via_id,
        "terminal_provenance": "LEGACY_RAIL_TEMPLATE",
    }
    if evidence is None:
        return SharedPadViaPath(**fields)
    fields.update(
        {
            "x_um": evidence.x_um,
            "y_um": evidence.y_um,
            "landing_layer": evidence.target_layer,
            "landing_padstack": evidence.target_padstack,
            "landing_pad_width_um": evidence.target_pad_width_um,
            "landing_pad_height_um": evidence.target_pad_height_um,
        }
    )
    if evidence.trace_hops or evidence.trace_alternate_exit:
        # Connectivity is source-proven, but no trace RL was persisted.  Do not
        # combine a partial Via estimate with an unmodeled copper trace; use
        # the complete rail-template terminal model instead.
        fields["terminal_provenance"] = "SOURCE_PROVEN_TRACE_CONNECTIVITY_LEGACY_TEMPLATE"
        return SharedPadViaPath(**fields)
    if via.impedance:
        # A sampled template is calibrated as a differential PWR/GND loop.
        # Retaining it exactly preserves its established symmetric branch split
        # in SharedPadClusterModel rather than overwriting it with uncalibrated
        # terminal R/L estimates.
        fields["terminal_provenance"] = (
            "SAMPLED_DIFFERENTIAL_TEMPLATE_SYMMETRIC"
        )
    else:
        resistance, inductance, segment_models = _source_terminal_estimate(
            evidence.segments, stackup_layers
        )
        classifications = tuple(item.classification for item in segment_models)
        conductor_models = {item.conductor_model for item in classifications}
        fields.update(
            {
                "terminal_resistance_ohm": resistance,
                "terminal_inductance_h": inductance,
                "terminal_provenance": "SOURCE_PROVEN_SEGMENT_RL",
                "conductor_model": (
                    classifications[0].conductor_model
                    if len(conductor_models) == 1
                    else "MIXED_SOURCE_SEGMENT_CONDUCTOR_MODELS"
                ),
                "fill_provenance": "; ".join(
                    dict.fromkeys(item.fill_provenance for item in classifications)
                ),
                "classification_basis": "; ".join(
                    dict.fromkeys(item.classification_basis for item in classifications)
                ),
                "effective_area_m2": min(
                    item.effective_area_m2 for item in classifications
                ),
            }
        )
    return SharedPadViaPath(**fields)


def _confirmed_metadata(scenario: ScenarioSpec) -> dict[str, Any]:
    metadata = dict(scenario.base_project.metadata)
    metadata.update(
        {
            "plane_pair_confirmed": True,
            "geometry_user_reviewed": False,
            "geometry_confirmation_source": "validated_read_only_spd_scenario",
            "scenario_design_fingerprint": scenario.design_fingerprint,
        }
    )
    nested = metadata.get("spd_import")
    spd_import = dict(nested) if isinstance(nested, Mapping) else {}
    spd_import.update(
        {
            "source_name": scenario.source.name,
            "source_size_bytes": scenario.source.size_bytes,
            "source_sha256": scenario.source.sha256,
            "raw_spd_embedded": False,
            "plane_pair_confirmed": True,
            "geometry_user_reviewed": False,
            "geometry_confirmation_source": "validated_read_only_spd_scenario",
        }
    )
    metadata["spd_import"] = spd_import
    return metadata


def _scenario_assumptions(base: ProjectSpec) -> list[str]:
    additions = (
        "Decap rail edits do not modify SPD plane geometry.",
        "Nonrectangular SPD planes retain the existing rectangular solver approximation.",
        "The selected DGND plane is treated as continuous and inter-rail coupling is not modeled; distinct source top-GND components remain electrically distinct above that plane.",
        "Shared-pad topology retains every unique PWR and GND Via without nearest "
        "pairing. Because the calibrated template is differential, its loop "
        "impedance is split symmetrically (Zloop/2 per terminal); a 1-PWR/1-GND "
        "cluster reproduces the calibrated loop while unequal Via counts remain "
        "electrically distinct under the continuous-DGND approximation.",
        "When raw SPD proves one unique monotonic same-net vertical Via chain, its selected-plane pad geometry and per-terminal segment R/L are used. A source COPPER MLO microvia (drill <=150 um, two conductor layers, one dielectric, dielectric/drill <=1) uses its full circular copper area; all other or unclassified vias retain min(20 um, drill/4) barrel plating. L is a straight-segment estimate without mutual-Via, anti-pad, or spreading calibration.",
        "A missing, branching, overshooting, trace-required, or unsupported raw Via path explicitly falls back to the rail template. Sampled differential templates retain their calibrated symmetric terminal representation.",
    )
    result: list[str] = []
    seen: set[str] = set()
    for text in (*base.assumptions, *additions):
        key = text.casefold()
        if key not in seen:
            result.append(text)
            seen.add(key)
    return result


def build_evaluation_project(
    scenario: ScenarioSpec,
    *,
    evaluation_rail_id: str | None = None,
) -> ProjectSpec:
    """Build and validate a solver project for the current scenario state.

    When a rail is supplied, only decaps assigned to that rail are materialized.
    The inherited solver does not model inter-rail coupling, so an unmodeled
    mounted capacitor on an unrelated rail must not block the requested rail.
    """

    base = scenario.base_project
    rail_by_id = _lookup_casefold(base.rails, "rail_id")
    model_by_id = _lookup_casefold(base.cap_models, "model_id")
    via_by_id = _lookup_casefold(base.via_templates, "template_id")

    device_pins = [item for item in base.pins if item.kind == PinKind.DEVICE_BUMP]
    pins: list[PinRecord] = list(device_pins)
    topologies: list[TopologyMap] = []
    shared_pad_clusters: list[SharedPadClusterSpec] = []
    placements: list[PlacementAssignment] = []
    enabled_usage: Counter[str] = Counter()
    evaluation_rail_key = (
        evaluation_rail_id.casefold() if evaluation_rail_id is not None else None
    )
    analysis = scenario.connection_analysis
    _require_current_shared_pad_analysis(scenario)
    if analysis is None:
        raise ScenarioEvaluationBuildError(
            "SHARED_PAD_ANALYSIS_REQUIRED",
            "reopen the verified source SPD so shared-pad/via connectivity can be analyzed",
        )
    # ``build_evaluation_project`` is also a public boundary used by callers
    # that bypass UI batch preflight.  Keep the mixed-reference source-GND
    # witness fail-closed here, but only for rails this project will consume;
    # an unrelated source candidate with no certified DGND reachability must
    # remain loadable and must not block a supported selected rail.
    selected_mixed_rail_keys = (
        {evaluation_rail_key}
        if evaluation_rail_key is not None
        else {
            rail.rail_id.casefold()
            for rail in base.rails
            if rail.mixed_reference_certificate is not None
        }
    )
    mixed_witness_failures = mixed_reference_ground_witness_failures(scenario)
    for rail in base.rails:
        if rail.rail_id.casefold() not in selected_mixed_rail_keys:
            continue
        reason = mixed_witness_failures.get(rail.rail_id)
        if reason is not None:
            raise ScenarioEvaluationBuildError(
                "MIXED_REFERENCE_GND_REACHABILITY_REQUIRED",
                reason,
            )
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    connection_by_key = {
        item.refdes.casefold(): item for item in analysis.connections.values()
    }
    consumed: set[str] = set()

    def on_evaluation_rail(decap: ScenarioDecap) -> bool:
        return (
            evaluation_rail_key is None
            or decap.current_rail_id.casefold() == evaluation_rail_key
        )

    def model_for_member(decap: ScenarioDecap) -> CapModel | None:
        if not decap.enabled:
            return None
        if decap.model_id is None:
            raise ScenarioEvaluationBuildError(
                "MODEL_REQUIRED",
                "enabled Decap requires an assigned electrical model",
                refdes=decap.refdes,
            )
        model = model_by_id.get(decap.model_id.casefold())
        if model is None:
            raise ScenarioEvaluationBuildError(
                "MODEL_UNKNOWN",
                f"Decap model {decap.model_id!r} is absent from the model library",
                refdes=decap.refdes,
            )
        if model.footprint.casefold() != decap.footprint.casefold():
            raise ScenarioEvaluationBuildError(
                "FOOTPRINT_MISMATCH",
                f"model footprint {model.footprint!r} does not match component "
                f"footprint {decap.footprint!r}",
                refdes=decap.refdes,
            )
        return model

    def append_coupled_cluster(
        *,
        cluster_id: str,
        member_components: tuple[tuple[ScenarioDecap, ...], ...],
        power_landings_by_component: tuple[tuple[Any, ...], ...],
        ground_member_components: tuple[tuple[ScenarioDecap, ...], ...],
        ground_landings_by_component: tuple[tuple[Any, ...], ...],
        rail: RailSpec,
        via: ViaLoopTemplate,
    ) -> None:
        if (
            len(member_components) != len(power_landings_by_component)
            or len(ground_member_components) != len(ground_landings_by_component)
        ):
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_COMPONENT_MAPPING_INVALID",
                f"shared-pad cluster {cluster_id!r} has inconsistent component data",
            )
        members = tuple(
            member for component in member_components for member in component
        )
        ground_members = tuple(
            member for component in ground_member_components for member in component
        )
        if {item.refdes.casefold() for item in members} != {
            item.refdes.casefold() for item in ground_members
        }:
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_COMPONENT_MAPPING_INVALID",
                f"shared-pad cluster {cluster_id!r} PWR/GND components do not map "
                "the same member set",
            )
        path_count = sum(map(len, power_landings_by_component)) + sum(
            map(len, ground_landings_by_component)
        )
        if path_count > MAX_SHARED_PAD_VIA_PATHS:
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_CLUSTER_TOO_LARGE",
                f"shared-pad cluster {cluster_id!r} has {path_count:,} unique "
                f"PWR/GND via paths; supported limit is "
                f"{MAX_SHARED_PAD_VIA_PATHS:,}",
            )
        if (
            not member_components
            or any(not item for item in power_landings_by_component)
            or not ground_member_components
            or any(not item for item in ground_landings_by_component)
        ):
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_CLUSTER_TERMINAL_MISSING",
                f"shared-pad cluster {cluster_id!r} requires distinct PWR and GND "
                "via evidence",
            )
        domain_cluster_id = f"SPDPI:CLUSTER:{cluster_id}"
        member_slot_ids: list[str] = []
        for decap in members:
            slot_id = f"SPDPI:{decap.refdes}"
            member_slot_ids.append(slot_id)
            topologies.append(
                TopologyMap(
                    slot_id=slot_id,
                    x_um=decap.pwr_pad.x_um,
                    y_um=decap.pwr_pad.y_um,
                    allowed_rail_ids=[rail.rail_id],
                    allowed_footprints=[decap.footprint],
                    topology=TopologyKind.SHARED_PAD_CLUSTER,
                    zone="SPD",
                    cluster_id=domain_cluster_id,
                )
            )
            model = model_for_member(decap)
            if decap.enabled:
                assert model is not None
                placements.append(
                    PlacementAssignment(
                        slot_id=slot_id,
                        topology=TopologyKind.SHARED_PAD_CLUSTER,
                        rail_id=rail.rail_id,
                        cap_model_id=model.model_id,
                    )
                )
                enabled_usage[model.model_id] += 1
        power_paths: list[SharedPadViaPath] = []
        power_components: list[SharedPadPowerComponentSpec] = []
        ground_paths: list[SharedPadViaPath] = []
        ground_components: list[SharedPadGroundComponentSpec] = []
        capacitor_mappings: list[SharedPadCapacitorComponentSpec] = []
        power_component_by_member: dict[str, str] = {}
        ground_component_by_member: dict[str, str] = {}
        for component_index, (component_members, component_landings) in enumerate(
            zip(
                member_components,
                power_landings_by_component,
                strict=True,
            ),
            start=1,
        ):
            component_path_ids: list[str] = []
            for path_index, landing in enumerate(component_landings, start=1):
                path_id = (
                    f"{domain_cluster_id}:PWR:{component_index}:{path_index}"
                )
                component_path_ids.append(path_id)
                power_paths.append(
                    _shared_pad_path_from_landing(
                        path_id=path_id,
                        terminal=TerminalKind.PWR,
                        landing=landing,
                        target_layer=rail.pwr_layer,
                        via=via,
                        stackup_layers=base.stackup_layers,
                    )
                )
            power_components.append(
                SharedPadPowerComponentSpec(
                    component_id=f"{domain_cluster_id}:PWR:{component_index}",
                    member_slot_ids=[
                        f"SPDPI:{decap.refdes}" for decap in component_members
                    ],
                    power_path_ids=component_path_ids,
                )
            )
            component_id = power_components[-1].component_id
            for decap in component_members:
                power_component_by_member[decap.refdes.casefold()] = component_id
        for component_index, (component_members, component_landings) in enumerate(
            zip(
                ground_member_components,
                ground_landings_by_component,
                strict=True,
            ),
            start=1,
        ):
            component_path_ids: list[str] = []
            for path_index, landing in enumerate(component_landings, start=1):
                path_id = f"{domain_cluster_id}:GND:{component_index}:{path_index}"
                component_path_ids.append(path_id)
                ground_paths.append(
                    _shared_pad_path_from_landing(
                        path_id=path_id,
                        terminal=TerminalKind.GND,
                        landing=landing,
                        target_layer=rail.gnd_layer,
                        via=via,
                        stackup_layers=base.stackup_layers,
                    )
                )
            ground_components.append(
                SharedPadGroundComponentSpec(
                    component_id=f"{domain_cluster_id}:GND:{component_index}",
                    member_slot_ids=[
                        f"SPDPI:{decap.refdes}" for decap in component_members
                    ],
                    ground_path_ids=component_path_ids,
                )
            )
            component_id = ground_components[-1].component_id
            for decap in component_members:
                ground_component_by_member[decap.refdes.casefold()] = component_id
        for decap in members:
            member_key = decap.refdes.casefold()
            power_component_id = power_component_by_member.get(member_key)
            ground_component_id = ground_component_by_member.get(member_key)
            if power_component_id is None or ground_component_id is None:
                raise ScenarioEvaluationBuildError(
                    "SHARED_PAD_COMPONENT_MAPPING_INVALID",
                    f"shared-pad cluster {cluster_id!r} cannot map {decap.refdes!r} "
                    "to one PWR and one GND component",
                )
            capacitor_mappings.append(
                SharedPadCapacitorComponentSpec(
                    member_slot_id=f"SPDPI:{decap.refdes}",
                    power_component_id=power_component_id,
                    ground_component_id=ground_component_id,
                )
            )
        shared_pad_clusters.append(
            SharedPadClusterSpec(
                cluster_id=domain_cluster_id,
                rail_id=rail.rail_id,
                member_slot_ids=member_slot_ids,
                via_paths=power_paths + ground_paths,
                power_components=power_components,
                ground_components=ground_components,
                capacitor_component_mappings=capacitor_mappings,
            )
        )

    for cluster in analysis.clusters:
        member_keys = tuple(item.casefold() for item in cluster.member_refdes)
        members = tuple(decap_by_key[key] for key in member_keys)
        consumed.update(member_keys)
        if cluster.state == SharedPadClusterState.FLOATING:
            continue
        if cluster.state != SharedPadClusterState.ANCHORED:
            if any(on_evaluation_rail(item) for item in members):
                raise ScenarioEvaluationBuildError(
                    "SHARED_PAD_CLUSTER_UNRESOLVED",
                    cluster.reason
                    or f"shared-pad cluster {cluster.cluster_id!r} is unresolved",
                )
            continue
        derivation = derive_shared_pad_current_components(
            cluster,
            {key: decap_by_key[key] for key in member_keys},
            {key: connection_by_key[key] for key in member_keys},
            analysis_version=analysis.version,
        )
        if derivation.shared_power_via_conflicts:
            conflict = derivation.shared_power_via_conflicts[0]
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_POWER_VIA_CONFLICT",
                f"physical PWR Via {conflict.via_id!r} spans more than one "
                f"post-edit PWR component in cluster {cluster.cluster_id!r}",
            )
        if derivation.shared_ground_via_conflicts:
            conflict = derivation.shared_ground_via_conflicts[0]
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_GROUND_VIA_CONFLICT",
                f"physical GND Via {conflict.via_id!r} spans more than one "
                f"post-edit GND component in cluster {cluster.cluster_id!r}",
            )
        component_rail_keys = {
            item.current_rail_id.casefold() for item in derivation.components
        }
        if evaluation_rail_key is None and len(component_rail_keys) > 1:
            raise ScenarioEvaluationBuildError(
                "EVALUATION_RAIL_REQUIRED_FOR_SPLIT_CLUSTER",
                f"shared-pad cluster {cluster.cluster_id!r} spans multiple current "
                "rails; build one explicit rail evaluation to avoid duplicating "
                "its common GND Via paths",
            )
        selected_components = tuple(
            item
            for item in derivation.components
            if evaluation_rail_key is None
            or item.current_rail_id.casefold() == evaluation_rail_key
        )
        if not selected_components:
            continue
        rail = rail_by_id.get(selected_components[0].current_rail_id.casefold())
        if rail is None:
            raise ScenarioEvaluationBuildError(
                "RAIL_UNKNOWN",
                f"current rail {selected_components[0].current_rail_id!r} is absent "
                "from the project",
            )
        component_eligibility: list[RailEligibility] = []
        for component in selected_components:
            eligibility = next(
                (
                    item
                    for item in shared_pad_component_eligibility(
                        cluster, component
                    ).values()
                    if item.rail_id.casefold() == rail.rail_id.casefold()
                    and item.allowed
                ),
                None,
            )
            if eligibility is None:
                raise ScenarioEvaluationBuildError(
                    "RAIL_INELIGIBLE",
                    f"shared-pad PWR component {component.member_refdes!r} is not "
                    f"physically eligible for rail {rail.rail_id!r}",
                )
            component_eligibility.append(eligibility)
        template_keys = {
            (item.via_template_id or "").casefold()
            for item in component_eligibility
        }
        if len(template_keys) != 1:
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_COMPONENT_TEMPLATE_CONFLICT",
                f"shared-pad cluster {cluster.cluster_id!r} requires inconsistent "
                f"via-loop templates on rail {rail.rail_id!r}",
            )
        via = via_by_id.get(next(iter(template_keys)))
        if via is None:
            raise ScenarioEvaluationBuildError(
                "VIA_TEMPLATE_REQUIRED",
                f"shared-pad cluster {cluster.cluster_id!r} has no calibrated "
                "via-loop template",
            )
        if (
            via.pwr_reference_layer.casefold() != rail.pwr_layer.casefold()
            or via.gnd_reference_layer.casefold() != rail.gnd_layer.casefold()
        ):
            raise ScenarioEvaluationBuildError(
                "VIA_TEMPLATE_LAYER_MISMATCH",
                f"via-loop template {via.template_id!r} does not reference the rail plane pair",
            )
        selected_member_keys = {
            refdes.casefold()
            for component in selected_components
            for refdes in component.member_refdes
        }
        selected_ground_components = tuple(
            component
            for component in derivation.ground_components
            if selected_member_keys.intersection(
                refdes.casefold() for refdes in component.member_refdes
            )
        )
        if not selected_ground_components:
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_GROUND_COMPONENT_MISSING",
                f"shared-pad cluster {cluster.cluster_id!r} has no GND component "
                "for the selected rail members",
            )
        append_coupled_cluster(
            cluster_id=cluster.cluster_id,
            member_components=tuple(
                tuple(
                    decap_by_key[refdes.casefold()]
                    for refdes in component.member_refdes
                )
                for component in selected_components
            ),
            power_landings_by_component=tuple(
                component.power_vias for component in selected_components
            ),
            ground_member_components=tuple(
                tuple(
                    decap_by_key[refdes.casefold()]
                    for refdes in component.member_refdes
                    if refdes.casefold() in selected_member_keys
                )
                for component in selected_ground_components
            ),
            ground_landings_by_component=tuple(
                component.ground_vias for component in selected_ground_components
            ),
            rail=rail,
            via=via,
        )

    for decap in sorted(scenario.decaps, key=lambda item: item.refdes.casefold()):
        key = decap.refdes.casefold()
        if key in consumed:
            continue
        connection = connection_by_key[key]
        if connection.kind == DecapConnectionKind.FLOATING_DUMMY:
            continue
        if connection.kind in {
            DecapConnectionKind.UNRESOLVED,
            DecapConnectionKind.OUT_OF_SCOPE,
        }:
            if on_evaluation_rail(decap):
                raise ScenarioEvaluationBuildError(
                    "DECAP_CONNECTION_UNRESOLVED",
                    connection.reason or "Decap pad/via connectivity is unresolved",
                    refdes=decap.refdes,
                )
            continue
        if connection.kind != DecapConnectionKind.DIRECT:
            raise ScenarioEvaluationBuildError(
                "CONNECTION_CLASSIFICATION_INVALID",
                f"unexpected unclustered connection kind {connection.kind.value!r}",
                refdes=decap.refdes,
            )
        if not on_evaluation_rail(decap):
            continue
        unique_power = {
            landing.via_id.casefold(): landing for landing in connection.power_vias
        }
        unique_ground = {
            landing.via_id.casefold(): landing for landing in connection.ground_vias
        }
        if (
            not decap.enabled
            and len(unique_power) == 1
            and len(unique_ground) == 1
        ):
            continue
        rail, _eligibility, model, via = _validated_assignment(
            decap,
            rail_by_id=rail_by_id,
            model_by_id=model_by_id,
            via_by_id=via_by_id,
        )
        has_recovered_terminal_path = any(
            landing.evidence_for_layer(rail.pwr_layer) is not None
            for landing in unique_power.values()
        ) or any(
            landing.evidence_for_layer(rail.gnd_layer) is not None
            for landing in unique_ground.values()
        )
        if (
            len(unique_power) > 1
            or len(unique_ground) > 1
            or has_recovered_terminal_path
        ):
            append_coupled_cluster(
                cluster_id=f"DIRECT:{decap.refdes}",
                member_components=((decap,),),
                power_landings_by_component=(
                    tuple(unique_power[key] for key in sorted(unique_power)),
                ),
                ground_member_components=((decap,),),
                ground_landings_by_component=(
                    tuple(unique_ground[key] for key in sorted(unique_ground)),
                ),
                rail=rail,
                via=via,
            )
            continue
        # One differential via-loop and no populated capacitor has no retained
        # plane stamp.  Multi-via unpopulated pads were handled above because
        # their shared PWR copper can still couple spatial plane ports.
        landing = next(iter(unique_power.values()))
        ground_net = _ground_net(base, rail)
        pins.extend(
            (
                PinRecord(
                    refdes=decap.refdes,
                    pin="PWR",
                    net=rail.net,
                    x_um=landing.x_um,
                    y_um=landing.y_um,
                    kind=PinKind.DECAP_PAD,
                    terminal=TerminalKind.PWR,
                    domain=rail.domain,
                    site=rail.site,
                    via_template_id=via.template_id,
                ),
                PinRecord(
                    refdes=decap.refdes,
                    pin="GND",
                    net=ground_net,
                    x_um=decap.gnd_pad.x_um,
                    y_um=decap.gnd_pad.y_um,
                    kind=PinKind.DECAP_PAD,
                    terminal=TerminalKind.GND,
                    domain=rail.domain,
                    site=rail.site,
                    via_template_id=via.template_id,
                ),
            )
        )
        slot_id = f"SPDPI:{decap.refdes}"
        topologies.append(
            TopologyMap(
                slot_id=slot_id,
                x_um=landing.x_um,
                y_um=landing.y_um,
                allowed_rail_ids=[rail.rail_id],
                allowed_footprints=[decap.footprint],
                topology=TopologyKind.DIRECT,
                zone="SPD",
                via_template_id=via.template_id,
            )
        )
        assert model is not None
        placements.append(
            PlacementAssignment(
                slot_id=slot_id,
                topology=TopologyKind.DIRECT,
                rail_id=rail.rail_id,
                cap_model_id=model.model_id,
            )
        )
        enabled_usage[model.model_id] += 1

    cap_models = [
        item.model_copy(
            update={"inventory": max(item.inventory, enabled_usage[item.model_id])}
        )
        for item in base.cap_models
    ]
    partitions = [item.model_copy(update={"confirmed": True}) for item in base.partitions]
    payload = base.model_dump(mode="json")
    payload.update(
        {
            "pins": [item.model_dump(mode="json") for item in pins],
            "cap_models": [item.model_dump(mode="json") for item in cap_models],
            "topology_maps": [item.model_dump(mode="json") for item in topologies],
            "shared_pad_clusters": [
                item.model_dump(mode="json") for item in shared_pad_clusters
            ],
            "placements": [item.model_dump(mode="json") for item in placements],
            "partitions": [item.model_dump(mode="json") for item in partitions],
            "assumptions": _scenario_assumptions(base),
            "metadata": _confirmed_metadata(scenario),
        }
    )
    try:
        return ProjectSpec.model_validate(payload)
    except ValueError as exc:
        raise ScenarioEvaluationBuildError(
            "PROJECT_VALIDATION_FAILED",
            f"transient evaluation project is invalid: {exc}",
        ) from exc


def build_evaluation_workspace(
    scenario: ScenarioSpec,
    *,
    attachments: Mapping[str, bytes] | None = None,
    evaluation_rail_id: str | None = None,
) -> WorkspaceState:
    """Return an isolated workspace for evaluation; source scenario stays immutable."""

    return WorkspaceState(
        project=build_evaluation_project(
            scenario, evaluation_rail_id=evaluation_rail_id
        ),
        attachments=dict(attachments or {}),
    )


def evaluate_scenario(
    scenario: ScenarioSpec,
    rail_id: str,
    target_ohm: float | None = None,
    modal_max_index: int = DEFAULT_EVALUATION_MODAL_MAX_INDEX,
    *,
    attachments: Mapping[str, bytes] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> ScenarioEvaluation:
    """Evaluate one rail and bind the view to deterministic scenario identity."""

    canonical_rail = next(
        (
            item.rail_id
            for item in scenario.base_project.rails
            if item.rail_id.casefold() == rail_id.casefold()
        ),
        None,
    )
    if canonical_rail is None:
        raise ScenarioEvaluationBuildError(
            "EVALUATION_RAIL_UNKNOWN",
            f"evaluation rail {rail_id!r} is absent from the SPD project",
        )
    state = build_evaluation_workspace(
        scenario,
        attachments=attachments,
        evaluation_rail_id=canonical_rail,
    )
    with scoped_blas_threads():
        view = evaluation_services.evaluate_workspace(
            state,
            canonical_rail,
            target_ohm,
            modal_max_index,
            progress=progress,
            is_cancelled=is_cancelled,
        )
    result_key = ScenarioResultKey.from_settings(
        design_fingerprint=scenario.design_fingerprint,
        rail_id=canonical_rail,
        settings=_evaluation_settings(target_ohm, modal_max_index),
        solver_version=view.solver_version,
    )
    return ScenarioEvaluation(
        state=state,
        view=view,
        result_key=result_key,
        scenario_revision=scenario.revision,
    )


def evaluate_comparison_batch(
    scenario: ScenarioSpec,
    rail_ids: Sequence[str],
    target_ohm: float | None = None,
    modal_max_index: int = DEFAULT_EVALUATION_MODAL_MAX_INDEX,
    *,
    attachments: Mapping[str, bytes] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> ScenarioEvaluationBatch:
    """Evaluate Original and Tuned configurations for selected PWR rails.

    Every requested baseline/current project is preflighted before the first
    solver call.  Results and new baseline attachments are returned atomically;
    the caller commits nothing when this function raises or is cancelled.
    """

    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    canonical_rails = _canonical_rail_ids(scenario, rail_ids)
    connectivity = preflight_evaluation_connectivity(scenario, canonical_rails)
    if not connectivity.is_clear:
        raise ScenarioEvaluationPreflightError(connectivity)
    prepared = scenario.with_baseline_captures(canonical_rails)
    working_attachments = _validated_scenario_attachments(prepared, attachments)
    baseline_scenarios = {
        rail_id: prepared.original_configuration(rail_id)
        for rail_id in canonical_rails
    }

    report(1, "Preflighting Original and Tuned rail projects")
    baseline_project_fingerprints: dict[str, str] = {}
    tuned_project_fingerprints: dict[str, str] = {}
    for rail_id in canonical_rails:
        if cancelled():
            raise RuntimeError("evaluation cancelled")
        baseline_project = build_evaluation_project(
            baseline_scenarios[rail_id], evaluation_rail_id=rail_id
        )
        tuned_project = build_evaluation_project(prepared, evaluation_rail_id=rail_id)
        baseline_project_fingerprints[rail_id] = _solver_project_fingerprint(
            baseline_project
        )
        tuned_project_fingerprints[rail_id] = _solver_project_fingerprint(
            tuned_project
        )

    total_stages = max(len(canonical_rails) * 2, 1)
    completed_stages = 0
    comparisons: list[RailComparison] = []
    persistent_change = prepared.baseline_captures != scenario.baseline_captures

    def stage_progress(stage_label: str) -> ProgressCallback:
        stage_start = completed_stages

        def update(value: int, message: str) -> None:
            bounded = min(max(int(value), 0), 100)
            overall = round((stage_start + bounded / 100.0) * 100 / total_stages)
            report(overall, f"{stage_label}: {message}")

        return update

    for rail_id in canonical_rails:
        if cancelled():
            raise RuntimeError("evaluation cancelled")
        baseline_scenario = baseline_scenarios[rail_id]
        capture = next(
            item
            for key, item in prepared.baseline_captures.items()
            if key.casefold() == rail_id.casefold()
        )
        configuration_unchanged = (
            baseline_project_fingerprints[rail_id]
            == tuned_project_fingerprints[rail_id]
        )
        baseline = _load_baseline_evaluation(
            prepared,
            working_attachments,
            rail_id,
            target_ohm=target_ohm,
            modal_max_index=modal_max_index,
        )
        baseline_from_cache = baseline is not None
        if baseline is None:
            evaluated_baseline = evaluate_scenario(
                baseline_scenario,
                rail_id,
                target_ohm=target_ohm,
                modal_max_index=modal_max_index,
                attachments=working_attachments,
                progress=stage_progress(f"{rail_id} Original"),
                is_cancelled=cancelled,
            )
            baseline = replace(
                evaluated_baseline,
                result_key=_expected_result_key(
                    capture.evaluation_input_sha256,
                    rail_id,
                    target_ohm=target_ohm,
                    modal_max_index=modal_max_index,
                ),
            )
            prepared, working_attachments = _cache_baseline_evaluation(
                prepared, working_attachments, rail_id, baseline
            )
            persistent_change = True
        else:
            report(
                round((completed_stages + 1) * 100 / total_stages),
                f"{rail_id} Original: using saved baseline",
            )
        completed_stages += 1

        if cancelled():
            raise RuntimeError("evaluation cancelled")
        if configuration_unchanged:
            tuned = replace(
                baseline,
                result_key=_expected_result_key(
                    prepared.design_fingerprint,
                    rail_id,
                    target_ohm=target_ohm,
                    modal_max_index=modal_max_index,
                ),
                scenario_revision=prepared.revision,
            )
            report(
                round((completed_stages + 1) * 100 / total_stages),
                f"{rail_id} Tuned: configuration is unchanged",
            )
        else:
            tuned = evaluate_scenario(
                prepared,
                rail_id,
                target_ohm=target_ohm,
                modal_max_index=modal_max_index,
                attachments=working_attachments,
                progress=stage_progress(f"{rail_id} Tuned"),
                is_cancelled=cancelled,
            )
        completed_stages += 1
        comparisons.append(
            RailComparison(
                rail_id=rail_id,
                baseline=baseline.compact(),
                tuned=tuned.compact(),
                baseline_from_cache=baseline_from_cache,
                configuration_unchanged=configuration_unchanged,
            )
        )

    updated_scenario = prepared
    if persistent_change:
        updated_scenario = ScenarioSpec.model_validate(
            {
                **prepared.model_dump(mode="python"),
                "revision": scenario.revision + 1,
            }
        )
    report(100, f"Completed {len(comparisons)} PWR rail comparison(s)")
    return ScenarioEvaluationBatch(
        comparisons=tuple(comparisons),
        updated_scenario=updated_scenario,
        updated_attachments=working_attachments,
        requested_design_fingerprint=scenario.design_fingerprint,
        requested_revision=scenario.revision,
    )


def rehydrate_scenario_evaluation(
    scenario: ScenarioSpec,
    evaluation: ScenarioEvaluation,
    *,
    attachments: Mapping[str, bytes] | None = None,
) -> ScenarioEvaluation:
    """Rebuild one transient solver state for AI analysis of a compact result."""

    if not evaluation.matches(scenario):
        raise ScenarioEvaluationBuildError(
            "EVALUATION_STALE", "evaluation no longer matches the tuned scenario"
        )
    state = build_evaluation_workspace(
        scenario,
        attachments=attachments,
        evaluation_rail_id=evaluation.view.rail_id,
    )
    state.last_evaluation = evaluation.view
    return replace(evaluation, state=state)


def analyze_scenario_with_local_llm(
    evaluation: ScenarioEvaluation,
    endpoint: str,
    model: str,
    allow_remote: bool = False,
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> LocalAIAnalysisView:
    """Analyze solver evidence in fixed Plot Analyst mode.

    No caller-controlled mode is accepted, so this scenario application cannot
    invoke Search Controller or any optimizer-mutating AI path.
    """

    if evaluation.state is None:
        raise ScenarioEvaluationBuildError(
            "EVALUATION_STATE_MISSING",
            "rehydrate the selected tuned result before AI analysis",
        )
    return evaluation_services.analyze_with_local_llm(
        evaluation.state,
        endpoint,
        model,
        mode=PLOT_ANALYST_MODE,
        allow_remote=allow_remote,
        progress=progress,
        is_cancelled=is_cancelled,
    )


__all__ = [
    "EVALUATION_ATTACHMENT_FORMAT",
    "PLOT_ANALYST_MODE",
    "EvaluationConnectivityBlocker",
    "EvaluationConnectivityPreflight",
    "RailComparison",
    "ScenarioEvaluation",
    "ScenarioEvaluationBatch",
    "ScenarioEvaluationBuildError",
    "ScenarioEvaluationCacheError",
    "ScenarioEvaluationPreflightError",
    "analyze_scenario_with_local_llm",
    "baseline_fallback_model_refdes",
    "build_evaluation_project",
    "build_evaluation_workspace",
    "evaluate_comparison_batch",
    "evaluate_scenario",
    "preflight_evaluation_connectivity",
    "rehydrate_scenario_evaluation",
]
