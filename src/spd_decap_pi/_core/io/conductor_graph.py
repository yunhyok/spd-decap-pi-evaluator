"""Bounded, source-faithful SPD conductor topology extraction.

This module intentionally has no electrical solver dependency.  It is a
read-only foundation for a future PEEC/MNA conductor model: a requested NET is
kept as its original branched Node/Trace/Via graph rather than being reduced to
one monotonic Via path.  Consequently, an absent geometry attribute (notably a
Trace width) is reported as unresolved evidence and is never replaced with an
assumed value.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from . import spd as _spd


class SpdConductorGraphError(ValueError):
    """Raised when a bounded conductor-graph request is not well defined."""


@dataclass(frozen=True, slots=True)
class ConductorGraphDiagnostic:
    severity: Literal["info", "warning", "error"]
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ConductorPadDef:
    """One exact layer-local ``.PadDef`` regular geometry record."""

    layer: str
    kind: Literal["CIRCLE", "RECTANGLE", "UNSUPPORTED"]
    width_um: float | None
    height_um: float | None
    source_record: str


@dataclass(frozen=True, slots=True)
class ConductorPadStack:
    """Source padstack dimensions associated with a retained Via or Node."""

    name: str
    drill_diameter_um: float | None
    pad_width_um: float | None
    pad_height_um: float | None
    layers: tuple[str, ...]
    material: str | None
    conductivity_s_m: float | None
    pad_defs: tuple[ConductorPadDef, ...]


@dataclass(frozen=True, slots=True)
class ConductorLayer:
    """Physical layer evidence retained for exact Via interval construction."""

    name: str
    thickness_um: float | None
    material: str | None
    conductivity_s_m: float | None
    kind: Literal["CONDUCTOR", "DIELECTRIC", "UNRESOLVED"]


@dataclass(frozen=True, slots=True)
class ConductorNode:
    source_id: str
    net: str
    x_um: float
    y_um: float
    layer: str
    padstack: str | None

    @property
    def key(self) -> str:
        """Stable graph key; source IDs alone are not assumed NET-unique."""

        return f"{self.net.casefold()}\x1f{self.source_id.casefold()}"


@dataclass(frozen=True, slots=True)
class ConductorVia:
    source_id: str
    net: str
    upper_node_id: str
    lower_node_id: str
    upper_node_net_evidence: str | None
    lower_node_net_evidence: str | None
    upper_layer: str
    lower_layer: str
    upper_conductor_thickness_um: float | None
    lower_conductor_thickness_um: float | None
    interval_layers: tuple[str, ...]
    upper_pad: ConductorPadDef
    lower_pad: ConductorPadDef
    padstack: ConductorPadStack
    source_tail: str
    electrically_resolved: bool
    electrical_blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConductorTrace:
    source_id: str
    net: str
    starting_node_id: str
    ending_node_id: str
    starting_node_net_evidence: str | None
    ending_node_net_evidence: str | None
    layer: str
    width_um: float | None
    source_tail: str
    electrically_resolved: bool
    electrical_blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConductorComponent:
    """One deterministic same-NET connected component; branches are retained."""

    component_id: str
    net: str
    node_keys: tuple[str, ...]
    via_ids: tuple[str, ...]
    trace_ids: tuple[str, ...]


_COMPILER_VERSION = "conductor-graph/v1"


@dataclass(frozen=True, slots=True)
class TopologyCertificate:
    """Immutable evidence identity for one fail-closed topology compilation.

    ``source_sha256`` identifies the exact SPD bytes used for the compile.
    ``topology_sha256`` deliberately identifies the *canonical compiled
    evidence*, rather than the input byte order.  Thus an exporter that merely
    reorders equivalent Node/Trace/Via records does not silently change the
    topology identity, while any retained source fact or unresolved diagnostic
    does.  The two identities must both participate in a future cache key.
    """

    compiler_version: str
    source_sha256: str
    topology_sha256: str
    source_size_bytes: int
    unresolved_codes: tuple[str, ...]
    owner_ledger: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class SpdConductorGraph:
    """Immutable topology and fail-closed electrical graph views.

    Neither component view includes SPD polygon/artwork connectivity.  The
    electrical view contains only source branches with complete scalar evidence
    available today; it cannot be certified until exact same-polygon contact
    proof is supplied by a future artwork adapter.
    """

    source_path: Path
    requested_nets: tuple[str, ...]
    site: str | None
    selected_layers: tuple[str, ...]
    layers: tuple[ConductorLayer, ...]
    nodes: tuple[ConductorNode, ...]
    vias: tuple[ConductorVia, ...]
    traces: tuple[ConductorTrace, ...]
    padstacks: tuple[ConductorPadStack, ...]
    topology_adjacency: Mapping[str, tuple[str, ...]]
    topology_components: tuple[ConductorComponent, ...]
    electrical_adjacency: Mapping[str, tuple[str, ...]]
    electrical_components: tuple[ConductorComponent, ...]
    polygon_connectivity_included: bool
    diagnostics: tuple[ConductorGraphDiagnostic, ...]
    statistics: Mapping[str, int]
    # ``None`` keeps direct construction by legacy research callers readable;
    # production extraction always supplies a certificate and cache/MNA
    # adapters must call ``require_certificate`` rather than invent identity.
    certificate: TopologyCertificate | None = None

    @property
    def has_unresolved_evidence(self) -> bool:
        return any(item.severity in {"warning", "error"} for item in self.diagnostics)

    @property
    def electrical_ready(self) -> bool:
        """Whether this graph is safe to hand to an electrical MNA adapter."""

        return (
            self.polygon_connectivity_included
            and not any(not item.electrically_resolved for item in self.vias)
            and not any(not item.electrically_resolved for item in self.traces)
            and not any(item.severity == "error" for item in self.diagnostics)
        )

    def require_electrical_ready(self) -> None:
        """Fail closed until all branches and polygon contacts are proven."""

        if self.electrical_ready:
            return
        blockers = [item.code for item in self.diagnostics if item.severity == "error"]
        blockers.extend(
            code
            for item in (*self.vias, *self.traces)
            if not item.electrically_resolved
            for code in item.electrical_blockers
        )
        if not self.polygon_connectivity_included:
            blockers.append("POLYGON_CONNECTIVITY_NOT_INCLUDED")
        summary = ", ".join(dict.fromkeys(blockers)) or "UNRESOLVED_ELECTRICAL_EVIDENCE"
        raise SpdConductorGraphError(
            f"SPD conductor graph is not electrical-ready: {summary}"
        )

    def require_certificate(self) -> TopologyCertificate:
        """Return compiler identity or fail closed for a manually-built graph."""

        if self.certificate is None:
            raise SpdConductorGraphError(
                "SPD conductor graph has no topology certificate; compile it from a stable source before caching or MNA assembly"
            )
        return self.certificate


def _node_identity(raw: bytes) -> tuple[str, str] | None:
    cuts = [value for value in (raw.find(b"!!"), raw.find(b"::"), raw.find(b" ")) if value >= 0]
    separator = raw.find(b"::")
    if not cuts or separator < 0:
        return None
    source_id = _spd._decode(raw[: min(cuts)])
    token = raw[separator + 2 :].split(None, 1)[0]
    net = _spd._decode(token)
    return (source_id, net) if source_id and net else None


def _endpoint_net_evidence(raw: bytes, attribute: bytes) -> str | None:
    """Return an endpoint token's explicit ``::NET`` suffix when present."""

    token = _spd._attribute(raw, attribute)
    if token is None or b"::" not in token:
        return None
    net = token.split(b"::", 1)[1]
    return _spd._decode(net) if net else None


def _record_net(raw: bytes, prefix: bytes) -> str | None:
    """Read the ``RecordId::NET`` prefix without accepting record syntax."""

    if not raw.startswith(prefix) or b"::" not in raw:
        return None
    token = raw.split(b"::", 1)[1].split(None, 1)[0]
    return _spd._decode(token) if token else None


def _normalise_nets(net_names: Iterable[str], site: str | None) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    normalised_site = site.strip() if site is not None else None
    if normalised_site == "":
        raise SpdConductorGraphError("site must be non-empty when supplied")
    for value in net_names:
        net = str(value).strip()
        if not net:
            continue
        if normalised_site is not None and net.rpartition("/")[2] != normalised_site:
            raise SpdConductorGraphError(
                f"requested NET {net!r} does not belong to requested site {normalised_site!r}"
            )
        if net.casefold() not in seen:
            result.append(net)
            seen.add(net.casefold())
    if not result:
        raise SpdConductorGraphError("at least one non-empty requested NET is required")
    return tuple(result)


def _file_lines(handle: object, start: int, end: int):
    """Yield bounded byte lines from one source interval (never map the SPD)."""

    file_handle = handle  # keeps the public helper's type surface deliberately small
    file_handle.seek(start)
    while file_handle.tell() < end:
        offset = file_handle.tell()
        raw = file_handle.readline()
        if not raw:
            return
        if offset >= end:
            return
        yield offset, raw.rstrip(b"\r\n")


def _section_offsets(handle: object) -> dict[bytes, int]:
    markers = {
        b"* Layer description lines",
        b"* Node description lines",
        b"* Trace description lines",
        b"* Via description lines",
        b"* PadStack collection description lines",
        b"* Material description lines",
        b"* Circuit description lines",
    }
    result: dict[bytes, int] = {}
    handle.seek(0)
    while True:
        offset = handle.tell()
        raw = handle.readline()
        if not raw:
            return result
        if raw.rstrip(b"\r\n") in markers:
            result[raw.rstrip(b"\r\n")] = offset


def _metal_models(
    handle: object,
    start: int,
    end: int,
    diagnostics: list[ConductorGraphDiagnostic],
) -> dict[str, float]:
    """Resolve source ``.MetalModel`` conductivity at the row nearest 20 C."""

    if start < 0:
        return {}
    result: dict[str, float] = {}
    rejected: set[str] = set()
    name: str | None = None
    rows: list[tuple[float, ...]] = []

    def flush() -> None:
        nonlocal name, rows
        if name is None:
            return
        key = name.casefold()
        valid = [row for row in rows if len(row) >= 2 and all(isfinite(value) for value in row[:2]) and row[1] > 0.0]
        if not valid:
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "METAL_MODEL_CONDUCTIVITY_INVALID",
                f"MetalModel {name!r} has no finite positive conductivity row.",
            ))
            rejected.add(key)
            result.pop(key, None)
        elif key in result or key in rejected:
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "DUPLICATE_METAL_MODEL",
                f"Duplicate MetalModel {name!r}; conductivity resolution is fail-closed.",
            ))
            rejected.add(key)
            result.pop(key, None)
        else:
            result[key] = min(valid, key=lambda row: abs(row[0] - 20.0))[1]
        name, rows = None, []

    for _, raw in _file_lines(handle, start, end):
        stripped = raw.strip()
        folded = stripped.lower()
        if folded.startswith(b".metalmodel "):
            flush()
            tokens = stripped.split(None, 1)
            name = _spd._decode(tokens[1]) if len(tokens) == 2 else ""
        elif folded.startswith(b".endmetalmodel"):
            flush()
        elif name is not None and stripped and stripped[:1] in b"+-.0123456789":
            try:
                values = tuple(float(value) for value in _spd._FLOAT_RE.findall(stripped))
            except ValueError:
                values = ()
            if values:
                rows.append(values)
    flush()
    return result


def _stackup_layers(
    handle: object,
    start: int,
    node_start: int,
    metal_models: Mapping[str, float],
    diagnostics: list[ConductorGraphDiagnostic],
) -> tuple[ConductorLayer, ...]:
    if start < 0:
        return ()
    result: list[ConductorLayer] = []
    index_by_key: dict[str, int] = {}
    for _, raw in _file_lines(handle, start, node_start):
        stripped = raw.strip()
        if not stripped or stripped.startswith((b"*", b"+", b".")) or b"Thickness" not in stripped:
            continue
        name = _spd._decode(stripped.split(None, 1)[0])
        name_key = name.casefold()
        if name_key in index_by_key:
            index = index_by_key[name_key]
            result[index] = replace(
                result[index], conductivity_s_m=None, kind="UNRESOLVED"
            )
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "DUPLICATE_LAYER",
                f"Duplicate physical layer {name!r}; its conductor evidence is fail-closed.",
            ))
            continue
        thickness_match = _spd._attribute(stripped, b"Thickness")
        try:
            thickness_um = _spd._length_um(thickness_match) if thickness_match else None
        except ValueError:
            thickness_um = None
        material_raw = _spd._attribute(stripped, b"Material")
        conductivity_raw = _spd._attribute(stripped, b"Conductivity")
        explicit_conductivity_invalid = False
        if conductivity_raw is None:
            conductivity = None
        else:
            try:
                conductivity = float(conductivity_raw)
            except ValueError:
                conductivity = None
                explicit_conductivity_invalid = True
            else:
                if not isfinite(conductivity) or conductivity <= 0.0:
                    conductivity = None
                    explicit_conductivity_invalid = True
        material = _spd._decode(material_raw) if material_raw else None
        layer_key = name_key
        if layer_key.startswith(("signal$", "power$", "conductor$")):
            kind: Literal["CONDUCTOR", "DIELECTRIC", "UNRESOLVED"] = "CONDUCTOR"
        elif layer_key.startswith(("medium$", "dielectric$")):
            kind = "DIELECTRIC"
        else:
            kind = "UNRESOLVED"
        if explicit_conductivity_invalid:
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "EXPLICIT_LAYER_CONDUCTIVITY_INVALID",
                f"Layer {name!r} has explicit Conductivity "
                f"{_spd._decode(conductivity_raw)!r}; material fallback is disabled.",
            ))
        elif conductivity is None and material is not None:
            conductivity = metal_models.get(material.casefold())
        if kind == "CONDUCTOR" and conductivity is None:
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "CONDUCTOR_CONDUCTIVITY_UNRESOLVED",
                f"Conductor layer {name!r} references material {material!r} without "
                "a finite positive inline or .MetalModel conductivity.",
            ))
        result.append(ConductorLayer(
            name, thickness_um,
            material,
            conductivity,
            kind,
        ))
        index_by_key[name_key] = len(result) - 1
    return tuple(result)


def _selected_padstacks(
    handle: object,
    start: int,
    end: int,
    requested_names: set[str],
    metal_models: Mapping[str, float],
    diagnostics: list[ConductorGraphDiagnostic],
) -> dict[str, ConductorPadStack]:
    """Parse only the pad dimensions used by retained records.

    This mirrors the source parser's radius-to-diameter convention but avoids
    materialising (or memory-mapping) unrelated pad definitions.
    """

    result: dict[str, ConductorPadStack] = {}
    rejected_names: set[str] = set()
    name: str | None = None
    drill: float | None = None
    width: float | None = None
    height: float | None = None
    layers: list[str] = []
    pad_defs: list[ConductorPadDef] = []
    active_layer: str | None = None
    material: str | None = None

    def flush() -> None:
        nonlocal name, drill, width, height, layers, pad_defs, active_layer, material
        if name is not None and name.casefold() in requested_names:
            key = name.casefold()
            if key in result or key in rejected_names:
                diagnostics.append(ConductorGraphDiagnostic(
                    "error",
                    "DUPLICATE_PADSTACK",
                    f"Duplicate PadStack {name!r}; its dimensions are fail-closed.",
                ))
                rejected_names.add(key)
                result.pop(key, None)
            else:
                result[key] = ConductorPadStack(
                    name,
                    drill,
                    width,
                    height,
                    tuple(dict.fromkeys(layers)),
                    material,
                    metal_models.get(material.casefold()) if material else None,
                    tuple(pad_defs),
                )
        name, drill, width, height, layers, pad_defs, active_layer, material = (
            None, None, None, None, [], [], None, None
        )

    for _, raw in _file_lines(handle, start, end):
        stripped = raw.strip()
        folded = stripped.lower()
        if folded.startswith(b".padstackdef "):
            flush()
            tokens = stripped.split()
            name = _spd._decode(tokens[1]) if len(tokens) > 1 else ""
            values = _spd._lengths(b" ".join(tokens[2:]))
            drill = 2.0 * values[0] if values else None
            material_raw = _spd._attribute(stripped, b"Material")
            material = _spd._decode(material_raw) if material_raw else None
        elif folded.startswith(b".endpadstackdef"):
            flush()
        elif name is not None and folded.startswith(b".paddef "):
            tokens = stripped.split(None, 1)
            if len(tokens) == 2:
                active_layer = _spd._decode(tokens[1])
                layers.append(active_layer)
        elif name is not None and folded.startswith(b".endpaddef"):
            active_layer = None
        elif name is not None and folded.startswith(b"regular "):
            values = _spd._lengths(stripped)
            candidate: tuple[float, float] | None = None
            shape_kind: Literal["CIRCLE", "RECTANGLE", "UNSUPPORTED"]
            if folded.startswith(b"regular circle") and values:
                candidate = (2.0 * values[0], 2.0 * values[0])
                shape_kind = "CIRCLE"
            elif folded.startswith(b"regular square") and values:
                candidate = (values[0], values[0])
                shape_kind = "RECTANGLE"
            elif folded.startswith(b"regular box") and len(values) >= 2:
                candidate = (values[0], values[1])
                shape_kind = "RECTANGLE"
            else:
                shape_kind = "UNSUPPORTED"
            if active_layer is not None:
                pad_defs.append(ConductorPadDef(
                    active_layer,
                    shape_kind,
                    candidate[0] if candidate is not None else None,
                    candidate[1] if candidate is not None else None,
                    _spd._decode(stripped),
                ))
            if candidate is not None:
                width = candidate[0] if width is None else max(width, candidate[0])
                height = candidate[1] if height is None else max(height, candidate[1])
    flush()
    return result


def _select_layers(
    order: tuple[str, ...],
    layer_names: Iterable[str] | None,
    layer_range: tuple[str, str] | None,
) -> tuple[str, ...]:
    selected: list[str] = []
    lookup = {value.casefold(): (index, value) for index, value in enumerate(order)}
    if layer_names is not None:
        for value in layer_names:
            key = str(value).strip().casefold()
            if not key:
                continue
            if key not in lookup:
                raise SpdConductorGraphError(f"requested layer {value!r} is not in the SPD stack-up")
            selected.append(lookup[key][1])
    if layer_range is not None:
        if len(layer_range) != 2:
            raise SpdConductorGraphError("layer_range must contain exactly (first_layer, last_layer)")
        first, last = (str(value).strip().casefold() for value in layer_range)
        if first not in lookup or last not in lookup:
            raise SpdConductorGraphError("layer_range endpoint is not in the SPD stack-up")
        begin, end = lookup[first][0], lookup[last][0]
        if begin > end:
            raise SpdConductorGraphError("layer_range must follow SPD stack-up order")
        selected.extend(order[begin : end + 1])
    if not selected:
        return ()
    return tuple(dict.fromkeys(selected))


def _frozen_mapping(values: Mapping[str, Iterable[str]] | Mapping[str, int]) -> Mapping:
    if all(isinstance(value, int) for value in values.values()):
        return MappingProxyType(dict(values))
    return MappingProxyType({key: tuple(sorted(value)) for key, value in values.items()})


def _sha256_file(path: Path) -> str:
    """Hash source bytes without mapping the multi-gigabyte SPD into memory."""

    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _topology_certificate(
    *,
    source_sha256: str,
    source_size_bytes: int,
    requested_nets: tuple[str, ...],
    site: str | None,
    selected_layers: tuple[str, ...],
    layers: tuple[ConductorLayer, ...],
    nodes: tuple[ConductorNode, ...],
    vias: tuple[ConductorVia, ...],
    traces: tuple[ConductorTrace, ...],
    components: tuple[ConductorComponent, ...],
    electrical_components: tuple[ConductorComponent, ...],
    diagnostics: tuple[ConductorGraphDiagnostic, ...],
) -> TopologyCertificate:
    """Create canonical evidence and an explicit source-owner ledger.

    This is intentionally a compiler certificate, not an assertion that the
    graph has proven polygon contact.  The unresolved codes make that boundary
    visible to cache users and MNA adapters.
    """

    def record(item: object) -> dict[str, object]:
        # The involved records are frozen dataclasses with scalar/tuple fields.
        # Encoding their public fields avoids a repr-dependent hash contract.
        return {field.name: getattr(item, field.name) for field in fields(item)}

    def canonical(value: object) -> object:
        if is_dataclass(value):
            return {field.name: canonical(getattr(value, field.name)) for field in fields(value)}
        if isinstance(value, tuple):
            return [canonical(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): canonical(value[key]) for key in sorted(value, key=str)}
        if isinstance(value, Path):
            return str(value)
        return value

    # An ownership identifier must refer to exactly one source record in this
    # compiler output.  It is intentionally scoped by net + kind + source ID;
    # a later physical artwork compiler may replace it with a face owner ID.
    owner_ledger: dict[str, str] = {}
    for kind, values in (
        ("node", nodes),
        ("via", vias),
        ("trace", traces),
    ):
        for item in values:
            owner_id = f"{kind}:{item.net.casefold()}:{item.source_id.casefold()}"
            if owner_id in owner_ledger:  # defensive; extraction rejects these
                raise SpdConductorGraphError(
                    f"topology owner collision for {owner_id!r}; source evidence is ambiguous"
                )
            owner_ledger[owner_id] = f"{kind}:{item.source_id}::{item.net}"
    payload = {
        "compiler_version": _COMPILER_VERSION,
        "requested_nets": sorted(requested_nets, key=str.casefold),
        "site": site,
        "selected_layers": list(selected_layers),
        "layers": [canonical(record(item)) for item in layers],
        "nodes": [canonical(record(item)) for item in nodes],
        "vias": [canonical(record(item)) for item in vias],
        "traces": [canonical(record(item)) for item in traces],
        "topology_components": [canonical(record(item)) for item in components],
        "electrical_components": [canonical(record(item)) for item in electrical_components],
        "diagnostics": [
            canonical(record(item))
            for item in sorted(diagnostics, key=lambda item: (item.severity, item.code, item.message))
        ],
        "owner_ledger": owner_ledger,
        "polygon_connectivity_included": False,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return TopologyCertificate(
        compiler_version=_COMPILER_VERSION,
        source_sha256=source_sha256,
        topology_sha256=sha256(encoded.encode("ascii")).hexdigest(),
        source_size_bytes=source_size_bytes,
        unresolved_codes=tuple(sorted({item.code for item in diagnostics})),
        owner_ledger=MappingProxyType(dict(owner_ledger)),
    )


def extract_spd_conductor_graph(
    path: str | Path,
    *,
    net_names: Iterable[str],
    site: str | None = None,
    layer_names: Iterable[str] | None = None,
    layer_range: tuple[str, str] | None = None,
) -> SpdConductorGraph:
    """Extract the exact requested-net Node/Trace/Via topology from an SPD.

    The source is scanned as bounded binary line streams.  Only Nodes whose NET matches
    ``net_names`` are materialized, and only edges of those NETs are retained.
    ``layer_names`` and ``layer_range`` are inclusive filters.  An edge leaving
    that filter is excluded and reported, rather than silently creating a
    partial electrical branch.
    """

    source_path = Path(path)
    if not source_path.is_file():
        raise SpdConductorGraphError(f"SPD source is not a file: {source_path}")
    source_stat_before = source_path.stat()
    requested_nets = _normalise_nets(net_names, site)
    requested_keys = {value.casefold() for value in requested_nets}
    diagnostics: list[ConductorGraphDiagnostic] = []
    node_records: dict[tuple[str, str], ConductorNode] = {}
    all_selected_node_layers: dict[tuple[str, str], str | None] = {}
    seen_node_keys: set[tuple[str, str]] = set()
    rejected_node_keys: set[tuple[str, str]] = set()
    node_failure_codes: dict[tuple[str, str], str] = {}
    vias_raw: list[
        tuple[str, str, str, str, str | None, str | None, str, str]
    ] = []
    traces_raw: list[_spd.SpdTraceRecord] = []
    selected_trace_identity_counts: Counter[tuple[str, str]] = Counter()
    selected_trace_identity_display: dict[tuple[str, str], tuple[str, str]] = {}
    counters: Counter[str] = Counter()

    with source_path.open("rb") as handle:
        offsets = _section_offsets(handle)
        node_start = offsets.get(b"* Node description lines", -1)
        trace_start = offsets.get(b"* Trace description lines", -1)
        via_start = offsets.get(b"* Via description lines", -1)
        pad_start = offsets.get(b"* PadStack collection description lines", -1)
        if node_start < 0 or via_start < 0:
            raise SpdConductorGraphError("SPD has no readable Node/Via description sections")
        node_end = trace_start if trace_start > node_start else via_start
        trace_end = via_start if via_start > trace_start else pad_start
        source_size = source_path.stat().st_size
        via_end = pad_start if pad_start > via_start else source_size
        material_start = offsets.get(b"* Material description lines", -1)
        material_end = offsets.get(b"* Circuit description lines", source_size)
        metal_models = _metal_models(
            handle, material_start, material_end, diagnostics
        )
        stackup_layers = _stackup_layers(
            handle,
            offsets.get(b"* Layer description lines", -1),
            node_start,
            metal_models,
            diagnostics,
        )
        order = tuple(item.name for item in stackup_layers)
        layer_by_key = {item.name.casefold(): item for item in stackup_layers}
        selected_layers = _select_layers(order, layer_names, layer_range)
        selected_layer_keys = {value.casefold() for value in selected_layers}

        for _, raw in _file_lines(handle, node_start, node_end):
            if not raw.startswith(b"Node"):
                continue
            identity = _node_identity(raw)
            if identity is None:
                continue
            source_id, net = identity
            net_key = net.casefold()
            if net_key not in requested_keys:
                continue
            counters["matching_node_records"] += 1
            layer_raw = _spd._attribute(raw, b"Layer")
            layer = _spd._decode(layer_raw) if layer_raw else None
            key = (net_key, source_id.casefold())
            if key in seen_node_keys:
                if key not in rejected_node_keys:
                    diagnostics.append(ConductorGraphDiagnostic("error", "DUPLICATE_NODE_ID", f"Duplicate Node {source_id!r} on requested NET {net!r}; graph extraction is fail-closed for its incident edges."))
                rejected_node_keys.add(key)
                node_failure_codes[key] = "DUPLICATE_NODE_ID"
                node_records.pop(key, None)
                continue
            seen_node_keys.add(key)
            all_selected_node_layers[key] = layer
            if layer is None:
                node_failure_codes[key] = "NODE_LAYER_MISSING"
                diagnostics.append(ConductorGraphDiagnostic("error", "NODE_LAYER_MISSING", f"Node {source_id!r} on {net!r} has no Layer evidence."))
                continue
            if selected_layer_keys and layer.casefold() not in selected_layer_keys:
                counters["nodes_outside_layer_filter"] += 1
                node_failure_codes[key] = "NODE_OUTSIDE_LAYER_FILTER"
                continue
            attributes = _spd._NODE_ATTR_RE.search(raw)
            if attributes is None:
                node_failure_codes[key] = "NODE_COORDINATES_MISSING"
                diagnostics.append(ConductorGraphDiagnostic("error", "NODE_COORDINATES_MISSING", f"Node {source_id!r} on {net!r} has no usable X/Y evidence."))
                continue
            try:
                x_um, y_um = _spd._length_um(attributes.group(1)), _spd._length_um(attributes.group(2))
            except ValueError:
                node_failure_codes[key] = "NODE_COORDINATES_INVALID"
                diagnostics.append(ConductorGraphDiagnostic("error", "NODE_COORDINATES_INVALID", f"Node {source_id!r} on {net!r} has invalid X/Y evidence."))
                continue
            padstack_raw = _spd._attribute(raw, b"PadStack")
            padstack = _spd._decode(padstack_raw) if padstack_raw is not None else ( _spd._decode(attributes.group(3)) if attributes.group(3) is not None else None )
            item = ConductorNode(source_id, net, x_um, y_um, layer, padstack)
            node_records[key] = item

        if trace_start >= 0 and trace_end > trace_start:
            try:
                trace_records = _spd._iter_spd_trace_records(
                    handle,
                    trace_start,
                    trace_end,
                )
                for record in trace_records:
                    counters["logical_trace_records"] += 1
                    net = record.net
                    if net is None or net.casefold() not in requested_keys:
                        continue
                    counters["matching_trace_records"] += 1
                    if record.source_id is not None:
                        trace_identity = (net.casefold(), record.source_id.casefold())
                        selected_trace_identity_counts[trace_identity] += 1
                        selected_trace_identity_display.setdefault(
                            trace_identity,
                            (net, record.source_id),
                        )
                    if (
                        record.source_id is None
                        or record.starting_node_id is None
                        or record.ending_node_id is None
                        or "MALFORMED_TRACE_RECORD" in record.issue_codes
                    ):
                        counters["malformed_selected_trace_records"] += 1
                        diagnostics.append(
                            ConductorGraphDiagnostic(
                                "error",
                                "MALFORMED_SELECTED_TRACE",
                                "Selected-NET logical Trace record could not be "
                                f"parsed at byte offset {record.source_offset}; "
                                f"record SHA-256 is {record.source_sha256}.",
                            )
                        )
                        continue
                    traces_raw.append(record)
            except _spd.SpdTraceRecordError as exc:
                raise SpdConductorGraphError(
                    f"SPD Trace section framing failed ({exc.code}) at byte "
                    f"offset {exc.offset}: {exc}"
                ) from exc

        for _, raw in _file_lines(handle, via_start, via_end):
            match = _spd._VIA_RE.match(raw)
            if match is None:
                malformed_net = _record_net(raw, b"Via")
                if malformed_net is not None and malformed_net.casefold() in requested_keys:
                    counters["malformed_selected_via_records"] += 1
                    diagnostics.append(ConductorGraphDiagnostic(
                        "error",
                        "MALFORMED_SELECTED_VIA",
                        f"Selected-NET Via record could not be parsed: "
                        f"{_spd._decode(raw[:240])!r}.",
                    ))
                continue
            net = _spd._decode(match.group(2))
            if net.casefold() not in requested_keys:
                continue
            counters["matching_via_records"] += 1
            vias_raw.append((
                _spd._decode(match.group(1)),
                net,
                _spd._decode(match.group(3)),
                _spd._decode(match.group(4)),
                _endpoint_net_evidence(raw, b"UpperNode"),
                _endpoint_net_evidence(raw, b"LowerNode"),
                _spd._decode(match.group(5)),
                _spd._decode(match.group(6)),
            ))

        referenced_padstacks = {item.padstack.casefold() for item in node_records.values() if item.padstack}
        referenced_padstacks.update(row[6].casefold() for row in vias_raw)
        padstack_evidence = (
            _selected_padstacks(
                handle,
                pad_start,
                material_start if material_start >= 0 else source_size,
                referenced_padstacks,
                metal_models,
                diagnostics,
            )
            if referenced_padstacks and pad_start >= 0
            else {}
        )
        for name in sorted(referenced_padstacks - set(padstack_evidence)):
            diagnostics.append(ConductorGraphDiagnostic("warning", "PADSTACK_EVIDENCE_MISSING", f"Requested graph references PadStack {name!r}, but its dimensions were not found."))

    duplicate_trace_identities = {
        identity
        for identity, count in selected_trace_identity_counts.items()
        if count > 1
    }
    for identity in sorted(duplicate_trace_identities):
        net, source_id = selected_trace_identity_display[identity]
        count = selected_trace_identity_counts[identity]
        counters["rejected_duplicate_trace_records"] += count
        diagnostics.append(
            ConductorGraphDiagnostic(
                "error",
                "DUPLICATE_TRACE_ID",
                f"Duplicate Trace {source_id!r} on requested NET {net!r}; "
                f"all {count} logical records were rejected.",
            )
        )

    retained_vias: list[ConductorVia] = []
    retained_traces: list[ConductorTrace] = []
    rejected_edge_ids: set[tuple[str, str]] = set()

    def endpoint(node_id: str, net: str, edge_kind: str, edge_id: str) -> ConductorNode | None:
        key = (net.casefold(), node_id.casefold())
        node = node_records.get(key)
        if node is not None:
            return node
        source_layer = all_selected_node_layers.get(key)
        failure_code = node_failure_codes.get(key)
        if key in rejected_node_keys:
            code = "EDGE_ENDPOINT_AMBIGUOUS"
            message = f"{edge_kind} {edge_id!r} on {net!r} references duplicate Node {node_id!r}."
        elif failure_code == "NODE_OUTSIDE_LAYER_FILTER":
            code = "EDGE_LEAVES_LAYER_FILTER"
            message = f"{edge_kind} {edge_id!r} on {net!r} reaches Node {node_id!r} on excluded layer {source_layer!r}."
        elif failure_code is not None:
            code = "EDGE_ENDPOINT_NODE_INVALID"
            message = (
                f"{edge_kind} {edge_id!r} on {net!r} references invalid Node "
                f"{node_id!r} ({failure_code}); layer-filter exit was not inferred."
            )
        else:
            code = "EDGE_ENDPOINT_UNRESOLVED"
            message = f"{edge_kind} {edge_id!r} on {net!r} references unresolved Node {node_id!r}."
        identity = (edge_kind, edge_id.casefold())
        if identity not in rejected_edge_ids:
            rejected_edge_ids.add(identity)
            diagnostics.append(ConductorGraphDiagnostic("error", code, message))
        return None

    def conductor_layer(
        node: ConductorNode, edge_kind: str, edge_id: str
    ) -> ConductorLayer | None:
        layer = layer_by_key.get(node.layer.casefold())
        if layer is None:
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "EDGE_ENDPOINT_LAYER_UNKNOWN",
                f"{edge_kind} {edge_id!r} endpoint Node {node.source_id!r} references "
                f"layer {node.layer!r}, which is absent from the physical stack-up.",
            ))
            return None
        if layer.kind != "CONDUCTOR":
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "EDGE_ENDPOINT_NOT_CONDUCTOR",
                f"{edge_kind} {edge_id!r} endpoint Node {node.source_id!r} is on "
                f"{node.layer!r} with source layer kind {layer.kind}; conductor contact "
                "was not guessed.",
            ))
            return None
        if layer.thickness_um is None or not isfinite(layer.thickness_um) or layer.thickness_um <= 0.0:
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "EDGE_ENDPOINT_CONDUCTOR_THICKNESS_MISSING",
                f"{edge_kind} {edge_id!r} endpoint conductor {node.layer!r} has no "
                "finite positive thickness evidence.",
            ))
            return None
        return layer

    def reject_via(code: str, message: str) -> None:
        counters["rejected_via_records"] += 1
        counters[f"rejected_via_{code.casefold()}"] += 1
        diagnostics.append(ConductorGraphDiagnostic("error", code, message))

    seen_edges: set[tuple[str, str, str]] = set()
    for (
        source_id,
        net,
        upper_id,
        lower_id,
        upper_net_evidence,
        lower_net_evidence,
        padstack_name,
        tail,
    ) in vias_raw:
        identity = ("VIA", net.casefold(), source_id.casefold())
        if identity in seen_edges:
            diagnostics.append(ConductorGraphDiagnostic("error", "DUPLICATE_VIA_ID", f"Duplicate Via {source_id!r} on requested NET {net!r}; both records were rejected."))
            retained_vias = [item for item in retained_vias if not (item.net.casefold() == net.casefold() and item.source_id.casefold() == source_id.casefold())]
            continue
        seen_edges.add(identity)
        conflicting_endpoint_nets = tuple(
            value for value in (upper_net_evidence, lower_net_evidence)
            if value is not None and value.casefold() != net.casefold()
        )
        if conflicting_endpoint_nets:
            reject_via(
                "VIA_ENDPOINT_NET_MISMATCH",
                f"Via {source_id!r} record NET {net!r} conflicts with explicit "
                f"endpoint NET evidence {conflicting_endpoint_nets!r}.",
            )
            continue
        upper = endpoint(upper_id, net, "Via", source_id)
        lower = endpoint(lower_id, net, "Via", source_id)
        if upper is None or lower is None:
            counters["rejected_via_records"] += 1
            continue
        upper_layer = conductor_layer(upper, "Via", source_id)
        lower_layer = conductor_layer(lower, "Via", source_id)
        if upper_layer is None or lower_layer is None:
            counters["rejected_via_records"] += 1
            continue
        upper_index = order.index(upper_layer.name)
        lower_index = order.index(lower_layer.name)
        if upper_index >= lower_index:
            reject_via(
                "VIA_INTERVAL_ORDER_INVALID",
                f"Via {source_id!r} on {net!r} declares UpperNode on {upper.layer!r} "
                f"and LowerNode on {lower.layer!r}, which does not follow physical "
                "stack-up order.",
            )
            continue
        interval = stackup_layers[upper_index : lower_index + 1]
        if any(
            item.thickness_um is None
            or not isfinite(item.thickness_um)
            or item.thickness_um <= 0.0
            or item.kind == "UNRESOLVED"
            for item in interval
        ):
            reject_via(
                "VIA_INTERVAL_EVIDENCE_INCOMPLETE",
                f"Via {source_id!r} on {net!r} crosses an interval with unresolved "
                "layer kind or thickness evidence.",
            )
            continue
        padstack = padstack_evidence.get(padstack_name.casefold())
        if padstack is None:
            reject_via(
                "VIA_PADSTACK_EVIDENCE_MISSING",
                f"Via {source_id!r} on {net!r} references PadStack {padstack_name!r} "
                "without a retained source definition.",
            )
            continue
        if (
            padstack.drill_diameter_um is None
            or not isfinite(padstack.drill_diameter_um)
            or padstack.drill_diameter_um <= 0.0
        ):
            reject_via(
                "VIA_DRILL_EVIDENCE_MISSING",
                f"Via {source_id!r} PadStack {padstack.name!r} has no finite positive "
                "drill diameter evidence.",
            )
            continue
        unknown_pad_layers = sorted({
            item.layer for item in padstack.pad_defs
            if item.layer.casefold() not in layer_by_key
        }, key=str.casefold)
        if unknown_pad_layers:
            reject_via(
                "VIA_PADSTACK_LAYER_UNKNOWN",
                f"Via {source_id!r} PadStack {padstack.name!r} has PadDef layer(s) "
                f"outside the physical stack-up: {', '.join(unknown_pad_layers)}.",
            )
            continue

        def endpoint_pad(layer: str) -> ConductorPadDef | None:
            matches = tuple(
                item for item in padstack.pad_defs
                if item.layer.casefold() == layer.casefold()
            )
            return matches[0] if len(matches) == 1 else None

        upper_pad = endpoint_pad(upper.layer)
        lower_pad = endpoint_pad(lower.layer)
        if upper_pad is None or lower_pad is None:
            reject_via(
                "VIA_ENDPOINT_PADDEF_MISSING",
                f"Via {source_id!r} PadStack {padstack.name!r} does not provide exactly "
                f"one PadDef for both endpoint layers {upper.layer!r} and {lower.layer!r}.",
            )
            continue
        if any(
            item.kind == "UNSUPPORTED"
            or item.width_um is None
            or item.height_um is None
            or item.width_um <= 0.0
            or item.height_um <= 0.0
            for item in (upper_pad, lower_pad)
        ):
            reject_via(
                "VIA_ENDPOINT_PAD_GEOMETRY_UNSUPPORTED",
                f"Via {source_id!r} PadStack {padstack.name!r} has unsupported or "
                "dimensionless endpoint PadDef geometry.",
            )
            continue
        electrical_blockers: list[str] = []
        if upper_layer.conductivity_s_m is None or lower_layer.conductivity_s_m is None:
            electrical_blockers.append("VIA_ENDPOINT_CONDUCTIVITY_UNRESOLVED")
        if padstack.conductivity_s_m is None:
            electrical_blockers.append("VIA_CONDUCTIVITY_UNRESOLVED")
        if electrical_blockers:
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "VIA_CONDUCTIVITY_UNRESOLVED",
                f"Via {source_id!r} lacks source-resolved finite metal conductivity "
                "for its PadStack or endpoint conductor; R input is fail-closed.",
            ))
        retained_vias.append(ConductorVia(
            source_id, net, upper_id, lower_id,
            upper_net_evidence, lower_net_evidence,
            upper.layer, lower.layer,
            upper_layer.thickness_um,
            lower_layer.thickness_um,
            tuple(item.name for item in interval),
            upper_pad,
            lower_pad,
            padstack,
            tail,
            not electrical_blockers,
            tuple(electrical_blockers),
        ))

    for trace_record in traces_raw:
        source_id = trace_record.source_id
        net = trace_record.net
        first_id = trace_record.starting_node_id
        second_id = trace_record.ending_node_id
        if (
            source_id is None
            or net is None
            or first_id is None
            or second_id is None
        ):
            raise RuntimeError("validated logical Trace record lost its identity")
        first_net_evidence = trace_record.starting_node_net_evidence
        second_net_evidence = trace_record.ending_node_net_evidence
        identity = ("TRACE", net.casefold(), source_id.casefold())
        if (net.casefold(), source_id.casefold()) in duplicate_trace_identities:
            continue
        seen_edges.add(identity)
        conflicting_endpoint_nets = tuple(
            value for value in (first_net_evidence, second_net_evidence)
            if value is not None and value.casefold() != net.casefold()
        )
        if conflicting_endpoint_nets:
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "TRACE_ENDPOINT_NET_MISMATCH",
                f"Trace {source_id!r} record NET {net!r} conflicts with explicit "
                f"endpoint NET evidence {conflicting_endpoint_nets!r}.",
            ))
            counters["rejected_trace_records"] += 1
            continue
        first = endpoint(first_id, net, "Trace", source_id)
        second = endpoint(second_id, net, "Trace", source_id)
        if first is None or second is None:
            counters["rejected_trace_records"] += 1
            continue
        first_layer = conductor_layer(first, "Trace", source_id)
        second_layer = conductor_layer(second, "Trace", source_id)
        if first_layer is None or second_layer is None:
            counters["rejected_trace_records"] += 1
            continue
        if first.layer.casefold() != second.layer.casefold():
            diagnostics.append(ConductorGraphDiagnostic("error", "TRACE_CROSSES_LAYERS", f"Trace {source_id!r} on {net!r} joins {first.layer!r} to {second.layer!r}; it is not a same-layer copper branch."))
            counters["rejected_trace_records"] += 1
            continue
        width_um = trace_record.width_um
        electrical_blockers: list[str] = []
        for issue_code in trace_record.issue_codes:
            diagnostic_code = (
                "TRACE_WIDTH_INVALID"
                if issue_code == "TRACE_WIDTH_NONFINITE"
                else issue_code
            )
            severity: Literal["warning", "error"] = (
                "warning" if issue_code == "TRACE_WIDTH_MISSING" else "error"
            )
            diagnostics.append(
                ConductorGraphDiagnostic(
                    severity,
                    diagnostic_code,
                    f"Trace {source_id!r} on {net!r} has unresolved logical-record "
                    f"geometry evidence ({issue_code}); record SHA-256 is "
                    f"{trace_record.source_sha256}. No trace R/L may be inferred.",
                )
            )
            electrical_blockers.append(diagnostic_code)
        if first_layer.conductivity_s_m is None:
            diagnostics.append(ConductorGraphDiagnostic(
                "error",
                "TRACE_CONDUCTIVITY_UNRESOLVED",
                f"Trace {source_id!r} conductor layer {first.layer!r} lacks finite "
                "source-resolved metal conductivity; R input is fail-closed.",
            ))
            electrical_blockers.append("TRACE_CONDUCTIVITY_UNRESOLVED")
        retained_traces.append(ConductorTrace(
            source_id,
            net,
            first_id,
            second_id,
            first_net_evidence,
            second_net_evidence,
            first.layer,
            width_um,
            trace_record.source_tail,
            not electrical_blockers,
            tuple(electrical_blockers),
        ))

    node_net_by_key = {item.key: item.net for item in node_records.values()}

    def build_connectivity(
        view: Literal["topology", "electrical"],
        vias: Iterable[ConductorVia],
        traces: Iterable[ConductorTrace],
    ) -> tuple[Mapping[str, tuple[str, ...]], tuple[ConductorComponent, ...]]:
        adjacency_sets: dict[str, set[str]] = {
            node.key: set() for node in node_records.values()
        }
        via_by_node: dict[str, list[str]] = defaultdict(list)
        trace_by_node: dict[str, list[str]] = defaultdict(list)
        for item in vias:
            first = node_records[(item.net.casefold(), item.upper_node_id.casefold())].key
            second = node_records[(item.net.casefold(), item.lower_node_id.casefold())].key
            adjacency_sets[first].add(second)
            adjacency_sets[second].add(first)
            via_by_node[first].append(item.source_id)
            via_by_node[second].append(item.source_id)
        for item in traces:
            first = node_records[(item.net.casefold(), item.starting_node_id.casefold())].key
            second = node_records[(item.net.casefold(), item.ending_node_id.casefold())].key
            adjacency_sets[first].add(second)
            adjacency_sets[second].add(first)
            trace_by_node[first].append(item.source_id)
            trace_by_node[second].append(item.source_id)

        components: list[ConductorComponent] = []
        seen_nodes: set[str] = set()
        sequence_by_net: Counter[str] = Counter()
        for start in sorted(adjacency_sets):
            if start in seen_nodes:
                continue
            members: set[str] = set()
            frontier = deque((start,))
            while frontier:
                current = frontier.popleft()
                if current in members:
                    continue
                members.add(current)
                frontier.extend(sorted(adjacency_sets[current] - members))
            seen_nodes.update(members)
            net = node_net_by_key[start]
            sequence_by_net[net.casefold()] += 1
            via_ids = {value for node in members for value in via_by_node[node]}
            trace_ids = {value for node in members for value in trace_by_node[node]}
            components.append(ConductorComponent(
                f"{view}:{net}:{sequence_by_net[net.casefold()]:04d}",
                net,
                tuple(sorted(members)),
                tuple(sorted(via_ids, key=str.casefold)),
                tuple(sorted(trace_ids, key=str.casefold)),
            ))
        return _frozen_mapping(adjacency_sets), tuple(components)

    ordered_nodes = tuple(sorted(node_records.values(), key=lambda item: (item.net.casefold(), item.source_id.casefold())))
    ordered_vias = tuple(sorted(retained_vias, key=lambda item: (item.net.casefold(), item.source_id.casefold())))
    ordered_traces = tuple(sorted(retained_traces, key=lambda item: (item.net.casefold(), item.source_id.casefold())))
    topology_adjacency, topology_components = build_connectivity(
        "topology", ordered_vias, ordered_traces
    )
    electrical_adjacency, electrical_components = build_connectivity(
        "electrical",
        (item for item in ordered_vias if item.electrically_resolved),
        (item for item in ordered_traces if item.electrically_resolved),
    )
    diagnostics.append(ConductorGraphDiagnostic(
        "warning",
        "POLYGON_CONNECTIVITY_NOT_INCLUDED",
        "Node/Trace/Via components do not include SPD polygon/artwork connectivity; "
        "electrical certification is blocked until exact same-polygon contacts are proven.",
    ))
    statistics = Counter(counters)
    statistics.update({
        "retained_nodes": len(ordered_nodes),
        "retained_vias": len(ordered_vias),
        "retained_traces": len(ordered_traces),
        "electrical_vias": sum(item.electrically_resolved for item in ordered_vias),
        "electrical_traces": sum(item.electrically_resolved for item in ordered_traces),
        "topology_components": len(topology_components),
        "electrical_components": len(electrical_components),
        "diagnostics": len(diagnostics),
        "source_size_bytes": source_stat_before.st_size,
    })
    source_stat_after = source_path.stat()
    if (
        source_stat_after.st_size != source_stat_before.st_size
        or source_stat_after.st_mtime_ns != source_stat_before.st_mtime_ns
    ):
        raise SpdConductorGraphError(
            "SPD source changed while topology evidence was compiled; retry from a stable file"
        )
    source_sha256 = _sha256_file(source_path)
    source_stat_hashed = source_path.stat()
    if (
        source_stat_hashed.st_size != source_stat_before.st_size
        or source_stat_hashed.st_mtime_ns != source_stat_before.st_mtime_ns
    ):
        raise SpdConductorGraphError(
            "SPD source changed while topology evidence was hashed; retry from a stable file"
        )
    diagnostic_tuple = tuple(diagnostics)
    certificate = _topology_certificate(
        source_sha256=source_sha256,
        source_size_bytes=source_stat_before.st_size,
        requested_nets=requested_nets,
        site=site,
        selected_layers=selected_layers,
        layers=stackup_layers,
        nodes=ordered_nodes,
        vias=ordered_vias,
        traces=ordered_traces,
        components=topology_components,
        electrical_components=electrical_components,
        diagnostics=diagnostic_tuple,
    )
    return SpdConductorGraph(
        source_path.resolve(), requested_nets, site, selected_layers, stackup_layers,
        ordered_nodes, ordered_vias, ordered_traces,
        tuple(sorted(padstack_evidence.values(), key=lambda item: item.name.casefold())),
        topology_adjacency,
        topology_components,
        electrical_adjacency,
        electrical_components,
        False,
        diagnostic_tuple,
        _frozen_mapping(statistics),
        certificate,
    )


__all__ = [
    "ConductorComponent",
    "ConductorGraphDiagnostic",
    "ConductorLayer",
    "ConductorNode",
    "ConductorPadDef",
    "ConductorPadStack",
    "ConductorTrace",
    "ConductorVia",
    "SpdConductorGraph",
    "SpdConductorGraphError",
    "TopologyCertificate",
    "extract_spd_conductor_graph",
]
