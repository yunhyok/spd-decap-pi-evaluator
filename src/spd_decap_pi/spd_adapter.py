"""Read-only PowerSI SPD import for editable decap PI scenarios."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from spd_decap_pi._core.domain import PinKind, ProjectSpec
from spd_decap_pi._core.io.spd import (
    SpdCapInstance,
    SpdImportError,
    analyze_spd,
    recover_spd_via_paths,
)
from spd_decap_pi._core.services import build_spd_import_plan, create_workspace_state

from .eligibility import EligibilityResult, PlaneEligibilityIndex
from .scenario import (
    DecapConnectionKind,
    RailEligibility,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSide,
    ScenarioSpec,
    ScenarioViaLanding,
    ScenarioViaPathEvidence,
    ScenarioViaSegment,
    SHARED_PAD_ANALYSIS_VERSION,
    SharedPadCluster,
    SharedPadClusterState,
    SharedPadConnectionAnalysis,
    SourceIdentity,
)


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]

_NET_PALETTE = (
    "#2E86DE",
    "#E67E22",
    "#27AE60",
    "#8E44AD",
    "#C0392B",
    "#16A085",
    "#D4AC0D",
    "#5D6D7E",
    "#E84393",
    "#00A8FF",
)

@dataclass(frozen=True, slots=True)
class ImportStageTimings:
    """Non-persistent wall-clock timings for one read-only SPD import."""

    analyze_s: float
    plan_s: float
    index_s: float
    recovery_s: float
    eligibility_s: float
    finalize_s: float
    total_s: float


@dataclass(frozen=True, slots=True)
class ScenarioImport:
    scenario: ScenarioSpec
    attachments: dict[str, bytes]
    diagnostics: tuple[Any, ...]
    timings: ImportStageTimings


def _top_conductor_name(project: ProjectSpec) -> str | None:
    return next(
        (layer.name for layer in project.stackup_layers if layer.is_conductor), None
    )


def _instance_side(instance: SpdCapInstance, top_layer: str | None) -> ScenarioSide:
    attach = (instance.attach_layer or "").casefold().replace("_", "")
    start = (instance.start_layer or "").casefold()
    if attach in {"topair", "airtop"}:
        return ScenarioSide.TOP
    if top_layer and start == top_layer.casefold():
        return ScenarioSide.TOP
    if "bottom" in start or attach in {"bottomair", "airbottom"}:
        return ScenarioSide.BOTTOM
    if "top" in start:
        return ScenarioSide.TOP
    return ScenarioSide.UNKNOWN


def _template_for_rail(project: ProjectSpec, rail_id: str) -> str | None:
    rail = next((item for item in project.rails if item.rail_id == rail_id), None)
    if rail is None:
        return None
    provenance = project.metadata.get("spd_via_template_provenance", {})
    if isinstance(provenance, dict):
        for template_id, raw in provenance.items():
            if isinstance(raw, dict) and str(raw.get("rail_id", "")).casefold() == rail_id.casefold():
                return str(template_id)
    return next(
        (
            item.template_id
            for item in project.via_templates
            if item.pwr_reference_layer.casefold() == rail.pwr_layer.casefold()
            and item.gnd_reference_layer.casefold() == rail.gnd_layer.casefold()
        ),
        None,
    )


def _rail_for_instance(project: ProjectSpec, instance: SpdCapInstance):
    candidates = [
        item
        for item in project.rails
        if item.net.casefold() == instance.power_net.casefold()
    ]
    if instance.site:
        exact = next(
            (
                item
                for item in candidates
                if item.site.casefold() == instance.site.casefold()
            ),
            None,
        )
        if exact is not None:
            return exact
    return candidates[0] if candidates else None


def _eligibility_by_rail(
    eligibility_index: PlaneEligibilityIndex,
    instance: SpdCapInstance,
    rail_choices_by_pair: dict[tuple[str, str, str], tuple[tuple[Any, str], ...]],
) -> dict[str, RailEligibility]:
    return _eligibility_at_point(
        eligibility_index,
        instance.power_x_um,
        instance.power_y_um,
        rail_choices_by_pair,
    )


def _eligibility_at_point(
    eligibility_index: PlaneEligibilityIndex,
    x_um: float,
    y_um: float,
    rail_choices_by_pair: dict[tuple[str, str, str], tuple[tuple[Any, str], ...]],
) -> dict[str, RailEligibility]:
    exact: EligibilityResult = eligibility_index.query(x_um, y_um)
    result: dict[str, RailEligibility] = {}
    for plane in exact.eligible:
        pair_key = (
            plane.net.casefold(),
            plane.pwr_layer.casefold(),
            plane.gnd_layer.casefold(),
        )
        for rail, template_id in rail_choices_by_pair.get(pair_key, ()):
            # Persist only actionable choices.  A production SPD can expose
            # hundreds of plane rails; materializing every negative rail/decap
            # Cartesian pair makes import and .spdpi files needlessly large.
            # Missing means ineligible, and exact boundary points remain absent.
            result[rail.rail_id] = RailEligibility(
                rail_id=rail.rail_id,
                net=rail.net,
                pwr_layer=rail.pwr_layer,
                gnd_layer=rail.gnd_layer,
                via_template_id=template_id,
                allowed=True,
            )
    return result


def _eligibility_for_via_landing(
    eligibility_index: PlaneEligibilityIndex,
    landing: ScenarioViaLanding,
    rail_choices_by_pair: dict[tuple[str, str, str], tuple[tuple[Any, str], ...]],
) -> dict[str, RailEligibility]:
    """Use a source-proven target-plane landing, or name the legacy fallback.

    A recovered Via path is authoritative for every rail whose PWR reference
    layer matches its target.  If it cannot prove a target landing, the existing
    TOP-endpoint eligibility/template behavior remains an explicit compatibility
    fallback rather than an inferred intermediate-layer geometry.
    """

    result = _eligibility_at_point(
        eligibility_index,
        landing.x_um,
        landing.y_um,
        rail_choices_by_pair,
    )
    rail_layer_by_key = {
        rail.rail_id.casefold(): rail.pwr_layer.casefold()
        for choices in rail_choices_by_pair.values()
        for rail, _template_id in choices
    }
    for evidence in landing.path_evidence:
        target_key = evidence.target_layer.casefold()
        target_rail_keys = {
            rail_key
            for rail_key, pwr_layer in rail_layer_by_key.items()
            if pwr_layer == target_key
        }
        for rail_id in tuple(result):
            if rail_id.casefold() in target_rail_keys:
                result.pop(rail_id)
        at_target = _eligibility_at_point(
            eligibility_index,
            evidence.x_um,
            evidence.y_um,
            rail_choices_by_pair,
        )
        result.update(
            {
                rail_id: item
                for rail_id, item in at_target.items()
                if item.pwr_layer.casefold() == target_key
            }
        )
    return result


def _common_eligibility_at_landings(
    eligibility_index: PlaneEligibilityIndex,
    landings: tuple[ScenarioViaLanding, ...],
    rail_choices_by_pair: dict[tuple[str, str, str], tuple[tuple[Any, str], ...]],
) -> dict[str, RailEligibility]:
    return _common_eligibility_maps(
        tuple(
            _eligibility_for_via_landing(
                eligibility_index, landing, rail_choices_by_pair
            )
            for landing in landings
        )
    )


def _scenario_via_landing(
    landing: Any,
    recovery: Any,
) -> ScenarioViaLanding:
    """Convert ephemeral path recovery output into compact persisted evidence."""

    evidence = tuple(
        ScenarioViaPathEvidence(
            target_layer=item.target_layer,
            target_node_id=item.target_node_id,
            target_padstack=item.target_padstack,
            target_pad_kind=item.target_pad_kind,
            target_pad_width_um=item.target_pad_width_um,
            target_pad_height_um=item.target_pad_height_um,
            x_um=item.target_x_um,
            y_um=item.target_y_um,
            segments=tuple(
                ScenarioViaSegment(
                    via_id=segment.via_id,
                    padstack=segment.padstack,
                    drill_diameter_um=segment.drill_diameter_um,
                    start_layer=segment.start_layer,
                    end_layer=segment.end_layer,
                    length_um=segment.length_um,
                    end_x_um=segment.end_x_um,
                    end_y_um=segment.end_y_um,
                    rotation_degrees=segment.rotation_degrees,
                    padstack_material=segment.padstack_material,
                )
                for segment in item.segments
            ),
        )
        for item in recovery.evidence_by_via.get(landing.via_id.casefold(), ())
    )
    return ScenarioViaLanding(
        via_id=landing.via_id,
        net=landing.net,
        endpoint_node_id=landing.endpoint_node_id,
        x_um=landing.x_um,
        y_um=landing.y_um,
        padstack=landing.padstack,
        rotation_degrees=landing.rotation_degrees,
        path_evidence=evidence,
    )


def _via_target_layers_by_net(project: ProjectSpec) -> dict[str, tuple[str, ...]]:
    """Target layers requested from recovery for every selected terminal net."""

    result: dict[str, set[str]] = {}
    ground_nets = {item.casefold() for item in project.gnd_aliases}
    for rail in project.rails:
        result.setdefault(rail.net.casefold(), set()).add(rail.pwr_layer)
        gnd_layer = next(
            (item for item in project.stackup_layers if item.name == rail.gnd_layer),
            None,
        )
        if gnd_layer is not None:
            ground_nets.update(item.casefold() for item in gnd_layer.pwr_nets)
            for net in ground_nets:
                result.setdefault(net, set()).add(rail.gnd_layer)
    return {
        net: tuple(sorted(layers, key=str.casefold))
        for net, layers in sorted(result.items())
    }


def _common_eligibility_at_points(
    eligibility_index: PlaneEligibilityIndex,
    points_um: tuple[tuple[float, float], ...],
    rail_choices_by_pair: dict[tuple[str, str, str], tuple[tuple[Any, str], ...]],
) -> dict[str, RailEligibility]:
    """Return rails present under every unique physical PWR-via landing."""

    unique_points = tuple(dict.fromkeys(points_um))
    if not unique_points:
        return {}
    return _common_eligibility_maps(
        tuple(
            _eligibility_at_point(
                eligibility_index,
                x_um,
                y_um,
                rail_choices_by_pair,
            )
            for x_um, y_um in unique_points
        )
    )


def _common_eligibility_maps(
    eligibility_maps: tuple[dict[str, RailEligibility], ...],
) -> dict[str, RailEligibility]:
    """Intersect allowed rail identities without pairing physical vias."""

    common: dict[str, RailEligibility] | None = None
    for at_point in eligibility_maps:
        by_key = {
            item.rail_id.casefold(): item
            for item in at_point.values()
            if item.allowed
        }
        if common is None:
            common = by_key
        else:
            common = {
                key: value for key, value in common.items() if key in by_key
            }
        if not common:
            return {}
    if common is None:
        return {}
    return {
        item.rail_id: item
        for item in sorted(common.values(), key=lambda value: value.rail_id.casefold())
    }


def _rail_choice_index(
    project: ProjectSpec,
) -> dict[tuple[str, str, str], tuple[tuple[Any, str], ...]]:
    choices: dict[tuple[str, str, str], list[tuple[Any, str]]] = {}
    for rail in project.rails:
        template_id = _template_for_rail(project, rail.rail_id)
        if not template_id:
            continue
        key = (
            rail.net.casefold(),
            rail.pwr_layer.casefold(),
            rail.gnd_layer.casefold(),
        )
        choices.setdefault(key, []).append((rail, template_id))
    return {key: tuple(value) for key, value in choices.items()}


def _normalized_base_project(project: ProjectSpec) -> ProjectSpec:
    """Strip source placements while keeping solver-ready SPD provenance."""

    metadata = dict(project.metadata)
    metadata.update(
        {
            "plane_pair_confirmed": True,
            "geometry_confirmation_source": "read_only_spd_scenario",
            "geometry_user_reviewed": False,
            "spd_scenario": True,
        }
    )
    assumptions = list(project.assumptions)
    exact_note = (
        "Decap PWR assignment uses the actual PWR pad and exact ordered SPD "
        "plane primitives; primitive boundaries are fail-closed"
    )
    if exact_note not in assumptions:
        assumptions.append(exact_note)
    payload = project.model_dump(mode="json")
    payload.update(
        {
            "pins": [
                item.model_dump(mode="json")
                for item in project.pins
                if item.kind == PinKind.DEVICE_BUMP
            ],
            "topology_maps": [],
            "shared_pad_clusters": [],
            "placements": [],
            "partitions": [
                item.model_copy(update={"confirmed": True}).model_dump(mode="json")
                for item in project.partitions
            ],
            "metadata": metadata,
            "assumptions": assumptions,
        }
    )
    return ProjectSpec.model_validate(payload)


def import_spd_scenario(
    path: str | Path,
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> ScenarioImport:
    """Create a sibling-app scenario without ever modifying the source SPD."""

    source_path = Path(path)
    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    state = create_workspace_state()
    total_started = perf_counter()

    def parser_progress(value: int, message: str) -> None:
        report(round(max(0, min(100, value)) * 0.82), message)

    analyze_started = perf_counter()
    analysis = analyze_spd(
        source_path,
        frequencies_hz=(
            state.project.frequency.start_hz,
            state.project.frequency.stop_hz,
        ),
        gnd_aliases=state.project.gnd_aliases,
        progress=parser_progress,
        is_cancelled=cancelled,
        scope="decap_scenario",
    )
    analyze_s = perf_counter() - analyze_started
    if cancelled():
        raise RuntimeError("SPD scenario import cancelled")
    report(
        83,
        f"Parsed the SPD in {analyze_s:.1f}s; normalizing exact plane geometry",
    )
    plan_started = perf_counter()
    plan = build_spd_import_plan(state.project, analysis, source_path)
    plan_s = perf_counter() - plan_started
    blocking = [
        item
        for item in plan.diagnostics
        if str(getattr(item, "severity", "")).casefold() == "error"
    ]
    if blocking or not plan.can_apply:
        details = "; ".join(
            f"{getattr(item, 'code', 'SPD_ERROR')}: {getattr(item, 'message', item)}"
            for item in blocking[:5]
        )
        raise SpdImportError(
            "SPD scenario import is blocked because exact evaluation geometry is "
            f"incomplete or invalid. {details or 'Import plan is not applicable.'}"
        )
    report(
        85,
        f"Normalized exact plane geometry in {plan_s:.1f}s; building spatial index",
    )
    index_started = perf_counter()
    base_project = _normalized_base_project(plan.project)
    top_layer = _top_conductor_name(base_project)
    rail_choices_by_pair = _rail_choice_index(base_project)
    indexed_geometry_keys = {
        (net, pwr_layer)
        for net, pwr_layer, _gnd_layer in rail_choices_by_pair
    }
    eligibility_index = PlaneEligibilityIndex(
        (
            geometry
            for geometry in analysis.plane_geometries
            if (geometry.net.casefold(), geometry.layer.casefold())
            in indexed_geometry_keys
        ),
        base_project.stackup_layers,
        gnd_aliases=base_project.gnd_aliases,
    )
    index_s = perf_counter() - index_started
    report(
        86,
        "Indexed "
        f"{eligibility_index.plane_count:,} plane groups / "
        f"{eligibility_index.primitive_count:,} primitives in {index_s:.1f}s",
    )
    top_instances = tuple(
        instance
        for instance in analysis.cap_instances
        if _instance_side(instance, top_layer) is ScenarioSide.TOP
    )
    top_instance_by_key = {
        instance.refdes.casefold(): instance for instance in top_instances
    }
    source_rail_id_by_key = {
        key: (
            rail.rail_id
            if (rail := _rail_for_instance(base_project, instance)) is not None
            else f"UNAVAILABLE::{instance.power_net}"
        )
        for key, instance in top_instance_by_key.items()
    }
    parsed_connection_by_key = {
        item.refdes.casefold(): item for item in analysis.decap_connections
    }
    parsed_cluster_by_key = {
        item.cluster_id.casefold(): item for item in analysis.shared_pad_clusters
    }
    source_landings = tuple(
        landing
        for connection in analysis.decap_connections
        for landing in (*connection.power_vias, *connection.ground_vias)
    )
    recovery_started = perf_counter()
    def recovery_progress(value: int, message: str) -> None:
        report(87 + round(max(0, min(100, value)) * 4 / 100), message)

    path_recovery = recover_spd_via_paths(
        source_path,
        landings=source_landings,
        target_layers_by_net=_via_target_layers_by_net(base_project),
        stackup_layers=base_project.stackup_layers,
        padstacks=analysis.padstacks,
        top_layer=top_layer,
        expected_source=analysis.source,
        progress=recovery_progress,
        is_cancelled=cancelled,
    )
    path_recovery_s = perf_counter() - recovery_started
    recovery_metadata = dict(base_project.metadata)
    recovery_metadata["spd_via_path_recovery"] = {
        **dict(path_recovery.statistics),
        "algorithm": "unique_monotonic_same_net_via_chain_v1",
        "fallback_behavior": "legacy_rail_template",
    }
    base_project = base_project.model_copy(update={"metadata": recovery_metadata})
    report(
        92,
        "Recovered source-proven Via path summaries; checking exact PWR-plane eligibility",
    )
    landing_cache: dict[str, ScenarioViaLanding] = {}

    def scenario_landing(landing: Any) -> ScenarioViaLanding:
        key = landing.via_id.casefold()
        result = landing_cache.get(key)
        if result is None:
            result = _scenario_via_landing(landing, path_recovery)
            landing_cache[key] = result
        return result

    # Cluster rail choices are evaluated at every unique physical PWR-via
    # landing.  A dummy pad never receives its own virtual via or independent
    # eligibility result.
    cluster_state_by_key: dict[str, SharedPadClusterState] = {}
    cluster_reason_by_key: dict[str, str | None] = {}
    cluster_eligibility_by_key: dict[str, dict[str, RailEligibility]] = {}
    cluster_via_eligibility_by_key: dict[
        str, dict[str, dict[str, RailEligibility]]
    ] = {}
    accepted_cluster_keys: set[str] = set()
    eligibility_started = perf_counter()
    for cluster_key, cluster in parsed_cluster_by_key.items():
        member_keys = {item.casefold() for item in cluster.member_refdes}
        if not member_keys.issubset(top_instance_by_key):
            continue
        accepted_cluster_keys.add(cluster_key)
        state_value = SharedPadClusterState(cluster.state)
        reason = cluster.reason
        source_rails = {source_rail_id_by_key[key].casefold() for key in member_keys}
        if (
            state_value != SharedPadClusterState.UNRESOLVED
            and len(source_rails) != 1
        ):
            state_value = SharedPadClusterState.UNRESOLVED
            reason = (
                "shared-pad members resolve to different source rail identities"
            )
        eligibility: dict[str, RailEligibility] = {}
        via_eligibility: dict[str, dict[str, RailEligibility]] = {}
        if state_value == SharedPadClusterState.ANCHORED:
            power_landings = {
                landing.via_id.casefold(): scenario_landing(landing)
                for member in cluster.member_refdes
                for landing in parsed_connection_by_key[member.casefold()].power_vias
            }
            via_eligibility = {
                landing.via_id: _eligibility_for_via_landing(
                    eligibility_index,
                    landing,
                    rail_choices_by_pair,
                )
                for landing in sorted(
                    power_landings.values(),
                    key=lambda item: item.via_id.casefold(),
                )
            }
            eligibility = _common_eligibility_maps(
                tuple(via_eligibility.values())
            )
            source_rail_key = next(iter(source_rails), "")
            source_present_at_every_via = bool(via_eligibility) and all(
                source_rail_key
                in {
                    item.rail_id.casefold()
                    for item in at_via.values()
                    if item.allowed
                }
                for at_via in via_eligibility.values()
            )
            if not source_present_at_every_via:
                state_value = SharedPadClusterState.UNRESOLVED
                reason = (
                    "the source rail is not present beneath every exact PWR-via "
                    "landing in the shared-pad cluster"
                )
                eligibility = {}
                via_eligibility = {}
        cluster_state_by_key[cluster_key] = state_value
        cluster_reason_by_key[cluster_key] = reason
        cluster_eligibility_by_key[cluster_key] = eligibility
        cluster_via_eligibility_by_key[cluster_key] = via_eligibility

    decaps: list[ScenarioDecap] = []
    scenario_connections: dict[str, ScenarioDecapConnection] = {}
    total_instances = len(top_instances)
    for index, instance in enumerate(top_instances, start=1):
        if index == 1 or index % 128 == 0:
            if cancelled():
                raise RuntimeError("SPD scenario import cancelled")
            fraction = index / max(1, total_instances)
            stage_elapsed = perf_counter() - eligibility_started
            total_elapsed = perf_counter() - total_started
            rate = index / max(stage_elapsed, 1.0e-9)
            remaining = max(0.0, total_instances - index) / max(rate, 1.0e-9)
            report(
                92 + round(fraction * 7),
                "Checking exact PWR-plane eligibility "
                f"({index:,}/{total_instances:,}; stage {stage_elapsed:.1f}s, "
                f"total {total_elapsed:.1f}s, ETA {remaining:.1f}s)",
            )
        side = _instance_side(instance, top_layer)
        source_rail = _rail_for_instance(base_project, instance)
        source_rail_id = (
            source_rail.rail_id
            if source_rail is not None
            else f"UNAVAILABLE::{instance.power_net}"
        )
        parsed_connection = parsed_connection_by_key.get(instance.refdes.casefold())
        connection_kind = DecapConnectionKind.UNRESOLVED
        connection_cluster_id: str | None = None
        connection_reason = "parser did not classify this TOP decap"
        power_vias: tuple[ScenarioViaLanding, ...] = ()
        ground_vias: tuple[ScenarioViaLanding, ...] = ()
        eligibility: dict[str, RailEligibility] = {}
        if parsed_connection is not None:
            connection_kind = DecapConnectionKind(parsed_connection.kind)
            connection_cluster_id = parsed_connection.cluster_id
            connection_reason = parsed_connection.reason
            power_vias = tuple(
                scenario_landing(item)
                for item in parsed_connection.power_vias
            )
            ground_vias = tuple(
                scenario_landing(item)
                for item in parsed_connection.ground_vias
            )
            if connection_cluster_id is None:
                if connection_kind == DecapConnectionKind.DIRECT:
                    eligibility = _common_eligibility_at_landings(
                        eligibility_index,
                        power_vias,
                        rail_choices_by_pair,
                    )
                    if source_rail_id.casefold() not in {
                        item.rail_id.casefold() for item in eligibility.values()
                    }:
                        connection_kind = DecapConnectionKind.UNRESOLVED
                        connection_reason = (
                            "the source rail is not present beneath every exact "
                            "PWR-via landing"
                        )
                        eligibility = {}
            else:
                cluster_key = connection_cluster_id.casefold()
                if cluster_key not in accepted_cluster_keys:
                    connection_kind = DecapConnectionKind.UNRESOLVED
                    connection_cluster_id = None
                    connection_reason = (
                        "shared-pad cluster includes a member outside the editable "
                        "TOP-side scenario"
                    )
                elif (
                    cluster_state_by_key[cluster_key]
                    == SharedPadClusterState.UNRESOLVED
                ):
                    connection_kind = DecapConnectionKind.UNRESOLVED
                    connection_reason = cluster_reason_by_key[cluster_key]
                else:
                    eligibility = cluster_eligibility_by_key[cluster_key]

        scenario_connection = ScenarioDecapConnection(
            refdes=instance.refdes,
            kind=connection_kind,
            cluster_id=connection_cluster_id,
            power_vias=power_vias,
            ground_vias=ground_vias,
            reason=connection_reason,
        )
        scenario_connections[instance.refdes] = scenario_connection
        decaps.append(
            ScenarioDecap(
                refdes=instance.refdes,
                center=ScenarioPoint(x_um=instance.x_um, y_um=instance.y_um),
                pwr_pad=ScenarioPad(
                    x_um=instance.power_x_um,
                    y_um=instance.power_y_um,
                    layer=instance.start_layer,
                    padstack=instance.power_padstack,
                ),
                gnd_pad=ScenarioPad(
                    x_um=(
                        instance.ground_pad_x_um
                        if instance.ground_pad_x_um is not None
                        else instance.x_um
                    ),
                    y_um=(
                        instance.ground_pad_y_um
                        if instance.ground_pad_y_um is not None
                        else instance.y_um
                    ),
                    layer=instance.start_layer,
                    padstack=instance.ground_padstack,
                ),
                side=side,
                start_layer=instance.start_layer,
                attach_layer=instance.attach_layer,
                footprint=instance.footprint or "GENERIC",
                source_net=instance.power_net,
                current_net=instance.power_net,
                source_rail_id=source_rail_id,
                current_rail_id=source_rail_id,
                source_model_id=instance.model_id,
                model_id=instance.model_id,
                # Electrical enable/disable represents whether the physical
                # source part is populated.  Missing SPICE data is a separate,
                # actionable model-assignment state and must not silently turn a
                # mounted capacitor into a DNP.
                enabled=bool(instance.mounted),
                source_mounted=instance.mounted,
                eligibility=eligibility,
            )
        )
    eligibility_s = perf_counter() - eligibility_started
    report(
        99,
        f"Checked {total_instances:,} decaps in {eligibility_s:.1f}s; validating scenario",
    )
    finalize_started = perf_counter()
    decaps.sort(key=lambda item: item.refdes.casefold())
    nets = sorted(
        {item.net for item in base_project.rails},
        key=str.casefold,
    )
    net_colors = {
        net: _NET_PALETTE[index % len(_NET_PALETTE)]
        for index, net in enumerate(nets)
    }
    attachments = dict(plan.attachments)
    attachment_hashes = {
        name: sha256(payload).hexdigest()
        for name, payload in attachments.items()
    }
    source = SourceIdentity(
        path=str(analysis.source.path),
        name=analysis.source.name,
        size_bytes=analysis.source.size_bytes,
        sha256=analysis.source.sha256,
    )
    scenario_clusters = tuple(
        SharedPadCluster(
            cluster_id=cluster.cluster_id,
            state=cluster_state_by_key[cluster_key],
            member_refdes=cluster.member_refdes,
            anchor_refdes=cluster.anchor_refdes,
            dummy_refdes=cluster.dummy_refdes,
            power_net=cluster.power_net,
            ground_net=cluster.ground_net,
            layer=cluster.layer,
            power_edges=cluster.power_edges,
            ground_edges=cluster.ground_edges,
            isolation_gap_refdes=cluster.isolation_gap_refdes,
            reason=cluster_reason_by_key[cluster_key],
            eligibility=cluster_eligibility_by_key[cluster_key],
            via_eligibility=cluster_via_eligibility_by_key[cluster_key],
        )
        for cluster_key, cluster in sorted(parsed_cluster_by_key.items())
        if cluster_key in accepted_cluster_keys
    )
    connection_analysis = SharedPadConnectionAnalysis(
        version=SHARED_PAD_ANALYSIS_VERSION,
        source_sha256=analysis.source.sha256,
        connections=scenario_connections,
        clusters=scenario_clusters,
    )
    scenario = ScenarioSpec(
        source=source,
        normalized_project=base_project,
        decaps=decaps,
        connection_analysis=connection_analysis,
        net_colors=net_colors,
        attachment_names=sorted(attachments, key=str.casefold),
        attachment_hashes=attachment_hashes,
    )
    finalize_s = perf_counter() - finalize_started
    total_s = perf_counter() - total_started
    timings = ImportStageTimings(
        analyze_s=analyze_s,
        plan_s=plan_s,
        index_s=index_s,
        recovery_s=path_recovery_s,
        eligibility_s=eligibility_s,
        finalize_s=finalize_s,
        total_s=total_s,
    )
    report(
        100,
        f"Loaded {len(decaps):,} top-side decap locations in {total_s:.1f}s",
    )
    return ScenarioImport(
        scenario=scenario,
        attachments=attachments,
        diagnostics=tuple((*plan.diagnostics, *path_recovery.diagnostics)),
        timings=timings,
    )


def verify_scenario_source(
    scenario: ScenarioSpec,
    source_path: str | Path | None = None,
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> Path:
    """Verify that the external SPD still matches the immutable source identity."""

    path = Path(source_path or scenario.source.path)
    if not path.is_file():
        raise FileNotFoundError(path)
    stat = path.stat()
    if stat.st_size != scenario.source.size_bytes:
        raise ValueError(
            f"SPD source size mismatch: expected {scenario.source.size_bytes:,} bytes, "
            f"found {stat.st_size:,} bytes"
        )
    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    digest = sha256()
    consumed = 0
    with path.open("rb") as handle:
        while True:
            if cancelled():
                raise RuntimeError("SPD source verification cancelled")
            chunk = handle.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            consumed += len(chunk)
            report(
                round(consumed * 100 / max(stat.st_size, 1)),
                "Verifying external SPD identity",
            )
    if digest.hexdigest() != scenario.source.sha256:
        raise ValueError("SPD source SHA-256 does not match this .spdpi scenario")
    report(100, "External SPD identity verified")
    return path.resolve()


__all__ = [
    "ImportStageTimings",
    "ScenarioImport",
    "import_spd_scenario",
    "verify_scenario_source",
]
