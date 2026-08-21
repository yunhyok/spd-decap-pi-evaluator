"""Read-only PowerSI SPD import for editable decap PI scenarios."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from math import hypot, isfinite
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from spd_decap_pi._core.domain import (
    MixedReferenceGroundWitness,
    PinKind,
    PlanePairSuggestion,
    ProjectSpec,
    TerminalKind,
)
from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.io.spd import (
    SpdCapInstance,
    SpdDiagnostic,
    SpdImportError,
    analyze_spd,
    recover_spd_ground_reachability,
    recover_spd_via_paths,
)
from spd_decap_pi._core.services import build_spd_import_plan, create_workspace_state

from .eligibility import (
    EligibilityResult,
    IndexedPlaneGeometry,
    PlaneEligibilityIndex,
)
from spd_decap_pi._core.plane_pairs import suggest_effective_plane_pairs
from .scenario import (
    DecapConnectionKind,
    RailEligibility,
    RoutingObstacleAssetRef,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSide,
    ScenarioSpec,
    ScenarioViaLanding,
    ScenarioViaGraphContactEvidence,
    ScenarioViaPathEvidence,
    ScenarioViaSegment,
    ScenarioViaStructuralEvidence,
    SHARED_PAD_ANALYSIS_VERSION,
    SharedPadCluster,
    SharedPadClusterState,
    SharedPadConnectionAnalysis,
    SourceIdentity,
    mixed_reference_ground_landing_identity,
)
from .routing_obstacles import (
    MLO_LANDING_CERTIFICATE_METADATA_KEY,
    MLO_LANDING_CERTIFICATE_VERSION,
    MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA,
    MLO_LANDING_CLASS_SHORT_SPAN_VIA,
    mlo_landing_certificate_claims_sha256,
    MLO_TRANSITION_POLICY_VERSION,
    PlannedViaProfile,
    RoutingObstacleAsset,
    decode_routing_obstacle_asset,
    detect_mlo_transition_policy,
    encode_routing_obstacle_asset,
    routing_attachment_name,
    stackup_fingerprint,
)


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]

# Versioned contract for final source-artwork and graph-contact validation.
# V2 distinguishes source-pad artwork dimensions from the solver template's
# rectangular analytical port dimensions.
FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION = "FINAL_TEMPLATE_ARTWORK_CONTAINMENT_V2"

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
    mixed_witness_selection_s: float
    ground_recovery_s: float
    eligibility_s: float
    finalize_s: float
    total_s: float


@dataclass(frozen=True, slots=True)
class _SourcePinLanding:
    """Ephemeral raw-node identity for a DUT/device terminal."""

    via_id: str
    net: str
    endpoint_node_id: str
    x_um: float = 0.0
    y_um: float = 0.0
    padstack: str = "DEVICE_PAD"


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


def _routing_via_profiles(
    project: ProjectSpec, padstacks: tuple[object, ...]
) -> tuple[PlannedViaProfile, ...]:
    """Build conservative research profiles from retained source padstacks.

    Layer pads use their true regular-shape outer envelope.  A layer without a
    PadDef uses the same explicit plated-barrel assumption already recorded by
    the SPD electrical template builder; the provenance string keeps that
    approximation visible and prevents it being mistaken for sign-off data.
    """

    padstack_by_key = {
        str(getattr(item, "name")).casefold(): item for item in padstacks
    }
    raw_provenance = project.metadata.get("spd_via_template_provenance", {})
    provenance = raw_provenance if isinstance(raw_provenance, dict) else {}
    profiles: list[PlannedViaProfile] = []
    for template in sorted(project.via_templates, key=lambda item: item.template_id.casefold()):
        raw = provenance.get(template.template_id, {})
        if not isinstance(raw, dict):
            raw = {}
        padstack_name = str(raw.get("padstack") or "")
        padstack = padstack_by_key.get(padstack_name.casefold())
        radius_by_layer: dict[str, tuple[str, float]] = {}
        if padstack is not None:
            for shape in getattr(padstack, "pad_shapes", ()):
                width = getattr(shape, "width_um", None)
                height = getattr(shape, "height_um", None)
                kind = str(getattr(shape, "kind", ""))
                if (
                    width is None
                    or height is None
                    or not isfinite(float(width))
                    or not isfinite(float(height))
                    or float(width) <= 0
                    or float(height) <= 0
                ):
                    continue
                radius = (
                    max(float(width), float(height)) / 2.0
                    if kind == "CIRCLE"
                    else hypot(float(width), float(height)) / 2.0
                )
                layer_name = str(getattr(shape, "layer"))
                layer_key = layer_name.casefold()
                previous = radius_by_layer.get(layer_key)
                if previous is None or radius > previous[1]:
                    radius_by_layer[layer_key] = (layer_name, radius)
        drill = (
            float(getattr(padstack, "drill_diameter_um"))
            if padstack is not None
            and getattr(padstack, "drill_diameter_um", None) is not None
            else None
        )
        plating = raw.get("barrel_plating_assumption_um")
        try:
            plating_um = float(plating) if plating is not None else None
        except (TypeError, ValueError):
            plating_um = None
        fallback = (
            drill / 2.0 + plating_um
            if drill is not None
            and drill > 0
            and plating_um is not None
            and isfinite(plating_um)
            and plating_um > 0
            else None
        )
        complete = bool(radius_by_layer or fallback is not None)
        profiles.append(
            PlannedViaProfile(
                profile_id=template.template_id,
                radius_um_by_layer=tuple(
                    sorted(radius_by_layer.values(), key=lambda item: item[0].casefold())
                ),
                fallback_barrel_radius_um=fallback,
                complete=complete,
                provenance=(
                    "SOURCE_PADSTACK_REGULAR_SHAPES_WITH_ANALYTICAL_BARREL_V1"
                    if complete
                    else "NO_PLANNED_REBUILD_RECIPE"
                ),
                unresolved_reason=(
                    None
                    if complete
                    else "no source pad envelope or plated-barrel radius is available"
                ),
            )
        )
    return tuple(profiles)


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
    # Unique serial path evidence wins; graph-only branched/cyclic evidence
    # carries the same exact target contact but no scalar RL.  Both must drive
    # shared-pad eligibility at the retained target layer rather than querying
    # the immutable source TOP coordinate.
    target_evidence = (
        *landing.path_evidence,
        *landing.graph_contact_evidence,
    )
    for evidence in target_evidence:
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
    connectivity_recovery: Any | None = None,
    source_sha256: str | None = None,
    contact_lookup: Mapping[tuple[str, str], tuple[tuple[str, tuple[tuple[str, float, float], ...]], ...]] | None = None,
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
            trace_hops=item.trace_hops,
            trace_alternate_exit=item.trace_alternate_exit,
            provenance=(
                "SOURCE_PROVEN_TRACE_ALTERNATE_EXIT_LEGACY_TEMPLATE"
                if item.trace_alternate_exit
                else "SOURCE_PROVEN_UNIQUE_TRACE_VIA_CHAIN"
                if item.trace_hops
                else "SOURCE_PROVEN_MONOTONIC_VIA_CHAIN"
            ),
        )
        for item in recovery.evidence_by_via.get(landing.via_id.casefold(), ())
    )
    structural_evidence = tuple(
        ScenarioViaStructuralEvidence(
            target_layer=item.target_layer,
            target_node_id=item.target_node_id,
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
            trace_hops=item.trace_hops,
            trace_alternate_exit=item.trace_alternate_exit,
        )
        for item in getattr(recovery, "structural_evidence_by_via", {}).get(
            landing.via_id.casefold(), ()
        )
    )
    graph_evidence: list[ScenarioViaGraphContactEvidence] = []
    if connectivity_recovery is not None and source_sha256:
        contacts_by_key = getattr(connectivity_recovery, "target_contacts_by_key", {})
        counts_by_key = getattr(
            connectivity_recovery, "target_contact_count_by_key", {}
        )
        hashes_by_key = getattr(
            connectivity_recovery, "target_contact_hash_by_key", {}
        )
        via_key = str(landing.via_id).casefold()
        endpoint_key = str(landing.endpoint_node_id).casefold()
        direct_contacts = (
            contact_lookup.get((via_key, endpoint_key), ())
            if contact_lookup is not None
            else tuple(
                (key[2], contacts)
                for key, contacts in contacts_by_key.items()
                if len(key) == 3 and key[0] == via_key and key[1] == endpoint_key
            )
        )
        for target_layer, contacts in direct_contacts:
            target_layer = str(target_layer)
            key = (via_key, endpoint_key, target_layer)
            if not contacts:
                continue
            selected = contacts[0]
            try:
                target_node_id, target_x_um, target_y_um = selected
                candidate_count = int(counts_by_key.get(key, 0))
                contact_hash = str(hashes_by_key.get(key, ""))
                distance_um = hypot(
                    float(target_x_um) - float(landing.x_um),
                    float(target_y_um) - float(landing.y_um),
                )
                graph_evidence.append(
                    ScenarioViaGraphContactEvidence(
                        target_layer=target_layer,
                        target_node_id=str(target_node_id),
                        x_um=float(target_x_um),
                        y_um=float(target_y_um),
                        candidate_count=max(1, candidate_count),
                        candidate_contacts_sha256=contact_hash,
                        selection_basis="NEAREST_COMPONENT_TARGET",
                        source_sha256=source_sha256,
                        selected_distance_um=distance_um,
                        connectivity_only=True,
                    )
                )
            except (TypeError, ValueError):
                # Invalid graph contact evidence is never promoted into the
                # persisted scenario; strict pair selection will already have
                # rejected the candidate when this evidence was required.
                continue
    return ScenarioViaLanding(
        via_id=landing.via_id,
        net=landing.net,
        endpoint_node_id=landing.endpoint_node_id,
        x_um=landing.x_um,
        y_um=landing.y_um,
        padstack=landing.padstack,
        rotation_degrees=landing.rotation_degrees,
        path_evidence=evidence,
        graph_contact_evidence=tuple(graph_evidence),
        structural_evidence=structural_evidence,
    )


def _via_target_layers_by_net(
    project: ProjectSpec,
    *,
    plane_geometries: tuple[Any, ...] = (),
) -> dict[str, tuple[str, ...]]:
    """Target layers requested from recovery for every selected terminal net.

    Include every retained same-NET PWR plane layer, not only the configured
    rail layer.  The configured rail may be a logical alias (for example L12)
    while the source terminal actually lands on L09/L11; omitting those layers
    turns valid source paths into pathless legacy fallbacks. GND aliases are
    resolved through rail/mixed-reference targets and are not expanded from
    every retained geometry record.
    """

    result: dict[str, set[str]] = {}
    aliases = {item.casefold() for item in project.gnd_aliases}
    for rail in project.rails:
        result.setdefault(rail.net.casefold(), set()).add(rail.pwr_layer)
        gnd_net = (
            rail.mixed_reference_certificate.gnd_net
            if rail.mixed_reference_certificate is not None
            else None
        )
        gnd_layer = next(
            (item for item in project.stackup_layers if item.name == rail.gnd_layer),
            None,
        )
        candidates = [
            net for net in (gnd_layer.pwr_nets if gnd_layer is not None else ())
            if net.casefold() in aliases
            and (gnd_net is None or net.casefold() == gnd_net.casefold())
        ]
        if len(candidates) == 1:
            result.setdefault(candidates[0].casefold(), set()).add(rail.gnd_layer)
        # Candidate selection may replace a closest-separation pair with a
        # farther source-proven pair.  Request every pure-GND layer permitted
        # by the stack-up now, before pair selection, so the raw walker cannot
        # accidentally hide the evidence needed for that choice.
        try:
            suggestions = suggest_effective_plane_pairs(
                project.stackup_layers,
                rail_net=rail.net,
                gnd_aliases=project.gnd_aliases,
            )
        except (AttributeError, TypeError, ValueError):
            suggestions = ()
        for suggestion in suggestions:
            result.setdefault(rail.net.casefold(), set()).add(suggestion.pwr_layer)
            target_layer = next(
                (item for item in project.stackup_layers if item.name.casefold() == suggestion.gnd_layer.casefold()),
                None,
            )
            if target_layer is None:
                continue
            for target_net in target_layer.pwr_nets:
                if target_net.casefold() in aliases:
                    result.setdefault(target_net.casefold(), set()).add(suggestion.gnd_layer)
    for geometry in plane_geometries:
        net = str(getattr(geometry, "net", "")).strip().casefold()
        layer = str(getattr(geometry, "layer", "")).strip()
        # Ground plane records are intentionally excluded here. GND target
        # layers are already derived from each rail/mixed-reference certificate;
        # expanding every retained DGND geometry would multiply requests by all
        # GND plane layers without adding PWR recovery targets.
        if net and layer and net not in aliases:
            result.setdefault(net, set()).add(layer)
    spd_import = project.metadata.get("spd_import", {})
    failures = (
        spd_import.get("mixed_reference_certificate_failures", ())
        if isinstance(spd_import, dict)
        else ()
    )
    for item in failures if isinstance(failures, list) else ():
        if not isinstance(item, dict):
            continue
        net = str(item.get("rail_net", "")).casefold()
        layer = str(item.get("pwr_layer", ""))
        if net and layer:
            result.setdefault(net, set()).add(layer)
    return {
        net: tuple(sorted(layers, key=str.casefold))
        for net, layers in sorted(result.items())
    }


def _finite_port_inside_solver_bounds(
    x_um: float,
    y_um: float,
    width_um: float,
    height_um: float,
    bounds: tuple[float, float, float, float],
) -> bool:
    """Require a finite analytical port to be strictly inside its solver cell."""
    try:
        x_value = float(x_um)
        y_value = float(y_um)
        width = float(width_um)
        height = float(height_um)
        xmin, xmax, ymin, ymax = (float(item) for item in bounds)
    except (TypeError, ValueError, ArithmeticError):
        return False
    if not all(isfinite(item) for item in (x_value, y_value, width, height, xmin, xmax, ymin, ymax)):
        return False
    if width <= 0.0 or height <= 0.0 or xmax <= xmin or ymax <= ymin:
        return False
    return (
        x_value - width / 2.0 > xmin
        and x_value + width / 2.0 < xmax
        and y_value - height / 2.0 > ymin
        and y_value + height / 2.0 < ymax
    )


def _prepare_post_plan_selection(
    selected_pairs: Mapping[str, PlanePairSuggestion],
    pair_provenance: dict[str, dict[str, Any]],
    preselection_pairs: Mapping[str, PlanePairSuggestion],
    failed_nets: set[str],
    preselection_rail_keys: set[str],
) -> dict[str, PlanePairSuggestion]:
    """Restore failed selections for a complete third import-plan build."""
    restored = dict(selected_pairs)
    for failed_net in sorted(failed_nets):
        original_pair = preselection_pairs.get(failed_net)
        if original_pair is None:
            if failed_net in preselection_rail_keys:
                raise SpdImportError(
                    f"cannot safely restore unresolved rail {failed_net}: "
                    "preselection pair is not a valid stackup suggestion"
                )
            # A graph candidate with no source rail is an orphan diagnostic;
            # never create a selectable invalid RailSpec for it.
            restored.pop(failed_net, None)
            proof = pair_provenance.get(failed_net)
            if isinstance(proof, dict):
                proof["source_graph_pair_unresolved"] = True
                proof["selection_mode"] = "LEGACY_PREEXISTING_PAIR_UNRESOLVED"
                failures = list(proof.get("selected_plane_pair_failures", ()))
                failures.append("source graph candidate has no preselection rail")
                proof["selected_plane_pair_failures"] = tuple(dict.fromkeys(failures))
                proof["final_template_footprint"] = {
                    "basis": "SOURCE_GRAPH_ORPHAN_UNRESOLVED",
                    "validated": False,
                    "contract_version": FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION,
                }
            continue
        restored[failed_net] = original_pair
    return restored


def _strict_source_plane_pairs(
    project: ProjectSpec,
    analysis: Any,
    path_recovery: Any,
    connectivity_recovery: Any | None = None,
    indexed_override: Mapping[tuple[str, str], IndexedPlaneGeometry] | None = None,
) -> tuple[dict[str, PlanePairSuggestion], dict[str, dict[str, Any]]]:
    """Select source-proven graph-connected PWR/GND pairs for raw import.

    Pair separation is only a deterministic tie-breaker. Every enabled decap
    landing must reach a candidate-layer target in the source Via/Trace graph,
    with exact retained artwork coverage whenever finite target-pad geometry
    is available; graph-only branches prove exact target-node interior but do
    not claim a finite pad footprint. Device terminals require source Node
    identities reaching the same target graph. Missing, ambiguous, or
    boundary/void evidence rejects a candidate rather than inventing a path or
    widening a bbox.
    """

    gnd_keys = {item.casefold() for item in project.gnd_aliases}
    geometries: dict[tuple[str, str], list[Any]] = {}
    for geometry in getattr(analysis, "plane_geometries", ()):
        key = (
            str(getattr(geometry, "layer", "")).casefold(),
            str(getattr(geometry, "net", "")).casefold(),
        )
        if all(key):
            geometries.setdefault(key, []).append(geometry)
    indexed: dict[tuple[str, str], IndexedPlaneGeometry] = {}
    if indexed_override is not None:
        # Reuse the shared index only for unambiguous retained artwork keys.
        # A dict keyed by (layer, net) must never silently last-win when the
        # source contains duplicate records; strict selection treats that as
        # unavailable evidence and fails closed.
        for key, values in geometries.items():
            if len(values) != 1:
                continue
            candidate = indexed_override.get(key)
            if candidate is not None:
                indexed[key] = candidate
    else:
        for key, values in geometries.items():
            if len(values) != 1:
                continue
            candidate = IndexedPlaneGeometry.build(values[0])
            if candidate is not None:
                indexed[key] = candidate

    mounted = {
        str(item.refdes).casefold()
        for item in getattr(analysis, "cap_instances", ())
        if bool(getattr(item, "mounted", False))
        and bool(getattr(item, "model_id", None))
    }
    connections = tuple(
        item
        for item in getattr(analysis, "decap_connections", ())
        if not mounted or str(getattr(item, "refdes", "")).casefold() in mounted
    )
    path_by_via = getattr(path_recovery, "evidence_by_via", {})
    source_sha256 = str(getattr(analysis.source, "sha256", "")).casefold()
    import_metadata = project.metadata.get("spd_import", {})
    bound_source_sha256 = (
        str(import_metadata.get("source_sha256", "")).casefold()
        if isinstance(import_metadata, dict)
        else ""
    )
    if (
        len(source_sha256) != 64
        or (bound_source_sha256 and bound_source_sha256 != source_sha256)
    ):
        raise SpdImportError(
            "SPD source-proven plane-pair selection is blocked: source SHA-256 "
            "does not match the retained scenario binding"
        )

    def evidence_for(via: Any, layer: str) -> tuple[Any, ...]:
        return tuple(
            item
            for item in path_by_via.get(str(via.via_id).casefold(), ())
            if str(getattr(item, "target_layer", "")).casefold()
            == layer.casefold()
        )

    artwork_digest_cache: dict[int, str] = {}

    def artwork_digest(plane: IndexedPlaneGeometry | None) -> str:
        if plane is None:
            return ""
        cache_key = id(plane)
        cached = artwork_digest_cache.get(cache_key)
        if cached is not None:
            return cached
        geometry = plane.geometry
        payload = {
            "positive_polygons_um": [list(item) for item in geometry.positive_polygons_um],
            "negative_polygons_um": [list(item) for item in geometry.negative_polygons_um],
            "positive_circles_um": [list(item) for item in geometry.positive_circles_um],
            "negative_circles_um": [list(item) for item in geometry.negative_circles_um],
            "primitive_order": [list(item) for item in geometry.primitive_order],
        }
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        artwork_digest_cache[cache_key] = digest
        return digest

    def covered(
        plane_key: tuple[str, str],
        plane: IndexedPlaneGeometry | None,
        x_um: float,
        y_um: float,
        width_um: float = 0.0,
        height_um: float = 0.0,
    ) -> bool:
        if plane is None or not all(isfinite(float(item)) for item in (x_um, y_um, width_um, height_um)):
            return False
        return plane.covers_footprint(float(x_um), float(y_um), float(width_um), float(height_um))

    covered_point_cache: dict[tuple[int, float, float], bool] = {}

    def covered_point(
        plane: IndexedPlaneGeometry | None,
        x_um: float,
        y_um: float,
    ) -> bool:
        """Require a graph-selected target node to be strict-interior copper.

        Connectivity-only witnesses do not carry a source-proven finite pad
        footprint.  The modal solver still uses its finite rectangular port,
        but that analytical rectangle is a disclosed cavity approximation and
        must not be mistaken for exact retained artwork coverage.
        """
        if plane is None:
            return False
        try:
            x_value = float(x_um)
            y_value = float(y_um)
        except (TypeError, ValueError, ArithmeticError):
            return False
        if not (isfinite(x_value) and isfinite(y_value)):
            return False
        cache_key = (id(plane), x_value, y_value)
        if cache_key in covered_point_cache:
            return covered_point_cache[cache_key]
        try:
            result = plane.contains(x_value, y_value) == "inside"
        except (TypeError, ValueError, ArithmeticError):
            result = False
        covered_point_cache[cache_key] = result
        return result

    # Device pins are consulted for every candidate pair.  Materialize one
    # immutable index up front so candidate evaluation stays O(1) per net and
    # does not rescan a production-sized pin table for each suggestion.
    device_pins_by_key: dict[tuple[str, TerminalKind], tuple[Any, ...]] = {}
    device_pwr_sites_by_net: dict[str, frozenset[Any]] = {}
    grouped_device_pins: dict[tuple[str, TerminalKind], list[Any]] = {}
    for item in getattr(analysis, "pins", ()):
        if item.kind != PinKind.DEVICE_BUMP:
            continue
        key = (str(item.net).casefold(), item.terminal)
        grouped_device_pins.setdefault(key, []).append(item)
    for key, values in grouped_device_pins.items():
        device_pins_by_key[key] = tuple(values)
        if key[1] == TerminalKind.PWR:
            device_pwr_sites_by_net[key[0]] = frozenset(
                item.site for item in values if item.site is not None
            )
    ground_aliases = {str(item).casefold() for item in project.gnd_aliases}
    device_ground_pins = tuple(
        item
        for (net_key, terminal), values in device_pins_by_key.items()
        if terminal == TerminalKind.GND and net_key in ground_aliases
        for item in values
    )

    def device_pins(net: str, terminal: TerminalKind) -> tuple[Any, ...]:
        return device_pins_by_key.get((net.casefold(), terminal), ())

    padstacks_by_key = {
        str(item.name).casefold(): item
        for item in getattr(analysis, "padstacks", ())
    }

    def finite_padstack_footprint(name: str) -> tuple[float, float] | None:
        padstack = padstacks_by_key.get(str(name).casefold())
        width = getattr(padstack, "pad_width_um", None)
        height = getattr(padstack, "pad_height_um", None)
        try:
            width_value = float(width)
            height_value = float(height)
        except (TypeError, ValueError):
            return None
        if not (
            isfinite(width_value)
            and isfinite(height_value)
            and width_value > 0.0
            and height_value > 0.0
        ):
            return None
        return width_value, height_value

    def finite_source_footprint(via: Any) -> tuple[float, float] | None:
        return finite_padstack_footprint(str(getattr(via, "padstack", "")))

    def effective_template_footprint(
        suggestion: PlanePairSuggestion,
    ) -> tuple[float, float] | None:
        templates = tuple(
            item
            for item in getattr(project, "via_templates", ())
            if str(getattr(item, "pwr_reference_layer", "")).casefold()
            == suggestion.pwr_layer.casefold()
            and str(getattr(item, "gnd_reference_layer", "")).casefold()
            == suggestion.gnd_layer.casefold()
        )
        for template in templates:
            try:
                width = float(template.finite_port_width_um)
                height = float(template.finite_port_height_um)
            except (TypeError, ValueError):
                continue
            if isfinite(width) and isfinite(height) and width > 0.0 and height > 0.0:
                return width, height
        # A newly selected internal pair has no bound template until the
        # import plan is rebuilt.  Use the largest finite existing template as
        # a conservative pre-plan domain probe; final validation rebinds the
        # exact rail template and repeats the check.
        fallback: list[tuple[float, float]] = []
        for template in getattr(project, "via_templates", ()):
            try:
                width = float(template.finite_port_width_um)
                height = float(template.finite_port_height_um)
            except (TypeError, ValueError):
                continue
            if isfinite(width) and isfinite(height) and width > 0.0 and height > 0.0:
                fallback.append((width, height))
        return max(fallback, key=lambda item: (item[0] * item[1], item[0], item[1])) if fallback else None

    def template_footprint_inside_candidate_domain(
        plane: IndexedPlaneGeometry | None,
        x_um: float,
        y_um: float,
        footprint: tuple[float, float] | None,
    ) -> bool:
        """Keep graph contacts inside the candidate's solver rectangle.

        Graph contacts prove exact target-node artwork, while the finite
        template port is an analytical rectangular cavity.  The candidate
        plane's positive artwork bounds are the pre-plan domain available for
        this check; strict edges fail closed.
        """
        if plane is None or footprint is None:
            return False
        return _finite_port_inside_solver_bounds(
            float(x_um),
            float(y_um),
            float(footprint[0]),
            float(footprint[1]),
            plane.positive_bounds,
        )

    selected: dict[str, PlanePairSuggestion] = {}
    provenance: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    project_net_spelling = {
        str(rail.net).casefold(): str(rail.net) for rail in project.rails
    }
    canonical_power_nets: list[str] = []
    seen_power_nets: set[str] = set()
    for raw_net in getattr(analysis, "power_plane_nets", ()):
        net_key = str(raw_net).casefold()
        if net_key in seen_power_nets:
            continue
        seen_power_nets.add(net_key)
        canonical_power_nets.append(
            project_net_spelling.get(net_key, str(raw_net))
        )
    for net in canonical_power_nets:
        suggestions = suggest_effective_plane_pairs(
            project.stackup_layers,
            rail_net=str(net),
            gnd_aliases=project.gnd_aliases,
        )
        if not suggestions:
            continue
        relevant = tuple(
            item
            for item in connections
            if any(
                str(getattr(via, "net", "")).casefold() == str(net).casefold()
                for via in getattr(item, "power_vias", ())
            )
        )
        relevant_device_pins = tuple(
            item
            for item in getattr(analysis, "pins", ())
            if item.kind == PinKind.DEVICE_BUMP
            and str(item.net).casefold() == str(net).casefold()
        )
        # Graph capability is explicit source identity, not a file-size or
        # empty-result heuristic.  A source with decap/device terminals is
        # strict-evaluable only when every terminal carries a parser-resolved
        # graph start node; otherwise candidate proof fails closed below.
        graph_capable = bool(relevant or relevant_device_pins) and all(
            bool(getattr(via, "endpoint_node_id", "").strip())
            and bool(getattr(via, "net", "").strip())
            for connection in relevant
            for via in (
                *getattr(connection, "power_vias", ()),
                *getattr(connection, "ground_vias", ()),
            )
        ) and all(
            bool(getattr(pin, "source_node_id", "").strip())
            for pin in relevant_device_pins
        )
        graph_statistics = getattr(connectivity_recovery, "statistics", {}) or {}
        explicit_graph_capability = str(
            (getattr(analysis, "counts", {}) or {}).get("source_graph_capability", "")
        ).upper()
        graph_available = bool(
            explicit_graph_capability != "LEGACY_SOURCE_GRAPH_UNAVAILABLE"
            and int(graph_statistics.get("requested", 0)) > 0
            and int(graph_statistics.get("node_section_passes", 0)) > 0
            and (
                int(graph_statistics.get("trace_edges", 0)) > 0
                or int(graph_statistics.get("via_edges", 0)) > 0
            )
        )
        # Sources without a retained Trace/Via graph remain importable for the
        # historical scenario workflow, but are explicitly tagged
        # LEGACY_SOURCE_GRAPH_UNAVAILABLE and cannot claim strict graph proof
        # during Evaluation.  This is an explicit parser capability check, not
        # a file-size/result-empty bypass.
        # A source explicitly marked graph-unavailable may still provide
        # complete legacy unique-path evidence.  Preserve that evidence for
        # compatibility; an empty recovery never grants a legacy waiver.
        explicit_legacy_source = (
            explicit_graph_capability == "LEGACY_SOURCE_GRAPH_UNAVAILABLE"
        )
        legacy_simple_import = (
            explicit_legacy_source and not any(path_by_via.values())
        )
        legacy_preexisting_pair = legacy_simple_import
        existing_pair_keys = {
            (
                str(item.net).casefold(),
                str(item.pwr_layer).casefold(),
                str(item.gnd_layer).casefold(),
            )
            for item in getattr(project, "rails", ())
        }
        device_route_required = bool(
            graph_available
            and bool(relevant_device_pins)
        )
        candidates: list[tuple[tuple[float, ...], PlanePairSuggestion, dict[str, Any]]] = []
        candidate_diagnostics: list[str] = []
        for suggestion in suggestions:
            pwr_key = (suggestion.pwr_layer.casefold(), str(net).casefold())
            pwr = indexed.get(
                pwr_key
            )
            gnd_layer = next(
                (
                    value
                    for value in project.stackup_layers
                    if value.name.casefold() == suggestion.gnd_layer.casefold()
                ),
                None,
            )
            gnd_candidates = [
                (suggestion.gnd_layer.casefold(), ground_net.casefold())
                for ground_net in (gnd_layer.pwr_nets if gnd_layer is not None else ())
                if ground_net.casefold() in gnd_keys
            ]
            # A pure DGND layer has exactly one configured alias in normal
            # imports; reject ambiguous/missing artwork rather than choosing it
            # by layer name alone.
            gnd = None
            gnd_key: tuple[str, str] | None = None
            for key in gnd_candidates:
                candidate = indexed.get(key)
                if candidate is not None:
                    if gnd is not None:
                        gnd = None
                        gnd_key = None
                        break
                    gnd = candidate
                    gnd_key = key
            missing_routes = 0
            uncovered = 0
            ambiguous_routes = 0
            route_count = 0
            route_witnesses: list[dict[str, Any]] = []
            device_route_proven = 0
            device_route_witnesses: list[dict[str, Any]] = []
            effective_footprint = effective_template_footprint(suggestion)
            for pin in (
                ()
                if legacy_simple_import or not device_route_required
                else device_pins(str(net), TerminalKind.PWR)
            ):
                landing = _SourcePinLanding(
                    via_id=f"PIN::{pin.pin_id}",
                    net=pin.net,
                    endpoint_node_id=str(pin.source_node_id or ""),
                    x_um=float(pin.x_um),
                    y_um=float(pin.y_um),
                )
                reachable = bool(
                    pin.source_node_id
                    and connectivity_recovery is not None
                    and connectivity_recovery.reaches(landing, suggestion.pwr_layer)
                )
                contact_key = (
                    landing.via_id.casefold(),
                    landing.endpoint_node_id.casefold(),
                    suggestion.pwr_layer.casefold(),
                )
                contacts = tuple(
                    getattr(connectivity_recovery, "target_contacts_by_key", {}).get(
                        contact_key, ()
                    )
                )
                if reachable and (
                    not contacts
                    or pwr is None
                    or not covered_point(pwr, float(contacts[0][1]), float(contacts[0][2]))
                    or not template_footprint_inside_candidate_domain(
                        pwr,
                        float(contacts[0][1]),
                        float(contacts[0][2]),
                        effective_footprint,
                    )
                ):
                    reachable = False
                device_route_witnesses.append(
                    {
                        "pin_id": pin.pin_id,
                        "net": pin.net,
                        "terminal": "PWR",
                        "source_node_id": pin.source_node_id,
                        "target_layer": suggestion.pwr_layer,
                        "reachable": reachable,
                            "target_contacts": [
                                {"node_id": item[0], "x_um": item[1], "y_um": item[2]}
                                for item in contacts
                            ],
                            "target_contact_count": int(
                                getattr(connectivity_recovery, "target_contact_count_by_key", {})
                                .get(contact_key, len(contacts))
                            ),
                        "target_contacts_sha256": str(
                                getattr(connectivity_recovery, "target_contact_hash_by_key", {})
                                .get(contact_key, "")
                            ),
                        "selection_basis": "NEAREST_COMPONENT_TARGET",
                        "artwork_coverage_basis": "SOURCE_GRAPH_TARGET_NODE_STRICT_INTERIOR",
                        "solver_port_basis": "RECTANGULAR_CAVITY_FINITE_PORT_V1",
                        "reachability_claim_sha256": sha256(
                            f"{source_sha256}|{pin.pin_id}|{pin.source_node_id}|{suggestion.pwr_layer}|{reachable}".encode("utf-8")
                        ).hexdigest(),
                    }
                )
                if not reachable:
                    uncovered += 1
                else:
                    device_route_proven += 1
            pwr_sites = device_pwr_sites_by_net.get(str(net).casefold(), frozenset())
            related_gnd = () if legacy_simple_import or not device_route_required else tuple(
                item
                for item in device_ground_pins
                if item.site is None or item.site in pwr_sites
            )
            excluded_device_gnd_ids: list[str] = []
            participating_device_gnd_count = 0
            for pin in related_gnd:
                landing = _SourcePinLanding(
                    via_id=f"PIN::{pin.pin_id}",
                    net=pin.net,
                    endpoint_node_id=str(pin.source_node_id or ""),
                    x_um=float(pin.x_um),
                    y_um=float(pin.y_um),
                )
                reachable = bool(
                    gnd_key is not None
                    and pin.source_node_id
                    and connectivity_recovery is not None
                    and connectivity_recovery.reaches(landing, suggestion.gnd_layer)
                )
                contact_key = (
                    landing.via_id.casefold(),
                    landing.endpoint_node_id.casefold(),
                    suggestion.gnd_layer.casefold(),
                )
                contacts = tuple(
                    getattr(connectivity_recovery, "target_contacts_by_key", {}).get(
                        contact_key, ()
                    )
                )
                if reachable and (
                    not contacts
                    or gnd is None
                    or not covered_point(gnd, float(contacts[0][1]), float(contacts[0][2]))
                    or not template_footprint_inside_candidate_domain(
                        pwr,
                        float(contacts[0][1]),
                        float(contacts[0][2]),
                        effective_footprint,
                    )
                ):
                    reachable = False
                if not reachable:
                    # GND bumps that do not reach this candidate cavity belong
                    # to another selected plane/domain.  Preserve their IDs
                    # as diagnostics rather than turning them into blockers or
                    # silently pretending they were modeled here.
                    excluded_device_gnd_ids.append(str(pin.pin_id))
                    continue
                participating_device_gnd_count += 1
                device_route_witnesses.append(
                    {
                        "pin_id": pin.pin_id,
                        "net": pin.net,
                        "terminal": "GND",
                        "source_node_id": pin.source_node_id,
                        "target_layer": suggestion.gnd_layer,
                        "reachable": reachable,
                        "target_contacts": [
                            {"node_id": item[0], "x_um": item[1], "y_um": item[2]}
                            for item in contacts
                        ],
                        "target_contact_count": int(
                            getattr(connectivity_recovery, "target_contact_count_by_key", {})
                            .get(contact_key, len(contacts))
                        ),
                        "target_contacts_sha256": str(
                            getattr(connectivity_recovery, "target_contact_hash_by_key", {})
                            .get(contact_key, "")
                        ),
                        "selection_basis": "NEAREST_COMPONENT_TARGET",
                        "artwork_coverage_basis": "SOURCE_GRAPH_TARGET_NODE_STRICT_INTERIOR",
                        "solver_port_basis": "RECTANGULAR_CAVITY_FINITE_PORT_V1",
                        "reachability_claim_sha256": sha256(
                            f"{source_sha256}|{pin.pin_id}|{pin.source_node_id}|{suggestion.gnd_layer}|{reachable}".encode("utf-8")
                        ).hexdigest(),
                    }
                )
                device_route_proven += 1
            if device_route_required and participating_device_gnd_count == 0:
                uncovered += 1
            for connection in relevant:
                for via in (*getattr(connection, "power_vias", ()), *getattr(connection, "ground_vias", ())):
                    is_power_via = via in getattr(connection, "power_vias", ())
                    terminal_kind = "PWR" if is_power_via else "GND"
                    layer = suggestion.pwr_layer if is_power_via else suggestion.gnd_layer
                    matches = evidence_for(via, layer)
                    graph_connected = bool(
                        connectivity_recovery is not None
                        and connectivity_recovery.reaches(via, layer)
                    )
                    if not matches and graph_connected:
                        # The union-find graph is the authoritative proof for
                        # branched/cyclic copper.  It intentionally carries no
                        # serial RL path; retain a connectivity witness and
                        # leave the template electrical values conservative.
                        matches = (None,)
                    if not matches:
                        missing_routes += 1
                        continue
                    route_count += 1
                    target = pwr if layer.casefold() == suggestion.pwr_layer.casefold() else gnd
                    target_key = pwr_key if target is pwr else gnd_key
                    # A source Via+Trace component may legitimately have
                    # several exits on the target plane.  Preserve and verify
                    # every recovered exit; do not collapse a branch to one
                    # invented scalar path.
                    if target_key is None or any(
                        evidence is not None
                        and not covered(
                            target_key,
                            target,
                            float(evidence.target_x_um),
                            float(evidence.target_y_um),
                            float(evidence.target_pad_width_um),
                            float(evidence.target_pad_height_um),
                        )
                        for evidence in matches
                    ):
                        uncovered += 1
                    for evidence in matches:
                        if evidence is None:
                            contacts = tuple(
                                getattr(connectivity_recovery, "target_contacts_by_key", {}).get(
                                    (
                                        str(getattr(via, "via_id", "")).casefold(),
                                        str(getattr(via, "endpoint_node_id", "")).casefold(),
                                        str(layer).casefold(),
                                    ),
                                    (),
                                )
                            )
                            source_x = float(getattr(via, "x_um", 0.0))
                            source_y = float(getattr(via, "y_um", 0.0))
                            selected_contact = (
                                min(
                                    contacts,
                                    key=lambda item: (
                                        (float(item[1]) - source_x) ** 2
                                        + (float(item[2]) - source_y) ** 2,
                                        str(item[0]).casefold(),
                                        str(item[0]),
                                        float(item[1]),
                                        float(item[2]),
                                    ),
                                )
                                if contacts
                                else None
                            )
                            if (
                                selected_contact is None
                                or target_key is None
                                or not covered_point(
                                    target,
                                    float(selected_contact[1]),
                                    float(selected_contact[2]),
                                )
                                or not template_footprint_inside_candidate_domain(
                                    pwr,
                                    float(selected_contact[1]),
                                    float(selected_contact[2]),
                                    effective_footprint,
                                )
                            ):
                                uncovered += 1
                            contacts_hash = sha256(
                                json.dumps(
                                    list(contacts), sort_keys=True, separators=(",", ":")
                                ).encode("utf-8")
                            ).hexdigest()
                            contact_key = (
                                str(getattr(via, "via_id", "")).casefold(),
                                str(getattr(via, "endpoint_node_id", "")).casefold(),
                                str(layer).casefold(),
                            )
                            contacts_hash = getattr(
                                connectivity_recovery,
                                "target_contact_hash_by_key",
                                {},
                            ).get(contact_key, contacts_hash)
                            contact_count = getattr(
                                connectivity_recovery,
                                "target_contact_count_by_key",
                                {},
                            ).get(contact_key, len(contacts))
                            route_witnesses.append(
                                {
                                    "refdes": str(getattr(connection, "refdes", "")),
                                    "terminal": terminal_kind,
                                    "via_id": str(getattr(via, "via_id", "")),
                                    "endpoint_node_id": str(getattr(via, "endpoint_node_id", "")),
                                    "target_layer": str(layer),
                                    "target_node_id": None,
                                    "target_x_um": None,
                                    "target_y_um": None,
                                    "target_contacts": [
                                        {"node_id": item[0], "x_um": item[1], "y_um": item[2]}
                                        for item in contacts
                                    ],
                                    "selected_target_contact": (
                                        {
                                            "node_id": selected_contact[0],
                                            "x_um": selected_contact[1],
                                            "y_um": selected_contact[2],
                                            "distance_sq_um2": (
                                                (selected_contact[1] - source_x) ** 2
                                                + (selected_contact[2] - source_y) ** 2
                                            ),
                                        }
                                        if selected_contact
                                        else None
                                    ),
                                    "target_contacts_sha256": contacts_hash,
                                    "target_contact_count": int(contact_count),
                                    "target_pad_width_um": (
                                        effective_footprint[0]
                                        if effective_footprint is not None
                                        else None
                                    ),
                                    "target_pad_height_um": (
                                        effective_footprint[1]
                                        if effective_footprint is not None
                                        else None
                                    ),
                                    "artwork_coverage_basis": "SOURCE_GRAPH_TARGET_NODE_STRICT_INTERIOR",
                                    "solver_port_basis": "RECTANGULAR_CAVITY_FINITE_PORT_V1",
                                    "selection_basis": "NEAREST_COMPONENT_TARGET",
                                    "trace_hops": 0,
                                    "trace_alternate_exit": True,
                                    "reachability_claim_sha256": sha256(
                                        f"{source_sha256}|{getattr(via, 'via_id', '')}|{getattr(via, 'endpoint_node_id', '')}|{layer}".encode("utf-8")
                                    ).hexdigest(),
                                    "segment_count": 0,
                                    "connectivity_only": True,
                                }
                            )
                            continue
                        witness_payload = {
                            "source_sha256": source_sha256,
                            "refdes": str(getattr(connection, "refdes", "")),
                            "terminal": terminal_kind,
                            "net": str(net),
                            "via_id": str(getattr(via, "via_id", "")),
                            "endpoint_node_id": str(getattr(via, "endpoint_node_id", "")),
                            "target_layer": str(evidence.target_layer),
                            "target_node_id": str(evidence.target_node_id),
                            "target_padstack": str(evidence.target_padstack),
                            "target_x_um": float(evidence.target_x_um),
                            "target_y_um": float(evidence.target_y_um),
                            "target_pad_width_um": float(evidence.target_pad_width_um),
                            "target_pad_height_um": float(evidence.target_pad_height_um),
                            "trace_hops": int(evidence.trace_hops),
                            "trace_alternate_exit": bool(evidence.trace_alternate_exit),
                            "segments": [
                                {
                                    "via_id": str(segment.via_id),
                                    "padstack": str(segment.padstack),
                                    "start_layer": str(segment.start_layer),
                                    "end_layer": str(segment.end_layer),
                                    "length_um": float(segment.length_um),
                                    "end_x_um": float(segment.end_x_um),
                                    "end_y_um": float(segment.end_y_um),
                                }
                                for segment in evidence.segments
                            ],
                        }
                        canonical = json.dumps(
                            witness_payload, sort_keys=True, separators=(",", ":")
                        ).encode("utf-8")
                        route_witnesses.append(
                            {
                                "refdes": witness_payload["refdes"],
                                "terminal": witness_payload["terminal"],
                                "via_id": witness_payload["via_id"],
                                "endpoint_node_id": witness_payload["endpoint_node_id"],
                                "target_layer": witness_payload["target_layer"],
                                "target_node_id": witness_payload["target_node_id"],
                                "target_x_um": witness_payload["target_x_um"],
                                "target_y_um": witness_payload["target_y_um"],
                                "target_pad_width_um": witness_payload["target_pad_width_um"],
                                "target_pad_height_um": witness_payload["target_pad_height_um"],
                                "trace_hops": witness_payload["trace_hops"],
                                "trace_alternate_exit": witness_payload["trace_alternate_exit"],
                                "component_witness_sha256": sha256(canonical).hexdigest(),
                                "segment_count": len(evidence.segments),
                            }
                        )
            proof = {
                "pwr_layer": suggestion.pwr_layer,
                "gnd_layer": suggestion.gnd_layer,
                "separation_um": float(suggestion.separation_um),
                "route_count": route_count,
                "missing_route_count": missing_routes,
                "ambiguous_route_count": ambiguous_routes,
                "route_witnesses": route_witnesses,
                "target_exit_count": len(route_witnesses),
                "device_route_proven_count": device_route_proven,
                "device_route_witnesses": sorted(
                    device_route_witnesses,
                    key=lambda item: (str(item["terminal"]), str(item["pin_id"])),
                ),
                "excluded_device_gnd_count": len(excluded_device_gnd_ids),
                "excluded_device_gnd_ids": sorted(excluded_device_gnd_ids, key=str.casefold),
                "excluded_device_gnd_ids_sha256": sha256(
                    "|".join(sorted(excluded_device_gnd_ids, key=str.casefold)).encode("utf-8")
                ).hexdigest(),
                "device_terminal_footprint_mode": (
                    "SOURCE_GRAPH_TARGET_NODE_STRICT_INTERIOR__RECTANGULAR_CAVITY_FINITE_PORT_V1"
                ),
                "graph_target_artwork_basis": (
                    "SOURCE_GRAPH_TARGET_NODE_STRICT_INTERIOR"
                    if any(bool(item.get("connectivity_only")) for item in route_witnesses)
                    or any(
                        str(item.get("artwork_coverage_basis", "")).upper()
                        == "SOURCE_GRAPH_TARGET_NODE_STRICT_INTERIOR"
                        for item in device_route_witnesses
                    )
                    else "SOURCE_EXACT_TARGET_PAD_FINITE_ARTWORK"
                ),
                "solver_port_geometry_basis": "RECTANGULAR_CAVITY_FINITE_PORT_V1",
                "graph_contact_confidence": "LOW",
                "power_si_signoff": False,
                "source_graph_capability": (
                    "TRACE_VIA_COMPONENTS_AVAILABLE"
                    if int(graph_statistics.get("trace_edges", 0)) > 0
                    else "VIA_COMPONENTS_AVAILABLE"
                    if graph_available
                    else "LEGACY_SOURCE_GRAPH_UNAVAILABLE"
                ),
                "vertical_impedance_model": (
                    "LEGACY_SOURCE_GRAPH_UNAVAILABLE"
                    if not graph_available
                    else "SOURCE_PROVEN_SEGMENT_RL"
                    if route_witnesses and all(
                        int(item["trace_hops"]) == 0
                        and int(item["segment_count"]) == 1
                        for item in route_witnesses
                    )
                    else "SOURCE_PROVEN_GRAPH_CONNECTIVITY_LEGACY_TEMPLATE"
                ),
                "uncovered_terminal_count": uncovered,
                "device_pwr_pin_count": sum(
                    1 for item in device_route_witnesses
                    if str(item.get("terminal", "")).upper() == "PWR"
                ),
                "device_pwr_uncovered_count": sum(
                    1 for item in device_route_witnesses
                    if str(item.get("terminal", "")).upper() == "PWR"
                    and not bool(item.get("reachable"))
                ),
                "device_gnd_participating_count": participating_device_gnd_count,
                "decap_uncovered_count": max(
                    0,
                    uncovered
                    - sum(
                        1 for item in device_route_witnesses
                        if str(item.get("terminal", "")).upper() == "PWR"
                        and not bool(item.get("reachable"))
                    ),
                ),
                "pwr_artwork_proven": pwr is not None,
                "gnd_artwork_proven": gnd is not None,
                "pwr_artwork_net": pwr_key[1] if pwr is not None else None,
                "gnd_artwork_net": gnd_key[1] if gnd is not None else None,
                "pwr_artwork_sha256": artwork_digest(pwr),
                "gnd_artwork_sha256": artwork_digest(gnd),
                "source_sha256": source_sha256,
            }
            rank = (
                float(missing_routes > 0 or ambiguous_routes > 0),
                float(uncovered),
                float(missing_routes),
                float(ambiguous_routes),
                float(suggestion.separation_um),
                float(suggestion.pwr_index),
                float(suggestion.gnd_index),
            )
            if (
                not legacy_simple_import
                and
                not missing_routes
                and not ambiguous_routes
                and not uncovered
                and pwr is not None
                and gnd is not None
            ) or (
                legacy_preexisting_pair
                and (
                    str(net).casefold(),
                    suggestion.pwr_layer.casefold(),
                    suggestion.gnd_layer.casefold(),
                )
                in existing_pair_keys
                and pwr is not None
                and gnd is not None
            ):
                if legacy_preexisting_pair:
                    proof["selection_mode"] = "LEGACY_PREEXISTING_PAIR"
                candidates.append((rank, suggestion, proof))
            else:
                candidate_diagnostics.append(
                    f"{suggestion.pwr_layer}/{suggestion.gnd_layer}:"
                    f"missing={missing_routes},ambiguous={ambiguous_routes},"
                    f"uncovered={uncovered},artwork={pwr is not None}/{gnd is not None}"
                )
        if not candidates:
            # Keep an existing rail pair for unrelated/unresolved nets so the
            # raw import remains atomic for proven rails.  The unresolved
            # marker is persisted and Evaluation blocks that rail explicitly;
            # this is not a connectivity waiver or alternate fallback.
            existing_suggestion = next(
                (
                    item
                    for item in suggestions
                    if (
                        str(net).casefold(),
                        item.pwr_layer.casefold(),
                        item.gnd_layer.casefold(),
                    )
                    in existing_pair_keys
                ),
                None,
            )
            if existing_suggestion is not None:
                gnd_layer_obj = next(
                    (
                        item
                        for item in project.stackup_layers
                        if item.name.casefold() == existing_suggestion.gnd_layer.casefold()
                    ),
                    None,
                )
                gnd_net = next(
                    (
                        item
                        for item in (gnd_layer_obj.pwr_nets if gnd_layer_obj is not None else ())
                        if item.casefold() in gnd_keys
                    ),
                    "",
                )
                selected[str(net).casefold()] = existing_suggestion
                provenance[str(net).casefold()] = {
                    "rail_net": str(net),
                    "pwr_layer": existing_suggestion.pwr_layer,
                    "gnd_layer": existing_suggestion.gnd_layer,
                    "separation_um": float(existing_suggestion.separation_um),
                    "selection_mode": "LEGACY_PREEXISTING_PAIR_UNRESOLVED",
                    "source_graph_pair_unresolved": True,
                    "selected_plane_pair_failures": tuple(candidate_diagnostics),
                    "pwr_artwork_net": str(net),
                    "gnd_artwork_net": gnd_net,
                    "source_sha256": source_sha256,
                    "source_graph_capability": (
                        "TRACE_VIA_COMPONENTS_AVAILABLE"
                        if int(graph_statistics.get("trace_edges", 0)) > 0
                        else "VIA_COMPONENTS_AVAILABLE"
                        if graph_available
                        else "LEGACY_SOURCE_GRAPH_UNAVAILABLE"
                    ),
                    "route_count": 0,
                    "missing_route_count": 0,
                    "ambiguous_route_count": 0,
                    "uncovered_terminal_count": 0,
                    "route_witnesses": [],
                    "device_route_witnesses": [],
                    "pwr_artwork_proven": False,
                    "gnd_artwork_proven": False,
                    "pwr_artwork_sha256": "",
                    "gnd_artwork_sha256": "",
                }
                continue
            failures.append(
                f"{net}: no source-proven graph-connected PWR/GND pair covers every terminal route "
                f"(suggestions={len(suggestions)}, relevant={len(relevant)}, "
                f"connectivity={len(getattr(connectivity_recovery, 'reachable_keys', ()))}, "
                f"candidates={'|'.join(candidate_diagnostics)})"
            )
            continue
        _, suggestion, proof = min(candidates, key=lambda item: item[0])
        proof["rail_net"] = str(net)
        selected[str(net).casefold()] = suggestion
        provenance[str(net).casefold()] = proof
    if failures:
        raise SpdImportError(
            "SPD source-proven plane-pair selection is blocked: " + "; ".join(failures[:8])
        )
    return selected, provenance


def _mixed_reference_target_node_predicate(
    project: ProjectSpec,
    attachments: dict[str, bytes],
    *,
    indexed_geometry_by_key: Mapping[tuple[str, str], IndexedPlaneGeometry]
    | None = None,
) -> Callable[[str, str, str, float, float], bool]:
    """Build fail-closed strict-interior tests for certificate DGND artwork."""

    indexed_by_key = indexed_geometry_by_key or {}
    try:
        from shapely.geometry import Point
        from shapely.prepared import prep
    except ImportError as exc:
        raise SpdImportError(
            "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: Shapely is required "
            "to verify GND target-node artwork"
        ) from exc
    spd_import = project.metadata.get("spd_import")
    records = spd_import.get("plane_geometries") if isinstance(spd_import, dict) else None
    if not isinstance(records, list):
        raise SpdImportError(
            "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: retained DGND geometry index is missing"
        )
    shapes: dict[tuple[str, str], tuple[Any, tuple[float, float, float, float]]] = {}
    indexed_shapes: dict[tuple[str, str], IndexedPlaneGeometry] = {}
    for rail in project.rails:
        certificate = rail.mixed_reference_certificate
        if certificate is None:
            continue
        key = (certificate.gnd_net.casefold(), certificate.gnd_layer.casefold())
        if key in shapes:
            continue
        matching = [
            item for item in records
            if isinstance(item, dict)
            and str(item.get("net", "")).casefold() == key[0]
            and str(item.get("layer", "")).casefold() == key[1]
            and str(item.get("asset_sha256", "")) == certificate.gnd_asset_sha256
        ]
        if len(matching) != 1:
            raise SpdImportError(
                "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: certificate DGND "
                f"asset binding is missing or ambiguous for {rail.rail_id}"
            )
        record = matching[0]
        asset_name = str(record.get("asset", ""))
        compressed = attachments.get(asset_name)
        if compressed is None:
            raise SpdImportError(
                "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: certificate DGND "
                f"asset is absent for {rail.rail_id}"
            )
        try:
            payload = core_services._decode_spd_geometry_asset(
                certificate.gnd_asset_sha256, compressed
            )
            core_services._validate_spd_geometry_payload(
                payload,
                expected_layer=certificate.gnd_layer,
                expected_net=certificate.gnd_net,
            )
        except (ValueError, ArithmeticError) as exc:
            raise SpdImportError(
                "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: certificate DGND "
                f"geometry is invalid for {rail.rail_id}: {exc}"
            ) from exc
        indexed = indexed_by_key.get(key)
        if indexed is not None:
            # The compressed certificate asset has been decoded and validated
            # above; reuse the already-built source index so exact ordered
            # boolean artwork is not materialized twice for the same layer.
            indexed_shapes[key] = indexed
            continue
        shape = core_services._ordered_spd_geometry(payload)
        if shape is None:
            raise SpdImportError(
                "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: certificate DGND "
                f"geometry cannot be constructed for {rail.rail_id}"
            )
        shapes[key] = (prep(shape), tuple(map(float, shape.bounds)))

    def accepts(net: str, layer: str, _node_id: str, x_um: float, y_um: float) -> bool:
        indexed = indexed_shapes.get((net.casefold(), layer.casefold()))
        if indexed is not None:
            try:
                return indexed.artwork_component(float(x_um), float(y_um)) is not None
            except Exception as exc:
                raise SpdImportError(
                    "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: DGND target-node "
                    f"geometry check failed: {exc}"
                ) from exc
        prepared = shapes.get((net.casefold(), layer.casefold()))
        if prepared is None:
            return False
        shape, (x_min, y_min, x_max, y_max) = prepared
        if not (x_min < x_um < x_max and y_min < y_um < y_max):
            return False
        # Strict interior is intentional: a target exactly on an artwork edge
        # is numerically/physically ambiguous, so the certificate fails closed.
        try:
            return bool(shape.contains(Point(float(x_um), float(y_um))))
        except Exception as exc:  # GEOS failures must never become connectivity.
            raise SpdImportError(
                "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: DGND target-node "
                f"geometry check failed: {exc}"
            ) from exc

    return accepts


def _raise_for_rejected_mixed_reference_landings(
    project: ProjectSpec,
    source_landings: tuple[Any, ...],
    path_recovery: Any,
) -> None:
    """Block a fallback pair when source Via evidence lands on a rejected pair."""

    spd_import = project.metadata.get("spd_import")
    failed_candidates = (
        spd_import.get("mixed_reference_certificate_failures", ())
        if isinstance(spd_import, dict)
        else ()
    )
    blocking_failures: list[dict[str, Any]] = []
    for failure in failed_candidates if isinstance(failed_candidates, list) else ():
        if not isinstance(failure, dict):
            continue
        if failure.get("blocking") is False:
            continue
        net_key = str(failure.get("rail_net", "")).casefold()
        target_key = str(failure.get("pwr_layer", "")).casefold()
        if any(
            landing.net.casefold() == net_key
            and any(
                evidence.target_layer.casefold() == target_key
                for evidence in path_recovery.evidence_by_via.get(
                    landing.via_id.casefold(), ()
                )
            )
            for landing in source_landings
        ):
            blocking_failures.append(failure)
    if not blocking_failures:
        return
    details = "; ".join(
        f"{item.get('rail_net')} {item.get('pwr_layer')}/{item.get('gnd_layer')}"
        for item in blocking_failures[:5]
    )
    raise SpdImportError(
        "SPD_MIXED_REFERENCE_CERTIFICATE_REQUIRED: source Via path evidence "
        f"lands on rejected mixed-reference pair(s): {details}"
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


def _select_mixed_reference_ground_landings(
    *,
    mixed_rails: dict[str, Any],
    top_instances: tuple[SpdCapInstance, ...],
    parsed_connection_by_key: dict[str, Any],
    parsed_cluster_by_key: dict[str, Any],
    path_recovery: Any,
    eligibility_index: PlaneEligibilityIndex,
    rail_choices_by_pair: dict[tuple[str, str, str], tuple[tuple[Any, str], ...]],
) -> tuple[dict[str, list[tuple[str, Any]]], dict[str, set[str]]]:
    """Select source GND landing witnesses once per physical decap/cluster.

    The previous rail-major traversal recomputed a direct decap's common PWR
    eligibility once for every mixed rail.  Invert that traversal: calculate
    it once, then append its matching source GND landings to each eligible
    mixed rail.  ``top_instances`` remains the outer order, so each per-rail
    witness list keeps the prior source order and shared clusters remain at
    their first anchor occurrence.
    """

    result: dict[str, list[tuple[str, Any]]] = {
        key: [] for key in mixed_rails
    }
    target_layers_by_net: dict[str, set[str]] = {}
    mixed_key_by_rail_id: dict[str, str] = {}
    gnd_key_by_mixed_rail: dict[str, str] = {}
    for rail_key, rail in mixed_rails.items():
        certificate = rail.mixed_reference_certificate
        assert certificate is not None
        mixed_key_by_rail_id[rail.rail_id.casefold()] = rail_key
        gnd_key = certificate.gnd_net.casefold()
        gnd_key_by_mixed_rail[rail_key] = gnd_key
        target_layers_by_net.setdefault(gnd_key, set()).add(certificate.gnd_layer)

    def eligible_mixed_keys(
        eligibility: dict[str, RailEligibility],
    ) -> tuple[str, ...]:
        # ``_common_eligibility_*`` already returns only allowed entries.  Keep
        # the explicit check as a fail-closed guard for future callers.
        return tuple(
            mixed_key
            for rail_id, item in eligibility.items()
            if item.allowed
            and (mixed_key := mixed_key_by_rail_id.get(rail_id.casefold()))
            is not None
        )

    processed_shared_clusters: set[str] = set()
    for instance in top_instances:
        connection = parsed_connection_by_key.get(instance.refdes.casefold())
        if connection is None or connection.kind not in {"DIRECT", "SHARED_ANCHOR"}:
            continue
        if connection.kind == "DIRECT":
            power_vias = tuple(
                _scenario_via_landing(landing, path_recovery)
                for landing in connection.power_vias
            )
            eligible = _common_eligibility_at_landings(
                eligibility_index,
                power_vias,
                rail_choices_by_pair,
            )
            mixed_keys = eligible_mixed_keys(eligible)
            owner = (
                f"cluster:{connection.cluster_id}"
                if connection.cluster_id is not None
                else instance.refdes
            )
            for rail_key in mixed_keys:
                gnd_key = gnd_key_by_mixed_rail[rail_key]
                result[rail_key].extend(
                    (owner, landing)
                    for landing in connection.ground_vias
                    if landing.net.casefold() == gnd_key
                )
            continue

        assert connection.cluster_id is not None
        cluster_key = connection.cluster_id.casefold()
        if cluster_key in processed_shared_clusters:
            continue
        processed_shared_clusters.add(cluster_key)
        cluster = parsed_cluster_by_key.get(cluster_key)
        if cluster is None:
            continue
        power_landings = {
            landing.via_id.casefold(): _scenario_via_landing(landing, path_recovery)
            for member in cluster.member_refdes
            for landing in parsed_connection_by_key[member.casefold()].power_vias
        }
        common = _common_eligibility_maps(
            tuple(
                _eligibility_for_via_landing(
                    eligibility_index, landing, rail_choices_by_pair
                )
                for landing in power_landings.values()
            )
        )
        mixed_keys = eligible_mixed_keys(common)
        if not mixed_keys:
            continue
        owner = f"cluster:{cluster.cluster_id}"
        ground_landings = tuple(
            landing
            for member in cluster.member_refdes
            for landing in parsed_connection_by_key[member.casefold()].ground_vias
        )
        for rail_key in mixed_keys:
            gnd_key = gnd_key_by_mixed_rail[rail_key]
            result[rail_key].extend(
                (owner, landing)
                for landing in ground_landings
                if landing.net.casefold() == gnd_key
            )
    return result, target_layers_by_net


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


def _build_mlo_landing_certificates(
    source_landings: tuple[Any, ...],
    *,
    padstacks: tuple[Any, ...],
    stackup_layers: tuple[Any, ...],
    source_sha256: str,
) -> dict[str, object]:
    """Persist a conservative, source-bound classification for every landing.

    A pathless landing is classified as conventional only when the raw
    PadStack has a positive drill and explicit TOP and BOTTOM copper pads.  A
    short-span padstack (for example DR-0102 TOP-to-L02) is unresolved and can
    never bypass the MLO gate.  This does not infer a translated MLO recipe;
    it merely preserves the narrow PTH evidence that survives import.
    """

    top = next((layer.name for layer in stackup_layers if layer.is_conductor), None)
    conductors = [layer.name for layer in stackup_layers if layer.is_conductor]
    bottom = conductors[-1] if conductors else None
    padstack_by_key = {str(item.name).casefold(): item for item in padstacks}
    rows: dict[str, dict[str, object]] = {}
    seen: set[str] = set()
    for landing in source_landings:
        via_id = str(getattr(landing, "via_id", "")).strip()
        key = via_id.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        padstack = padstack_by_key.get(str(getattr(landing, "padstack", "")).casefold())
        layer_names = {
            str(item).strip() for item in (getattr(padstack, "layers", ()) or ())
            if str(item).strip()
        }
        layer_keys = {item.casefold() for item in layer_names}
        drill = getattr(padstack, "drill_diameter_um", None)
        material = getattr(padstack, "material", None)
        normalized_material = material.strip() if isinstance(material, str) else None
        material_is_copper = (
            normalized_material is not None
            and normalized_material.casefold() == "copper"
        )
        positive_drill = bool(
            isinstance(drill, (int, float))
            and isfinite(float(drill))
            and float(drill) > 0.0
        )
        conventional = bool(
            padstack is not None
            and top
            and bottom
            and top.casefold() in layer_keys
            and bottom.casefold() in layer_keys
            and positive_drill
            and material_is_copper
        )
        short_span = bool(
            padstack is not None
            and top
            and top.casefold() in layer_keys
            and any(
                layer.casefold() in layer_keys
                for layer in conductors[1:-1]
            )
            and not (bottom and bottom.casefold() in layer_keys)
            and positive_drill
            and material_is_copper
        )
        rows[via_id] = {
            "classification": (
                MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA
                if conventional
                else MLO_LANDING_CLASS_SHORT_SPAN_VIA
                if short_span
                else "UNRESOLVED"
            ),
            "padstack": str(getattr(landing, "padstack", "")),
            "span_layers": sorted(layer_names, key=str.casefold),
            "drill_diameter_um": (
                float(drill) if isinstance(drill, (int, float)) else None
            ),
            "padstack_material": normalized_material,
        }
    canonical_ids = sorted(seen)
    ids_hash = sha256(
        (json.dumps(canonical_ids, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    conventional_count = sum(
        item["classification"] == MLO_LANDING_CLASS_CONVENTIONAL_THROUGH_VIA
        for item in rows.values()
    )
    short_span_count = sum(
        item["classification"] == MLO_LANDING_CLASS_SHORT_SPAN_VIA
        for item in rows.values()
    )
    return {
        "certificate_version": MLO_LANDING_CERTIFICATE_VERSION,
        "source_sha256": source_sha256,
        "landing_count": len(canonical_ids),
        "landing_ids_sha256": ids_hash,
        "conventional_count": conventional_count,
        "short_span_count": short_span_count,
        "claims_sha256": mlo_landing_certificate_claims_sha256(rows),
        "by_via_id": rows,
    }


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
    geometry_donor_plan = plan
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
    # Preserve the first normalized project so a later final-template failure
    # can restore the original rail/template/partition binding safely.
    preselection_project = base_project.model_copy(deep=True)
    preselection_pairs: dict[str, PlanePairSuggestion] = {}
    for rail in preselection_project.rails:
        matching = next(
            (
                item
                for item in suggest_effective_plane_pairs(
                    preselection_project.stackup_layers,
                    rail_net=rail.net,
                    gnd_aliases=preselection_project.gnd_aliases,
                    mixed_reference_certificates=tuple(
                        certificate
                        for candidate_rail in preselection_project.rails
                        if candidate_rail.net.casefold() == rail.net.casefold()
                        for certificate in (candidate_rail.mixed_reference_certificate,)
                        if certificate is not None
                    ),
                )
                if item.pwr_layer.casefold() == rail.pwr_layer.casefold()
                and item.gnd_layer.casefold() == rail.gnd_layer.casefold()
            ),
            None,
        )
        if matching is not None:
            preselection_pairs[rail.net.casefold()] = matching
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
        mixed_reference_certificates=tuple(
            rail.mixed_reference_certificate
            for rail in base_project.rails
            if rail.mixed_reference_certificate is not None
        ),
        selected_pairs=tuple(
            PlanePairSuggestion(
                rail_net=rail.net,
                pwr_layer=rail.pwr_layer,
                gnd_layer=rail.gnd_layer,
                pwr_index=0,
                gnd_index=0,
                separation_um=0.0,
                mixed_reference_certificate=rail.mixed_reference_certificate,
            )
            for rail in base_project.rails
        ),
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
    device_pin_landings = tuple(
        _SourcePinLanding(
            via_id=f"PIN::{pin.pin_id}",
            net=pin.net,
            endpoint_node_id=str(pin.source_node_id),
            x_um=float(pin.x_um),
            y_um=float(pin.y_um),
        )
        for pin in analysis.pins
        if pin.kind == PinKind.DEVICE_BUMP and pin.source_node_id
    )
    source_graph_landings = source_landings + device_pin_landings
    # Pair selection is graph-first for sources that expose a retained
    # Trace/Via component graph.  A legacy source with no graph capability may
    # still carry direct via-only chains; recover those once so compatibility
    # imports retain their historical route witnesses without using a size
    # heuristic or silently claiming graph connectivity.
    graph_available = False
    path_recovery = SimpleNamespace(
        evidence_by_via={},
        structural_evidence_by_via={},
        diagnostics=(),
        statistics={
            "algorithm": "graph_connectivity_contact_recovery_v1",
            "skipped_unique_path_recovery": 1,
        },
    )
    # This stage covers the shared source Node/Trace/Via graph pass.  Legacy
    # unique-path fallback time is retained separately in ``path_recovery_s``.
    recovery_s = 0.0
    path_recovery_s = 0.0
    # Build one shared artwork index, retaining only unique (layer, net)
    # records.  Duplicate retained records are ambiguous evidence and must
    # remain absent rather than being silently overwritten by a dict
    # comprehension (which could make strict/final validation disagree).
    target_layers_by_net = _via_target_layers_by_net(
        base_project,
        plane_geometries=analysis.plane_geometries,
    )
    ground_keys = {str(item).casefold() for item in base_project.gnd_aliases}
    requested_geometry_keys = {
        (str(layer).casefold(), str(net).casefold())
        for net, layers in target_layers_by_net.items()
        for layer in layers
    }
    connectivity_geometry_groups: dict[tuple[str, str], list[Any]] = {}
    for item in analysis.plane_geometries:
        key = (str(item.layer).casefold(), str(item.net).casefold())
        connectivity_geometry_groups.setdefault(key, []).append(item)
    connectivity_index = {
        key: indexed
        for key, values in connectivity_geometry_groups.items()
        if (
            len(values) == 1
            and values[0].primitive_order
            and (key[1] in ground_keys or key in requested_geometry_keys)
        )
        for indexed in (IndexedPlaneGeometry.build(values[0]),)
        if indexed is not None
    }

    # Same-NET copper on an intermediate conductor plane is an electrical graph
    # edge, even when PowerSI did not emit an explicit Trace for that plane.
    # Restrict this to the requested graph nets; solver cavity checks remain
    # unchanged and still validate exact PWR/GND geometry separately.
    artwork_layers_by_net: dict[str, tuple[str, ...]] = {}
    for (layer_key, net_key), _indexed in connectivity_index.items():
        if net_key not in ground_keys or net_key not in target_layers_by_net:
            continue
        artwork_layers_by_net.setdefault(net_key, ())
        artwork_layers_by_net[net_key] = tuple(
            sorted(
                {*artwork_layers_by_net[net_key], _indexed.geometry.layer},
                key=str.casefold,
            )
        )
    target_layer_keys_by_net = {
        net: {str(layer).casefold() for layer in layers}
        for net, layers in target_layers_by_net.items()
    }
    artwork_query_cache: tuple[tuple[str, str, float, float], object | None] | None = None
    artwork_batch_result_cache: dict[tuple[str, str, float, float], object | None] = {}

    def source_artwork_component(
        net: str, layer: str, x_um: float, y_um: float
    ) -> object | None:
        nonlocal artwork_query_cache
        cache_key = (net.casefold(), layer.casefold(), float(x_um), float(y_um))
        if artwork_query_cache is not None and artwork_query_cache[0] == cache_key:
            return artwork_query_cache[1]
        indexed = connectivity_index.get((layer.casefold(), net.casefold()))
        result = (
            None
            if indexed is None
            else indexed.artwork_component(float(x_um), float(y_um))
        )
        artwork_query_cache = (cache_key, result)
        return result

    def source_artwork_components_batch(
        net: str, layer: str, points: tuple[tuple[float, float], ...]
    ) -> tuple[object | None, ...]:
        indexed = connectivity_index.get((layer.casefold(), net.casefold()))
        if indexed is None:
            return (None,) * len(points)
        result = indexed.artwork_components_batch(points)
        # Only retain results for target layers: artwork-only points can never
        # be consumed by the target resolver and would otherwise retain ~1M
        # coordinate keys through the entire import.
        if (
            layer.casefold() in target_layer_keys_by_net.get(net.casefold(), set())
            and (net.casefold(), layer.casefold()) not in target_node_predicates_by_key
        ):
            for point, component in zip(points, result, strict=True):
                artwork_batch_result_cache[
                    (net.casefold(), layer.casefold(), float(point[0]), float(point[1]))
                ] = component
        return result

    def source_artwork_release(net: str, layer: str) -> None:
        nonlocal artwork_query_cache
        key = (net.casefold(), layer.casefold())
        if artwork_query_cache is not None and artwork_query_cache[0][:2] == key:
            artwork_query_cache = None
        if net.casefold() not in ground_keys:
            return
        indexed = connectivity_index.get((layer.casefold(), net.casefold()))
        if indexed is not None:
            indexed.release_artwork_shape()

    def source_trace_contact(
        net: str,
        _trace_id: str,
        _first_node: str,
        _second_node: str,
        first_layer: str,
        first_x: float,
        first_y: float,
        second_layer: str,
        second_x: float,
        second_y: float,
        width_um: float,
    ) -> tuple[str, object] | None:
        if not isfinite(float(width_um)) or float(width_um) <= 0.0:
            return None
        target_layers = target_layer_keys_by_net.get(net.casefold(), set())
        if net.casefold() in ground_keys:
            return None
        if first_layer.casefold() != second_layer.casefold():
            return None
        indexed_candidate = connectivity_index.get(
            (first_layer.casefold(), net.casefold())
        )
        if indexed_candidate is None:
            return None
        first_status = indexed_candidate.contains(float(first_x), float(first_y))
        second_status = indexed_candidate.contains(float(second_x), float(second_y))
        if (
            "inside" in {first_status, second_status}
            or "boundary" not in {first_status, second_status}
        ):
            return None
        for layer, x_um, y_um in (
            (first_layer, first_x, first_y),
            (second_layer, second_x, second_y),
        ):
            if layer.casefold() not in target_layers:
                continue
            indexed = connectivity_index.get((layer.casefold(), net.casefold()))
            if indexed is None:
                continue
            if indexed.contains(float(x_um), float(y_um)) == "inside":
                # Existing strict target Nodes already provide the witness;
                # do not manufacture a duplicate synthetic contact.
                return None
            contact = indexed.artwork_trace_component(
                first_x,
                first_y,
                second_x,
                second_y,
                width_um,
            )
            if contact is not None:
                component, _contact_x, _contact_y = contact
                return layer, component
        return None

    def source_target_node_inside(
        net: str, layer: str, _node_id: str, x_um: float, y_um: float
    ) -> bool:
        if net.casefold() in ground_keys:
            return source_artwork_component(net, layer, x_um, y_um) is not None
        geometry = connectivity_index.get((layer.casefold(), net.casefold()))
        return geometry is not None and geometry.contains(float(x_um), float(y_um)) == "inside"

    # The source graph and mixed-reference witness checks need the same raw
    # Node/Trace/Via graph.  Include the certified DGND target keys in this
    # first pass and select the stricter certificate predicate only for those
    # keys; this lets the later witness phase reuse the completed reachability
    # result instead of reopening and rescanning the 1+ GB SPD.
    mixed_graph_target_layers_by_net: dict[str, set[str]] = {}
    for rail in base_project.rails:
        certificate = rail.mixed_reference_certificate
        if certificate is None:
            continue
        mixed_graph_target_layers_by_net.setdefault(
            certificate.gnd_net.casefold(), set()
        ).add(certificate.gnd_layer)
    target_node_predicates_by_key: dict[
        tuple[str, str], Callable[[str, str, str, float, float], bool]
    ] = {}
    if mixed_graph_target_layers_by_net:
        mixed_graph_predicate = _mixed_reference_target_node_predicate(
            base_project,
            dict(plan.attachments),
            indexed_geometry_by_key={
                (net, layer): indexed
                for (layer, net), indexed in connectivity_index.items()
            },
        )
        for net, layers in mixed_graph_target_layers_by_net.items():
            for layer in layers:
                target_node_predicates_by_key[(net, layer.casefold())] = (
                    mixed_graph_predicate
                )
    graph_target_layers_by_net = {
        net: set(layers) for net, layers in target_layers_by_net.items()
    }
    for net, layers in mixed_graph_target_layers_by_net.items():
        graph_target_layers_by_net.setdefault(net, set()).update(layers)

    def source_target_nodes_batch(
        net: str, layer: str, points: tuple[tuple[float, float], ...]
    ) -> tuple[bool, ...]:
        strict = target_node_predicates_by_key.get((net.casefold(), layer.casefold()))
        if strict is not None:
            return tuple(strict(net, layer, "BATCH", x_um, y_um) for x_um, y_um in points)
        indexed = connectivity_index.get((layer.casefold(), net.casefold()))
        if indexed is None:
            return (False,) * len(points)
        if net.casefold() in ground_keys:
            components: list[object | None] = []
            missing_indices: list[int] = []
            consumed_keys: set[tuple[str, str, float, float]] = set()
            for index, (x_um, y_um) in enumerate(points):
                cache_key = (net.casefold(), layer.casefold(), float(x_um), float(y_um))
                if cache_key in artwork_batch_result_cache:
                    # Read shared results first so duplicate-coordinate Nodes
                    # in one batch reuse the same exact lookup. Evict once the
                    # complete batch has been reconstructed below.
                    components.append(artwork_batch_result_cache[cache_key])
                    consumed_keys.add(cache_key)
                else:
                    components.append(None)
                    missing_indices.append(index)
            if missing_indices:
                fresh = indexed.artwork_components_batch(
                    tuple(points[index] for index in missing_indices)
                )
                if len(fresh) != len(missing_indices):
                    raise RuntimeError("artwork batch resolver returned an invalid result length")
                for index, component in zip(missing_indices, fresh, strict=True):
                    components[index] = component
            for cache_key in consumed_keys:
                artwork_batch_result_cache.pop(cache_key, None)
            return tuple(component is not None for component in components)
        return tuple(
            indexed.contains(float(x_um), float(y_um)) == "inside"
            for x_um, y_um in points
        )

    recovery_started = perf_counter()
    connectivity_recovery = recover_spd_ground_reachability(
        source_path,
        landings=source_graph_landings,
        target_layers_by_net=graph_target_layers_by_net,
        target_node_predicate=source_target_node_inside,
        target_node_predicate_batch=source_target_nodes_batch,
        target_node_predicates_by_key=target_node_predicates_by_key,
        same_layer_artwork_layers_by_net=artwork_layers_by_net,
        same_layer_artwork_component=source_artwork_component,
        same_layer_artwork_components_batch=source_artwork_components_batch,
        same_layer_artwork_release=source_artwork_release,
        target_trace_contact_predicate=source_trace_contact,
        expected_source=analysis.source,
        progress=lambda value, message: report(
            87 + round(max(0, min(100, value)) * 4 / 100), message
        ),
        is_cancelled=cancelled,
    )
    # No subsequent import phase consumes these coordinate-keyed results;
    # release any residual tail (e.g. artwork-only target layers) promptly.
    artwork_batch_result_cache.clear()
    recovery_s = perf_counter() - recovery_started
    connectivity_statistics = getattr(connectivity_recovery, "statistics", {}) or {}
    explicit_graph_capability = str(
        (getattr(analysis, "counts", {}) or {}).get("source_graph_capability", "")
    ).upper()
    graph_available = bool(
        explicit_graph_capability != "LEGACY_SOURCE_GRAPH_UNAVAILABLE"
        and int(connectivity_statistics.get("requested", 0)) > 0
        and int(connectivity_statistics.get("node_section_passes", 0)) > 0
        and (
            int(connectivity_statistics.get("trace_edges", 0)) > 0
            or int(connectivity_statistics.get("via_edges", 0)) > 0
        )
    )
    if (
        source_landings
        and not graph_available
    ):
        path_recovery_started = perf_counter()
        legacy_targets = _via_target_layers_by_net(
            base_project,
            plane_geometries=analysis.plane_geometries,
        )
        path_recovery = recover_spd_via_paths(
            source_path,
            landings=source_landings,
            target_layers_by_net=legacy_targets,
            stackup_layers=base_project.stackup_layers,
            padstacks=analysis.padstacks,
            top_layer=top_layer,
            expected_source=analysis.source,
            progress=lambda _value, message: report(91, message),
            is_cancelled=cancelled,
        )
        path_recovery_s = perf_counter() - path_recovery_started
        recovery_s += path_recovery_s
    # The source graph pass proves connectivity before any pair is finalized.
    # Graph-capable imports remain graph/contact-only; the legacy unique-chain
    # recovery above runs once only for explicitly graph-unavailable sources.
    report(91, "Selecting strict source plane pairs")
    selected_pairs, pair_provenance = _strict_source_plane_pairs(
        base_project,
        analysis,
        path_recovery,
        connectivity_recovery,
        indexed_override=connectivity_index,
    )
    report(91, "Selected strict source plane pairs")
    selected_targets: dict[str, set[str]] = {
        str(net).casefold(): {pair.pwr_layer}
        for net, pair in selected_pairs.items()
    }
    for net, pair in selected_pairs.items():
        gnd_layer = next(
            (item for item in base_project.stackup_layers if item.name.casefold() == pair.gnd_layer.casefold()),
            None,
        )
        if gnd_layer is not None:
            for ground_net in gnd_layer.pwr_nets:
                if ground_net.casefold() in {item.casefold() for item in base_project.gnd_aliases}:
                    selected_targets.setdefault(ground_net.casefold(), set()).add(pair.gnd_layer)
                    break
    for net, pair in selected_pairs.items():
        proof = pair_provenance.setdefault(str(net).casefold(), {})
        unique_evidence: list[dict[str, Any]] = []
        for _via_id, evidences in sorted(path_recovery.evidence_by_via.items()):
            for evidence in evidences:
                if evidence.target_layer.casefold() not in {
                    pair.pwr_layer.casefold(), pair.gnd_layer.casefold()
                }:
                    continue
                unique_evidence.append(
                    {
                        "via_id": evidence.via_id,
                        "target_layer": evidence.target_layer,
                        "target_node_id": evidence.target_node_id,
                        "target_x_um": float(evidence.target_x_um),
                        "target_y_um": float(evidence.target_y_um),
                        "target_pad_width_um": float(evidence.target_pad_width_um),
                        "target_pad_height_um": float(evidence.target_pad_height_um),
                        "selection_basis": "UNIQUE_PATH_TARGET",
                        "trace_hops": int(evidence.trace_hops),
                        "trace_alternate_exit": bool(evidence.trace_alternate_exit),
                        "segments": [
                            {
                                "via_id": segment.via_id,
                                "padstack": segment.padstack,
                                "start_layer": segment.start_layer,
                                "end_layer": segment.end_layer,
                                "length_um": float(segment.length_um),
                                "end_x_um": float(segment.end_x_um),
                                "end_y_um": float(segment.end_y_um),
                            }
                            for segment in evidence.segments
                        ],
                    }
                )
        proof["unique_path_evidence"] = unique_evidence
        if unique_evidence:
            # Keep the compact historical route_witnesses view populated for
            # consumers that only inspect selected-path evidence.  Graph-only
            # witnesses remain alongside it when branches were accepted.
            proof["route_witnesses"] = [
                *proof.get("route_witnesses", ()),
                *unique_evidence,
            ]
            proof["target_exit_count"] = len(proof["route_witnesses"])
        unique_rl_complete = bool(unique_evidence) and (
            len(unique_evidence) >= int(proof.get("route_count", 0))
            and not any(
                bool(item.get("connectivity_only"))
                for item in proof.get("route_witnesses", ())
                if isinstance(item, dict)
            )
            and not proof.get("device_route_witnesses")
        )
        if unique_rl_complete and all(
            int(item["trace_hops"]) == 0
            and len(item["segments"]) == 1
            for item in unique_evidence
        ):
            proof["vertical_impedance_model"] = "SOURCE_PROVEN_SEGMENT_RL"
        else:
            proof["vertical_impedance_model"] = (
                "SOURCE_PROVEN_GRAPH_CONNECTIVITY_LEGACY_TEMPLATE"
            )
    report(91, "Building second SPD import plan")
    plan = build_spd_import_plan(
        state.project,
        analysis,
        source_path,
        selected_pairs=selected_pairs,
        selected_pair_provenance=pair_provenance,
        _geometry_from_plan=geometry_donor_plan,
    )
    base_project = _normalized_base_project(plan.project)
    report(91, "Built second SPD import plan")

    # The first pair-selection pass may not yet have a ViaLoopTemplate for a
    # newly selected internal pair.  Re-check every persisted target contact
    # against the *final* template footprint after the import plan derives
    # those templates.  This is exact ordered artwork containment; bbox-only
    # acceptance is not sufficient for voids or boundary contact.
    final_shapes: dict[tuple[str, str], IndexedPlaneGeometry | None] = {}
    final_artwork_digests: dict[tuple[str, str], str] = {}

    def final_shape(layer: str, net: str) -> Any | None:
        key = (str(layer).casefold(), str(net).casefold())
        if key not in final_shapes:
            indexed_plane = connectivity_index.get(key)
            geometry_values = connectivity_geometry_groups.get(key, ())
            # Duplicate or malformed records are intentionally absent from
            # the shared index and therefore fail closed here as well.
            if indexed_plane is None or len(geometry_values) != 1:
                final_shapes[key] = None
            else:
                geometry = geometry_values[0]
                payload = {
                    "positive_polygons_um": [list(item) for item in geometry.positive_polygons_um],
                    "negative_polygons_um": [list(item) for item in geometry.negative_polygons_um],
                    "positive_circles_um": [list(item) for item in geometry.positive_circles_um],
                    "negative_circles_um": [list(item) for item in geometry.negative_circles_um],
                    "primitive_order": [list(item) for item in geometry.primitive_order],
                }
                final_shapes[key] = indexed_plane
                final_artwork_digests[key] = sha256(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
        return final_shapes[key]

    report(91, "Validating final template footprints")
    post_plan_reverts: set[str] = set()
    for net_key, proof in pair_provenance.items():
        matching_rails = tuple(
            rail
            for rail in base_project.rails
            if rail.net.casefold() == str(net_key).casefold()
            and rail.pwr_layer.casefold() == str(proof.get("pwr_layer", "")).casefold()
            and rail.gnd_layer.casefold() == str(proof.get("gnd_layer", "")).casefold()
        )
        template_ids = {
            _template_for_rail(base_project, rail.rail_id)
            for rail in matching_rails
            if _template_for_rail(base_project, rail.rail_id)
        }
        if len(template_ids) > 1:
            raise SpdImportError(
                f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                "multiple rail-bound ViaLoopTemplates are ambiguous"
            )
        template = next(
            (
                item
                for item in base_project.via_templates
                if item.template_id in template_ids
            ),
            None,
        )
        if template is None:
            # Preserve the import for an unrelated rail whose selected pair
            # cannot be bound to a compiler template, but make the unresolved
            # state explicit so that Evaluation blocks that rail.  Proven
            # rails with a real template continue through the strict gate;
            # this is never a silent template substitution.
            proof["source_graph_pair_unresolved"] = True
            proof["selection_mode"] = "LEGACY_PREEXISTING_PAIR_UNRESOLVED"
            failures = list(proof.get("selected_plane_pair_failures", ()))
            failures.append("selected ViaLoopTemplate is missing for final rail binding")
            proof["selected_plane_pair_failures"] = tuple(dict.fromkeys(failures))
            proof["final_template_footprint"] = {
                "basis": "SELECTED_RAIL_TEMPLATE_BINDING_UNAVAILABLE",
                "validated": False,
                "contract_version": FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION,
            }
            post_plan_reverts.add(str(net_key).casefold())
            continue
        if proof.get("selection_mode") == "LEGACY_PREEXISTING_PAIR_UNRESOLVED":
            proof["final_template_footprint"] = {
                "basis": "SOURCE_GRAPH_PLANE_PAIR_UNRESOLVED",
                "validated": False,
                "contract_version": FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION,
            }
            continue
        width = float(template.finite_port_width_um)
        height = float(template.finite_port_height_um)
        if not isfinite(width) or not isfinite(height) or width <= 0.0 or height <= 0.0:
            raise SpdImportError(
                f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                "selected ViaLoopTemplate footprint is non-finite"
            )
        pwr_artwork_net = str(proof.get("pwr_artwork_net", "")).strip()
        ground_net = str(proof.get("gnd_artwork_net", "")).strip()
        if not pwr_artwork_net or not ground_net:
            raise SpdImportError(
                f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                "candidate artwork net bindings are missing"
            )
        if pwr_artwork_net.casefold() != str(net_key).casefold():
            raise SpdImportError(
                f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                "PWR artwork net binding disagrees with selected rail"
            )
        if ground_net.casefold() not in {item.casefold() for item in base_project.gnd_aliases}:
            raise SpdImportError(
                f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                "GND artwork net binding is not a configured pure-GND alias"
            )
        if (
            proof.get("selection_mode")
            not in {"LEGACY_PREEXISTING_PAIR", "LEGACY_PREEXISTING_PAIR_UNRESOLVED"}
            and str((getattr(analysis, "counts", {}) or {}).get("source_graph_capability", "")).upper()
            != "LEGACY_SOURCE_GRAPH_UNAVAILABLE"
        ):
            for layer, artwork_net, digest_field in (
                (proof.get("pwr_layer"), pwr_artwork_net, "pwr_artwork_sha256"),
                (proof.get("gnd_layer"), ground_net, "gnd_artwork_sha256"),
            ):
                final_shape(str(layer), str(artwork_net))
                actual_digest = final_artwork_digests.get(
                    (str(layer).casefold(), str(artwork_net).casefold()), ""
                )
                if not actual_digest or actual_digest != str(proof.get(digest_field, "")):
                    raise SpdImportError(
                        f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                        f"{digest_field} does not match retained artwork"
                    )
        witnesses = [
            *proof.get("route_witnesses", ()),
            *proof.get("device_route_witnesses", ()),
        ]
        solver_bounds: tuple[float, float, float, float] | None = None
        if len(matching_rails) == 1:
            bound_rail = matching_rails[0]
            bound_partition = next(
                (
                    item
                    for item in base_project.partitions
                    if item.layer.casefold() == bound_rail.pwr_layer.casefold()
                    and bound_rail.domain in item.domain_to_cell
                ),
                None,
            )
            if bound_partition is not None:
                bound_cell_id = bound_partition.domain_to_cell[bound_rail.domain]
                bound_cell = next(
                    (item for item in bound_partition.cells if item.cell_id == bound_cell_id),
                    None,
                )
                if bound_cell is not None:
                    solver_bounds = (
                        float(bound_cell.x_min_um),
                        float(bound_cell.x_max_um),
                        float(bound_cell.y_min_um),
                        float(bound_cell.y_max_um),
                    )
            if solver_bounds is None:
                solver_bounds = (
                    float(base_project.outline.origin_x_um),
                    float(base_project.outline.origin_x_um + base_project.outline.width_um),
                    float(base_project.outline.origin_y_um),
                    float(base_project.outline.origin_y_um + base_project.outline.height_um),
                )
        if solver_bounds is None:
            raise SpdImportError(
                f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                "selected rail has no rectangular solver domain"
            )
        for witness in witnesses:
            layer = str(witness.get("target_layer", ""))
            artwork_net = (
                pwr_artwork_net
                if layer.casefold() == str(proof.get("pwr_layer", "")).casefold()
                else ground_net
            )
            selected = witness.get("selected_target_contact")
            x_um = selected.get("x_um") if isinstance(selected, dict) else witness.get("target_x_um")
            y_um = selected.get("y_um") if isinstance(selected, dict) else witness.get("target_y_um")
            if x_um is None or y_um is None:
                contacts = witness.get("target_contacts")
                if isinstance(contacts, list) and contacts:
                    x_um, y_um = contacts[0].get("x_um"), contacts[0].get("y_um")
            if x_um is None or y_um is None or not all(isfinite(float(item)) for item in (x_um, y_um)):
                raise SpdImportError(
                    f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                    f"target contact for {witness.get('via_id', witness.get('pin_id', '<unknown>'))!r} is unavailable"
                )
            x_value = float(x_um)
            y_value = float(y_um)
            if not _finite_port_inside_solver_bounds(
                x_value, y_value, width, height, solver_bounds
            ):
                post_plan_reverts.add(str(net_key).casefold())
                proof["source_graph_pair_unresolved"] = True
                proof["selection_mode"] = "LEGACY_PREEXISTING_PAIR_UNRESOLVED"
                failures = list(proof.get("selected_plane_pair_failures", ()))
                failures.append(
                    "final template footprint crosses the rectangular solver domain boundary"
                )
                proof["selected_plane_pair_failures"] = tuple(dict.fromkeys(failures))
                proof["final_template_footprint"] = {
                    "basis": "FINAL_TEMPLATE_SOLVER_DOMAIN_UNRESOLVED",
                    "validated": False,
                    "contract_version": FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION,
                }
                break
            graph_contact = (
                str(witness.get("artwork_coverage_basis", "")).upper()
                == "SOURCE_GRAPH_TARGET_NODE_STRICT_INTERIOR"
                or bool(witness.get("connectivity_only"))
            )
            artwork_width = width
            artwork_height = height
            if not graph_contact:
                try:
                    artwork_width = float(witness.get("target_pad_width_um"))
                    artwork_height = float(witness.get("target_pad_height_um"))
                except (TypeError, ValueError):
                    raise SpdImportError(
                        f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                        f"source target-pad footprint for {witness.get('via_id', witness.get('pin_id', '<unknown>'))!r} is unavailable"
                    ) from None
                if not (
                    isfinite(artwork_width)
                    and isfinite(artwork_height)
                    and artwork_width > 0.0
                    and artwork_height > 0.0
                ):
                    raise SpdImportError(
                        f"SPD final plane-pair footprint validation is blocked for {net_key}: "
                        f"source target-pad footprint for {witness.get('via_id', witness.get('pin_id', '<unknown>'))!r} is non-finite"
                    )
            shape = final_shape(layer, artwork_net)
            artwork_ok = (
                shape is not None
                and (
                    shape.contains(x_value, y_value) == "inside"
                    if graph_contact
                    else shape.covers_footprint(
                        x_value, y_value, artwork_width, artwork_height
                    )
                )
            )
            if not artwork_ok:
                post_plan_reverts.add(str(net_key).casefold())
                proof["source_graph_pair_unresolved"] = True
                proof["selection_mode"] = "LEGACY_PREEXISTING_PAIR_UNRESOLVED"
                failures = list(proof.get("selected_plane_pair_failures", ()))
                failures.append(
                    "graph target node is not strict-interior retained artwork"
                    if graph_contact
                    else "source target-pad footprint crosses retained artwork boundary/void"
                )
                proof["selected_plane_pair_failures"] = tuple(dict.fromkeys(failures))
                proof["final_template_footprint"] = {
                    "basis": "FINAL_TEMPLATE_ARTWORK_UNRESOLVED",
                    "validated": False,
                    "contract_version": FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION,
                }
                break
        if str(net_key).casefold() in post_plan_reverts:
            continue
        proof["final_template_footprint"] = {
            "contract_version": FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION,
            "width_um": width,
            "height_um": height,
            "basis": "RECTANGULAR_CAVITY_FINITE_PORT_V1",
            "artwork_graph_contact_basis": "SOURCE_GRAPH_TARGET_NODE_STRICT_INTERIOR",
            "pwr_layer": proof.get("pwr_layer"),
            "gnd_layer": proof.get("gnd_layer"),
            "template_id": template.template_id,
            "pwr_artwork_net": proof.get("pwr_artwork_net"),
            "gnd_artwork_net": proof.get("gnd_artwork_net"),
        }
    report(91, "Validated final template footprints")
    if post_plan_reverts:
        # Rebuild the complete import plan with failed nets restored to their
        # exact preselection pair.  This restores templates, pin bindings,
        # eligibility, topology, partitions, and all import metadata together;
        # Evaluation will block only the persisted unresolved rails.
        preselection_rail_keys = {
            rail.net.casefold() for rail in preselection_project.rails
        }
        restored_selected_pairs = _prepare_post_plan_selection(
            selected_pairs,
            pair_provenance,
            preselection_pairs,
            post_plan_reverts,
            preselection_rail_keys,
        )
        for failed_net in post_plan_reverts:
            if failed_net not in restored_selected_pairs:
                continue
            proof = pair_provenance.get(failed_net)
            if isinstance(proof, dict):
                original_rail = next(
                    (
                        rail
                        for rail in preselection_project.rails
                        if rail.net.casefold() == failed_net
                    ),
                    None,
                )
                if original_rail is None:
                    raise SpdImportError(
                        f"cannot safely restore unresolved rail {failed_net}: "
                        "preselection rail is missing"
                    )
                proof["pwr_layer"] = original_rail.pwr_layer
                proof["gnd_layer"] = original_rail.gnd_layer
                proof["pwr_artwork_net"] = original_rail.net
                proof["selection_mode"] = "LEGACY_PREEXISTING_PAIR_UNRESOLVED"
                proof["source_graph_pair_unresolved"] = True
        report(91, "Rebuilding restored SPD import plan")
        restored_plan = build_spd_import_plan(
            state.project,
            analysis,
            source_path,
            selected_pairs=restored_selected_pairs,
            selected_pair_provenance=pair_provenance,
            _geometry_from_plan=geometry_donor_plan,
        )
        if not restored_plan.can_apply:
            raise SpdImportError(
                "cannot safely rebuild import after rail-scoped final validation failure"
            )
        plan = restored_plan
        base_project = _normalized_base_project(restored_plan.project)
        report(91, "Rebuilt restored SPD import plan")
        # Preserve the unresolved diagnostics after the complete rebuild.
        rebuilt_provenance = dict(
            base_project.metadata.get("spd_import", {}).get(
                "selected_plane_pair_provenance", {}
            )
            if isinstance(base_project.metadata.get("spd_import"), dict)
            else {}
        )
        for key, proof in pair_provenance.items():
            if key in post_plan_reverts:
                rebuilt_provenance[key] = proof
        metadata = dict(base_project.metadata)
        spd_import = dict(metadata.get("spd_import", {}))
        spd_import["selected_plane_pair_provenance"] = rebuilt_provenance
        metadata["spd_import"] = spd_import
        base_project = base_project.model_copy(update={"metadata": metadata})
    final_metadata = dict(base_project.metadata)
    final_spd_import = dict(final_metadata.get("spd_import", {}))
    display_net_by_key = {
        str(rail.net).casefold(): str(rail.net) for rail in base_project.rails
    }
    final_spd_import["selected_plane_pair_provenance"] = {
        display_net_by_key.get(str(key).casefold(), str(key)): value
        for key, value in pair_provenance.items()
    }
    final_spd_import["final_template_footprint_validation"] = {
        "version": FINAL_TEMPLATE_ARTWORK_CONTRACT_VERSION,
        "predicate": "ordered_boolean_contains_source_target_pad_or_graph_target_node",
        "graph_contact_basis": "SOURCE_GRAPH_TARGET_NODE_STRICT_INTERIOR",
        "solver_port_basis": "RECTANGULAR_CAVITY_FINITE_PORT_V1",
        "boundary_and_voids_rejected": True,
        "graph_contact_finite_artwork_rectangle_not_claimed": True,
        "source_sha256": analysis.source.sha256,
    }
    final_metadata["spd_import"] = final_spd_import
    base_project = base_project.model_copy(update={"metadata": final_metadata})
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
        mixed_reference_certificates=tuple(
            rail.mixed_reference_certificate
            for rail in base_project.rails
            if rail.mixed_reference_certificate is not None
        ),
        selected_pairs=tuple(
            PlanePairSuggestion(
                rail_net=rail.net,
                pwr_layer=rail.pwr_layer,
                gnd_layer=rail.gnd_layer,
                pwr_index=0,
                gnd_index=0,
                separation_um=0.0,
                mixed_reference_certificate=rail.mixed_reference_certificate,
            )
            for rail in base_project.rails
        ),
    )
    source_rail_id_by_key = {
        key: (
            rail.rail_id
            if (rail := _rail_for_instance(base_project, instance)) is not None
            else f"UNAVAILABLE::{instance.power_net}"
        )
        for key, instance in top_instance_by_key.items()
    }
    _raise_for_rejected_mixed_reference_landings(
        base_project,
        source_landings,
        path_recovery,
    )
    # A mixed-reference artwork certificate establishes plane overlap, not the
    # source GND topology.  Build one stable witness universe per mixed rail:
    # every DIRECT source landing whose PWR evidence is eligible for that rail,
    # independent of the mutable current_rail_id.  This lets distribution
    # reassign a proven-compatible decap into/out of the rail without making a
    # source witness stale.  The evaluation preflight separately requires the
    # enabled *current* assignment to be a witnessed subset.  This batched pass
    # accepts branches (connectivity proof) and intentionally does not turn
    # them into a unique RL path; the terminal model remains legacy-template.
    mixed_rails = {
        rail.rail_id.casefold(): rail
        for rail in base_project.rails
        if rail.mixed_reference_certificate is not None
    }
    report(91, "Selecting mixed-reference GND witness landings")
    mixed_witness_selection_started = perf_counter()
    (
        mixed_ground_landings_by_rail,
        mixed_target_layers_by_net,
    ) = _select_mixed_reference_ground_landings(
        mixed_rails=mixed_rails,
        top_instances=top_instances,
        parsed_connection_by_key=parsed_connection_by_key,
        parsed_cluster_by_key=parsed_cluster_by_key,
        path_recovery=path_recovery,
        eligibility_index=eligibility_index,
        rail_choices_by_pair=rail_choices_by_pair,
    )
    mixed_witness_selection_s = perf_counter() - mixed_witness_selection_started
    # ``connectivity_recovery`` already covered every mixed-reference landing
    # and its certified DGND target layer in the shared graph pass above.
    # Reusing it avoids a second full-file hash plus Node/Trace/Via scan.
    ground_recovery_started = perf_counter()
    ground_reachability = connectivity_recovery
    report(91, "Reused source graph for mixed-reference GND witnesses")
    ground_recovery_s = perf_counter() - ground_recovery_started
    mixed_witnesses: dict[str, MixedReferenceGroundWitness] = {}
    mixed_ground_reachability_by_rail: list[dict[str, Any]] = []
    mixed_ground_reachability_diagnostics: list[SpdDiagnostic] = []
    for rail_key, rail in mixed_rails.items():
        certificate = rail.mixed_reference_certificate
        assert certificate is not None
        identities: list[str] = []
        unreachable: list[str] = []
        for refdes, landing in mixed_ground_landings_by_rail[rail_key]:
            identity = mixed_reference_ground_landing_identity(refdes, landing)
            if ground_reachability.reaches(landing, certificate.gnd_layer):
                identities.append(identity)
            else:
                unreachable.append(identity)
        canonical = tuple(sorted(set(identities)))
        landing_hash = sha256(
            (json.dumps(list(canonical), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        ).hexdigest()
        mixed_witnesses[rail_key] = MixedReferenceGroundWitness(
            rail_net=rail.net,
            gnd_net=certificate.gnd_net,
            pwr_layer=rail.pwr_layer,
            gnd_layer=rail.gnd_layer,
            gnd_asset_sha256=certificate.gnd_asset_sha256,
            source_sha256=analysis.source.sha256,
            landing_identities=canonical,
            landing_count=len(canonical),
            landing_identities_sha256=landing_hash,
        )
        unreachable_examples = tuple(sorted(set(unreachable))[:5])
        mixed_ground_reachability_by_rail.append(
            {
                "rail_id": rail.rail_id,
                "candidate_landing_count": len(
                    {
                        mixed_reference_ground_landing_identity(owner, landing)
                        for owner, landing in mixed_ground_landings_by_rail[rail_key]
                    }
                ),
                "reachable_landing_count": len(canonical),
                "unreachable_landing_count": len(set(unreachable)),
                "unreachable_examples": list(unreachable_examples),
            }
        )
        if unreachable_examples:
            mixed_ground_reachability_diagnostics.append(
                SpdDiagnostic(
                    severity="warning",
                    code="SPD_MIXED_REFERENCE_GND_REACHABILITY_INCOMPLETE",
                    message=(
                        f"{rail.rail_id}: {len(set(unreachable))} source GND "
                        f"landing(s) do not reach certified {certificate.gnd_net} "
                        f"artwork on {certificate.gnd_layer}; this rail is blocked "
                        "only if selected for evaluation (examples: "
                        + ", ".join(unreachable_examples)
                        + ")"
                    ),
                )
            )
    mixed_requested_keys = {
        (
            str(getattr(landing, "via_id", "")).casefold(),
            str(getattr(landing, "endpoint_node_id", "")).casefold(),
            str(layer).casefold(),
        )
        for entries in mixed_ground_landings_by_rail.values()
        for _refdes, landing in entries
        for layer in mixed_target_layers_by_net.get(
            str(getattr(landing, "net", "")).casefold(), ()
        )
    }
    mixed_reachable_keys = (
        set(getattr(ground_reachability, "reachable_keys", ()))
        & mixed_requested_keys
    )
    mixed_unreachable_keys = mixed_requested_keys - mixed_reachable_keys
    mixed_reachability_statistics = dict(
        getattr(ground_reachability, "statistics", {}) or {}
    )
    mixed_reachability_statistics.update(
        requested=len(mixed_requested_keys),
        reachable=len(mixed_reachable_keys),
        unreachable=len(mixed_unreachable_keys),
        shared_source_graph_reused=True,
    )
    if not mixed_requested_keys:
        # No mixed witness scan was requested; do not expose the shared full
        # source-graph counters as if they described an empty mixed pass.
        for key in (
            "node_section_passes",
            "trace_section_passes",
            "via_section_passes",
            "components",
            "graph_nodes",
            "graph_edges",
            "trace_edges",
            "via_edges",
        ):
            mixed_reachability_statistics[key] = 0
    recovery_metadata = dict(base_project.metadata)
    recovery_metadata[MLO_LANDING_CERTIFICATE_METADATA_KEY] = (
        _build_mlo_landing_certificates(
            source_landings,
            padstacks=analysis.padstacks,
            stackup_layers=base_project.stackup_layers,
            source_sha256=analysis.source.sha256,
        )
    )
    # This source-bound policy is a board-level positive MLO summary.  Path
    # recovery can legitimately fall back for individual landings, so a false
    # result is never a per-landing conventional-via certificate; Distribution
    # requires each non-TOP candidate to retain explicit path evidence.
    mlo_transition_policy = detect_mlo_transition_policy(
        source_landings,
        stackup_layers=base_project.stackup_layers,
        evidence_by_via=path_recovery.evidence_by_via,
        structural_evidence_by_via=path_recovery.structural_evidence_by_via,
    )

    recovery_metadata["spd_mlo_transition_policy"] = {
        **mlo_transition_policy.payload(),
        "policy_version": MLO_TRANSITION_POLICY_VERSION,
        "source_sha256": analysis.source.sha256,
    }
    recovery_metadata["spd_via_path_recovery"] = {
        **dict(path_recovery.statistics),
        "algorithm": (
            "graph_connectivity_contact_recovery_v1"
            if graph_available
            else "unique_monotonic_same_net_via_chain_v1"
        ),
        "skipped_unique_path_recovery": bool(graph_available),
        "fallback_behavior": "legacy_rail_template",
        "mixed_reference_ground_reachability": {
            **mixed_reachability_statistics,
            "algorithm": str(
                mixed_reachability_statistics.get(
                    "artwork_algorithm",
                    "same_net_via_trace_artwork_reachability_v3_layerwise_deferred",
                )
            ),
            "indexed_geometry_count": len(connectivity_index),
            "exact_geometry_build_count": sum(
                int(indexed.artwork_shape_built)
                for indexed in connectivity_index.values()
            ),
            "by_rail": mixed_ground_reachability_by_rail,
        },
        "source_graph_artwork_reachability": {
            "version": str(
                connectivity_statistics.get(
                    "artwork_algorithm",
                    "same_net_via_trace_artwork_reachability_v3_layerwise_deferred",
                )
            ),
            "indexed_geometry_count": len(connectivity_index),
            "exact_geometry_build_count": sum(
                int(indexed.artwork_shape_built)
                for indexed in connectivity_index.values()
            ),
            "artwork_nodes": int(connectivity_statistics.get("artwork_nodes", 0)),
            "artwork_edges": int(connectivity_statistics.get("artwork_edges", 0)),
            "artwork_components": int(
                connectivity_statistics.get("artwork_components", 0)
            ),
            "artwork_trace_contacts": int(
                connectivity_statistics.get("artwork_trace_contacts", 0)
            ),
            "trace_artwork_contact_count": int(
                connectivity_statistics.get("trace_artwork_contact_count", 0)
            ),
            "trace_artwork_conditional_passes": int(
                connectivity_statistics.get("trace_artwork_conditional_passes", 0)
            ),
            "trace_artwork_conditional_checks": int(
                connectivity_statistics.get("trace_artwork_conditional_checks", 0)
            ),
            "trace_artwork_conditional_successes": int(
                connectivity_statistics.get("trace_artwork_conditional_successes", 0)
            ),
        },
    }
    base_project = base_project.model_copy(
        update={
            "metadata": recovery_metadata,
            "rails": [
                rail.model_copy(
                    update={
                        "mixed_reference_ground_witness": mixed_witnesses.get(
                            rail.rail_id.casefold()
                        )
                    }
                )
                for rail in base_project.rails
            ],
        }
    )
    report(
        92,
        "Recovered source-proven Via path summaries; checking exact PWR-plane eligibility",
    )
    landing_cache: dict[str, ScenarioViaLanding] = {}
    contact_lookup: dict[
        tuple[str, str], list[tuple[str, tuple[tuple[str, float, float], ...]]]
    ] = {}
    for key, contacts in getattr(connectivity_recovery, "target_contacts_by_key", {}).items():
        if len(key) != 3:
            continue
        contact_lookup.setdefault((str(key[0]), str(key[1])), []).append(
            (str(key[2]), tuple(contacts))
        )
    frozen_contact_lookup = {
        key: tuple(sorted(value, key=lambda item: item[0]))
        for key, value in contact_lookup.items()
    }

    def scenario_landing(landing: Any) -> ScenarioViaLanding:
        key = landing.via_id.casefold()
        result = landing_cache.get(key)
        if result is None:
            result = _scenario_via_landing(
                landing,
                path_recovery,
                connectivity_recovery,
                analysis.source.sha256,
                frozen_contact_lookup,
            )
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
                    # Exact landing eligibility is an assignment/distribution
                    # constraint.  It must never rewrite parser-proven source
                    # connectivity: the unedited source rail can be modeled
                    # from its source terminals even when an alternate-plane
                    # eligibility query cannot prove that rail at every Via.
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
    routing_asset_ref: RoutingObstacleAssetRef | None = None
    routing_asset_diagnostics: list[SpdDiagnostic] = []
    if analysis.routing_extraction is not None:
        try:
            conductor_layers = tuple(
                item.name for item in base_project.stackup_layers if item.is_conductor
            )
            routing_asset = RoutingObstacleAsset(
                source_sha256=analysis.source.sha256,
                stackup_fingerprint=stackup_fingerprint(base_project.stackup_layers),
                conductor_layers=conductor_layers,
                segments=analysis.routing_extraction.segments,
                layer_completeness=analysis.routing_extraction.layer_completeness,
                via_profiles=_routing_via_profiles(base_project, analysis.padstacks),
                compiler_policy=analysis.routing_extraction.compiler_policy,
                production_ready=analysis.routing_extraction.production_ready,
            )
            routing_payload = encode_routing_obstacle_asset(routing_asset)
            routing_name = routing_attachment_name(routing_payload)
            decoded_routing_asset = decode_routing_obstacle_asset(
                routing_payload,
                expected_source_sha256=analysis.source.sha256,
                expected_stackup_fingerprint=routing_asset.stackup_fingerprint,
            )
            attachments[routing_name] = routing_payload
            routing_asset_ref = RoutingObstacleAssetRef(
                attachment_name=routing_name,
                attachment_sha256=sha256(routing_payload).hexdigest(),
                content_sha256=str(decoded_routing_asset.content_sha256),
                schema_version=decoded_routing_asset.schema_version,
                source_sha256=decoded_routing_asset.source_sha256,
                stackup_fingerprint=decoded_routing_asset.stackup_fingerprint,
                scope=decoded_routing_asset.scope.value,
                compiler_policy=decoded_routing_asset.compiler_policy,
                production_ready=decoded_routing_asset.production_ready,
                scope_limitation=decoded_routing_asset.scope_limitation,
                via_profile_ids=tuple(
                    item.profile_id for item in decoded_routing_asset.via_profiles
                ),
            )
        except (ValueError, OverflowError) as exc:
            routing_asset_diagnostics.append(
                SpdDiagnostic(
                    "warning",
                    "SPD_SIGNAL_ROUTING_RESEARCH_ASSET_UNAVAILABLE",
                    f"Optional signal-routing attachment was not created: {exc}",
                )
            )
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
            isolation_gap_refdes=(
                cluster.isolation_gap_refdes
                if cluster_state_by_key[cluster_key]
                == SharedPadClusterState.ANCHORED
                else ()
            ),
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
        routing_obstacle_asset=routing_asset_ref,
    )
    finalize_s = perf_counter() - finalize_started
    total_s = perf_counter() - total_started
    timings = ImportStageTimings(
        analyze_s=analyze_s,
        plan_s=plan_s,
        index_s=index_s,
        recovery_s=recovery_s,
        mixed_witness_selection_s=mixed_witness_selection_s,
        ground_recovery_s=ground_recovery_s,
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
        diagnostics=tuple(
            (
                *plan.diagnostics,
                *path_recovery.diagnostics,
                *mixed_ground_reachability_diagnostics,
                *routing_asset_diagnostics,
            )
        ),
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
