"""Read-only PowerSI SPD import for editable decap PI scenarios."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import json
from math import hypot, isfinite
from pathlib import Path
from time import perf_counter
from typing import Any

from spd_decap_pi._core.domain import (
    MixedReferenceGroundWitness,
    PinKind,
    PlanePairSuggestion,
    ProjectSpec,
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

from .eligibility import EligibilityResult, PlaneEligibilityIndex
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
    return ScenarioViaLanding(
        via_id=landing.via_id,
        net=landing.net,
        endpoint_node_id=landing.endpoint_node_id,
        x_um=landing.x_um,
        y_um=landing.y_um,
        padstack=landing.padstack,
        rotation_degrees=landing.rotation_degrees,
        path_evidence=evidence,
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


def _mixed_reference_target_node_predicate(
    project: ProjectSpec, attachments: dict[str, bytes]
) -> Callable[[str, str, str, float, float], bool]:
    """Build fail-closed strict-interior tests for certificate DGND artwork."""

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
            shape = core_services._ordered_spd_geometry(payload)
        except (ValueError, ArithmeticError) as exc:
            raise SpdImportError(
                "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: certificate DGND "
                f"geometry is invalid for {rail.rail_id}: {exc}"
            ) from exc
        if shape is None:
            raise SpdImportError(
                "SPD_MIXED_REFERENCE_GND_ARTWORK_REQUIRED: certificate DGND "
                f"geometry cannot be constructed for {rail.rail_id}"
            )
        shapes[key] = (prep(shape), tuple(map(float, shape.bounds)))

    def accepts(net: str, layer: str, _node_id: str, x_um: float, y_um: float) -> bool:
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
    recovery_started = perf_counter()
    def recovery_progress(value: int, message: str) -> None:
        report(87 + round(max(0, min(100, value)) * 4 / 100), message)

    path_recovery = recover_spd_via_paths(
        source_path,
        landings=source_landings,
        target_layers_by_net=_via_target_layers_by_net(
            base_project,
            plane_geometries=analysis.plane_geometries,
        ),
        stackup_layers=base_project.stackup_layers,
        padstacks=analysis.padstacks,
        top_layer=top_layer,
        expected_source=analysis.source,
        progress=recovery_progress,
        is_cancelled=cancelled,
    )
    path_recovery_s = perf_counter() - recovery_started
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
    ground_recovery_started = perf_counter()
    ground_reachability = recover_spd_ground_reachability(
        source_path,
        landings=(
            landing
            for entries in mixed_ground_landings_by_rail.values()
            for _refdes, landing in entries
        ),
        target_layers_by_net=mixed_target_layers_by_net,
        target_node_predicate=_mixed_reference_target_node_predicate(
            base_project, dict(plan.attachments)
        ),
        expected_source=analysis.source,
        progress=lambda value, message: report(91 + round(max(0, min(100, value)) * 1 / 100), message),
        is_cancelled=cancelled,
    )
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
        "algorithm": "unique_monotonic_same_net_via_chain_v1",
        "fallback_behavior": "legacy_rail_template",
        "mixed_reference_ground_reachability": {
            **dict(ground_reachability.statistics),
            "algorithm": "same_net_via_trace_reachability_v1",
            "by_rail": mixed_ground_reachability_by_rail,
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
        routing_obstacle_asset=routing_asset_ref,
    )
    finalize_s = perf_counter() - finalize_started
    total_s = perf_counter() - total_started
    timings = ImportStageTimings(
        analyze_s=analyze_s,
        plan_s=plan_s,
        index_s=index_s,
        recovery_s=path_recovery_s,
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
