"""Pure, atomic edit services for validated SPD decap scenarios.

GUI callers use these functions instead of changing individual decaps in a
loop.  Every operation preflights its complete selection and returns either one
fully validated scenario revision or raises without mutating the input model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .scenario import (
    DecapConnectionKind,
    DecapPadState,
    RailEligibility,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioSpec,
    SharedPadCluster,
    SharedPadClusterState,
    SharedPadCurrentComponent,
    SharedPadPowerViaConflict,
    SharedPadActiveShortError,
    SharedPadRailAliasConflictError,
    derive_shared_pad_current_components,
    shared_pad_component_eligibility,
)


class ScenarioEditError(ValueError):
    """Actionable failure raised before an electrical scenario edit commits."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        refdes: str | None = None,
        cluster_id: str | None = None,
        missing_refdes: tuple[str, ...] = (),
        invalid_islands: tuple["InvalidSharedPadIsland", ...] = (),
        shared_power_via_conflicts: tuple[SharedPadPowerViaConflict, ...] = (),
        ineligible_refdes: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.refdes = refdes
        self.cluster_id = cluster_id
        self.missing_refdes = missing_refdes
        self.invalid_islands = invalid_islands
        self.shared_power_via_conflicts = shared_power_via_conflicts
        self.ineligible_refdes = ineligible_refdes
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class IncompleteClusterSelection:
    cluster_id: str
    selected_refdes: tuple[str, ...]
    missing_refdes: tuple[str, ...]
    required_refdes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BlockedDecapSelection:
    refdes: str
    kind: DecapConnectionKind | None
    reason: str


@dataclass(frozen=True, slots=True)
class InvalidSharedPadIsland:
    """Actionable post-edit component that cannot be committed."""

    cluster_id: str
    rail_id: str
    net: str
    member_refdes: tuple[str, ...]
    power_via_ids: tuple[str, ...]
    reason: str
    missing_refdes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProposedRailAssignmentAnalysis:
    """Whole-cluster result of one explicit partial PWR edit or restore."""

    selected_refdes: tuple[str, ...]
    rail_id: str | None
    components: tuple[SharedPadCurrentComponent, ...]
    invalid_islands: tuple[InvalidSharedPadIsland, ...]
    shared_power_via_conflicts: tuple[SharedPadPowerViaConflict, ...]
    ineligible_refdes: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not (
            self.invalid_islands
            or self.shared_power_via_conflicts
            or self.ineligible_refdes
        )

    @property
    def missing_refdes(self) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for island in self.invalid_islands:
            for refdes in island.missing_refdes:
                key = refdes.casefold()
                if key not in seen:
                    seen.add(key)
                    result.append(refdes)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class ClusterSelectionAnalysis:
    """Selection coverage and fail-closed PWR-edit state."""

    selected_refdes: tuple[str, ...]
    complete_cluster_ids: tuple[str, ...]
    incomplete_clusters: tuple[IncompleteClusterSelection, ...]
    blocked_decaps: tuple[BlockedDecapSelection, ...]
    connection_analysis_available: bool

    @property
    def pwr_editable(self) -> bool:
        return bool(
            self.selected_refdes
            and self.connection_analysis_available
            and not self.blocked_decaps
        )

    @property
    def missing_refdes(self) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for cluster in self.incomplete_clusters:
            for refdes in cluster.missing_refdes:
                key = refdes.casefold()
                if key not in seen:
                    seen.add(key)
                    result.append(refdes)
        return tuple(result)


def _canonical_selection(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
) -> tuple[str, ...]:
    requested = {
        str(refdes).strip().casefold()
        for refdes in selected_refdes
        if str(refdes).strip()
    }
    available = {item.refdes.casefold() for item in scenario.decaps}
    unknown = requested - available
    if unknown:
        raise ScenarioEditError(
            "SELECTION_UNKNOWN",
            "selection contains unknown REFDES values: "
            + ", ".join(sorted(unknown)),
        )
    return tuple(
        item.refdes for item in scenario.decaps if item.refdes.casefold() in requested
    )


def _analysis_indexes(
    scenario: ScenarioSpec,
) -> tuple[
    dict[str, ScenarioDecapConnection],
    dict[str, SharedPadCluster],
]:
    analysis = scenario.connection_analysis
    if analysis is None:
        return {}, {}
    return (
        {
            item.refdes.casefold(): item
            for item in analysis.connections.values()
        },
        {item.cluster_id.casefold(): item for item in analysis.clusters},
    )


def analyze_cluster_selection(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
) -> ClusterSelectionAnalysis:
    """Return exact cluster coverage without silently expanding selection."""

    selected = _canonical_selection(scenario, selected_refdes)
    if scenario.connection_analysis is None:
        return ClusterSelectionAnalysis(
            selected_refdes=selected,
            complete_cluster_ids=(),
            incomplete_clusters=(),
            blocked_decaps=tuple(
                BlockedDecapSelection(
                    refdes,
                    None,
                    "source shared-pad connectivity has not been analyzed",
                )
                for refdes in selected
            ),
            connection_analysis_available=False,
        )

    connection_by_refdes, cluster_by_id = _analysis_indexes(scenario)
    touched_cluster_keys: set[str] = set()
    blocked: list[BlockedDecapSelection] = []
    for refdes in selected:
        connection = connection_by_refdes[refdes.casefold()]
        if connection.cluster_id is not None:
            touched_cluster_keys.add(connection.cluster_id.casefold())
        if connection.kind in {
            DecapConnectionKind.FLOATING_DUMMY,
            DecapConnectionKind.UNRESOLVED,
            DecapConnectionKind.OUT_OF_SCOPE,
        }:
            blocked.append(
                BlockedDecapSelection(
                    refdes,
                    connection.kind,
                    connection.reason
                    or {
                        DecapConnectionKind.FLOATING_DUMMY: (
                            "via-less dummy has no connected shared-pad anchor"
                        ),
                        DecapConnectionKind.UNRESOLVED: (
                            "shared-pad connectivity is unresolved"
                        ),
                        DecapConnectionKind.OUT_OF_SCOPE: (
                            "decap connection is outside the supported topology"
                        ),
                    }[connection.kind],
                )
            )

    complete: list[str] = []
    selected_keys = {item.casefold() for item in selected}
    for cluster in scenario.connection_analysis.clusters:
        key = cluster.cluster_id.casefold()
        if key not in touched_cluster_keys:
            continue
        member_keys = {item.casefold() for item in cluster.member_refdes}
        if cluster.state == SharedPadClusterState.ANCHORED:
            if member_keys.issubset(selected_keys):
                complete.append(cluster.cluster_id)
            continue
        if not any(item.refdes.casefold() in member_keys for item in blocked):
            # Scenario validation normally makes every FLOATING/UNRESOLVED
            # member blocked.  Keep the selection result fail-closed even if a
            # future connection kind is added without updating the loop above.
            first = cluster.member_refdes[0]
            blocked.append(
                BlockedDecapSelection(
                    first,
                    connection_by_refdes[first.casefold()].kind,
                    f"cluster {cluster.cluster_id} is not electrically anchored",
                )
            )
    return ClusterSelectionAnalysis(
        selected_refdes=selected,
        complete_cluster_ids=tuple(complete),
        # V2 permits explicit partial selection.  Invalid resulting islands
        # are candidate-rail dependent and are reported by assignment preflight.
        incomplete_clusters=(),
        blocked_decaps=tuple(blocked),
        connection_analysis_available=True,
    )


def selection_with_required_cluster_members(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
) -> tuple[str, ...]:
    """Return an optional safe whole-source-cluster expansion shortcut."""

    selected = _canonical_selection(scenario, selected_refdes)
    requested = {item.casefold() for item in selected}
    connection_by_refdes, cluster_by_id = _analysis_indexes(scenario)
    for refdes in selected:
        connection = connection_by_refdes.get(refdes.casefold())
        if connection is None or connection.cluster_id is None:
            continue
        cluster = cluster_by_id.get(connection.cluster_id.casefold())
        if cluster is not None:
            requested.update(item.casefold() for item in cluster.member_refdes)
    return tuple(
        item.refdes for item in scenario.decaps if item.refdes.casefold() in requested
    )


def _require_pwr_editable(state: ClusterSelectionAnalysis) -> None:
    if not state.selected_refdes:
        raise ScenarioEditError("SELECTION_EMPTY", "select at least one decap")
    if not state.connection_analysis_available:
        raise ScenarioEditError(
            "CONNECTION_ANALYSIS_REQUIRED",
            "source shared-pad connectivity must be analyzed before changing PWR",
        )
    if state.blocked_decaps:
        blocked = state.blocked_decaps[0]
        kind = blocked.kind
        code = {
            DecapConnectionKind.FLOATING_DUMMY: "FLOATING_DUMMY",
            DecapConnectionKind.UNRESOLVED: "CONNECTION_UNRESOLVED",
            DecapConnectionKind.OUT_OF_SCOPE: "CONNECTION_OUT_OF_SCOPE",
        }.get(kind, "PWR_EDIT_BLOCKED")
        raise ScenarioEditError(
            code,
            f"{blocked.refdes}: {blocked.reason}",
            refdes=blocked.refdes,
        )


def _analyze_proposed_state(
    scenario: ScenarioSpec,
    selected_refdes: tuple[str, ...],
    replacements: dict[str, ScenarioDecap],
    *,
    requested_rail_id: str | None,
) -> ProposedRailAssignmentAnalysis:
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    decap_by_key.update(replacements)
    connection_by_refdes, cluster_by_id = _analysis_indexes(scenario)
    selected_keys = {item.casefold() for item in selected_refdes}
    touched_cluster_keys = {
        connection.cluster_id.casefold()
        for key in selected_keys
        if (connection := connection_by_refdes[key]).cluster_id is not None
    }
    components: list[SharedPadCurrentComponent] = []
    islands: list[InvalidSharedPadIsland] = []
    conflicts: list[SharedPadPowerViaConflict] = []
    for cluster_key in sorted(touched_cluster_keys):
        cluster = cluster_by_id[cluster_key]
        safe_expansion = tuple(
            refdes
            for refdes in cluster.member_refdes
            if refdes.casefold() not in selected_keys
        )
        try:
            derivation = derive_shared_pad_current_components(
                cluster,
                {
                    refdes.casefold(): decap_by_key[refdes.casefold()]
                    for refdes in cluster.member_refdes
                },
                {
                    refdes.casefold(): connection_by_refdes[refdes.casefold()]
                    for refdes in cluster.member_refdes
                },
                analysis_version=(
                    scenario.connection_analysis.version
                    if scenario.connection_analysis is not None
                    else None
                ),
            )
        except SharedPadActiveShortError as exc:
            islands.append(
                InvalidSharedPadIsland(
                    cluster_id=exc.cluster_id,
                    rail_id="",
                    net=f"{exc.left_net} / {exc.right_net}",
                    member_refdes=(exc.left_refdes, exc.right_refdes),
                    power_via_ids=(),
                    reason="ACTIVE_PAD_SHORT",
                    missing_refdes=safe_expansion,
                )
            )
            continue
        except SharedPadRailAliasConflictError as exc:
            member_keys = {item.casefold() for item in exc.member_refdes}
            power_via_ids = tuple(
                sorted(
                    {
                        landing.via_id
                        for key in member_keys
                        for landing in connection_by_refdes[key].power_vias
                    },
                    key=str.casefold,
                )
            )
            islands.append(
                InvalidSharedPadIsland(
                    cluster_id=exc.cluster_id,
                    rail_id=" / ".join(exc.rail_ids),
                    net=exc.current_net,
                    member_refdes=exc.member_refdes,
                    power_via_ids=power_via_ids,
                    reason="MIXED_RAIL_IDS_ON_SAME_NET",
                    missing_refdes=safe_expansion,
                )
            )
            continue
        components.extend(derivation.components)
        conflicts.extend(derivation.shared_power_via_conflicts)
        for component in derivation.components:
            via_ids = tuple(item.via_id for item in component.power_vias)
            if not via_ids:
                islands.append(
                    InvalidSharedPadIsland(
                        cluster_id=cluster.cluster_id,
                        rail_id=component.current_rail_id,
                        net=component.current_net,
                        member_refdes=component.member_refdes,
                        power_via_ids=(),
                        reason="NO_PWR_VIA_ANCHOR",
                        missing_refdes=safe_expansion,
                    )
                )
                continue
            allowed = shared_pad_component_eligibility(
                cluster, component, require_all_vias=False
            )
            if component.current_rail_id.casefold() not in {
                item.rail_id.casefold() for item in allowed.values()
            }:
                islands.append(
                    InvalidSharedPadIsland(
                        cluster_id=cluster.cluster_id,
                        rail_id=component.current_rail_id,
                        net=component.current_net,
                        member_refdes=component.member_refdes,
                        power_via_ids=via_ids,
                        reason="PWR_VIA_RAIL_INELIGIBLE",
                    )
                )

    ineligible_direct: list[str] = []
    for refdes in selected_refdes:
        key = refdes.casefold()
        connection = connection_by_refdes[key]
        if connection.cluster_id is not None:
            continue
        decap = decap_by_key[key]
        eligibility = next(
            (
                item
                for item in decap.eligibility.values()
                if item.rail_id.casefold() == decap.current_rail_id.casefold()
            ),
            None,
        )
        if eligibility is None or not eligibility.allowed:
            ineligible_direct.append(decap.refdes)
    return ProposedRailAssignmentAnalysis(
        selected_refdes=selected_refdes,
        rail_id=requested_rail_id,
        components=tuple(components),
        invalid_islands=tuple(islands),
        shared_power_via_conflicts=tuple(conflicts),
        ineligible_refdes=tuple(ineligible_direct),
    )


def _raise_invalid_proposal(analysis: ProposedRailAssignmentAnalysis) -> None:
    if analysis.shared_power_via_conflicts:
        conflict = analysis.shared_power_via_conflicts[0]
        raise ScenarioEditError(
            "SHARED_PAD_PWR_VIA_SPLIT",
            f"physical PWR Via {conflict.via_id!r} would serve more than one "
            f"current-NET component: {conflict.component_member_refdes}",
            cluster_id=conflict.cluster_id,
            missing_refdes=analysis.missing_refdes,
            invalid_islands=analysis.invalid_islands,
            shared_power_via_conflicts=analysis.shared_power_via_conflicts,
            ineligible_refdes=analysis.ineligible_refdes,
        )
    if analysis.invalid_islands:
        island = analysis.invalid_islands[0]
        if island.reason == "NO_PWR_VIA_ANCHOR":
            code = "SHARED_PAD_DUMMY_ISLAND"
            message = (
                f"shared-pad members {island.member_refdes} would form a "
                "dummy-only NET island without a physical PWR Via anchor"
            )
        elif island.reason == "ACTIVE_PAD_SHORT":
            code = "SHARED_PAD_ACTIVE_SHORT"
            message = (
                f"active shared-pad neighbors {island.member_refdes} carry "
                f"different NETs {island.net!r}; one intervening pad cell must "
                "be committed as an isolation gap"
            )
        elif island.reason == "MIXED_RAIL_IDS_ON_SAME_NET":
            code = "SHARED_PAD_RAIL_ALIAS_MIX"
            message = (
                f"shared-pad members {island.member_refdes} remain physically "
                f"shorted on NET {island.net!r} and cannot use mixed rail IDs "
                f"{island.rail_id!r}; select the complete same-NET segment"
            )
        else:
            code = "RAIL_INELIGIBLE_AT_PWR_VIA"
            message = (
                f"rail {island.rail_id!r} is not eligible at every physical "
                f"PWR Via serving shared-pad component {island.member_refdes}"
            )
        raise ScenarioEditError(
            code,
            message,
            cluster_id=island.cluster_id,
            missing_refdes=analysis.missing_refdes,
            invalid_islands=analysis.invalid_islands,
            shared_power_via_conflicts=analysis.shared_power_via_conflicts,
            ineligible_refdes=analysis.ineligible_refdes,
        )
    if analysis.ineligible_refdes:
        refdes = analysis.ineligible_refdes[0]
        raise ScenarioEditError(
            "RAIL_INELIGIBLE",
            f"{refdes}: PWR rail is not eligible at the physical Via landing",
            refdes=refdes,
            ineligible_refdes=analysis.ineligible_refdes,
        )


def analyze_rail_assignment(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
    rail_id: str,
) -> ProposedRailAssignmentAnalysis:
    """Simulate an explicit partial edit against every resulting source cluster."""

    state = analyze_cluster_selection(scenario, selected_refdes)
    _require_pwr_editable(state)
    rail = next(
        (
            item
            for item in scenario.base_project.rails
            if item.rail_id.casefold() == str(rail_id).strip().casefold()
        ),
        None,
    )
    if rail is None:
        raise ScenarioEditError("RAIL_UNKNOWN", f"unknown PWR rail {rail_id!r}")
    selected_keys = {item.casefold() for item in state.selected_refdes}
    replacements = {
        item.refdes.casefold(): ScenarioDecap.model_validate(
            {
                **item.model_dump(mode="python"),
                "current_rail_id": rail.rail_id,
                "current_net": rail.net,
            }
        )
        for item in scenario.decaps
        if item.refdes.casefold() in selected_keys
    }
    return _analyze_proposed_state(
        scenario,
        state.selected_refdes,
        replacements,
        requested_rail_id=rail.rail_id,
    )


def assignment_options_for_selection(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
) -> tuple[str, ...]:
    """Return canonical common rail IDs for one PWR-editable selection."""

    state = analyze_cluster_selection(scenario, selected_refdes)
    _require_pwr_editable(state)
    return tuple(
        rail.rail_id
        for rail in scenario.base_project.rails
        if analyze_rail_assignment(
            scenario, state.selected_refdes, rail.rail_id
        ).valid
    )


def _replace_selected_decaps(
    scenario: ScenarioSpec,
    selected_refdes: tuple[str, ...],
    replacements: dict[str, ScenarioDecap],
) -> ScenarioSpec:
    selected_keys = {item.casefold() for item in selected_refdes}
    decaps = [
        replacements.get(item.refdes.casefold(), item)
        if item.refdes.casefold() in selected_keys
        else item
        for item in scenario.decaps
    ]
    if decaps == scenario.decaps:
        return scenario
    return ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "decaps": decaps,
            "revision": scenario.revision + 1,
        }
    )


def assign_rail_atomic(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
    rail_id: str,
) -> ScenarioSpec:
    """Assign one rail after validating every resulting source-graph component."""

    state = analyze_cluster_selection(scenario, selected_refdes)
    _require_pwr_editable(state)
    rail = next(
        (
            item
            for item in scenario.base_project.rails
            if item.rail_id.casefold() == str(rail_id).strip().casefold()
        ),
        None,
    )
    if rail is None:
        raise ScenarioEditError("RAIL_UNKNOWN", f"unknown PWR rail {rail_id!r}")
    proposal = analyze_rail_assignment(
        scenario, state.selected_refdes, rail.rail_id
    )
    if not proposal.valid:
        _raise_invalid_proposal(proposal)

    replacements: dict[str, ScenarioDecap] = {}
    selected_keys = {item.casefold() for item in state.selected_refdes}
    for decap in scenario.decaps:
        key = decap.refdes.casefold()
        if key not in selected_keys:
            continue
        if (
            decap.current_rail_id.casefold() == rail.rail_id.casefold()
            and decap.current_net.casefold() == rail.net.casefold()
        ):
            continue
        replacements[key] = ScenarioDecap.model_validate(
            {
                **decap.model_dump(mode="python"),
                "current_rail_id": rail.rail_id,
                "current_net": rail.net,
            }
        )
    return _replace_selected_decaps(
        scenario,
        state.selected_refdes,
        replacements,
    )


def _batch_rail_replacements(
    scenario: ScenarioSpec,
    assignments: Mapping[str, str],
) -> tuple[tuple[str, ...], dict[str, ScenarioDecap]]:
    """Canonicalize a heterogeneous REFDES-to-rail edit without committing it."""

    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    rail_by_key = {
        item.rail_id.casefold(): item for item in scenario.base_project.rails
    }
    requested: dict[str, tuple[str, str]] = {}
    for raw_refdes, raw_rail_id in assignments.items():
        refdes = str(raw_refdes).strip()
        rail_id = str(raw_rail_id).strip()
        refdes_key = refdes.casefold()
        if not refdes or refdes_key in requested:
            raise ScenarioEditError(
                "SELECTION_DUPLICATE",
                "batch PWR assignments require unique, nonblank REFDES keys",
            )
        requested[refdes_key] = (refdes, rail_id)

    unknown_refdes = sorted(set(requested) - set(decap_by_key))
    if unknown_refdes:
        raise ScenarioEditError(
            "SELECTION_UNKNOWN",
            "selection contains unknown REFDES values: "
            + ", ".join(unknown_refdes),
        )

    replacements: dict[str, ScenarioDecap] = {}
    for refdes_key, (_raw_refdes, raw_rail_id) in requested.items():
        rail = rail_by_key.get(raw_rail_id.casefold())
        if rail is None:
            raise ScenarioEditError(
                "RAIL_UNKNOWN", f"unknown PWR rail {raw_rail_id!r}"
            )
        decap = decap_by_key[refdes_key]
        if (
            decap.current_rail_id.casefold() == rail.rail_id.casefold()
            and decap.current_net.casefold() == rail.net.casefold()
        ):
            continue
        replacements[refdes_key] = ScenarioDecap.model_validate(
            {
                **decap.model_dump(mode="python"),
                "current_rail_id": rail.rail_id,
                "current_net": rail.net,
            }
        )

    selected = tuple(
        item.refdes for item in scenario.decaps if item.refdes.casefold() in replacements
    )
    return selected, replacements


def analyze_rail_assignments(
    scenario: ScenarioSpec,
    assignments: Mapping[str, str],
) -> ProposedRailAssignmentAnalysis:
    """Preflight multiple heterogeneous rail edits against one final state."""

    selected, replacements = _batch_rail_replacements(scenario, assignments)
    if not selected:
        return ProposedRailAssignmentAnalysis(
            selected_refdes=(),
            rail_id=None,
            components=(),
            invalid_islands=(),
            shared_power_via_conflicts=(),
            ineligible_refdes=(),
        )
    state = analyze_cluster_selection(scenario, selected)
    _require_pwr_editable(state)
    return _analyze_proposed_state(
        scenario,
        state.selected_refdes,
        replacements,
        requested_rail_id=None,
    )


def assign_rails_atomic(
    scenario: ScenarioSpec,
    assignments: Mapping[str, str],
) -> ScenarioSpec:
    """Commit heterogeneous rail assignments in one validated revision."""

    selected, replacements = _batch_rail_replacements(scenario, assignments)
    if not selected:
        return scenario
    state = analyze_cluster_selection(scenario, selected)
    _require_pwr_editable(state)
    proposal = _analyze_proposed_state(
        scenario,
        state.selected_refdes,
        replacements,
        requested_rail_id=None,
    )
    if not proposal.valid:
        _raise_invalid_proposal(proposal)
    return _replace_selected_decaps(
        scenario,
        state.selected_refdes,
        replacements,
    )


def assign_rails_and_isolation_gaps_atomic(
    scenario: ScenarioSpec,
    assignments: Mapping[str, str],
    isolation_gap_refdes: Iterable[str],
) -> ScenarioSpec:
    """Commit PWR relabels and physical separator-pad removals in one revision."""

    assigned, replacements = _batch_rail_replacements(scenario, assignments)
    gaps = _canonical_selection(scenario, isolation_gap_refdes)
    assigned_keys = {item.casefold() for item in assigned}
    gap_keys = {item.casefold() for item in gaps}
    overlap = assigned_keys & gap_keys
    if overlap:
        raise ScenarioEditError(
            "ISOLATION_GAP_ASSIGNED",
            f"an isolation-gap cell cannot also receive a rail: {sorted(overlap)}",
        )
    connection_by_refdes, cluster_by_id = _analysis_indexes(scenario)
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    for key in gap_keys:
        decap = decap_by_key[key]
        connection = connection_by_refdes.get(key)
        cluster = (
            cluster_by_id.get(connection.cluster_id.casefold())
            if connection is not None and connection.cluster_id is not None
            else None
        )
        if cluster is None or cluster.state != SharedPadClusterState.ANCHORED:
            raise ScenarioEditError(
                "ISOLATION_GAP_NOT_SHARED",
                f"{decap.refdes}: only an anchored shared-pad member can become "
                "an isolation gap",
                refdes=decap.refdes,
            )
        if key not in {
            item.casefold() for item in cluster.isolation_gap_refdes
        }:
            raise ScenarioEditError(
                "ISOLATION_GAP_NOT_PROVEN",
                f"{decap.refdes}: source TOP copper does not prove a collinear "
                "path that can be split by removing this pad cell",
                refdes=decap.refdes,
            )
        replacements[key] = ScenarioDecap.model_validate(
            {
                **decap.model_dump(mode="python"),
                "enabled": False,
                "pad_state": DecapPadState.ISOLATION_GAP,
            }
        )
    selected_keys = assigned_keys | gap_keys
    selected = tuple(
        item.refdes
        for item in scenario.decaps
        if item.refdes.casefold() in selected_keys
    )
    if not selected:
        return scenario
    state = analyze_cluster_selection(scenario, selected)
    _require_pwr_editable(state)
    proposal = _analyze_proposed_state(
        scenario,
        state.selected_refdes,
        replacements,
        requested_rail_id=None,
    )
    if not proposal.valid:
        _raise_invalid_proposal(proposal)
    return _replace_selected_decaps(
        scenario,
        state.selected_refdes,
        replacements,
    )


def assign_model_atomic(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
    model_id: str | None,
) -> ScenarioSpec:
    """Assign or clear a component model without cluster expansion."""

    selected = _canonical_selection(scenario, selected_refdes)
    if not selected:
        raise ScenarioEditError("SELECTION_EMPTY", "select at least one decap")
    model = None
    if model_id is not None:
        model = next(
            (
                item
                for item in scenario.base_project.cap_models
                if item.model_id.casefold() == str(model_id).strip().casefold()
            ),
            None,
        )
        if model is None:
            raise ScenarioEditError("MODEL_UNKNOWN", f"unknown model {model_id!r}")
    selected_keys = {item.casefold() for item in selected}
    replacements: dict[str, ScenarioDecap] = {}
    for decap in scenario.decaps:
        key = decap.refdes.casefold()
        if key not in selected_keys:
            continue
        if model is not None and model.footprint.casefold() != decap.footprint.casefold():
            raise ScenarioEditError(
                "FOOTPRINT_MISMATCH",
                f"{decap.refdes}: model footprint {model.footprint!r} does not "
                f"match {decap.footprint!r}",
                refdes=decap.refdes,
            )
        canonical_model_id = model.model_id if model is not None else None
        if decap.model_id == canonical_model_id:
            continue
        replacements[key] = ScenarioDecap.model_validate(
            {**decap.model_dump(mode="python"), "model_id": canonical_model_id}
        )
    return _replace_selected_decaps(scenario, selected, replacements)


def _casefold_eligibility(
    eligibility: dict[str, RailEligibility], rail_id: str
) -> RailEligibility | None:
    key = rail_id.casefold()
    return next(
        (
            item
            for item in eligibility.values()
            if item.rail_id.casefold() == key
        ),
        None,
    )


def _current_assignment_allowed(
    scenario: ScenarioSpec,
    decap: ScenarioDecap,
    connection_by_refdes: dict[str, ScenarioDecapConnection],
    cluster_by_id: dict[str, SharedPadCluster],
) -> bool:
    connection = connection_by_refdes.get(decap.refdes.casefold())
    if connection is None:
        eligibility = _casefold_eligibility(
            decap.eligibility, decap.current_rail_id
        )
        return eligibility is not None and eligibility.allowed
    if connection.kind in {
        DecapConnectionKind.FLOATING_DUMMY,
        DecapConnectionKind.UNRESOLVED,
        DecapConnectionKind.OUT_OF_SCOPE,
    }:
        # Population/model edits remain component-local even when the part has
        # no usable solver connection.
        return True
    if connection.cluster_id is None:
        eligibility = _casefold_eligibility(
            decap.eligibility, decap.current_rail_id
        )
    else:
        cluster = cluster_by_id[connection.cluster_id.casefold()]
        scenario_decaps_by_key = {
            item.refdes.casefold(): item for item in scenario.decaps
        }
        derivation = derive_shared_pad_current_components(
            cluster,
            {
                refdes.casefold(): scenario_decaps_by_key[refdes.casefold()]
                for refdes in cluster.member_refdes
            },
            {
                refdes.casefold(): connection_by_refdes[refdes.casefold()]
                for refdes in cluster.member_refdes
            },
            analysis_version=(
                scenario.connection_analysis.version
                if scenario.connection_analysis is not None
                else None
            ),
        )
        component = next(
            (
                item
                for item in derivation.components
                if decap.refdes.casefold()
                in {refdes.casefold() for refdes in item.member_refdes}
            ),
            None,
        )
        if component is None or derivation.shared_power_via_conflicts:
            return False
        eligibility = _casefold_eligibility(
            shared_pad_component_eligibility(
                cluster, component, require_all_vias=False
            ),
            decap.current_rail_id,
        )
    return eligibility is not None and eligibility.allowed


def set_enabled_atomic(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
    enabled: bool,
) -> ScenarioSpec:
    """Set per-component population state without expanding pad clusters."""

    selected = _canonical_selection(scenario, selected_refdes)
    if not selected:
        raise ScenarioEditError("SELECTION_EMPTY", "select at least one decap")
    selected_keys = {item.casefold() for item in selected}
    models = {
        item.model_id.casefold(): item for item in scenario.base_project.cap_models
    }
    connection_by_refdes, cluster_by_id = _analysis_indexes(scenario)
    replacements: dict[str, ScenarioDecap] = {}
    for decap in scenario.decaps:
        key = decap.refdes.casefold()
        if key not in selected_keys:
            continue
        if enabled:
            if decap.pad_state == DecapPadState.ISOLATION_GAP:
                raise ScenarioEditError(
                    "ISOLATION_GAP_RESTORE_REQUIRED",
                    f"{decap.refdes}: restore the source pad state before enabling",
                    refdes=decap.refdes,
                )
            if not _current_assignment_allowed(
                scenario, decap, connection_by_refdes, cluster_by_id
            ):
                raise ScenarioEditError(
                    "CURRENT_RAIL_INELIGIBLE",
                    f"{decap.refdes}: current PWR assignment is not eligible",
                    refdes=decap.refdes,
                )
            model = models.get((decap.model_id or "").casefold())
            if model is None:
                raise ScenarioEditError(
                    "MODEL_REQUIRED",
                    f"{decap.refdes}: assign a decap model before enabling",
                    refdes=decap.refdes,
                )
            if model.footprint.casefold() != decap.footprint.casefold():
                raise ScenarioEditError(
                    "FOOTPRINT_MISMATCH",
                    f"{decap.refdes}: model footprint mismatch",
                    refdes=decap.refdes,
                )
        if decap.enabled == bool(enabled):
            continue
        replacements[key] = ScenarioDecap.model_validate(
            {**decap.model_dump(mode="python"), "enabled": bool(enabled)}
        )
    return _replace_selected_decaps(scenario, selected, replacements)


def _source_model_id(scenario: ScenarioSpec, decap: ScenarioDecap) -> str | None:
    if decap.source_model_id is not None:
        return decap.source_model_id
    capture = next(
        (
            item
            for key, item in scenario.baseline_captures.items()
            if key.casefold() == decap.source_rail_id.casefold()
        ),
        None,
    )
    if capture is None:
        return None
    binding = next(
        (
            item
            for item in capture.model_bindings
            if item.refdes.casefold() == decap.refdes.casefold()
        ),
        None,
    )
    return binding.model_id if binding is not None else None


def _restore_replacements(
    scenario: ScenarioSpec,
    selected_refdes: tuple[str, ...],
) -> dict[str, ScenarioDecap]:
    selected_keys = {item.casefold() for item in selected_refdes}
    return {
        decap.refdes.casefold(): ScenarioDecap.model_validate(
            {
                **decap.model_dump(mode="python"),
                "current_net": decap.source_net,
                "current_rail_id": decap.source_rail_id,
                "model_id": _source_model_id(scenario, decap),
                "enabled": decap.source_mounted,
                "pad_state": DecapPadState.NORMAL,
            }
        )
        for decap in scenario.decaps
        if decap.refdes.casefold() in selected_keys
    }


def analyze_restore_selection(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
) -> ProposedRailAssignmentAnalysis:
    """Cheap GUI preflight using the same whole-cluster rule as Restore."""

    state = analyze_cluster_selection(scenario, selected_refdes)
    _require_pwr_editable(state)
    return _analyze_proposed_state(
        scenario,
        state.selected_refdes,
        _restore_replacements(scenario, state.selected_refdes),
        requested_rail_id=None,
    )


def restore_source_atomic(
    scenario: ScenarioSpec,
    selected_refdes: Iterable[str],
) -> ScenarioSpec:
    """Restore explicit members when the resulting source graph stays valid."""

    state = analyze_cluster_selection(scenario, selected_refdes)
    _require_pwr_editable(state)
    proposed = _restore_replacements(scenario, state.selected_refdes)
    analysis = _analyze_proposed_state(
        scenario,
        state.selected_refdes,
        proposed,
        requested_rail_id=None,
    )
    if not analysis.valid:
        _raise_invalid_proposal(analysis)
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    replacements = {
        key: restored
        for key, restored in proposed.items()
        if restored != decap_by_key[key]
    }
    return _replace_selected_decaps(
        scenario,
        state.selected_refdes,
        replacements,
    )


__all__ = [
    "BlockedDecapSelection",
    "ClusterSelectionAnalysis",
    "IncompleteClusterSelection",
    "InvalidSharedPadIsland",
    "ProposedRailAssignmentAnalysis",
    "ScenarioEditError",
    "analyze_cluster_selection",
    "analyze_rail_assignment",
    "analyze_rail_assignments",
    "analyze_restore_selection",
    "assign_model_atomic",
    "assign_rail_atomic",
    "assign_rails_atomic",
    "assign_rails_and_isolation_gaps_atomic",
    "assignment_options_for_selection",
    "restore_source_atomic",
    "selection_with_required_cluster_members",
    "set_enabled_atomic",
]
