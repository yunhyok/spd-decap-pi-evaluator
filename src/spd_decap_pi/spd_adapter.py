"""Read-only PowerSI SPD import for editable decap PI scenarios."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from math import hypot, isclose, isfinite
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
from spd_decap_pi._core.plane_pairs import suggest_effective_plane_pairs
from spd_decap_pi._core.solver.evaluator import (
    compile_project_evaluation_template,
)

from .compiled_topology_asset import COMPILED_TOPOLOGY_ASSET_METADATA_KEY
from .eligibility import EligibilityResult, IndexedPlaneGeometry, PlaneEligibilityIndex
from .raw_spatial_contact_asset import (
    RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY,
    validate_project_raw_spatial_contact_asset_envelope,
)
from .raw_spatial_contact_compiler import compile_raw_spatial_contact_asset
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
from .surface_certificate_asset import (
    SurfaceCertificateAssetError,
    canonical_surface_certificate_sha256,
    externalize_project_surface_certificate,
)


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]

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

_LAYER_SURFACE_CONNECTIVITY_SCHEMA = "spd-layer-surface-connectivity-v4"
_LAYER_SURFACE_CONNECTIVITY_COMPILER = (
    "powersi-same-layer-trace-artwork-finite-via-quotient-v4"
)


def _has_compiled_topology_manifest(project: ProjectSpec) -> bool:
    """Return whether the finalized project carries a real compiled manifest."""

    metadata = project.metadata
    spd_import = metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    if not isinstance(spd_import, Mapping):
        return False
    if COMPILED_TOPOLOGY_ASSET_METADATA_KEY not in spd_import:
        return False
    if not isinstance(spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY], Mapping):
        raise SpdImportError(
            "RAW_SPATIAL_COMPILED_TOPOLOGY_INVALID: finalized compiled topology "
            "metadata is not a manifest"
        )
    return True


def _merge_raw_spatial_contact_asset(
    project: ProjectSpec,
    attachments: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    generated: tuple[str, bytes],
) -> tuple[ProjectSpec, dict[str, bytes]]:
    """Bind one generated raw asset without overwriting any sibling state."""

    if not isinstance(manifest, Mapping):
        raise SpdImportError(
            "RAW_SPATIAL_MANIFEST_INVALID: compiler did not return a manifest"
        )
    try:
        asset_name, asset_payload = generated
    except (TypeError, ValueError) as exc:
        raise SpdImportError(
            "RAW_SPATIAL_ATTACHMENT_INVALID: compiler did not return one attachment"
        ) from exc
    if (
        type(asset_name) is not str
        or not asset_name
        or type(asset_payload) is not bytes
        or manifest.get("asset_name") != asset_name
    ):
        raise SpdImportError(
            "RAW_SPATIAL_ATTACHMENT_INVALID: generated member differs from its manifest"
        )

    updated_attachments = dict(attachments)
    matches = [
        name
        for name in updated_attachments
        if type(name) is str and name.casefold() == asset_name.casefold()
    ]
    if matches and (
        len(matches) != 1
        or matches[0] != asset_name
        or updated_attachments[matches[0]] != asset_payload
    ):
        raise SpdImportError(
            "RAW_SPATIAL_ASSET_COLLISION: scenario already contains a different "
            f"attachment named {asset_name!r}"
        )
    if not matches:
        updated_attachments[asset_name] = asset_payload

    metadata = dict(project.metadata)
    source_spd_import = metadata.get("spd_import", {})
    if not isinstance(source_spd_import, Mapping):
        raise SpdImportError(
            "RAW_SPATIAL_PROJECT_INVALID: finalized SPD import metadata is not a mapping"
        )
    spd_import = dict(source_spd_import)
    matching_metadata_keys = [
        key
        for key in spd_import
        if type(key) is str
        and key.casefold() == RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY.casefold()
    ]
    if matching_metadata_keys and matching_metadata_keys != [
        RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
    ]:
        raise SpdImportError(
            "RAW_SPATIAL_METADATA_COLLISION: finalized project contains a "
            "non-canonical raw spatial metadata key"
        )
    existing_manifest = spd_import.get(RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY)
    normalized_manifest = dict(manifest)
    if existing_manifest is not None and existing_manifest != normalized_manifest:
        raise SpdImportError(
            "RAW_SPATIAL_METADATA_COLLISION: finalized project already contains "
            "different raw spatial metadata"
        )
    spd_import[RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY] = normalized_manifest
    metadata["spd_import"] = spd_import
    return project.model_copy(update={"metadata": metadata}), updated_attachments


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
class _SourceGraphLanding:
    """Minimal landing identity accepted by raw SPD graph recovery."""

    via_id: str
    net: str
    endpoint_node_id: str
    x_um: float
    y_um: float
    padstack: str
    pin_id: str
    contact_path_kind: str


@dataclass(frozen=True, slots=True)
class _RetargetLandingDestinationRequest:
    """Exact Distribution destination found under one physical PWR Via XY.

    Distribution may rebuild a PWR Via column to a retained plane that has no
    source-path Node evidence.  This compact row preserves the independent
    ordered-artwork query needed to bind that destination to the global finite
    Via quotient without serializing the raw SPD Node inventory.
    """

    refdes: str
    via_id: str
    endpoint_node_id: str
    source_net: str
    x_um: float
    y_um: float
    destination_net: str
    destination_layer: str
    destination_island_id: str
    geometry_asset_sha256: str
    target_rail_id: str
    via_template_id: str | None


@dataclass(frozen=True, slots=True)
class _RetainedSurfaceArtwork:
    """Bound exact surface inventory with a one-live-shape query lifecycle."""

    node_predicate: Callable[[str, str, str, float, float], bool]
    surface_resolver: Callable[[str, str, str, float, float], str | None]
    surface_resolver_batch: Callable[
        [str, str, Sequence[str], Sequence[tuple[float, float]]],
        Sequence[str | None],
    ]
    strict_surface_resolver: Callable[
        [str, str, str, float, float], str | None
    ]
    strict_surface_resolver_batch: Callable[
        [str, str, Sequence[str], Sequence[tuple[float, float]]],
        Sequence[str | None],
    ]
    artwork_component: Callable[[str, str, float, float], object | None]
    artwork_components_batch: Callable[
        [str, str, Sequence[tuple[float, float]]], Sequence[object | None]
    ]
    release: Callable[[str, str], None]
    geometry_assets: list[dict[str, Any]]
    target_layers_by_net: dict[str, set[str]]
    island_ids_by_surface: dict[tuple[str, str], tuple[str, ...]]


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
                destination_pwr_layer=plane.pwr_layer,
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
    mixed_reference_certificates: Iterable[Any] = (),
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
    for certificate in mixed_reference_certificates:
        rail_net = str(getattr(certificate, "rail_net", "")).strip()
        gnd_net = str(getattr(certificate, "gnd_net", "")).strip()
        pwr_layer = str(getattr(certificate, "pwr_layer", "")).strip()
        gnd_layer = str(getattr(certificate, "gnd_layer", "")).strip()
        if rail_net and pwr_layer:
            result.setdefault(rail_net.casefold(), set()).add(pwr_layer)
        if gnd_net and gnd_layer:
            result.setdefault(gnd_net.casefold(), set()).add(gnd_layer)
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
    mixed_reference_certificates: Iterable[Any] = (),
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

    certified_pairs = tuple(mixed_reference_certificates)
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
            mixed_reference_certificates=certified_pairs,
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



def _ephemeral_anchor_compile_project(
    project: ProjectSpec,
    *,
    source_sha256: str,
) -> ProjectSpec:
    """Break the mixed-witness/anchor extraction cycle without persistence.

    The evaluation compiler checks that mixed-reference artwork has a witness
    before it constructs Device branches.  Raw-graph recovery needs those
    branch anchors first.  This in-memory copy supplies only the certificate's
    exact GND asset identity; it is never persisted and makes no connectivity
    claim (zero landings).  The real project receives only the later recovery
    result.
    """

    empty_landing_hash = sha256(b"[]\n").hexdigest()
    changed = False
    rails = []
    for rail in project.rails:
        certificate = rail.mixed_reference_certificate
        if certificate is None or rail.mixed_reference_ground_witness is not None:
            rails.append(rail)
            continue
        changed = True
        rails.append(
            rail.model_copy(
                update={
                    "mixed_reference_ground_witness": (
                        MixedReferenceGroundWitness(
                            rail_net=rail.net,
                            gnd_net=certificate.gnd_net,
                            pwr_layer=rail.pwr_layer,
                            gnd_layer=rail.gnd_layer,
                            gnd_asset_sha256=certificate.gnd_asset_sha256,
                            source_sha256=source_sha256,
                            landing_identities=(),
                            landing_count=0,
                            landing_identities_sha256=empty_landing_hash,
                        )
                    )
                }
            )
        )
    return project.model_copy(update={"rails": rails}) if changed else project


def _compile_active_rail_anchor_bindings(
    project: ProjectSpec,
    *,
    source_sha256: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Compile every ACTIVE rail and flatten exact Device branch anchors."""

    compile_project = _ephemeral_anchor_compile_project(
        project, source_sha256=source_sha256
    )
    bindings: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    active_rails = sorted(
        (
            rail
            for rail in project.rails
            if str(getattr(rail.state, "value", rail.state)) == "ACTIVE"
        ),
        key=lambda rail: (rail.rail_id.casefold(), rail.rail_id),
    )
    for rail in active_rails:
        try:
            template = compile_project_evaluation_template(
                compile_project, rail.rail_id
            )
        except Exception as exc:
            failures.append(
                {
                    "rail_id": rail.rail_id,
                    "code": type(exc).__name__,
                    "message": str(exc),
                }
            )
            continue
        branches = tuple(getattr(template.device, "branches", ()) or ())
        if not branches:
            failures.append(
                {
                    "rail_id": rail.rail_id,
                    "code": "DEVICE_BRANCHES_MISSING",
                    "message": "compiled evaluation template has no Device branch",
                }
            )
            continue
        for branch in sorted(
            branches,
            key=lambda item: (
                str(getattr(item, "branch_id", "")).casefold(),
                str(getattr(item, "branch_id", "")),
            ),
        ):
            branch_id = str(getattr(branch, "branch_id", "")).strip()
            for role, attribute in (
                ("power", "source_power_pin_id"),
                ("ground", "source_ground_pin_id"),
            ):
                pin_id = str(getattr(branch, attribute, "") or "").strip()
                if not branch_id or not pin_id:
                    failures.append(
                        {
                            "rail_id": rail.rail_id,
                            "code": "ANCHOR_BINDING_MISSING",
                            "message": (
                                f"compiled branch {branch_id or '<blank>'!r} "
                                f"has no {attribute}"
                            ),
                        }
                    )
                    continue
                bindings.append(
                    {
                        "rail_id": rail.rail_id,
                        "branch_id": branch_id,
                        "role": role,
                        "pin_id": pin_id,
                    }
                )
    bindings = sorted(
        {
            (
                item["rail_id"].casefold(),
                item["branch_id"].casefold(),
                item["role"],
                item["pin_id"].casefold(),
            ): item
            for item in bindings
        }.values(),
        key=lambda item: (
            item["rail_id"].casefold(),
            item["branch_id"].casefold(),
            item["role"],
            item["pin_id"].casefold(),
        ),
    )
    failures.sort(
        key=lambda item: (
            item["rail_id"].casefold(),
            item["code"].casefold(),
            item["message"],
        )
    )
    return bindings, failures


def _anchor_graph_landings(
    bindings: Iterable[Mapping[str, str]],
    device_terminal_endpoints: Iterable[Any],
) -> tuple[
    dict[str, _SourceGraphLanding],
    dict[str, dict[str, Any]],
]:
    """Map only compiled anchor pin IDs into raw source-graph starts."""

    endpoint_by_pin: dict[str, list[Any]] = {}
    for endpoint in device_terminal_endpoints:
        pin_id = str(getattr(endpoint, "pin_id", "")).strip()
        if pin_id:
            endpoint_by_pin.setdefault(pin_id.casefold(), []).append(endpoint)
    required_pin_ids = sorted(
        {
            str(item["pin_id"]).strip()
            for item in bindings
            if str(item.get("pin_id", "")).strip()
        },
        key=lambda value: (value.casefold(), value),
    )
    landings: dict[str, _SourceGraphLanding] = {}
    contacts: dict[str, dict[str, Any]] = {}
    for pin_id in required_pin_ids:
        pin_key = pin_id.casefold()
        candidates = endpoint_by_pin.get(pin_key, ())
        issues: list[str] = []
        endpoint = candidates[0] if len(candidates) == 1 else None
        if not candidates:
            issues.append("terminal_endpoint_missing")
        elif len(candidates) != 1:
            issues.append("terminal_endpoint_ambiguous")
        net = str(getattr(endpoint, "net", "") or "").strip()
        source_node_id = str(
            getattr(endpoint, "source_node_id", "") or ""
        ).strip()
        source_layer = str(
            getattr(endpoint, "source_layer", "") or ""
        ).strip()
        first_via_status = str(
            getattr(endpoint, "status", "") or ""
        ).strip()
        if first_via_status and first_via_status != "complete":
            issues.append(f"first_via_status:{first_via_status}")
        incident_via_id = str(
            getattr(endpoint, "incident_via_id", "") or ""
        ).strip()
        incident_net = str(
            getattr(endpoint, "incident_net", "") or net
        ).strip()
        incident_opposite_node_id = str(
            getattr(endpoint, "incident_opposite_node_id", "") or ""
        ).strip()
        incident_padstack = str(
            getattr(endpoint, "incident_padstack", "") or ""
        ).strip()
        if incident_net and net and incident_net.casefold() != net.casefold():
            issues.append("incident_net_mismatch")
        x_um = getattr(endpoint, "source_x_um", None)
        y_um = getattr(endpoint, "source_y_um", None)
        source_identity_valid = bool(
            endpoint is not None
            and net
            and source_node_id
            and source_layer
            and isinstance(x_um, (int, float))
            and isinstance(y_um, (int, float))
            and isfinite(float(x_um))
            and isfinite(float(y_um))
        )
        if not source_identity_valid:
            issues.append("source_node_identity_incomplete")
        if source_identity_valid:
            padstack = str(
                incident_padstack
                or getattr(endpoint, "source_padstack", "")
                or ""
            ).strip()
            landing = _SourceGraphLanding(
                via_id=(
                    incident_via_id
                    if incident_via_id
                    else f"source-node:{source_node_id}"
                ),
                net=net,
                endpoint_node_id=source_node_id,
                x_um=float(x_um),
                y_um=float(y_um),
                padstack=padstack,
                pin_id=pin_id,
                contact_path_kind=(
                    "direct_via_landing"
                    if incident_via_id
                    else "trace_component"
                ),
            )
            landings[pin_key] = landing
        contacts[pin_key] = {
            "pin_id": pin_id,
            "net": net,
            "source_node_id": source_node_id or None,
            "incident_via_id": incident_via_id or None,
            "incident_net": incident_net or None,
            "incident_opposite_node_id": incident_opposite_node_id or None,
            "incident_padstack": incident_padstack or None,
            "source_layer": source_layer or None,
            "contact_path_kind": (
                "direct_via_landing"
                if incident_via_id
                else "trace_component"
                if source_identity_valid
                else "unresolved"
            ),
            "contact_layers": [],
            "status": "pending" if source_identity_valid else "incomplete",
            "issues": sorted(set(issues)),
        }
    return landings, contacts


def _retained_surface_artwork(
    project: ProjectSpec,
    attachments: Mapping[str, bytes],
    plane_geometries: Iterable[Any],
    *,
    indexed_geometry_by_key: Mapping[
        tuple[str, str], IndexedPlaneGeometry
    ] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> _RetainedSurfaceArtwork:
    """Bind retained assets to exact islands without retaining Shapely shapes."""

    raw_report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    last_progress = -1

    def check_cancelled() -> None:
        if cancelled():
            raise RuntimeError("SPD retained-surface artwork compilation cancelled")

    def emit(value: int, message: str) -> None:
        nonlocal last_progress
        bounded = max(last_progress, min(100, max(0, int(value))))
        if bounded == last_progress:
            return
        last_progress = bounded
        raw_report(bounded, message)

    spd_import = project.metadata.get("spd_import")
    records = (
        spd_import.get("plane_geometries")
        if isinstance(spd_import, dict)
        else None
    )
    if not isinstance(records, list) or not records:
        raise SpdImportError(
            "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: retained geometry index is missing"
        )

    geometry_groups: dict[tuple[str, str], list[Any]] = {}
    for geometry in plane_geometries:
        net = str(getattr(geometry, "net", "")).strip()
        layer = str(getattr(geometry, "layer", "")).strip()
        if net and layer:
            geometry_groups.setdefault(
                (net.casefold(), layer.casefold()), []
            ).append(geometry)

    indexes: dict[tuple[str, str], IndexedPlaneGeometry] = {}
    component_island_ids: dict[tuple[str, str], tuple[str, ...]] = {}
    component_wkb_by_key: dict[tuple[str, str], tuple[bytes, ...]] = {}
    identities: list[dict[str, Any]] = []
    target_layers_by_net: dict[str, set[str]] = {}
    target_surface_island_ids: dict[tuple[str, str], tuple[str, ...]] = {}
    active_key: tuple[str, str] | None = None
    active_artwork: tuple[
        tuple[object, ...], object, tuple[object, ...]
    ] | None = None

    def activate(
        net: str, layer: str
    ) -> tuple[
        tuple[str, str],
        IndexedPlaneGeometry | None,
        tuple[tuple[object, ...], object, tuple[object, ...]] | None,
    ]:
        nonlocal active_artwork, active_key
        key = (str(net).casefold(), str(layer).casefold())
        indexed = indexes.get(key)
        if indexed is None:
            if active_key is not None:
                indexes[active_key].release_artwork_shape()
            active_artwork = None
            active_key = None
            return key, None, None
        if active_key == key and active_artwork is not None:
            return key, indexed, active_artwork
        if active_key is not None:
            indexes[active_key].release_artwork_shape()
        active_artwork = None
        active_key = None
        component_wkbs = component_wkb_by_key.get(key)
        if component_wkbs is None:
            return key, indexed, None
        try:
            from shapely.prepared import prep
            from shapely.strtree import STRtree
            from shapely.wkb import loads as load_wkb

            components = tuple(load_wkb(item) for item in component_wkbs)
            active_artwork = (
                components,
                STRtree(components),
                tuple(prep(item) for item in components),
            )
        except Exception as exc:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: retained ordered artwork "
                f"cannot be restored for {net!r} on {layer!r}: {exc}"
            ) from exc
        active_key = key
        return key, indexed, active_artwork

    def release(net: str, layer: str) -> None:
        nonlocal active_artwork, active_key
        key = (str(net).casefold(), str(layer).casefold())
        indexed = indexes.get(key)
        if indexed is not None:
            indexed.release_artwork_shape()
        if active_key == key:
            active_artwork = None
            active_key = None

    check_cancelled()
    emit(0, "Indexing retained layer-surface artwork")
    total_records = len(records)
    for record_index, record in enumerate(records):
        check_cancelled()
        if not isinstance(record, dict):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: geometry index row is invalid"
            )
        layer = str(record.get("layer", "")).strip()
        net = str(record.get("net", "")).strip()
        asset = str(record.get("asset", "")).strip()
        asset_sha256 = str(record.get("asset_sha256", "")).strip().casefold()
        key = (net.casefold(), layer.casefold())
        compressed = attachments.get(asset)
        geometries = geometry_groups.get(key, ())
        if (
            not layer
            or not net
            or not asset
            or len(asset_sha256) != 64
            or any(character not in "0123456789abcdef" for character in asset_sha256)
        ):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: geometry asset identity is incomplete"
            )
        if compressed is None or sha256(compressed).hexdigest() != asset_sha256:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: geometry attachment hash "
                f"mismatch for {net!r} on {layer!r}"
            )
        if key in indexes or len(geometries) != 1:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: retained exact geometry is "
                f"missing or ambiguous for {net!r} on {layer!r}"
            )
        indexed = (
            indexed_geometry_by_key.get((layer.casefold(), net.casefold()))
            if indexed_geometry_by_key is not None
            else None
        )
        if indexed is None:
            indexed = IndexedPlaneGeometry.build(geometries[0])
        if indexed is None:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: exact geometry cannot be "
                f"indexed for {net!r} on {layer!r}"
            )
        indexes[key] = indexed

        emit(
            round(record_index * 99 / max(1, total_records)),
            "Identifying retained surface islands "
            f"({record_index}/{total_records})",
        )
        try:
            filled = indexed._artwork_components()
            if filled is False:
                raise ValueError("ordered artwork could not be constructed")
            components = tuple(filled[0])
            if not components:
                raise ValueError("ordered artwork contains no connected island")
            from shapely.geometry import MultiPolygon

            combined = (
                components[0]
                if len(components) == 1
                else MultiPolygon(components)
            )
            islands = core_services._spd_surface_islands(
                layer=layer,
                net=net,
                asset_sha256=asset_sha256,
                shape=combined,
                progress=(
                    (
                        lambda value, message, low=record_index: emit(
                            round(
                                (
                                    low
                                    + max(0, min(100, value)) / 100
                                )
                                * 99
                                / max(1, total_records)
                            ),
                            message,
                        )
                    )
                    if progress is not None or is_cancelled is not None
                    else None
                ),
                is_cancelled=(cancelled if is_cancelled is not None else None),
            )
            island_id_by_digest = {
                sha256(bytes(island.normalize().wkb)).hexdigest(): island_id
                for island_id, island in islands
            }
            aligned_ids = tuple(
                island_id_by_digest[
                    sha256(bytes(component.normalize().wkb)).hexdigest()
                ]
                for component in components
            )
            aligned_component_wkbs = tuple(
                bytes(component.wkb) for component in components
            )
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: cannot identify exact "
                f"connected islands for {net!r} on {layer!r}: {exc}"
            ) from exc
        finally:
            indexed.release_artwork_shape()

        if len(set(aligned_ids)) != len(aligned_ids):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: connected island identities "
                f"are not unique for {net!r} on {layer!r}"
            )
        component_island_ids[key] = aligned_ids
        component_wkb_by_key[key] = aligned_component_wkbs
        canonical_ids = tuple(sorted(aligned_ids))
        target_surface_island_ids[key] = canonical_ids
        target_layers_by_net.setdefault(net.casefold(), set()).add(layer)
        identities.append(
            {
                "layer": layer,
                "net": net,
                "asset": asset,
                "asset_sha256": asset_sha256,
                "island_ids": list(canonical_ids),
            }
        )

    identities.sort(
        key=lambda item: (
            item["net"].casefold(),
            item["layer"].casefold(),
            item["asset"].casefold(),
            item["asset_sha256"],
        )
    )
    emit(100, f"Indexed {len(identities)} retained layer-surface asset(s)")

    def scalar_strict_component_index(
        filled: tuple[tuple[object, ...], object, tuple[object, ...]],
        x_um: float,
        y_um: float,
    ) -> int | None:
        from shapely.geometry import Point

        components, tree, prepared = filled
        point = Point(float(x_um), float(y_um))
        try:
            candidates = tree.query(point, predicate="intersects")
        except TypeError:
            candidates = tree.query(point)
        for candidate in candidates:
            try:
                component_index = int(candidate)
            except (TypeError, ValueError):
                component_index = components.index(candidate)
            if prepared[component_index].contains(point):
                return component_index
        return None

    def strict_component_indices(
        net: str,
        layer: str,
        points: Sequence[tuple[float, float]],
    ) -> tuple[int | None, ...]:
        _key, indexed, filled = activate(net, layer)
        if indexed is None or filled is None:
            return (None,) * len(points)
        normalized = tuple((float(x), float(y)) for x, y in points)
        if any(not isfinite(x) or not isfinite(y) for x, y in normalized):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: artwork query coordinates "
                "must be finite"
            )
        bounds = indexed.positive_bounds
        tolerance_um = 1.0e-6
        in_bounds = tuple(
            index
            for index, (x_um, y_um) in enumerate(normalized)
            if bounds[0] - tolerance_um <= x_um <= bounds[1] + tolerance_um
            and bounds[2] - tolerance_um <= y_um <= bounds[3] + tolerance_um
        )
        if not in_bounds:
            return (None,) * len(normalized)
        components, tree, _prepared = filled
        try:
            from shapely import contains_xy, points as shapely_points
        except ImportError:
            fallback_results: list[int | None] = [None] * len(normalized)
            for index in in_bounds:
                x_um, y_um = normalized[index]
                fallback_results[index] = scalar_strict_component_index(
                    filled, x_um, y_um
                )
            return tuple(fallback_results)
        results: list[int | None] = [None] * len(normalized)
        if len(components) == 1:
            try:
                component = components[0]
                for start in range(0, len(in_bounds), 32768):
                    chunk = in_bounds[start : start + 32768]
                    inside = contains_xy(
                        component,
                        [normalized[index][0] for index in chunk],
                        [normalized[index][1] for index in chunk],
                    )
                    for local_index, is_inside in enumerate(inside):
                        if bool(is_inside):
                            results[chunk[local_index]] = 0
                return tuple(results)
            except (AttributeError, TypeError, ValueError):
                pass
        try:
            for start in range(0, len(in_bounds), 32768):
                chunk = in_bounds[start : start + 32768]
                point_geometries = shapely_points(
                    [normalized[index][0] for index in chunk],
                    [normalized[index][1] for index in chunk],
                )
                pairs = tree.query(point_geometries)
                point_indices_by_component: dict[int, list[int]] = {}
                for point_index, component_index in zip(
                    pairs[0], pairs[1], strict=False
                ):
                    point_indices_by_component.setdefault(
                        int(component_index), []
                    ).append(int(point_index))
                for component_index, point_indices in (
                    point_indices_by_component.items()
                ):
                    component = components[component_index]
                    inside = contains_xy(
                        component,
                        [
                            normalized[chunk[point_index]][0]
                            for point_index in point_indices
                        ],
                        [
                            normalized[chunk[point_index]][1]
                            for point_index in point_indices
                        ],
                    )
                    for point_index, is_inside in zip(
                        point_indices, inside, strict=True
                    ):
                        result_index = chunk[point_index]
                        if bool(is_inside) and results[result_index] is None:
                            results[result_index] = component_index
        except (AttributeError, TypeError, ValueError):
            for index in in_bounds:
                x_um, y_um = normalized[index]
                results[index] = scalar_strict_component_index(
                    filled, x_um, y_um
                )
        return tuple(results)

    def artwork_component(
        net: str, layer: str, x_um: float, y_um: float
    ) -> object | None:
        try:
            return strict_component_indices(
                net, layer, ((float(x_um), float(y_um)),)
            )[0]
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: exact artwork component "
                f"query failed for {net!r} on {layer!r}: {exc}"
            ) from exc

    def artwork_components_batch(
        net: str,
        layer: str,
        points: Sequence[tuple[float, float]],
    ) -> Sequence[object | None]:
        try:
            return strict_component_indices(net, layer, points)
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: exact artwork batch "
                f"query failed for {net!r} on {layer!r}: {exc}"
            ) from exc

    def covering_component_indices(
        net: str,
        layer: str,
        points: Sequence[tuple[float, float]],
    ) -> tuple[int | None, ...]:
        _key, indexed, filled = activate(net, layer)
        if indexed is None or filled is None:
            return (None,) * len(points)
        normalized = tuple((float(x), float(y)) for x, y in points)
        if any(not isfinite(x) or not isfinite(y) for x, y in normalized):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: surface query coordinates "
                "must be finite"
            )
        components, tree, _prepared = filled
        results: list[int | None] = [None] * len(normalized)
        try:
            from shapely import points as shapely_points

            point_geometries = shapely_points(
                [point[0] for point in normalized],
                [point[1] for point in normalized],
            )
            pairs = tree.query(point_geometries, predicate="intersects")
            for point_index, component_index in zip(
                pairs[0], pairs[1], strict=True
            ):
                point_position = int(point_index)
                candidate = int(component_index)
                previous = results[point_position]
                if previous is not None and previous != candidate:
                    raise SpdImportError(
                        "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: one source Node is "
                        "covered by multiple disconnected islands"
                    )
                results[point_position] = candidate
        except ImportError:
            from shapely.geometry import Point

            for point_index, (x_um, y_um) in enumerate(normalized):
                point = Point(x_um, y_um)
                try:
                    candidates = tree.query(point, predicate="intersects")
                except TypeError:
                    candidates = tree.query(point)
                matches: list[int] = []
                for candidate in candidates:
                    try:
                        component_index = int(candidate)
                    except (TypeError, ValueError):
                        component_index = components.index(candidate)
                    if bool(components[component_index].covers(point)):
                        matches.append(component_index)
                if len(set(matches)) > 1:
                    raise SpdImportError(
                        "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: one source Node is "
                        "covered by multiple disconnected islands"
                    )
                if matches:
                    results[point_index] = matches[0]
        except (AttributeError, TypeError, ValueError):
            from shapely.geometry import Point

            for point_index, (x_um, y_um) in enumerate(normalized):
                point = Point(x_um, y_um)
                matches = [
                    index
                    for index, component in enumerate(components)
                    if bool(component.covers(point))
                ]
                if len(matches) > 1:
                    raise SpdImportError(
                        "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: one source Node is "
                        "covered by multiple disconnected islands"
                    )
                if matches:
                    results[point_index] = matches[0]
        return tuple(results)

    def resolve_batch(
        net: str,
        layer: str,
        node_ids: Sequence[str],
        points: Sequence[tuple[float, float]],
    ) -> Sequence[str | None]:
        if len(node_ids) != len(points):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: surface batch identity "
                "length does not match its point batch"
            )
        key = (str(net).casefold(), str(layer).casefold())
        island_ids = component_island_ids.get(key)
        if island_ids is None:
            return (None,) * len(points)
        return tuple(
            None if component is None else island_ids[component]
            for component in covering_component_indices(net, layer, points)
        )

    def resolve_island(
        net: str,
        layer: str,
        node_id: str,
        x_um: float,
        y_um: float,
    ) -> str | None:
        return resolve_batch(
            net, layer, (node_id,), ((float(x_um), float(y_um)),)
        )[0]

    def strict_batch(
        net: str,
        layer: str,
        node_ids: Sequence[str],
        points: Sequence[tuple[float, float]],
    ) -> Sequence[str | None]:
        if len(node_ids) != len(points):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: strict surface batch "
                "identity length does not match its point batch"
            )
        key = (str(net).casefold(), str(layer).casefold())
        island_ids = component_island_ids.get(key)
        if island_ids is None:
            return (None,) * len(points)
        component_indices = strict_component_indices(net, layer, points)
        if active_key != key or active_artwork is None:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_ARTWORK_REQUIRED: exact artwork shape is invalid"
            )
        shapes = active_artwork[0]
        from shapely.geometry import Point

        resolved: list[str | None] = []
        for component_index, (x_um, y_um) in zip(
            component_indices, points, strict=True
        ):
            if component_index is None:
                resolved.append(None)
                continue
            point = Point(float(x_um), float(y_um))
            resolved.append(
                island_ids[component_index]
                if float(point.distance(shapes[component_index].boundary)) > 1.0e-6
                else None
            )
        return tuple(resolved)

    def resolve_strict_island(
        net: str,
        layer: str,
        node_id: str,
        x_um: float,
        y_um: float,
    ) -> str | None:
        return strict_batch(
            net, layer, (node_id,), ((float(x_um), float(y_um)),)
        )[0]

    def covers(
        net: str,
        layer: str,
        node_id: str,
        x_um: float,
        y_um: float,
    ) -> bool:
        return resolve_island(net, layer, node_id, x_um, y_um) is not None

    return _RetainedSurfaceArtwork(
        node_predicate=covers,
        surface_resolver=resolve_island,
        surface_resolver_batch=resolve_batch,
        strict_surface_resolver=resolve_strict_island,
        strict_surface_resolver_batch=strict_batch,
        artwork_component=artwork_component,
        artwork_components_batch=artwork_components_batch,
        release=release,
        geometry_assets=identities,
        target_layers_by_net=target_layers_by_net,
        island_ids_by_surface=target_surface_island_ids,
    )

def _compile_retarget_landing_destination_requests(
    *,
    project: ProjectSpec,
    decap_connections: Iterable[Any],
    geometry_assets: Iterable[Mapping[str, Any]],
    strict_island_resolver: Callable[
        [str, str, str, float, float], str | None
    ],
    strict_island_resolver_batch: Callable[
        [str, str, Sequence[str], Sequence[tuple[float, float]]],
        Sequence[str | None],
    ] | None = None,
    release_surface: Callable[[str, str], None] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> tuple[
    tuple[_RetargetLandingDestinationRequest, ...],
    dict[str, Any],
]:
    """Enumerate exact retarget destinations in bounded surface batches."""

    report = progress or (lambda _value, _message: None)
    cancelled = is_cancelled or (lambda: False)
    connections = tuple(decap_connections)
    total_landings = sum(
        len(tuple(getattr(connection, "power_vias", ())))
        for connection in connections
    )
    report(
        0,
        "Indexing exact Distribution retarget destinations "
        f"for {total_landings:,} PWR landing(s)",
    )
    rails_by_net: dict[str, list[dict[str, str | None]]] = {}
    for rail in getattr(project, "rails", ()):
        rail_id = str(getattr(rail, "rail_id", "")).strip()
        net = str(getattr(rail, "net", "")).strip()
        if rail_id and net:
            rails_by_net.setdefault(net.casefold(), []).append(
                {
                    "rail_id": rail_id,
                    "via_template_id": _template_for_rail(project, rail_id),
                }
            )
    rails_by_net = {
        net_key: sorted(
            {
                str(item["rail_id"]).casefold(): item
                for item in rail_rows
            }.values(),
            key=lambda item: str(item["rail_id"]).casefold(),
        )
        for net_key, rail_rows in rails_by_net.items()
    }

    conductor_net_layers = {
        (str(net).strip().casefold(), layer_name.casefold())
        for raw_layer in getattr(project, "stackup_layers", ())
        if bool(getattr(raw_layer, "is_conductor", False))
        and (layer_name := str(getattr(raw_layer, "name", "")).strip())
        for net in getattr(raw_layer, "pwr_nets", ())
        if str(net).strip()
    }
    surfaces: list[dict[str, Any]] = []
    seen_surface_keys: set[tuple[str, str]] = set()
    for raw_asset in geometry_assets:
        net = str(raw_asset.get("net", "")).strip()
        layer = str(raw_asset.get("layer", "")).strip()
        asset_sha256 = str(raw_asset.get("asset_sha256", "")).strip().casefold()
        key = (net.casefold(), layer.casefold())
        eligible_rails = rails_by_net.get(key[0], [])
        if (
            not net
            or not layer
            or len(asset_sha256) != 64
            or not eligible_rails
            or (conductor_net_layers and key not in conductor_net_layers)
        ):
            continue
        if key in seen_surface_keys:
            raise SpdImportError(
                "SPD_RETARGET_LANDING_SURFACE_DUPLICATE: retained geometry "
                f"repeats {net!r} on {layer!r}"
            )
        seen_surface_keys.add(key)
        surfaces.append(
            {
                "net": net,
                "layer": layer,
                "asset_sha256": asset_sha256,
                "eligible_rails": eligible_rails,
            }
        )
    surfaces.sort(
        key=lambda item: (
            item["net"].casefold(),
            item["layer"].casefold(),
            item["asset_sha256"],
        )
    )

    landing_identities: list[dict[str, str]] = []
    landing_rows: list[dict[str, Any]] = []
    seen_landing_keys: set[tuple[str, str, str]] = set()
    processed_landings = 0
    for connection in sorted(
        connections,
        key=lambda item: str(getattr(item, "refdes", "")).casefold(),
    ):
        refdes = str(getattr(connection, "refdes", "")).strip()
        for landing in tuple(getattr(connection, "power_vias", ())):
            if cancelled():
                raise RuntimeError("SPD retarget destination scan cancelled")
            if processed_landings % 64 == 0:
                report(
                    round(20 * processed_landings / max(1, total_landings)),
                    "Normalizing exact Distribution retarget landings "
                    f"({processed_landings:,}/{total_landings:,})",
                )
            via_id = str(getattr(landing, "via_id", "")).strip()
            node_id = str(getattr(landing, "endpoint_node_id", "")).strip()
            source_net = str(getattr(landing, "net", "")).strip()
            try:
                x_um = float(getattr(landing, "x_um"))
                y_um = float(getattr(landing, "y_um"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise SpdImportError(
                    "SPD_RETARGET_LANDING_IDENTITY_INVALID: every PWR Via "
                    "landing requires finite immutable XY coordinates"
                ) from exc
            landing_key = (
                refdes.casefold(),
                via_id.casefold(),
                node_id.casefold(),
            )
            if (
                not all((refdes, via_id, node_id, source_net))
                or not isfinite(x_um)
                or not isfinite(y_um)
                or landing_key in seen_landing_keys
            ):
                raise SpdImportError(
                    "SPD_RETARGET_LANDING_IDENTITY_INVALID: PWR landing "
                    "REFDES/Via/Node identities must be nonblank and unique"
                )
            seen_landing_keys.add(landing_key)
            landing_identities.append(
                {
                    "refdes": refdes.casefold(),
                    "via_id": via_id.casefold(),
                    "endpoint_node_id": node_id.casefold(),
                }
            )
            landing_rows.append(
                {
                    "refdes": refdes,
                    "via_id": via_id,
                    "node_id": node_id,
                    "source_net": source_net,
                    "x_um": x_um,
                    "y_um": y_um,
                    "query_id": f"retarget:{refdes}:{via_id}:{node_id}",
                }
            )
            processed_landings += 1

    requests: list[_RetargetLandingDestinationRequest] = []
    total_tests = len(landing_rows) * len(surfaces)
    processed_tests = 0
    for surface in surfaces:
        if cancelled():
            raise RuntimeError("SPD retarget destination scan cancelled")
        try:
            for start in range(0, len(landing_rows), 32768):
                if cancelled():
                    raise RuntimeError("SPD retarget destination scan cancelled")
                batch = landing_rows[start : start + 32768]
                node_ids = tuple(str(item["query_id"]) for item in batch)
                points = tuple(
                    (float(item["x_um"]), float(item["y_um"]))
                    for item in batch
                )
                if strict_island_resolver_batch is not None:
                    island_ids = strict_island_resolver_batch(
                        str(surface["net"]),
                        str(surface["layer"]),
                        node_ids,
                        points,
                    )
                else:
                    island_ids = tuple(
                        strict_island_resolver(
                            str(surface["net"]),
                            str(surface["layer"]),
                            node_id,
                            x_um,
                            y_um,
                        )
                        for node_id, (x_um, y_um) in zip(
                            node_ids, points, strict=True
                        )
                    )
                if len(island_ids) != len(batch):
                    raise SpdImportError(
                        "SPD_RETARGET_LANDING_SURFACE_BATCH_INVALID: exact "
                        "surface resolver returned an invalid result length"
                    )
                for landing, island_id in zip(batch, island_ids, strict=True):
                    if island_id is None:
                        continue
                    requests.extend(
                        _RetargetLandingDestinationRequest(
                            refdes=str(landing["refdes"]),
                            via_id=str(landing["via_id"]),
                            endpoint_node_id=str(landing["node_id"]),
                            source_net=str(landing["source_net"]),
                            x_um=float(landing["x_um"]),
                            y_um=float(landing["y_um"]),
                            destination_net=str(surface["net"]),
                            destination_layer=str(surface["layer"]),
                            destination_island_id=str(island_id),
                            geometry_asset_sha256=str(
                                surface["asset_sha256"]
                            ),
                            target_rail_id=str(rail["rail_id"]),
                            via_template_id=(
                                str(rail["via_template_id"])
                                if rail["via_template_id"] is not None
                                else None
                            ),
                        )
                        for rail in surface["eligible_rails"]
                    )
                processed_tests += len(batch)
                report(
                    20 + round(79 * processed_tests / max(1, total_tests)),
                    "Indexing exact Distribution retarget destinations "
                    f"({processed_tests:,}/{total_tests:,} surface tests)",
                )
        finally:
            if release_surface is not None:
                release_surface(
                    str(surface["net"]), str(surface["layer"])
                )

    requests.sort(
        key=lambda item: (
            item.refdes.casefold(),
            item.via_id.casefold(),
            item.endpoint_node_id.casefold(),
            item.destination_net.casefold(),
            item.destination_layer.casefold(),
            item.target_rail_id.casefold(),
            item.destination_island_id,
        )
    )
    request_identities = [
        {
            "refdes": item.refdes.casefold(),
            "via_id": item.via_id.casefold(),
            "endpoint_node_id": item.endpoint_node_id.casefold(),
            "destination_net": item.destination_net.casefold(),
            "destination_layer": item.destination_layer.casefold(),
            "destination_island_id": item.destination_island_id,
            "target_rail_id": item.target_rail_id.casefold(),
        }
        for item in requests
    ]
    coverage = {
        "power_landing_count": len(landing_identities),
        "scanned_landing_count": len(seen_landing_keys),
        "candidate_surface_count": len(surfaces),
        "candidate_surface_rail_count": sum(
            len(item["eligible_rails"]) for item in surfaces
        ),
        "landing_surface_test_count": total_tests,
        "covered_destination_count": len(requests),
        "power_landing_ids_sha256": core_services._canonical_metadata_sha256(
            {"landings": landing_identities}
        ),
        "candidate_surface_ids_sha256": (
            core_services._canonical_metadata_sha256({"surfaces": surfaces})
        ),
        "covered_destination_ids_sha256": (
            core_services._canonical_metadata_sha256(
                {"destinations": request_identities}
            )
        ),
        "artwork_predicate": (
            "ordered_geometry_strict_interior_boundary_distance_gt_1e-6_um"
        ),
        "status": "complete",
    }
    report(
        100,
        "Indexed exact Distribution retarget destinations "
        f"({len(requests):,} destination binding(s))",
    )
    return tuple(requests), coverage

def _bind_certified_surface_islands(
    plane_geometry_records: object,
    certified_geometry_assets: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Bind exact connected-island IDs back to the project geometry index.

    The compiled-only topology compiler consumes the project plane-geometry
    manifest, while exact island discovery happens later when retained artwork
    is reconstructed.  Keep those two views atomically identical; otherwise a
    production-complete certificate is incorrectly routed to the legacy raw
    multi-GiB certificate path.
    """

    if not isinstance(plane_geometry_records, list):
        raise SpdImportError(
            "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_INVALID: retained project "
            "plane geometry index is not a list"
        )

    def identity(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(row.get("net", "")).strip().casefold(),
            str(row.get("layer", "")).strip().casefold(),
            str(row.get("asset", "")).strip(),
            str(row.get("asset_sha256", "")).strip().casefold(),
        )

    certified_by_identity: dict[
        tuple[str, str, str, str], tuple[str, ...]
    ] = {}
    for raw_certified in certified_geometry_assets:
        if not isinstance(raw_certified, Mapping):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_INVALID: certified "
                "surface geometry row is not a mapping"
            )
        key = identity(raw_certified)
        raw_islands = raw_certified.get("island_ids")
        islands = (
            tuple(str(item).strip() for item in raw_islands)
            if isinstance(raw_islands, (list, tuple))
            else ()
        )
        if (
            not all(key)
            or len(key[3]) != 64
            or any(character not in "0123456789abcdef" for character in key[3])
            or not islands
            or any(not item for item in islands)
            or len(set(islands)) != len(islands)
            or key in certified_by_identity
        ):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_INVALID: certified "
                "surface geometry identity/island partition is incomplete "
                "or duplicated"
            )
        certified_by_identity[key] = islands

    merged: list[dict[str, Any]] = []
    seen_project_identities: set[tuple[str, str, str, str]] = set()
    for raw_record in plane_geometry_records:
        if not isinstance(raw_record, Mapping):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_INVALID: retained project "
                "plane geometry row is not a mapping"
            )
        key = identity(raw_record)
        islands = certified_by_identity.get(key)
        if (
            not all(key)
            or key in seen_project_identities
            or islands is None
        ):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_MISMATCH: retained project "
                "and certified surface geometry identities differ"
            )
        seen_project_identities.add(key)
        existing = raw_record.get("island_ids")
        if existing is not None and (
            not isinstance(existing, (list, tuple))
            or tuple(str(item).strip() for item in existing) != islands
        ):
            raise SpdImportError(
                "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_MISMATCH: an existing "
                "surface-island partition conflicts with exact artwork"
            )
        merged.append({**dict(raw_record), "island_ids": list(islands)})

    if seen_project_identities != set(certified_by_identity):
        raise SpdImportError(
            "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_MISMATCH: certified surface "
            "geometry coverage is not exact"
        )
    return merged


def _layer_surface_connectivity_certificate(
    *,
    project: ProjectSpec,
    source_sha256: str,
    geometry_assets: list[dict[str, Any]],
    rail_anchor_bindings: list[dict[str, str]],
    compile_failures: list[dict[str, str]],
    contact_seeds: dict[str, dict[str, Any]],
    landing_by_pin: dict[str, _SourceGraphLanding],
    reachability: Any,
    decap_connections: Iterable[Any] = (),
    shared_pad_clusters: Iterable[Any] = (),
    retarget_landing_destination_requests: Iterable[Any] = (),
    retarget_landing_scan_coverage: Mapping[str, Any] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> dict[str, Any]:
    """Create deterministic persisted evidence from one exact graph pass."""

    raw_report = progress or (lambda _value, _message: None)
    last_reported_progress = -1

    def report(value: int, message: str) -> None:
        nonlocal last_reported_progress
        bounded = max(
            last_reported_progress, min(100, max(0, int(value)))
        )
        last_reported_progress = bounded
        raw_report(bounded, message)

    cancelled = is_cancelled or (lambda: False)

    def check_cancelled(phase: str) -> None:
        if cancelled():
            raise SpdImportError(
                "SPD_LAYER_SURFACE_CONNECTIVITY_CERTIFICATE_CANCELLED: "
                f"cancelled while {phase}"
            )

    def retarget_landing_order_key(item: Any) -> tuple[str, ...]:
        return (
            str(getattr(item, "refdes", "")).casefold(),
            str(getattr(item, "via_id", "")).casefold(),
            str(getattr(item, "endpoint_node_id", "")).casefold(),
            str(getattr(item, "destination_net", "")).casefold(),
            str(getattr(item, "destination_layer", "")).casefold(),
            str(getattr(item, "target_rail_id", "")).casefold(),
            str(getattr(item, "destination_island_id", "")),
        )

    def canonical_concrete_row_bytes(row: Mapping[str, Any]) -> bytes:
        return json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    report(0, "Normalizing layer-surface connectivity certificate evidence")
    check_cancelled("starting layer-surface certificate compilation")
    decap_connections = tuple(decap_connections)
    shared_pad_clusters = tuple(shared_pad_clusters)
    retarget_landing_destination_requests = tuple(
        retarget_landing_destination_requests
    )
    previous_order_key: tuple[str, ...] | None = None
    retarget_requests_are_sorted = True
    for request_index, request in enumerate(
        retarget_landing_destination_requests
    ):
        if request_index % 4096 == 0:
            check_cancelled("normalizing exact retarget landing bindings")
        order_key = retarget_landing_order_key(request)
        if previous_order_key is not None and order_key < previous_order_key:
            retarget_requests_are_sorted = False
            break
        previous_order_key = order_key
    if not retarget_requests_are_sorted:
        retarget_landing_destination_requests = tuple(
            sorted(
                retarget_landing_destination_requests,
                key=retarget_landing_order_key,
            )
        )
    scenario_topology_requested = bool(decap_connections)

    if not isinstance(geometry_assets, list) or not all(
        isinstance(item, Mapping) for item in geometry_assets
    ):
        raise SpdImportError(
            "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_INVALID: every retained "
            "plane-geometry row must be a mapping"
        )

    conductor_position_by_key: dict[str, int] = {}
    conductor_center_um_by_key: dict[str, float] = {}
    duplicate_conductor_keys: set[str] = set()
    depth_um = 0.0
    conductor_ordinal = 0
    for raw_layer in getattr(project, "stackup_layers", ()):
        name = str(getattr(raw_layer, "name", "")).strip()
        try:
            thickness_um = float(getattr(raw_layer, "thickness_um"))
        except (AttributeError, TypeError, ValueError):
            thickness_um = float("nan")
        if bool(getattr(raw_layer, "is_conductor", False)) and name:
            key = name.casefold()
            if key in conductor_position_by_key:
                duplicate_conductor_keys.add(key)
            else:
                conductor_position_by_key[key] = conductor_ordinal
                if isfinite(thickness_um) and thickness_um > 0.0:
                    conductor_center_um_by_key[key] = (
                        depth_um + thickness_um / 2.0
                    )
            conductor_ordinal += 1
        if isfinite(thickness_um) and thickness_um > 0.0:
            depth_um += thickness_um

    def segment_stackup_issues(
        segments: list[dict[str, Any]],
        *,
        expected_start_layer: object,
        expected_end_layer: object,
    ) -> set[str]:
        """Re-certify one Via chain against the current project stack-up."""

        issues: set[str] = set()
        if not segments:
            return {"physical_segment_chain_missing"}
        start_key = str(expected_start_layer or "").strip().casefold()
        end_key = str(expected_end_layer or "").strip().casefold()
        ordinals = [int(item.get("ordinal", -1)) for item in segments]
        if ordinals != list(range(len(segments))):
            issues.add("segment_chain_ordinal_invalid")
        segment_pairs = [
            (
                str(item.get("start_layer", "")).strip().casefold(),
                str(item.get("end_layer", "")).strip().casefold(),
            )
            for item in segments
        ]
        if (
            not start_key
            or not end_key
            or segment_pairs[0][0] != start_key
            or segment_pairs[-1][1] != end_key
            or any(
                first[1] != second[0]
                for first, second in zip(
                    segment_pairs, segment_pairs[1:], strict=False
                )
            )
        ):
            issues.add("segment_chain_endpoint_or_continuity_mismatch")
        path_layer_keys = [segment_pairs[0][0]] + [
            pair[1] for pair in segment_pairs
        ]
        if len(set(path_layer_keys)) != len(path_layer_keys):
            issues.add("segment_stackup_path_repeats_layer")
        if any(
            not key
            or key in duplicate_conductor_keys
            or key not in conductor_position_by_key
            or key not in conductor_center_um_by_key
            for key in path_layer_keys
        ):
            issues.add("segment_stackup_layer_missing_or_ambiguous")
            return issues
        positions = [conductor_position_by_key[key] for key in path_layer_keys]
        deltas = [
            second - first
            for first, second in zip(positions, positions[1:], strict=False)
        ]
        if not deltas or not (
            all(delta > 0 for delta in deltas)
            or all(delta < 0 for delta in deltas)
        ):
            issues.add("segment_stackup_path_non_monotonic")
        for item, (first_key, second_key) in zip(
            segments, segment_pairs, strict=True
        ):
            try:
                length_um = float(item.get("length_um"))
            except (TypeError, ValueError):
                issues.add("segment_length_stackup_mismatch")
                continue
            expected_length_um = abs(
                conductor_center_um_by_key[second_key]
                - conductor_center_um_by_key[first_key]
            )
            if (
                not isfinite(length_um)
                or length_um <= 0.0
                or not isclose(
                    length_um,
                    expected_length_um,
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                )
            ):
                issues.add("segment_length_stackup_mismatch")
        return issues

    raw_layers_by_landing = getattr(
        reachability, "surface_layers_by_landing", {}
    )
    raw_islands_by_landing = getattr(
        reachability, "surface_islands_by_landing", {}
    )
    raw_finite_vertex_by_landing = getattr(
        reachability, "finite_via_vertex_id_by_landing", {}
    )
    raw_finite_edge_by_landing = getattr(
        reachability, "finite_via_edge_id_by_landing", {}
    )
    raw_landing_surface_contacts = tuple(
        getattr(reachability, "landing_surface_contacts", ())
    )
    landing_contact_identity_by_via: dict[str, tuple[str, str]] = {}
    for item in raw_landing_surface_contacts:
        if str(
            getattr(item, "terminal_owner_kind", "unknown")
        ).strip().casefold() != "device":
            continue
        via_key = str(getattr(item, "via_id", "")).strip().casefold()
        identity = (
            str(getattr(item, "net", "")).strip().casefold(),
            str(getattr(item, "endpoint_node_id", "")).strip().casefold(),
        )
        if not via_key:
            continue
        previous = landing_contact_identity_by_via.get(via_key)
        if previous is not None:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_TERMINAL_VIA_OWNERSHIP_INVALID: one physical "
                "terminal Via ID has multiple persisted landing contacts"
            )
        landing_contact_identity_by_via[via_key] = identity
    direct_via_owner_by_key: dict[str, str] = {}
    for pin_key, landing in landing_by_pin.items():
        seed = contact_seeds.get(pin_key, {})
        if str(seed.get("contact_path_kind", "")).strip() != "direct_via_landing":
            continue
        via_key = str(getattr(landing, "via_id", "")).strip().casefold()
        if not via_key:
            continue
        previous_pin = direct_via_owner_by_key.get(via_key)
        if previous_pin is not None and previous_pin != pin_key:
            raise SpdImportError(
                "SPD_LAYER_SURFACE_TERMINAL_VIA_OWNERSHIP_INVALID: one physical "
                "terminal Via ID is assigned to multiple device pins"
            )
        direct_via_owner_by_key[via_key] = pin_key
    landing_surface_contact_by_key = {
        (
            str(getattr(item, "via_id", "")).casefold(),
            str(getattr(item, "endpoint_node_id", "")).casefold(),
        ): item
        for item in raw_landing_surface_contacts
        if str(getattr(item, "via_id", "")).strip()
        and str(getattr(item, "endpoint_node_id", "")).strip()
    }
    terminal_contacts: list[dict[str, Any]] = []
    for pin_key in sorted(contact_seeds):
        contact = dict(contact_seeds[pin_key])
        issues = list(contact["issues"])
        landing = landing_by_pin.get(pin_key)
        contact_layers: tuple[str, ...] = ()
        if landing is not None:
            contact_layers = tuple(
                raw_layers_by_landing.get(
                    (
                        landing.via_id.casefold(),
                        landing.endpoint_node_id.casefold(),
                    ),
                    (),
                )
            )
        contact["contact_layers"] = sorted(
            {str(layer) for layer in contact_layers}, key=str.casefold
        )
        contact["contact_island_ids"] = sorted(
            {
                str(island_id)
                for island_id in (
                    raw_islands_by_landing.get(
                        (
                            landing.via_id.casefold(),
                            landing.endpoint_node_id.casefold(),
                        ),
                        (),
                    )
                    if landing is not None
                    else ()
                )
                if str(island_id).strip()
            }
        )
        landing_surface_contact = (
            landing_surface_contact_by_key.get(
                (
                    landing.via_id.casefold(),
                    landing.endpoint_node_id.casefold(),
                )
            )
            if landing is not None
            else None
        )
        contact["contact_island_ids_by_layer"] = {
            str(layer): list(island_ids)
            for layer, island_ids in sorted(
                dict(
                    getattr(
                        landing_surface_contact,
                        "contact_island_ids_by_layer",
                        {},
                    )
                ).items(),
                key=lambda item: (str(item[0]).casefold(), str(item[0])),
            )
        }
        contact["external_endpoint_node_id"] = (
            landing.endpoint_node_id if landing is not None else None
        )
        finite_landing_key = (
            (
                landing.via_id.casefold(),
                landing.endpoint_node_id.casefold(),
            )
            if landing is not None
            else None
        )
        contact["exposed_quotient_vertex_id"] = (
            raw_finite_vertex_by_landing.get(finite_landing_key)
            if finite_landing_key is not None
            else None
        )
        contact["first_via_quotient_edge_id"] = (
            raw_finite_edge_by_landing.get(finite_landing_key)
            if finite_landing_key is not None
            else None
        )
        contact["internal_endpoint_node_id"] = getattr(
            landing_surface_contact, "internal_endpoint_node_id", None
        )
        expected_internal_node_id = str(
            contact.get("incident_opposite_node_id") or ""
        ).strip()
        observed_internal_node_id = str(
            contact.get("internal_endpoint_node_id") or ""
        ).strip()
        if (
            expected_internal_node_id
            and observed_internal_node_id
            and expected_internal_node_id.casefold()
            != observed_internal_node_id.casefold()
        ):
            issues.append("incident_opposite_endpoint_mismatch")
        direct_rows = contact["contact_island_ids_by_layer"]
        contact["endpoint_layer"] = (
            next(iter(direct_rows)) if len(direct_rows) == 1 else None
        )
        contact["endpoint_island_id"] = (
            next(iter(direct_rows.values()))[0]
            if len(direct_rows) == 1
            and len(next(iter(direct_rows.values()))) == 1
            else None
        )
        contact["issues"] = sorted(set(issues))
        contact["status"] = (
            "complete"
            if landing is not None
            and bool(contact["contact_layers"])
            and bool(contact["contact_island_ids"])
            and len(contact["contact_island_ids_by_layer"]) == 1
            and len(
                next(iter(contact["contact_island_ids_by_layer"].values()))
            )
            == 1
            and contact["endpoint_layer"]
            in contact["contact_island_ids_by_layer"]
            and contact["endpoint_island_id"]
            == contact["contact_island_ids_by_layer"][
                contact["endpoint_layer"]
            ][0]
            and bool(contact["internal_endpoint_node_id"])
            and "incident_opposite_endpoint_mismatch" not in issues
            else "incomplete"
        )
        terminal_contacts.append(contact)

    components: list[dict[str, Any]] = []
    for component in getattr(reachability, "surface_components", ()):
        net = str(getattr(component, "net", "")).strip()
        layers = sorted(
            {str(item).strip() for item in getattr(component, "layers", ()) if str(item).strip()},
            key=str.casefold,
        )
        if not net or len(layers) < 2:
            continue
        identity = core_services._canonical_metadata_sha256(
            {
                "source_sha256": source_sha256.casefold(),
                "net": net.casefold(),
                "layers": [item.casefold() for item in layers],
            }
        )
        components.append(
            {
                "component_id": f"spd-surface-component:{identity[:24]}",
                "net": net,
                "layers": layers,
            }
        )
    components = sorted(
        {
            (
                item["net"].casefold(),
                tuple(layer.casefold() for layer in item["layers"]),
            ): item
            for item in components
        }.values(),
        key=lambda item: (
            item["net"].casefold(),
            tuple(layer.casefold() for layer in item["layers"]),
        ),
    )
    proof_contacted_islands_by_surface = {
        (
            str(getattr(item, "net", "")).strip().casefold(),
            str(getattr(item, "layer", "")).strip().casefold(),
        ): frozenset(
            str(value).strip()
            for value in getattr(item, "contacted_island_ids", ())
            if str(value).strip()
        )
        for item in getattr(reachability, "surface_equivalence_proofs", ())
    }
    surface_equivalence_components = [
        {
            "net": str(getattr(item, "net", "")).strip(),
            "layer": str(getattr(item, "layer", "")).strip(),
            "island_ids": list(getattr(item, "island_ids", ())),
        }
        for item in getattr(
            reachability, "surface_equivalence_components", ()
        )
    ]
    surface_equivalence_components.sort(
        key=lambda item: (
            item["net"].casefold(),
            item["layer"].casefold(),
            tuple(item["island_ids"]),
            item["net"],
            item["layer"],
        )
    )
    surface_component_by_island: dict[
        tuple[str, str, str], dict[str, Any]
    ] = {}
    surface_component_by_id: dict[str, dict[str, Any]] = {}
    surface_component_by_identity: dict[
        tuple[str, str, tuple[str, ...]], dict[str, Any]
    ] = {}
    for item in surface_equivalence_components:
        item["island_ids"] = sorted(str(value) for value in item["island_ids"])
        identity_payload = {
            "source_sha256": source_sha256.casefold(),
            "net": item["net"].casefold(),
            "layer": item["layer"].casefold(),
            "island_ids": item["island_ids"],
        }
        evidence_sha256 = core_services._canonical_metadata_sha256(
            identity_payload
        )
        item["component_id"] = (
            f"spd-surface-equivalence-component:{evidence_sha256[:24]}"
        )
        item["representative_island_id"] = item["island_ids"][0]
        item["component_evidence_sha256"] = evidence_sha256
        contacted_islands = proof_contacted_islands_by_surface.get(
            (item["net"].casefold(), item["layer"].casefold()), frozenset()
        )
        item["contact_status"] = (
            "complete"
            if set(item["island_ids"]) <= contacted_islands
            else "uncontacted"
        )
        surface_component_by_id[item["component_id"]] = item
        identity = (
            item["net"].casefold(),
            item["layer"].casefold(),
            tuple(item["island_ids"]),
        )
        surface_component_by_identity[identity] = item
        for island_id in item["island_ids"]:
            surface_component_by_island[
                (
                    item["net"].casefold(),
                    item["layer"].casefold(),
                    island_id,
                )
            ] = item
    finite_via_vertices = []
    finite_vertex_by_id: dict[str, dict[str, Any]] = {}
    for raw_vertex in getattr(reachability, "finite_via_vertices", ()):
        retained_rows = dict(
            getattr(
                raw_vertex,
                "retained_component_island_ids_by_layer",
                {},
            )
        )
        retained_component_ids: list[str] = []
        retained_component_evidence_sha256s: list[str] = []
        retained_binding_issues: list[str] = []
        for layer, island_ids in sorted(
            retained_rows.items(), key=lambda row: str(row[0]).casefold()
        ):
            component = surface_component_by_identity.get(
                (
                    str(getattr(raw_vertex, "net", "")).casefold(),
                    str(layer).casefold(),
                    tuple(sorted(str(value) for value in island_ids)),
                )
            )
            if component is None:
                retained_binding_issues.append(
                    f"retained_component_unresolved:{layer}"
                )
            elif component["contact_status"] != "complete":
                retained_binding_issues.append(
                    f"retained_component_uncontacted:{layer}"
                )
            else:
                retained_component_ids.append(component["component_id"])
                retained_component_evidence_sha256s.append(
                    component["component_evidence_sha256"]
                )
        roles = list(getattr(raw_vertex, "roles", ()))
        vertex = {
            "vertex_id": str(getattr(raw_vertex, "vertex_id", "")).strip(),
            "net": str(getattr(raw_vertex, "net", "")).strip(),
            "layer": str(getattr(raw_vertex, "layer", "")).strip(),
            "representative_node_id": str(
                getattr(raw_vertex, "representative_node_id", "")
            ).strip(),
            "source_node_count": int(
                getattr(raw_vertex, "source_node_count", 0)
            ),
            "source_node_ids_sha256": str(
                getattr(raw_vertex, "source_node_ids_sha256", "")
            ).casefold(),
            "roles": roles,
            "terminal_ids": list(getattr(raw_vertex, "terminal_ids", ())),
            "retained_component_ids": retained_component_ids,
            "retained_component_evidence_sha256s": (
                retained_component_evidence_sha256s
            ),
            "retained_component_island_ids_by_layer": {
                str(layer): list(island_ids)
                for layer, island_ids in sorted(
                    retained_rows.items(),
                    key=lambda row: str(row[0]).casefold(),
                )
            },
            "component_binding_status": (
                "complete" if not retained_binding_issues else "incomplete"
            ),
            "component_binding_issues": retained_binding_issues,
        }
        finite_via_vertices.append(vertex)
        finite_vertex_by_id[vertex["vertex_id"]] = vertex
    finite_via_vertices.sort(key=lambda item: item["vertex_id"])
    finite_vertex_ids_by_component_id: dict[str, list[str]] = {}
    for vertex in finite_via_vertices:
        for component_id in vertex["retained_component_ids"]:
            finite_vertex_ids_by_component_id.setdefault(
                component_id, []
            ).append(vertex["vertex_id"])
    for vertex_ids in finite_vertex_ids_by_component_id.values():
        vertex_ids.sort()

    finite_via_edges = []
    for raw_edge in getattr(reachability, "finite_via_edges", ()):
        terms = []
        edge_physical_issues = set(
            str(item)
            for item in getattr(raw_edge, "physical_model_issues", ())
            if str(item)
        )
        for raw_term in getattr(raw_edge, "series_terms", ()):
            segments = [
                {
                    "ordinal": int(getattr(segment, "ordinal")),
                    "start_layer": str(
                        getattr(segment, "start_layer", "")
                    ).strip(),
                    "end_layer": str(
                        getattr(segment, "end_layer", "")
                    ).strip(),
                    "length_um": float(getattr(segment, "length_um")),
                }
                for segment in getattr(raw_term, "segments", ())
            ]
            term_issues = set(
                str(item)
                for item in getattr(
                    raw_term, "physical_model_issues", ()
                )
                if str(item)
            )
            term_issues.update(
                segment_stackup_issues(
                    segments,
                    expected_start_layer=getattr(
                        raw_term, "start_layer", None
                    ),
                    expected_end_layer=getattr(raw_term, "end_layer", None),
                )
            )
            edge_physical_issues.update(term_issues)
            terms.append(
                {
                    "ordinal": int(getattr(raw_term, "ordinal")),
                    "count": int(getattr(raw_term, "count")),
                    "padstack": str(
                        getattr(raw_term, "padstack", "")
                    ).strip(),
                    "start_layer": str(
                        getattr(raw_term, "start_layer", "")
                    ).strip(),
                    "end_layer": str(
                        getattr(raw_term, "end_layer", "")
                    ).strip(),
                    "drill_diameter_um": getattr(
                        raw_term, "drill_diameter_um", None
                    ),
                    "material": getattr(raw_term, "material", None),
                    "segments": segments,
                    "resistance_ohm": getattr(
                        raw_term, "resistance_ohm", None
                    ),
                    "inductance_h": getattr(
                        raw_term, "inductance_h", None
                    ),
                    "length_um": getattr(raw_term, "length_um", None),
                    "physical_model_status": str(
                        getattr(
                            raw_term,
                            "physical_model_status",
                            "incomplete",
                        )
                    ).casefold(),
                    "physical_model_issues": sorted(term_issues),
                }
            )
        start_vertex_id = str(
            getattr(raw_edge, "start_vertex_id", "")
        ).strip()
        end_vertex_id = str(
            getattr(raw_edge, "end_vertex_id", "")
        ).strip()
        endpoint_issues = []
        start_vertex = finite_vertex_by_id.get(start_vertex_id)
        end_vertex = finite_vertex_by_id.get(end_vertex_id)
        edge_net = str(getattr(raw_edge, "net", "")).strip()
        if start_vertex is None:
            endpoint_issues.append("start_vertex_unresolved")
        elif start_vertex["net"].casefold() != edge_net.casefold():
            endpoint_issues.append("start_vertex_net_mismatch")
        if end_vertex is None:
            endpoint_issues.append("end_vertex_unresolved")
        elif end_vertex["net"].casefold() != edge_net.casefold():
            endpoint_issues.append("end_vertex_net_mismatch")
        via_digest = str(
            getattr(raw_edge, "raw_via_ids_sha256", "")
        ).casefold()
        raw_via_count = int(getattr(raw_edge, "raw_via_count", 0))
        owner_ids = list(getattr(raw_edge, "owner_ids", ()))
        edge = {
            "edge_id": str(getattr(raw_edge, "edge_id", "")).strip(),
            "net": edge_net,
            "start_vertex_id": start_vertex_id,
            "end_vertex_id": end_vertex_id,
            "parallel_path_count": int(
                getattr(raw_edge, "parallel_path_count", 1)
            ),
            "per_path_via_count": int(
                getattr(raw_edge, "per_path_via_count", 0)
            ),
            "raw_via_count": raw_via_count,
            "raw_via_ids_sha256": via_digest,
            "owner_ids": owner_ids,
            "raw_owner_token": (
                f"spd-raw-via-set:{via_digest}:{raw_via_count}"
            ),
            "mode": str(getattr(raw_edge, "mode", "")).casefold(),
            "series_terms": terms,
            "resistance_ohm": getattr(raw_edge, "resistance_ohm", None),
            "inductance_h": getattr(raw_edge, "inductance_h", None),
            "length_um": getattr(raw_edge, "length_um", None),
            "endpoint_binding_status": (
                "complete" if not endpoint_issues else "incomplete"
            ),
            "endpoint_binding_issues": endpoint_issues,
            "physical_model_status": (
                "complete"
                if str(
                    getattr(raw_edge, "physical_model_status", "")
                ).casefold()
                == "complete"
                and not edge_physical_issues
                else "incomplete"
            ),
            "physical_model_issues": sorted(edge_physical_issues),
        }
        edge["status"] = (
            "complete"
            if edge["endpoint_binding_status"] == "complete"
            and edge["physical_model_status"] == "complete"
            else "incomplete"
        )
        finite_via_edges.append(edge)
    finite_via_edges.sort(key=lambda item: item["edge_id"])
    finite_edge_by_id = {
        item["edge_id"]: item for item in finite_via_edges
    }
    finite_owner_assignments = [
        (str(owner_id), item["edge_id"])
        for item in finite_via_edges
        for owner_id in item["owner_ids"]
    ]
    finite_owner_keys = [
        owner_id.casefold() for owner_id, _edge_id in finite_owner_assignments
    ]
    finite_owner_assignment_hash = sha256()
    for owner_id, edge_id in sorted(
        finite_owner_assignments,
        key=lambda row: (row[0].casefold(), row[1].casefold(), row),
    ):
        for token in (owner_id.casefold(), edge_id.casefold()):
            encoded = token.encode("utf-8")
            finite_owner_assignment_hash.update(
                len(encoded).to_bytes(4, "big")
            )
            finite_owner_assignment_hash.update(encoded)
    finite_owner_canonical_hash = sha256()
    for owner_id in sorted(
        (owner_id for owner_id, _edge_id in finite_owner_assignments),
        key=lambda value: (value.casefold(), value),
    ):
        encoded = owner_id.casefold().encode("utf-8")
        finite_owner_canonical_hash.update(len(encoded).to_bytes(4, "big"))
        finite_owner_canonical_hash.update(encoded)
    raw_finite_coverage = getattr(
        reachability, "finite_via_coverage", None
    )
    finite_owner_ledger_complete = bool(
        raw_finite_coverage is not None
        and len(finite_owner_keys)
        == int(getattr(raw_finite_coverage, "modeled_global_via_count"))
        and len(set(finite_owner_keys)) == len(finite_owner_keys)
        and finite_owner_canonical_hash.hexdigest()
        == str(
            getattr(
                raw_finite_coverage,
                "modeled_owner_canonical_sha256",
                "",
            )
        ).casefold()
    )
    finite_via_coverage = (
        {
            "raw_target_via_count": int(
                getattr(raw_finite_coverage, "raw_target_via_count")
            ),
            "modeled_global_via_count": int(
                getattr(raw_finite_coverage, "modeled_global_via_count")
            ),
            "pruned_dangling_via_count": int(
                getattr(raw_finite_coverage, "pruned_dangling_via_count")
            ),
            "outside_scope_via_count": int(
                getattr(raw_finite_coverage, "outside_scope_via_count")
            ),
            "physical_complete_via_count": int(
                getattr(raw_finite_coverage, "physical_complete_via_count")
            ),
            "physical_incomplete_via_count": int(
                getattr(raw_finite_coverage, "physical_incomplete_via_count")
            ),
            "raw_target_via_ids_sha256": str(
                getattr(raw_finite_coverage, "raw_target_via_ids_sha256")
            ).casefold(),
            "modeled_global_via_ids_sha256": str(
                getattr(
                    raw_finite_coverage,
                    "modeled_global_via_ids_sha256",
                )
            ).casefold(),
            "modeled_owner_ledger_sha256": str(
                getattr(
                    raw_finite_coverage,
                    "modeled_owner_ledger_sha256",
                )
            ).casefold(),
            "modeled_owner_canonical_sha256": str(
                getattr(
                    raw_finite_coverage,
                    "modeled_owner_canonical_sha256",
                )
            ).casefold(),
            "pruned_dangling_via_ids_sha256": str(
                getattr(
                    raw_finite_coverage,
                    "outside_scope_via_ids_sha256",
                )
            ).casefold(),
            "terminal_exclusive_via_count": int(
                getattr(
                    raw_finite_coverage,
                    "terminal_exclusive_via_count",
                    0,
                )
            ),
            "modeled_owner_count": len(finite_owner_keys),
            "modeled_owner_unique_count": len(set(finite_owner_keys)),
            "modeled_owner_edge_assignment_sha256": (
                finite_owner_assignment_hash.hexdigest()
            ),
            "owner_ledger_status": (
                "complete" if finite_owner_ledger_complete else "incomplete"
            ),
            "owner_policy": "global_mna_with_scenario_retarget_cut_suppression",
            "status": str(getattr(raw_finite_coverage, "status")),
        }
        if raw_finite_coverage is not None
        else None
    )
    raw_scenario_isolation_coverage = getattr(
        reachability,
        "finite_via_scenario_isolation_coverage",
        None,
    )
    scenario_isolated_landing_keys = {
        (str(raw_key[0]).casefold(), str(raw_key[1]).casefold())
        for raw_key in getattr(
            reachability,
            "finite_via_scenario_isolated_landing_keys",
            (),
        )
        if len(raw_key) == 2
    }
    finite_via_scenario_isolation_coverage = (
        {
            "requested_landing_count": int(
                getattr(
                    raw_scenario_isolation_coverage,
                    "requested_landing_count",
                )
            ),
            "isolated_landing_count": int(
                getattr(
                    raw_scenario_isolation_coverage,
                    "isolated_landing_count",
                )
            ),
            "isolated_node_count": int(
                getattr(
                    raw_scenario_isolation_coverage,
                    "isolated_node_count",
                )
            ),
            "suppressed_artwork_contact_count": int(
                getattr(
                    raw_scenario_isolation_coverage,
                    "suppressed_artwork_contact_count",
                )
            ),
            "suppressed_trace_edge_count": int(
                getattr(
                    raw_scenario_isolation_coverage,
                    "suppressed_trace_edge_count",
                )
            ),
            "requested_landing_ids_sha256": str(
                getattr(
                    raw_scenario_isolation_coverage,
                    "requested_landing_ids_sha256",
                )
            ).casefold(),
            "isolated_landing_ids_sha256": str(
                getattr(
                    raw_scenario_isolation_coverage,
                    "isolated_landing_ids_sha256",
                )
            ).casefold(),
            "same_layer_base_policy": (
                "editable_decap_landing_nodes_excluded_from_permanent_"
                "trace_and_artwork_union"
            ),
            "status": str(
                getattr(raw_scenario_isolation_coverage, "status")
            ).casefold(),
        }
        if raw_scenario_isolation_coverage is not None
        else None
    )
    raw_retarget_destination_vertex_by_key = {
        tuple(str(item).casefold() for item in raw_key): str(vertex_id).strip()
        for raw_key, vertex_id in dict(
            getattr(
                reachability,
                "finite_via_vertex_id_by_retarget_destination",
                {},
            )
        ).items()
        if len(raw_key) == 3 and str(vertex_id).strip()
    }
    raw_retarget_destination_coverage = getattr(
        reachability,
        "finite_via_retarget_destination_coverage",
        None,
    )
    finite_via_retarget_destination_coverage = (
        {
            "requested_destination_count": int(
                getattr(
                    raw_retarget_destination_coverage,
                    "requested_destination_count",
                )
            ),
            "resolved_destination_count": int(
                getattr(
                    raw_retarget_destination_coverage,
                    "resolved_destination_count",
                )
            ),
            "requested_destination_ids_sha256": str(
                getattr(
                    raw_retarget_destination_coverage,
                    "requested_destination_ids_sha256",
                )
            ).casefold(),
            "resolved_destination_ids_sha256": str(
                getattr(
                    raw_retarget_destination_coverage,
                    "resolved_destination_ids_sha256",
                )
            ).casefold(),
            "status": str(
                getattr(raw_retarget_destination_coverage, "status")
            ).casefold(),
        }
        if raw_retarget_destination_coverage is not None
        else None
    )
    landing_surface_contacts = [
        {
            "via_id": str(getattr(item, "via_id", "")).strip(),
            "endpoint_node_id": str(
                getattr(item, "endpoint_node_id", "")
            ).strip(),
            "external_endpoint_node_id": str(
                getattr(item, "endpoint_node_id", "")
            ).strip(),
            "landing_key": [
                str(getattr(item, "via_id", "")).strip().casefold(),
                str(getattr(item, "endpoint_node_id", "")).strip().casefold(),
            ],
            "exposed_quotient_vertex_id": raw_finite_vertex_by_landing.get(
                (
                    str(getattr(item, "via_id", "")).strip().casefold(),
                    str(getattr(item, "endpoint_node_id", ""))
                    .strip()
                    .casefold(),
                )
            ),
            "first_via_quotient_edge_id": raw_finite_edge_by_landing.get(
                (
                    str(getattr(item, "via_id", "")).strip().casefold(),
                    str(getattr(item, "endpoint_node_id", ""))
                    .strip()
                    .casefold(),
                )
            ),
            "internal_endpoint_node_id": getattr(
                item, "internal_endpoint_node_id", None
            ),
            "terminal_owner_kind": str(
                getattr(item, "terminal_owner_kind", "unknown")
            ).strip().casefold(),
            "external_endpoint_layer": getattr(
                item, "external_endpoint_layer", None
            ),
            "padstack": getattr(item, "padstack", None),
            "drill_diameter_um": getattr(
                item, "drill_diameter_um", None
            ),
            "material": getattr(item, "material", None),
            "segments": [
                {
                    "ordinal": int(getattr(segment, "ordinal")),
                    "start_layer": str(
                        getattr(segment, "start_layer", "")
                    ).strip(),
                    "end_layer": str(
                        getattr(segment, "end_layer", "")
                    ).strip(),
                    "length_um": float(getattr(segment, "length_um")),
                }
                for segment in getattr(item, "segments", ())
            ],
            "physical_model_status": str(
                getattr(item, "physical_model_status", "incomplete")
            ).strip().casefold(),
            "physical_model_issues": list(
                getattr(item, "physical_model_issues", ())
            ),
            "net": str(getattr(item, "net", "")).strip(),
            "contact_island_ids_by_layer": {
                str(layer): list(island_ids)
                for layer, island_ids in sorted(
                    dict(
                        getattr(item, "contact_island_ids_by_layer", {})
                    ).items(),
                    key=lambda row: (
                        str(row[0]).casefold(),
                        str(row[0]),
                    ),
                )
            },
            "endpoint_layer": getattr(item, "endpoint_layer", None),
            "endpoint_island_id": getattr(
                item, "endpoint_island_id", None
            ),
        }
        for item in raw_landing_surface_contacts
    ]
    landing_surface_contacts.sort(
        key=lambda item: (
            item["via_id"].casefold(),
            item["endpoint_node_id"].casefold(),
            item["net"].casefold(),
            item["via_id"],
            item["endpoint_node_id"],
            item["net"],
        )
    )
    for item in landing_surface_contacts:
        contact_rows = item["contact_island_ids_by_layer"]
        component = None
        if len(contact_rows) == 1:
            component_layer = next(iter(contact_rows))
            component_island_ids = tuple(
                sorted(next(iter(contact_rows.values())))
            )
            component = surface_component_by_identity.get(
                (
                    item["net"].casefold(),
                    component_layer.casefold(),
                    component_island_ids,
                )
            )
        resolved_component = (
            component
            if component is not None
            and component["contact_status"] == "complete"
            else None
        )
        item["contact_path_kind"] = "direct_via_landing"
        item["endpoint_resolution_kind"] = (
            "same_layer_trace_artwork_component"
            if resolved_component is not None
            else "unresolved"
        )
        item["contact_component_id"] = (
            resolved_component["component_id"]
            if resolved_component is not None
            else None
        )
        item["component_layer"] = (
            resolved_component["layer"]
            if resolved_component is not None
            else None
        )
        item["component_island_ids"] = (
            list(resolved_component["island_ids"])
            if resolved_component is not None
            else []
        )
        item["representative_island_id"] = (
            resolved_component["representative_island_id"]
            if resolved_component is not None
            else None
        )
        item["component_evidence_sha256"] = (
            resolved_component["component_evidence_sha256"]
            if resolved_component is not None
            else None
        )
        item["candidate_component_ids"] = (
            [component["component_id"]] if component is not None else []
        )
        item["component_binding_status"] = (
            "complete" if resolved_component is not None else "incomplete"
        )
        item["component_binding_issues"] = (
            []
            if resolved_component is not None
            else [
                "contact_component_uncontacted"
                if component is not None
                else "contact_component_unresolved"
            ]
        )
        item["endpoint_layer"] = item["component_layer"]
        item["endpoint_island_id"] = item["representative_island_id"]
        physical_issues = set(item["physical_model_issues"])
        physical_issues.update(
            segment_stackup_issues(
                item["segments"],
                expected_start_layer=item["external_endpoint_layer"],
                expected_end_layer=item["component_layer"],
            )
        )
        item["physical_model_issues"] = sorted(physical_issues)
        if physical_issues:
            item["physical_model_status"] = "incomplete"
        item["status"] = (
            "complete"
            if resolved_component is not None
            and bool(item["internal_endpoint_node_id"])
            and item["physical_model_status"] == "complete"
            else "incomplete"
        )
        exposed_vertex = finite_vertex_by_id.get(
            str(item.get("exposed_quotient_vertex_id") or "")
        )
        first_via_edge = finite_edge_by_id.get(
            str(item.get("first_via_quotient_edge_id") or "")
        )
        quotient_issues: list[str] = []
        if exposed_vertex is None:
            quotient_issues.append("exposed_quotient_vertex_unresolved")
        elif exposed_vertex["net"].casefold() != item["net"].casefold():
            quotient_issues.append("exposed_quotient_vertex_net_mismatch")
        if not str(item["via_id"]).startswith("source-node:"):
            expected_owner_id = f"via:{item['via_id']}"
            if first_via_edge is None:
                quotient_issues.append("first_via_global_edge_unresolved")
            elif item["exposed_quotient_vertex_id"] not in {
                first_via_edge["start_vertex_id"],
                first_via_edge["end_vertex_id"],
            }:
                quotient_issues.append("first_via_global_edge_not_incident")
            elif expected_owner_id.casefold() not in {
                str(owner_id).casefold()
                for owner_id in first_via_edge["owner_ids"]
            }:
                quotient_issues.append("first_via_raw_owner_unresolved")
        else:
            expected_owner_id = None
        item["first_via_owner_id"] = expected_owner_id
        item["scenario_isolated_base_contact"] = (
            tuple(item["landing_key"]) in scenario_isolated_landing_keys
        )
        if (
            item["terminal_owner_kind"] == "decap"
            and scenario_topology_requested
            and not item["scenario_isolated_base_contact"]
        ):
            quotient_issues.append("scenario_base_contact_not_isolated")
        item["global_quotient_binding_status"] = (
            "complete" if not quotient_issues else "incomplete"
        )
        item["global_quotient_binding_issues"] = quotient_issues
        item["retarget_cut_status"] = (
            "complete"
            if item["terminal_owner_kind"] == "decap"
            and first_via_edge is not None
            and first_via_edge["mode"] == "retained_explicit"
            and first_via_edge["raw_via_count"] == 1
            and [
                str(owner_id).casefold()
                for owner_id in first_via_edge["owner_ids"]
            ]
            == [str(expected_owner_id).casefold()]
            else "not_applicable"
            if item["terminal_owner_kind"] != "decap"
            else "incomplete"
        )
    finite_via_terminal_bindings = [
        {
            "landing_key": list(item["landing_key"]),
            "via_id": item["via_id"],
            "external_endpoint_node_id": item["external_endpoint_node_id"],
            "net": item["net"],
            "terminal_owner_kind": item["terminal_owner_kind"],
            "exposed_quotient_vertex_id": item[
                "exposed_quotient_vertex_id"
            ],
            "first_via_quotient_edge_id": item[
                "first_via_quotient_edge_id"
            ],
            "first_via_owner_id": item["first_via_owner_id"],
            "global_quotient_binding_status": item[
                "global_quotient_binding_status"
            ],
            "global_quotient_binding_issues": list(
                item["global_quotient_binding_issues"]
            ),
            "retarget_cut_status": item["retarget_cut_status"],
            "scenario_isolated_base_contact": item[
                "scenario_isolated_base_contact"
            ],
            "source_route_owner": "global_mna",
        }
        for item in landing_surface_contacts
    ]
    finite_via_terminal_bindings.sort(
        key=lambda item: tuple(item["landing_key"])
    )

    def scenario_topology_id(prefix: str, *tokens: object) -> str:
        digest = sha256()
        for token in (source_sha256.casefold(), *tokens):
            encoded = str(token).strip().casefold().encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
        return f"{prefix}:{digest.hexdigest()[:24]}"

    def enum_text(value: object) -> str:
        return str(getattr(value, "value", value)).strip().upper()

    connection_by_refdes: dict[str, Any] = {}
    for connection in decap_connections:
        refdes = str(getattr(connection, "refdes", "")).strip()
        key = refdes.casefold()
        if not refdes or key in connection_by_refdes:
            raise SpdImportError(
                "SPD_SCENARIO_TERMINAL_TOPOLOGY_CONNECTION_ID_INVALID: "
                "decap connection REFDES values must be nonblank and unique"
            )
        connection_by_refdes[key] = connection
    participating_cluster_keys = {
        str(getattr(connection, "cluster_id", "") or "").strip().casefold()
        for connection in connection_by_refdes.values()
        if str(getattr(connection, "cluster_id", "") or "").strip()
    }
    cluster_by_id: dict[str, Any] = {}
    for cluster in shared_pad_clusters:
        cluster_id = str(getattr(cluster, "cluster_id", "")).strip()
        key = cluster_id.casefold()
        if not cluster_id:
            raise SpdImportError(
                "SPD_SCENARIO_TERMINAL_TOPOLOGY_CLUSTER_ID_INVALID: shared-pad "
                "cluster IDs must be nonblank and unique"
            )
        if key not in participating_cluster_keys:
            # UNRESOLVED/FLOATING clusters have no scenario-editable terminal
            # vertices.  Their raw connectivity remains in the immutable base
            # graph and must not create dangling synthetic shared links.
            continue
        if key in cluster_by_id:
            raise SpdImportError(
                "SPD_SCENARIO_TERMINAL_TOPOLOGY_CLUSTER_ID_INVALID: shared-pad "
                "cluster IDs must be nonblank and unique"
            )
        cluster_by_id[key] = cluster

    scenario_terminal_vertices: list[dict[str, Any]] = []
    scenario_terminal_vertex_by_ref_role: dict[tuple[str, str], str] = {}
    scenario_conditional_contacts: list[dict[str, Any]] = []
    scenario_contacts_by_ref_role: dict[
        tuple[str, str], list[dict[str, Any]]
    ] = {}
    scenario_topology_issues: set[str] = set()
    for refdes_key, connection in sorted(connection_by_refdes.items()):
        refdes = str(getattr(connection, "refdes", "")).strip()
        connection_kind = enum_text(getattr(connection, "kind", ""))
        cluster_id = str(getattr(connection, "cluster_id", "") or "").strip()
        cluster = cluster_by_id.get(cluster_id.casefold()) if cluster_id else None
        if connection_kind == "OUT_OF_SCOPE":
            continue
        for role, raw_landings in (
            ("power", tuple(getattr(connection, "power_vias", ()))),
            ("ground", tuple(getattr(connection, "ground_vias", ()))),
        ):
            net_by_key = {
                str(getattr(landing, "net", "")).strip().casefold(): str(
                    getattr(landing, "net", "")
                ).strip()
                for landing in raw_landings
                if str(getattr(landing, "net", "")).strip()
            }
            if not net_by_key and cluster is not None:
                cluster_net = str(
                    getattr(
                        cluster,
                        "power_net" if role == "power" else "ground_net",
                        "",
                    )
                ).strip()
                if cluster_net:
                    net_by_key[cluster_net.casefold()] = cluster_net
            terminal_issues: list[str] = []
            if len(net_by_key) != 1:
                terminal_issues.append("terminal_net_unresolved_or_ambiguous")
                terminal_net = ""
            else:
                terminal_net = net_by_key[next(iter(sorted(net_by_key)))]
            if connection_kind in {"UNRESOLVED", "FLOATING_DUMMY"}:
                terminal_issues.append(
                    f"connection_kind_{connection_kind.casefold()}"
                )
            terminal_vertex_id = scenario_topology_id(
                "spd-decap-terminal",
                refdes,
                role,
            )
            scenario_terminal_vertex_by_ref_role[
                (refdes_key, role)
            ] = terminal_vertex_id
            scenario_terminal_vertices.append(
                {
                    "terminal_vertex_id": terminal_vertex_id,
                    "refdes": refdes,
                    "role": role,
                    "net": terminal_net,
                    "connection_kind": connection_kind,
                    "cluster_id": cluster_id or None,
                    "body_stamp_policy": "cap_body_between_synthetic_terminals",
                    "normal_disabled_policy": (
                        "retain_topology_contacts_omit_cap_body"
                    ),
                    "status": "complete" if not terminal_issues else "incomplete",
                    "issues": terminal_issues,
                }
            )
            scenario_topology_issues.update(terminal_issues)
            contact_bucket = scenario_contacts_by_ref_role.setdefault(
                (refdes_key, role), []
            )
            for landing in raw_landings:
                via_id = str(getattr(landing, "via_id", "")).strip()
                node_id = str(
                    getattr(landing, "endpoint_node_id", "")
                ).strip()
                landing_key = (via_id.casefold(), node_id.casefold())
                exposed_vertex_id = raw_finite_vertex_by_landing.get(landing_key)
                first_edge_id = raw_finite_edge_by_landing.get(landing_key)
                first_edge = finite_edge_by_id.get(str(first_edge_id or ""))
                expected_owner_id = f"via:{via_id}"
                contact_issues: list[str] = []
                if exposed_vertex_id is None:
                    contact_issues.append("exposed_quotient_vertex_unresolved")
                if first_edge is None:
                    contact_issues.append("first_via_global_edge_unresolved")
                elif [
                    str(owner_id).casefold()
                    for owner_id in first_edge["owner_ids"]
                ] != [expected_owner_id.casefold()]:
                    contact_issues.append("first_via_owner_not_exclusive")
                if landing_key not in scenario_isolated_landing_keys:
                    contact_issues.append("raw_same_layer_contact_not_isolated")
                contact_id = scenario_topology_id(
                    "spd-decap-contact",
                    refdes,
                    role,
                    via_id,
                    node_id,
                )
                contact = {
                    "contact_id": contact_id,
                    "terminal_vertex_id": terminal_vertex_id,
                    "refdes": refdes,
                    "role": role,
                    "net": str(getattr(landing, "net", "")).strip(),
                    "landing_key": [landing_key[0], landing_key[1]],
                    "landing_vertex_id": exposed_vertex_id,
                    "first_via_edge_id": first_edge_id,
                    "first_via_owner_id": expected_owner_id,
                    "link_kind": "topology_only_ideal",
                    "default_state": "enabled",
                    "disable_on": (
                        ["moved", "isolation_gap"]
                        if role == "power"
                        else ["isolation_gap"]
                    ),
                    "moved_retarget_policy": (
                        "disable_source_contact_then_add_each_eligible_"
                        "destination_finite_route"
                        if role == "power"
                        else "retain_source_contact"
                    ),
                    "raw_same_layer_bypass_status": (
                        "blocked"
                        if landing_key in scenario_isolated_landing_keys
                        else "unresolved"
                    ),
                    "status": "complete" if not contact_issues else "incomplete",
                    "issues": contact_issues,
                }
                scenario_conditional_contacts.append(contact)
                contact_bucket.append(contact)
                scenario_topology_issues.update(contact_issues)

    retarget_destination_bindings: list[dict[str, Any]] = []
    retarget_binding_identity_keys: set[
        tuple[str, str, str, str, str, str]
    ] = set()
    retarget_expected_destination_keys: set[tuple[str, str, str]] = set()
    for refdes_key, connection in sorted(connection_by_refdes.items()):
        refdes = str(getattr(connection, "refdes", "")).strip()
        for role, raw_landings in (
            ("power", tuple(getattr(connection, "power_vias", ()))),
            ("ground", tuple(getattr(connection, "ground_vias", ()))),
        ):
            for landing in raw_landings:
                via_id = str(getattr(landing, "via_id", "")).strip()
                node_id = str(
                    getattr(landing, "endpoint_node_id", "")
                ).strip()
                net = str(getattr(landing, "net", "")).strip()
                source_landing_key = (
                    via_id.casefold(),
                    node_id.casefold(),
                )
                for evidence in tuple(getattr(landing, "path_evidence", ())):
                    target_layer = str(
                        getattr(evidence, "target_layer", "")
                    ).strip()
                    target_node_id = str(
                        getattr(evidence, "target_node_id", "")
                    ).strip()
                    destination_key = (
                        net.casefold(),
                        target_layer.casefold(),
                        target_node_id.casefold(),
                    )
                    identity_key = (
                        refdes_key,
                        role,
                        source_landing_key[0],
                        source_landing_key[1],
                        destination_key[1],
                        destination_key[2],
                    )
                    if identity_key in retarget_binding_identity_keys:
                        raise SpdImportError(
                            "SPD_RETARGET_DESTINATION_BINDING_DUPLICATE: one "
                            "source landing/path target is repeated"
                        )
                    retarget_binding_identity_keys.add(identity_key)
                    retarget_expected_destination_keys.add(destination_key)
                    destination_vertex_id = (
                        raw_retarget_destination_vertex_by_key.get(
                            destination_key
                        )
                    )
                    destination_vertex = finite_vertex_by_id.get(
                        str(destination_vertex_id or "")
                    )
                    binding_issues: list[str] = []
                    if not all(destination_key):
                        binding_issues.append("destination_identity_invalid")
                    if destination_vertex is None:
                        binding_issues.append(
                            "destination_quotient_vertex_unresolved"
                        )
                        destination_island_ids: list[str] = []
                        destination_component = None
                    else:
                        if destination_vertex["net"].casefold() != net.casefold():
                            binding_issues.append("destination_vertex_net_mismatch")
                        if (
                            destination_vertex["layer"].casefold()
                            != target_layer.casefold()
                        ):
                            binding_issues.append(
                                "destination_vertex_layer_mismatch"
                            )
                        retained_rows = destination_vertex[
                            "retained_component_island_ids_by_layer"
                        ]
                        matching_layers = [
                            layer
                            for layer in retained_rows
                            if layer.casefold() == target_layer.casefold()
                        ]
                        destination_island_ids = (
                            sorted(retained_rows[matching_layers[0]])
                            if len(matching_layers) == 1
                            else []
                        )
                        destination_component = (
                            surface_component_by_identity.get(
                                (
                                    net.casefold(),
                                    target_layer.casefold(),
                                    tuple(destination_island_ids),
                                )
                            )
                            if destination_island_ids
                            else None
                        )
                        if len(matching_layers) != 1:
                            binding_issues.append(
                                "destination_retained_layer_binding_unresolved"
                            )
                        if destination_component is None:
                            binding_issues.append(
                                "destination_surface_component_unresolved"
                            )
                        elif destination_component["contact_status"] != "complete":
                            binding_issues.append(
                                "destination_surface_component_uncontacted"
                            )
                    row = {
                        "refdes": refdes,
                        "role": role,
                        "net": net,
                        "source_landing_key": [
                            source_landing_key[0],
                            source_landing_key[1],
                        ],
                        "source_landing_vertex_id": (
                            raw_finite_vertex_by_landing.get(
                                source_landing_key
                            )
                        ),
                        "source_first_via_edge_id": (
                            raw_finite_edge_by_landing.get(source_landing_key)
                        ),
                        "source_first_via_owner_id": f"via:{via_id}",
                        "target_layer": target_layer,
                        "target_node_id": target_node_id,
                        "destination_vertex_id": destination_vertex_id,
                        "destination_component_id": (
                            destination_component["component_id"]
                            if destination_component is not None
                            else None
                        ),
                        "destination_island_ids": destination_island_ids,
                        "destination_representative_island_id": (
                            destination_component["representative_island_id"]
                            if destination_component is not None
                            else None
                        ),
                        "destination_component_evidence_sha256": (
                            destination_component["component_evidence_sha256"]
                            if destination_component is not None
                            else None
                        ),
                        "status": (
                            "complete" if not binding_issues else "incomplete"
                        ),
                        "issues": binding_issues,
                    }
                    row["binding_evidence_sha256"] = (
                        core_services._canonical_metadata_sha256(row)
                    )
                    retarget_destination_bindings.append(row)
                    scenario_topology_issues.update(binding_issues)
    retarget_destination_bindings.sort(
        key=lambda item: (
            item["refdes"].casefold(),
            item["role"],
            tuple(item["source_landing_key"]),
            item["target_layer"].casefold(),
            item["target_node_id"].casefold(),
        )
    )
    retarget_binding_rows_sha256 = core_services._canonical_metadata_sha256(
        {"bindings": retarget_destination_bindings}
    )
    if finite_via_retarget_destination_coverage is None:
        retarget_destination_coverage = {
            "requested_destination_count": len(
                retarget_expected_destination_keys
            ),
            "resolved_destination_count": len(
                raw_retarget_destination_vertex_by_key
            ),
            "requested_destination_ids_sha256": None,
            "resolved_destination_ids_sha256": None,
            "binding_rows_sha256": retarget_binding_rows_sha256,
            "status": (
                "complete"
                if not retarget_expected_destination_keys
                and not raw_retarget_destination_vertex_by_key
                else "incomplete"
            ),
        }
    else:
        retarget_destination_coverage = {
            **finite_via_retarget_destination_coverage,
            "binding_rows_sha256": retarget_binding_rows_sha256,
        }
        if (
            retarget_destination_coverage["requested_destination_count"]
            != len(retarget_expected_destination_keys)
            or retarget_destination_coverage["resolved_destination_count"]
            != len(raw_retarget_destination_vertex_by_key)
            or set(raw_retarget_destination_vertex_by_key)
            != retarget_expected_destination_keys
            or any(
                item["status"] != "complete"
                for item in retarget_destination_bindings
            )
        ):
            retarget_destination_coverage["status"] = "incomplete"
            scenario_topology_issues.add(
                "retarget_destination_binding_coverage_incomplete"
            )

    retarget_landing_xy_bindings: list[dict[str, Any]] = []
    previous_landing_identity_key: (
        tuple[str, str, str, str, str, str] | None
    ) = None
    request_identity_digest = sha256()
    request_identity_digest.update(b'{"destinations":[')
    binding_rows_digest = sha256()
    binding_rows_digest.update(b'{"bindings":[')
    rail_by_key = {
        str(getattr(rail, "rail_id", "")).strip().casefold(): rail
        for rail in getattr(project, "rails", ())
        if str(getattr(rail, "rail_id", "")).strip()
    }
    total_retarget_landing_bindings = len(
        retarget_landing_destination_requests
    )
    report(
        60,
        "Normalizing exact retarget landing bindings "
        f"(0/{total_retarget_landing_bindings:,})",
    )
    report(61, "Hashing exact retarget landing bindings")
    for request_index, request in enumerate(
        retarget_landing_destination_requests
    ):
        if request_index % 2048 == 0:
            check_cancelled("normalizing exact retarget landing bindings")
            report(
                61
                + round(
                    20
                    * request_index
                    / max(1, total_retarget_landing_bindings)
                ),
                "Normalizing exact retarget landing bindings "
                f"({request_index:,}/{total_retarget_landing_bindings:,})",
            )
        refdes = str(getattr(request, "refdes", "")).strip()
        via_id = str(getattr(request, "via_id", "")).strip()
        node_id = str(
            getattr(request, "endpoint_node_id", "")
        ).strip()
        source_net = str(getattr(request, "source_net", "")).strip()
        destination_net = str(
            getattr(request, "destination_net", "")
        ).strip()
        destination_layer = str(
            getattr(request, "destination_layer", "")
        ).strip()
        destination_island_id = str(
            getattr(request, "destination_island_id", "")
        ).strip()
        geometry_asset_sha256 = str(
            getattr(request, "geometry_asset_sha256", "")
        ).strip().casefold()
        target_rail_id = str(
            getattr(request, "target_rail_id", "")
        ).strip()
        via_template_id = getattr(request, "via_template_id", None)
        if via_template_id is not None:
            via_template_id = str(via_template_id).strip() or None
        try:
            x_um = float(getattr(request, "x_um"))
            y_um = float(getattr(request, "y_um"))
        except (AttributeError, TypeError, ValueError):
            x_um = float("nan")
            y_um = float("nan")
        identity_key = (
            refdes.casefold(),
            via_id.casefold(),
            node_id.casefold(),
            target_rail_id.casefold(),
            destination_net.casefold(),
            destination_layer.casefold(),
        )
        if identity_key == previous_landing_identity_key:
            raise SpdImportError(
                "SPD_RETARGET_LANDING_BINDING_DUPLICATE: one physical PWR "
                "landing repeats a destination NET/layer"
            )
        previous_landing_identity_key = identity_key
        source_landing_key = (via_id.casefold(), node_id.casefold())
        destination_component = surface_component_by_island.get(
            (
                destination_net.casefold(),
                destination_layer.casefold(),
                destination_island_id,
            )
        )
        destination_vertex_ids = (
            finite_vertex_ids_by_component_id.get(
                destination_component["component_id"], []
            )
            if destination_component is not None
            else []
        )
        destination_vertex_id = (
            destination_vertex_ids[0]
            if len(destination_vertex_ids) == 1
            else None
        )
        destination_vertex = finite_vertex_by_id.get(
            str(destination_vertex_id or "")
        )
        source_vertex_id = raw_finite_vertex_by_landing.get(
            source_landing_key
        )
        source_first_edge_id = raw_finite_edge_by_landing.get(
            source_landing_key
        )
        source_first_edge = finite_edge_by_id.get(
            str(source_first_edge_id or "")
        )
        binding_issues: list[str] = []
        if (
            not all(
                (
                    refdes,
                    via_id,
                    node_id,
                    source_net,
                    destination_net,
                    destination_layer,
                    destination_island_id,
                    target_rail_id,
                )
            )
            or not isfinite(x_um)
            or not isfinite(y_um)
            or len(geometry_asset_sha256) != 64
        ):
            binding_issues.append("landing_xy_destination_identity_invalid")
        if refdes.casefold() not in connection_by_refdes:
            binding_issues.append("source_refdes_connection_unresolved")
        if source_vertex_id is None:
            binding_issues.append("source_landing_vertex_unresolved")
        if source_first_edge is None:
            binding_issues.append("source_first_via_edge_unresolved")
        elif [
            str(owner_id).casefold()
            for owner_id in source_first_edge["owner_ids"]
        ] != [f"via:{via_id}".casefold()]:
            binding_issues.append("source_first_via_owner_not_exclusive")
        if source_landing_key not in scenario_isolated_landing_keys:
            binding_issues.append("source_landing_not_scenario_isolated")
        target_rail = rail_by_key.get(target_rail_id.casefold())
        if target_rail is None:
            binding_issues.append("target_rail_unresolved")
        elif (
            str(getattr(target_rail, "net", "")).casefold()
            != destination_net.casefold()
        ):
            binding_issues.append("target_rail_destination_net_mismatch")
        if destination_component is None:
            binding_issues.append("destination_surface_component_unresolved")
        elif destination_component["contact_status"] != "complete":
            binding_issues.append("destination_surface_component_uncontacted")
        if not destination_vertex_ids:
            binding_issues.append("destination_quotient_vertex_unresolved")
        elif len(destination_vertex_ids) != 1:
            binding_issues.append("destination_quotient_vertex_ambiguous")
        elif destination_vertex is None:
            binding_issues.append("destination_quotient_vertex_missing")
        else:
            if (
                destination_vertex["net"].casefold()
                != destination_net.casefold()
            ):
                binding_issues.append("destination_vertex_net_mismatch")
            if (
                destination_component is not None
                and destination_component["component_id"]
                not in destination_vertex["retained_component_ids"]
            ):
                binding_issues.append(
                    "destination_vertex_component_binding_mismatch"
                )
        request_identity = {
            "refdes": refdes.casefold(),
            "via_id": via_id.casefold(),
            "endpoint_node_id": node_id.casefold(),
            "destination_net": destination_net.casefold(),
            "destination_layer": destination_layer.casefold(),
            "destination_island_id": destination_island_id,
            "target_rail_id": target_rail_id.casefold(),
        }
        if request_index:
            request_identity_digest.update(b",")
        request_identity_digest.update(
            canonical_concrete_row_bytes(request_identity)
        )
        row = {
            "binding_kind": "landing_xy_exact_artwork",
            "refdes": refdes,
            "role": "power",
            "source_net": source_net,
            "source_landing_key": [
                source_landing_key[0],
                source_landing_key[1],
            ],
            "source_landing_vertex_id": source_vertex_id,
            "source_first_via_edge_id": source_first_edge_id,
            "source_first_via_owner_id": f"via:{via_id}",
            "landing_x_um": x_um,
            "landing_y_um": y_um,
            "target_rail_id": target_rail_id,
            "target_net": destination_net,
            "target_layer": destination_layer,
            "via_template_id": via_template_id,
            "destination_net": destination_net,
            "destination_layer": destination_layer,
            "destination_pwr_layer": destination_layer,
            "destination_island_id": destination_island_id,
            "destination_island_ids": (
                list(destination_component["island_ids"])
                if destination_component is not None
                else []
            ),
            "destination_component_id": (
                destination_component["component_id"]
                if destination_component is not None
                else None
            ),
            "destination_representative_island_id": (
                destination_component["representative_island_id"]
                if destination_component is not None
                else None
            ),
            "destination_component_evidence_sha256": (
                destination_component["component_evidence_sha256"]
                if destination_component is not None
                else None
            ),
            "destination_vertex_id": destination_vertex_id,
            "geometry_asset_sha256": geometry_asset_sha256,
            "status": "complete" if not binding_issues else "incomplete",
            "issues": binding_issues,
        }
        row["binding_evidence_sha256"] = sha256(
            canonical_concrete_row_bytes(row)
        ).hexdigest()
        retarget_landing_xy_bindings.append(row)
        if request_index:
            binding_rows_digest.update(b",")
        binding_rows_digest.update(canonical_concrete_row_bytes(row))
        scenario_topology_issues.update(binding_issues)

    check_cancelled("normalizing exact retarget landing bindings")
    request_identity_digest.update(b"]}")
    binding_rows_digest.update(b"]}")
    request_identity_sha256 = request_identity_digest.hexdigest()
    binding_rows_sha256 = binding_rows_digest.hexdigest()
    report(
        82,
        "Hashed exact retarget landing bindings "
        f"({total_retarget_landing_bindings:,} binding(s))",
    )
    expected_power_landing_count = sum(
        len(tuple(getattr(connection, "power_vias", ())))
        for connection in connection_by_refdes.values()
    )
    scan_coverage = (
        dict(retarget_landing_scan_coverage)
        if retarget_landing_scan_coverage is not None
        else None
    )
    retarget_landing_xy_coverage = {
        **(scan_coverage or {}),
        "requested_binding_count": len(
            retarget_landing_destination_requests
        ),
        "resolved_binding_count": sum(
            item["status"] == "complete"
            for item in retarget_landing_xy_bindings
        ),
        "missing_binding_count": sum(
            item["status"] != "complete"
            for item in retarget_landing_xy_bindings
        ),
        "requested_binding_ids_sha256": request_identity_sha256,
        "binding_rows_sha256": binding_rows_sha256,
        "lookup_key_policy": (
            "refdes_via_target_rail_destination_pwr_layer"
        ),
    }
    landing_coverage_issues: list[str] = []
    if scenario_topology_requested and scan_coverage is None:
        landing_coverage_issues.append(
            "retarget_landing_scan_coverage_missing"
        )
    if scan_coverage is not None:
        if str(scan_coverage.get("status", "")).casefold() != "complete":
            landing_coverage_issues.append(
                "retarget_landing_scan_status_incomplete"
            )
        if int(scan_coverage.get("power_landing_count", -1)) != (
            expected_power_landing_count
        ):
            landing_coverage_issues.append(
                "retarget_landing_scan_power_count_mismatch"
            )
        if int(scan_coverage.get("scanned_landing_count", -1)) != (
            expected_power_landing_count
        ):
            landing_coverage_issues.append(
                "retarget_landing_scan_coverage_incomplete"
            )
        if int(scan_coverage.get("covered_destination_count", -1)) != len(
            retarget_landing_destination_requests
        ):
            landing_coverage_issues.append(
                "retarget_landing_scan_destination_count_mismatch"
            )
        if (
            str(
                scan_coverage.get("covered_destination_ids_sha256", "")
            ).casefold()
            != request_identity_sha256
        ):
            landing_coverage_issues.append(
                "retarget_landing_scan_destination_hash_mismatch"
            )
    if any(
        item["status"] != "complete"
        for item in retarget_landing_xy_bindings
    ):
        landing_coverage_issues.append(
            "retarget_landing_xy_binding_incomplete"
        )
    retarget_landing_xy_coverage["status"] = (
        "complete" if not landing_coverage_issues else "incomplete"
    )
    retarget_landing_xy_coverage["issues"] = sorted(
        set(landing_coverage_issues)
    )
    if landing_coverage_issues:
        scenario_topology_issues.add(
            "retarget_landing_xy_binding_coverage_incomplete"
        )

    scenario_shared_ideal_links: list[dict[str, Any]] = []
    scenario_link_by_cluster_role_refs: dict[
        tuple[str, str, tuple[str, str]], dict[str, Any]
    ] = {}
    for cluster_id_key, cluster in sorted(cluster_by_id.items()):
        cluster_id = str(getattr(cluster, "cluster_id", "")).strip()
        for role, raw_edges in (
            ("power", tuple(getattr(cluster, "power_edges", ()))),
            ("ground", tuple(getattr(cluster, "ground_edges", ()))),
        ):
            for raw_left, raw_right in raw_edges:
                left = str(raw_left).strip()
                right = str(raw_right).strip()
                ordered_refs = tuple(sorted((left, right), key=str.casefold))
                left_vertex_id = scenario_terminal_vertex_by_ref_role.get(
                    (ordered_refs[0].casefold(), role)
                )
                right_vertex_id = scenario_terminal_vertex_by_ref_role.get(
                    (ordered_refs[1].casefold(), role)
                )
                link_issues: list[str] = []
                if left_vertex_id is None or right_vertex_id is None:
                    link_issues.append("shared_terminal_vertex_unresolved")
                link = {
                    "link_id": scenario_topology_id(
                        "spd-shared-pad-link",
                        cluster_id,
                        role,
                        ordered_refs[0],
                        ordered_refs[1],
                    ),
                    "cluster_id": cluster_id,
                    "role": role,
                    "member_refdes": list(ordered_refs),
                    "start_terminal_vertex_id": left_vertex_id,
                    "end_terminal_vertex_id": right_vertex_id,
                    "link_kind": "topology_only_ideal",
                    "default_state": "enabled",
                    "disable_when_endpoint_pad_state": "isolation_gap",
                    "status": "complete" if not link_issues else "incomplete",
                    "issues": link_issues,
                }
                scenario_shared_ideal_links.append(link)
                scenario_link_by_cluster_role_refs[
                    (cluster_id_key, role, tuple(item.casefold() for item in ordered_refs))
                ] = link
                scenario_topology_issues.update(link_issues)

    def post_gap_components(
        member_refdes: tuple[str, ...],
        raw_edges: tuple[tuple[str, str], ...],
        gap_refdes: str,
    ) -> list[list[str]]:
        display_by_key = {
            str(item).casefold(): str(item).strip()
            for item in member_refdes
            if str(item).strip()
            and str(item).casefold() != gap_refdes.casefold()
        }
        adjacency = {key: set() for key in display_by_key}
        for raw_left, raw_right in raw_edges:
            left = str(raw_left).casefold()
            right = str(raw_right).casefold()
            if left in adjacency and right in adjacency:
                adjacency[left].add(right)
                adjacency[right].add(left)
        components: list[list[str]] = []
        remaining = set(adjacency)
        while remaining:
            first = min(remaining)
            pending = [first]
            component: set[str] = set()
            while pending:
                current = pending.pop()
                if current in component:
                    continue
                component.add(current)
                pending.extend(adjacency[current] - component)
            remaining.difference_update(component)
            components.append(
                [display_by_key[key] for key in sorted(component)]
            )
        return components

    scenario_gap_cut_proofs: list[dict[str, Any]] = []
    for cluster_id_key, cluster in sorted(cluster_by_id.items()):
        cluster_id = str(getattr(cluster, "cluster_id", "")).strip()
        members = tuple(getattr(cluster, "member_refdes", ()))
        power_edges = tuple(getattr(cluster, "power_edges", ()))
        ground_edges = tuple(getattr(cluster, "ground_edges", ()))
        for gap_refdes in tuple(
            getattr(cluster, "isolation_gap_refdes", ())
        ):
            gap_refdes = str(gap_refdes).strip()
            proof_issues: list[str] = []
            cluster_contacts = [
                contact
                for (ref_key, _role), contacts in scenario_contacts_by_ref_role.items()
                if ref_key
                in {str(member).casefold() for member in members}
                for contact in contacts
            ]
            if any(contact["status"] != "complete" for contact in cluster_contacts):
                proof_issues.append("cluster_raw_contacts_not_fully_isolated")
            expected_link_count = len(power_edges) + len(ground_edges)
            actual_links = [
                link
                for link in scenario_shared_ideal_links
                if link["cluster_id"].casefold() == cluster_id_key
            ]
            if len(actual_links) != expected_link_count or any(
                link["status"] != "complete" for link in actual_links
            ):
                proof_issues.append("conditional_shared_link_set_incomplete")
            power_components = post_gap_components(
                members, power_edges, gap_refdes
            )
            ground_components = post_gap_components(
                members, ground_edges, gap_refdes
            )
            component_index_by_ref = {
                refdes.casefold(): index
                for index, component in enumerate(power_components)
                for refdes in component
            }
            landing_component_indexes: dict[str, set[int]] = {}
            for (ref_key, role), contacts in scenario_contacts_by_ref_role.items():
                if role != "power" or ref_key not in component_index_by_ref:
                    continue
                component_index = component_index_by_ref[ref_key]
                for contact in contacts:
                    identity = str(
                        contact.get("landing_vertex_id")
                        or contact.get("first_via_owner_id")
                        or ""
                    ).casefold()
                    if identity:
                        landing_component_indexes.setdefault(identity, set()).add(
                            component_index
                        )
            if any(
                len(component_indexes) > 1
                for component_indexes in landing_component_indexes.values()
            ):
                proof_issues.append(
                    "raw_landing_shared_across_post_gap_components"
                )
            removed_contact_ids = sorted(
                contact["contact_id"]
                for role in ("power", "ground")
                for contact in scenario_contacts_by_ref_role.get(
                    (gap_refdes.casefold(), role), []
                )
            )
            removed_link_ids = sorted(
                link["link_id"]
                for link in actual_links
                if gap_refdes.casefold()
                in {item.casefold() for item in link["member_refdes"]}
            )
            proof = {
                "proof_id": scenario_topology_id(
                    "spd-gap-cut-proof", cluster_id, gap_refdes
                ),
                "cluster_id": cluster_id,
                "gap_refdes": gap_refdes,
                "removed_terminal_vertex_ids": [
                    scenario_terminal_vertex_by_ref_role.get(
                        (gap_refdes.casefold(), role)
                    )
                    for role in ("power", "ground")
                ],
                "removed_contact_ids": removed_contact_ids,
                "removed_ideal_link_ids": removed_link_ids,
                "post_gap_power_components": power_components,
                "post_gap_ground_components": ground_components,
                "raw_top_pad_bypass_status": (
                    "blocked" if not proof_issues else "unresolved"
                ),
                "status": "complete" if not proof_issues else "incomplete",
                "issues": proof_issues,
            }
            scenario_gap_cut_proofs.append(proof)
            scenario_topology_issues.update(proof_issues)

    scenario_terminal_vertices.sort(
        key=lambda item: (item["refdes"].casefold(), item["role"])
    )
    scenario_conditional_contacts.sort(key=lambda item: item["contact_id"])
    scenario_shared_ideal_links.sort(key=lambda item: item["link_id"])
    scenario_gap_cut_proofs.sort(key=lambda item: item["proof_id"])
    scenario_conditional_landing_keys = {
        tuple(contact["landing_key"])
        for contact in scenario_conditional_contacts
    }
    scenario_isolation_complete = bool(
        not scenario_topology_requested
        or finite_via_scenario_isolation_coverage is not None
        and finite_via_scenario_isolation_coverage["status"] == "complete"
        and finite_via_scenario_isolation_coverage["requested_landing_count"]
        == len(scenario_conditional_landing_keys)
    )
    scenario_decap_terminal_topology = {
        "schema_version": "spd-scenario-decap-terminal-topology-v1",
        "base_union_policy": (
            "editable_decap_landing_nodes_excluded_from_permanent_"
            "trace_and_artwork_union"
        ),
        "scenario_compile_policy": (
            "rebuild_ideal_aliases_from_active_contacts_and_shared_links"
        ),
        "moved_component_policy": (
            "disable_all_source_power_contacts_and_retarget_every_eligible_"
            "root_require_at_least_one"
        ),
        "moved_destination_binding_policy": (
            "lookup_refdes_via_target_rail_destination_pwr_layer_in_exact_"
            "landing_xy_quotient_bindings"
        ),
        "normal_disabled_policy": "retain_topology_contacts_omit_cap_body",
        "isolation_gap_policy": (
            "remove_terminal_incident_power_ground_links_contacts_and_body"
        ),
        "vertices": scenario_terminal_vertices,
        "conditional_contacts": scenario_conditional_contacts,
        "conditional_shared_links": scenario_shared_ideal_links,
        "retarget_destination_bindings": retarget_destination_bindings,
        "retarget_destination_coverage": retarget_destination_coverage,
        "retarget_landing_xy_bindings": retarget_landing_xy_bindings,
        "retarget_landing_xy_coverage": retarget_landing_xy_coverage,
        "gap_cut_proofs": scenario_gap_cut_proofs,
        "base_isolation_coverage": finite_via_scenario_isolation_coverage,
        "status": (
            "complete"
            if scenario_isolation_complete and not scenario_topology_issues
            else "incomplete"
        ),
        "issues": sorted(scenario_topology_issues),
    }
    via_island_pair_aggregates = [
        {
            "net": str(getattr(item, "net", "")).strip(),
            "padstack": str(getattr(item, "padstack", "")).strip(),
            "start_layer": str(getattr(item, "start_layer", "")).strip(),
            "end_layer": str(getattr(item, "end_layer", "")).strip(),
            "start_island_id": str(
                getattr(item, "start_island_id", "")
            ).strip(),
            "end_island_id": str(
                getattr(item, "end_island_id", "")
            ).strip(),
            "start_component_island_ids": list(
                getattr(item, "start_component_island_ids", ())
            ),
            "end_component_island_ids": list(
                getattr(item, "end_component_island_ids", ())
            ),
            "count": int(getattr(item, "count")),
            "via_ids_sha256": str(
                getattr(item, "via_ids_sha256", "")
            ).casefold(),
            "terminal_owned_count": getattr(
                item, "terminal_owned_count", None
            ),
            "substrate_count": getattr(item, "substrate_count", None),
            "drill_diameter_um": getattr(
                item, "drill_diameter_um", None
            ),
            "material": getattr(item, "material", None),
            "segments": [
                {
                    "ordinal": int(getattr(segment, "ordinal")),
                    "start_layer": str(
                        getattr(segment, "start_layer", "")
                    ).strip(),
                    "end_layer": str(
                        getattr(segment, "end_layer", "")
                    ).strip(),
                    "length_um": float(getattr(segment, "length_um")),
                }
                for segment in getattr(item, "segments", ())
            ],
            "physical_model_status": str(
                getattr(item, "physical_model_status", "incomplete")
            ).strip().casefold(),
            "physical_model_issues": list(
                getattr(item, "physical_model_issues", ())
            ),
        }
        for item in getattr(reachability, "via_island_pair_aggregates", ())
    ]
    via_island_pair_aggregates.sort(
        key=lambda item: (
            item["net"].casefold(),
            item["padstack"].casefold(),
            item["start_layer"].casefold(),
            item["end_layer"].casefold(),
            item["start_island_id"],
            item["end_island_id"],
        )
    )
    for item in via_island_pair_aggregates:
        start_component = surface_component_by_identity.get(
            (
                item["net"].casefold(),
                item["start_layer"].casefold(),
                tuple(sorted(item["start_component_island_ids"])),
            )
        )
        end_component = surface_component_by_identity.get(
            (
                item["net"].casefold(),
                item["end_layer"].casefold(),
                tuple(sorted(item["end_component_island_ids"])),
            )
        )
        item["start_component_id"] = (
            start_component["component_id"]
            if start_component is not None
            else None
        )
        item["start_component_evidence_sha256"] = (
            start_component["component_evidence_sha256"]
            if start_component is not None
            else None
        )
        item["end_component_id"] = (
            end_component["component_id"]
            if end_component is not None
            else None
        )
        item["end_component_evidence_sha256"] = (
            end_component["component_evidence_sha256"]
            if end_component is not None
            else None
        )
        component_issues: list[str] = []
        if start_component is None:
            component_issues.append("start_component_unresolved")
        elif start_component["contact_status"] != "complete":
            component_issues.append("start_component_uncontacted")
        elif item["start_island_id"] != start_component[
            "representative_island_id"
        ]:
            component_issues.append("start_representative_mismatch")
        if end_component is None:
            component_issues.append("end_component_unresolved")
        elif end_component["contact_status"] != "complete":
            component_issues.append("end_component_uncontacted")
        elif item["end_island_id"] != end_component[
            "representative_island_id"
        ]:
            component_issues.append("end_representative_mismatch")
        item["component_binding_status"] = (
            "complete" if not component_issues else "incomplete"
        )
        item["component_binding_issues"] = component_issues
        physical_issues = set(item["physical_model_issues"])
        physical_issues.update(
            segment_stackup_issues(
                item["segments"],
                expected_start_layer=item["start_layer"],
                expected_end_layer=item["end_layer"],
            )
        )
        item["physical_model_issues"] = sorted(physical_issues)
        if physical_issues:
            item["physical_model_status"] = "incomplete"
    raw_via_island_pair_coverage = getattr(
        reachability, "via_island_pair_coverage", None
    )
    via_island_pair_coverage = (
        {
            "raw_target_via_count": int(
                getattr(raw_via_island_pair_coverage, "raw_target_via_count")
            ),
            "paired_via_count": int(
                getattr(raw_via_island_pair_coverage, "paired_via_count")
            ),
            "terminal_owned_unpaired_count": int(
                getattr(
                    raw_via_island_pair_coverage,
                    "terminal_owned_unpaired_count",
                )
            ),
            "terminal_owned_unpaired_via_ids_sha256": str(
                getattr(
                    raw_via_island_pair_coverage,
                    "terminal_owned_unpaired_via_ids_sha256",
                )
            ).casefold(),
            "unsupported_missing_endpoint_count": int(
                getattr(
                    raw_via_island_pair_coverage,
                    "unsupported_missing_endpoint_count",
                )
            ),
            "unsupported_missing_endpoint_via_ids_sha256": str(
                getattr(
                    raw_via_island_pair_coverage,
                    "unsupported_missing_endpoint_via_ids_sha256",
                )
            ).casefold(),
            "outside_retained_interface_scope_count": int(
                getattr(
                    raw_via_island_pair_coverage,
                    "outside_retained_interface_scope_count",
                )
            ),
            "outside_retained_interface_scope_via_ids_sha256": str(
                getattr(
                    raw_via_island_pair_coverage,
                    "outside_retained_interface_scope_via_ids_sha256",
                )
            ).casefold(),
            "outside_retained_interface_scope_reason": (
                "outside_retained_interface_scope"
            ),
            "model_relevant_via_count": int(
                getattr(
                    raw_via_island_pair_coverage,
                    "model_relevant_via_count",
                )
            ),
            "terminal_owned_ids_supplied": bool(
                getattr(
                    raw_via_island_pair_coverage,
                    "terminal_owned_ids_supplied",
                )
            ),
            "terminal_owned_declared_count": int(
                getattr(
                    raw_via_island_pair_coverage,
                    "terminal_owned_declared_count",
                )
            ),
            "terminal_owned_observed_count": int(
                getattr(
                    raw_via_island_pair_coverage,
                    "terminal_owned_observed_count",
                )
            ),
            "paired_terminal_owned_count": getattr(
                raw_via_island_pair_coverage,
                "paired_terminal_owned_count",
            ),
            "paired_substrate_count": getattr(
                raw_via_island_pair_coverage,
                "paired_substrate_count",
            ),
            "status": str(
                getattr(raw_via_island_pair_coverage, "status")
            ),
        }
        if raw_via_island_pair_coverage is not None
        else None
    )
    serialized_landing_contact_by_key = {
        tuple(item["landing_key"]): item
        for item in landing_surface_contacts
    }
    rail_by_key = {
        rail.rail_id.casefold(): rail
        for rail in getattr(project, "rails", ())
    }
    required_layer_keys_by_pin: dict[str, set[str]] = {}
    for binding in rail_anchor_bindings:
        pin_key = str(binding.get("pin_id", "")).strip().casefold()
        rail = rail_by_key.get(
            str(binding.get("rail_id", "")).strip().casefold()
        )
        role = str(binding.get("role", "")).strip().casefold()
        if not pin_key or rail is None or role not in {"power", "ground"}:
            continue
        layer = rail.pwr_layer if role == "power" else rail.gnd_layer
        required_layer_keys_by_pin.setdefault(pin_key, set()).add(
            layer.casefold()
        )
    for contact in terminal_contacts:
        pin_key = str(contact.get("pin_id", "")).strip().casefold()
        net_key = str(contact.get("net", "")).strip().casefold()
        required_layer_keys = required_layer_keys_by_pin.get(pin_key, set())
        path_kind = str(
            contact.get("contact_path_kind", "unresolved")
        ).strip()
        candidate_components: dict[str, dict[str, Any]] = {}
        landing = landing_by_pin.get(pin_key)
        serialized_landing_contact = (
            serialized_landing_contact_by_key.get(
                (
                    landing.via_id.casefold(),
                    landing.endpoint_node_id.casefold(),
                )
            )
            if landing is not None
            else None
        )
        # v4 binds the external Device pin to its quotient vertex.  Required
        # rail surfaces may be reached through any finite branch/cycle network;
        # the first Via's immediate opposite endpoint is not required to be
        # artwork and multiple reachable surface components are valid MNA
        # boundaries rather than an ambiguity.
        for island_id in contact.get("contact_island_ids", ()):
            for required_layer_key in required_layer_keys:
                component = surface_component_by_island.get(
                    (net_key, required_layer_key, str(island_id))
                )
                if (
                    component is not None
                    and component["contact_status"] == "complete"
                ):
                    candidate_components[component["component_id"]] = component
        candidates = sorted(
            (
                component
                for component in candidate_components.values()
                if component["net"].casefold() == net_key
                and component["layer"].casefold() in required_layer_keys
            ),
            key=lambda item: item["component_id"],
        )
        issues = {
            str(item)
            for item in contact.get("issues", ())
            if str(item)
        }
        exposed_vertex_id = str(
            contact.get("exposed_quotient_vertex_id") or ""
        ).strip()
        exposed_vertex = finite_vertex_by_id.get(exposed_vertex_id)
        first_via_edge_id = str(
            contact.get("first_via_quotient_edge_id") or ""
        ).strip()
        first_via_edge = finite_edge_by_id.get(first_via_edge_id)
        if exposed_vertex is None:
            issues.add("exposed_quotient_vertex_unresolved")
        else:
            if exposed_vertex["net"].casefold() != net_key:
                issues.add("exposed_quotient_vertex_net_mismatch")
            if "terminal" not in exposed_vertex["roles"]:
                issues.add("exposed_quotient_vertex_not_terminal")
        if path_kind == "direct_via_landing":
            if first_via_edge is None:
                issues.add("first_via_global_edge_unresolved")
            elif exposed_vertex_id not in {
                first_via_edge["start_vertex_id"],
                first_via_edge["end_vertex_id"],
            }:
                issues.add("first_via_global_edge_not_incident")
            elif first_via_edge["status"] != "complete":
                issues.add("first_via_global_edge_incomplete")
        if path_kind == "trace_component" and candidates:
            issues = {
                item
                for item in issues
                if item != "first_via_status:missing_incident_via"
            }
        if not required_layer_keys:
            issues.add("required_rail_surface_missing")
        if not candidates:
            issues.add("required_surface_component_unresolved")
        if path_kind not in {
            "direct_via_landing",
            "trace_component",
            "unresolved",
        }:
            issues.add("contact_path_kind_invalid")
        contact["contact_component_ids"] = [
            item["component_id"] for item in candidates
        ]
        contact["reachable_required_component_ids"] = list(
            contact["contact_component_ids"]
        )
        contact["contact_component_evidence_sha256s"] = [
            item["component_evidence_sha256"] for item in candidates
        ]
        contact["contact_component_id"] = (
            candidates[0]["component_id"] if len(candidates) == 1 else None
        )
        contact["contact_component_evidence_sha256"] = (
            candidates[0]["component_evidence_sha256"]
            if len(candidates) == 1
            else None
        )
        contact["contact_component_island_ids"] = (
            list(candidates[0]["island_ids"])
            if len(candidates) == 1
            else sorted(
                {
                    island_id
                    for item in candidates
                    for island_id in item["island_ids"]
                }
            )
        )
        contact["representative_island_id"] = (
            candidates[0]["representative_island_id"]
            if len(candidates) == 1
            else None
        )
        contact["contact_component_layer"] = (
            candidates[0]["layer"] if len(candidates) == 1 else None
        )
        contact["issues"] = sorted(issues)
        contact["status"] = (
            "complete"
            if landing is not None
            and bool(candidates)
            and exposed_vertex is not None
            and not issues
            and path_kind in {"direct_via_landing", "trace_component"}
            else "incomplete"
        )
    surface_equivalence_proofs = [
        {
            "net": str(getattr(item, "net", "")).strip(),
            "layer": str(getattr(item, "layer", "")).strip(),
            "island_ids": list(getattr(item, "island_ids", ())),
            "contacted_island_ids": list(
                getattr(item, "contacted_island_ids", ())
            ),
            "graph_component_count": int(
                getattr(item, "graph_component_count", -1)
            ),
            "partition_kind": (
                "single_component"
                if int(getattr(item, "graph_component_count", -1)) == 1
                else "split_components"
            ),
            "status": str(getattr(item, "status", "")).strip(),
        }
        for item in getattr(reachability, "surface_equivalence_proofs", ())
    ]
    surface_equivalence_proofs.sort(
        key=lambda item: (
            item["net"].casefold(),
            item["layer"].casefold(),
            item["net"],
            item["layer"],
        )
    )
    proof_keys = {
        (item["net"].casefold(), item["layer"].casefold())
        for item in surface_equivalence_proofs
    }
    geometry_keys = {
        (str(item.get("net", "")).casefold(), str(item.get("layer", "")).casefold())
        for item in geometry_assets
    }
    geometry_islands_by_key = {
        (str(item.get("net", "")).casefold(), str(item.get("layer", "")).casefold()): tuple(
            sorted(str(value) for value in item.get("island_ids", ()))
        )
        for item in geometry_assets
    }
    proof_islands_by_key = {
        (item["net"].casefold(), item["layer"].casefold()): tuple(
            sorted(str(value) for value in item["island_ids"])
        )
        for item in surface_equivalence_proofs
    }
    partition_islands_by_key: dict[tuple[str, str], set[str]] = {}
    for item in surface_equivalence_components:
        partition_islands_by_key.setdefault(
            (item["net"].casefold(), item["layer"].casefold()), set()
        ).update(str(value) for value in item["island_ids"])
    surface_partition_complete = bool(
        surface_equivalence_proofs
        and proof_keys == geometry_keys
        and proof_islands_by_key == geometry_islands_by_key
        and {
            key: tuple(sorted(values))
            for key, values in partition_islands_by_key.items()
        }
        == geometry_islands_by_key
    )
    recovery_statistics = {
        str(key): value
        for key, value in sorted(dict(reachability.statistics).items())
    }
    via_ownership_complete = bool(
        finite_via_coverage is not None
        and finite_via_coverage["status"] == "complete"
        and finite_via_coverage["owner_ledger_status"] == "complete"
        and finite_via_coverage["terminal_exclusive_via_count"] == 0
    )
    finite_vertex_bindings_complete = bool(finite_via_vertices) and all(
        item["component_binding_status"] == "complete"
        and not item["component_binding_issues"]
        for item in finite_via_vertices
    )
    finite_edges_complete = finite_via_coverage is not None and all(
        item["status"] == "complete"
        and not item["endpoint_binding_issues"]
        and not item["physical_model_issues"]
        for item in finite_via_edges
    )
    finite_terminal_bindings_complete = bool(
        finite_via_terminal_bindings
    ) and all(
        item["global_quotient_binding_status"] == "complete"
        and not item["global_quotient_binding_issues"]
        and (
            item["terminal_owner_kind"] != "decap"
            or item["retarget_cut_status"] == "complete"
        )
        for item in finite_via_terminal_bindings
    )
    scenario_terminal_topology_complete = (
        scenario_decap_terminal_topology["status"] == "complete"
    )
    retarget_destination_bindings_complete = (
        retarget_destination_coverage["status"] == "complete"
    )
    retarget_landing_xy_bindings_complete = (
        retarget_landing_xy_coverage["status"] == "complete"
    )
    source_hash_valid = bool(
        len(source_sha256) == 64
        and all(character in "0123456789abcdef" for character in source_sha256.casefold())
    )
    payload: dict[str, Any] = {
        "schema_version": _LAYER_SURFACE_CONNECTIVITY_SCHEMA,
        "compiler_id": _LAYER_SURFACE_CONNECTIVITY_COMPILER,
        "source_sha256": source_sha256.casefold(),
        "geometry_assets": geometry_assets,
        "components": components,
        "surface_equivalence_components": surface_equivalence_components,
        "surface_equivalence_proofs": surface_equivalence_proofs,
        "rail_anchor_bindings": rail_anchor_bindings,
        "terminal_contacts": terminal_contacts,
        "terminal_landing_contacts": landing_surface_contacts,
        "finite_via_quotient": {
            "schema_version": "spd-finite-via-quotient-v1",
            "reducer_id": "finite-route-reduction-v1-compact-csr",
            "same_layer_union_policy": "trace_and_exact_artwork_only",
            "finite_edge_policy": "every_relevant_raw_via_global_mna",
            "scenario_retarget_policy": (
                "suppress_explicit_decap_first_via_then_stamp_destination_path"
            ),
            "vertices": finite_via_vertices,
            "edges": finite_via_edges,
            "terminal_bindings": finite_via_terminal_bindings,
            "coverage": finite_via_coverage,
            "scenario_isolation_coverage": (
                finite_via_scenario_isolation_coverage
            ),
            "retarget_destination_bindings": (
                retarget_destination_bindings
            ),
            "retarget_destination_coverage": (
                retarget_destination_coverage
            ),
            "retarget_landing_xy_bindings": (
                retarget_landing_xy_bindings
            ),
            "retarget_landing_xy_coverage": (
                retarget_landing_xy_coverage
            ),
            "status": (
                "complete"
                if via_ownership_complete
                and finite_vertex_bindings_complete
                and finite_edges_complete
                and finite_terminal_bindings_complete
                and scenario_terminal_topology_complete
                and retarget_destination_bindings_complete
                and retarget_landing_xy_bindings_complete
                else "incomplete"
            ),
        },
        "scenario_decap_terminal_topology": (
            scenario_decap_terminal_topology
        ),
        "via_island_pair_aggregates": via_island_pair_aggregates,
        "via_island_pair_coverage": via_island_pair_coverage,
        "compile_failures": compile_failures,
        "recovery_statistics": recovery_statistics,
        "status": (
            "complete"
            if source_hash_valid
            and bool(geometry_assets)
            and bool(rail_anchor_bindings)
            and not compile_failures
            and surface_partition_complete
            and terminal_contacts
            and all(item["status"] == "complete" for item in terminal_contacts)
            and via_ownership_complete
            and finite_vertex_bindings_complete
            and finite_edges_complete
            and finite_terminal_bindings_complete
            and scenario_terminal_topology_complete
            and retarget_destination_bindings_complete
            and retarget_landing_xy_bindings_complete
            else "incomplete"
        ),
    }
    report(95, "Hashing complete layer-surface certificate evidence")
    check_cancelled("hashing complete layer-surface certificate evidence")
    try:
        evidence_sha256 = canonical_surface_certificate_sha256(
            payload,
            is_cancelled=cancelled,
            concrete_containers=True,
        )
    except SurfaceCertificateAssetError as exc:
        if exc.code == "SURFACE_CERTIFICATE_CANCELLED":
            raise SpdImportError(
                "SPD_LAYER_SURFACE_CONNECTIVITY_CERTIFICATE_CANCELLED: "
                "cancelled while hashing complete layer-surface certificate "
                "evidence"
            ) from exc
        raise
    report(100, "Compiled layer-surface connectivity certificate evidence")
    return {
        **payload,
        # This certificate is multi-GiB on the supplied production boards.
        # Stream the exact same canonical JSON bytes into SHA-256 instead of
        # materializing one full Unicode string and a second UTF-8 copy.  The
        # import owns a concrete dict/list tree, so the standard streaming
        # encoder preserves the wire bytes while avoiding per-scalar dispatch.
        "evidence_sha256": evidence_sha256,
    }


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


def _compile_shared_cluster_eligibility(
    *,
    parsed_cluster_by_key: dict[str, Any],
    top_instance_by_key: dict[str, SpdCapInstance],
    source_rail_id_by_key: dict[str, str],
    parsed_connection_by_key: dict[str, Any],
    scenario_landing: Callable[[Any], ScenarioViaLanding],
    eligibility_index: PlaneEligibilityIndex,
    rail_choices_by_pair: dict[
        tuple[str, str, str], tuple[tuple[Any, str], ...]
    ],
) -> tuple[
    set[str],
    dict[str, SharedPadClusterState],
    dict[str, str | None],
    dict[str, dict[str, RailEligibility]],
    dict[str, dict[str, dict[str, RailEligibility]]],
]:
    """Compile the one canonical persisted state for every editable cluster.

    Mixed-reference witness selection and the final scenario must consume this
    same result.  Computing witnesses from raw parser state first can certify a
    cluster that is later demoted to UNRESOLVED when its source rail is absent
    beneath one exact PWR-via landing.
    """

    accepted_cluster_keys: set[str] = set()
    state_by_key: dict[str, SharedPadClusterState] = {}
    reason_by_key: dict[str, str | None] = {}
    eligibility_by_key: dict[str, dict[str, RailEligibility]] = {}
    via_eligibility_by_key: dict[
        str, dict[str, dict[str, RailEligibility]]
    ] = {}
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
            reason = "shared-pad members resolve to different source rail identities"
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
                    power_landings.values(), key=lambda item: item.via_id.casefold()
                )
            }
            eligibility = _common_eligibility_maps(tuple(via_eligibility.values()))
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
        state_by_key[cluster_key] = state_value
        reason_by_key[cluster_key] = reason
        eligibility_by_key[cluster_key] = eligibility
        via_eligibility_by_key[cluster_key] = via_eligibility
    return (
        accepted_cluster_keys,
        state_by_key,
        reason_by_key,
        eligibility_by_key,
        via_eligibility_by_key,
    )


def _select_mixed_reference_ground_landings(
    *,
    mixed_rails: dict[str, Any],
    top_instances: tuple[SpdCapInstance, ...],
    parsed_connection_by_key: dict[str, Any],
    parsed_cluster_by_key: dict[str, Any],
    cluster_state_by_key: dict[str, SharedPadClusterState],
    cluster_eligibility_by_key: dict[str, dict[str, RailEligibility]],
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
            # A persisted DIRECT connection cannot name a cluster.  Do not
            # create witness evidence for malformed raw classifier output that
            # final scenario normalization cannot retain as DIRECT.
            if connection.cluster_id is not None:
                continue
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
            # DIRECT connections are persisted and revalidated by physical
            # RefDes.  Only SHARED_ANCHOR branches use a cluster owner identity.
            owner = instance.refdes
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
        if (
            cluster is None
            or cluster_state_by_key.get(cluster_key)
            != SharedPadClusterState.ANCHORED
        ):
            continue
        common = cluster_eligibility_by_key.get(cluster_key, {})
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
    raw_report = progress or (lambda _value, _message: None)
    last_reported_progress = -1

    def report(value: int, message: str) -> None:
        nonlocal last_reported_progress
        bounded = max(last_reported_progress, min(100, max(0, int(value))))
        last_reported_progress = bounded
        raw_report(bounded, message)

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

    def plan_progress(value: int, message: str) -> None:
        report(83 + round(max(0, min(100, value)) * 2 / 100), message)

    plan = build_spd_import_plan(
        state.project,
        analysis,
        source_path,
        progress=plan_progress,
        is_cancelled=cancelled,
    )
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
                        for certificate in (
                            candidate_rail.mixed_reference_certificate,
                        )
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
        mixed_reference_certificates=plan.mixed_reference_certificates,
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
            mixed_reference_certificates=plan.mixed_reference_certificates,
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

    # Select source-proven pairs before compiling the persisted surface
    # certificate.  Retain the first plan as the exact geometry donor so the
    # rebuild changes only rail/pair bindings and cannot recompress or reorder
    # the already certified artwork assets.
    connectivity_geometry_groups: dict[
        tuple[str, str], list[Any]
    ] = {}
    for geometry in analysis.plane_geometries:
        key = (
            str(geometry.layer).casefold(),
            str(geometry.net).casefold(),
        )
        connectivity_geometry_groups.setdefault(key, []).append(geometry)
    connectivity_index = {
        key: indexed
        for key, values in connectivity_geometry_groups.items()
        if len(values) == 1 and values[0].primitive_order
        for indexed in (IndexedPlaneGeometry.build(values[0]),)
        if indexed is not None
    }
    report(91, "Selecting strict source-proven plane pairs")
    selected_pairs, pair_provenance = _strict_source_plane_pairs(
        base_project,
        analysis,
        path_recovery,
        indexed_override=connectivity_index,
        mixed_reference_certificates=plan.mixed_reference_certificates,
    )
    rebuild_started = perf_counter()
    plan = build_spd_import_plan(
        state.project,
        analysis,
        source_path,
        selected_pairs=selected_pairs,
        selected_pair_provenance=pair_provenance,
        _geometry_from_plan=geometry_donor_plan,
        progress=lambda _value, message: report(91, message),
        is_cancelled=cancelled,
    )
    plan_s += perf_counter() - rebuild_started
    blocking = [
        item
        for item in plan.diagnostics
        if str(getattr(item, "severity", "")).casefold() == "error"
    ]
    if blocking or not plan.can_apply:
        details = "; ".join(
            f"{getattr(item, 'code', 'SPD_ERROR')}: "
            f"{getattr(item, 'message', item)}"
            for item in blocking[:5]
        )
        raise SpdImportError(
            "SPD scenario import is blocked because the source-proven pair "
            f"rebuild is invalid. {details or 'Import plan is not applicable.'}"
        )
    base_project = _normalized_base_project(plan.project)
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
            progress=lambda _value, message: report(91, message),
            is_cancelled=cancelled,
        )
        if not restored_plan.can_apply:
            raise SpdImportError(
                "cannot safely rebuild import after rail-scoped final validation failure"
            )
        plan = restored_plan
        selected_pairs = restored_selected_pairs
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
        mixed_reference_certificates=plan.mixed_reference_certificates,
        selected_pairs=tuple(selected_pairs.values()),
    )
    source_rail_id_by_key = {
        key: (
            rail.rail_id
            if (rail := _rail_for_instance(base_project, instance)) is not None
            else f"UNAVAILABLE::{instance.power_net}"
        )
        for key, instance in top_instance_by_key.items()
    }
    landing_cache: dict[str, ScenarioViaLanding] = {}

    def scenario_landing(landing: Any) -> ScenarioViaLanding:
        key = landing.via_id.casefold()
        result = landing_cache.get(key)
        if result is None:
            result = _scenario_via_landing(landing, path_recovery)
            landing_cache[key] = result
        return result

    cluster_eligibility_started = perf_counter()
    (
        accepted_cluster_keys,
        cluster_state_by_key,
        cluster_reason_by_key,
        cluster_eligibility_by_key,
        cluster_via_eligibility_by_key,
    ) = _compile_shared_cluster_eligibility(
        parsed_cluster_by_key=parsed_cluster_by_key,
        top_instance_by_key=top_instance_by_key,
        source_rail_id_by_key=source_rail_id_by_key,
        parsed_connection_by_key=parsed_connection_by_key,
        scenario_landing=scenario_landing,
        eligibility_index=eligibility_index,
        rail_choices_by_pair=rail_choices_by_pair,
    )
    cluster_eligibility_s = perf_counter() - cluster_eligibility_started
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
        cluster_state_by_key=cluster_state_by_key,
        cluster_eligibility_by_key=cluster_eligibility_by_key,
        path_recovery=path_recovery,
        eligibility_index=eligibility_index,
        rail_choices_by_pair=rail_choices_by_pair,
    )
    rail_anchor_bindings, anchor_compile_failures = (
        _compile_active_rail_anchor_bindings(
            base_project,
            source_sha256=analysis.source.sha256,
        )
    )
    anchor_landings_by_pin, anchor_contact_seeds = _anchor_graph_landings(
        rail_anchor_bindings,
        analysis.device_terminal_via_endpoints,
    )
    surface_artwork = _retained_surface_artwork(
        base_project,
        dict(plan.attachments),
        analysis.plane_geometries,
        indexed_geometry_by_key=connectivity_index,
        progress=lambda value, message: report(
            91 + round(max(0, min(100, value)) * 1 / 100), message
        ),
        is_cancelled=cancelled,
    )
    surface_geometry_assets = surface_artwork.geometry_assets
    surface_target_layers_by_net = surface_artwork.target_layers_by_net
    surface_target_island_ids = surface_artwork.island_ids_by_surface
    for net, layers in mixed_target_layers_by_net.items():
        surface_target_layers_by_net.setdefault(net.casefold(), set()).update(
            layers
        )
    mixed_witness_selection_s = perf_counter() - mixed_witness_selection_started
    ground_recovery_started = perf_counter()
    mixed_recovery_landings = tuple(
        landing
        for entries in mixed_ground_landings_by_rail.values()
        for _refdes, landing in entries
    )
    recovery_landing_by_key: dict[tuple[str, str, str], Any] = {}
    for landing in (*mixed_recovery_landings, *anchor_landings_by_pin.values()):
        key = (
            str(getattr(landing, "via_id")).casefold(),
            str(getattr(landing, "endpoint_node_id")).casefold(),
            str(getattr(landing, "net")).casefold(),
        )
        recovery_landing_by_key.setdefault(key, landing)
    active_cap_keys = set(top_instance_by_key)
    surface_target_net_keys = set(surface_target_layers_by_net)
    def source_connection_kind(connection: object) -> str:
        value = getattr(connection, "kind", "")
        return str(getattr(value, "value", value)).strip().upper()

    scenario_topology_connection_kinds = {
        "DIRECT",
        "SHARED_ANCHOR",
        "SHARED_DUMMY",
    }
    certificate_decap_connections = tuple(
        ScenarioDecapConnection(
            refdes=connection.refdes,
            kind=DecapConnectionKind(source_connection_kind(connection)),
            cluster_id=connection.cluster_id,
            power_vias=tuple(
                scenario_landing(landing)
                for landing in connection.power_vias
            ),
            ground_vias=tuple(
                scenario_landing(landing)
                for landing in connection.ground_vias
            ),
            reason=connection.reason,
        )
        for connection in analysis.decap_connections
        if connection.refdes.casefold() in active_cap_keys
        and source_connection_kind(connection)
        in scenario_topology_connection_kinds
    )
    (
        retarget_landing_destination_requests,
        retarget_landing_scan_coverage,
    ) = _compile_retarget_landing_destination_requests(
        project=base_project,
        decap_connections=certificate_decap_connections,
        geometry_assets=surface_geometry_assets,
        strict_island_resolver=surface_artwork.strict_surface_resolver,
        strict_island_resolver_batch=(
            surface_artwork.strict_surface_resolver_batch
        ),
        release_surface=surface_artwork.release,
        progress=lambda value, message: report(
            92 + round(max(0, min(100, value)) * 1 / 100), message
        ),
        is_cancelled=cancelled,
    )
    terminal_decap_landings = tuple(
        landing
        for connection in certificate_decap_connections
        for landing in (*connection.power_vias, *connection.ground_vias)
        if landing.net.casefold() in surface_target_net_keys
    )
    retarget_destination_requests = tuple(
        (
            landing.net,
            evidence.target_layer,
            evidence.target_node_id,
        )
        for connection in certificate_decap_connections
        for landing in (*connection.power_vias, *connection.ground_vias)
        for evidence in landing.path_evidence
        if landing.net.casefold() in surface_target_net_keys
    )
    terminal_contact_landing_by_key: dict[tuple[str, str, str], Any] = {}
    for landing in (
        *terminal_decap_landings,
        *anchor_landings_by_pin.values(),
    ):
        key = (
            str(getattr(landing, "via_id")).casefold(),
            str(getattr(landing, "endpoint_node_id")).casefold(),
            str(getattr(landing, "net")).casefold(),
        )
        terminal_contact_landing_by_key.setdefault(key, landing)
    terminal_owned_via_ids = {
        str(landing.via_id).strip()
        for landing in terminal_decap_landings
        if str(landing.via_id).strip()
    }
    terminal_owned_via_ids.update(
        str(endpoint.incident_via_id).strip()
        for endpoint in analysis.device_terminal_via_endpoints
        if endpoint.status == "complete"
        and endpoint.incident_via_id is not None
        and endpoint.incident_net is not None
        and endpoint.incident_net.casefold() in surface_target_net_keys
        and str(endpoint.incident_via_id).strip()
    )
    mixed_target_predicates_by_key: dict[
        tuple[str, str], Callable[[str, str, str, float, float], bool]
    ] = {}
    if plan.mixed_reference_certificates:
        mixed_target_predicate = _mixed_reference_target_node_predicate(
            base_project,
            dict(plan.attachments),
            indexed_geometry_by_key={
                (net, layer): indexed
                for (layer, net), indexed in connectivity_index.items()
            },
        )
        mixed_target_predicates_by_key = {
            (
                str(certificate.gnd_net).casefold(),
                str(certificate.gnd_layer).casefold(),
            ): mixed_target_predicate
            for certificate in plan.mixed_reference_certificates
        }
    ground_reachability = recover_spd_ground_reachability(
        source_path,
        landings=tuple(recovery_landing_by_key.values()),
        terminal_contact_landings=tuple(
            terminal_contact_landing_by_key.values()
        ),
        scenario_isolated_terminal_landings=terminal_decap_landings,
        retarget_destination_requests=retarget_destination_requests,
        terminal_owned_via_ids=terminal_owned_via_ids,
        padstacks=analysis.padstacks,
        stackup_layers=base_project.stackup_layers,
        target_layers_by_net=surface_target_layers_by_net,
        target_node_predicate=None,
        target_node_predicates_by_key=mixed_target_predicates_by_key,
        same_layer_artwork_layers_by_net=surface_target_layers_by_net,
        same_layer_artwork_component=surface_artwork.artwork_component,
        same_layer_artwork_components_batch=(
            surface_artwork.artwork_components_batch
        ),
        same_layer_artwork_release=surface_artwork.release,
        target_node_surface_resolver=surface_artwork.surface_resolver,
        target_node_surface_resolver_batch=(
            surface_artwork.surface_resolver_batch
        ),
        target_surface_island_ids=surface_target_island_ids,
        expected_source=analysis.source,
        progress=lambda value, message: report(92 + round(max(0, min(100, value)) * 1 / 100), message),
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
    # The shared recovery pass covers the complete source/surface graph once.
    # Derive the mixed-reference witness universe from the exact mixed landing
    # and target-layer Cartesian product, then intersect it with the keys
    # proved by that shared pass.  Keep the mixed view's counters scoped to
    # this universe; the full graph/artwork counters remain under the separate
    # source_graph_artwork_reachability metadata entry below.
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
        # No mixed witness scan was requested.  Do not expose the shared
        # source-graph work as if it described an empty mixed pass.
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
    source_graph_statistics = dict(
        getattr(ground_reachability, "statistics", {}) or {}
    )
    source_graph_artwork_reachability = {
        "version": str(
            source_graph_statistics.get(
                "artwork_algorithm",
                "same_net_via_trace_artwork_reachability_v3_layerwise_deferred",
            )
        ),
        "indexed_geometry_count": len(connectivity_index),
        "exact_geometry_build_count": sum(
            int(indexed.artwork_shape_built)
            for indexed in connectivity_index.values()
        ),
        # Preserve every source/surface recovery statistic in its own scope;
        # mixed_reference_ground_reachability above is intentionally filtered.
        **source_graph_statistics,
    }
    surface_connectivity_certificate = _layer_surface_connectivity_certificate(
        project=base_project,
        source_sha256=analysis.source.sha256,
        geometry_assets=surface_geometry_assets,
        rail_anchor_bindings=rail_anchor_bindings,
        compile_failures=anchor_compile_failures,
        contact_seeds=anchor_contact_seeds,
        landing_by_pin=anchor_landings_by_pin,
        reachability=ground_reachability,
        decap_connections=certificate_decap_connections,
        shared_pad_clusters=analysis.shared_pad_clusters,
        retarget_landing_destination_requests=(
            retarget_landing_destination_requests
        ),
        retarget_landing_scan_coverage=retarget_landing_scan_coverage,
        progress=lambda value, message: report(
            93 + round(max(0, min(100, value)) * 1 / 100), message
        ),
        is_cancelled=cancelled,
    )
    surface_connectivity_diagnostics: list[SpdDiagnostic] = []
    if surface_connectivity_certificate["status"] != "complete":
        surface_connectivity_diagnostics.append(
            SpdDiagnostic(
                severity="warning",
                code="SPD_LAYER_SURFACE_CONNECTIVITY_INCOMPLETE",
                message=(
                    "The global layer-surface connectivity certificate is "
                    "incomplete; selected-rail proof must validate its own "
                    "compiled anchors and exact artwork contacts. "
                    f"compile_failures={len(anchor_compile_failures)}, "
                    "incomplete_contacts="
                    f"{sum(item['status'] != 'complete' for item in surface_connectivity_certificate['terminal_contacts'])}."
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
    spd_import_metadata = dict(recovery_metadata.get("spd_import", {}))
    spd_import_metadata["plane_geometries"] = (
        _bind_certified_surface_islands(
            spd_import_metadata.get("plane_geometries"),
            surface_geometry_assets,
        )
    )
    spd_import_metadata["layerwise_surface_connectivity_certificate"] = (
        surface_connectivity_certificate
    )
    quotient_status = str(
        surface_connectivity_certificate.get("finite_via_quotient", {}).get(
            "status", "missing"
        )
    )
    topology_status = str(
        surface_connectivity_certificate.get(
            "scenario_decap_terminal_topology", {}
        ).get("status", "missing")
    )
    report(
        94,
        "Bound exact surface-island manifest; "
        f"certificate={surface_connectivity_certificate['status']}, "
        f"quotient={quotient_status}, scenario={topology_status}",
    )
    recovery_metadata["spd_import"] = spd_import_metadata
    recovery_metadata["spd_via_path_recovery"] = {
        **dict(path_recovery.statistics),
        "algorithm": "unique_monotonic_same_net_via_chain_v1",
        "fallback_behavior": "legacy_rail_template",
        "mixed_reference_ground_reachability": {
            **mixed_reachability_statistics,
            "algorithm": "same_net_via_trace_reachability_v1",
            "by_rail": mixed_ground_reachability_by_rail,
        },
        "source_graph_artwork_reachability": source_graph_artwork_reachability,
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
    # Externalize before ScenarioSpec validation.  A real v4 quotient has
    # millions of rows; allowing Pydantic to copy that nested tree drove fresh
    # import peak memory above 26 GiB.  The authoritative canonical certificate
    # and its safe compiled fast path are persisted now, and only their small
    # manifests enter ScenarioSpec.
    base_project, scenario_attachments = externalize_project_surface_certificate(
        base_project,
        dict(plan.attachments),
        progress=lambda value, message: report(
            94 + round(max(0, min(100, value)) * 3 / 100), message
        ),
        is_cancelled=cancelled,
    )
    del surface_connectivity_certificate
    del spd_import_metadata
    del recovery_metadata
    report(
        97,
        "Recovered source-proven Via path summaries; checking exact PWR-plane eligibility",
    )
    eligibility_started = perf_counter()

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
                97 + round(fraction * 2),
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
    eligibility_s = cluster_eligibility_s + (perf_counter() - eligibility_started)
    report(
        99,
        f"Checked {total_instances:,} decaps in {eligibility_s:.1f}s; "
        "validating scenario and compressing finite-topology evidence",
    )
    finalize_started = perf_counter()
    if _has_compiled_topology_manifest(base_project):
        report(
            99,
            "Compiling exact raw SPD spatial-contact evidence",
        )
        raw_spatial_manifest, raw_spatial_generated = (
            compile_raw_spatial_contact_asset(
                source_path,
                analysis=analysis,
                project=base_project,
                attachments=scenario_attachments,
                is_cancelled=cancelled,
            )
        )
        base_project, scenario_attachments = _merge_raw_spatial_contact_asset(
            base_project,
            scenario_attachments,
            raw_spatial_manifest,
            raw_spatial_generated,
        )
        validate_project_raw_spatial_contact_asset_envelope(
            base_project,
            scenario_attachments,
        )
        report(
            99,
            "Compiled and bound exact raw SPD spatial-contact evidence",
        )
    decaps.sort(key=lambda item: item.refdes.casefold())
    nets = sorted(
        {item.net for item in base_project.rails},
        key=str.casefold,
    )
    net_colors = {
        net: _NET_PALETTE[index % len(_NET_PALETTE)]
        for index, net in enumerate(nets)
    }
    attachments = dict(scenario_attachments)
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
                *surface_connectivity_diagnostics,
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
