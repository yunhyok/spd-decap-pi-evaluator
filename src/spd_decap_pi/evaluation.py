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
import re
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
    evaluation_modal_convergence_ceiling,
    scoped_blas_threads,
)
from spd_decap_pi._core.solver import (
    CONVERGENCE_POLICY_VERSION,
    DEFAULT_CURVATURE_THRESHOLD_DB,
    DEFAULT_MAX_NEW_FREQUENCY_POINTS,
    DEFAULT_MAX_REFINEMENT_ITERATIONS,
    DEFAULT_MAX_TOLERANCE_DB,
    DEFAULT_PEAK_SHIFT_TOLERANCE_PERCENT,
    DEFAULT_RMS_TOLERANCE_DB,
    SOLVER_VERSION,
    compile_project_evaluation_template,
)
from spd_decap_pi._core.solver.profiles import (
    DEFAULT_SOLVER_PROFILE_KEY,
    LAYERWISE_ADMITTANCE_PROFILE,
    LEGACY_MODAL_PROFILE,
    solver_profile as resolve_solver_profile,
    solver_profile_static_identity_sha256,
)
from spd_decap_pi._core.solver.layerwise_network import (
    LAYERWISE_COMPILER_VERSION,
)
from spd_decap_pi._core.via_model import ViaModelError, estimate_via_segment_rl
from spd_decap_pi._core.io.spd import SpdPlaneGeometry
from .eligibility import IndexedPlaneGeometry

from .scenario import (
    CachedEvaluationMetadata,
    DecapConnectionKind,
    DecapPadState,
    EvaluationRole,
    RailEligibility,
    SHARED_PAD_ANALYSIS_VERSION,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioResultKey,
    ScenarioSpec,
    SharedPadClusterState,
    derive_shared_pad_current_components,
    mixed_reference_ground_witness_failures,
    shared_pad_component_eligibility,
)

EVALUATION_POLICY_STRICT = "STRICT_EXACT"
EVALUATION_POLICY_EMBEDDED_ALTERNATE = "EMBEDDED_ALTERNATE_PAIR_APPROXIMATION_V1"
_EVALUATION_POLICIES = frozenset({EVALUATION_POLICY_STRICT, EVALUATION_POLICY_EMBEDDED_ALTERNATE})


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
PLOT_ANALYST_MODE = "Plot Analyst"
EVALUATION_ATTACHMENT_FORMAT = "spd-decap-evaluation-0.1"
MAX_EVALUATION_ATTACHMENT_BYTES = 16 * 1024 * 1024
# General shared-pad networks materialize one dense F x N x N terminal
# admittance before the modal stamp.  Keep that established resource bound.
MAX_SHARED_PAD_VIA_PATHS = 128
# A one-PWR/one-GND-component cluster whose every terminal uses the same rail
# template is handled by the solver's exact aggregate arrowhead stamp.  That
# path never materializes N x N terminal matrices during ordinary Evaluation,
# so permit the largest source-proven production cluster with bounded headroom.
MAX_BATCHED_SHARED_PAD_VIA_PATHS = 512

_LAYERWISE_PROVENANCE_HASH_FIELDS = (
    "static_compiler_algorithm_sha256",
    "source_sha256",
    "geometry_manifest_sha256",
    "material_manifest_sha256",
    "substrate_identity_sha256",
    "ground_alias_manifest_sha256",
    "via_group_evidence_sha256",
    "surface_connectivity_evidence_sha256",
    "terminal_surface_contact_proof_sha256",
    "base_layerwise_evidence_sha256",
    "termination_manifest_sha256",
    "layerwise_identity_sha256",
)


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
    configuration: EvaluationRole | None = None
    terminal: TerminalKind | None = None
    path_id: str | None = None
    source_via_id: str | None = None
    center_x_um: float | None = None
    center_y_um: float | None = None
    width_um: float | None = None
    height_um: float | None = None
    plane_x_min_um: float | None = None
    plane_x_max_um: float | None = None
    plane_y_min_um: float | None = None
    plane_y_max_um: float | None = None
    overrun_left_um: float = 0.0
    overrun_right_um: float = 0.0
    overrun_bottom_um: float = 0.0
    overrun_top_um: float = 0.0


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
        grouped: dict[
            tuple[str, EvaluationRole | None, DecapConnectionKind, str], list[str]
        ] = {}
        for blocker in self.blockers:
            grouped.setdefault(
                (
                    blocker.rail_id,
                    blocker.configuration,
                    blocker.kind,
                    blocker.reason,
                ),
                [],
            ).append(blocker.refdes)
        rails: dict[str, list[str]] = {rail_id: [] for rail_id in self.rail_ids}
        for (rail_id, configuration, kind, reason), refdes in grouped.items():
            ordered = sorted(refdes, key=str.casefold)
            examples = ", ".join(ordered[:max_refdes_per_group])
            if len(ordered) > max_refdes_per_group:
                examples += f", +{len(ordered) - max_refdes_per_group:,} more"
            configuration_label = (
                "Original "
                if configuration == EvaluationRole.BASELINE
                else "Tuned "
                if configuration == EvaluationRole.TUNED
                else ""
            )
            rails[rail_id].append(
                f"{configuration_label}{kind.value}: {examples} ({reason})"
            )
        details = "; ".join(
            f"{rail_id} [{'; '.join(rails[rail_id])}]"
            for rail_id in self.rail_ids
            if rails[rail_id]
        )
        return (
            f"Evaluation is blocked by {len(self.blockers):,} decap "
            f"connectivity/modelability blocker(s) on "
            f"{sum(bool(items) for items in rails.values()):,} "
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
                or baseline.view.solver_profile_key
                != tuned.view.solver_profile_key
                or baseline.view.target_ohm != tuned.view.target_ohm
            ):
                raise ScenarioEvaluationCacheError(
                    f"Original/Tuned settings disagree for {comparison.rail_id!r}"
                )
            profile = resolve_solver_profile(baseline.view.solver_profile_key)
            if profile.experimental:
                # Research baselines deliberately have no persisted cache
                # attachment: evidence identity exists only after compilation.
                # Both curves must nevertheless bind the identical evidence.
                baseline_identity = baseline.view.solver_provenance.get(
                    "research_identity_sha256"
                )
                tuned_identity = tuned.view.solver_provenance.get(
                    "research_identity_sha256"
                )
                if (
                    not isinstance(baseline_identity, str)
                    or baseline_identity != tuned_identity
                    or baseline.view.solver_provenance
                    != tuned.view.solver_provenance
                ):
                    raise ScenarioEvaluationCacheError(
                        f"Original/Tuned research evidence identity disagrees for {comparison.rail_id!r}"
                    )
                if comparison.configuration_unchanged and baseline.view != tuned.view:
                    raise ScenarioEvaluationCacheError(
                        f"unchanged comparison for {comparison.rail_id!r} has different results"
                    )
                continue
            if profile == LAYERWISE_ADMITTANCE_PROFILE:
                # The layer-surface solve includes mounted terminations from
                # every other rail.  Its Original identity is therefore the
                # complete board-wide source configuration, not the legacy
                # selected-rail BaselineCapture fingerprint.  Layerwise
                # results are deliberately not persisted because the v0.1
                # attachment schema cannot encode that all-rail dependency.
                original = _comparison_original_configuration(
                    self.updated_scenario
                )
                if (
                    comparison.baseline_from_cache
                    or baseline.design_fingerprint
                    != original.design_fingerprint
                ):
                    raise ScenarioEvaluationCacheError(
                        f"Original layerwise result for {comparison.rail_id!r} "
                        "does not match the board-wide source configuration"
                    )
                for label, evaluation in (
                    ("Original", baseline),
                    ("Tuned", tuned),
                ):
                    manifest_sha = evaluation.view.solver_provenance.get(
                        "termination_manifest_sha256"
                    )
                    if (
                        not isinstance(manifest_sha, str)
                        or len(manifest_sha) != 64
                        or any(
                            character not in "0123456789abcdef"
                            for character in manifest_sha.casefold()
                        )
                    ):
                        raise ScenarioEvaluationCacheError(
                            f"{label} layerwise result for "
                            f"{comparison.rail_id!r} has no mounted-state identity"
                        )
                if (
                    comparison.configuration_unchanged
                    and baseline.view != tuned.view
                ):
                    raise ScenarioEvaluationCacheError(
                        f"unchanged comparison for {comparison.rail_id!r} "
                        "has different results"
                    )
                continue
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
    target_ohm: float | None,
    modal_max_index: int,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    *,
    solver_provenance: Mapping[str, Any] | None = None,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
) -> dict[str, Any]:
    """Return cache/result settings without ever guessing research evidence.

    Legacy settings deliberately retain their v0.17 spelling.  An experimental
    solver gets a complete source/compiler/material/component/geometry identity
    only after its fail-closed evidence gate succeeds; without that identity it
    is explicitly marked non-cacheable rather than accidentally colliding with
    a prior experimental calculation.
    """

    profile = resolve_solver_profile(solver_profile)
    if evaluation_policy not in _EVALUATION_POLICIES:
        raise ScenarioEvaluationBuildError("EVALUATION_POLICY_UNKNOWN", f"unknown Evaluation geometry policy {evaluation_policy!r}")
    settings: dict[str, Any] = {
        "target_ohm": target_ohm,
        "modal_max_index": modal_max_index,
        "solver_profile": profile.key,
        "evaluation_policy": evaluation_policy,
        "modal_convergence_ceiling_index": evaluation_modal_convergence_ceiling(
            modal_max_index, profile.key
        ),
        "convergence_policy": {
            "version": CONVERGENCE_POLICY_VERSION,
            "max_refinement_iterations": DEFAULT_MAX_REFINEMENT_ITERATIONS,
            "max_new_frequency_points_per_iteration": (
                DEFAULT_MAX_NEW_FREQUENCY_POINTS
            ),
            "curvature_threshold_db": DEFAULT_CURVATURE_THRESHOLD_DB,
            "rms_tolerance_db": DEFAULT_RMS_TOLERANCE_DB,
            "max_tolerance_db": DEFAULT_MAX_TOLERANCE_DB,
            "peak_shift_tolerance_percent": (
                DEFAULT_PEAK_SHIFT_TOLERANCE_PERCENT
            ),
        },
    }
    if profile == LAYERWISE_ADMITTANCE_PROFILE:
        settings["solver_static_identity_sha256"] = (
            solver_profile_static_identity_sha256(profile)
        )
        return settings
    if not profile.experimental:
        return settings
    if solver_provenance is None:
        settings["experimental_cache_policy"] = "disabled_until_evidence_compiled"
        return settings
    if not isinstance(solver_provenance, Mapping):
        raise ScenarioEvaluationBuildError(
            "RESEARCH_IDENTITY_INVALID",
            "research solver provenance must be a complete identity object",
        )
    identity_fields = (
        "research_identity_sha256",
        "static_compiler_algorithm_sha256",
        "source_sha256",
        "component_manifest_sha256",
        "material_manifest_sha256",
        "geometry_manifest_sha256",
    )
    identity = {name: str(solver_provenance.get(name, "")).lower() for name in identity_fields}
    if any(
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in identity.values()
    ):
        raise ScenarioEvaluationBuildError(
            "RESEARCH_IDENTITY_INVALID",
            "research evaluation is missing a complete compiler/source/material/component/geometry evidence identity",
        )
    if solver_provenance.get("artwork_evidence_sha256") != identity["research_identity_sha256"]:
        raise ScenarioEvaluationBuildError(
            "RESEARCH_IDENTITY_INVALID",
            "research result identity does not match its artwork evidence hash",
        )
    settings["research_identity"] = identity
    settings["experimental_cache_policy"] = "disabled"
    return settings


def _expected_result_key(
    design_fingerprint: str,
    rail_id: str,
    *,
    target_ohm: float | None,
    modal_max_index: int,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    solver_provenance: Mapping[str, Any] | None = None,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
) -> ScenarioResultKey:
    profile = resolve_solver_profile(solver_profile)
    if profile.experimental and solver_provenance is None:
        raise ScenarioEvaluationCacheError(
            "experimental research results cannot be looked up before their evidence identity is compiled"
        )
    return ScenarioResultKey.from_settings(
        design_fingerprint=design_fingerprint,
        rail_id=rail_id,
        settings=_evaluation_settings(
            target_ohm,
            modal_max_index,
            solver_profile,
            solver_provenance=solver_provenance,
            evaluation_policy=evaluation_policy,
        ),
        solver_version=SOLVER_VERSION,
    )


def _result_key_from_view(
    design_fingerprint: str,
    rail_id: str,
    *,
    target_ohm: float | None,
    modal_max_index: int,
    view: EvaluationView,
    evaluation_policy: str | None = None,
) -> ScenarioResultKey:
    """Bind an emitted curve to its actual profile/evidence identity."""

    return ScenarioResultKey.from_settings(
        design_fingerprint=design_fingerprint,
        rail_id=rail_id,
        settings=_evaluation_settings(
            target_ohm,
            modal_max_index,
            view.solver_profile_key,
            solver_provenance=view.solver_provenance,
            evaluation_policy=(
                evaluation_policy
                if evaluation_policy is not None
                else getattr(view, "evaluation_policy", EVALUATION_POLICY_STRICT)
            ),
        ),
        solver_version=view.solver_version,
    )


def _canonical_rail_ids(
    scenario: ScenarioSpec,
    rail_ids: Sequence[str],
    *,
    _project: ProjectSpec | None = None,
) -> tuple[str, ...]:
    requested = {str(item).strip().casefold() for item in rail_ids if str(item).strip()}
    if not requested:
        raise ScenarioEvaluationBuildError(
            "EVALUATION_RAILS_EMPTY", "select at least one PWR rail"
        )
    project = _project if _project is not None else scenario.base_project
    available = {item.rail_id.casefold(): item.rail_id for item in project.rails}
    unknown = requested - set(available)
    if unknown:
        raise ScenarioEvaluationBuildError(
            "EVALUATION_RAIL_UNKNOWN",
            "unknown PWR rail selection(s): " + ", ".join(sorted(unknown)),
        )
    return tuple(
        item.rail_id
        for item in project.rails
        if item.rail_id.casefold() in requested
    )


def _via_template_ids_by_rail(project: ProjectSpec) -> dict[str, str]:
    """Resolve the imported rail-template binding without using plane eligibility."""

    provenance = project.metadata.get("spd_via_template_provenance", {})
    result: dict[str, str] = {}
    for rail in project.rails:
        template_id: str | None = None
        if isinstance(provenance, Mapping):
            template_id = next(
                (
                    str(raw_template_id)
                    for raw_template_id, raw in provenance.items()
                    if isinstance(raw, Mapping)
                    and str(raw.get("rail_id", "")).casefold()
                    == rail.rail_id.casefold()
                ),
                None,
            )
        if template_id is None:
            template_id = next(
                (
                    item.template_id
                    for item in project.via_templates
                    if item.pwr_reference_layer.casefold()
                    == rail.pwr_layer.casefold()
                    and item.gnd_reference_layer.casefold()
                    == rail.gnd_layer.casefold()
                ),
                None,
            )
        if template_id:
            result[rail.rail_id.casefold()] = template_id
    return result


def _source_direct_fallback_eligibility(
    decap: ScenarioDecap,
    connection: ScenarioDecapConnection,
    rail: RailSpec,
    template_ids_by_rail: Mapping[str, str],
) -> RailEligibility | None:
    """Model only an unchanged source DIRECT assignment without plane permission.

    ``ScenarioDecap.eligibility`` is the mutable-assignment/Distribution gate.
    The imported DIRECT connection is separate immutable evidence for the
    original source assignment.  Preserve that distinction: a redistributed
    assignment must still carry exact eligibility, while the source state may
    use its imported rail-loop template when the ordered-plane query omitted
    the source rail at one of its physical Via landings.
    """

    if (
        connection.kind != DecapConnectionKind.DIRECT
        or not connection.power_vias
        or not connection.ground_vias
        or decap.current_rail_id.casefold() != decap.source_rail_id.casefold()
        or decap.current_net.casefold() != decap.source_net.casefold()
        or rail.rail_id.casefold() != decap.source_rail_id.casefold()
        or rail.net.casefold() != decap.source_net.casefold()
        or any(
            landing.net.casefold() != decap.source_net.casefold()
            for landing in connection.power_vias
        )
    ):
        return None
    template_id = template_ids_by_rail.get(rail.rail_id.casefold())
    if not template_id:
        return None
    return RailEligibility(
        rail_id=rail.rail_id,
        net=rail.net,
        pwr_layer=rail.pwr_layer,
        gnd_layer=rail.gnd_layer,
        destination_pwr_layer=rail.pwr_layer,
        via_template_id=template_id,
        allowed=True,
    )


@dataclass(frozen=True, slots=True)
class _TerminalFootprint:
    x_um: float
    y_um: float
    width_um: float
    height_um: float


def _selected_rail_plane_bounds_um(
    project: ProjectSpec, rail: RailSpec
) -> tuple[float, float, float, float]:
    """Return the exact rectangle used by the modal solver for one rail."""

    partition = next(
        (
            item
            for item in project.partitions
            if item.layer == rail.pwr_layer and rail.domain in item.domain_to_cell
        ),
        None,
    )
    if partition is None:
        x_min = float(project.outline.origin_x_um)
        y_min = float(project.outline.origin_y_um)
        return (
            x_min,
            x_min + float(project.outline.width_um),
            y_min,
            y_min + float(project.outline.height_um),
        )
    cell_id = partition.domain_to_cell[rail.domain]
    cell = next(item for item in partition.cells if item.cell_id == cell_id)
    return (
        float(cell.x_min_um),
        float(cell.x_max_um),
        float(cell.y_min_um),
        float(cell.y_max_um),
    )


def _terminal_footprint(
    landing: Any, target_layer: str, via: ViaLoopTemplate
) -> _TerminalFootprint:
    """Mirror the worker's source-landing-first finite-port construction."""

    evidence = landing.evidence_for_layer(target_layer)
    if evidence is not None:
        return _TerminalFootprint(
            x_um=float(evidence.x_um),
            y_um=float(evidence.y_um),
            width_um=float(evidence.target_pad_width_um),
            height_um=float(evidence.target_pad_height_um),
        )
    graph_contact = getattr(landing, "graph_contact_for_layer", lambda _layer: None)(
        target_layer
    )
    if graph_contact is not None:
        # A source graph contact identifies the exact retained target node even
        # when the legacy landing coordinate is outside the selected plane.
        # Keep finite-port dimensions from the source template; only the
        # proven contact location is remapped.
        return _TerminalFootprint(
            x_um=float(graph_contact.x_um),
            y_um=float(graph_contact.y_um),
            width_um=float(via.finite_port_width_um),
            height_um=float(via.finite_port_height_um),
        )
    return _TerminalFootprint(
        x_um=float(landing.x_um),
        y_um=float(landing.y_um),
        width_um=float(via.finite_port_width_um),
        height_um=float(via.finite_port_height_um),
    )


def _source_graph_provenance_refresh_required(
    scenario: ScenarioSpec,
    project: ProjectSpec,
) -> bool:
    """Identify old bundles that cannot prove the source plane pair."""

    metadata = project.metadata.get("spd_import")
    if not isinstance(metadata, Mapping):
        return False
    source_sha = str(metadata.get("source_sha256", "")).casefold()
    if source_sha != str(scenario.source.sha256).casefold() or len(source_sha) != 64:
        return False
    provenance = metadata.get("selected_plane_pair_provenance")
    if isinstance(provenance, Mapping) and provenance:
        return False
    version = str(getattr(project, "app_version", ""))
    try:
        major, minor, patch = (int(item) for item in version.split(".")[:3])
    except (TypeError, ValueError):
        return False
    return (major, minor, patch) < (0, 22, 7)


def _source_graph_provenance_blockers(
    scenario: ScenarioSpec,
    project: ProjectSpec,
    rail_ids: Sequence[str],
) -> tuple[EvaluationConnectivityBlocker, ...]:
    """Return hard blockers for invalid source graph provenance.

    These checks intentionally run before all geometry policies.  Embedded
    alternate geometry is an approximation, never a repair for stale source
    identity or an unresolved source graph pair.
    """

    if "spd_import" not in project.metadata:
        return ()
    metadata = project.metadata.get("spd_import")
    if not isinstance(metadata, Mapping):
        reason = (
            "SOURCE_GRAPH_PROVENANCE_INVALID: project metadata spd_import "
            "must be an object when present; re-import matching raw SPD"
        )
        return tuple(
            EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes="<source graph provenance>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=reason,
            )
            for rail_id in rail_ids
        )
    source_sha = str(metadata.get("source_sha256", "")).strip().casefold()
    expected_sha = str(scenario.source.sha256).strip().casefold()
    source_binding_mismatch = bool(
        source_sha and (len(source_sha) != 64 or source_sha != expected_sha)
    )

    def source_binding_blockers() -> tuple[EvaluationConnectivityBlocker, ...]:
        reason = (
            "SOURCE_GRAPH_SOURCE_BINDING_MISMATCH: retained SPD source SHA-256 "
            f"{source_sha or '<missing>'} does not match the scenario source "
            f"{expected_sha}; re-import the matching raw SPD."
        )
        return tuple(
            EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes="<source graph provenance>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=reason,
            )
            for rail_id in rail_ids
        )
    # A present malformed/empty proof is an explicit invalid contract, even
    # for an old bundle that would otherwise qualify for the migration hint.
    # Only proof-key absence may produce the legacy refresh diagnostic.
    if (
        "selected_plane_pair_provenance" not in metadata
        and _source_graph_provenance_refresh_required(scenario, project)
    ):
        source_path = str(scenario.source.path)
        reason = (
            "SOURCE_GRAPH_PROVENANCE_REFRESH_REQUIRED: retained bundle source="
            f"{source_path} sha256={scenario.source.sha256} lacks v0.22.7 "
            "source-exact plane-pair graph provenance. Re-import the matching "
            "raw SPD in v0.22.7 or later; the loaded bundle remains unchanged."
        )
        return tuple(
            EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes="<scenario migration>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=reason,
            )
            for rail_id in rail_ids
        )
    # Synthetic fixtures are exempt only when the proof key is absent and no
    # source SHA is retained.  A present key is an explicit source-graph
    # contract: malformed, non-mapping, and empty values always fail closed.
    if "selected_plane_pair_provenance" not in metadata:
        if source_binding_mismatch:
            return source_binding_blockers()
        if not source_sha:
            return ()
        reason = (
            "SOURCE_GRAPH_PROVENANCE_INVALID: source-bound bundle is missing "
            "selected plane-pair provenance; re-import matching raw SPD"
        )
        return tuple(
            EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes="<source graph provenance>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=reason,
            )
            for rail_id in rail_ids
        )
    provenance = metadata.get("selected_plane_pair_provenance")
    if not isinstance(provenance, Mapping):
        invalid_keys = {rail_id.casefold() for rail_id in rail_ids}
        return tuple(
            EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes="<source graph provenance>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=(
                    "SOURCE_GRAPH_PROVENANCE_INVALID: selected plane-pair "
                    "provenance is not an object for selected rail(s) "
                    f"{', '.join(sorted(invalid_keys))}"
                ),
            )
            for rail_id in rail_ids
        )
    if not provenance:
        reason = (
            "SOURCE_GRAPH_PROVENANCE_INVALID: selected plane-pair provenance "
            "is empty; re-import matching raw SPD"
        )
        return tuple(
            EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes="<source graph provenance>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=reason,
            )
            for rail_id in rail_ids
        )
    provenance_key_counts: dict[str, int] = {}
    for raw_key in provenance:
        folded_key = str(raw_key).casefold()
        provenance_key_counts[folded_key] = provenance_key_counts.get(folded_key, 0) + 1
    duplicate_keys = sorted(
        key for key, count in provenance_key_counts.items() if count > 1
    )
    if duplicate_keys:
        reason = (
            "SOURCE_GRAPH_PROVENANCE_DUPLICATE_KEY: persisted plane-pair "
            "provenance contains duplicate case-insensitive rail key(s): "
            + ", ".join(duplicate_keys)
        )
        return tuple(
            EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes="<source graph provenance>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=reason,
            )
            for rail_id in rail_ids
        )
    if source_binding_mismatch:
        rail_by_key = {item.rail_id.casefold(): item for item in project.rails}
        selected_unresolved = False
        for rail_id in rail_ids:
            rail = rail_by_key.get(rail_id.casefold())
            aliases = {rail_id.casefold()}
            if rail is not None:
                aliases.add(str(rail.net).casefold())
            selected_unresolved = selected_unresolved or any(
                str(raw_key).casefold() in aliases
                and isinstance(value, Mapping)
                and bool(value.get("source_graph_pair_unresolved"))
                for raw_key, value in provenance.items()
            )
        if not selected_unresolved:
            return source_binding_blockers()
    rail_by_key = {item.rail_id.casefold(): item for item in project.rails}
    blockers: list[EvaluationConnectivityBlocker] = []
    for rail_id in rail_ids:
        rail = rail_by_key.get(rail_id.casefold())
        aliases = {rail_id.casefold()}
        if rail is not None:
            aliases.add(str(rail.net).casefold())
        matches = sorted(
            (
                (str(raw_key), value)
                for raw_key, value in provenance.items()
                if str(raw_key).casefold() in aliases
            ),
            key=lambda item: (item[0].casefold(), item[0]),
        )
        unresolved_matches = [
            item for item in matches
            if isinstance(item[1], Mapping)
            and bool(item[1].get("source_graph_pair_unresolved"))
        ]
        if unresolved_matches:
            blockers.append(EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes="<source graph plane pair>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=(
                    "SOURCE_GRAPH_PLANE_PAIR_UNRESOLVED: selected rail has "
                    "no source-proven PWR/GND pair; re-import matching raw SPD"
                ),
            ))
            continue
        if len(matches) != 1:
            if len(matches) > 1:
                matched_keys = ", ".join(item[0] for item in matches)
                reason = (
                    "SOURCE_GRAPH_PROVENANCE_AMBIGUOUS: selected rail matches "
                    f"multiple rail-id/net plane-pair proofs ({matched_keys})"
                )
            else:
                reason = (
                    "SOURCE_GRAPH_PROVENANCE_INVALID: selected rail has no "
                    "matching rail-id/net plane-pair proof; re-import matching raw SPD"
                )
            blockers.append(EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes="<source graph provenance>",
                kind=DecapConnectionKind.UNRESOLVED,
                reason=reason,
            ))
            continue
        proof = matches[0][1]
        required = ("pwr_layer", "gnd_layer", "source_sha256")
        valid = isinstance(proof, Mapping) and bool(proof) and all(
            str(proof.get(name, "")).strip() for name in required
        )
        if valid and not source_sha:
            valid = False
        valid = valid and str(proof.get("source_sha256", "")).casefold() == expected_sha
        if rail is not None:
            valid = valid and str(proof.get("pwr_layer", "")).casefold() == rail.pwr_layer.casefold() and str(proof.get("gnd_layer", "")).casefold() == rail.gnd_layer.casefold()
        if not valid:
            reason = (
                "SOURCE_GRAPH_PROVENANCE_INVALID: selected rail lacks a "
                "non-empty source-bound PWR/GND plane-pair proof"
            )
            blockers.append(EvaluationConnectivityBlocker(
                rail_id=rail_id, refdes="<source graph provenance>",
                kind=DecapConnectionKind.UNRESOLVED, reason=reason
            ))
    return tuple(blockers)


@dataclass(frozen=True, slots=True)
class _AlternateEvaluationContext:
    scenario: ScenarioSpec
    project: ProjectSpec
    rail_id: str
    candidate_layer: str
    ground_layer: str
    provenance: dict[str, Any]


def _geometry_records(project: ProjectSpec) -> list[Mapping[str, Any]]:
    raw = project.metadata.get("spd_import")
    records = raw.get("plane_geometries") if isinstance(raw, Mapping) else None
    return [item for item in records if isinstance(item, Mapping)] if isinstance(records, list) else []


def _record_bounds(record: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    values = record.get("solver_bounds_um") or record.get("bounds_um") or record.get("bbox_um")
    if isinstance(values, (list, tuple)) and len(values) == 4:
        try:
            result = tuple(float(item) for item in values)
        except (TypeError, ValueError):
            result = None
        if result is not None and all(isfinite(item) for item in result) and result[1] > result[0] and result[3] > result[2]:
            return result  # type: ignore[return-value]
    xs: list[float] = []
    ys: list[float] = []
    for poly in record.get("positive_polygons_um", []):
        if isinstance(poly, (list, tuple)):
            for point in poly:
                if isinstance(point, (list, tuple)) and len(point) == 2:
                    xs.append(float(point[0])); ys.append(float(point[1]))
    for circle in record.get("positive_circles_um", []):
        if isinstance(circle, (list, tuple)) and len(circle) == 3:
            x, y, radius = (float(item) for item in circle)
            xs.extend((x - radius, x + radius)); ys.extend((y - radius, y + radius))
    if not xs or not ys:
        return None
    result = (min(xs), max(xs), min(ys), max(ys))
    return result if result[1] > result[0] and result[3] > result[2] else None


@dataclass(frozen=True, slots=True)
class _RetainedArtworkEntry:
    asset: str
    digest: str
    layer: str
    net: str
    indexed: IndexedPlaneGeometry


class _RetainedArtworkIndex:
    """Decode retained plane assets once and validate exact finite coverage."""

    def __init__(self, records: Sequence[Mapping[str, Any]], attachments: Mapping[str, bytes]) -> None:
        entries: list[_RetainedArtworkEntry] = []
        decoded: set[tuple[str, str]] = set()
        for record in records:
            asset = str(record.get("asset", "")).strip()
            digest = str(record.get("asset_sha256", "")).strip().lower()
            layer = str(record.get("layer", "")).strip()
            net = str(record.get("net", "")).strip()
            if not asset or not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            key = (asset.casefold(), digest)
            if key in decoded:
                continue
            decoded.add(key)
            payload_bytes = attachments.get(asset)
            if payload_bytes is None:
                continue
            try:
                payload = evaluation_services._decode_spd_geometry_asset(digest, payload_bytes)
                evaluation_services._validate_spd_geometry_payload(payload, expected_layer=layer, expected_net=net)
                geometry = SpdPlaneGeometry(
                    layer=str(payload["layer"]),
                    net=str(payload["net"]),
                    positive_polygons_um=tuple(tuple((float(point[0]), float(point[1])) for point in polygon) for polygon in payload["positive_polygons_um"]),
                    negative_polygons_um=tuple(tuple((float(point[0]), float(point[1])) for point in polygon) for polygon in payload["negative_polygons_um"]),
                    positive_circles_um=tuple(tuple(float(value) for value in circle) for circle in payload["positive_circles_um"]),
                    negative_circles_um=tuple(tuple(float(value) for value in circle) for circle in payload["negative_circles_um"]),
                    primitive_order=tuple((str(step[0]), int(step[1])) for step in payload["primitive_order"]),
                )
                indexed = IndexedPlaneGeometry.build(geometry)
                if indexed is not None:
                    entries.append(_RetainedArtworkEntry(asset, digest, layer, net, indexed))
            except (TypeError, ValueError, IndexError, ArithmeticError, ImportError):
                continue
        self._entries = tuple(entries)
        self._by_layer_net: dict[tuple[str, str], list[_RetainedArtworkEntry]] = {}
        for entry in self._entries:
            self._by_layer_net.setdefault((entry.layer.casefold(), entry.net.casefold()), []).append(entry)

    def covers_footprint(self, *, layer: str, net: str, asset: str, digest: str, x_um: float, y_um: float, width_um: float, height_um: float) -> bool:
        entry = next((item for item in self._entries if item.asset.casefold() == asset.casefold() and item.digest == digest.casefold() and item.layer.casefold() == layer.casefold() and item.net.casefold() == net.casefold()), None)
        if entry is None:
            return False
        try:
            return bool(entry.indexed.covers_footprint(float(x_um), float(y_um), float(width_um), float(height_um)))
        except (ImportError, TypeError, ValueError, ArithmeticError):
            return False

    def contains(self, *, layer: str, net: str, x_um: float, y_um: float) -> str:
        entries = self._by_layer_net.get((layer.casefold(), net.casefold()), ())
        saw_boundary = False
        saw_inside = False
        for entry in entries:
            try:
                result = entry.indexed.contains(x_um, y_um)
            except (ImportError, TypeError, ValueError, ArithmeticError):
                continue
            if result == "boundary":
                saw_boundary = True
            elif result == "inside":
                saw_inside = True
        if saw_boundary:
            return "boundary"
        return "inside" if saw_inside else "outside"


def _alternate_terminal_points(
    scenario: ScenarioSpec,
    project: ProjectSpec,
    rail_id: str,
    *,
    pwr_layer: str,
    gnd_layer: str,
    via: ViaLoopTemplate,
) -> tuple[tuple[str, str, float, float, float, float], ...]:
    analysis = scenario.connection_analysis
    if analysis is None:
        return ()
    decaps = {item.refdes.casefold(): item for item in scenario.decaps}
    points: list[tuple[str, str, float, float, float, float]] = []
    for connection in analysis.connections.values():
        decap = decaps.get(connection.refdes.casefold())
        if decap is None or decap.current_rail_id.casefold() != rail_id.casefold():
            continue
        if not decap.enabled and len(connection.power_vias) == 1 and len(connection.ground_vias) == 1:
            continue
        for terminal, landings, layer in ((TerminalKind.PWR, connection.power_vias, pwr_layer), (TerminalKind.GND, connection.ground_vias, gnd_layer)):
            for landing in landings:
                evidence = landing.evidence_for_layer(layer)
                graph_contact = getattr(landing, "graph_contact_for_layer", lambda _layer: None)(layer)
                x_um = float(evidence.x_um) if evidence is not None else float(graph_contact.x_um) if graph_contact is not None else float(landing.x_um)
                y_um = float(evidence.y_um) if evidence is not None else float(graph_contact.y_um) if graph_contact is not None else float(landing.y_um)
                width = float(evidence.target_pad_width_um) if evidence is not None else float(via.finite_port_width_um)
                height = float(evidence.target_pad_height_um) if evidence is not None else float(via.finite_port_height_um)
                if not all(isfinite(value) for value in (x_um, y_um)) or not all(isfinite(value) and value > 0.0 for value in (width, height)):
                    return ()
                points.extend((
                    (terminal.value, layer, x_um, y_um, width, height),
                    (terminal.value, layer, x_um - width / 2.0, y_um - height / 2.0, width, height),
                    (terminal.value, layer, x_um - width / 2.0, y_um + height / 2.0, width, height),
                    (terminal.value, layer, x_um + width / 2.0, y_um - height / 2.0, width, height),
                    (terminal.value, layer, x_um + width / 2.0, y_um + height / 2.0, width, height),
                ))
    return tuple(points)


def _alternate_context_for_rail(
    scenario: ScenarioSpec,
    rail_id: str,
    *,
    attachments: Mapping[str, bytes] | None = None,
    _cache: dict[tuple[str, str, str, str], _AlternateEvaluationContext] | None = None,
) -> _AlternateEvaluationContext | None:
    """Derive an attachment/hash-bound alternate plane pair fail-closed."""

    base = scenario.base_project
    rail = next((item for item in base.rails if item.rail_id.casefold() == rail_id.casefold()), None)
    if rail is None:
        return None
    records = _geometry_records(base)
    raw_import = base.metadata.get("spd_import")
    source_hash = str(raw_import.get("source_sha256", "")) if isinstance(raw_import, Mapping) else ""
    if attachments is None or source_hash.casefold() != str(scenario.source.sha256).casefold() or not re.fullmatch(r"[0-9a-fA-F]{64}", source_hash):
        return None
    # Baseline/cache attachments are added after comparison preflight.  Bind
    # the alternate cache only to the retained geometry assets it consumes so
    # the same shared cache entry survives the preflight -> preparatory-build
    # handoff instead of rebuilding once per attachment-set shape.
    geometry_names = {
        str(record.get("asset", ""))
        for record in records
        if str(record.get("asset", "")).strip()
    }
    attachment_fingerprint = sha256(
        b"".join(
            name.encode("utf-8") + sha256(payload).digest()
            for name, payload in sorted(attachments.items(), key=lambda item: item[0].casefold())
            if name in geometry_names
        )
    ).hexdigest()
    cache_key = (scenario.design_fingerprint, rail.rail_id.casefold(), source_hash.casefold(), attachment_fingerprint)
    cache = _cache if _cache is not None else {}
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    stack = {layer.name.casefold(): index for index, layer in enumerate(base.stackup_layers)}
    from spd_decap_pi._core.plane_pairs import suggest_effective_plane_pairs
    allowed_pair_separation = {
        (item.pwr_layer.casefold(), item.gnd_layer.casefold()): float(item.separation_um)
        for item in suggest_effective_plane_pairs(base.stackup_layers, rail_net=rail.net, gnd_aliases=base.gnd_aliases)
    }
    gnd_keys = {item.casefold() for item in base.gnd_aliases}
    outline_bounds = (
        float(base.outline.origin_x_um),
        float(base.outline.origin_x_um + base.outline.width_um),
        float(base.outline.origin_y_um),
        float(base.outline.origin_y_um + base.outline.height_um),
    )
    candidates: list[tuple[tuple[Any, ...], Mapping[str, Any], Mapping[str, Any], tuple[float, float, float, float], tuple[float, float, float, float]]] = []
    for pwr in records:
        pwr_layer = str(pwr.get("layer", "")).strip()
        pwr_net = str(pwr.get("net", "")).strip()
        pwr_bounds = _record_bounds(pwr)
        pwr_asset = str(pwr.get("asset", "")).strip()
        pwr_sha = str(pwr.get("asset_sha256", "")).strip().lower()
        if (not pwr_layer or not pwr_net or pwr_net.casefold() != rail.net.casefold() or pwr_layer.casefold() == rail.pwr_layer.casefold() or pwr_bounds is None or pwr_bounds[0] < outline_bounds[0] or pwr_bounds[1] > outline_bounds[1] or pwr_bounds[2] < outline_bounds[2] or pwr_bounds[3] > outline_bounds[3] or not pwr_asset or not re.fullmatch(r"[0-9a-f]{64}", pwr_sha) or attachments.get(pwr_asset) is None or sha256(attachments[pwr_asset]).hexdigest().casefold() != pwr_sha or pwr_layer.casefold() not in stack):
            continue
        for gnd in records:
            gnd_layer = str(gnd.get("layer", "")).strip()
            gnd_net = str(gnd.get("net", "")).strip()
            pair_key = (pwr_layer.casefold(), gnd_layer.casefold())
            gnd_bounds = _record_bounds(gnd)
            gnd_asset = str(gnd.get("asset", "")).strip()
            gnd_sha = str(gnd.get("asset_sha256", "")).strip().lower()
            if (not gnd_layer or gnd_net.casefold() not in gnd_keys or pair_key not in allowed_pair_separation or gnd_bounds is None or gnd_bounds[0] < outline_bounds[0] or gnd_bounds[1] > outline_bounds[1] or gnd_bounds[2] < outline_bounds[2] or gnd_bounds[3] > outline_bounds[3] or not gnd_asset or not re.fullmatch(r"[0-9a-f]{64}", gnd_sha) or attachments.get(gnd_asset) is None or sha256(attachments[gnd_asset]).hexdigest().casefold() != gnd_sha):
                continue
            candidates.append(((allowed_pair_separation[pair_key], pwr_layer.casefold(), gnd_layer.casefold(), pwr_net.casefold()), pwr, gnd, pwr_bounds, gnd_bounds))
    if not candidates:
        return None
    template_ids = _via_template_ids_by_rail(base)
    source_template_id = template_ids.get(rail.rail_id.casefold())
    template = next((item for item in base.via_templates if item.template_id.casefold() == (source_template_id or "").casefold()), None)
    if template is None:
        template = next((item for item in base.via_templates if item.pwr_reference_layer.casefold() == rail.pwr_layer.casefold() and item.gnd_reference_layer.casefold() == rail.gnd_layer.casefold()), None)
    if template is None:
        return None
    selected: tuple[Mapping[str, Any], Mapping[str, Any], tuple[float, float, float, float], tuple[float, float, float, float], ViaLoopTemplate] | None = None
    for _key, pwr, gnd, pwr_bounds, gnd_bounds in sorted(candidates, key=lambda item: item[0]):
        artwork = _RetainedArtworkIndex((pwr, gnd), attachments)
        if not artwork._entries:
            continue
        probe_template = template.model_copy(update={"pwr_reference_layer": str(pwr["layer"]), "gnd_reference_layer": str(gnd["layer"])})
        points = _alternate_terminal_points(scenario, base, rail.rail_id, pwr_layer=str(pwr["layer"]), gnd_layer=str(gnd["layer"]), via=probe_template)
        valid = bool(points)
        for point_index, (_terminal, layer, x_um, y_um, width_um, height_um) in enumerate(points):
            record = pwr if layer.casefold() == str(pwr["layer"]).casefold() else gnd
            bounds = pwr_bounds if record is pwr else gnd_bounds
            if point_index % 5 == 0 and (x_um - width_um / 2.0 < bounds[0] or x_um + width_um / 2.0 > bounds[1] or y_um - height_um / 2.0 < bounds[2] or y_um + height_um / 2.0 > bounds[3]):
                valid = False
                break
            if artwork.contains(layer=layer, net=str(record.get("net", "")), x_um=x_um, y_um=y_um) != "inside":
                valid = False
                break
            if point_index % 5 == 0 and not artwork.covers_footprint(layer=layer, net=str(record.get("net", "")), asset=str(record.get("asset", "")), digest=str(record.get("asset_sha256", "")), x_um=x_um, y_um=y_um, width_um=width_um, height_um=height_um):
                valid = False
                break
        if valid:
            selected = (pwr, gnd, pwr_bounds, gnd_bounds, probe_template)
            break
    if selected is None:
        return None
    pwr_record, gnd_record, _pwr_bounds, _gnd_bounds, _ = selected
    alt_template_id = f"{template.template_id}__ALT_{str(pwr_record['layer']).replace(' ', '_')}__{str(gnd_record['layer']).replace(' ', '_')}"
    alt_template = template.model_copy(update={"template_id": alt_template_id, "pwr_reference_layer": str(pwr_record["layer"]), "gnd_reference_layer": str(gnd_record["layer"])})
    rl_recomputed = False
    resistance, inductance = template.loop_resistance_ohm, template.loop_inductance_h
    pwr_depth = gnd_depth = None
    try:
        depths = evaluation_services._spd_layer_center_depths(base.stackup_layers)
        pwr_depth, gnd_depth = depths[str(pwr_record["layer"])], depths[str(gnd_record["layer"])]
        detail = base.metadata.get("spd_via_template_provenance", {}).get(template.template_id, {})
        drill = detail.get("drill_diameter_um") if isinstance(detail, Mapping) else None
        resistance, inductance = evaluation_services._spd_uncalibrated_loop_estimate(pwr_depth_um=pwr_depth, gnd_depth_um=gnd_depth, drill_diameter_um=float(drill) if drill is not None else None)
        alt_template = alt_template.model_copy(update={"loop_resistance_ohm": resistance, "loop_inductance_h": inductance})
        rl_recomputed = True
    except (KeyError, TypeError, ValueError):
        pass
    alt_rail = rail.model_copy(update={"pwr_layer": str(pwr_record["layer"]), "gnd_layer": str(gnd_record["layer"]), "mixed_reference_certificate": None, "mixed_reference_ground_witness": None})
    rails = [alt_rail if item.rail_id.casefold() == rail.rail_id.casefold() else item for item in base.rails]
    metadata = dict(base.metadata)
    spd_import = dict(metadata.get("spd_import", {})) if isinstance(metadata.get("spd_import"), Mapping) else {}
    spd_import["plane_geometries"] = [dict(item) for item in records]
    spd_import["evaluation_alternate_pair"] = {"policy": EVALUATION_POLICY_EMBEDDED_ALTERNATE, "rail_id": rail.rail_id, "pwr_layer": alt_rail.pwr_layer, "gnd_layer": alt_rail.gnd_layer, "pwr_source_net": str(pwr_record.get("net", "")), "gnd_source_net": str(gnd_record.get("net", ""))}
    metadata["spd_import"] = spd_import
    # Rebind the imported Via-template provenance to the derived pair.  The
    # source template entry must not remain selected for this transformed
    # rail: downstream template resolution is metadata-first and would
    # otherwise silently choose the original PWR1/GND1 identity.
    raw_via_provenance = metadata.get("spd_via_template_provenance", {})
    via_provenance = dict(raw_via_provenance) if isinstance(raw_via_provenance, Mapping) else {}
    source_details: dict[str, Any] = {}
    old_key = next(
        (key for key in via_provenance if str(key).casefold() == template.template_id.casefold()),
        None,
    )
    if old_key is not None and isinstance(via_provenance.get(old_key), Mapping):
        source_details = dict(via_provenance[old_key])
        via_provenance.pop(old_key, None)
    alternate_details = dict(source_details)
    alternate_details.update(
        {
            "rail_id": rail.rail_id,
            "pwr_reference_layer": alt_rail.pwr_layer,
            "gnd_reference_layer": alt_rail.gnd_layer,
            "calibration": "embedded_alternate_analytical_approximation",
            "source_vertical_path_proven": False,
            "derived_loop_resistance_ohm": resistance,
            "derived_loop_inductance_h": inductance,
            "derived_pwr_depth_um": pwr_depth,
            "derived_gnd_depth_um": gnd_depth,
            "source_template_id": template.template_id,
            "source_template_provenance": source_details,
        }
    )
    via_provenance[alt_template_id] = alternate_details
    metadata["spd_via_template_provenance"] = via_provenance
    provisional = base.model_copy(update={"rails": rails, "via_templates": [*base.via_templates, alt_template], "metadata": metadata, "partitions": []})
    candidate_project = provisional.model_copy(update={"partitions": evaluation_services._spd_plane_partitions(provisional)})
    decaps: list[ScenarioDecap] = []
    for decap in scenario.decaps:
        if decap.current_rail_id.casefold() != rail.rail_id.casefold():
            decaps.append(decap)
            continue
        eligibility = dict(decap.eligibility)
        eligibility[rail.rail_id] = RailEligibility(rail_id=rail.rail_id, net=rail.net, pwr_layer=alt_rail.pwr_layer, gnd_layer=alt_rail.gnd_layer, via_template_id=alt_template_id, allowed=True)
        decaps.append(decap.model_copy(update={"eligibility": eligibility}))
    clusters = []
    analysis = scenario.connection_analysis
    for cluster in analysis.clusters if analysis is not None else ():
        member_keys = {item.casefold() for item in cluster.member_refdes}
        if not any(
            decap.current_rail_id.casefold() == rail.rail_id.casefold()
            for decap in scenario.decaps
            if decap.refdes.casefold() in member_keys
        ):
            clusters.append(cluster)
            continue
        selected_via_ids = {
            landing.via_id.casefold()
            for connection in (analysis.connections.values() if analysis is not None else ())
            if connection.refdes.casefold() in member_keys
            for landing in connection.power_vias
        }
        via_eligibility: dict[str, dict[str, RailEligibility]] = {}
        for via_id, by_rail in cluster.via_eligibility.items():
            updated = dict(by_rail)
            selected_key = next((key for key in updated if key.casefold() == rail.rail_id.casefold()), None)
            if selected_key is not None or via_id.casefold() in selected_via_ids:
                selected_key = selected_key or rail.rail_id
                updated[selected_key] = RailEligibility(
                    rail_id=rail.rail_id, net=rail.net,
                    pwr_layer=alt_rail.pwr_layer, gnd_layer=alt_rail.gnd_layer,
                    via_template_id=alt_template_id, allowed=True,
                )
            via_eligibility[via_id] = updated
        updated_eligibility = dict(cluster.eligibility)
        selected_key = next((key for key in updated_eligibility if key.casefold() == rail.rail_id.casefold()), None)
        if selected_key is not None:
            updated_eligibility[selected_key] = RailEligibility(
                rail_id=rail.rail_id, net=rail.net,
                pwr_layer=alt_rail.pwr_layer, gnd_layer=alt_rail.gnd_layer,
                via_template_id=alt_template_id, allowed=True,
            )
        clusters.append(cluster.model_copy(update={"via_eligibility": via_eligibility, "eligibility": updated_eligibility}))
    candidate_analysis = analysis.model_copy(update={"clusters": tuple(clusters)}) if analysis is not None else None
    candidate_scenario = scenario.model_copy(update={"decaps": tuple(decaps), "connection_analysis": candidate_analysis})
    provenance = {"policy": EVALUATION_POLICY_EMBEDDED_ALTERNATE, "candidate_pwr_layer": alt_rail.pwr_layer, "candidate_gnd_layer": alt_rail.gnd_layer, "candidate_pwr_source_net": str(pwr_record.get("net", "")), "candidate_gnd_source_net": str(gnd_record.get("net", "")), "candidate_pwr_asset": str(pwr_record.get("asset", "")), "candidate_gnd_asset": str(gnd_record.get("asset", "")), "candidate_pwr_asset_sha256": str(pwr_record.get("asset_sha256", "")), "candidate_gnd_asset_sha256": str(gnd_record.get("asset_sha256", "")), "source_template_id": template.template_id, "alternate_template_id": alt_template_id, "missing_source_vertical_path_approximation": True, "template_rl_recomputed": rl_recomputed, "loop_resistance_ohm": resistance, "loop_inductance_h": inductance, "derived_pwr_depth_um": pwr_depth, "derived_gnd_depth_um": gnd_depth}
    context = _AlternateEvaluationContext(candidate_scenario, candidate_project, rail.rail_id, alt_rail.pwr_layer, alt_rail.gnd_layer, provenance)
    cache[cache_key] = context
    return context
def _outside_terminal_blocker(
    *,
    rail: RailSpec,
    refdes: str,
    kind: DecapConnectionKind,
    terminal: TerminalKind,
    path_id: str,
    landing: Any,
    target_layer: str,
    via: ViaLoopTemplate,
    bounds: tuple[float, float, float, float],
) -> EvaluationConnectivityBlocker | None:
    footprint = _terminal_footprint(landing, target_layer, via)
    x_min, x_max, y_min, y_max = bounds
    footprint_x_min = footprint.x_um - footprint.width_um / 2.0
    footprint_x_max = footprint.x_um + footprint.width_um / 2.0
    footprint_y_min = footprint.y_um - footprint.height_um / 2.0
    footprint_y_max = footprint.y_um + footprint.height_um / 2.0
    tolerance_um = max(x_max - x_min, y_max - y_min) * 1e-12
    overruns = (
        max(0.0, x_min - footprint_x_min),
        max(0.0, footprint_x_max - x_max),
        max(0.0, y_min - footprint_y_min),
        max(0.0, footprint_y_max - y_max),
    )
    if max(overruns) <= tolerance_um:
        return None
    left, right, bottom, top = overruns
    terminal_value = terminal.value
    reason = (
        "TERMINAL_OUTSIDE_SELECTED_PLANE: "
        f"{terminal_value} terminal {landing.via_id!r}, path {path_id!r}, "
        f"footprint center=({footprint.x_um:.6g}, {footprint.y_um:.6g}) um, "
        f"size=({footprint.width_um:.6g} x {footprint.height_um:.6g}) um; "
        f"selected plane x=[{x_min:.6g}, {x_max:.6g}] um, "
        f"y=[{y_min:.6g}, {y_max:.6g}] um; overruns "
        f"left={left:.6g}, right={right:.6g}, bottom={bottom:.6g}, "
        f"top={top:.6g} um. Source coordinates and landing geometry are "
        "preserved; Evaluation does not clamp, expand, or drop the terminal."
    )
    return EvaluationConnectivityBlocker(
        rail_id=rail.rail_id,
        refdes=refdes,
        kind=kind,
        reason=reason,
        terminal=terminal,
        path_id=path_id,
        source_via_id=landing.via_id,
        center_x_um=footprint.x_um,
        center_y_um=footprint.y_um,
        width_um=footprint.width_um,
        height_um=footprint.height_um,
        plane_x_min_um=x_min,
        plane_x_max_um=x_max,
        plane_y_min_um=y_min,
        plane_y_max_um=y_max,
        overrun_left_um=left,
        overrun_right_um=right,
        overrun_bottom_um=bottom,
        overrun_top_um=top,
    )


def _evaluation_geometry_blockers(
    scenario: ScenarioSpec,
    canonical_rails: Sequence[str],
    *,
    _project: ProjectSpec | None = None,
) -> tuple[EvaluationConnectivityBlocker, ...]:
    """Aggregate every finite terminal footprint that the worker cannot stamp."""

    analysis = scenario.connection_analysis
    if analysis is None:
        return ()
    project = _project if _project is not None else scenario.base_project
    selected_keys = {item.casefold() for item in canonical_rails}
    rail_by_key = {item.rail_id.casefold(): item for item in project.rails}
    via_by_key = {item.template_id.casefold(): item for item in project.via_templates}
    template_ids_by_rail = _via_template_ids_by_rail(project)
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    connections = {
        item.refdes.casefold(): item for item in analysis.connections.values()
    }
    landing_owner_by_terminal = {
        TerminalKind.PWR: {},
        TerminalKind.GND: {},
    }
    for refdes_key in sorted(connections):
        connection = connections[refdes_key]
        for terminal, landings in (
            (TerminalKind.PWR, connection.power_vias),
            (TerminalKind.GND, connection.ground_vias),
        ):
            for landing in landings:
                landing_owner_by_terminal[terminal].setdefault(
                    landing.via_id.casefold(), connection.refdes
                )
    bounds_by_rail = {
        key: _selected_rail_plane_bounds_um(project, rail)
        for key, rail in rail_by_key.items()
        if key in selected_keys
    }
    blockers: list[EvaluationConnectivityBlocker] = []

    def append_landing(
        *,
        rail: RailSpec,
        refdes: str,
        kind: DecapConnectionKind,
        terminal: TerminalKind,
        path_id: str,
        landing: Any,
        via: ViaLoopTemplate,
    ) -> None:
        blocker = _outside_terminal_blocker(
            rail=rail,
            refdes=refdes,
            kind=kind,
            terminal=terminal,
            path_id=path_id,
            landing=landing,
            target_layer=(
                rail.pwr_layer if terminal == TerminalKind.PWR else rail.gnd_layer
            ),
            via=via,
            bounds=bounds_by_rail[rail.rail_id.casefold()],
        )
        if blocker is not None:
            blockers.append(blocker)

    consumed: set[str] = set()
    for cluster in analysis.clusters:
        member_keys = tuple(item.casefold() for item in cluster.member_refdes)
        consumed.update(member_keys)
        if cluster.state != SharedPadClusterState.ANCHORED or any(
            key not in decap_by_key or key not in connections for key in member_keys
        ):
            continue
        if not any(
            decap_by_key[key].current_rail_id.casefold() in selected_keys
            for key in member_keys
        ):
            continue
        derivation = derive_shared_pad_current_components(
            cluster,
            {key: decap_by_key[key] for key in member_keys},
            {key: connections[key] for key in member_keys},
            analysis_version=analysis.version,
        )
        if (
            derivation.shared_power_via_conflicts
            or derivation.shared_ground_via_conflicts
        ):
            continue
        components_by_rail: dict[str, list[Any]] = {}
        for component in derivation.components:
            rail_key = component.current_rail_id.casefold()
            if rail_key in selected_keys:
                components_by_rail.setdefault(rail_key, []).append(component)
        for rail_key, grouped_components in components_by_rail.items():
            rail = rail_by_key.get(rail_key)
            if rail is None:
                continue
            selected_components = tuple(grouped_components)
            component_eligibility = []
            for component in selected_components:
                eligibility = next(
                    (
                        item
                        for item in shared_pad_component_eligibility(
                            cluster, component
                        ).values()
                        if item.rail_id.casefold() == rail_key and item.allowed
                    ),
                    None,
                )
                if eligibility is None:
                    component_eligibility = []
                    break
                component_eligibility.append(eligibility)
            template_keys = {
                (item.via_template_id or "").casefold()
                for item in component_eligibility
            }
            if len(template_keys) != 1:
                continue
            via = via_by_key.get(next(iter(template_keys)))
            if via is None or (
                via.pwr_reference_layer.casefold() != rail.pwr_layer.casefold()
                or via.gnd_reference_layer.casefold() != rail.gnd_layer.casefold()
            ):
                continue
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
            domain_cluster_id = f"SPDPI:CLUSTER:{cluster.cluster_id}"
            for component_index, component in enumerate(
                selected_components, start=1
            ):
                for path_index, landing in enumerate(component.power_vias, start=1):
                    append_landing(
                        rail=rail,
                        refdes=landing_owner_by_terminal[TerminalKind.PWR].get(
                            landing.via_id.casefold(),
                            sorted(component.member_refdes, key=str.casefold)[0],
                        ),
                        kind=DecapConnectionKind.SHARED_ANCHOR,
                        terminal=TerminalKind.PWR,
                        path_id=(
                            f"{domain_cluster_id}:PWR:{component_index}:{path_index}"
                        ),
                        landing=landing,
                        via=via,
                    )
            for component_index, component in enumerate(
                selected_ground_components, start=1
            ):
                for path_index, landing in enumerate(component.ground_vias, start=1):
                    append_landing(
                        rail=rail,
                        refdes=landing_owner_by_terminal[TerminalKind.GND].get(
                            landing.via_id.casefold(),
                            sorted(component.member_refdes, key=str.casefold)[0],
                        ),
                        kind=DecapConnectionKind.SHARED_ANCHOR,
                        terminal=TerminalKind.GND,
                        path_id=(
                            f"{domain_cluster_id}:GND:{component_index}:{path_index}"
                        ),
                        landing=landing,
                        via=via,
                    )

    for decap in scenario.decaps:
        decap_key = decap.refdes.casefold()
        if decap_key in consumed or decap.current_rail_id.casefold() not in selected_keys:
            continue
        connection = connections.get(decap_key)
        if connection is None or connection.kind != DecapConnectionKind.DIRECT:
            continue
        unique_power = {
            item.via_id.casefold(): item for item in connection.power_vias
        }
        unique_ground = {
            item.via_id.casefold(): item for item in connection.ground_vias
        }
        if not decap.enabled and len(unique_power) == 1 and len(unique_ground) == 1:
            continue
        rail = rail_by_key.get(decap.current_rail_id.casefold())
        if rail is None:
            continue
        eligibility = next(
            (
                item
                for rail_id, item in decap.eligibility.items()
                if rail_id.casefold() == rail.rail_id.casefold()
            ),
            None,
        )
        if eligibility is None:
            eligibility = _source_direct_fallback_eligibility(
                decap, connection, rail, template_ids_by_rail
            )
        if eligibility is None or not eligibility.allowed or not eligibility.via_template_id:
            continue
        via = via_by_key.get(eligibility.via_template_id.casefold())
        if via is None or (
            via.pwr_reference_layer.casefold() != rail.pwr_layer.casefold()
            or via.gnd_reference_layer.casefold() != rail.gnd_layer.casefold()
        ):
            continue
        has_recovered_path = any(
            item.evidence_for_layer(rail.pwr_layer) is not None
            for item in unique_power.values()
        ) or any(
            item.evidence_for_layer(rail.gnd_layer) is not None
            for item in unique_ground.values()
        )
        coupled = len(unique_power) > 1 or len(unique_ground) > 1 or has_recovered_path
        power_landings = tuple(unique_power[key] for key in sorted(unique_power))
        ground_landings = tuple(unique_ground[key] for key in sorted(unique_ground))
        for path_index, landing in enumerate(power_landings, start=1):
            append_landing(
                rail=rail,
                refdes=decap.refdes,
                kind=DecapConnectionKind.DIRECT,
                terminal=TerminalKind.PWR,
                path_id=(
                    f"SPDPI:CLUSTER:DIRECT:{decap.refdes}:PWR:1:{path_index}"
                    if coupled
                    else f"SPDPI:{decap.refdes}:PWR"
                ),
                landing=landing,
                via=via,
            )
        if coupled:
            for path_index, landing in enumerate(ground_landings, start=1):
                append_landing(
                    rail=rail,
                    refdes=decap.refdes,
                    kind=DecapConnectionKind.DIRECT,
                    terminal=TerminalKind.GND,
                    path_id=(
                        f"SPDPI:CLUSTER:DIRECT:{decap.refdes}:GND:1:{path_index}"
                    ),
                    landing=landing,
                    via=via,
                )
    return tuple(blockers)


def _layerwise_artwork_bounds_um(
    project: ProjectSpec, rail: RailSpec
) -> tuple[float, float, float, float]:
    """Union every retained layer bbox for one net without decoding geometry."""

    spd_import = project.metadata.get("spd_import")
    records = (
        spd_import.get("plane_geometries", ())
        if isinstance(spd_import, Mapping)
        else ()
    )
    bounds: list[tuple[float, float, float, float]] = []
    for record in records:
        if (
            not isinstance(record, Mapping)
            or str(record.get("net", "")).casefold() != rail.net.casefold()
        ):
            continue
        raw = record.get("bbox_um")
        if (
            not isinstance(raw, Sequence)
            or isinstance(raw, (str, bytes))
            or len(raw) != 4
        ):
            continue
        candidate = tuple(float(value) for value in raw)
        if (
            all(isfinite(value) for value in candidate)
            and candidate[1] > candidate[0]
            and candidate[3] > candidate[2]
        ):
            bounds.append(candidate)  # type: ignore[arg-type]
    if not bounds:
        raise ScenarioEvaluationBuildError(
            "LAYERWISE_ARTWORK_BOUNDS_MISSING",
            f"rail {rail.rail_id!r} has no retained multilayer artwork bbox",
        )
    return (
        min(item[0] for item in bounds),
        max(item[1] for item in bounds),
        min(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def _layerwise_profile_project(
    scenario: ScenarioSpec,
    project: ProjectSpec,
    rail_ids: Sequence[str],
) -> ProjectSpec:
    """Return a transient aggregate-artwork/terminal compatibility envelope.

    Exact adjacent-gap blocks and external Device ports come from retained
    source evidence.  The shared request/template schema still requires a
    finite rectangle, so use the union of retained layer bboxes and extend it
    only as far as a real Device launch footprint requires.  In the production
    terminal-complete path this rectangle is provenance/compatibility data: no
    legacy rectangular modal term or higher-mode correction is added to Zii.
    Mounted decaps therefore never expand it.  No Device terminal is clamped,
    moved, or dropped; every extension remains recorded for audit.
    """

    selected = {item.casefold() for item in rail_ids}
    rail_by_key = {
        rail.rail_id.casefold(): rail
        for rail in project.rails
        if rail.rail_id.casefold() in selected
    }
    artwork_bounds = {
        key: _layerwise_artwork_bounds_um(project, rail)
        for key, rail in rail_by_key.items()
    }

    def rebuild_partitions(
        bounds_by_key: Mapping[str, tuple[float, float, float, float]]
    ) -> tuple[Any, ...]:
        updated = []
        for partition in project.partitions:
            domain_to_rail = {
                rail.domain: key
                for key, rail in rail_by_key.items()
                if rail.pwr_layer.casefold() == partition.layer.casefold()
                and rail.domain in partition.domain_to_cell
            }
            cell_key = {
                partition.domain_to_cell[domain]: key
                for domain, key in domain_to_rail.items()
            }
            cells = []
            for cell in partition.cells:
                key = cell_key.get(cell.cell_id)
                if key is None:
                    cells.append(cell)
                    continue
                x_min, x_max, y_min, y_max = bounds_by_key[key]
                cells.append(
                    cell.model_copy(
                        update={
                            "x_min_um": x_min,
                            "x_max_um": x_max,
                            "y_min_um": y_min,
                            "y_max_um": y_max,
                            "nominal_x_min_um": None,
                            "nominal_x_max_um": None,
                            "nominal_y_min_um": None,
                            "nominal_y_max_um": None,
                            "source_net": None,
                            "source_geometry_asset": None,
                            "source_geometry_sha256": None,
                            "source_positive_polygons_um": [],
                            "source_negative_polygons_um": [],
                            "source_positive_circles_um": [],
                            "source_negative_circles_um": [],
                            "source_primitive_order": [],
                            "source_positive_subelement_count": 0,
                            "source_negative_subelement_count": 0,
                            "solver_geometry": "active_bounds",
                        }
                    )
                )
            updated.append(partition.model_copy(update={"cells": cells}))
        return tuple(updated)

    terminal_bounds = dict(artwork_bounds)

    # Preserve a finite compatibility envelope around the exact Device launch
    # evidence. The terminal-complete global-Y solve uses the certified exposed
    # quotient port directly and does not evaluate rectangular higher modes.
    for key, rail in rail_by_key.items():
        power_pins = [
            pin
            for pin in project.pins
            if pin.kind == PinKind.DEVICE_BUMP
            and pin.terminal == TerminalKind.PWR
            and pin.net.casefold() == rail.net.casefold()
        ]
        groups = {
            (str(pin.site or "").casefold(), str(pin.bump_group or "").casefold())
            for pin in power_pins
            if pin.bump_group
        }
        related_ground = [
            pin
            for pin in project.pins
            if pin.kind == PinKind.DEVICE_BUMP
            and pin.terminal == TerminalKind.GND
            and pin.bump_group
            and (
                str(pin.site or "").casefold(),
                str(pin.bump_group or "").casefold(),
            )
            in groups
        ]
        x_min, x_max, y_min, y_max = terminal_bounds[key]
        for pin in (*power_pins, *related_ground):
            x_min = min(x_min, float(pin.x_um) - 50.0)
            x_max = max(x_max, float(pin.x_um) + 50.0)
            y_min = min(y_min, float(pin.y_um) - 50.0)
            y_max = max(y_max, float(pin.y_um) + 50.0)
        terminal_bounds[key] = (x_min, x_max, y_min, y_max)

    expansion_by_rail = {
        rail_by_key[key].rail_id: {
            "artwork_bbox_um": list(artwork_bounds[key]),
            "terminal_envelope_um": list(terminal_bounds[key]),
            "extension_um": [
                max(0.0, artwork_bounds[key][0] - terminal_bounds[key][0]),
                max(0.0, terminal_bounds[key][1] - artwork_bounds[key][1]),
                max(0.0, artwork_bounds[key][2] - terminal_bounds[key][2]),
                max(0.0, terminal_bounds[key][3] - artwork_bounds[key][3]),
            ],
        }
        for key in rail_by_key
    }
    metadata = {
        **project.metadata,
        "layerwise_solver_envelopes": expansion_by_rail,
    }
    assumptions = list(
        dict.fromkeys(
            (
                *project.assumptions,
                "Terminal-complete Layerwise uses exact retained adjacent artwork and the external Device-port global-Y Zii alone; the recorded artwork/terminal rectangle is compatibility provenance and adds no legacy modal correction.",
            )
        )
    )
    return project.model_copy(
        update={
            "partitions": list(rebuild_partitions(terminal_bounds)),
            "metadata": metadata,
            "assumptions": assumptions,
        }
    )


def preflight_evaluation_connectivity(
    scenario: ScenarioSpec,
    rail_ids: Sequence[str],
    *,
    _project: ProjectSpec | None = None,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
    attachments: Mapping[str, bytes] | None = None,
    _skip_geometry: bool = False,
    _alternate_cache: dict[tuple[str, str, str, str], _AlternateEvaluationContext] | None = None,
    _skip_source_graph_provenance: bool = False,
) -> EvaluationConnectivityPreflight:
    """Aggregate fail-closed source-connectivity blockers for selected rails.

    This intentionally follows the evaluation builder's fail-closed ordering:
    unresolved or out-of-scope source connectivity on a selected legacy rail
    blocks before population or isolation-gap materialization is considered.
    The layerwise profile defers only ``UNRESOLVED`` classifications to its
    stricter exact endpoint-island termination compiler; ``OUT_OF_SCOPE`` and
    every missing/ambiguous endpoint still fail closed.  The returned rail IDs
    are canonical project spellings and the full blocker list is retained for
    callers that need more than the compact UI text.
    """

    if evaluation_policy not in _EVALUATION_POLICIES:
        raise ScenarioEvaluationBuildError("EVALUATION_POLICY_UNKNOWN", f"unknown Evaluation geometry policy {evaluation_policy!r}")
    profile = resolve_solver_profile(solver_profile)
    project = _project if _project is not None else scenario.base_project
    canonical_rails = _canonical_rail_ids(scenario, rail_ids, _project=project)
    source_blockers = () if _skip_source_graph_provenance else _source_graph_provenance_blockers(scenario, project, canonical_rails)
    if source_blockers:
        # Provenance is a hard gate for both strict and embedded alternate
        # policies.  An approximation can never repair a stale source binding.
        return EvaluationConnectivityPreflight(canonical_rails, source_blockers)
    if evaluation_policy == EVALUATION_POLICY_EMBEDDED_ALTERNATE:
        blockers: list[EvaluationConnectivityBlocker] = []
        for rail_id in canonical_rails:
            strict = preflight_evaluation_connectivity(
                scenario, (rail_id,), _project=project,
                solver_profile=profile.key,
                evaluation_policy=EVALUATION_POLICY_STRICT,
                attachments=attachments,
                _alternate_cache=_alternate_cache,
            )
            if strict.is_clear:
                continue
            if any(
                not item.reason.startswith("TERMINAL_OUTSIDE_SELECTED_PLANE:")
                and not item.reason.startswith("SOURCE_GRAPH_PROVENANCE_REFRESH_REQUIRED:")
                for item in strict.blockers
            ):
                blockers.extend(strict.blockers)
                continue
            context = _alternate_context_for_rail(
                scenario, rail_id, attachments=attachments, _cache=_alternate_cache
            )
            if context is None:
                blockers.extend(strict.blockers)
                continue
            alternate = preflight_evaluation_connectivity(
                context.scenario, (rail_id,), _project=context.project,
                solver_profile=profile.key,
                evaluation_policy=EVALUATION_POLICY_STRICT,
                attachments=attachments,
                _skip_geometry=True,
                _skip_source_graph_provenance=True,
                _alternate_cache=_alternate_cache,
            )
            blockers.extend(alternate.blockers)
        return EvaluationConnectivityPreflight(canonical_rails, tuple(blockers))
    _require_current_shared_pad_analysis(scenario)
    analysis = scenario.connection_analysis
    if analysis is None:
        return EvaluationConnectivityPreflight(
            canonical_rails,
            tuple(
                EvaluationConnectivityBlocker(
                    rail_id=rail_id,
                    refdes="<connection analysis>",
                    kind=DecapConnectionKind.UNRESOLVED,
                    reason=(
                        "evaluation modelability: source connection analysis is "
                        "missing; reopen the verified SPD before evaluation"
                    ),
                )
                for rail_id in canonical_rails
            ),
        )
    canonical_by_key = {
        rail_id.casefold(): rail_id for rail_id in canonical_rails
    }
    rail_by_key = {item.rail_id.casefold(): item for item in project.rails}
    via_by_key = {item.template_id.casefold(): item for item in project.via_templates}
    template_ids_by_rail = _via_template_ids_by_rail(project)
    rail_order = {rail_id.casefold(): index for index, rail_id in enumerate(canonical_rails)}
    connections = {
        item.refdes.casefold(): item for item in analysis.connections.values()
    }
    mixed_witness_failures = mixed_reference_ground_witness_failures(
        scenario, _project=project
    )
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
    blocking_kinds = {DecapConnectionKind.OUT_OF_SCOPE}
    if profile != LAYERWISE_ADMITTANCE_PROFILE:
        blocking_kinds.add(DecapConnectionKind.UNRESOLVED)
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
        rail = rail_by_key.get(decap.current_rail_id.casefold())
        eligibility = next(
            (
                item
                for rail_id, item in decap.eligibility.items()
                if rail_id.casefold() == decap.current_rail_id.casefold()
            ),
            None,
        )
        if eligibility is None and rail is not None:
            eligibility = _source_direct_fallback_eligibility(
                decap,
                connection,
                rail,
                template_ids_by_rail,
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
            template = via_by_key.get(eligibility.via_template_id.casefold())
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
    # The legacy modal worker stamps finite terminals into one selected
    # rectangular plane, so a terminal outside that rectangle is a real build
    # error for that profile.  Terminal-complete Layerwise instead binds every
    # launch and mounted component to an exact retained (layer, NET) surface in
    # ``_builder_preflight_blockers``.  Applying the legacy rectangle here
    # would reject valid contacts on non-rectangular or disconnected artwork
    # before the exact surface/endpoint certificate can be consulted.
    if profile != LAYERWISE_ADMITTANCE_PROFILE:
        blockers.extend(
            _evaluation_geometry_blockers(
                scenario, canonical_rails, _project=project
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


def _comparison_original_configuration(scenario: ScenarioSpec) -> ScenarioSpec:
    """Return one all-source physical state for comparison preflight only.

    Original connectivity and terminal geometry do not depend on the selected
    rail's baseline model capture.  Preparing one source state therefore checks
    every selected Original rail in a single connectivity/geometry pass instead
    of constructing two full solver projects per rail.  Stored capture bindings
    remain authoritative; a missing capture uses the same source-model/current-
    model fallback as :meth:`ScenarioSpec.with_baseline_captures`.
    """

    captured_models: dict[str, str] = {}
    for capture in scenario.baseline_captures.values():
        for binding in capture.model_bindings:
            captured_models.setdefault(binding.refdes.casefold(), binding.model_id)
    original_decaps = tuple(
        decap.model_copy(
            update={
                "current_net": decap.source_net,
                "current_rail_id": decap.source_rail_id,
                "model_id": (
                    captured_models.get(decap.refdes.casefold())
                    or decap.source_model_id
                    or decap.model_id
                    if decap.source_mounted
                    else decap.source_model_id
                ),
                "enabled": decap.source_mounted,
                "pad_state": DecapPadState.NORMAL,
            }
        )
        for decap in scenario.decaps
    )
    return scenario.model_copy(update={"decaps": original_decaps})


def _evaluation_modelability_blockers(
    scenario: ScenarioSpec,
    canonical_rails: Sequence[str],
    *,
    _project: ProjectSpec | None = None,
) -> tuple[EvaluationConnectivityBlocker, ...]:
    """Mirror the builder's enabled-model checks without building a project."""

    selected = {rail_id.casefold(): rail_id for rail_id in canonical_rails}
    connected = {
        refdes.casefold() for refdes in scenario.electrically_connected_refdes
    }
    project = _project if _project is not None else scenario.base_project
    models = {model.model_id.casefold(): model for model in project.cap_models}
    connections = (
        {
            connection.refdes.casefold(): connection
            for connection in scenario.connection_analysis.connections.values()
        }
        if scenario.connection_analysis is not None
        else {}
    )
    blockers: list[EvaluationConnectivityBlocker] = []
    for decap in scenario.decaps:
        rail_id = selected.get(decap.current_rail_id.casefold())
        if (
            rail_id is None
            or not decap.enabled
            or decap.refdes.casefold() not in connected
        ):
            continue
        model = (
            models.get(decap.model_id.casefold())
            if decap.model_id is not None
            else None
        )
        if decap.model_id is None:
            reason = "evaluation modelability: enabled decap electrical model is missing"
        elif model is None:
            reason = (
                "evaluation modelability: electrical model "
                f"{decap.model_id!r} is absent from the model library"
            )
        elif model.footprint.casefold() != decap.footprint.casefold():
            reason = (
                "evaluation modelability: electrical model footprint "
                f"{model.footprint!r} does not match component footprint "
                f"{decap.footprint!r}"
            )
        else:
            continue
        connection = connections.get(decap.refdes.casefold())
        blockers.append(
            EvaluationConnectivityBlocker(
                rail_id=rail_id,
                refdes=decap.refdes,
                kind=(
                    connection.kind
                    if connection is not None
                    else DecapConnectionKind.DIRECT
                ),
                reason=reason,
            )
        )
    return tuple(blockers)


def _comparison_blocker_identity(
    blocker: EvaluationConnectivityBlocker,
) -> EvaluationConnectivityBlocker:
    """Return the hashable blocker identity shared by Original and Tuned."""

    return replace(blocker, configuration=None)


def _builder_preflight_blockers(
    scenario: ScenarioSpec,
    canonical_rails: Sequence[str],
    existing: Sequence[EvaluationConnectivityBlocker],
    *,
    project: ProjectSpec,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    stage_offset: int = 0,
    stage_count: int = 1,
    skip_rail_ids: frozenset[str] = frozenset(),
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
    attachments: Mapping[str, bytes] | None = None,
    _alternate_cache: dict[tuple[str, str, str, str], _AlternateEvaluationContext] | None = None,
) -> tuple[EvaluationConnectivityBlocker, ...]:
    """Dry-build structurally clear rails and convert every build failure.

    The layerwise profile also compiles its retained artwork/certificates and
    selected terminal binding here.  A project-only dry build cannot discover
    a missing attachment, a stale certificate, or a missing selected surface;
    reporting such a rail as runnable would defer a deterministic source
    failure until after the user accepted the run manifest.
    """

    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    blocked = {item.rail_id.casefold() for item in existing}
    connections = (
        {
            item.refdes.casefold(): item
            for item in scenario.connection_analysis.connections.values()
        }
        if scenario.connection_analysis is not None
        else {}
    )
    result: list[EvaluationConnectivityBlocker] = []
    design_fingerprint = scenario.design_fingerprint
    profile = resolve_solver_profile(solver_profile)
    # A v4 Layerwise scenario binding is the complete mutable board topology
    # and mounted-termination inventory.  It is independent of the selected
    # measurement rail: the binding identity covers the Scenario design,
    # source/certificate, base substrate, every capacitor model, and every Via
    # template.  Keep the first successful binding local to this one
    # Original-or-Tuned preflight pass instead of recomputing its board-scale
    # cache identity for every rail.
    #
    # Only failures raised *while creating that binding* are board-global and
    # replayed for the remaining clear rails.  Project/template/source-model
    # construction and ``with_termination_manifest`` stay outside this narrow
    # failure cache because they retain selected-rail endpoint/port checks.
    layerwise_board_binding: Any = None
    layerwise_board_binding_ready = False
    layerwise_board_binding_error: ValueError | None = None
    total = max(stage_count, 1)
    for index, rail_id in enumerate(canonical_rails):
        if cancelled():
            raise RuntimeError("evaluation preflight cancelled")
        stage = stage_offset + index
        report(
            round(stage * 100 / total),
            f"Preflighting {rail_id} solver project",
        )
        rail_key = rail_id.casefold()
        if rail_key in blocked or rail_key in skip_rail_ids:
            continue
        try:
            candidate = build_evaluation_project(
                scenario,
                evaluation_rail_id=rail_id,
                _project=project,
                _design_fingerprint=design_fingerprint,
                solver_profile=profile.key,
                evaluation_policy=evaluation_policy,
                attachments=attachments,
                _alternate_cache=_alternate_cache,
            )
            if profile == LAYERWISE_ADMITTANCE_PROFILE:
                from spd_decap_pi._core.solver.layerwise_network import (
                    build_layerwise_uniform_source_model,
                )

                template = compile_project_evaluation_template(
                    candidate,
                    rail_id,
                    terminal_complete_external_input=True,
                )

                def source_progress(value: int, message: str) -> None:
                    bounded = min(max(int(value), 0), 100)
                    report(
                        round((stage + bounded / 100.0) * 100 / total),
                        f"Preflighting {rail_id}: {message}",
                    )

                source_model = build_layerwise_uniform_source_model(
                    candidate,
                    dict(attachments or {}),
                    rail_id,
                    template,
                    progress=source_progress,
                    is_cancelled=cancelled,
                )
                from .layerwise_termination_adapter import (
                    LayerwiseScenarioTerminationFactory,
                )

                if layerwise_board_binding_error is not None:
                    raise layerwise_board_binding_error
                if not layerwise_board_binding_ready:
                    try:
                        layerwise_board_binding = (
                            LayerwiseScenarioTerminationFactory(
                                scenario=scenario,
                                project=candidate,
                                attachments=dict(attachments or {}),
                            )(source_model.substrate, template)
                        )
                    except ValueError as exc:
                        # The factory consumes the complete Scenario and the
                        # board-wide substrate/model inventories; no selected
                        # rail enters its binding identity.  A deterministic
                        # failure here therefore makes every selected global-Y
                        # measurement unmodelable.  Cache only this narrow
                        # board-global failure, never a rail proof failure.
                        layerwise_board_binding_error = exc
                        raise
                    layerwise_board_binding_ready = True
                # Binding performs the same physical-surface mapping audit as
                # the numerical service.  Requiring it here keeps the UI
                # manifest honest: a rail cannot be shown as runnable and then
                # silently fall back to a source-only layerwise solve.
                source_model.with_termination_manifest(
                    layerwise_board_binding,
                    required=True,
                )
        except ScenarioEvaluationPreflightError as exc:
            result.extend(exc.preflight.blockers)
        except ScenarioEvaluationBuildError as exc:
            connection = (
                connections.get(exc.refdes.casefold())
                if exc.refdes is not None
                else None
            )
            result.append(
                EvaluationConnectivityBlocker(
                    rail_id=rail_id,
                    refdes=exc.refdes or "<rail project>",
                    kind=(
                        connection.kind
                        if connection is not None
                        else DecapConnectionKind.UNRESOLVED
                    ),
                    reason=f"evaluation builder [{exc.code}]: {exc}",
                )
            )
        except ValueError as exc:
            # Some source-topology derivation errors intentionally carry richer
            # domain exception types than ScenarioEvaluationBuildError.  They
            # are still predictable pre-solver failures and belong in the same
            # per-rail manifest rather than escaping after user consent.
            result.append(
                EvaluationConnectivityBlocker(
                    rail_id=rail_id,
                    refdes="<rail project>",
                    kind=DecapConnectionKind.UNRESOLVED,
                    reason=(
                        "evaluation builder "
                        f"[{getattr(exc, 'code', 'PROJECT_BUILD_FAILED')}]: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
            )
    return tuple(result)


def _rail_builder_input_sha256_by_rail(
    scenario: ScenarioSpec,
    canonical_rails: Sequence[str],
    *,
    include_all_decaps: bool = False,
) -> dict[str, str]:
    """Hash mutable build inputs in one board-scale pass.

    Legacy modal rails are independent and retain their historical per-rail
    identity.  The production layer-surface network includes mounted
    *other-rail* terminations in one global Y solve, so every requested rail
    must bind the complete board decap state.  Otherwise Original preflight or
    an unchanged-result shortcut could be reused after an electrically visible
    change on another rail.
    """

    selected = {rail_id.casefold() for rail_id in canonical_rails}
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    if include_all_decaps:
        board_sha256 = sha256(
            _canonical_json(
                [
                    decap_by_key[key].model_dump(mode="json")
                    for key in sorted(decap_by_key)
                ]
            )
        ).hexdigest()
        return {rail_key: board_sha256 for rail_key in selected}
    relevant_by_rail: dict[str, set[str]] = {key: set() for key in selected}
    for key, decap in decap_by_key.items():
        rail_key = decap.current_rail_id.casefold()
        if rail_key in relevant_by_rail:
            relevant_by_rail[rail_key].add(key)
    analysis = scenario.connection_analysis
    if analysis is not None:
        for cluster in analysis.clusters:
            member_keys = {item.casefold() for item in cluster.member_refdes}
            touching = {
                decap_by_key[key].current_rail_id.casefold()
                for key in member_keys
                if key in decap_by_key
                and decap_by_key[key].current_rail_id.casefold() in selected
            }
            for rail_key in touching:
                relevant_by_rail[rail_key].update(member_keys)
    return {
        rail_key: sha256(
            _canonical_json(
                [
                    decap_by_key[key].model_dump(mode="json")
                    for key in sorted(relevant)
                ]
            )
        ).hexdigest()
        for rail_key, relevant in relevant_by_rail.items()
    }


def preflight_evaluation_comparison(
    scenario: ScenarioSpec,
    rail_ids: Sequence[str],
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
    attachments: Mapping[str, bytes] | None = None,
    _alternate_cache: dict[tuple[str, str, str, str], _AlternateEvaluationContext] | None = None,
) -> EvaluationConnectivityPreflight:
    """Gate both Original and Tuned states before baseline or solver work.

    Connectivity, finite-terminal geometry, and enabled-model assignments are
    checked for both configurations.  Identical blockers are reported once;
    state-specific blockers are explicitly labeled Original or Tuned.  Original
    geometry is prepared once for all rails.  Structurally clear rail/state
    pairs are then dry-built through the production project builder so a rail
    cannot be labeled runnable and subsequently fail before its first solve.
    """

    if evaluation_policy not in _EVALUATION_POLICIES:
        raise ScenarioEvaluationBuildError("EVALUATION_POLICY_UNKNOWN", f"unknown Evaluation geometry policy {evaluation_policy!r}")
    profile = resolve_solver_profile(solver_profile)
    alternate_cache = _alternate_cache if _alternate_cache is not None else {}
    source_project = scenario.base_project
    canonical_rails = _canonical_rail_ids(
        scenario, rail_ids, _project=source_project
    )
    # Provenance is the first public comparison gate.  Do not profile or
    # alternate-transform the project before rejecting a stale/malformed
    # source proof; those transforms can otherwise hide the authoritative
    # source-graph failure behind an artwork/modelability error.
    source_graph_blockers = _source_graph_provenance_blockers(
        scenario, source_project, canonical_rails
    )
    if source_graph_blockers:
        return EvaluationConnectivityPreflight(canonical_rails, source_graph_blockers)
    project = (
        _layerwise_profile_project(scenario, source_project, canonical_rails)
        if profile == LAYERWISE_ADMITTANCE_PROFILE
        else source_project
    )
    tuned = preflight_evaluation_connectivity(
        scenario,
        canonical_rails,
        _project=project,
        solver_profile=profile.key,
        evaluation_policy=evaluation_policy,
        attachments=attachments,
        _alternate_cache=alternate_cache,
    )
    original_scenario = _comparison_original_configuration(scenario)
    original = preflight_evaluation_connectivity(
        original_scenario,
        canonical_rails,
        _project=project,
        solver_profile=profile.key,
        evaluation_policy=evaluation_policy,
        attachments=attachments,
        _alternate_cache=alternate_cache,
    )
    tuned_items = (
        *tuned.blockers,
        *_evaluation_modelability_blockers(
            scenario, canonical_rails, _project=project
        ),
    )
    original_items = (
        *original.blockers,
        *_evaluation_modelability_blockers(
            original_scenario, canonical_rails, _project=project
        ),
    )
    stage_count = len(canonical_rails) * 2
    tuned_items = (
        *tuned_items,
        *_builder_preflight_blockers(
            scenario,
            canonical_rails,
            tuned_items,
            project=project,
            progress=progress,
            is_cancelled=is_cancelled,
            stage_count=stage_count,
            solver_profile=profile.key,
            evaluation_policy=evaluation_policy,
            attachments=attachments,
            _alternate_cache=alternate_cache,
        ),
    )
    tuned_input_sha = _rail_builder_input_sha256_by_rail(
        scenario,
        canonical_rails,
        include_all_decaps=(profile == LAYERWISE_ADMITTANCE_PROFILE),
    )
    original_input_sha = _rail_builder_input_sha256_by_rail(
        original_scenario,
        canonical_rails,
        include_all_decaps=(profile == LAYERWISE_ADMITTANCE_PROFILE),
    )
    identical_rail_keys = frozenset(
        rail_key
        for rail_key in tuned_input_sha
        if tuned_input_sha[rail_key] == original_input_sha[rail_key]
    )
    original_items = (
        *original_items,
        *(
            blocker
            for blocker in tuned_items
            if blocker.rail_id.casefold() in identical_rail_keys
        ),
    )
    original_items = (
        *original_items,
        *_builder_preflight_blockers(
            original_scenario,
            canonical_rails,
            original_items,
            project=project,
            progress=progress,
            is_cancelled=is_cancelled,
            stage_offset=len(canonical_rails),
            stage_count=stage_count,
            skip_rail_ids=identical_rail_keys,
            solver_profile=profile.key,
            evaluation_policy=evaluation_policy,
            attachments=attachments,
            _alternate_cache=alternate_cache,
        ),
    )
    tuned_by_identity = {
        _comparison_blocker_identity(item): item for item in tuned_items
    }
    original_by_identity = {
        _comparison_blocker_identity(item): item for item in original_items
    }
    merged: list[EvaluationConnectivityBlocker] = []
    for identity in set(tuned_by_identity) | set(original_by_identity):
        in_tuned = identity in tuned_by_identity
        in_original = identity in original_by_identity
        merged.append(
            identity
            if in_tuned and in_original
            else replace(
                (
                    tuned_by_identity[identity]
                    if in_tuned
                    else original_by_identity[identity]
                ),
                configuration=(
                    EvaluationRole.TUNED if in_tuned else EvaluationRole.BASELINE
                ),
            )
        )
    rail_order = {
        rail_id.casefold(): index for index, rail_id in enumerate(canonical_rails)
    }
    role_order = {
        EvaluationRole.BASELINE: 0,
        EvaluationRole.TUNED: 1,
        None: 2,
    }
    return EvaluationConnectivityPreflight(
        canonical_rails,
        tuple(
            sorted(
                merged,
                key=lambda item: (
                    rail_order[item.rail_id.casefold()],
                    role_order[item.configuration],
                    item.kind.value,
                    item.reason.casefold(),
                    item.refdes.casefold(),
                    item.path_id or "",
                ),
            )
        ),
    )


def _solver_project_fingerprint(project: ProjectSpec) -> str:
    """Hash numerical inputs plus canonical selected-pair provenance identity."""

    payload = project.model_dump(mode="json")
    metadata = payload.pop("metadata", {})
    if isinstance(metadata, dict):
        spd_import = metadata.get("spd_import")
        if isinstance(spd_import, dict):
            provenance = spd_import.get("selected_plane_pair_provenance")
            if provenance:
                # Keep this one canonical source-graph proof in the solver
                # cache identity.  Other import metadata (asset paths,
                # diagnostics, UI notes) is intentionally provenance-only and
                # must not perturb numerical cache reuse.
                payload["selected_plane_pair_provenance"] = provenance
    return sha256(_canonical_json(payload)).hexdigest()


def baseline_fallback_model_refdes(
    scenario: ScenarioSpec,
    rail_ids: Sequence[str],
    *,
    include_all_source_mounted: bool = False,
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
        and (
            include_all_source_mounted
            or item.refdes.casefold() in connected
        )
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
    try:
        profile = resolve_solver_profile(view.solver_profile_key)
    except ValueError as exc:
        raise ScenarioEvaluationCacheError(
            "cached evaluation solver profile is unknown"
        ) from exc
    policy = getattr(view, "evaluation_policy", EVALUATION_POLICY_STRICT)
    if policy not in _EVALUATION_POLICIES:
        raise ScenarioEvaluationCacheError("cached evaluation geometry policy is unknown")
    if (
        view.solver_profile_label != profile.label
        or view.solver_profile_badge != profile.badge
    ):
        raise ScenarioEvaluationCacheError(
            "cached evaluation solver profile disclosure is inconsistent"
        )
    if not isinstance(view.solver_provenance, dict):
        raise ScenarioEvaluationCacheError(
            "cached evaluation solver provenance must be an object"
        )
    if profile == LAYERWISE_ADMITTANCE_PROFILE and not view.solver_provenance:
        raise ScenarioEvaluationCacheError(
            "cached layerwise evaluation has incomplete source/compiler provenance"
        )
    if view.solver_provenance:
        if (
            view.solver_provenance.get("profile_key") != profile.key
            or view.solver_provenance.get("profile_badge") != profile.badge
            or view.solver_provenance.get("powersi_used_for_parameters") is not False
        ):
            raise ScenarioEvaluationCacheError(
                "cached evaluation solver provenance is inconsistent"
            )
        if profile.experimental and (
            view.solver_provenance.get("source_only") is not True
            or view.solver_provenance.get("validation_status")
            != "research_not_validated"
        ):
            raise ScenarioEvaluationCacheError(
                "cached research evaluation lacks source-only/validation provenance"
            )
        if profile == LAYERWISE_ADMITTANCE_PROFILE:
            provenance = view.solver_provenance

            def layerwise_hash(name: str) -> str:
                raw_value = provenance.get(name)
                if name == "terminal_surface_contact_proof_sha256" and not raw_value:
                    # Read the pre-v0.22 field only as a compatibility alias,
                    # matching the result UI's disclosure validator.
                    raw_value = provenance.get("terminal_artwork_proof_sha256")
                return str(raw_value or "").strip().casefold()

            if (
                provenance.get("source_only") is not True
                or any(
                    len(value := layerwise_hash(name)) != 64
                    or any(character not in "0123456789abcdef" for character in value)
                    for name in _LAYERWISE_PROVENANCE_HASH_FIELDS
                )
                or provenance.get("compiler_algorithm_id")
                != LAYERWISE_ADMITTANCE_PROFILE.compiler_algorithm_id
                or provenance.get("compiler_version") != LAYERWISE_COMPILER_VERSION
                or provenance.get("termination_manifest_required") is not True
                or provenance.get("static_compiler_algorithm_sha256")
                != solver_profile_static_identity_sha256(profile)
            ):
                raise ScenarioEvaluationCacheError(
                    "cached layerwise evaluation has incomplete source/compiler provenance"
                )
        if profile.experimental:
            # Experimental curves are intentionally never read from the
            # baseline cache, but this validation still protects an exported
            # attachment/result from falsely claiming a source-artwork identity.
            try:
                _evaluation_settings(
                    view.target_ohm,
                    DEFAULT_EVALUATION_MODAL_MAX_INDEX,
                    profile.key,
                    solver_provenance=view.solver_provenance,
                )
            except ScenarioEvaluationBuildError as exc:
                raise ScenarioEvaluationCacheError(
                    "cached research evaluation has an incomplete evidence identity"
                ) from exc
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
    profile_view_fields = {
        "solver_profile_key",
        "solver_profile_label",
        "solver_profile_badge",
        "solver_provenance",
        "evaluation_policy",
    }
    actual_view_fields = (
        frozenset(view_payload) if isinstance(view_payload, dict) else frozenset()
    )
    if not isinstance(view_payload, dict) or actual_view_fields not in {
        frozenset(expected_view_fields),
        frozenset(expected_view_fields - profile_view_fields),
    }:
        raise ScenarioEvaluationCacheError(
            "cached evaluation view has an incompatible schema"
        )
    if not profile_view_fields.intersection(view_payload):
        # v0.17 attachments predate explicit profile identity.  They can only
        # mean the legacy regression backend; never infer a research profile.
        view_payload = {
            **view_payload,
            "solver_profile_key": LEGACY_MODAL_PROFILE.key,
            "solver_profile_label": LEGACY_MODAL_PROFILE.label,
            "solver_profile_badge": LEGACY_MODAL_PROFILE.badge,
            "solver_provenance": {
                "profile_key": LEGACY_MODAL_PROFILE.key,
                "profile_badge": LEGACY_MODAL_PROFILE.badge,
                "status": "legacy_regression",
                "source_only": True,
                "powersi_used_for_parameters": False,
                "validation_status": "legacy_regression",
            },
        }
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
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
) -> ScenarioEvaluation | None:
    profile = resolve_solver_profile(solver_profile)
    if profile.experimental or profile == LAYERWISE_ADMITTANCE_PROFILE:
        # Evidence is compiled only inside the source-only evaluation path.
        # The layerwise result additionally owns all-board mounted
        # terminations, while the historical BaselineCapture schema freezes
        # only the selected rail's fallback models.  Reusing either result
        # before compiling that full evidence would make cache identity depend
        # on a prediction, so these baseline cache reads are intentionally off.
        return None
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
        solver_profile=solver_profile,
        evaluation_policy=evaluation_policy,
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
    profile = resolve_solver_profile(evaluation.view.solver_profile_key)
    if profile.experimental or profile == LAYERWISE_ADMITTANCE_PROFILE:
        raise ScenarioEvaluationCacheError(
            "source-compiled/all-board baseline results are intentionally not persisted or reused"
        )
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


def _eligibility_for(
    decap: ScenarioDecap,
    *,
    fallback: RailEligibility | None = None,
) -> RailEligibility:
    eligibility = decap.eligibility.get(decap.current_rail_id)
    if eligibility is None:
        folded = decap.current_rail_id.casefold()
        eligibility = next(
            (item for key, item in decap.eligibility.items() if key.casefold() == folded),
            None,
        )
    if eligibility is None:
        eligibility = fallback
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
    fallback_eligibility: RailEligibility | None = None,
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

    eligibility = _eligibility_for(decap, fallback=fallback_eligibility)
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


def _confirmed_metadata(
    scenario: ScenarioSpec,
    *,
    design_fingerprint: str | None = None,
    project: ProjectSpec | None = None,
) -> dict[str, Any]:
    base = project if project is not None else scenario.base_project
    metadata = dict(base.metadata)
    metadata.update(
        {
            "plane_pair_confirmed": True,
            "geometry_user_reviewed": False,
            "geometry_confirmation_source": "validated_read_only_spd_scenario",
            "scenario_design_fingerprint": (
                design_fingerprint or scenario.design_fingerprint
            ),
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
    _project: ProjectSpec | None = None,
    _design_fingerprint: str | None = None,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
    attachments: Mapping[str, bytes] | None = None,
    _alternate_cache: dict[tuple[str, str, str, str], _AlternateEvaluationContext] | None = None,
) -> ProjectSpec:
    """Build and validate a solver project for the current scenario state.

    When a rail is supplied, only decaps assigned to that rail are materialized.
    The inherited solver does not model inter-rail coupling, so an unmodeled
    mounted capacitor on an unrelated rail must not block the requested rail.
    """

    if evaluation_policy not in _EVALUATION_POLICIES:
        raise ScenarioEvaluationBuildError("EVALUATION_POLICY_UNKNOWN", f"unknown Evaluation geometry policy {evaluation_policy!r}")
    source_base = _project if _project is not None else scenario.base_project
    provenance_rail_ids = _canonical_rail_ids(
        scenario,
        ((evaluation_rail_id,) if evaluation_rail_id is not None else tuple(item.rail_id for item in source_base.rails)),
        _project=source_base,
    )
    source_graph_blockers = _source_graph_provenance_blockers(
        scenario, source_base, provenance_rail_ids
    )
    if source_graph_blockers:
        raise ScenarioEvaluationPreflightError(
            EvaluationConnectivityPreflight(provenance_rail_ids, source_graph_blockers)
        )
    profile = resolve_solver_profile(solver_profile)
    effective_policy = evaluation_policy
    alternate_provenance: dict[str, Any] | None = None
    if evaluation_policy == EVALUATION_POLICY_EMBEDDED_ALTERNATE and evaluation_rail_id is not None:
        strict_preflight = preflight_evaluation_connectivity(
            scenario,
            (evaluation_rail_id,),
            _project=source_base,
            solver_profile=profile.key,
            evaluation_policy=EVALUATION_POLICY_STRICT,
            attachments=attachments,
            _alternate_cache=_alternate_cache,
        )
        if strict_preflight.is_clear:
            effective_policy = EVALUATION_POLICY_STRICT
        else:
            hard = tuple(
                item for item in strict_preflight.blockers
                if not item.reason.startswith("TERMINAL_OUTSIDE_SELECTED_PLANE:")
                and not item.reason.startswith("SOURCE_GRAPH_PROVENANCE_REFRESH_REQUIRED:")
            )
            if hard:
                raise ScenarioEvaluationPreflightError(
                    EvaluationConnectivityPreflight((evaluation_rail_id,), hard)
                )
            context = _alternate_context_for_rail(
                scenario,
                evaluation_rail_id,
                attachments=attachments,
                _cache=_alternate_cache,
            )
            if context is None:
                raise ScenarioEvaluationPreflightError(strict_preflight)
            # Alternate geometry changes both the project plane pair and the
            # scenario eligibility/connection view.  Keeping only the project
            # would re-run original rail proofs against PWR2/GND2 and lose the
            # alternate template/device-pin provenance.
            scenario = context.scenario
            source_base = context.project
            alternate_provenance = context.provenance
    profile_rail_ids = (
        (evaluation_rail_id,)
        if evaluation_rail_id is not None
        else tuple(rail.rail_id for rail in source_base.rails)
    )
    base = (
        _layerwise_profile_project(scenario, source_base, profile_rail_ids)
        if profile == LAYERWISE_ADMITTANCE_PROFILE
        else source_base
    )
    rail_by_id = _lookup_casefold(base.rails, "rail_id")
    model_by_id = _lookup_casefold(base.cap_models, "model_id")
    via_by_id = _lookup_casefold(base.via_templates, "template_id")
    template_ids_by_rail = _via_template_ids_by_rail(base)

    device_pins = [item for item in base.pins if item.kind == PinKind.DEVICE_BUMP]
    if alternate_provenance is not None and evaluation_rail_id is not None:
        selected_rail = rail_by_id[evaluation_rail_id.casefold()]
        alt_template_id = str(alternate_provenance.get("alternate_template_id", ""))
        source_template_id = str(alternate_provenance.get("source_template_id", ""))
        device_pins = [
            pin.model_copy(update={"via_template_id": alt_template_id})
            if (
                pin.via_template_id
                and pin.via_template_id.casefold() == source_template_id.casefold()
                and pin.site is not None
                and pin.site.casefold() == selected_rail.site.casefold()
                and (
                    (pin.terminal == TerminalKind.PWR and pin.net.casefold() == selected_rail.net.casefold())
                    or pin.terminal == TerminalKind.GND
                )
            )
            else pin
            for pin in device_pins
        ]
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
    geometry_rail_ids = tuple(
        rail.rail_id
        for rail in base.rails
        if evaluation_rail_key is None
        or rail.rail_id.casefold() == evaluation_rail_key
    )
    # The original source graph was gated before alternate transformation.
    # Its retained proof names the source PWR/GND pair (PWR1/GND1), while the
    # derived project intentionally exposes the alternate PWR2/GND2 rail; do
    # not revalidate the original proof against that transformed rail.
    if alternate_provenance is None:
        source_graph_blockers = _source_graph_provenance_blockers(
            scenario, base, geometry_rail_ids
        )
        if source_graph_blockers:
            raise ScenarioEvaluationPreflightError(
                EvaluationConnectivityPreflight(geometry_rail_ids, source_graph_blockers)
            )
    # Only the rectangular legacy modal builder consumes this geometry gate.
    # Layerwise performs the stronger exact retained-surface/endpoint binding
    # when its source model and termination manifest are compiled.
    if profile != LAYERWISE_ADMITTANCE_PROFILE and effective_policy == EVALUATION_POLICY_STRICT:
        geometry_blockers = _evaluation_geometry_blockers(
            scenario, geometry_rail_ids, _project=base
        )
        if geometry_blockers:
            raise ScenarioEvaluationPreflightError(
                EvaluationConnectivityPreflight(geometry_rail_ids, geometry_blockers)
            )
    elif profile != LAYERWISE_ADMITTANCE_PROFILE and effective_policy == EVALUATION_POLICY_EMBEDDED_ALTERNATE:
        # Alternate is permitted only for finite geometry; all source graph,
        # mixed-reference, and modelability gates above remain authoritative.
        geometry_blockers = tuple(
            item for item in _evaluation_geometry_blockers(
                scenario, geometry_rail_ids, _project=base
            ) if not item.reason.startswith("TERMINAL_OUTSIDE_SELECTED_PLANE:")
        )
        if geometry_blockers:
            raise ScenarioEvaluationPreflightError(
                EvaluationConnectivityPreflight(geometry_rail_ids, geometry_blockers)
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
    mixed_witness_failures = mixed_reference_ground_witness_failures(
        scenario, _project=base
    )
    for rail in base.rails:
        if rail.rail_id.casefold() not in selected_mixed_rail_keys:
            continue
        reason = mixed_witness_failures.get(rail.rail_id)
        if reason is not None:
            raise ScenarioEvaluationBuildError(
                "MIXED_REFERENCE_GND_REACHABILITY_REQUIRED",
                reason,
            )
    if profile == LAYERWISE_ADMITTANCE_PROFILE:
        # All mounted decaps, including the selected rail, are physical loads
        # owned by the exact endpoint-island termination manifest.  Creating
        # legacy modal placements here would both re-run the old surface-under-
        # every-landing classifier and stamp the same capacitor again after the
        # global Kron reduction.  Keep only Device launches in the transient
        # project; model/via libraries and the full Scenario remain available
        # to the manifest compiler.
        partitions = [
            item.model_copy(update={"confirmed": True}) for item in base.partitions
        ]
        payload = base.model_dump(mode="json")
        payload.update(
            {
                "pins": [
                    item.model_dump(mode="json")
                    for item in base.pins
                    if item.kind == PinKind.DEVICE_BUMP
                ],
                "topology_maps": [],
                "shared_pad_clusters": [],
                "placements": [],
                "partitions": [item.model_dump(mode="json") for item in partitions],
                "assumptions": _scenario_assumptions(base),
                "metadata": _confirmed_metadata(
                    scenario,
                    design_fingerprint=_design_fingerprint,
                    project=base,
                ),
            }
        )
        try:
            return ProjectSpec.model_validate(payload)
        except ValueError as exc:
            raise ScenarioEvaluationBuildError(
                "PROJECT_VALIDATION_FAILED",
                f"transient layerwise evaluation project is invalid: {exc}",
            ) from exc
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
        if path_count > MAX_BATCHED_SHARED_PAD_VIA_PATHS:
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_CLUSTER_TOO_LARGE",
                f"shared-pad cluster {cluster_id!r} has {path_count:,} unique "
                "PWR/GND via paths; exact-batched supported limit is "
                f"{MAX_BATCHED_SHARED_PAD_VIA_PATHS:,}",
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
        exact_batch_eligible = (
            len(power_components) == 1
            and len(ground_components) == 1
        )
        if path_count > MAX_SHARED_PAD_VIA_PATHS and not exact_batch_eligible:
            raise ScenarioEvaluationBuildError(
                "SHARED_PAD_CLUSTER_TOO_LARGE",
                f"shared-pad cluster {cluster_id!r} has {path_count:,} unique "
                "PWR/GND via paths and requires a dense terminal model; "
                f"supported dense limit is {MAX_SHARED_PAD_VIA_PATHS:,}",
            )
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
        if evaluation_rail_key is not None and not any(
            item.current_rail_id.casefold() == evaluation_rail_key
            for item in members
        ):
            # A one-rail evaluation cannot consume an unrelated anchored
            # cluster.  Avoid deriving its full current topology (and avoid
            # letting a defect on another rail block the selected rail); a
            # selected member still takes the exact fail-closed path below.
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
        direct_rail = rail_by_id.get(decap.current_rail_id.casefold())
        fallback_eligibility = (
            _source_direct_fallback_eligibility(
                decap,
                connection,
                direct_rail,
                template_ids_by_rail,
            )
            if direct_rail is not None
            else None
        )
        rail, _eligibility, model, via = _validated_assignment(
            decap,
            rail_by_id=rail_by_id,
            model_by_id=model_by_id,
            via_by_id=via_by_id,
            fallback_eligibility=fallback_eligibility,
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
            "metadata": _confirmed_metadata(
                scenario,
                design_fingerprint=_design_fingerprint,
                project=base,
            ),
        }
    )
    if alternate_provenance is not None:
        metadata = dict(payload["metadata"])
        metadata["evaluation_alternate_pair_provenance"] = dict(alternate_provenance)
        payload["metadata"] = metadata
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
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
    _alternate_cache: dict[tuple[str, str, str, str], _AlternateEvaluationContext] | None = None,
) -> WorkspaceState:
    """Return an isolated workspace for evaluation; source scenario stays immutable."""

    project = build_evaluation_project(
        scenario,
        evaluation_rail_id=evaluation_rail_id,
        solver_profile=solver_profile,
        evaluation_policy=evaluation_policy,
        attachments=attachments,
        _alternate_cache=_alternate_cache,
    )
    termination_factory = None
    if resolve_solver_profile(solver_profile) == LAYERWISE_ADMITTANCE_PROFILE:
        from .layerwise_termination_adapter import (
            LayerwiseScenarioTerminationFactory,
        )

        termination_factory = LayerwiseScenarioTerminationFactory(
            scenario=scenario,
            project=project,
            attachments=dict(attachments or {}),
        )
    return WorkspaceState(
        project=project,
        attachments=dict(attachments or {}),
        layerwise_termination_factory=termination_factory,
    )


def _with_layerwise_mounted_view_counts(
    scenario: ScenarioSpec,
    rail_id: str,
    view: EvaluationView,
) -> EvaluationView:
    """Replace cleared legacy-placement counts with bound termination counts.

    Layerwise deliberately removes ``ProjectSpec.placements`` to avoid stamping
    each capacitor twice.  Therefore its UI counts must come from the exact
    solve-bound termination provenance, not from that transient empty list.
    """

    if (
        resolve_solver_profile(view.solver_profile_key)
        != LAYERWISE_ADMITTANCE_PROFILE
    ):
        return view

    raw_cap_count = view.solver_provenance.get(
        "termination_active_selected_rail_cluster_count"
    )
    if (
        not isinstance(raw_cap_count, int)
        or isinstance(raw_cap_count, bool)
        or raw_cap_count < 0
    ):
        raise ScenarioEvaluationBuildError(
            "LAYERWISE_MOUNTED_COUNT_PROVENANCE_MISSING",
            "layerwise solve did not prove the selected-rail mounted-cap count",
        )

    rail_key = rail_id.casefold()
    mounted = tuple(
        item
        for item in scenario.decaps
        if item.enabled
        and item.pad_state != DecapPadState.ISOLATION_GAP
        and item.current_rail_id.casefold() == rail_key
    )
    if raw_cap_count != len(mounted):
        raise ScenarioEvaluationBuildError(
            "LAYERWISE_MOUNTED_COUNT_MISMATCH",
            f"bound termination manifest reports {raw_cap_count} mounted cap(s) "
            f"for rail {rail_id!r}, but the evaluated Scenario requires "
            f"{len(mounted)}",
        )

    model_keys: set[str] = set()
    for item in mounted:
        model_id = str(item.model_id or "").strip()
        if not model_id:
            raise ScenarioEvaluationBuildError(
                "LAYERWISE_MOUNTED_MODEL_MISSING",
                "enabled layerwise termination has no capacitor model",
                refdes=item.refdes,
            )
        model_keys.add(model_id.casefold())
    return replace(view, cap_count=raw_cap_count, model_count=len(model_keys))


def evaluate_scenario(
    scenario: ScenarioSpec,
    rail_id: str,
    target_ohm: float | None = None,
    modal_max_index: int = DEFAULT_EVALUATION_MODAL_MAX_INDEX,
    *,
    attachments: Mapping[str, bytes] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
    _alternate_cache: dict[tuple[str, str, str, str], _AlternateEvaluationContext] | None = None,
) -> ScenarioEvaluation:
    """Evaluate one rail and bind the view to deterministic scenario identity."""

    if evaluation_policy not in _EVALUATION_POLICIES:
        raise ScenarioEvaluationBuildError("EVALUATION_POLICY_UNKNOWN", f"unknown Evaluation geometry policy {evaluation_policy!r}")
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
        solver_profile=solver_profile,
        evaluation_policy=evaluation_policy,
        _alternate_cache=_alternate_cache,
    )
    with scoped_blas_threads():
        solved_view = evaluation_services.evaluate_workspace(
            state,
            canonical_rail,
            target_ohm,
            modal_max_index,
            progress=progress,
            is_cancelled=is_cancelled,
            solver_profile=solver_profile,
        )
    view = _with_layerwise_mounted_view_counts(
        scenario,
        canonical_rail,
        solved_view,
    )
    actual_policy = (
        EVALUATION_POLICY_EMBEDDED_ALTERNATE
        if state.project.metadata.get("evaluation_alternate_pair_provenance")
        else EVALUATION_POLICY_STRICT
    )
    view.evaluation_policy = actual_policy
    if actual_policy == EVALUATION_POLICY_EMBEDDED_ALTERNATE:
        view.confidence = "LOW"
        view.confidence_note = (
            f"{view.confidence_note} | Embedded alternate plane-pair approximation; "
            "missing source vertical path proof; not exact sign-off."
        )
        view.assumptions = [
            *view.assumptions,
            "Missing source vertical landing path is approximated from retained adjacent PWR/pure-GND geometry; result is LOW confidence and not exact sign-off.",
        ]
    if view is not solved_view:
        state.last_evaluation = view
        state.evaluation_history = [
            view if item is solved_view else item for item in state.evaluation_history
        ]
    result_key = ScenarioResultKey.from_settings(
        design_fingerprint=scenario.design_fingerprint,
        rail_id=canonical_rail,
        settings=_evaluation_settings(
            target_ohm,
            modal_max_index,
            solver_profile,
            solver_provenance=view.solver_provenance,
            evaluation_policy=actual_policy,
        ),
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
    solver_profile: str = DEFAULT_SOLVER_PROFILE_KEY,
    evaluation_policy: str = EVALUATION_POLICY_STRICT,
) -> ScenarioEvaluationBatch:
    """Evaluate Original and Tuned configurations for selected PWR rails.

    Every requested baseline/current project is preflighted before the first
    solver call.  Results and new baseline attachments are returned atomically;
    the caller commits nothing when this function raises or is cancelled.
    """

    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    if evaluation_policy not in _EVALUATION_POLICIES:
        raise ScenarioEvaluationBuildError("EVALUATION_POLICY_UNKNOWN", f"unknown Evaluation geometry policy {evaluation_policy!r}")
    profile = resolve_solver_profile(solver_profile)
    alternate_cache: dict[tuple[str, str, str, str], _AlternateEvaluationContext] = {}
    canonical_rails = _canonical_rail_ids(scenario, rail_ids)
    preflight_span = 5
    solve_span = 100 - preflight_span

    def repeat_preflight_progress(value: int, message: str) -> None:
        bounded = min(max(int(value), 0), 100)
        report(
            round(bounded * preflight_span / 100),
            f"Rechecking worker-side Original/Tuned preflight: {message}",
        )

    report(0, "Rechecking worker-side Original/Tuned preflight")
    connectivity = preflight_evaluation_comparison(
        scenario,
        canonical_rails,
        progress=repeat_preflight_progress,
        is_cancelled=cancelled,
        solver_profile=profile.key,
        evaluation_policy=evaluation_policy,
        attachments=dict(attachments or {}),
        _alternate_cache=alternate_cache,
    )
    if not connectivity.is_clear:
        raise ScenarioEvaluationPreflightError(connectivity)
    capture_rails = (
        tuple(item.rail_id for item in scenario.base_project.rails)
        if profile == LAYERWISE_ADMITTANCE_PROFILE
        else canonical_rails
    )
    prepared = scenario.with_baseline_captures(
        capture_rails,
        include_all_source_mounted=(
            profile == LAYERWISE_ADMITTANCE_PROFILE
        ),
    )
    working_attachments = _validated_scenario_attachments(prepared, attachments)
    if profile == LAYERWISE_ADMITTANCE_PROFILE:
        board_original = _comparison_original_configuration(prepared)
        baseline_scenarios = {
            rail_id: board_original for rail_id in canonical_rails
        }
    else:
        baseline_scenarios = {
            rail_id: prepared.original_configuration(rail_id)
            for rail_id in canonical_rails
        }

    report(preflight_span, "Preparing Original and Tuned rail projects")
    baseline_project_fingerprints: dict[str, str] = {}
    tuned_project_fingerprints: dict[str, str] = {}
    effective_policy_by_rail: dict[str, str] = {}
    for rail_id in canonical_rails:
        if cancelled():
            raise RuntimeError("evaluation cancelled")
        baseline_project = build_evaluation_project(
            baseline_scenarios[rail_id],
            evaluation_rail_id=rail_id,
            solver_profile=profile.key,
            evaluation_policy=evaluation_policy,
            attachments=working_attachments,
            _alternate_cache=alternate_cache,
        )
        tuned_project = build_evaluation_project(
            prepared,
            evaluation_rail_id=rail_id,
            solver_profile=profile.key,
            evaluation_policy=evaluation_policy,
            attachments=working_attachments,
            _alternate_cache=alternate_cache,
        )
        baseline_project_fingerprints[rail_id] = _solver_project_fingerprint(
            baseline_project
        )
        tuned_project_fingerprints[rail_id] = _solver_project_fingerprint(
            tuned_project
        )
        effective_policy_by_rail[rail_id] = (
            EVALUATION_POLICY_EMBEDDED_ALTERNATE
            if (
                baseline_project.metadata.get("evaluation_alternate_pair_provenance")
                or tuned_project.metadata.get("evaluation_alternate_pair_provenance")
            )
            else EVALUATION_POLICY_STRICT
        )

    total_stages = max(len(canonical_rails) * 2, 1)
    completed_stages = 0
    comparisons: list[RailComparison] = []
    persistent_change = prepared.baseline_captures != scenario.baseline_captures

    def stage_progress(stage_label: str) -> ProgressCallback:
        stage_start = completed_stages

        def update(value: int, message: str) -> None:
            bounded = min(max(int(value), 0), 100)
            overall = preflight_span + round(
                (stage_start + bounded / 100.0) * solve_span / total_stages
            )
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
            (
                baseline_scenario.design_fingerprint
                == prepared.design_fingerprint
            )
            if profile == LAYERWISE_ADMITTANCE_PROFILE
            else (
                baseline_project_fingerprints[rail_id]
                == tuned_project_fingerprints[rail_id]
            )
        )
        baseline = _load_baseline_evaluation(
            prepared,
            working_attachments,
            rail_id,
            target_ohm=target_ohm,
            modal_max_index=modal_max_index,
            solver_profile=solver_profile,
            evaluation_policy=effective_policy_by_rail[rail_id],
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
                solver_profile=solver_profile,
                evaluation_policy=evaluation_policy,
                _alternate_cache=alternate_cache,
            )
            baseline = (
                evaluated_baseline
                if profile == LAYERWISE_ADMITTANCE_PROFILE
                else replace(
                    evaluated_baseline,
                    result_key=(
                        _result_key_from_view(
                            capture.evaluation_input_sha256,
                            rail_id,
                            target_ohm=target_ohm,
                            modal_max_index=modal_max_index,
                            view=evaluated_baseline.view,
                            evaluation_policy=effective_policy_by_rail[rail_id],
                        )
                        if profile.experimental
                        else _expected_result_key(
                            capture.evaluation_input_sha256,
                            rail_id,
                            target_ohm=target_ohm,
                            modal_max_index=modal_max_index,
                            solver_profile=solver_profile,
                            evaluation_policy=effective_policy_by_rail[rail_id],
                        )
                    ),
                )
            )
            if (
                not profile.experimental
                and profile != LAYERWISE_ADMITTANCE_PROFILE
            ):
                prepared, working_attachments = _cache_baseline_evaluation(
                    prepared, working_attachments, rail_id, baseline
                )
                persistent_change = True
        else:
            report(
                preflight_span
                + round((completed_stages + 1) * solve_span / total_stages),
                f"{rail_id} Original: using saved baseline",
            )
        completed_stages += 1

        if cancelled():
            raise RuntimeError("evaluation cancelled")
        if configuration_unchanged:
            tuned = replace(
                baseline,
                result_key=(
                    _result_key_from_view(
                        prepared.design_fingerprint,
                        rail_id,
                        target_ohm=target_ohm,
                        modal_max_index=modal_max_index,
                        view=baseline.view,
                        evaluation_policy=effective_policy_by_rail[rail_id],
                    )
                    if profile.experimental
                    else _expected_result_key(
                        prepared.design_fingerprint,
                        rail_id,
                        target_ohm=target_ohm,
                        modal_max_index=modal_max_index,
                        solver_profile=solver_profile,
                        evaluation_policy=effective_policy_by_rail[rail_id],
                    )
                ),
                scenario_revision=prepared.revision,
            )
            report(
                preflight_span
                + round((completed_stages + 1) * solve_span / total_stages),
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
                solver_profile=solver_profile,
                evaluation_policy=evaluation_policy,
                _alternate_cache=alternate_cache,
            )
        completed_stages += 1
        # A preparatory project can be strict-clear while the corresponding
        # worker build resolves the opposite side through the embedded pair
        # (and vice versa).  Harmonize only the persisted result-key policy
        # after observing both actual views; each view keeps its own policy and
        # confidence disclosure.
        if profile != LAYERWISE_ADMITTANCE_PROFILE:
            effective_policy = (
                EVALUATION_POLICY_EMBEDDED_ALTERNATE
                if (
                    getattr(baseline.view, "evaluation_policy", evaluation_policy)
                    == EVALUATION_POLICY_EMBEDDED_ALTERNATE
                    or getattr(tuned.view, "evaluation_policy", evaluation_policy)
                    == EVALUATION_POLICY_EMBEDDED_ALTERNATE
                )
                else EVALUATION_POLICY_STRICT
            )
            effective_policy_by_rail[rail_id] = effective_policy
            if profile.experimental:
                baseline_key = _result_key_from_view(
                    capture.evaluation_input_sha256,
                    rail_id,
                    target_ohm=target_ohm,
                    modal_max_index=modal_max_index,
                    view=baseline.view,
                    evaluation_policy=effective_policy,
                )
                tuned_key = _result_key_from_view(
                    prepared.design_fingerprint,
                    rail_id,
                    target_ohm=target_ohm,
                    modal_max_index=modal_max_index,
                    view=tuned.view,
                    evaluation_policy=effective_policy,
                )
            else:
                baseline_key = _expected_result_key(
                    capture.evaluation_input_sha256,
                    rail_id,
                    target_ohm=target_ohm,
                    modal_max_index=modal_max_index,
                    solver_profile=solver_profile,
                    evaluation_policy=effective_policy,
                )
                tuned_key = _expected_result_key(
                    prepared.design_fingerprint,
                    rail_id,
                    target_ohm=target_ohm,
                    modal_max_index=modal_max_index,
                    solver_profile=solver_profile,
                    evaluation_policy=effective_policy,
                )
            baseline = replace(baseline, result_key=baseline_key)
            tuned = replace(tuned, result_key=tuned_key)
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
        solver_profile=evaluation.view.solver_profile_key,
        evaluation_policy=getattr(evaluation.view, "evaluation_policy", EVALUATION_POLICY_STRICT),
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
    "EVALUATION_POLICY_STRICT",
    "EVALUATION_POLICY_EMBEDDED_ALTERNATE",
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
    "preflight_evaluation_comparison",
    "preflight_evaluation_connectivity",
    "rehydrate_scenario_evaluation",
]
