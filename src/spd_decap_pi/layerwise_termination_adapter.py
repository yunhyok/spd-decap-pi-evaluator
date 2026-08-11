"""Bind a saved Scenario's mounted decaps to physical layer-surface nodes.

This is the application/domain adapter between :mod:`spd_decap_pi.scenario`
and the numerical termination compiler.  The numerical kernel deliberately
does not know about mutable rail assignments or Distribution state; this
module resolves those identities once, from the exact Scenario that is being
evaluated, and returns a hash-bound compiled termination manifest.

The adapter never reads a PowerSI result and never fits a parameter.  The
explicit low-level v3 diagnostic compiler below still binds exact source
``(Via ID, external endpoint Node)`` landings to retained artwork-island nodes.
Production Evaluation, however, accepts only the v4 finite-Via certificate so
Distribution edits can rebuild and bind the full topology atomically; it never
falls back to that manifest-only diagnostic path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
import json
from math import isclose, isfinite
from typing import Any

from spd_decap_pi._core.models.impedance import ImpedanceModel
from spd_decap_pi._core.solver.finite_via_layerwise import (
    FINITE_VIA_SURFACE_SCHEMA,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    CompiledLayerSurfaceTerminationManifest,
    LayerSurfaceTerminationError,
    impedance_model_identity_sha256,
)

from .scenario import (
    DecapConnectionKind,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioSpec,
    ScenarioViaLanding,
    SharedPadCluster,
)
from .scenario_termination_manifest import compile_scenario_termination_manifest


_SURFACE_CONNECTIVITY_SCHEMA = "spd-layer-surface-connectivity-v3"
_SURFACE_CONNECTIVITY_COMPILER = (
    "powersi-same-layer-trace-island-terminal-via-pair-v3"
)
_SURFACE_CONNECTIVITY_METADATA_KEY = "layerwise_surface_connectivity_certificate"


def _key(value: Any, *, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise LayerSurfaceTerminationError(
            "SCENARIO_TERMINATION_IDENTITY_MISSING",
            f"{name} must not be blank",
        )
    return text.casefold()


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _metadata_sha256(payload: Any) -> str:
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INVALID",
            f"surface-connectivity certificate is not canonical JSON: {exc}",
        ) from exc
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _ExactTerminalIslandBinding:
    via_id: str
    external_endpoint_node_id: str
    internal_endpoint_node_id: str
    net: str
    layer: str
    island_node_id: str


@dataclass(frozen=True, slots=True)
class _CertifiedSurfaceComponent:
    component_id: str
    net: str
    layer: str
    island_ids: tuple[str, ...]
    representative_island_id: str
    component_evidence_sha256: str
    contact_status: str


def _casefold_index(values: Sequence[Any], attribute: str, *, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        display = str(getattr(value, attribute, "")).strip()
        key = _key(display, name=f"{label} ID")
        if key in result:
            raise LayerSurfaceTerminationError(
                "SCENARIO_TERMINATION_MAPPING_AMBIGUOUS",
                f"{label} identity {display!r} is duplicated",
            )
        result[key] = value
    return result


def _validated_terminal_landing_contacts(
    scenario: ScenarioSpec,
    project: Any,
) -> tuple[
    str,
    Mapping[tuple[str, str], Mapping[str, Any]],
    Mapping[str, _CertifiedSurfaceComponent],
    frozenset[str],
]:
    """Validate and index the exact v3 component-resolved contact certificate."""

    metadata = getattr(project, "metadata", {})
    spd_import = metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    certificate = (
        spd_import.get(_SURFACE_CONNECTIVITY_METADATA_KEY)
        if isinstance(spd_import, Mapping)
        else None
    )
    if not isinstance(certificate, Mapping):
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_MISSING",
            "reimport the SPD to retain v3 exact terminal Via/island evidence",
        )
    if (
        certificate.get("schema_version") != _SURFACE_CONNECTIVITY_SCHEMA
        or certificate.get("compiler_id") != _SURFACE_CONNECTIVITY_COMPILER
    ):
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_UNSUPPORTED",
            "termination binding requires the v3 terminal-Via island schema/compiler",
        )

    certificate_source = str(certificate.get("source_sha256", "")).strip().casefold()
    scenario_source = str(scenario.source.sha256).strip().casefold()
    if (
        len(certificate_source) != 64
        or any(character not in "0123456789abcdef" for character in certificate_source)
        or certificate_source != scenario_source
    ):
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_SOURCE_MISMATCH",
            "surface-connectivity certificate is not bound to this Scenario SPD source",
        )

    evidence_sha256 = str(certificate.get("evidence_sha256", "")).strip().casefold()
    unsigned = {
        str(key): value
        for key, value in certificate.items()
        if str(key) != "evidence_sha256"
    }
    if (
        len(evidence_sha256) != 64
        or any(character not in "0123456789abcdef" for character in evidence_sha256)
        or _metadata_sha256(unsigned) != evidence_sha256
    ):
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INTEGRITY_FAILED",
            "surface-connectivity evidence SHA-256 does not match its payload",
        )
    if str(certificate.get("status", "")).strip().casefold() != "complete":
        raise LayerSurfaceTerminationError(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INCOMPLETE",
            "source compiler did not complete the v3 terminal Via/island certificate",
        )

    raw_components = certificate.get("surface_equivalence_components")
    if not _sequence(raw_components) or not raw_components:
        raise LayerSurfaceTerminationError(
            "SURFACE_EQUIVALENCE_COMPONENT_MANIFEST_INVALID",
            "v3 certificate surface_equivalence_components must be a non-empty sequence",
        )
    components: dict[str, _CertifiedSurfaceComponent] = {}
    observed_islands: dict[str, str] = {}
    for ordinal, raw in enumerate(raw_components):
        if not isinstance(raw, Mapping):
            raise LayerSurfaceTerminationError(
                "SURFACE_EQUIVALENCE_COMPONENT_MANIFEST_INVALID",
                f"surface equivalence component row {ordinal} is not a mapping",
            )
        component_id = str(raw.get("component_id", "")).strip()
        net = str(raw.get("net", "")).strip()
        layer = str(raw.get("layer", "")).strip()
        raw_islands = raw.get("island_ids")
        representative = str(
            raw.get("representative_island_id", "")
        ).strip()
        component_evidence = str(
            raw.get("component_evidence_sha256", "")
        ).strip().casefold()
        contact_status = str(raw.get("contact_status", "")).strip().casefold()
        if (
            not component_id
            or not net
            or not layer
            or not _sequence(raw_islands)
            or not raw_islands
            or contact_status not in {"complete", "uncontacted"}
        ):
            raise LayerSurfaceTerminationError(
                "SURFACE_EQUIVALENCE_COMPONENT_MANIFEST_INVALID",
                f"surface equivalence component row {ordinal} has incomplete identity",
            )
        island_ids = tuple(str(item).strip() for item in raw_islands)
        if (
            any(not item for item in island_ids)
            or len({item.casefold() for item in island_ids}) != len(island_ids)
            or island_ids != tuple(sorted(island_ids))
            or representative != island_ids[0]
        ):
            raise LayerSurfaceTerminationError(
                "SURFACE_EQUIVALENCE_COMPONENT_MANIFEST_INVALID",
                f"surface equivalence component {component_id!r} has a non-canonical partition",
            )
        expected_evidence = _metadata_sha256(
            {
                "source_sha256": certificate_source,
                "net": net.casefold(),
                "layer": layer.casefold(),
                "island_ids": list(island_ids),
            }
        )
        expected_id = (
            "spd-surface-equivalence-component:"
            f"{expected_evidence[:24]}"
        )
        if (
            component_evidence != expected_evidence
            or component_id != expected_id
        ):
            raise LayerSurfaceTerminationError(
                "SURFACE_EQUIVALENCE_COMPONENT_INTEGRITY_FAILED",
                f"surface equivalence component {component_id!r} identity/evidence is invalid",
            )
        component_key = component_id.casefold()
        if component_key in components:
            raise LayerSurfaceTerminationError(
                "SURFACE_EQUIVALENCE_COMPONENT_AMBIGUOUS",
                f"surface equivalence component {component_id!r} is duplicated",
            )
        for island_id in island_ids:
            island_key = island_id.casefold()
            previous = observed_islands.setdefault(island_key, component_id)
            if previous != component_id:
                raise LayerSurfaceTerminationError(
                    "SURFACE_EQUIVALENCE_COMPONENT_AMBIGUOUS",
                    f"artwork island {island_id!r} belongs to multiple certified components",
                )
        components[component_key] = _CertifiedSurfaceComponent(
            component_id=component_id,
            net=net,
            layer=layer,
            island_ids=island_ids,
            representative_island_id=representative,
            component_evidence_sha256=component_evidence,
            contact_status=contact_status,
        )

    raw_proofs = certificate.get("surface_equivalence_proofs")
    if not _sequence(raw_proofs) or not raw_proofs:
        raise LayerSurfaceTerminationError(
            "SURFACE_EQUIVALENCE_PROOF_INVALID",
            "v3 certificate surface_equivalence_proofs must be a non-empty sequence",
        )
    components_by_surface: dict[tuple[str, str], list[_CertifiedSurfaceComponent]] = {}
    for component in components.values():
        components_by_surface.setdefault(
            (component.net.casefold(), component.layer.casefold()), []
        ).append(component)
    proof_surfaces: set[tuple[str, str]] = set()
    contacted_component_ids: set[str] = set()
    for ordinal, raw in enumerate(raw_proofs):
        if not isinstance(raw, Mapping):
            raise LayerSurfaceTerminationError(
                "SURFACE_EQUIVALENCE_PROOF_INVALID",
                f"surface equivalence proof row {ordinal} is not a mapping",
            )
        net = str(raw.get("net", "")).strip()
        layer = str(raw.get("layer", "")).strip()
        surface_key = (net.casefold(), layer.casefold())
        surface_components = components_by_surface.get(surface_key)
        raw_islands = raw.get("island_ids")
        raw_contacted = raw.get("contacted_island_ids")
        if (
            not net
            or not layer
            or surface_key in proof_surfaces
            or not surface_components
            or not _sequence(raw_islands)
            or not _sequence(raw_contacted)
        ):
            raise LayerSurfaceTerminationError(
                "SURFACE_EQUIVALENCE_PROOF_INVALID",
                f"surface equivalence proof row {ordinal} has an invalid identity",
            )
        island_ids = tuple(sorted(str(item).strip() for item in raw_islands))
        contacted_ids = tuple(
            sorted(str(item).strip() for item in raw_contacted)
        )
        certified_islands = tuple(
            sorted(
                island_id
                for component in surface_components
                for island_id in component.island_ids
            )
        )
        contacted_set = set(contacted_ids)
        touched_components = tuple(
            component
            for component in surface_components
            if contacted_set.intersection(component.island_ids)
        )
        uncontacted_components_are_singletons = all(
            len(component.island_ids) == 1
            for component in surface_components
            if not contacted_set.intersection(component.island_ids)
        )
        component_count = raw.get("graph_component_count")
        expected_status = (
            "complete" if contacted_ids == island_ids else "uncontacted_island"
        )
        expected_partition_kind = (
            "single_component"
            if component_count == 1
            else "split_components"
        )
        if (
            island_ids != certified_islands
            or any(not item for item in contacted_ids)
            or len(set(contacted_ids)) != len(contacted_ids)
            or not contacted_set <= set(island_ids)
            or component_count != len(touched_components)
            or not uncontacted_components_are_singletons
            or str(raw.get("status", "")).strip().casefold()
            != expected_status
            or str(raw.get("partition_kind", "")).strip().casefold()
            != expected_partition_kind
        ):
            raise LayerSurfaceTerminationError(
                "SURFACE_EQUIVALENCE_PROOF_INVALID",
                f"surface equivalence proof for {net!r}/{layer!r} disagrees with its partition",
            )
        for component in touched_components:
            if set(component.island_ids) <= contacted_set:
                contacted_component_ids.add(component.component_id.casefold())
        proof_surfaces.add(surface_key)
    if proof_surfaces != set(components_by_surface):
        raise LayerSurfaceTerminationError(
            "SURFACE_EQUIVALENCE_PROOF_INVALID",
            "surface equivalence proofs do not cover every certified surface",
        )
    for component in components.values():
        expected_contact_status = (
            "complete"
            if component.component_id.casefold() in contacted_component_ids
            else "uncontacted"
        )
        if component.contact_status != expected_contact_status:
            raise LayerSurfaceTerminationError(
                "SURFACE_EQUIVALENCE_PROOF_INVALID",
                f"surface component {component.component_id!r} contact status disagrees with its proof",
            )

    raw_contacts = certificate.get("terminal_landing_contacts")
    if not _sequence(raw_contacts):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_CONTACT_MANIFEST_INVALID",
            "v3 certificate terminal_landing_contacts must be a sequence",
        )
    contacts: dict[tuple[str, str], Mapping[str, Any]] = {}
    for ordinal, raw in enumerate(raw_contacts):
        if not isinstance(raw, Mapping):
            raise LayerSurfaceTerminationError(
                "TERMINAL_LANDING_CONTACT_MANIFEST_INVALID",
                f"terminal landing contact row {ordinal} is not a mapping",
            )
        via_id = str(raw.get("via_id", "")).strip()
        external_endpoint = str(
            raw.get("external_endpoint_node_id", "")
        ).strip()
        if not via_id or not external_endpoint:
            raise LayerSurfaceTerminationError(
                "TERMINAL_LANDING_CONTACT_MANIFEST_INVALID",
                f"terminal landing contact row {ordinal} has a blank identity",
            )
        canonical_key = (via_id.casefold(), external_endpoint.casefold())
        raw_landing_key = raw.get("landing_key")
        if not _sequence(raw_landing_key) or len(raw_landing_key) != 2:
            raise LayerSurfaceTerminationError(
                "TERMINAL_LANDING_CONTACT_MANIFEST_INVALID",
                f"terminal landing contact {canonical_key!r} has no canonical landing_key",
            )
        observed_key = tuple(str(item).strip().casefold() for item in raw_landing_key)
        if observed_key != canonical_key:
            raise LayerSurfaceTerminationError(
                "TERMINAL_LANDING_CONTACT_MANIFEST_INVALID",
                "terminal landing contact landing_key "
                f"{observed_key!r} disagrees with its row identity",
            )
        if canonical_key in contacts:
            raise LayerSurfaceTerminationError(
                "TERMINAL_LANDING_CONTACT_AMBIGUOUS",
                f"terminal landing {canonical_key!r} has multiple v3 contact rows",
            )
        contacts[canonical_key] = raw
    return (
        evidence_sha256,
        contacts,
        components,
        frozenset(contacted_component_ids),
    )


def _exact_terminal_island_binding(
    landing: ScenarioViaLanding,
    contacts: Mapping[tuple[str, str], Mapping[str, Any]],
    components: Mapping[str, _CertifiedSurfaceComponent],
    contacted_component_ids: frozenset[str],
    conductor_center_depth_um: Mapping[str, float],
) -> _ExactTerminalIslandBinding:
    """Resolve one source landing to one certified same-layer equivalence component."""

    landing_key = (
        _key(landing.via_id, name="physical source Via ID"),
        _key(landing.endpoint_node_id, name="external endpoint Node ID"),
    )
    contact = contacts.get(landing_key)
    if contact is None:
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_CONTACT_MISSING",
            f"landing {landing.via_id!r}/{landing.endpoint_node_id!r} has no "
            "v3 opposite-endpoint contact",
        )
    if str(contact.get("status", "")).strip().casefold() != "complete":
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_CONTACT_INCOMPLETE",
            f"landing {landing.via_id!r}/{landing.endpoint_node_id!r} contact "
            "status is incomplete",
        )
    owner_kind = str(contact.get("terminal_owner_kind", "")).strip().casefold()
    if owner_kind != "decap":
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_OWNER_MISMATCH",
            f"landing {landing.via_id!r} must have decap ownership, observed "
            f"{owner_kind or 'unknown'!r}",
        )
    _validate_decap_terminal_physical_evidence(
        landing,
        contact,
        conductor_center_depth_um,
    )

    if (
        str(contact.get("contact_path_kind", "")).strip().casefold()
        != "direct_via_landing"
        or str(contact.get("endpoint_resolution_kind", "")).strip().casefold()
        != "same_layer_trace_artwork_component"
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_CONTACT_INCOMPLETE",
            f"landing {landing.via_id!r} is not a certified direct Via-to-component contact",
        )

    contact_net = str(contact.get("net", "")).strip()
    if not contact_net or contact_net.casefold() != landing.net.casefold():
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_CONTACT_MISMATCH",
            f"landing {landing.via_id!r} NET differs from its v3 contact row",
        )
    internal_endpoint = str(
        contact.get("internal_endpoint_node_id", "") or ""
    ).strip()
    if (
        not internal_endpoint
        or internal_endpoint.casefold() == landing.endpoint_node_id.casefold()
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_CONTACT_INCOMPLETE",
            f"landing {landing.via_id!r} has no distinct opposite/internal Via endpoint",
        )

    component_id = str(contact.get("contact_component_id", "")).strip()
    component = components.get(component_id.casefold())
    if (
        not component_id
        or component is None
        or component.component_id.casefold() not in contacted_component_ids
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_CONTACT_INCOMPLETE",
            f"landing {landing.via_id!r} has no contacted, certified surface-equivalence component",
        )
    raw_component_islands = contact.get("component_island_ids")
    rows = contact.get("contact_island_ids_by_layer")
    candidate_component_ids = contact.get("candidate_component_ids")
    if (
        not _sequence(raw_component_islands)
        or tuple(str(item).strip() for item in raw_component_islands)
        != component.island_ids
        or not isinstance(rows, Mapping)
        or len(rows) != 1
        or not _sequence(candidate_component_ids)
        or tuple(str(item).strip().casefold() for item in candidate_component_ids)
        != (component.component_id.casefold(),)
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_CONTACT_INCOMPLETE",
            f"landing {landing.via_id!r} component partition evidence is inconsistent",
        )
    raw_layer, raw_islands = next(iter(rows.items()))
    layer = str(raw_layer).strip()
    row_islands = (
        tuple(str(item).strip() for item in raw_islands)
        if _sequence(raw_islands)
        else ()
    )
    endpoint_layer = str(contact.get("endpoint_layer", "") or "").strip()
    endpoint_island = str(contact.get("endpoint_island_id", "") or "").strip()
    component_layer = str(contact.get("component_layer", "") or "").strip()
    representative = str(
        contact.get("representative_island_id", "") or ""
    ).strip()
    component_evidence = str(
        contact.get("component_evidence_sha256", "") or ""
    ).strip().casefold()
    if (
        not endpoint_layer
        or not endpoint_island
        or layer.casefold() != component.layer.casefold()
        or component_layer.casefold() != component.layer.casefold()
        or row_islands != component.island_ids
        or endpoint_layer.casefold() != component.layer.casefold()
        or endpoint_island.casefold()
        != component.representative_island_id.casefold()
        or representative.casefold()
        != component.representative_island_id.casefold()
        or component_evidence != component.component_evidence_sha256
        or contact_net.casefold() != component.net.casefold()
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_CONTACT_INCOMPLETE",
            f"landing {landing.via_id!r} endpoint component evidence is inconsistent",
        )
    return _ExactTerminalIslandBinding(
        via_id=str(contact.get("via_id", "")).strip(),
        external_endpoint_node_id=str(
            contact.get("external_endpoint_node_id", "")
        ).strip(),
        internal_endpoint_node_id=internal_endpoint,
        net=contact_net,
        layer=component.layer,
        island_node_id=component.representative_island_id,
    )


def _validate_decap_terminal_physical_evidence(
    landing: ScenarioViaLanding,
    contact: Mapping[str, Any],
    conductor_center_depth_um: Mapping[str, float],
) -> None:
    """Reject incomplete v3 physical Via provenance without restamping it."""

    context = f"landing {landing.via_id!r}/{landing.endpoint_node_id!r}"
    status = str(contact.get("physical_model_status", "")).strip().casefold()
    issues = contact.get("physical_model_issues")
    if status != "complete" or not _sequence(issues) or len(issues) != 0:
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} does not have complete physical Via provenance",
        )
    raw_drill_diameter_um = contact.get("drill_diameter_um")
    try:
        drill_diameter_um = float(contact.get("drill_diameter_um"))
    except (TypeError, ValueError, OverflowError):
        drill_diameter_um = float("nan")
    if (
        isinstance(raw_drill_diameter_um, bool)
        or not isfinite(drill_diameter_um)
        or drill_diameter_um <= 0.0
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} has no finite positive drill diameter",
        )
    material = contact.get("material")
    if material is not None and (
        not isinstance(material, str) or not material.strip()
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} has an invalid optional Via material",
        )
    padstack = contact.get("padstack")
    if not isinstance(padstack, str) or not padstack.strip():
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} has no physical Via padstack identity",
        )

    external_layer = str(contact.get("external_endpoint_layer", "") or "").strip()
    endpoint_layer = str(contact.get("endpoint_layer", "") or "").strip()
    raw_segments = contact.get("segments")
    if (
        not external_layer
        or not endpoint_layer
        or not _sequence(raw_segments)
        or not raw_segments
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} has no complete ordered physical Via segment path",
        )
    segments: list[tuple[int, str, str, float]] = []
    for raw in raw_segments:
        if not isinstance(raw, Mapping):
            raise LayerSurfaceTerminationError(
                "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
                f"{context} physical Via segment is not a mapping",
            )
        raw_ordinal = raw.get("ordinal")
        raw_length_um = raw.get("length_um")
        try:
            length_um = float(raw_length_um)
        except (TypeError, ValueError, OverflowError):
            length_um = float("nan")
        ordinal = (
            raw_ordinal
            if isinstance(raw_ordinal, int) and not isinstance(raw_ordinal, bool)
            else -1
        )
        start_layer = str(raw.get("start_layer", "")).strip()
        end_layer = str(raw.get("end_layer", "")).strip()
        if (
            ordinal < 0
            or not start_layer
            or not end_layer
            or isinstance(raw_length_um, bool)
            or not isfinite(length_um)
            or length_um <= 0.0
        ):
            raise LayerSurfaceTerminationError(
                "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
                f"{context} physical Via segment is incomplete",
            )
        segments.append((ordinal, start_layer, end_layer, length_um))
    if [item[0] for item in segments] != list(range(len(segments))):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} physical Via segment ordinals are not exact source order",
        )
    if segments[0][1].casefold() != external_layer.casefold() or segments[-1][
        2
    ].casefold() != endpoint_layer.casefold():
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} physical Via path does not span the certified endpoints",
        )
    if any(
        first[2].casefold() != second[1].casefold()
        for first, second in zip(segments, segments[1:])
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} physical Via segments are not contiguous",
        )
    layer_path = [segments[0][1], *(item[2] for item in segments)]
    layer_keys = [item.casefold() for item in layer_path]
    if len(set(layer_keys)) != len(layer_keys):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} physical Via path repeats a conductor layer",
        )
    try:
        depths = [conductor_center_depth_um[item] for item in layer_keys]
    except KeyError as exc:
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} physical Via path references a non-current conductor layer",
        ) from exc
    depth_deltas = [second - first for first, second in zip(depths, depths[1:])]
    if (
        not depth_deltas
        or any(delta == 0.0 for delta in depth_deltas)
        or not (
            all(delta > 0.0 for delta in depth_deltas)
            or all(delta < 0.0 for delta in depth_deltas)
        )
    ):
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            f"{context} physical Via path is not strictly monotonic through the current stackup",
        )
    for segment, delta in zip(segments, depth_deltas):
        if not isclose(
            segment[3],
            abs(delta),
            rel_tol=1.0e-12,
            abs_tol=1.0e-9,
        ):
            raise LayerSurfaceTerminationError(
                "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
                f"{context} physical Via segment length differs from the current stackup",
            )


def _conductor_center_depth_um(project: Any) -> Mapping[str, float]:
    """Return current stackup conductor centers for stale-evidence rejection."""

    result: dict[str, float] = {}
    seen_layers: set[str] = set()
    depth_um = 0.0
    for raw in tuple(getattr(project, "stackup_layers", ())):
        name = str(getattr(raw, "name", "")).strip()
        raw_thickness = getattr(raw, "thickness_um", None)
        try:
            thickness_um = float(raw_thickness)
        except (TypeError, ValueError, OverflowError):
            thickness_um = float("nan")
        key = name.casefold()
        if (
            not name
            or key in seen_layers
            or isinstance(raw_thickness, bool)
            or not isfinite(thickness_um)
            or thickness_um <= 0.0
        ):
            raise LayerSurfaceTerminationError(
                "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
                "current stackup has invalid layer identity or thickness",
            )
        seen_layers.add(key)
        if bool(getattr(raw, "is_conductor", False)):
            result[key] = depth_um + thickness_um / 2.0
        depth_um += thickness_um
    if len(result) < 2:
        raise LayerSurfaceTerminationError(
            "TERMINAL_LANDING_PHYSICAL_MODEL_INCOMPLETE",
            "current stackup has fewer than two conductor layers",
        )
    return result


def _rail_template_ids(project: Any) -> dict[str, str]:
    """Resolve one imported Via-loop template per rail without guessing by order."""

    templates = tuple(getattr(project, "via_templates", ()))
    provenance = getattr(project, "metadata", {}).get(
        "spd_via_template_provenance", {}
    )
    result: dict[str, str] = {}
    for rail in getattr(project, "rails", ()):
        rail_key = _key(rail.rail_id, name="rail ID")
        provenance_candidates: dict[str, str] = {}
        if isinstance(provenance, Mapping):
            for raw_template_id, raw in provenance.items():
                if (
                    isinstance(raw, Mapping)
                    and str(raw.get("rail_id", "")).strip().casefold() == rail_key
                ):
                    display = str(raw_template_id).strip()
                    if display:
                        provenance_candidates.setdefault(display.casefold(), display)
        if provenance_candidates:
            candidates = provenance_candidates
        else:
            candidates = {}
            for template in templates:
                if (
                    str(template.pwr_reference_layer).casefold()
                    == str(rail.pwr_layer).casefold()
                    and str(template.gnd_reference_layer).casefold()
                    == str(rail.gnd_layer).casefold()
                ):
                    display = str(template.template_id).strip()
                    candidates.setdefault(display.casefold(), display)
        if len(candidates) != 1:
            raise LayerSurfaceTerminationError(
                "RAIL_VIA_TEMPLATE_AMBIGUOUS",
                f"rail {rail.rail_id!r} requires exactly one retained Via-loop "
                f"template; observed {tuple(candidates.values())!r}",
            )
        result[rail_key] = next(iter(candidates.values()))
    return result


def _ground_net(project: Any, rail: Any) -> str:
    layers = _casefold_index(
        tuple(getattr(project, "stackup_layers", ())),
        "name",
        label="stackup layer",
    )
    layer = layers.get(_key(rail.gnd_layer, name="GND layer"))
    aliases = {
        _key(item, name="configured GND alias")
        for item in getattr(project, "gnd_aliases", ())
    }
    certificate = getattr(rail, "mixed_reference_certificate", None)
    configured = (
        None
        if certificate is None
        else _key(certificate.gnd_net, name="certified GND NET")
    )
    matches = {
        str(net).strip().casefold(): str(net).strip()
        for net in (getattr(layer, "pwr_nets", ()) if layer is not None else ())
        if str(net).strip().casefold() in aliases
        and (configured is None or str(net).strip().casefold() == configured)
    }
    if len(matches) != 1:
        raise LayerSurfaceTerminationError(
            "GROUND_SURFACE_UNRESOLVED",
            f"rail {rail.rail_id!r} requires exactly one configured GND NET on "
            f"{rail.gnd_layer!r}",
        )
    return next(iter(matches.values()))


def _eligibility_template_id(
    decap: ScenarioDecap,
    connection: ScenarioDecapConnection,
    rail: Any,
    *,
    rail_template_id: str,
    cluster: SharedPadCluster | None,
    via_id: str | None,
    source_landing: ScenarioViaLanding,
    expected_landing_net: str,
) -> str:
    """Return a current-rail template only from explicit or unchanged-source proof."""

    rail_key = _key(rail.rail_id, name="current rail ID")
    candidates: list[Any] = []
    if cluster is not None and via_id is not None:
        via_rows = next(
            (
                rows
                for raw_via_id, rows in cluster.via_eligibility.items()
                if raw_via_id.casefold() == via_id.casefold()
            ),
            {},
        )
        candidates.extend(
            item
            for item in via_rows.values()
            if item.rail_id.casefold() == rail_key
        )
    elif cluster is not None:
        # GND landings have no per-GND-Via eligibility table.  Their terminal
        # half uses the one differential template proven by the cluster's PWR
        # roots for this rail.
        candidates.extend(
            item
            for rows in cluster.via_eligibility.values()
            for item in rows.values()
            if item.rail_id.casefold() == rail_key
        )
    candidates.extend(
        item
        for item in decap.eligibility.values()
        if item.rail_id.casefold() == rail_key
    )
    valid = {
        str(item.via_template_id).strip().casefold(): str(item.via_template_id).strip()
        for item in candidates
        if item.allowed
        and item.via_template_id
        and item.net.casefold() == str(rail.net).casefold()
        and item.pwr_layer.casefold() == str(rail.pwr_layer).casefold()
        and item.gnd_layer.casefold() == str(rail.gnd_layer).casefold()
    }
    if len(valid) == 1:
        template_id = next(iter(valid.values()))
        if template_id.casefold() != rail_template_id.casefold():
            raise LayerSurfaceTerminationError(
                "RAIL_VIA_TEMPLATE_MISMATCH",
                f"{decap.refdes} eligibility uses {template_id!r}, but rail "
                f"{rail.rail_id!r} is bound to {rail_template_id!r}",
            )
        return template_id
    if len(valid) > 1:
        raise LayerSurfaceTerminationError(
            "RAIL_VIA_TEMPLATE_AMBIGUOUS",
            f"{decap.refdes} has multiple current-rail Via templates",
        )

    # Distribution eligibility proves a *new* assignment.  It is not required
    # to re-prove a component that remained on its imported source rail: the
    # persisted source landing, retained Via ownership certificate, and the
    # downstream complete pad-graph/supernode audit are the electrical proof.
    # This distinction is important for source UNRESOLVED shared members.  They
    # may have no per-destination eligibility row yet still belong to a fully
    # anchored, source-proven cluster; treating that absence as a current-rail
    # assignment failure was the cause of the post-Distribution Evaluation
    # blocker reported by the real examples.
    unchanged_source_landing = (
        connection.kind
        in {
            DecapConnectionKind.DIRECT,
            DecapConnectionKind.SHARED_ANCHOR,
            DecapConnectionKind.SHARED_DUMMY,
            DecapConnectionKind.UNRESOLVED,
        }
        and decap.current_rail_id.casefold() == decap.source_rail_id.casefold()
        and decap.current_net.casefold() == decap.source_net.casefold()
        and str(rail.rail_id).casefold() == decap.source_rail_id.casefold()
        and str(rail.net).casefold() == decap.source_net.casefold()
        and source_landing.net.casefold() == expected_landing_net.casefold()
        and any(
            landing.via_id.casefold() == source_landing.via_id.casefold()
            for landing in (*connection.power_vias, *connection.ground_vias)
        )
    )
    if unchanged_source_landing:
        return rail_template_id
    raise LayerSurfaceTerminationError(
        "CURRENT_RAIL_ELIGIBILITY_MISSING",
        f"{decap.refdes} landing {via_id or '<terminal>'!r} has no complete "
        f"eligibility for rail {rail.rail_id!r}",
    )


@dataclass(frozen=True, slots=True)
class LayerwiseScenarioTerminationInputs:
    """Inspectable exact-island bindings used by one termination manifest.

    ``source_via_to_surface_node`` retains its public compatibility name, but
    every value is now the v3 certificate's exact artwork-island node ID.
    """

    source_via_to_surface_node: Mapping[str, str]
    source_via_to_internal_endpoint_node: Mapping[str, str]
    terminal_via_model_by_id: Mapping[str, ImpedanceModel]
    connectivity_certificate_evidence_sha256: str


@dataclass(frozen=True, slots=True)
class LayerwiseScenarioTerminationFactory:
    """Transient callable carried by an Evaluation workspace.

    ``ProjectSpec`` remains JSON-only and no Scenario payload is copied into
    project metadata.  The factory is solve-scoped and binds the exact
    Original or Tuned Scenario object to the matching transient solver project.
    This production boundary is v4-only because a v3 manifest cannot cut and
    reconnect the source topology after Distribution changes.
    """

    scenario: ScenarioSpec
    project: Any
    attachments: Mapping[str, bytes] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def __call__(
        self, substrate: Any, template: Any
    ) -> Any:
        metadata = getattr(self.project, "metadata", {})
        spd_import = (
            metadata.get("spd_import") if isinstance(metadata, Mapping) else None
        )
        certificate = (
            spd_import.get(_SURFACE_CONNECTIVITY_METADATA_KEY)
            if isinstance(spd_import, Mapping)
            else None
        )
        if not isinstance(certificate, Mapping):
            raise LayerSurfaceTerminationError(
                "SURFACE_CONNECTIVITY_CERTIFICATE_MISSING",
                "production layerwise Evaluation requires a v4 finite-Via "
                "topology certificate; reimport the SPD",
            )
        if certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA:
            raise LayerSurfaceTerminationError(
                "SURFACE_CONNECTIVITY_CERTIFICATE_UNSUPPORTED",
                "production layerwise Evaluation requires the v4 finite-Via "
                "topology certificate; reimport the SPD",
            )

        from .layerwise_scenario_adapter import (
            compile_v4_layerwise_scenario_binding,
        )

        return compile_v4_layerwise_scenario_binding(
            self.scenario,
            self.project,
            substrate,
            template,
            attachments=self.attachments,
        )


def build_layerwise_scenario_termination_inputs(
    scenario: ScenarioSpec,
    project: Any,
    template: Any,
) -> LayerwiseScenarioTerminationInputs:
    """Resolve physical landing Vias for every populated or anchoring cluster."""

    if scenario.connection_analysis is None:
        raise LayerSurfaceTerminationError(
            "CONNECTION_ANALYSIS_MISSING",
            "scenario has no source connection analysis",
        )
    (
        certificate_evidence_sha256,
        terminal_contacts,
        surface_components,
        contacted_component_ids,
    ) = _validated_terminal_landing_contacts(scenario, project)
    rails = _casefold_index(tuple(project.rails), "rail_id", label="rail")
    conductor_center_depth_um = _conductor_center_depth_um(project)
    rail_templates = _rail_template_ids(project)
    runtime_via_models = {
        _key(raw_id, name="runtime Via model ID"): model
        for raw_id, model in dict(getattr(template, "via_models", {})).items()
    }
    decaps = {
        _key(item.refdes, name="decap REFDES"): item for item in scenario.decaps
    }
    connections = {
        _key(item.refdes, name="connection REFDES"): item
        for item in scenario.connection_analysis.connections.values()
    }
    cluster_by_member: dict[str, SharedPadCluster] = {}
    for cluster in scenario.connection_analysis.clusters:
        for raw_refdes in cluster.member_refdes:
            key = _key(raw_refdes, name="shared-cluster member")
            if key in cluster_by_member:
                raise LayerSurfaceTerminationError(
                    "SHARED_CLUSTER_MEMBER_AMBIGUOUS",
                    f"{raw_refdes!r} belongs to more than one shared cluster",
                )
            cluster_by_member[key] = cluster

    populated_cluster_keys = {
        cluster.cluster_id.casefold()
        for cluster in scenario.connection_analysis.clusters
        if any(
            decaps.get(refdes.casefold()) is not None
            and decaps[refdes.casefold()].enabled
            for refdes in cluster.member_refdes
        )
    }
    bindings: dict[str, str] = {}
    internal_endpoint_bindings: dict[str, str] = {}
    via_models: dict[str, ImpedanceModel] = {}
    evidence_by_via: dict[str, tuple[str, str, str]] = {}

    def bind(
        *,
        via_id: str,
        island_node: str,
        internal_endpoint_node_id: str,
        template_id: str,
        context: str,
    ) -> None:
        via_key = _key(via_id, name="physical source Via ID")
        model = runtime_via_models.get(template_id.casefold())
        if model is None:
            raise LayerSurfaceTerminationError(
                "TERMINAL_VIA_MODEL_MISSING",
                f"{context} references absent Via model {template_id!r}",
            )
        identity = impedance_model_identity_sha256(model)
        observed = (
            island_node.casefold(),
            internal_endpoint_node_id.casefold(),
            identity,
        )
        previous = evidence_by_via.setdefault(via_key, observed)
        if previous != observed:
            raise LayerSurfaceTerminationError(
                "SOURCE_VIA_BINDING_CONFLICT",
                f"physical Via {via_id!r} is assigned to more than one "
                "internal island/endpoint/model",
            )
        bindings.setdefault(via_id, island_node)
        internal_endpoint_bindings.setdefault(via_id, internal_endpoint_node_id)
        via_models.setdefault(via_id, model)

    for refdes_key in sorted(decaps):
        decap = decaps[refdes_key]
        connection = connections.get(refdes_key)
        if connection is None:
            if decap.enabled:
                raise LayerSurfaceTerminationError(
                    "TERMINATION_CONNECTION_MISSING",
                    f"enabled decap {decap.refdes!r} has no connection record",
                )
            continue
        cluster = cluster_by_member.get(refdes_key)
        if not decap.enabled and (
            cluster is None
            or cluster.cluster_id.casefold() not in populated_cluster_keys
        ):
            continue
        rail = rails.get(_key(decap.current_rail_id, name="current rail ID"))
        if rail is None:
            raise LayerSurfaceTerminationError(
                "CURRENT_RAIL_UNKNOWN",
                f"{decap.refdes} references absent rail {decap.current_rail_id!r}",
            )
        rail_template_id = rail_templates[_key(rail.rail_id, name="rail ID")]
        ground_net = _ground_net(project, rail)
        for landing in connection.power_vias:
            exact_contact = _exact_terminal_island_binding(
                landing,
                terminal_contacts,
                surface_components,
                contacted_component_ids,
                conductor_center_depth_um,
            )
            template_id = _eligibility_template_id(
                decap,
                connection,
                rail,
                rail_template_id=rail_template_id,
                cluster=cluster,
                via_id=landing.via_id,
                source_landing=landing,
                expected_landing_net=str(rail.net),
            )
            bind(
                via_id=landing.via_id,
                island_node=exact_contact.island_node_id,
                internal_endpoint_node_id=(
                    exact_contact.internal_endpoint_node_id
                ),
                template_id=template_id,
                context=f"{decap.refdes} PWR Via {landing.via_id}",
            )
        # GND eligibility is represented by the same differential loop
        # template after PWR-plane eligibility and the rail's certified/pure
        # configured-GND identity have both been resolved.
        for landing in connection.ground_vias:
            exact_contact = _exact_terminal_island_binding(
                landing,
                terminal_contacts,
                surface_components,
                contacted_component_ids,
                conductor_center_depth_um,
            )
            template_id = _eligibility_template_id(
                decap,
                connection,
                rail,
                rail_template_id=rail_template_id,
                cluster=cluster,
                via_id=None,
                source_landing=landing,
                expected_landing_net=ground_net,
            )
            bind(
                via_id=landing.via_id,
                island_node=exact_contact.island_node_id,
                internal_endpoint_node_id=(
                    exact_contact.internal_endpoint_node_id
                ),
                template_id=template_id,
                context=f"{decap.refdes} GND Via {landing.via_id}",
            )

    return LayerwiseScenarioTerminationInputs(
        source_via_to_surface_node=dict(bindings),
        source_via_to_internal_endpoint_node=dict(internal_endpoint_bindings),
        terminal_via_model_by_id=dict(via_models),
        connectivity_certificate_evidence_sha256=(
            certificate_evidence_sha256
        ),
    )


def compile_layerwise_scenario_terminations(
    scenario: ScenarioSpec,
    project: Any,
    substrate: Any,
    template: Any,
) -> CompiledLayerSurfaceTerminationManifest:
    """Compile the exact Scenario state against one verified base substrate."""

    network = getattr(substrate, "network", None)
    surface_node_ids = tuple(getattr(network, "surface_node_ids", ()))
    if not surface_node_ids:
        raise LayerSurfaceTerminationError(
            "GLOBAL_NODE_MANIFEST_INVALID",
            "verified layer-surface substrate has no physical node manifest",
        )
    inputs = build_layerwise_scenario_termination_inputs(
        scenario, project, template
    )
    return compile_scenario_termination_manifest(
        scenario,
        surface_node_ids,
        inputs.source_via_to_surface_node,
        dict(getattr(template, "cap_models", {})),
        inputs.terminal_via_model_by_id,
    )


__all__ = [
    "LayerwiseScenarioTerminationFactory",
    "LayerwiseScenarioTerminationInputs",
    "build_layerwise_scenario_termination_inputs",
    "compile_layerwise_scenario_terminations",
]
