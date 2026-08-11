"""Build a v4 scenario topology plan from exact SPD/import evidence.

The importer owns all raw graph interpretation.  This adapter only translates
its signed v4 contact/destination records plus the persisted Distribution
eligibility into the typed scenario plan consumed by the numerical topology
compiler.  Missing destination evidence remains an explicit ineligible route;
there is no nearest-island or layer-order fallback.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Mapping, Sequence

from spd_decap_pi._core.solver.finite_via_layerwise import (
    FINITE_VIA_SURFACE_COMPILER,
    FINITE_VIA_SURFACE_SCHEMA,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationError,
    impedance_model_identity_sha256,
)

from .layerwise_scenario_topology import (
    LayerwiseScenarioTopologyError,
    compile_layerwise_scenario_network,
)
from .scenario import (
    DecapPadState,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioSpec,
    derive_shared_pad_current_components,
)
from .scenario_topology_plan import (
    RetargetRouteEvidenceSet,
    SourceContactEvidenceSet,
    SourceViaContactEvidence,
    TargetRouteDecision,
    compile_scenario_topology_plan,
    connection_analysis_evidence_sha256,
)
from .surface_certificate_asset import (
    SurfaceCertificateAssetError,
    hydrate_surface_certificate,
    is_validation_cache_safe_json_view,
)


_SURFACE_CONNECTIVITY_METADATA_KEY = "layerwise_surface_connectivity_certificate"
_SCENARIO_TERMINAL_TOPOLOGY_SCHEMA = "spd-scenario-decap-terminal-topology-v1"
_SCENARIO_COMPACT_VIEW_SCHEMA_V1 = "spd-layerwise-scenario-certificate-view-v1"
_SCENARIO_COMPACT_VIEW_SCHEMA = "spd-layerwise-scenario-certificate-view-v2"
_SCENARIO_COMPACT_VIEW_SCHEMAS = frozenset(
    {_SCENARIO_COMPACT_VIEW_SCHEMA_V1, _SCENARIO_COMPACT_VIEW_SCHEMA}
)
_SCENARIO_BINDING_PROJECTION_SCHEMA = (
    "spd-retarget-binding-runtime-projection-v1"
)
_SCENARIO_BINDING_RUNTIME_KEYS = frozenset(
    {
        "source_landing_key",
        "role",
        "target_layer",
        "status",
        "issues",
        "target_rail_id",
        "rail_id",
        "target_net",
        "destination_net",
        "net",
        "refdes",
        "via_template_id",
        "destination_vertex_id",
    }
)
_SCENARIO_RETARGET_KEYS = (
    "retarget_destination_bindings",
    "retarget_landing_xy_bindings",
)
_SCENARIO_COMPACT_VIEW_KEYS = frozenset(
    {
        "schema_version",
        "compiler_id",
        "source_sha256",
        "evidence_sha256",
        "status",
        "view_schema",
        "integrity_validation",
        "scenario_decap_terminal_topology",
        "finite_via_quotient",
        "view_evidence_sha256",
    }
)
_SCENARIO_BINDING_CACHE_LIMIT = 2
_SCENARIO_BINDING_CACHE: "OrderedDict[str, Any]" = OrderedDict()
_SCENARIO_BINDING_CACHE_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class _ValidatedScenarioViewEntry:
    """Strongly retain one immutable view and all external hash bindings."""

    view: object
    binding: tuple[str, ...]


_SCENARIO_VIEW_VALIDATION_CACHE: OrderedDict[
    int, _ValidatedScenarioViewEntry
] = OrderedDict()


@dataclass(frozen=True, slots=True)
class _CurrentPowerRoot:
    via_id: str
    source_refdes: str
    current_rail_id: str
    current_net: str
    moved: bool


@dataclass(frozen=True, slots=True)
class _ScenarioRootCandidateIndex:
    """Case-insensitive candidate buckets for per-root Scenario lookups."""

    clusters_by_refdes: Mapping[str, tuple[Any, ...]]
    decaps_by_refdes: Mapping[str, tuple[Any, ...]]
    connections_by_refdes: Mapping[str, tuple[Any, ...]]


def _json_plain(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        return {str(key): _json_plain(value) for key, value in payload.items()}
    if isinstance(payload, Sequence) and not isinstance(
        payload, (str, bytes, bytearray)
    ):
        return [_json_plain(value) for value in payload]
    return payload


def _canonical_sha256(payload: Any) -> str:
    try:
        encoded = json.dumps(
            _json_plain(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LayerSurfaceTerminationError(
            "SCENARIO_TOPOLOGY_EVIDENCE_INVALID",
            f"scenario topology evidence is not canonical JSON: {exc}",
        ) from exc
    return sha256(encoded).hexdigest()


def clear_layerwise_scenario_binding_cache() -> None:
    """Release the bounded Original/Tuned topology-binding cache."""

    with _SCENARIO_BINDING_CACHE_LOCK:
        _SCENARIO_BINDING_CACHE.clear()
        _SCENARIO_VIEW_VALIDATION_CACHE.clear()


def _scenario_binding_cache_identity(
    scenario: ScenarioSpec,
    project: Any,
    substrate: Any,
    template: Any,
    certificate: Mapping[str, Any],
) -> str:
    cap_models = [
        (
            str(model_id),
            impedance_model_identity_sha256(model),
        )
        for model_id, model in sorted(
            dict(getattr(template, "cap_models", {})).items(),
            key=lambda item: (str(item[0]).casefold(), str(item[0])),
        )
    ]
    via_templates = []
    for item in sorted(
        tuple(getattr(project, "via_templates", ())),
        key=lambda value: (
            str(getattr(value, "template_id", "")).casefold(),
            str(getattr(value, "template_id", "")),
        ),
    ):
        if hasattr(item, "model_dump"):
            via_templates.append(item.model_dump(mode="json"))
        else:
            via_templates.append(
                {
                    "template_id": str(getattr(item, "template_id", "")),
                    "impedance": list(getattr(item, "impedance", ())),
                    "loop_resistance_ohm": getattr(
                        item, "loop_resistance_ohm", None
                    ),
                    "loop_inductance_h": getattr(
                        item, "loop_inductance_h", None
                    ),
                }
            )
    return _canonical_sha256(
        {
            "identity_schema": "layerwise-scenario-binding-cache-v1",
            "scenario_design_fingerprint": scenario.design_fingerprint,
            "source_sha256": scenario.source.sha256.casefold(),
            "surface_connectivity_evidence_sha256": str(
                certificate.get("evidence_sha256", "")
            )
            .strip()
            .casefold(),
            "scenario_compact_view_evidence_sha256": str(
                certificate.get("view_evidence_sha256", "")
            )
            .strip()
            .casefold(),
            "base_substrate_identity_sha256": str(
                getattr(substrate, "substrate_identity_sha256", "")
            ).casefold(),
            "cap_models": cap_models,
            "via_templates": via_templates,
        }
    )


def _key(value: Any, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise LayerSurfaceTerminationError(
            "SCENARIO_TOPOLOGY_IDENTITY_MISSING",
            f"{label} must not be blank",
        )
    return text.casefold()


def _rows(value: Any, *, label: str) -> tuple[Mapping[str, Any], ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise LayerSurfaceTerminationError(
            "SCENARIO_TOPOLOGY_EVIDENCE_INVALID",
            f"{label} must be a sequence of mapping rows",
        )
    return tuple(value)


def _validate_v4_certificate_uncached(
    scenario: ScenarioSpec,
    project: Any,
    substrate: Any,
    attachments: Mapping[str, bytes],
) -> Mapping[str, Any]:
    compact_view = getattr(substrate, "scenario_certificate_view", None)
    if isinstance(compact_view, Mapping):
        if (
            compact_view.get("view_schema")
            not in _SCENARIO_COMPACT_VIEW_SCHEMAS
            or compact_view.get("integrity_validation")
            != "full-canonical-certificate-verified"
            or set(compact_view) != _SCENARIO_COMPACT_VIEW_KEYS
        ):
            raise LayerSurfaceTerminationError(
                "SURFACE_CONNECTIVITY_CERTIFICATE_VIEW_INVALID",
                "base substrate carries an unsupported scenario certificate view",
            )
        observed_view_sha256 = str(
            compact_view.get("view_evidence_sha256", "")
        ).strip().casefold()
        payload = {
            key: compact_view[key]
            for key in _SCENARIO_COMPACT_VIEW_KEYS
            if key != "view_evidence_sha256"
        }
        if (
            len(observed_view_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in observed_view_sha256
            )
            or _canonical_sha256(payload) != observed_view_sha256
        ):
            raise LayerSurfaceTerminationError(
                "SURFACE_CONNECTIVITY_CERTIFICATE_VIEW_INTEGRITY_FAILED",
                "base substrate scenario certificate view hash differs from its payload",
            )
        certificate = compact_view
    else:
        try:
            certificate = hydrate_surface_certificate(project, attachments)
        except SurfaceCertificateAssetError as exc:
            raise LayerSurfaceTerminationError(exc.code, str(exc)) from exc
    if not isinstance(certificate, Mapping):
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_MISSING",
            "reimport the SPD to retain the v4 finite-Via topology certificate",
        )
    if (
        certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or certificate.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
    ):
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_UNSUPPORTED",
            "scenario topology rebuilding requires the v4 finite-Via certificate",
        )
    source_sha = str(certificate.get("source_sha256", "")).strip().casefold()
    if source_sha != scenario.source.sha256.casefold():
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_SOURCE_MISMATCH",
            "v4 topology certificate is not bound to this Scenario SPD source",
        )
    observed = str(certificate.get("evidence_sha256", "")).strip().casefold()
    # hydrate_surface_certificate has already checked the canonical payload and
    # its outer evidence hash with a streaming encoder.  Re-serializing a
    # multi-GiB quotient here would duplicate the certificate solely to repeat
    # the same check.
    if len(observed) != 64 or any(
        character not in "0123456789abcdef" for character in observed
    ):
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INTEGRITY_FAILED",
            "v4 topology certificate has no valid evidence SHA-256",
        )
    metadata = getattr(project, "metadata", {})
    spd_import = metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    retained = (
        spd_import.get(_SURFACE_CONNECTIVITY_METADATA_KEY)
        if isinstance(spd_import, Mapping)
        else None
    )
    retained_evidence = (
        str(retained.get("evidence_sha256", "")).strip().casefold()
        if isinstance(retained, Mapping)
        else ""
    )
    if retained_evidence and retained_evidence != observed:
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_VIEW_MISMATCH",
            "scenario certificate view differs from retained project metadata",
        )
    if str(certificate.get("status", "")).casefold() != "complete":
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INCOMPLETE",
            "v4 topology certificate is not complete",
        )
    if (
        isinstance(compact_view, Mapping)
        and compact_view.get("view_schema") == _SCENARIO_COMPACT_VIEW_SCHEMA
    ):
        _validate_projected_scenario_certificate(certificate)
    provenance_sha = str(
        getattr(substrate, "provenance", {}).get(
            "surface_connectivity_evidence_sha256", ""
        )
    ).casefold()
    if provenance_sha and provenance_sha != observed:
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_SUBSTRATE_MISMATCH",
            "base substrate and scenario adapter use different v4 certificates",
        )
    return certificate


def _scenario_view_validation_binding(
    scenario: ScenarioSpec,
    project: Any,
    substrate: Any,
    view: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return the cheap values consumed by compact Scenario-view validation."""

    metadata = getattr(project, "metadata", {})
    spd_import = metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    retained = (
        spd_import.get(_SURFACE_CONNECTIVITY_METADATA_KEY)
        if isinstance(spd_import, Mapping)
        else None
    )
    provenance = getattr(substrate, "provenance", {})
    return (
        str(view.get("view_schema", "")).strip(),
        str(view.get("integrity_validation", "")).strip(),
        str(view.get("schema_version", "")).strip(),
        str(view.get("compiler_id", "")).strip(),
        str(view.get("source_sha256", "")).strip().casefold(),
        str(view.get("evidence_sha256", "")).strip().casefold(),
        str(view.get("view_evidence_sha256", "")).strip().casefold(),
        str(view.get("status", "")).strip().casefold(),
        scenario.source.sha256.strip().casefold(),
        str(retained.get("evidence_sha256", "")).strip().casefold()
        if isinstance(retained, Mapping)
        else "",
        str(getattr(substrate, "substrate_identity_sha256", ""))
        .strip()
        .casefold(),
        str(provenance.get("surface_connectivity_evidence_sha256", ""))
        .strip()
        .casefold()
        if isinstance(provenance, Mapping)
        else "",
    )


def _validated_v4_certificate(
    scenario: ScenarioSpec,
    project: Any,
    substrate: Any,
    attachments: Mapping[str, bytes],
) -> Mapping[str, Any]:
    """Validate a compact frozen view once per exact source/hash binding."""

    compact_view = getattr(substrate, "scenario_certificate_view", None)
    if not isinstance(
        compact_view, Mapping
    ) or not is_validation_cache_safe_json_view(compact_view):
        # Attachment hydration retains its own content-addressed cache.  An
        # ad-hoc or mutable compact mapping must still pass the complete hash
        # gate on every call so nested mutation can never reuse prior trust.
        return _validate_v4_certificate_uncached(
            scenario, project, substrate, attachments
        )
    binding = _scenario_view_validation_binding(
        scenario, project, substrate, compact_view
    )
    identity = id(compact_view)
    with _SCENARIO_BINDING_CACHE_LOCK:
        cached = _SCENARIO_VIEW_VALIDATION_CACHE.get(identity)
        if (
            cached is not None
            and cached.view is compact_view
            and cached.binding == binding
        ):
            _SCENARIO_VIEW_VALIDATION_CACHE.move_to_end(identity)
            return compact_view
        validated = _validate_v4_certificate_uncached(
            scenario, project, substrate, attachments
        )
        # Keeping the wrapper alive prevents object-id reuse.  The binding
        # separately detects a changed retained/source/view/substrate digest.
        _SCENARIO_VIEW_VALIDATION_CACHE[identity] = _ValidatedScenarioViewEntry(
            view=compact_view,
            binding=binding,
        )
        _SCENARIO_VIEW_VALIDATION_CACHE.move_to_end(identity)
        while (
            len(_SCENARIO_VIEW_VALIDATION_CACHE)
            > _SCENARIO_BINDING_CACHE_LIMIT
        ):
            _SCENARIO_VIEW_VALIDATION_CACHE.popitem(last=False)
        return validated


def _physical_landing_index(
    analysis: Any,
) -> dict[str, tuple[str, str, Any]]:
    result: dict[str, tuple[str, str, Any]] = {}
    for connection in analysis.connections.values():
        for role, landings in (
            ("PWR", connection.power_vias),
            ("GND", connection.ground_vias),
        ):
            for landing in landings:
                key = landing.via_id.casefold()
                previous = result.setdefault(
                    key, (connection.refdes, role, landing)
                )
                if previous[1] != role or previous[2] != landing:
                    raise LayerSurfaceTerminationError(
                        "SOURCE_VIA_OWNERSHIP_CONFLICT",
                        f"physical Via {landing.via_id!r} has conflicting terminal evidence",
                    )
    return result


def _source_contact_evidence(
    scenario: ScenarioSpec,
    certificate: Mapping[str, Any],
) -> SourceContactEvidenceSet:
    analysis = scenario.connection_analysis
    if analysis is None:
        raise LayerSurfaceTerminationError(
            "CONNECTION_ANALYSIS_MISSING", "scenario has no source connection analysis"
        )
    topology = certificate.get("scenario_decap_terminal_topology")
    if (
        not isinstance(topology, Mapping)
        or topology.get("schema_version") != _SCENARIO_TERMINAL_TOPOLOGY_SCHEMA
        or str(topology.get("status", "")).casefold() != "complete"
    ):
        raise LayerSurfaceTerminationError(
            "SCENARIO_TERMINAL_TOPOLOGY_INCOMPLETE",
            "v4 certificate lacks complete conditional decap contacts/gap proof",
        )
    landing_index = _physical_landing_index(analysis)
    contacts: list[SourceViaContactEvidence] = []
    seen_vias: set[str] = set()
    for row in _rows(topology.get("conditional_contacts"), label="conditional contacts"):
        raw_landing_key = row.get("landing_key")
        if (
            not isinstance(raw_landing_key, Sequence)
            or isinstance(raw_landing_key, (str, bytes, bytearray))
            or len(raw_landing_key) != 2
        ):
            raise LayerSurfaceTerminationError(
                "SOURCE_CONTACT_EVIDENCE_INVALID",
                "a conditional contact has an invalid landing key",
            )
        via_key = _key(raw_landing_key[0], label="contact Via ID")
        source = landing_index.get(via_key)
        if source is None or via_key in seen_vias:
            raise LayerSurfaceTerminationError(
                "SOURCE_CONTACT_INVENTORY_CONFLICT",
                f"conditional contact Via {raw_landing_key[0]!r} is absent or duplicated",
            )
        refdes, role, landing = source
        if (
            str(row.get("status", "")).casefold() != "complete"
            or tuple(str(item).casefold() for item in row.get("issues", ()))
            or str(row.get("refdes", "")).casefold() != refdes.casefold()
            or str(row.get("role", "")).casefold()
            != ("power" if role == "PWR" else "ground")
            or str(raw_landing_key[1]).casefold()
            != landing.endpoint_node_id.casefold()
        ):
            raise LayerSurfaceTerminationError(
                "SOURCE_CONTACT_EVIDENCE_INVALID",
                f"conditional contact for Via {landing.via_id!r} conflicts with source evidence",
            )
        exposed = str(row.get("landing_vertex_id") or "").strip()
        cut_link = str(row.get("first_via_edge_id") or "").strip()
        cut_owner = str(row.get("first_via_owner_id") or "").strip()
        contact_id = str(row.get("contact_id") or "").strip()
        if (
            not exposed
            or not cut_link
            or not contact_id
            or cut_owner.casefold() != f"via:{landing.via_id}".casefold()
        ):
            raise LayerSurfaceTerminationError(
                "SOURCE_CONTACT_EVIDENCE_INVALID",
                f"conditional contact for Via {landing.via_id!r} lacks exact cut ownership",
            )
        contacts.append(
            SourceViaContactEvidence(
                via_id=landing.via_id,
                terminal=role,  # type: ignore[arg-type]
                source_refdes=refdes,
                exposed_node_id=exposed,
                contact_owner_id=contact_id,
                cut_link_id=cut_link,
                cut_owner_id=cut_owner,
                contact_evidence_sha256=_canonical_sha256(dict(row)),
            )
        )
        seen_vias.add(via_key)
    if seen_vias != set(landing_index):
        missing = sorted(set(landing_index) - seen_vias)
        raise LayerSurfaceTerminationError(
            "SOURCE_CONTACT_INVENTORY_INCOMPLETE",
            f"v4 conditional contact map is missing physical Via(s): {missing[:8]}",
        )
    return SourceContactEvidenceSet.create(
        source_sha256=scenario.source.sha256,
        connection_evidence_sha256=connection_analysis_evidence_sha256(analysis),
        finite_route_certificate_sha256=str(certificate["evidence_sha256"]),
        contacts=contacts,
    )


def _current_power_roots(scenario: ScenarioSpec) -> tuple[_CurrentPowerRoot, ...]:
    analysis = scenario.connection_analysis
    assert analysis is not None
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    connection_by_key = {
        item.refdes.casefold(): item for item in analysis.connections.values()
    }
    result: dict[str, _CurrentPowerRoot] = {}
    clustered: set[str] = set()
    for cluster in analysis.clusters:
        member_decaps = {
            refdes: decap_by_key[refdes.casefold()]
            for refdes in cluster.member_refdes
        }
        member_connections = {
            refdes: connection_by_key[refdes.casefold()]
            for refdes in cluster.member_refdes
        }
        clustered.update(refdes.casefold() for refdes in cluster.member_refdes)
        derivation = derive_shared_pad_current_components(
            cluster,
            member_decaps,
            member_connections,
            analysis_version=analysis.version,
        )
        for component in derivation.components:
            moved = any(
                member_decaps[refdes].current_rail_id.casefold()
                != member_decaps[refdes].source_rail_id.casefold()
                or member_decaps[refdes].current_net.casefold()
                != member_decaps[refdes].source_net.casefold()
                for refdes in component.member_refdes
            )
            source_by_via = {
                landing.via_id.casefold(): connection.refdes
                for connection in member_connections.values()
                for landing in connection.power_vias
            }
            for landing in component.power_vias:
                key = landing.via_id.casefold()
                root = _CurrentPowerRoot(
                    via_id=landing.via_id,
                    source_refdes=source_by_via[key],
                    current_rail_id=component.current_rail_id,
                    current_net=component.current_net,
                    moved=moved,
                )
                previous = result.setdefault(key, root)
                if previous != root:
                    raise LayerSurfaceTerminationError(
                        "CURRENT_POWER_ROOT_CONFLICT",
                        f"physical PWR Via {landing.via_id!r} belongs to multiple current components",
                    )
    for key, decap in decap_by_key.items():
        if key in clustered or decap.pad_state == DecapPadState.ISOLATION_GAP:
            continue
        connection: ScenarioDecapConnection = connection_by_key[key]
        for landing in connection.power_vias:
            root = _CurrentPowerRoot(
                via_id=landing.via_id,
                source_refdes=decap.refdes,
                current_rail_id=decap.current_rail_id,
                current_net=decap.current_net,
                moved=(
                    decap.current_rail_id.casefold()
                    != decap.source_rail_id.casefold()
                    or decap.current_net.casefold() != decap.source_net.casefold()
                ),
            )
            previous = result.setdefault(landing.via_id.casefold(), root)
            if previous != root:
                raise LayerSurfaceTerminationError(
                    "CURRENT_POWER_ROOT_CONFLICT",
                    f"physical PWR Via {landing.via_id!r} is duplicated",
                )
    return tuple(result[key] for key in sorted(result))


def _casefold_mapping_item(mapping: Mapping[str, Any], identity: str) -> Any | None:
    key = identity.casefold()
    matches = [item for raw, item in mapping.items() if str(raw).casefold() == key]
    return matches[0] if len(matches) == 1 else None


def _scenario_root_candidate_index(
    scenario: ScenarioSpec,
) -> _ScenarioRootCandidateIndex:
    """Bucket the immutable Scenario inventories once before root iteration.

    Large boards can have thousands of clusters, decaps, connections, and
    current PWR roots.  The lookup helpers retain their original filtering and
    first-match behavior, but receive only the case-insensitive REFDES bucket
    instead of re-scanning each complete inventory for every root.
    """

    analysis = scenario.connection_analysis
    assert analysis is not None
    cluster_buckets: dict[str, list[Any]] = {}
    for cluster in analysis.clusters:
        for refdes in cluster.member_refdes:
            cluster_buckets.setdefault(str(refdes).casefold(), []).append(cluster)
    decap_buckets: dict[str, list[Any]] = {}
    for decap in scenario.decaps:
        decap_buckets.setdefault(str(decap.refdes).casefold(), []).append(decap)
    connection_buckets: dict[str, list[Any]] = {}
    for connection in analysis.connections.values():
        connection_buckets.setdefault(
            str(connection.refdes).casefold(), []
        ).append(connection)
    return _ScenarioRootCandidateIndex(
        clusters_by_refdes={
            key: tuple(items) for key, items in cluster_buckets.items()
        },
        decaps_by_refdes={
            key: tuple(items) for key, items in decap_buckets.items()
        },
        connections_by_refdes={
            key: tuple(items) for key, items in connection_buckets.items()
        },
    )


def _eligibility_for_root(
    scenario: ScenarioSpec,
    root: _CurrentPowerRoot,
    *,
    candidate_index: _ScenarioRootCandidateIndex | None = None,
) -> Any | None:
    analysis = scenario.connection_analysis
    assert analysis is not None
    refdes_key = root.source_refdes.casefold()
    cluster_candidates = (
        analysis.clusters
        if candidate_index is None
        else candidate_index.clusters_by_refdes.get(refdes_key, ())
    )
    for cluster in cluster_candidates:
        if root.source_refdes.casefold() not in {
            item.casefold() for item in cluster.member_refdes
        }:
            continue
        per_via = _casefold_mapping_item(cluster.via_eligibility, root.via_id)
        return (
            _casefold_mapping_item(per_via, root.current_rail_id)
            if isinstance(per_via, Mapping)
            else None
        )
    decap_candidates = (
        scenario.decaps
        if candidate_index is None
        else candidate_index.decaps_by_refdes.get(refdes_key, ())
    )
    decap = next(
        item for item in decap_candidates if item.refdes.casefold() == refdes_key
    )
    return _casefold_mapping_item(decap.eligibility, root.current_rail_id)


def _landing_for_root(
    scenario: ScenarioSpec,
    root: _CurrentPowerRoot,
    *,
    candidate_index: _ScenarioRootCandidateIndex | None = None,
) -> Any:
    analysis = scenario.connection_analysis
    assert analysis is not None
    refdes_key = root.source_refdes.casefold()
    connection_candidates = (
        analysis.connections.values()
        if candidate_index is None
        else candidate_index.connections_by_refdes.get(refdes_key, ())
    )
    connection = next(
        item
        for item in connection_candidates
        if item.refdes.casefold() == refdes_key
    )
    return next(
        item
        for item in connection.power_vias
        if item.via_id.casefold() == root.via_id.casefold()
    )


def _destination_binding(
    rows: Sequence[Mapping[str, Any]],
    *,
    root: _CurrentPowerRoot,
    landing: Any,
    target_layer: str,
    via_template_id: str,
) -> Mapping[str, Any] | None:
    candidates: list[tuple[int, Mapping[str, Any]]] = []
    landing_key = (landing.via_id.casefold(), landing.endpoint_node_id.casefold())
    for row in rows:
        raw_source = row.get("source_landing_key")
        if (
            not isinstance(raw_source, Sequence)
            or isinstance(raw_source, (str, bytes, bytearray))
            or len(raw_source) != 2
            or tuple(str(item).casefold() for item in raw_source) != landing_key
            or str(row.get("role", "")).casefold() != "power"
            or str(row.get("target_layer", "")).casefold()
            != target_layer.casefold()
            or str(row.get("status", "")).casefold() != "complete"
            or tuple(row.get("issues", ()))
        ):
            continue
        explicit_rail = str(
            row.get("target_rail_id", row.get("rail_id", ""))
        ).strip()
        explicit_net = str(
            row.get(
                "target_net",
                row.get("destination_net", row.get("net", "")),
            )
        ).strip()
        if explicit_rail and explicit_rail.casefold() != root.current_rail_id.casefold():
            continue
        if explicit_net and explicit_net.casefold() != root.current_net.casefold():
            continue
        explicit_refdes = str(row.get("refdes", "")).strip()
        explicit_template = str(row.get("via_template_id", "")).strip()
        if explicit_refdes and explicit_refdes.casefold() != root.source_refdes.casefold():
            continue
        if (
            explicit_template
            and explicit_template.casefold() != via_template_id.casefold()
        ):
            continue
        score = (
            int(bool(explicit_rail)) * 4
            + int(bool(explicit_net)) * 2
            + int(bool(explicit_refdes))
        )
        candidates.append((score, row))
    if not candidates:
        return None
    best_score = max(item[0] for item in candidates)
    best = [row for score, row in candidates if score == best_score]
    vertex_ids = {str(row.get("destination_vertex_id", "")).strip() for row in best}
    if len(best) != 1 and len(vertex_ids) != 1:
        raise LayerSurfaceTerminationError(
            "RETARGET_DESTINATION_AMBIGUOUS",
            f"PWR Via {root.via_id!r} has multiple target vertices for rail {root.current_rail_id!r}",
        )
    return best[0]


def _destination_binding_index(
    rows: Sequence[Mapping[str, Any]],
) -> Mapping[tuple[str, str, str, str], tuple[Mapping[str, Any], ...]]:
    """Bucket exact destination rows by their immutable lookup identity.

    A production board can carry more than 220k exact landing bindings and
    about 12k current PWR roots.  Re-scanning the complete manifest for every
    root turns a linear validation into billions of Python comparisons.  The
    same base predicates used by ``_destination_binding`` are applied once
    here; rail/NET/REFDES/template specificity and ambiguity remain checked by
    that function within the matching bucket.
    """

    buckets: dict[
        tuple[str, str, str, str], list[Mapping[str, Any]]
    ] = {}
    for row in rows:
        raw_source = row.get("source_landing_key")
        if (
            not isinstance(raw_source, Sequence)
            or isinstance(raw_source, (str, bytes, bytearray))
            or len(raw_source) != 2
            or str(row.get("status", "")).casefold() != "complete"
            or tuple(row.get("issues", ()))
        ):
            continue
        source_key = tuple(str(item).casefold() for item in raw_source)
        role = str(row.get("role", "")).casefold()
        target_layer = str(row.get("target_layer", "")).casefold()
        buckets.setdefault(
            (source_key[0], source_key[1], role, target_layer), []
        ).append(row)
    return {
        key: tuple(bucket)
        for key, bucket in buckets.items()
    }


def _scenario_projection_evidence_sha256(
    row: Mapping[str, Any],
    *,
    source_sha256: str,
    certificate_evidence_sha256: str,
) -> str:
    source = str(source_sha256).strip().casefold()
    certificate_evidence = str(certificate_evidence_sha256).strip().casefold()
    for value, label in (
        (source, "source"),
        (certificate_evidence, "certificate evidence"),
    ):
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise LayerSurfaceTerminationError(
                "RETARGET_DESTINATION_INTEGRITY_FAILED",
                f"retarget projection {label} SHA-256 is invalid",
            )
    return _canonical_sha256(
        {
            "schema": _SCENARIO_BINDING_PROJECTION_SCHEMA,
            "source_sha256": source,
            "certificate_evidence_sha256": certificate_evidence,
            "binding": row,
        }
    )


def _scenario_binding_runtime_identity_is_valid(row: Mapping[str, Any]) -> bool:
    source_landing_key = row.get("source_landing_key")
    return bool(
        isinstance(source_landing_key, Sequence)
        and not isinstance(source_landing_key, (str, bytes, bytearray))
        and len(source_landing_key) == 2
        and all(str(item).strip() for item in source_landing_key)
        and str(row.get("role", "")).strip().casefold() in {"power", "ground"}
        and str(row.get("target_layer", "")).strip()
        and str(row.get("destination_vertex_id", "")).strip()
    )


def _verified_binding_sha256(
    row: Mapping[str, Any],
    *,
    source_sha256: str = "",
    certificate_evidence_sha256: str = "",
) -> str:
    observed = str(row.get("binding_evidence_sha256", "")).strip().casefold()
    if "projection_evidence_sha256" in row:
        projected_sha = str(
            row.get("projection_evidence_sha256", "")
        ).strip().casefold()
        if (
            len(observed) != 64
            or any(character not in "0123456789abcdef" for character in observed)
            or len(projected_sha) != 64
            or any(
                character not in "0123456789abcdef"
                for character in projected_sha
            )
        ):
            raise LayerSurfaceTerminationError(
                "RETARGET_DESTINATION_INTEGRITY_FAILED",
                "retarget destination projection has no valid inner SHA-256",
            )
        unsigned_projection = {
            str(key): value
            for key, value in row.items()
            if str(key) != "projection_evidence_sha256"
        }
        expected_projection = _scenario_projection_evidence_sha256(
            unsigned_projection,
            source_sha256=source_sha256,
            certificate_evidence_sha256=certificate_evidence_sha256,
        )
        if projected_sha != expected_projection:
            raise LayerSurfaceTerminationError(
                "RETARGET_DESTINATION_INTEGRITY_FAILED",
                "retarget destination projection SHA-256 does not match its runtime payload",
            )
        return observed
    if not observed:
        return _canonical_sha256(dict(row))
    unsigned = {
        str(key): value
        for key, value in row.items()
        if str(key) != "binding_evidence_sha256"
    }
    expected = _canonical_sha256(unsigned)
    if observed != expected:
        raise LayerSurfaceTerminationError(
            "RETARGET_DESTINATION_INTEGRITY_FAILED",
            "retarget destination binding SHA-256 does not match its payload",
        )
    return observed


def _validate_projected_scenario_certificate(
    certificate: Mapping[str, Any],
) -> None:
    topology = certificate.get("scenario_decap_terminal_topology")
    quotient = certificate.get("finite_via_quotient")
    if not isinstance(topology, Mapping) or not isinstance(quotient, Mapping):
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_VIEW_INVALID",
            "projected scenario certificate has no topology/quotient mapping",
        )
    topology_keys = frozenset(
        {
            "schema_version",
            "status",
            "conditional_contacts",
            "retarget_landing_xy_coverage",
            *_SCENARIO_RETARGET_KEYS,
        }
    )
    if not set(topology).issubset(topology_keys) or not set(quotient).issubset(
        {"schema_version", "status"}
    ):
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_VIEW_INVALID",
            "projected scenario certificate retains unsupported topology fields",
        )
    allowed_row_keys = _SCENARIO_BINDING_RUNTIME_KEYS | {
        "binding_evidence_sha256",
        "projection_evidence_sha256",
    }
    source_sha256 = str(certificate.get("source_sha256", ""))
    certificate_evidence_sha256 = str(certificate.get("evidence_sha256", ""))
    for key in _SCENARIO_RETARGET_KEYS:
        if key not in topology:
            continue
        for row in _rows(topology[key], label=key.replace("_", " ")):
            issues = row.get("issues")
            if (
                not set(row).issubset(allowed_row_keys)
                or "projection_evidence_sha256" not in row
                or str(row.get("status", "")).strip().casefold() != "complete"
                or not isinstance(issues, Sequence)
                or isinstance(issues, (str, bytes, bytearray))
                or bool(issues)
                or not _scenario_binding_runtime_identity_is_valid(row)
            ):
                raise LayerSurfaceTerminationError(
                    "SURFACE_CONNECTIVITY_CERTIFICATE_VIEW_INVALID",
                    "projected retarget row is incomplete or unsupported",
                )
            _verified_binding_sha256(
                row,
                source_sha256=source_sha256,
                certificate_evidence_sha256=certificate_evidence_sha256,
            )


def _retarget_route_evidence(
    scenario: ScenarioSpec,
    project: Any,
    substrate: Any,
    certificate: Mapping[str, Any],
) -> RetargetRouteEvidenceSet:
    analysis = scenario.connection_analysis
    assert analysis is not None
    quotient = certificate.get("finite_via_quotient")
    topology = certificate.get("scenario_decap_terminal_topology")
    if not isinstance(quotient, Mapping) or not isinstance(topology, Mapping):
        raise LayerSurfaceTerminationError(
            "RETARGET_DESTINATION_EVIDENCE_MISSING",
            "v4 certificate has no finite quotient/scenario destination manifest",
        )
    path_binding_rows = _rows(
        topology.get(
            "retarget_destination_bindings",
            quotient.get("retarget_destination_bindings", ()),
        ),
        label="retarget destination bindings",
    )
    landing_xy_binding_rows = _rows(
        topology.get(
            "retarget_landing_xy_bindings",
            quotient.get("retarget_landing_xy_bindings", ()),
        ),
        label="retarget landing-XY bindings",
    )
    binding_rows = (*landing_xy_binding_rows, *path_binding_rows)
    binding_index = _destination_binding_index(binding_rows)
    root_candidate_index = _scenario_root_candidate_index(scenario)
    templates = {
        item.template_id.casefold(): item
        for item in getattr(project, "via_templates", ())
    }
    network_nodes = {
        item.casefold() for item in getattr(substrate.network, "surface_node_ids", ())
    }
    decisions: list[TargetRouteDecision] = []
    for root in _current_power_roots(scenario):
        if not root.moved:
            decisions.append(
                TargetRouteDecision(
                    via_id=root.via_id,
                    rail_id=root.current_rail_id,
                    eligible=False,
                    reason="source PWR contact is retained",
                )
            )
            continue
        eligibility = _eligibility_for_root(
            scenario,
            root,
            candidate_index=root_candidate_index,
        )
        if eligibility is None or not bool(getattr(eligibility, "allowed", False)):
            decisions.append(
                TargetRouteDecision(
                    via_id=root.via_id,
                    rail_id=root.current_rail_id,
                    eligible=False,
                    reason=(
                        "current-rail exact destination eligibility is missing or blocked"
                    ),
                )
            )
            continue
        if (
            str(getattr(eligibility, "rail_id", "")).casefold()
            != root.current_rail_id.casefold()
            or str(getattr(eligibility, "net", "")).casefold()
            != root.current_net.casefold()
        ):
            raise LayerSurfaceTerminationError(
                "RETARGET_ELIGIBILITY_CONFLICT",
                f"PWR Via {root.via_id!r} eligibility conflicts with its current rail/NET",
            )
        target_layer = str(
            getattr(eligibility, "destination_pwr_layer", None)
            or getattr(eligibility, "pwr_layer", "")
        ).strip()
        template_id = str(getattr(eligibility, "via_template_id", "") or "").strip()
        landing = _landing_for_root(
            scenario,
            root,
            candidate_index=root_candidate_index,
        )
        landing_key = (
            landing.via_id.casefold(),
            landing.endpoint_node_id.casefold(),
            "power",
            target_layer.casefold(),
        )
        binding = _destination_binding(
            binding_index.get(landing_key, ()),
            root=root,
            landing=landing,
            target_layer=target_layer,
            via_template_id=template_id,
        )
        destination_vertex = (
            str(binding.get("destination_vertex_id", "")).strip()
            if binding is not None
            else ""
        )
        template = templates.get(template_id.casefold()) if template_id else None
        if (
            binding is None
            or not destination_vertex
            or destination_vertex.casefold() not in network_nodes
            or template is None
        ):
            decisions.append(
                TargetRouteDecision(
                    via_id=root.via_id,
                    rail_id=root.current_rail_id,
                    eligible=False,
                    reason=(
                        "exact destination quotient vertex or Via template is unavailable"
                    ),
                )
            )
            continue
        if getattr(template, "impedance", ()):
            raise LayerSurfaceTerminationError(
                "RETARGET_SAMPLED_VIA_UNSUPPORTED",
                f"Via template {template_id!r} is sampled; the finite scenario route requires explicit R/L",
            )
        resistance = 0.5 * float(getattr(template, "loop_resistance_ohm", 0.0))
        inductance = 0.5 * float(getattr(template, "loop_inductance_h", 0.0))
        if resistance < 0.0 or inductance < 0.0 or (
            resistance == 0.0 and inductance == 0.0
        ):
            raise LayerSurfaceTerminationError(
                "RETARGET_VIA_MODEL_INVALID",
                f"Via template {template_id!r} has no passive nonzero loop R/L",
            )
        binding_sha = _verified_binding_sha256(
            binding,
            source_sha256=str(certificate.get("source_sha256", "")),
            certificate_evidence_sha256=str(
                certificate.get("evidence_sha256", "")
            ),
        )
        route_evidence = _canonical_sha256(
            {
                "binding_evidence_sha256": binding_sha,
                "source_via_id": root.via_id.casefold(),
                "current_rail_id": root.current_rail_id.casefold(),
                "target_net": root.current_net.casefold(),
                "target_layer": target_layer.casefold(),
                "destination_vertex_id": destination_vertex.casefold(),
                "via_template_id": template_id.casefold(),
                "terminal_impedance_scale": 0.5,
                "resistance_ohm": resistance,
                "inductance_h": inductance,
            }
        )
        decisions.append(
            TargetRouteDecision(
                via_id=root.via_id,
                rail_id=root.current_rail_id,
                eligible=True,
                target_node_id=destination_vertex,
                route_id=(
                    f"scenario-retarget:{root.via_id}:{root.current_rail_id}"
                ),
                route_owner_id=f"via:{root.via_id}",
                target_net=root.current_net,
                target_layer=target_layer,
                via_template_id=template_id,
                resistance_ohm=resistance,
                inductance_h=inductance,
                route_evidence_sha256=route_evidence,
            )
        )
    return RetargetRouteEvidenceSet.create(
        source_sha256=scenario.source.sha256,
        connection_evidence_sha256=connection_analysis_evidence_sha256(analysis),
        finite_route_certificate_sha256=str(certificate["evidence_sha256"]),
        decisions=decisions,
    )


def compile_v4_layerwise_scenario_binding(
    scenario: ScenarioSpec,
    project: Any,
    substrate: Any,
    template: Any,
    *,
    attachments: Mapping[str, bytes] | None = None,
) -> Any:
    """Compile a v4 certificate + saved scenario into one global-MNA binding."""

    if scenario.connection_analysis is None:
        raise LayerSurfaceTerminationError(
            "CONNECTION_ANALYSIS_MISSING", "scenario has no source connection analysis"
        )
    certificate = _validated_v4_certificate(
        scenario, project, substrate, dict(attachments or {})
    )
    cache_key = _scenario_binding_cache_identity(
        scenario, project, substrate, template, certificate
    )
    # Plan/network compilation touches the complete mutable board state. Keep
    # it under one lock so concurrent rail preflights cannot materialize the
    # same multi-million-node binding more than once.
    with _SCENARIO_BINDING_CACHE_LOCK:
        cached = _SCENARIO_BINDING_CACHE.get(cache_key)
        if cached is not None:
            _SCENARIO_BINDING_CACHE.move_to_end(cache_key)
            return cached
        source_contacts = _source_contact_evidence(scenario, certificate)
        retarget_routes = _retarget_route_evidence(
            scenario, project, substrate, certificate
        )
        try:
            plan = compile_scenario_topology_plan(
                decap_by_refdes={item.refdes: item for item in scenario.decaps},
                connection_analysis=scenario.connection_analysis,
                source_contacts=source_contacts,
                retarget_routes=retarget_routes,
            )
            binding = compile_layerwise_scenario_network(
                base_substrate=substrate,
                plan=plan,
                cap_models=dict(getattr(template, "cap_models", {})),
            )
        except LayerwiseScenarioTopologyError as exc:
            raise LayerSurfaceTerminationError(exc.code, str(exc)) from exc
        _SCENARIO_BINDING_CACHE[cache_key] = binding
        _SCENARIO_BINDING_CACHE.move_to_end(cache_key)
        while len(_SCENARIO_BINDING_CACHE) > _SCENARIO_BINDING_CACHE_LIMIT:
            _SCENARIO_BINDING_CACHE.popitem(last=False)
        return binding


__all__ = [
    "clear_layerwise_scenario_binding_cache",
    "compile_v4_layerwise_scenario_binding",
]
