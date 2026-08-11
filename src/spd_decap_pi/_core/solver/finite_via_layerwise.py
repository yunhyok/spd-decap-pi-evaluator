"""Validated v4 finite-Via quotient consumer for the layerwise solver.

The SPD importer deliberately persists a compact quotient of the raw
same-layer Trace/artwork graph.  Every retained raw Via belongs to exactly one
finite edge and every editable terminal landing remains an exposed quotient
vertex.  This module is the narrow, fail-closed boundary that turns that
certificate into :class:`LayerSurfaceViaLink` and external Device-port data.

It does not compile a mutable Distribution state.  Scenario contacts,
retargeted first-Via edges, shared-pad aliases and isolation-gap cuts are
applied by the scenario topology compiler on top of this immutable base.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from .layer_surface_network import LayerSurfacePort, LayerSurfaceViaLink


FINITE_VIA_SURFACE_SCHEMA = "spd-layer-surface-connectivity-v4"
FINITE_VIA_SURFACE_COMPILER = (
    "powersi-same-layer-trace-artwork-finite-via-quotient-v4"
)
FINITE_VIA_QUOTIENT_SCHEMA = "spd-finite-via-quotient-v1"


class FiniteViaCertificateError(ValueError):
    """Actionable integrity or topology failure in one v4 certificate."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "FINITE_VIA_CERTIFICATE_INVALID"
        super().__init__(f"Finite-Via certificate [{self.code}]: {message}")


def _key(value: Any, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise FiniteViaCertificateError(
            "IDENTITY_MISSING", f"{label} must not be blank"
        )
    return text.casefold()


def _is_sha256(value: Any) -> bool:
    text = str(value).strip().casefold()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _canonical_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FiniteViaCertificateError(
            "EVIDENCE_NOT_CANONICAL", f"certificate is not canonical JSON: {exc}"
        ) from exc


def _canonical_sha256(payload: Any) -> str:
    return sha256(_canonical_bytes(payload)).hexdigest()


def _canonical_sequence_sha256(
    values: Iterable[Any],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    """Hash canonical JSON for a sequence without materialising the sequence.

    This is byte-for-byte equivalent to ``_canonical_sha256(list(values))``.
    The explicit array delimiters and separators let million-row topology
    manifests retain their prior canonical semantics with O(one row) transient
    memory.
    """

    digest = sha256()
    cancelled = is_cancelled or (lambda: False)
    digest.update(b"[")
    first = True
    for ordinal, value in enumerate(values):
        if ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED",
                "compiled topology identity calculation was cancelled",
            )
        if not first:
            digest.update(b",")
        digest.update(_canonical_bytes(value))
        first = False
    digest.update(b"]")
    return digest.hexdigest()


def _rows(
    value: Any,
    *,
    label: str,
    allow_empty: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or (not value and not allow_empty)
    ):
        raise FiniteViaCertificateError(
            "MANIFEST_INVALID", f"{label} must be a sequence of mapping rows"
        )
    cancelled = is_cancelled or (lambda: False)
    for ordinal, item in enumerate(value):
        if ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED", f"{label} validation was cancelled"
            )
        if not isinstance(item, Mapping):
            raise FiniteViaCertificateError(
                "MANIFEST_INVALID", f"{label} must be a sequence of mapping rows"
            )
    return tuple(value)


def _int_field(row: Mapping[str, Any], name: str, *, minimum: int = 0) -> int:
    value = row.get(name)
    if isinstance(value, bool):
        raise FiniteViaCertificateError(
            "COUNT_INVALID", f"{name} must be an integer"
        )
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise FiniteViaCertificateError(
            "COUNT_INVALID", f"{name} must be an integer"
        ) from exc
    if result < minimum or result != value:
        raise FiniteViaCertificateError(
            "COUNT_INVALID", f"{name} must be >= {minimum}"
        )
    return result


def _float_field(
    row: Mapping[str, Any], name: str, *, nonnegative: bool = True
) -> float:
    value = row.get(name)
    if isinstance(value, bool):
        raise FiniteViaCertificateError(
            "PHYSICAL_MODEL_INVALID", f"{name} must be numeric"
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise FiniteViaCertificateError(
            "PHYSICAL_MODEL_INVALID", f"{name} must be numeric"
        ) from exc
    if not isfinite(result) or (nonnegative and result < 0.0):
        raise FiniteViaCertificateError(
            "PHYSICAL_MODEL_INVALID", f"{name} is not finite/passive"
        )
    return result


@dataclass(frozen=True, slots=True)
class FiniteViaRailPortEvidence:
    rail_id: str
    selected_net: str
    reference_net: str
    positive_node_id: str
    negative_node_id: str
    positive_pin_ids: tuple[str, ...]
    negative_pin_ids: tuple[str, ...]
    positive_anchor_node_ids: tuple[str, ...]
    negative_anchor_node_ids: tuple[str, ...]

    @property
    def port(self) -> LayerSurfacePort:
        return LayerSurfacePort(
            self.rail_id, self.positive_node_id, self.negative_node_id
        )


@dataclass(frozen=True, slots=True)
class CompiledFiniteViaBaseTopology:
    """Immutable source topology decoded from one exact v4 certificate."""

    certificate: Mapping[str, Any]
    certificate_evidence_sha256: str
    source_sha256: str
    quotient_vertex_ids: tuple[str, ...]
    external_port_node_ids: tuple[str, ...]
    topology_links: tuple[LayerSurfaceViaLink, ...]
    finite_links: tuple[LayerSurfaceViaLink, ...]
    rail_ports: tuple[FiniteViaRailPortEvidence, ...]
    omitted_rail_ids: tuple[str, ...]
    owner_edge_by_id: Mapping[str, str]
    vertex_by_landing_key: Mapping[tuple[str, str], str]
    first_edge_by_landing_key: Mapping[tuple[str, str], str]
    topology_identity_sha256: str

    @property
    def via_links(self) -> tuple[LayerSurfaceViaLink, ...]:
        return (*self.topology_links, *self.finite_links)


def _finite_link_identity_manifest(link: LayerSurfaceViaLink) -> dict[str, Any]:
    """Canonical identity row for one ownership-complete finite link."""

    return {
        "link_id": link.link_id,
        "owners": list(link.owner_ids),
        "first": link.first_node_id,
        "second": link.second_node_id,
        "count": link.count,
        "r": link.resistance_ohm_per_via,
        "l": link.inductance_h_per_via,
    }


def compiled_finite_via_topology_identity_sha256(
    *,
    certificate_evidence_sha256: str,
    artwork_node_ids: Sequence[str],
    quotient_vertex_ids: Sequence[str],
    external_port_node_ids: Sequence[str],
    topology_links: Sequence[LayerSurfaceViaLink],
    finite_links: Sequence[LayerSurfaceViaLink],
    rail_ports: Sequence[FiniteViaRailPortEvidence],
    omitted_rail_ids: Sequence[str],
    is_cancelled: Callable[[], bool] | None = None,
) -> str:
    """Hash the complete compiled v4 topology without materialising manifests.

    The persisted compiled-topology asset uses this same calculation after its
    bounded, typed decode.  That keeps the fast path bound to the exact
    topology identity produced by the full canonical-certificate compiler.
    """

    evidence = str(certificate_evidence_sha256).strip().casefold()
    if not _is_sha256(evidence):
        raise FiniteViaCertificateError(
            "CERTIFICATE_INTEGRITY_FAILED",
            "compiled topology names an invalid certificate evidence SHA-256",
        )
    artwork = tuple(str(item).strip() for item in artwork_node_ids)
    quotient = tuple(str(item).strip() for item in quotient_vertex_ids)
    external = tuple(str(item).strip() for item in external_port_node_ids)
    omitted = tuple(str(item).strip() for item in omitted_rail_ids)
    return _canonical_sha256(
        {
            "schema": "finite-via-layerwise-base-v2",
            "certificate_evidence_sha256": evidence,
            "artwork_node_ids": sorted(artwork),
            "quotient_vertex_manifest": {
                "schema": "canonical-json-sequence-sha256-v1",
                "count": len(quotient),
                "sha256": _canonical_sequence_sha256(
                    quotient, is_cancelled=is_cancelled
                ),
            },
            "external_port_node_ids": list(external),
            "topology_link_manifest": {
                "schema": "canonical-json-sequence-sha256-v1",
                "count": len(topology_links),
                "sha256": _canonical_sequence_sha256(
                    (
                        _finite_link_identity_manifest(item)
                        for item in topology_links
                    ),
                    is_cancelled=is_cancelled,
                ),
            },
            "finite_link_manifest": {
                "schema": "canonical-json-sequence-sha256-v1",
                "count": len(finite_links),
                "sha256": _canonical_sequence_sha256(
                    (
                        _finite_link_identity_manifest(item)
                        for item in finite_links
                    ),
                    is_cancelled=is_cancelled,
                ),
            },
            "ports": [
                {
                    "rail_id": item.rail_id,
                    "positive": item.positive_node_id,
                    "negative": item.negative_node_id,
                }
                for item in rail_ports
            ],
            "omitted": list(omitted),
        }
    )


def _geometry_asset_identity(row: Mapping[str, Any]) -> tuple[str, str, str, str, tuple[str, ...]]:
    layer = _key(row.get("layer", ""), label="geometry layer")
    net = _key(row.get("net", ""), label="geometry NET")
    asset = str(row.get("asset", "")).strip()
    digest = str(row.get("asset_sha256", "")).strip().casefold()
    raw_islands = row.get("island_ids")
    if (
        not asset
        or not _is_sha256(digest)
        or not isinstance(raw_islands, Sequence)
        or isinstance(raw_islands, (str, bytes, bytearray))
    ):
        raise FiniteViaCertificateError(
            "GEOMETRY_MANIFEST_INVALID", "geometry asset identity is incomplete"
        )
    islands = tuple(sorted(str(item).strip() for item in raw_islands))
    if not islands or any(not item for item in islands) or len(set(islands)) != len(islands):
        raise FiniteViaCertificateError(
            "GEOMETRY_MANIFEST_INVALID", "geometry island IDs are blank or duplicated"
        )
    return layer, net, asset, digest, islands


def _validated_component_index(
    certificate: Mapping[str, Any],
    artwork_node_ids: set[str],
    *,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, str]]:
    cancelled = is_cancelled or (lambda: False)
    report = progress or (lambda _value, _message: None)
    rows = _rows(
        certificate.get("surface_equivalence_components"),
        label="surface equivalence components",
        is_cancelled=cancelled,
    )
    component_by_id: dict[str, Mapping[str, Any]] = {}
    component_by_island: dict[str, str] = {}
    report(0, f"Validating {len(rows):,} surface equivalence components")
    for row_ordinal, row in enumerate(rows):
        if row_ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED",
                "surface component validation was cancelled",
            )
        if row_ordinal % 100_000 == 0:
            report(
                round(100 * row_ordinal / max(1, len(rows))),
                f"Validating surface components ({row_ordinal:,}/{len(rows):,})",
            )
        component_id = str(row.get("component_id", "")).strip()
        component_key = _key(component_id, label="surface component ID")
        net = _key(row.get("net", ""), label="surface component NET")
        layer = _key(row.get("layer", ""), label="surface component layer")
        evidence = str(row.get("component_evidence_sha256", "")).strip().casefold()
        raw_islands = row.get("island_ids")
        if (
            component_key in component_by_id
            or not _is_sha256(evidence)
            or str(row.get("contact_status", "")).strip().casefold()
            not in {"complete", "uncontacted"}
            or not isinstance(raw_islands, Sequence)
            or isinstance(raw_islands, (str, bytes, bytearray))
        ):
            raise FiniteViaCertificateError(
                "SURFACE_COMPONENT_INVALID",
                "surface components must be unique, contacted and hash-bound",
            )
        islands = tuple(str(item).strip() for item in raw_islands)
        if not islands or len(set(islands)) != len(islands):
            raise FiniteViaCertificateError(
                "SURFACE_COMPONENT_INVALID", "surface component islands are invalid"
            )
        for island_id in islands:
            if island_id not in artwork_node_ids or island_id in component_by_island:
                raise FiniteViaCertificateError(
                    "SURFACE_COMPONENT_PARTITION_INVALID",
                    f"artwork island {island_id!r} is absent or appears more than once",
                )
            component_by_island[island_id] = component_id
        # Read these identities now so blank rows cannot survive merely because
        # they are not selected by a particular rail.
        _ = net, layer
        component_by_id[component_key] = row
    if set(component_by_island) != artwork_node_ids:
        raise FiniteViaCertificateError(
            "SURFACE_COMPONENT_PARTITION_INVALID",
            "surface components do not exactly partition retained artwork islands",
        )
    report(100, f"Validated {len(rows):,} surface equivalence components")
    return component_by_id, component_by_island


def _validated_quotient(
    certificate: Mapping[str, Any],
    *,
    artwork_node_ids: set[str],
    component_by_id: Mapping[str, Mapping[str, Any]],
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> tuple[
    tuple[str, ...],
    tuple[LayerSurfaceViaLink, ...],
    tuple[LayerSurfaceViaLink, ...],
    dict[str, str],
    dict[tuple[str, str], str],
    dict[tuple[str, str], str],
]:
    cancelled = is_cancelled or (lambda: False)
    report = progress or (lambda _value, _message: None)
    quotient = certificate.get("finite_via_quotient")
    if not isinstance(quotient, Mapping):
        raise FiniteViaCertificateError(
            "FINITE_QUOTIENT_MISSING", "finite_via_quotient is absent"
        )
    if (
        quotient.get("schema_version") != FINITE_VIA_QUOTIENT_SCHEMA
        or str(quotient.get("status", "")).strip().casefold() != "complete"
    ):
        raise FiniteViaCertificateError(
            "FINITE_QUOTIENT_INCOMPLETE",
            "only a complete v1 finite-Via quotient is supported",
        )
    vertex_rows = _rows(
        quotient.get("vertices"),
        label="finite quotient vertices",
        is_cancelled=cancelled,
    )
    edge_rows = _rows(
        quotient.get("edges"),
        label="finite quotient edges",
        is_cancelled=cancelled,
    )
    binding_rows = _rows(
        quotient.get("terminal_bindings"),
        label="finite quotient terminal bindings",
        allow_empty=True,
        is_cancelled=cancelled,
    )

    vertex_by_id: dict[str, Mapping[str, Any]] = {}
    topology_links: list[LayerSurfaceViaLink] = []
    topology_owner_ids: set[str] = set()
    report(0, f"Validating {len(vertex_rows):,} finite quotient vertices")
    for row_ordinal, row in enumerate(vertex_rows):
        if row_ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED",
                "finite quotient vertex validation was cancelled",
            )
        if row_ordinal % 100_000 == 0:
            report(
                round(30 * row_ordinal / max(1, len(vertex_rows))),
                f"Validating quotient vertices ({row_ordinal:,}/{len(vertex_rows):,})",
            )
        vertex_id = str(row.get("vertex_id", "")).strip()
        vertex_key = _key(vertex_id, label="finite quotient vertex ID")
        net = _key(row.get("net", ""), label="finite quotient vertex NET")
        vertex_layer = _key(
            row.get("layer", ""), label="finite quotient vertex layer"
        )
        source_count = _int_field(row, "source_node_count", minimum=1)
        source_digest = str(row.get("source_node_ids_sha256", "")).strip().casefold()
        issues = row.get("component_binding_issues")
        raw_component_ids = row.get("retained_component_ids")
        raw_component_evidence = row.get("retained_component_evidence_sha256s")
        if (
            not vertex_id.startswith("spd-finite-via-vertex:")
            or vertex_key in vertex_by_id
            or not _is_sha256(source_digest)
            or str(row.get("component_binding_status", "")).strip().casefold()
            != "complete"
            or not isinstance(issues, Sequence)
            or isinstance(issues, (str, bytes, bytearray))
            or len(issues) != 0
            or not isinstance(raw_component_ids, Sequence)
            or isinstance(raw_component_ids, (str, bytes, bytearray))
            or not isinstance(raw_component_evidence, Sequence)
            or isinstance(raw_component_evidence, (str, bytes, bytearray))
            or len(raw_component_ids) != len(raw_component_evidence)
        ):
            raise FiniteViaCertificateError(
                "FINITE_VERTEX_INVALID",
                f"finite quotient vertex {vertex_id!r} is incomplete",
            )
        vertex_by_id[vertex_key] = row
        retained_islands: set[str] = set()
        for raw_component_id, raw_evidence in zip(
            raw_component_ids, raw_component_evidence, strict=True
        ):
            component_id = str(raw_component_id).strip()
            component = component_by_id.get(component_id.casefold())
            if (
                component is None
                or str(component.get("net", "")).strip().casefold() != net
                or str(component.get("layer", "")).strip().casefold()
                != vertex_layer
                or str(component.get("contact_status", "")).strip().casefold()
                != "complete"
                or str(component.get("component_evidence_sha256", ""))
                .strip()
                .casefold()
                != str(raw_evidence).strip().casefold()
            ):
                raise FiniteViaCertificateError(
                    "FINITE_VERTEX_COMPONENT_MISMATCH",
                    f"vertex {vertex_id!r} references a stale surface component",
                )
            retained_islands.update(
                str(item).strip() for item in component.get("island_ids", ())
            )
        declared_rows = row.get("retained_component_island_ids_by_layer")
        if not isinstance(declared_rows, Mapping):
            raise FiniteViaCertificateError(
                "FINITE_VERTEX_COMPONENT_MISMATCH",
                f"vertex {vertex_id!r} has no retained-island binding",
            )
        declared_islands = {
            str(item).strip()
            for raw_ids in declared_rows.values()
            if isinstance(raw_ids, Sequence)
            and not isinstance(raw_ids, (str, bytes, bytearray))
            for item in raw_ids
        }
        if retained_islands != declared_islands or not retained_islands <= artwork_node_ids:
            raise FiniteViaCertificateError(
                "FINITE_VERTEX_COMPONENT_MISMATCH",
                f"vertex {vertex_id!r} retained-island rows disagree",
            )
        for ordinal, island_id in enumerate(sorted(retained_islands), start=1):
            owner_id = f"finite-vertex-surface:{vertex_id}:{ordinal}"
            owner_key = owner_id.casefold()
            if owner_key in topology_owner_ids:
                raise FiniteViaCertificateError(
                    "TOPOLOGY_OWNER_DUPLICATED", "vertex/surface owner IDs collide"
                )
            topology_owner_ids.add(owner_key)
            topology_links.append(
                LayerSurfaceViaLink(
                    link_id=f"finite-vertex-surface-link:{sha256(owner_key.encode('utf-8')).hexdigest()[:24]}",
                    first_node_id=vertex_id,
                    second_node_id=island_id,
                    count=1,
                    mode="topology_only_ideal",
                    owner_ids=(owner_id,),
                )
            )
        _ = source_count

    finite_links: list[LayerSurfaceViaLink] = []
    owner_edge_by_id: dict[str, str] = {}
    edge_by_id: dict[str, Mapping[str, Any]] = {}
    owner_ordinal = 0
    report(30, f"Validating {len(edge_rows):,} finite quotient edges")
    for row_ordinal, row in enumerate(edge_rows):
        if row_ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED",
                "finite quotient edge validation was cancelled",
            )
        if row_ordinal % 100_000 == 0:
            report(
                30 + round(35 * row_ordinal / max(1, len(edge_rows))),
                f"Validating quotient edges ({row_ordinal:,}/{len(edge_rows):,})",
            )
        edge_id = str(row.get("edge_id", "")).strip()
        edge_key = _key(edge_id, label="finite quotient edge ID")
        start_id = str(row.get("start_vertex_id", "")).strip()
        end_id = str(row.get("end_vertex_id", "")).strip()
        edge_net = _key(row.get("net", ""), label="finite quotient edge NET")
        count = _int_field(row, "parallel_path_count", minimum=1)
        per_path = _int_field(row, "per_path_via_count", minimum=1)
        raw_count = _int_field(row, "raw_via_count", minimum=1)
        resistance = _float_field(row, "resistance_ohm")
        inductance = _float_field(row, "inductance_h")
        owners_raw = row.get("owner_ids")
        issues = row.get("physical_model_issues")
        endpoint_issues = row.get("endpoint_binding_issues")
        if (
            not edge_id.startswith("spd-finite-via-edge:")
            or edge_key in edge_by_id
            or start_id.casefold() not in vertex_by_id
            or end_id.casefold() not in vertex_by_id
            or start_id.casefold() == end_id.casefold()
            or str(vertex_by_id[start_id.casefold()].get("net", "")).strip().casefold()
            != edge_net
            or str(vertex_by_id[end_id.casefold()].get("net", "")).strip().casefold()
            != edge_net
            or raw_count != count * per_path
            or resistance < 0.0
            or inductance < 0.0
            or (resistance == 0.0 and inductance == 0.0)
            or str(row.get("status", "")).strip().casefold() != "complete"
            or str(row.get("physical_model_status", "")).strip().casefold()
            != "complete"
            or str(row.get("endpoint_binding_status", "")).strip().casefold()
            != "complete"
            or not isinstance(issues, Sequence)
            or isinstance(issues, (str, bytes, bytearray))
            or len(issues) != 0
            or not isinstance(endpoint_issues, Sequence)
            or isinstance(endpoint_issues, (str, bytes, bytearray))
            or len(endpoint_issues) != 0
            or not isinstance(owners_raw, Sequence)
            or isinstance(owners_raw, (str, bytes, bytearray))
        ):
            raise FiniteViaCertificateError(
                "FINITE_EDGE_INVALID", f"finite quotient edge {edge_id!r} is incomplete"
            )
        owners = tuple(str(item).strip() for item in owners_raw)
        if (
            len(owners) != raw_count
            or any(not item.startswith("via:") for item in owners)
            or len({item.casefold() for item in owners}) != len(owners)
        ):
            raise FiniteViaCertificateError(
                "FINITE_OWNER_LEDGER_INVALID",
                f"edge {edge_id!r} does not own each raw Via exactly once",
            )
        for owner in owners:
            owner_ordinal += 1
            if owner_ordinal % 10_000 == 0 and cancelled():
                raise FiniteViaCertificateError(
                    "COMPILED_TOPOLOGY_CANCELLED",
                    "finite quotient owner validation was cancelled",
                )
            previous = owner_edge_by_id.setdefault(owner.casefold(), edge_id)
            if previous != edge_id:
                raise FiniteViaCertificateError(
                    "FINITE_OWNER_LEDGER_INVALID",
                    f"owner {owner!r} is assigned to multiple finite edges",
                )
        finite_links.append(
            LayerSurfaceViaLink(
                link_id=edge_id,
                first_node_id=start_id,
                second_node_id=end_id,
                count=count,
                mode="finite_parallel_rl",
                resistance_ohm_per_via=resistance,
                inductance_h_per_via=inductance,
                owner_ids=owners,
            )
        )
        edge_by_id[edge_key] = row

    coverage = quotient.get("coverage")
    if not isinstance(coverage, Mapping):
        raise FiniteViaCertificateError(
            "FINITE_COVERAGE_MISSING", "finite quotient coverage is absent"
        )
    modeled_count = _int_field(coverage, "modeled_global_via_count")
    raw_count = _int_field(coverage, "raw_target_via_count")
    outside_count = _int_field(coverage, "outside_scope_via_count")
    pruned_count = _int_field(coverage, "pruned_dangling_via_count")
    physical_complete = _int_field(coverage, "physical_complete_via_count")
    physical_incomplete = _int_field(coverage, "physical_incomplete_via_count")
    if (
        str(coverage.get("status", "")).strip().casefold() != "complete"
        or str(coverage.get("owner_ledger_status", "")).strip().casefold()
        != "complete"
        or _int_field(coverage, "terminal_exclusive_via_count") != 0
        or modeled_count != len(owner_edge_by_id)
        or _int_field(coverage, "modeled_owner_count") != len(owner_edge_by_id)
        or _int_field(coverage, "modeled_owner_unique_count") != len(owner_edge_by_id)
        or raw_count != modeled_count + outside_count
        or pruned_count != outside_count
        or modeled_count != physical_complete + physical_incomplete
        or physical_incomplete != 0
    ):
        raise FiniteViaCertificateError(
            "FINITE_COVERAGE_INVALID",
            "finite quotient coverage does not exactly partition passive raw-Via owners",
        )
    for digest_name in (
        "raw_target_via_ids_sha256",
        "modeled_global_via_ids_sha256",
        "modeled_owner_ledger_sha256",
        "modeled_owner_edge_assignment_sha256",
    ):
        if not _is_sha256(coverage.get(digest_name)):
            raise FiniteViaCertificateError(
                "FINITE_COVERAGE_INVALID", f"{digest_name} is not SHA-256"
            )
    outside_digest = coverage.get(
        "outside_scope_via_ids_sha256",
        coverage.get("pruned_dangling_via_ids_sha256"),
    )
    if not _is_sha256(outside_digest):
        raise FiniteViaCertificateError(
            "FINITE_COVERAGE_INVALID",
            "outside/pruned Via disposition digest is not SHA-256",
        )
    canonical_owner_digest = sha256()
    report(65, f"Hashing {len(owner_edge_by_id):,} canonical Via owners")
    for owner_ordinal, owner in enumerate(sorted(owner_edge_by_id)):
        if owner_ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED",
                "finite quotient owner digest was cancelled",
            )
        if owner_ordinal % 100_000 == 0:
            report(
                65 + round(10 * owner_ordinal / max(1, len(owner_edge_by_id))),
                f"Hashing canonical Via owners ({owner_ordinal:,}/{len(owner_edge_by_id):,})",
            )
        encoded = owner.encode("utf-8")
        canonical_owner_digest.update(len(encoded).to_bytes(4, "big"))
        canonical_owner_digest.update(encoded)
    declared_canonical_owner_digest = str(
        coverage.get("modeled_owner_canonical_sha256", "")
    ).strip().casefold()
    if canonical_owner_digest.hexdigest() != declared_canonical_owner_digest:
        raise FiniteViaCertificateError(
            "FINITE_OWNER_LEDGER_INVALID",
            "canonical modeled-owner digest does not match the edge owner ledger",
        )
    assignment_digest = sha256()
    report(75, f"Hashing {len(owner_edge_by_id):,} Via-to-edge assignments")
    for owner_ordinal, (owner, edge_id) in enumerate(sorted(
        ((owner, edge_id) for owner, edge_id in owner_edge_by_id.items()),
        key=lambda item: (item[0], item[1].casefold(), item[1]),
    )):
        if owner_ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED",
                "finite quotient assignment digest was cancelled",
            )
        if owner_ordinal % 100_000 == 0:
            report(
                75 + round(15 * owner_ordinal / max(1, len(owner_edge_by_id))),
                "Hashing Via-to-edge assignments "
                f"({owner_ordinal:,}/{len(owner_edge_by_id):,})",
            )
        for token in (owner, edge_id.casefold()):
            encoded = token.encode("utf-8")
            assignment_digest.update(len(encoded).to_bytes(4, "big"))
            assignment_digest.update(encoded)
    if assignment_digest.hexdigest() != str(
        coverage.get("modeled_owner_edge_assignment_sha256", "")
    ).strip().casefold():
        raise FiniteViaCertificateError(
            "FINITE_OWNER_LEDGER_INVALID",
            "owner-to-edge assignment digest does not match the certificate",
        )

    vertex_by_landing: dict[tuple[str, str], str] = {}
    first_edge_by_landing: dict[tuple[str, str], str] = {}
    report(90, f"Validating {len(binding_rows):,} terminal bindings")
    for row_ordinal, row in enumerate(binding_rows):
        if row_ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED",
                "terminal binding validation was cancelled",
            )
        if row_ordinal % 100_000 == 0:
            report(
                90 + round(10 * row_ordinal / max(1, len(binding_rows))),
                f"Validating terminal bindings ({row_ordinal:,}/{len(binding_rows):,})",
            )
        raw_key = row.get("landing_key")
        if (
            not isinstance(raw_key, Sequence)
            or isinstance(raw_key, (str, bytes, bytearray))
            or len(raw_key) != 2
        ):
            raise FiniteViaCertificateError(
                "TERMINAL_BINDING_INVALID", "terminal landing key is invalid"
            )
        landing_key = tuple(str(item).strip().casefold() for item in raw_key)
        vertex_id = str(
            row.get("exposed_quotient_vertex_id", "") or ""
        ).strip()
        edge_id = str(
            row.get("first_via_quotient_edge_id", "") or ""
        ).strip()
        owner_id = str(row.get("first_via_owner_id", "") or "").strip()
        trace_first = landing_key[0].startswith("source-node:")
        bound_edge = edge_by_id.get(edge_id.casefold())
        if (
            not all(landing_key)
            or landing_key in vertex_by_landing
            or vertex_id.casefold() not in vertex_by_id
            or (
                not trace_first
                and (
                    bound_edge is None
                    or owner_edge_by_id.get(owner_id.casefold()) != edge_id
                    or vertex_id
                    not in {
                        str(bound_edge.get("start_vertex_id", "")).strip(),
                        str(bound_edge.get("end_vertex_id", "")).strip(),
                    }
                )
            )
            or (trace_first and (edge_id or owner_id))
            or str(row.get("global_quotient_binding_status", ""))
            .strip()
            .casefold()
            != "complete"
        ):
            raise FiniteViaCertificateError(
                "TERMINAL_BINDING_INVALID",
                f"terminal binding {landing_key!r} is not exact and complete",
        )
        vertex_by_landing[landing_key] = vertex_id
        if not trace_first:
            first_edge_by_landing[landing_key] = edge_id

    report(100, "Finite quotient validation complete")
    return (
        tuple(sorted((str(row.get("vertex_id")) for row in vertex_rows))),
        tuple(sorted(topology_links, key=lambda item: item.link_id)),
        tuple(sorted(finite_links, key=lambda item: item.link_id)),
        owner_edge_by_id,
        vertex_by_landing,
        first_edge_by_landing,
    )


def _rail_ports(
    project: Any,
    certificate: Mapping[str, Any],
    vertex_ids: set[str],
    *,
    required_rail_id: str | None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[
    tuple[FiniteViaRailPortEvidence, ...],
    tuple[str, ...],
    tuple[LayerSurfaceViaLink, ...],
]:
    cancelled = is_cancelled or (lambda: False)
    anchors = _rows(
        certificate.get("rail_anchor_bindings"),
        label="rail anchors",
        is_cancelled=cancelled,
    )
    contacts = _rows(
        certificate.get("terminal_contacts"),
        label="terminal contacts",
        is_cancelled=cancelled,
    )
    contact_by_pin: dict[str, Mapping[str, Any]] = {}
    for row_ordinal, row in enumerate(contacts):
        if row_ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED",
                "terminal contact validation was cancelled",
            )
        pin_id = str(row.get("pin_id", "")).strip()
        pin_key = _key(pin_id, label="terminal pin ID")
        if pin_key in contact_by_pin:
            raise FiniteViaCertificateError(
                "DEVICE_CONTACT_AMBIGUOUS", f"terminal pin {pin_id!r} is duplicated"
            )
        contact_by_pin[pin_key] = row
    anchors_by_rail: dict[str, list[Mapping[str, Any]]] = {}
    for row_ordinal, row in enumerate(anchors):
        if row_ordinal % 10_000 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED",
                "rail anchor validation was cancelled",
            )
        role = _key(row.get("role", ""), label="rail anchor role")
        if role not in {"power", "ground"}:
            raise FiniteViaCertificateError(
                "RAIL_ANCHOR_INVALID", f"unsupported rail anchor role {role!r}"
            )
        anchors_by_rail.setdefault(
            _key(row.get("rail_id", ""), label="rail anchor ID"), []
        ).append(row)

    aliases = {
        _key(item, label="configured GND alias"): str(item).strip()
        for item in getattr(project, "gnd_aliases", ())
    }
    if not aliases:
        raise FiniteViaCertificateError(
            "GROUND_ALIAS_MISSING", "project has no configured GND alias"
        )
    required_key = (
        _key(required_rail_id, label="required rail ID")
        if required_rail_id is not None
        else None
    )
    ports: list[FiniteViaRailPortEvidence] = []
    port_links: list[LayerSurfaceViaLink] = []
    omitted: list[str] = []
    seen: set[str] = set()
    for rail_ordinal, rail in enumerate(sorted(
        tuple(getattr(project, "rails", ())),
        key=lambda item: (
            str(getattr(item, "rail_id", "")).casefold(),
            str(getattr(item, "rail_id", "")),
        ),
    )):
        if rail_ordinal % 100 == 0 and cancelled():
            raise FiniteViaCertificateError(
                "COMPILED_TOPOLOGY_CANCELLED", "rail port validation was cancelled"
            )
        rail_id = str(getattr(rail, "rail_id", "")).strip()
        rail_key = _key(rail_id, label="rail ID")
        if rail_key in seen:
            raise FiniteViaCertificateError(
                "RAIL_PORT_AMBIGUOUS", f"rail {rail_id!r} is duplicated"
            )
        seen.add(rail_key)
        try:
            selected_net = str(getattr(rail, "net", "")).strip()
            selected_key = _key(selected_net, label="rail NET")
            rows = anchors_by_rail.get(rail_key, ())
            if not rows:
                raise FiniteViaCertificateError(
                    "RAIL_ANCHOR_MISSING", f"rail {rail_id!r} has no Device anchors"
                )
            by_branch: dict[str, dict[str, Mapping[str, Any]]] = {}
            for row in rows:
                branch = by_branch.setdefault(
                    _key(row.get("branch_id", ""), label="Device branch ID"), {}
                )
                role = _key(row.get("role", ""), label="Device anchor role")
                if role in branch:
                    raise FiniteViaCertificateError(
                        "RAIL_ANCHOR_INVALID",
                        f"rail {rail_id!r} duplicates one branch role",
                    )
                branch[role] = row
            if any(set(branch) != {"power", "ground"} for branch in by_branch.values()):
                raise FiniteViaCertificateError(
                    "RAIL_ANCHOR_INVALID",
                    f"rail {rail_id!r} lacks one PWR/GND pair per Device branch",
                )
            nodes_by_role: dict[str, set[str]] = {"power": set(), "ground": set()}
            pins_by_role: dict[str, list[str]] = {"power": [], "ground": []}
            nets_by_role: dict[str, set[str]] = {"power": set(), "ground": set()}
            for branch in by_branch.values():
                for role in ("power", "ground"):
                    pin_id = str(branch[role].get("pin_id", "")).strip()
                    contact = contact_by_pin.get(pin_id.casefold())
                    node_id = (
                        str(contact.get("exposed_quotient_vertex_id", "")).strip()
                        if contact is not None
                        else ""
                    )
                    net = (
                        str(contact.get("net", "")).strip()
                        if contact is not None
                        else ""
                    )
                    if (
                        contact is None
                        or str(contact.get("status", "")).strip().casefold()
                        != "complete"
                        or node_id not in vertex_ids
                        or not net
                    ):
                        raise FiniteViaCertificateError(
                            "DEVICE_CONTACT_INCOMPLETE",
                            f"rail {rail_id!r} pin {pin_id!r} lacks an exposed quotient vertex",
                        )
                    nodes_by_role[role].add(node_id)
                    nets_by_role[role].add(net.casefold())
                    pins_by_role[role].append(pin_id)
            if nets_by_role["power"] != {selected_key}:
                raise FiniteViaCertificateError(
                    "RAIL_PORT_NET_MISMATCH",
                    f"rail {rail_id!r} power Device anchors have the wrong NET",
                )
            if (
                len(nets_by_role["ground"]) != 1
                or not nets_by_role["ground"] <= set(aliases)
            ):
                raise FiniteViaCertificateError(
                    "RAIL_PORT_NET_MISMATCH",
                    f"rail {rail_id!r} ground Device anchors are not one configured GND NET",
                )
            anchor_nodes = {
                role: tuple(
                    sorted(nodes_by_role[role], key=lambda item: (item.casefold(), item))
                )
                for role in ("power", "ground")
            }

            def external_node(role: str) -> str:
                nodes = anchor_nodes[role]
                if not nodes:
                    raise FiniteViaCertificateError(
                        "DEVICE_CONTACT_INCOMPLETE",
                        f"rail {rail_id!r} has no {role} Device anchor vertex",
                    )
                if len(nodes) == 1:
                    return nodes[0]
                digest = _canonical_sha256(
                    {
                        "schema": "finite-via-external-device-supernode-v1",
                        "rail_id": rail_id.casefold(),
                        "role": role,
                        "anchor_node_ids": list(nodes),
                    }
                )
                node_id = f"spd-device-port-node:{digest[:24]}"
                for ordinal, anchor in enumerate(nodes, start=1):
                    owner = f"device-port-supernode:{rail_id}:{role}:{ordinal}:{digest}"
                    port_links.append(
                        LayerSurfaceViaLink(
                            link_id=f"device-port-supernode-link:{digest[:24]}:{ordinal}",
                            first_node_id=node_id,
                            second_node_id=anchor,
                            count=1,
                            mode="topology_only_ideal",
                            owner_ids=(owner,),
                        )
                    )
                return node_id

            positive = external_node("power")
            negative = external_node("ground")
            if positive == negative:
                raise FiniteViaCertificateError(
                    "RAIL_PORT_SHORTED", f"rail {rail_id!r} external port is shorted"
                )
            reference_key = next(iter(nets_by_role["ground"]))
            ports.append(
                FiniteViaRailPortEvidence(
                    rail_id=rail_id,
                    selected_net=selected_net,
                    reference_net=aliases[reference_key],
                    positive_node_id=positive,
                    negative_node_id=negative,
                    positive_pin_ids=tuple(sorted(pins_by_role["power"], key=str.casefold)),
                    negative_pin_ids=tuple(sorted(pins_by_role["ground"], key=str.casefold)),
                    positive_anchor_node_ids=anchor_nodes["power"],
                    negative_anchor_node_ids=anchor_nodes["ground"],
                )
            )
        except FiniteViaCertificateError:
            if rail_key == required_key:
                raise
            omitted.append(rail_id)
    if required_key is not None and not any(
        item.rail_id.casefold() == required_key for item in ports
    ):
        raise FiniteViaCertificateError(
            "RAIL_PORT_MISSING",
            f"required rail {required_rail_id!r} has no complete external Device port",
        )
    return (
        tuple(ports),
        tuple(sorted(omitted, key=lambda item: (item.casefold(), item))),
        tuple(sorted(port_links, key=lambda item: item.link_id)),
    )


def compile_finite_via_base_topology(
    project: Any,
    geometry_records: Sequence[Mapping[str, Any]],
    artwork_node_ids: Sequence[str],
    *,
    required_rail_id: str | None = None,
    certificate: Mapping[str, Any] | None = None,
    certificate_verified: bool = False,
    is_cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> CompiledFiniteViaBaseTopology:
    """Validate and decode the exact v4 quotient for one retained project."""

    cancelled = is_cancelled or (lambda: False)
    report = progress or (lambda _value, _message: None)
    report(0, "Checking finite-Via certificate identity and geometry")
    if cancelled():
        raise FiniteViaCertificateError(
            "COMPILED_TOPOLOGY_CANCELLED", "finite-Via compilation was cancelled"
        )
    metadata = getattr(project, "metadata", {})
    spd_import = metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    if certificate is None:
        certificate = (
            spd_import.get("layerwise_surface_connectivity_certificate")
            if isinstance(spd_import, Mapping)
            else None
        )
    if not isinstance(certificate, Mapping):
        raise FiniteViaCertificateError(
            "CERTIFICATE_MISSING", "project has no layer-surface certificate"
        )
    if (
        certificate.get("schema_version") != FINITE_VIA_SURFACE_SCHEMA
        or certificate.get("compiler_id") != FINITE_VIA_SURFACE_COMPILER
    ):
        raise FiniteViaCertificateError(
            "CERTIFICATE_UNSUPPORTED", "project does not carry the v4 finite-Via schema"
        )
    source_sha256 = str(certificate.get("source_sha256", "")).strip().casefold()
    expected_source = (
        str(spd_import.get("source_sha256", "")).strip().casefold()
        if isinstance(spd_import, Mapping)
        else ""
    )
    evidence = str(certificate.get("evidence_sha256", "")).strip().casefold()
    unsigned = {key: value for key, value in certificate.items() if key != "evidence_sha256"}
    if (
        not _is_sha256(source_sha256)
        or source_sha256 != expected_source
        or not _is_sha256(evidence)
        or (
            not certificate_verified
            and _canonical_sha256(unsigned) != evidence
        )
        or str(certificate.get("status", "")).strip().casefold() != "complete"
    ):
        raise FiniteViaCertificateError(
            "CERTIFICATE_INTEGRITY_FAILED",
            "v4 certificate is stale, incomplete, or has an invalid evidence SHA-256",
        )
    expected_assets = sorted(_geometry_asset_identity(row) for row in geometry_records)
    observed_assets = sorted(
        _geometry_asset_identity(row)
        for row in _rows(
            certificate.get("geometry_assets"),
            label="geometry assets",
            is_cancelled=cancelled,
        )
    )
    if expected_assets != observed_assets:
        raise FiniteViaCertificateError(
            "GEOMETRY_MANIFEST_MISMATCH",
            "certificate geometry assets differ from retained project records",
        )
    artwork_nodes = tuple(str(item).strip() for item in artwork_node_ids)
    if not artwork_nodes or any(not item for item in artwork_nodes) or len(set(artwork_nodes)) != len(artwork_nodes):
        raise FiniteViaCertificateError(
            "ARTWORK_NODE_MANIFEST_INVALID", "artwork node IDs must be unique and nonblank"
        )
    asset_islands = {
        island_id for _layer, _net, _asset, _digest, islands in expected_assets for island_id in islands
    }
    if asset_islands != set(artwork_nodes):
        raise FiniteViaCertificateError(
            "ARTWORK_NODE_MANIFEST_MISMATCH",
            "decoded artwork nodes differ from the certificate geometry inventory",
        )
    component_by_id, _component_by_island = _validated_component_index(
        certificate,
        set(artwork_nodes),
        is_cancelled=cancelled,
        progress=lambda value, message: report(
            10 + round(max(0, min(100, value)) * 10 / 100), message
        ),
    )
    (
        vertex_ids,
        topology_links,
        finite_links,
        owner_edge_by_id,
        vertex_by_landing,
        first_edge_by_landing,
    ) = _validated_quotient(
        certificate,
        artwork_node_ids=set(artwork_nodes),
        component_by_id=component_by_id,
        is_cancelled=cancelled,
        progress=lambda value, message: report(
            20 + round(max(0, min(100, value)) * 65 / 100), message
        ),
    )
    vertex_id_set = set(vertex_ids)
    if vertex_id_set.intersection(artwork_nodes):
        raise FiniteViaCertificateError(
            "GLOBAL_NODE_ID_COLLISION", "quotient and artwork node IDs collide"
        )
    ports, omitted, device_port_links = _rail_ports(
        project,
        certificate,
        vertex_id_set,
        required_rail_id=required_rail_id,
        is_cancelled=cancelled,
    )
    report(90, "Hashing compiled finite-Via topology identity")
    external_port_node_ids = tuple(
        sorted(
            {
                node_id
                for item in ports
                for node_id in (item.positive_node_id, item.negative_node_id)
                if node_id not in vertex_id_set
            }
        )
    )
    if set(external_port_node_ids).intersection(artwork_nodes):
        raise FiniteViaCertificateError(
            "GLOBAL_NODE_ID_COLLISION", "external Device and artwork node IDs collide"
        )
    topology_links = tuple(
        sorted((*topology_links, *device_port_links), key=lambda item: item.link_id)
    )
    topology_identity = compiled_finite_via_topology_identity_sha256(
        certificate_evidence_sha256=evidence,
        artwork_node_ids=artwork_nodes,
        quotient_vertex_ids=vertex_ids,
        external_port_node_ids=external_port_node_ids,
        topology_links=topology_links,
        finite_links=finite_links,
        rail_ports=ports,
        omitted_rail_ids=omitted,
        is_cancelled=cancelled,
    )
    report(100, "Finite-Via topology decode and identity complete")
    return CompiledFiniteViaBaseTopology(
        # The hydrated certificate can be several GiB.  Recursively rebuilding
        # every dict/list as immutable containers doubles peak memory without
        # strengthening the already checked hashes.  Snapshot/protect the
        # outer mapping; nested evidence is ephemeral and read-only by compiler
        # contract for the remainder of this synchronous compile.
        certificate=MappingProxyType(dict(certificate)),
        certificate_evidence_sha256=evidence,
        source_sha256=source_sha256,
        quotient_vertex_ids=vertex_ids,
        external_port_node_ids=external_port_node_ids,
        topology_links=topology_links,
        finite_links=finite_links,
        rail_ports=ports,
        omitted_rail_ids=omitted,
        # These dictionaries are fresh compiler locals and are never mutated
        # after this return.  Wrapping them directly is caller-immutable and
        # avoids duplicating the 1.73M-entry raw-owner ledger at peak memory.
        owner_edge_by_id=MappingProxyType(owner_edge_by_id),
        vertex_by_landing_key=MappingProxyType(vertex_by_landing),
        first_edge_by_landing_key=MappingProxyType(first_edge_by_landing),
        topology_identity_sha256=topology_identity,
    )


__all__ = [
    "CompiledFiniteViaBaseTopology",
    "FINITE_VIA_QUOTIENT_SCHEMA",
    "FINITE_VIA_SURFACE_COMPILER",
    "FINITE_VIA_SURFACE_SCHEMA",
    "FiniteViaCertificateError",
    "FiniteViaRailPortEvidence",
    "compiled_finite_via_topology_identity_sha256",
    "compile_finite_via_base_topology",
]
