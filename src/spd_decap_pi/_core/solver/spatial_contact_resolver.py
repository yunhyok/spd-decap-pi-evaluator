"""Fail-closed resolution of saved terminal contacts onto a finite quotient.

This module is intentionally dormant.  It resolves only scenario/device
terminal contacts whose source certificate contains explicit landing evidence;
it does not claim spatial coverage for the substrate finite-via edge lattice.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .finite_via_layerwise import (
    FINITE_VIA_SURFACE_COMPILER,
    FINITE_VIA_SURFACE_SCHEMA,
    FiniteViaCertificateError,
    compiled_finite_via_topology_identity_sha256,
)


class SpatialContactResolutionError(ValueError):
    """A source, topology, ownership, or geometry contract failed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "SPATIAL_CONTACT_INVALID"
        super().__init__(f"{self.code}: {message}")


def _text(value: object, *, label: str) -> str:
    if value is None:
        raise SpatialContactResolutionError("IDENTITY_MISSING", f"{label} is absent")
    result = str(value).strip()
    if not result:
        raise SpatialContactResolutionError("IDENTITY_MISSING", f"{label} is blank")
    return result


def _key(value: object, *, label: str) -> str:
    return _text(value, label=label).casefold()


def _sha(value: object, *, label: str) -> str:
    result = _key(value, label=label)
    if len(result) != 64 or any(c not in "0123456789abcdef" for c in result):
        raise SpatialContactResolutionError(
            "HASH_INVALID", f"{label} is not a SHA-256 digest"
        )
    return result


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _rows(value: object) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        value = value.values()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SpatialContactResolutionError("EVIDENCE_INVALID", "rows are not a sequence")
    result: list[Mapping[str, Any]] = []
    for row in value:
        if not isinstance(row, Mapping):
            raise SpatialContactResolutionError("EVIDENCE_INVALID", "row is not a mapping")
        result.append(row)
    return tuple(result)


def _canonical_hash(value: object) -> str:
    def plain(item: object) -> object:
        if isinstance(item, Mapping):
            result: dict[str, object] = {}
            for raw_key, raw_value in item.items():
                if not isinstance(raw_key, str):
                    raise SpatialContactResolutionError(
                        "EVIDENCE_INVALID", "canonical mapping key is not text"
                    )
                result[raw_key] = plain(raw_value)
            return result
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            return [plain(value) for value in item]
        return item

    try:
        raw = json.dumps(plain(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpatialContactResolutionError("EVIDENCE_INVALID", str(exc)) from exc
    return sha256(raw).hexdigest()


def _verify_certificate_identity(
    certificate: Mapping[str, Any], expected_evidence_sha256: str,
    expected_source_sha256: str,
) -> None:
    """Bind either a full certificate or a verified compact view to its bytes."""

    if (
        certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or certificate.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
        or str(certificate.get("status", "")).strip().casefold() != "complete"
        or _sha(certificate.get("source_sha256"), label="certificate source")
        != expected_source_sha256
    ):
        raise SpatialContactResolutionError(
            "CERTIFICATE_IDENTITY_MISMATCH",
            "certificate schema, compiler, status, or source differs",
        )
    declared = certificate.get(
        "evidence_sha256", certificate.get("certificate_evidence_sha256")
    )
    if declared is None or _sha(declared, label="certificate evidence") != expected_evidence_sha256:
        raise SpatialContactResolutionError(
            "HASH_MISMATCH", "certificate evidence differs"
        )
    view_digest = certificate.get("view_evidence_sha256")
    if view_digest is not None:
        unsigned = {
            key: value
            for key, value in certificate.items()
            if key != "view_evidence_sha256"
        }
        if _sha(view_digest, label="certificate view evidence") != _canonical_hash(unsigned):
            raise SpatialContactResolutionError(
                "EVIDENCE_TAMPERED", "compact certificate view content differs"
            )
        return
    unsigned = {
        key: value for key, value in certificate.items() if key != "evidence_sha256"
    }
    if _canonical_hash(unsigned) != expected_evidence_sha256:
        raise SpatialContactResolutionError(
            "EVIDENCE_TAMPERED", "certificate content differs from its evidence hash"
        )


def _verify_topology_identity(
    topology: object, certificate: Mapping[str, Any], evidence_sha256: str
) -> None:
    geometry_rows = _rows(certificate.get("geometry_assets", ()))
    artwork: list[str] = []
    seen: set[str] = set()
    for row in geometry_rows:
        raw_islands = row.get("island_ids")
        if (
            not isinstance(raw_islands, Sequence)
            or isinstance(raw_islands, (str, bytes))
        ):
            raise SpatialContactResolutionError(
                "TOPOLOGY_IDENTITY_MISSING", "geometry island manifest is absent"
            )
        for raw_island in raw_islands:
            island = _text(raw_island, label="artwork island")
            key = island.casefold()
            if key in seen:
                raise SpatialContactResolutionError(
                    "TOPOLOGY_IDENTITY_MISMATCH", "artwork islands are duplicated"
                )
            seen.add(key)
            artwork.append(island)
    if not artwork:
        raise SpatialContactResolutionError(
            "TOPOLOGY_IDENTITY_MISSING", "geometry island manifest is empty"
        )
    declared = _sha(
        _field(topology, "topology_identity_sha256"), label="topology identity"
    )
    try:
        current = compiled_finite_via_topology_identity_sha256(
            certificate_evidence_sha256=evidence_sha256,
            artwork_node_ids=artwork,
            quotient_vertex_ids=tuple(_field(topology, "quotient_vertex_ids", ()) or ()),
            external_port_node_ids=tuple(
                _field(topology, "external_port_node_ids", ()) or ()
            ),
            topology_links=tuple(_field(topology, "topology_links", ()) or ()),
            finite_links=tuple(_field(topology, "finite_links", ()) or ()),
            rail_ports=tuple(_field(topology, "rail_ports", ()) or ()),
            omitted_rail_ids=tuple(_field(topology, "omitted_rail_ids", ()) or ()),
        )
    except FiniteViaCertificateError as exc:
        raise SpatialContactResolutionError(
            "TOPOLOGY_IDENTITY_MISMATCH", str(exc)
        ) from exc
    if current != declared:
        raise SpatialContactResolutionError(
            "TOPOLOGY_IDENTITY_MISMATCH", "compiled topology was forged or mutated"
        )


def _contact_identity_payload(contact: "SpatialMeshContact") -> dict[str, object]:
    return {
        "contact_id": contact.contact_id,
        "contact_kind": contact.contact_kind,
        "refdes": contact.refdes,
        "terminal": contact.terminal,
        "net": contact.net,
        "layer": contact.layer,
        "x_um": contact.x_um,
        "y_um": contact.y_um,
        "rotation_degrees": contact.rotation_degrees,
        "quotient_vertex_id": contact.quotient_vertex_id,
        "first_edge_id": contact.first_edge_id,
        "owner_id": contact.owner_id,
        "island_id": contact.island_id,
        "pad_kind": contact.pad_kind,
        "pad_width_um": contact.pad_width_um,
        "pad_height_um": contact.pad_height_um,
        "source_via_id": contact.source_via_id,
        "provenance": dict(contact.provenance),
    }


@dataclass(frozen=True, slots=True)
class SpatialMeshContact:
    """One immutable terminal contact projected onto the quotient mesh."""

    contact_id: str
    contact_kind: str
    refdes: str
    terminal: str
    net: str
    layer: str
    x_um: float
    y_um: float
    rotation_degrees: float
    quotient_vertex_id: str
    first_edge_id: str
    owner_id: str | None
    island_id: str
    pad_kind: str
    pad_width_um: float
    pad_height_um: float
    source_via_id: str | None
    provenance: Mapping[str, object]
    identity_sha256: str

    def __post_init__(self) -> None:
        for name in ("contact_id", "contact_kind", "refdes", "terminal", "net",
                     "layer", "quotient_vertex_id", "first_edge_id", "island_id",
                     "pad_kind"):
            object.__setattr__(self, name, _text(getattr(self, name), label=name))
        for name in ("x_um", "y_um", "rotation_degrees", "pad_width_um",
                     "pad_height_um"):
            value = float(getattr(self, name))
            if not isfinite(value) or (name.startswith("pad_") and value <= 0.0):
                raise SpatialContactResolutionError("GEOMETRY_INVALID", f"{name} invalid")
            object.__setattr__(self, name, value)
        if self.owner_id is not None:
            object.__setattr__(self, "owner_id", _text(self.owner_id, label="owner_id"))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))
        object.__setattr__(self, "identity_sha256", _sha(self.identity_sha256,
                                                            label="contact identity"))
        self.verify_current_identity()

    def verify_current_identity(self) -> None:
        if _canonical_hash(_contact_identity_payload(self)) != self.identity_sha256:
            raise SpatialContactResolutionError(
                "CONTACT_IDENTITY_MISMATCH", "terminal contact was forged or mutated"
            )

    @property
    def vertex_id(self) -> str:
        return self.quotient_vertex_id

    @property
    def edge_id(self) -> str:
        return self.first_edge_id

    @property
    def via_owner_id(self) -> str | None:
        return self.owner_id


@dataclass(frozen=True, slots=True)
class SpatialContactSkip:
    refdes: str
    classification: str
    reason: str
    provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "refdes", _text(self.refdes, label="refdes"))
        object.__setattr__(self, "classification", _text(self.classification,
                                                           label="classification"))
        object.__setattr__(self, "reason", _text(self.reason, label="reason"))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


@dataclass(frozen=True, slots=True)
class SpatialContactResolution:
    contacts: tuple[SpatialMeshContact, ...]
    skipped: tuple[SpatialContactSkip, ...]
    source_sha256: str
    certificate_evidence_sha256: str
    identity_sha256: str
    provenance: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "contacts", tuple(self.contacts))
        object.__setattr__(self, "skipped", tuple(self.skipped))
        object.__setattr__(self, "source_sha256", _sha(self.source_sha256,
                                                         label="source hash"))
        object.__setattr__(self, "certificate_evidence_sha256",
                           _sha(self.certificate_evidence_sha256,
                                label="certificate evidence hash"))
        object.__setattr__(self, "identity_sha256", _sha(self.identity_sha256,
                                                          label="resolution identity"))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))
        self.verify_current_identity()

    def verify_current_identity(self) -> None:
        for contact in self.contacts:
            contact.verify_current_identity()
        payload = {
            "source_sha256": self.source_sha256,
            "certificate_evidence_sha256": self.certificate_evidence_sha256,
            "contacts": [item.identity_sha256 for item in self.contacts],
            "skipped": [
                {
                    "refdes": item.refdes,
                    "classification": item.classification,
                    "reason": item.reason,
                    "provenance": dict(item.provenance),
                }
                for item in self.skipped
            ],
            "provenance": dict(self.provenance),
        }
        if _canonical_hash(payload) != self.identity_sha256:
            raise SpatialContactResolutionError(
                "RESOLUTION_IDENTITY_MISMATCH", "contact resolution was forged or mutated"
            )


def _certificate_rows(certificate: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    for name in ("terminal_landing_contacts", "via_landing_contacts",
                 "landing_contacts", "via_pad_aggregates"):
        if name in certificate:
            return _rows(certificate[name])
    return ()


def _row_value(row: Mapping[str, Any], *names: str) -> object:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def _pad_aggregate(rows: Sequence[Mapping[str, Any]], landing: object,
                   path: object) -> tuple[str, float, float, str]:
    via = _key(_field(landing, "via_id"), label="via ID")
    layer = _key(_field(path, "target_layer"), label="target layer")
    matches: list[tuple[str, float, float, str]] = []
    for row in rows:
        row_via = _row_value(row, "via_id", "source_via_id", "terminal_via_id")
        row_layer = _row_value(
            row,
            "target_layer",
            "landing_layer",
            "external_endpoint_layer",
            "layer",
        )
        if row_via is None or row_layer is None:
            continue
        if _key(row_via, label="certificate via") != via:
            continue
        if _key(row_layer, label="certificate layer") != layer:
            continue
        kind = _row_value(row, "target_pad_kind", "landing_pad_kind", "pad_kind")
        width = _row_value(row, "target_pad_width_um", "landing_pad_width_um",
                           "pad_width_um")
        height = _row_value(row, "target_pad_height_um", "landing_pad_height_um",
                            "pad_height_um")
        island = _row_value(
            row, "target_island_id", "endpoint_island_id", "landing_island_id"
        )
        if island is None:
            by_layer = row.get("contact_island_ids_by_layer")
            if isinstance(by_layer, Mapping):
                layer_rows = [
                    value
                    for raw_layer, value in by_layer.items()
                    if _key(raw_layer, label="certificate island layer") == layer
                ]
                if len(layer_rows) == 1:
                    raw_islands = layer_rows[0]
                    if (
                        isinstance(raw_islands, Sequence)
                        and not isinstance(raw_islands, (str, bytes))
                        and len(raw_islands) == 1
                    ):
                        island = raw_islands[0]
        if kind is None or width is None or height is None:
            # Older compact terminal rows carry a unique padstack aggregate,
            # while the immutable ScenarioViaPathEvidence carries its exact
            # shape.  Accept that binding only when the row names the same
            # physical padstack; never invent dimensions for a missing path.
            padstack = _row_value(row, "padstack", "landing_padstack")
            source_padstack = _field(landing, "padstack")
            path_kind = _field(path, "target_pad_kind")
            path_width = _field(path, "target_pad_width_um")
            path_height = _field(path, "target_pad_height_um")
            if (
                padstack is None
                or source_padstack is None
                or _key(padstack, label="certificate padstack")
                != _key(source_padstack, label="landing padstack")
                or path_kind is None
                or path_width is None
                or path_height is None
            ):
                raise SpatialContactResolutionError(
                    "PAD_CERTIFICATE_INCOMPLETE", "pad aggregate is incomplete"
                )
            kind, width, height = path_kind, path_width, path_height
        if island is None:
            raise SpatialContactResolutionError(
                "PAD_CERTIFICATE_INCOMPLETE", "pad aggregate has no unique source island"
            )
        candidate = (
            _text(kind, label="pad kind"),
            float(width),
            float(height),
            _text(island, label="source island"),
        )
        if not isfinite(candidate[1]) or not isfinite(candidate[2]) or \
                candidate[1] <= 0 or candidate[2] <= 0:
            raise SpatialContactResolutionError("PAD_CERTIFICATE_INVALID",
                                                "pad aggregate dimensions invalid")
        matches.append(candidate)
    unique = {
        (kind.casefold(), width, height, island.casefold())
        for kind, width, height, island in matches
    }
    if len(unique) != 1:
        code = "PAD_CERTIFICATE_MISSING" if not matches else "PAD_CERTIFICATE_AMBIGUOUS"
        raise SpatialContactResolutionError(code, f"no unique pad aggregate for {via}/{layer}")
    return matches[0]


def _topology_maps(topology: object) -> tuple[Mapping[Any, Any], Mapping[Any, Any]]:
    vertices = _field(topology, "vertex_by_landing_key")
    edges = _field(topology, "first_edge_by_landing_key")
    if not isinstance(vertices, Mapping) or not isinstance(edges, Mapping):
        raise SpatialContactResolutionError("TOPOLOGY_INCOMPLETE", "landing maps missing")
    return vertices, edges


def _links(topology: object) -> tuple[object, ...]:
    values = _field(topology, "via_links")
    if values is None:
        values = tuple(_field(topology, name, ()) for name in ("topology_links",
                                                                "finite_links"))
        values = tuple(item for group in values for item in group)
    if isinstance(values, (str, bytes)):
        raise SpatialContactResolutionError("TOPOLOGY_INCOMPLETE", "links invalid")
    return tuple(values)


def _resolve_destination(vertices: Mapping[Any, Any], edges: Mapping[Any, Any],
                         links: Mapping[str, object], landing: object
                         ) -> tuple[str, str, str]:
    via_id = _key(_field(landing, "via_id"), label="via ID")
    endpoint = _key(_field(landing, "endpoint_node_id"), label="landing endpoint")
    lookup = (via_id, endpoint)
    vertex = vertices.get(lookup)
    edge = edges.get(lookup)
    if vertex is None or edge is None:
        raise SpatialContactResolutionError(
            "TOPOLOGY_INCIDENCE_MISSING",
            f"no exact source landing incidence for {lookup}",
        )
    vertex = _text(vertex, label="quotient vertex")
    edge = _text(edge, label="first edge")
    link = links.get(edge.casefold())
    if link is None:
        raise SpatialContactResolutionError("TOPOLOGY_INCIDENCE_MISSING",
                                            f"first edge {edge!r} is absent")
    endpoints = {
        _text(_field(link, "first_node_id"), label="link endpoint"),
        _text(_field(link, "second_node_id"), label="link endpoint"),
    }
    if vertex not in endpoints:
        raise SpatialContactResolutionError(
            "TOPOLOGY_INCIDENCE_MISMATCH",
            f"first edge {edge!r} is not incident to vertex {vertex!r}",
        )
    return vertex, edge, "compiled_source_landing_map"


def resolve_spatial_contacts(scenario: object, topology: object, *,
                             certificate: Mapping[str, Any] | None = None,
                             via_group_evidence: Mapping[str, Any] | None = None
                             ) -> SpatialContactResolution:
    """Resolve terminal/device contacts; substrate spatial coverage is disclosed."""
    cert = certificate if certificate is not None else _field(topology, "certificate")
    if not isinstance(cert, Mapping):
        raise SpatialContactResolutionError("EVIDENCE_MISSING", "source certificate missing")
    source_obj = _field(scenario, "source")
    source = _sha(_field(source_obj, "sha256"), label="source hash")
    topo_source = _field(topology, "source_sha256")
    if topo_source is not None and _sha(topo_source, label="topology source hash") != source:
        raise SpatialContactResolutionError("HASH_MISMATCH", "scenario/topology source differs")
    evidence = _sha(_field(topology, "certificate_evidence_sha256",
                            cert.get("evidence_sha256")), label="certificate evidence")
    _verify_certificate_identity(cert, evidence, source)
    _verify_topology_identity(topology, cert, evidence)
    if via_group_evidence is not None:
        raise SpatialContactResolutionError(
            "VIA_GROUP_EVIDENCE_UNSUPPORTED",
            "detached Via-group rows have no typed canonical identity contract",
        )
    vertices, edge_map = _topology_maps(topology)
    link_values = _links(topology)
    links = {
        _key(_field(item, "link_id"), label="link ID"): item
        for item in link_values
    }
    if len(links) != len(link_values):
        raise SpatialContactResolutionError("TOPOLOGY_INVALID", "duplicate link IDs")
    row_data = _certificate_rows(cert)
    if not row_data:
        raise SpatialContactResolutionError("PAD_CERTIFICATE_MISSING", "landing aggregates absent")
    connection_analysis = _field(scenario, "connection_analysis")
    connections = (
        _field(connection_analysis, "connections")
        if connection_analysis is not None
        else None
    )
    if not isinstance(connections, Mapping):
        raise SpatialContactResolutionError("CONTACT_INCOMPLETE", "connection analysis missing")
    analysis_source = _field(connection_analysis, "source_sha256")
    if analysis_source is None or _sha(analysis_source, label="analysis source hash") != source:
        raise SpatialContactResolutionError("HASH_MISMATCH", "connection analysis source differs")
    project = _field(scenario, "normalized_project")
    pins = _field(project, "pins", ())
    pin_rows = _rows(cert.get("terminal_contacts", ()))
    if pins and not pin_rows:
        raise SpatialContactResolutionError(
            "DEVICE_CONTACT_CERTIFICATE_MISSING", "device terminal rows absent"
        )
    pin_row_by_id: dict[str, Mapping[str, Any]] = {}
    for row in pin_rows:
        key = _key(_row_value(row, "pin_id"), label="terminal pin ID")
        if key in pin_row_by_id:
            raise SpatialContactResolutionError(
                "DEVICE_CONTACT_AMBIGUOUS", f"terminal pin {key!r} is duplicated"
            )
        pin_row_by_id[key] = row
    rails = tuple(_field(project, "rails", ()) or ())
    gnd_aliases = {
        _key(item, label="ground alias") for item in tuple(
            _field(project, "gnd_aliases", ()) or ()
        )
    }
    contacts: list[SpatialMeshContact] = []
    skipped: list[SpatialContactSkip] = []
    seen_vias: dict[str, tuple[str, str]] = {}
    topology_vertices = {
        _text(value, label="quotient vertex") for value in vertices.values()
    }

    def add_contact(contact_id: str, kind: str, refdes: str, terminal: str, net: str,
                    layer: str, x: float, y: float, rotation: float, via_id: str | None,
                    pad: tuple[str, float, float, str], landing: object) -> None:
        vertex, edge, map_source = _resolve_destination(
            vertices, edge_map, links, landing
        )
        if vertex not in topology_vertices:
            raise SpatialContactResolutionError("TOPOLOGY_INCIDENCE_MISSING",
                                                f"vertex {vertex!r} not in quotient")
        link = links[edge.casefold()]
        owners = tuple(_field(link, "owner_ids", ()) or ())
        expected_owner = f"via:{_text(via_id, label='source Via ID')}"
        if (
            len(owners) != 1
            or _key(owners[0], label="owner") != expected_owner.casefold()
        ):
            raise SpatialContactResolutionError(
                "OWNER_MISMATCH",
                f"edge {edge!r} is not exclusively owned by {expected_owner!r}",
            )
        owner = _text(owners[0], label="owner")
        provenance = {
            "source": map_source,
            "terminal_contacts_only": True,
            "substrate_via_spatial_coverage": False,
        }
        identity_payload = {
            "contact_id": contact_id,
            "contact_kind": kind,
            "refdes": refdes,
            "terminal": terminal,
            "net": net,
            "layer": layer,
            "x_um": x,
            "y_um": y,
            "rotation_degrees": rotation,
            "quotient_vertex_id": vertex,
            "first_edge_id": edge,
            "owner_id": owner,
            "island_id": pad[3],
            "pad_kind": pad[0],
            "pad_width_um": pad[1],
            "pad_height_um": pad[2],
            "source_via_id": via_id,
            "provenance": provenance,
        }
        contacts.append(
            SpatialMeshContact(
                contact_id, kind, refdes, terminal, net, layer, x, y, rotation,
                vertex, edge, owner, pad[3], pad[0], pad[1], pad[2], via_id,
                provenance, _canonical_hash(identity_payload),
            )
        )

    decaps = tuple(_field(scenario, "decaps", ()) or ())
    decap_by_key: dict[str, object] = {}
    for item in decaps:
        key = _key(_field(item, "refdes"), label="refdes")
        if key in decap_by_key:
            raise SpatialContactResolutionError(
                "CONTACT_DUPLICATE", f"decap {key!r} is duplicated"
            )
        decap_by_key[key] = item
    connection_by_refdes: dict[str, object] = {}
    for raw_key, connection in connections.items():
        refdes = _text(_field(connection, "refdes", raw_key), label="refdes")
        key = refdes.casefold()
        if key in connection_by_refdes:
            raise SpatialContactResolutionError(
                "CONTACT_DUPLICATE", f"connection {refdes!r} is duplicated"
            )
        if _key(raw_key, label="connection key") != key:
            raise SpatialContactResolutionError(
                "CONTACT_IDENTITY_MISMATCH",
                f"connection key {raw_key!r} differs from {refdes!r}",
            )
        connection_by_refdes[key] = connection
    if set(connection_by_refdes) != set(decap_by_key):
        raise SpatialContactResolutionError(
            "CONTACT_INCOMPLETE",
            "connection analysis does not exactly cover the scenario decaps",
        )
    for refdes_key, connection in connection_by_refdes.items():
        refdes = _text(_field(connection, "refdes"), label="refdes")
        decap = decap_by_key.get(refdes.casefold())
        if decap is None:
            raise SpatialContactResolutionError("CONTACT_INCOMPLETE", f"decap {refdes!r} missing")
        kind = _text(_field(connection, "kind"), label="connection kind").upper()
        if kind == "SHARED_DUMMY":
            _text(_field(connection, "cluster_id"), label="shared cluster ID")
            if tuple(_field(connection, "power_vias", ()) or ()) or tuple(
                _field(connection, "ground_vias", ()) or ()
            ):
                raise SpatialContactResolutionError(
                    "CONTACT_INVALID", f"dummy {refdes!r} carries physical Via evidence"
                )
            skipped.append(
                SpatialContactSkip(
                    refdes,
                    kind,
                    "dummy has no physical plane landing; anchor/shared network only",
                    {"terminal_contacts_only": True},
                )
            )
            continue
        if kind not in {"DIRECT", "SHARED_ANCHOR"}:
            raise SpatialContactResolutionError(
                "CONTACT_UNRESOLVED", f"unsupported {kind} for {refdes}"
            )
        for terminal, field_name in (("PWR", "power_vias"), ("GND", "ground_vias")):
            pad = _field(decap, "pwr_pad" if terminal == "PWR" else "gnd_pad")
            for landing in tuple(_field(connection, field_name, ()) or ()):
                via_key = _key(_field(landing, "via_id"), label="via ID")
                if via_key in seen_vias:
                    raise SpatialContactResolutionError(
                        "CONTACT_DUPLICATE", f"Via {via_key!r} is emitted more than once"
                    )
                unit_id = _field(connection, "cluster_id") or refdes
                unit = (_text(unit_id, label="unit"), terminal)
                seen_vias[via_key] = unit
                rail_id = _text(_field(decap, "current_rail_id"), label="decap rail ID")
                rail_matches = [
                    item
                    for item in rails
                    if _key(_field(item, "rail_id"), label="rail ID")
                    == rail_id.casefold()
                ]
                if len(rail_matches) != 1:
                    raise SpatialContactResolutionError(
                        "RAIL_IDENTITY_MISSING",
                        f"decap {refdes!r} has no unique current rail",
                    )
                rail = rail_matches[0]
                expected_layer = _text(
                    _field(rail, "pwr_layer" if terminal == "PWR" else "gnd_layer"),
                    label="rail layer",
                )
                path_evidence = tuple(_field(landing, "path_evidence", ()) or ())
                matching_paths = [
                    item
                    for item in path_evidence
                    if _key(_field(item, "target_layer"), label="target layer")
                    == expected_layer.casefold()
                ]
                if len(matching_paths) != 1:
                    raise SpatialContactResolutionError(
                        "CONTACT_INCOMPLETE",
                        f"Via {via_key!r} has no unique path for {expected_layer!r}",
                    )
                path = matching_paths[0]
                pad_data = _pad_aggregate(row_data, landing, path)
                net = _text(_field(landing, "net"), label="landing net")
                expected = (
                    _text(_field(decap, "current_net"), label="decap net")
                    if terminal == "PWR"
                    else net
                )
                if terminal == "PWR" and net.casefold() != expected.casefold():
                    raise SpatialContactResolutionError(
                        "NET_MISMATCH", f"Via {via_key!r} net mismatch"
                    )
                target_layer = _text(_field(path, "target_layer"), label="target layer")
                if expected_layer.casefold() != target_layer.casefold():
                    raise SpatialContactResolutionError(
                        "LAYER_MISMATCH",
                        f"Via {via_key!r} is not on the selected rail layer",
                    )
                if terminal == "PWR" and _key(
                    _field(rail, "net"), label="rail net"
                ) != _key(_field(decap, "current_net"), label="decap net"):
                    raise SpatialContactResolutionError(
                        "NET_MISMATCH", f"decap {refdes!r} rail NET differs"
                    )
                if (
                    terminal == "GND"
                    and gnd_aliases
                    and _key(net, label="ground net") not in gnd_aliases
                ):
                    raise SpatialContactResolutionError(
                        "NET_MISMATCH", f"Via {via_key!r} is not a configured ground net"
                    )
                add_contact(f"{refdes}:{terminal}:{_field(landing, 'via_id')}", kind, refdes,
                            terminal,
                            net,
                            _field(path, "target_layer"),
                            float(_field(landing, "x_um")),
                            float(_field(landing, "y_um")),
                            float(_field(landing, "rotation_degrees", 0.0)),
                            _field(landing, "via_id"), pad_data, landing)

    if tuple(pins or ()):
        raise SpatialContactResolutionError(
            "DEVICE_PAD_GEOMETRY_MISSING",
            "saved Device contacts do not retain exact finite pad geometry",
        )
    contacts.sort(key=lambda item: item.contact_id.casefold())
    skipped.sort(key=lambda item: item.refdes.casefold())
    payload = {"source_sha256": source, "certificate_evidence_sha256": evidence,
               "contacts": [item.identity_sha256 for item in contacts],
               "skipped": [
                   {"refdes": item.refdes, "classification": item.classification,
                    "reason": item.reason, "provenance": dict(item.provenance)}
                   for item in skipped
               ],
               "provenance": {"terminal_contacts_only": True,
                              "substrate_via_spatial_coverage": False,
                              "contact_count": len(contacts)}}
    return SpatialContactResolution(tuple(contacts), tuple(skipped), source, evidence,
                                   _canonical_hash(payload),
                                   payload["provenance"])


class SpatialContactResolver:
    """Typed facade retained for future production wiring."""

    @staticmethod
    def resolve(scenario: object, topology: object, **kwargs: object) -> SpatialContactResolution:
        return resolve_spatial_contacts(scenario, topology, **kwargs)

    resolve_terminal_contacts = resolve


resolve_spatial_mesh_contacts = resolve_spatial_contacts


__all__ = ["SpatialContactResolutionError", "SpatialMeshContact",
           "SpatialContactSkip", "SpatialContactResolution",
           "SpatialContactResolver", "resolve_spatial_contacts",
           "resolve_spatial_mesh_contacts"]
