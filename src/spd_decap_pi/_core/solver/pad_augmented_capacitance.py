"""Research-only layer-local pad augmentation for exact Maxwell-C extraction.

The retained SPD plane assets contain direct ``.Shape`` artwork only.  This
module adds a separate, auditable electrostatic experiment: source-proven
``Regular`` pad copper is placed on its physical layer and appended to the
exact artwork model before the existing Maxwell-C extractor runs.  Pads never
inherit the surrounding plane net -- their explicit Node/Via NET remains the
conductor identity, so another-net copper inside a plane opening stays a
separate conductor.

This is not an evaluator adapter.  Trace width is irrelevant to this isolated
electrostatic term, while ambiguous Node/Via/PadStack evidence remains a hard
error.  Fringing and non-adjacent aperture-fill terms are not introduced.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite, pi, sin, sqrt
from numbers import Integral
from pathlib import Path
import re
from time import perf_counter
from typing import Any, Iterable, Iterator, Sequence, overload

from ..io.conductor_graph import (
    ConductorPadDef,
    ConductorPadStack,
    SpdConductorGraph,
)
from ..io import spd as _spd
from .multilayer_capacitance import (
    CapacitanceArtwork,
    MultilayerCapacitanceError,
    MultilayerCapacitanceModel,
    MultilayerCapacitanceResult,
    extract_multilayer_bulk_capacitance,
)


class PadAugmentedCapacitanceError(MultilayerCapacitanceError):
    """Raised when pad copper cannot be reconstructed without guessing."""


_ROTATION_RE = re.compile(
    r"(?:^|\s)(?:Absolute)?Rotation\s*=\s*([^\s]+)", re.IGNORECASE
)
_ALLOWED_ISOLATED_C_DIAGNOSTICS = frozenset({
    "TRACE_WIDTH_MISSING",
    "POLYGON_CONNECTIVITY_NOT_INCLUDED",
    "EDGE_LEAVES_LAYER_FILTER",
})
_CIRCLE_QUAD_SEGS = (32, 64, 128)
_CIRCLE_FINAL_AREA_ERROR_LIMIT = 3.0e-5


def _name(value: str, *, field: str) -> str:
    result = str(value).strip()
    if not result:
        raise PadAugmentedCapacitanceError(f"pad {field} must not be empty")
    return result


@dataclass(frozen=True, slots=True)
class PadPlacementEvidence:
    """One source-resolved layer-local Regular pad placement."""

    source_id: str
    layer: str
    net: str
    component_id: str
    x_um: float
    y_um: float
    padstack: str
    pad_def: ConductorPadDef
    rotation_deg: float | None
    provenance: str

    def __post_init__(self) -> None:
        for field_name in ("source_id", "layer", "net", "component_id", "padstack", "provenance"):
            object.__setattr__(self, field_name, _name(getattr(self, field_name), field=field_name))
        if not isfinite(self.x_um) or not isfinite(self.y_um):
            raise PadAugmentedCapacitanceError("pad coordinates must be finite")
        pad = self.pad_def
        if pad.layer.casefold() != self.layer.casefold():
            raise PadAugmentedCapacitanceError("PadDef layer does not match placement layer")
        if (
            pad.kind == "UNSUPPORTED"
            or pad.width_um is None
            or pad.height_um is None
            or not isfinite(pad.width_um)
            or not isfinite(pad.height_um)
            or pad.width_um <= 0.0
            or pad.height_um <= 0.0
        ):
            raise PadAugmentedCapacitanceError("Regular pad geometry is unsupported or dimensionless")
        if pad.kind == "RECTANGLE" and self.rotation_deg is None:
            raise PadAugmentedCapacitanceError("rectangular Regular pad has no source rotation evidence")
        if self.rotation_deg is not None and not isfinite(self.rotation_deg):
            raise PadAugmentedCapacitanceError("pad rotation must be finite when supplied")


@dataclass(frozen=True, slots=True)
class PadAreaSummary:
    layer: str
    net: str
    pad_count: int
    geometric_pad_area_um2: float
    added_union_area_um2: float


@dataclass(frozen=True, slots=True)
class PadPlacementExtraction(Sequence[PadPlacementEvidence]):
    """Placements plus explicitly irrelevant graph diagnostics.

    Keeping this metadata attached prevents the benchmark from accidentally
    hiding that the conductor graph lacked trace widths.  Width evidence is
    irrelevant to a layer-local electrostatic pad surface, but the omission is
    still reported rather than discarded.
    """

    placements: tuple[PadPlacementEvidence, ...]
    ignored_trace_width_diagnostics: int

    def __len__(self) -> int:
        return len(self.placements)

    @overload
    def __getitem__(self, index: int) -> PadPlacementEvidence: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[PadPlacementEvidence, ...]: ...

    def __getitem__(self, index: int | slice) -> PadPlacementEvidence | tuple[PadPlacementEvidence, ...]:
        return self.placements[index]

    def __iter__(self) -> Iterator[PadPlacementEvidence]:
        return iter(self.placements)


@dataclass(frozen=True, slots=True)
class PadAugmentationDiagnostics:
    source_placement_count: int
    unique_pad_count: int
    duplicate_pad_count: int
    circle_pad_count: int
    rectangle_pad_count: int
    circle_quad_segs: tuple[int, ...]
    circle_relative_area_errors: tuple[float, ...]
    circle_relative_refinement_deltas: tuple[float, ...]
    maximum_circle_area_error: float
    maximum_circle_refinement_delta: float
    ignored_trace_width_diagnostics: int
    area_by_layer_net: tuple[PadAreaSummary, ...]


@dataclass(frozen=True, slots=True)
class PadAugmentedCapacitanceModel:
    model: MultilayerCapacitanceModel
    diagnostics: PadAugmentationDiagnostics


@dataclass(frozen=True, slots=True)
class PadAugmentedCapacitanceResult:
    capacitance: MultilayerCapacitanceResult
    diagnostics: PadAugmentationDiagnostics


@dataclass(frozen=True, slots=True)
class StreamingLayerPadSummary:
    layer: str
    artwork_hole_count: int
    maximum_source_pad_reach_um: float
    bbox_retained_node_count: int
    raw_candidate_count: int
    unique_hole_pad_count: int
    idempotent_pad_count: int
    partially_added_pad_count: int
    fully_added_pad_count: int
    pad_union_area_um2: float
    added_union_area_um2: float


@dataclass(frozen=True, slots=True)
class StreamingReferencePadDiagnostics:
    source_path: str
    source_size_bytes: int
    source_line_passes: int
    scanned_reference_node_count: int
    requested_layer_node_count: int
    bbox_retained_node_count: int
    scanned_reference_via_count: int
    via_with_retained_endpoint_count: int
    referenced_padstack_count: int
    raw_candidate_count: int
    unique_candidate_count: int
    outside_hole_neighborhood_count: int
    unique_hole_pad_count: int
    emitted_non_idempotent_pad_count: int
    geometric_pad_union_area_um2: float
    added_union_area_um2: float
    padstack_prepass_seconds: float
    scan_seconds: float
    classification_seconds: float
    layer_summaries: tuple[StreamingLayerPadSummary, ...]


@dataclass(frozen=True, slots=True)
class StreamingReferencePadExtraction(Sequence[PadPlacementEvidence]):
    placements: tuple[PadPlacementEvidence, ...]
    added_artwork: tuple[CapacitanceArtwork, ...]
    diagnostics: StreamingReferencePadDiagnostics

    def __len__(self) -> int:
        return len(self.placements)

    @overload
    def __getitem__(self, index: int) -> PadPlacementEvidence: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[PadPlacementEvidence, ...]: ...

    def __getitem__(self, index: int | slice) -> PadPlacementEvidence | tuple[PadPlacementEvidence, ...]:
        return self.placements[index]

    def __iter__(self) -> Iterator[PadPlacementEvidence]:
        return iter(self.placements)


@dataclass(frozen=True, slots=True)
class _StreamingNode:
    source_id: str
    layer: str
    x_um: float
    y_um: float
    padstack: str | None
    source_line: str


@dataclass(frozen=True, slots=True)
class _StreamingCandidate:
    source_id: str
    node_id: str
    layer: str
    x_um: float
    y_um: float
    padstack: str
    source_tail: str
    provenance: str


def _rotation_from_source(source_tail: str, *, required: bool) -> float | None:
    match = _ROTATION_RE.search(source_tail or "")
    if match is None:
        if required:
            raise PadAugmentedCapacitanceError("rectangular Via pad has no source Rotation attribute")
        return None
    token = match.group(1)
    try:
        value = float(token)
    except ValueError as exc:
        raise PadAugmentedCapacitanceError(f"malformed source pad rotation {token!r}") from exc
    if not isfinite(value):
        raise PadAugmentedCapacitanceError("source pad rotation must be finite")
    return value


def _component_by_node(graph: SpdConductorGraph) -> dict[str, str]:
    result: dict[str, str] = {}
    for component in graph.topology_components:
        for key in component.node_keys:
            if key in result and result[key] != component.component_id:
                raise PadAugmentedCapacitanceError(f"Node {key!r} belongs to multiple topology components")
            result[key] = component.component_id
    return result


def pad_placements_from_conductor_graph(
    graph: SpdConductorGraph,
    *,
    requested_layers: Sequence[str],
) -> PadPlacementExtraction:
    """Extract every source-proven Node/Via Regular pad on requested layers.

    Missing trace width is explicitly irrelevant here.  All other error
    diagnostics, duplicate/ambiguous component membership, missing padstack
    definitions and unsupported pad geometry fail closed.
    """

    layers = tuple(_name(item, field="requested layer") for item in requested_layers)
    if not layers or len({item.casefold() for item in layers}) != len(layers):
        raise PadAugmentedCapacitanceError("requested_layers must be non-empty and unique")
    wanted = {item.casefold(): item for item in layers}
    blocking = tuple(
        item for item in graph.diagnostics
        if item.severity == "error" and item.code not in _ALLOWED_ISOLATED_C_DIAGNOSTICS
    )
    if blocking:
        summary = ", ".join(dict.fromkeys(item.code for item in blocking))
        raise PadAugmentedCapacitanceError(f"ambiguous/incomplete conductor graph evidence: {summary}")
    padstacks = {item.name.casefold(): item for item in graph.padstacks}
    if len(padstacks) != len(graph.padstacks):
        raise PadAugmentedCapacitanceError("duplicate retained PadStack evidence")
    nodes = {item.key: item for item in graph.nodes}
    if len(nodes) != len(graph.nodes):
        raise PadAugmentedCapacitanceError("duplicate retained Node evidence")
    components = _component_by_node(graph)
    placements: list[PadPlacementEvidence] = []

    def add(
        *, source_id: str, node_key: str, layer: str, net: str, x_um: float,
        y_um: float, padstack_name: str, pad_def: ConductorPadDef,
        rotation: float | None, provenance: str,
    ) -> None:
        if layer.casefold() not in wanted:
            return
        component = components.get(node_key)
        if component is None:
            raise PadAugmentedCapacitanceError(f"pad Node {node_key!r} has no unique topology component")
        placements.append(PadPlacementEvidence(
            source_id, wanted[layer.casefold()], net, component, x_um, y_um,
            padstack_name, pad_def, rotation, provenance,
        ))

    # Node PadStack evidence covers standalone pads and is also deliberately
    # deduplicated against Via endpoint evidence below.
    for node in graph.nodes:
        if node.layer.casefold() not in wanted or node.padstack is None:
            continue
        padstack = padstacks.get(node.padstack.casefold())
        if padstack is None:
            raise PadAugmentedCapacitanceError(
                f"Node {node.source_id!r} references missing PadStack {node.padstack!r}"
            )
        matches = tuple(item for item in padstack.pad_defs if item.layer.casefold() == node.layer.casefold())
        if len(matches) != 1:
            raise PadAugmentedCapacitanceError(
                f"Node {node.source_id!r} PadStack {padstack.name!r} has {len(matches)} PadDefs on {node.layer!r}"
            )
        pad = matches[0]
        rotation = _rotation_from_source("", required=pad.kind == "RECTANGLE")
        add(
            source_id=f"Node:{node.source_id}", node_key=node.key, layer=node.layer,
            net=node.net, x_um=node.x_um, y_um=node.y_um,
            padstack_name=padstack.name, pad_def=pad, rotation=rotation,
            provenance=f"Node {node.source_id} PadStack {padstack.name} {pad.source_record}",
        )

    for via in graph.vias:
        upper_key = f"{via.net.casefold()}\x1f{via.upper_node_id.casefold()}"
        lower_key = f"{via.net.casefold()}\x1f{via.lower_node_id.casefold()}"
        upper = nodes.get(upper_key)
        lower = nodes.get(lower_key)
        if upper is None or lower is None:
            raise PadAugmentedCapacitanceError(f"Via {via.source_id!r} endpoint Node evidence is missing")
        if abs(upper.x_um - lower.x_um) > 1.0e-9 or abs(upper.y_um - lower.y_um) > 1.0e-9:
            raise PadAugmentedCapacitanceError(f"Via {via.source_id!r} endpoints are not vertically co-located")
        if components.get(upper_key) != components.get(lower_key):
            raise PadAugmentedCapacitanceError(f"Via {via.source_id!r} endpoints have ambiguous component identity")
        layer_pads: dict[str, ConductorPadDef] = {}
        for pad in via.padstack.pad_defs:
            key = pad.layer.casefold()
            if key in layer_pads:
                raise PadAugmentedCapacitanceError(
                    f"Via {via.source_id!r} PadStack {via.padstack.name!r} has duplicate PadDef layer {pad.layer!r}"
                )
            layer_pads[key] = pad
        for layer in layers:
            if layer.casefold() not in {item.casefold() for item in via.interval_layers}:
                continue
            pad = layer_pads.get(layer.casefold())
            if pad is None:
                # A PadDef is required only where the source padstack claims a
                # layer-local pad.  An interval layer without PadDef is not
                # silently filled.
                continue
            rotation = _rotation_from_source(via.source_tail, required=pad.kind == "RECTANGLE")
            add(
                source_id=f"Via:{via.source_id}:{layer}", node_key=upper_key,
                layer=layer, net=via.net, x_um=upper.x_um, y_um=upper.y_um,
                padstack_name=via.padstack.name, pad_def=pad, rotation=rotation,
                provenance=f"Via {via.source_id} PadStack {via.padstack.name} {pad.source_record}",
            )
    ordered = tuple(sorted(
        placements,
        key=lambda item: (
            item.layer.casefold(), item.net.casefold(), item.x_um, item.y_um,
            item.padstack.casefold(), item.source_id.casefold(),
        ),
    ))
    return PadPlacementExtraction(
        ordered,
        sum(item.code == "TRACE_WIDTH_MISSING" for item in graph.diagnostics),
    )


def _pad_polygon(
    placement: PadPlacementEvidence,
) -> tuple[Any, float, float]:
    try:
        from shapely import affinity
        from shapely.geometry import Point, box
    except ImportError as exc:  # pragma: no cover
        raise PadAugmentedCapacitanceError("Shapely is required for pad copper geometry") from exc
    pad = placement.pad_def
    assert pad.width_um is not None and pad.height_um is not None
    if pad.kind == "CIRCLE":
        if abs(pad.width_um - pad.height_um) > max(pad.width_um, pad.height_um) * 1.0e-12:
            raise PadAugmentedCapacitanceError("circular pad width and height evidence disagree")
        radius = pad.width_um * 0.5
        exact_area = pi * radius * radius
        areas = tuple(float(Point(placement.x_um, placement.y_um).buffer(radius, quad_segs=value).area) for value in _CIRCLE_QUAD_SEGS)
        errors = tuple(abs(value - exact_area) / exact_area for value in areas)
        deltas = tuple(abs(areas[index + 1] - areas[index]) / exact_area for index in range(len(areas) - 1))
        if errors[-1] > _CIRCLE_FINAL_AREA_ERROR_LIMIT or not all(
            errors[index + 1] < errors[index] for index in range(len(errors) - 1)
        ) or not all(deltas[index + 1] < deltas[index] for index in range(len(deltas) - 1)):
            raise PadAugmentedCapacitanceError(
                f"circular pad discretization did not converge: errors={errors!r}, deltas={deltas!r}"
            )
        return Point(placement.x_um, placement.y_um).buffer(radius, quad_segs=_CIRCLE_QUAD_SEGS[-1]), errors[-1], deltas[-1]
    if pad.kind == "RECTANGLE":
        half_width, half_height = pad.width_um * 0.5, pad.height_um * 0.5
        geometry = box(
            placement.x_um - half_width, placement.y_um - half_height,
            placement.x_um + half_width, placement.y_um + half_height,
        )
        rotation = float(placement.rotation_deg or 0.0) % 360.0
        if rotation:
            geometry = affinity.rotate(geometry, rotation, origin=(placement.x_um, placement.y_um))
        return geometry, 0.0, 0.0
    raise PadAugmentedCapacitanceError(f"unsupported Regular pad shape {pad.kind!r}")


def _dedupe_key(placement: PadPlacementEvidence) -> tuple[Any, ...]:
    pad = placement.pad_def
    rotation = 0.0 if pad.kind == "CIRCLE" else float(placement.rotation_deg or 0.0) % 360.0
    return (
        placement.layer.casefold(), placement.net.casefold(),
        float(placement.x_um).hex(), float(placement.y_um).hex(),
        pad.kind, float(pad.width_um or 0.0).hex(), float(pad.height_um or 0.0).hex(),
        float(rotation).hex(),
    )


def _streaming_node_identity(raw: bytes) -> tuple[str, str] | None:
    separator = raw.find(b"::")
    cuts = [
        value
        for value in (raw.find(b"!!"), separator, raw.find(b" "))
        if value >= 0
    ]
    if not cuts or separator < 0:
        return None
    source_id = _spd._decode(raw[: min(cuts)])
    token = raw[separator + 2 :].split(None, 1)[0]
    net = _spd._decode(token)
    return (source_id, net) if source_id and net else None


def _polygon_parts(geometry: Any) -> Iterator[Any]:
    geometry_type = str(getattr(geometry, "geom_type", ""))
    if geometry_type == "Polygon":
        yield geometry
        return
    for part in getattr(geometry, "geoms", ()):
        yield from _polygon_parts(part)


def _parse_referenced_padstacks_from_line_stream(
    raw: bytes,
    *,
    referenced: set[str] | None,
    state: dict[str, Any],
    result: dict[str, ConductorPadStack],
) -> None:
    """Consume one PadStack-section line without retaining unrelated stacks."""

    stripped = raw.strip()
    folded = stripped.lower()

    def flush() -> None:
        name = state.get("name")
        if name is not None:
            key = name.casefold()
            if key in result:
                raise PadAugmentedCapacitanceError(
                    f"duplicate streamed PadStack definition {name!r}"
                )
            pad_defs = tuple(state.get("pad_defs", ()))
            widths = [item.width_um for item in pad_defs if item.width_um is not None]
            heights = [item.height_um for item in pad_defs if item.height_um is not None]
            result[key] = ConductorPadStack(
                name,
                None,
                max(widths) if widths else None,
                max(heights) if heights else None,
                tuple(dict.fromkeys(state.get("layers", ()))),
                None,
                None,
                pad_defs,
            )
        state.clear()

    if folded.startswith(b".padstackdef "):
        flush()
        tokens = stripped.split()
        name = _spd._decode(tokens[1]) if len(tokens) > 1 else ""
        if referenced is None or name.casefold() in referenced:
            state.update(name=name, layers=[], pad_defs=[], active_layer=None)
        else:
            state.update(name=None, layers=[], pad_defs=[], active_layer=None)
    elif folded.startswith(b".endpadstackdef"):
        flush()
    elif state.get("name") is not None and folded.startswith(b".paddef "):
        tokens = stripped.split(None, 1)
        if len(tokens) != 2:
            raise PadAugmentedCapacitanceError("malformed streamed .PadDef record")
        layer = _spd._decode(tokens[1])
        state["active_layer"] = layer
        state["layers"].append(layer)
    elif state.get("name") is not None and folded.startswith(b".endpaddef"):
        state["active_layer"] = None
    elif state.get("name") is not None and folded.startswith(b"regular "):
        layer = state.get("active_layer")
        if not layer:
            raise PadAugmentedCapacitanceError("Regular pad geometry has no active .PadDef layer")
        values = _spd._lengths(stripped)
        candidate: tuple[float, float] | None = None
        if folded.startswith(b"regular circle") and values:
            candidate = (2.0 * values[0], 2.0 * values[0])
            kind = "CIRCLE"
        elif folded.startswith(b"regular square") and values:
            candidate = (values[0], values[0])
            kind = "RECTANGLE"
        elif folded.startswith(b"regular box") and len(values) >= 2:
            candidate = (values[0], values[1])
            kind = "RECTANGLE"
        else:
            kind = "UNSUPPORTED"
        state["pad_defs"].append(ConductorPadDef(
            layer,
            kind,
            candidate[0] if candidate is not None else None,
            candidate[1] if candidate is not None else None,
            _spd._decode(stripped),
        ))


def _stream_all_padstacks(path: Path) -> dict[str, ConductorPadStack]:
    """Parse only the bounded PadStack section with constant-size buffers."""

    marker = b"* PadStack collection description lines"
    end_markers = (
        b"* Material description lines",
        b"* Circuit description lines",
    )
    result: dict[str, ConductorPadStack] = {}
    state: dict[str, Any] = {}
    def find_bytes(handle: Any, needle: bytes, *, start: int = 0) -> int:
        chunk_size = 8 * 1024 * 1024
        overlap = max(0, len(needle) - 1)
        handle.seek(start)
        position = start
        tail = b""
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return -1
            data = tail + chunk
            index = data.find(needle)
            if index >= 0:
                return position - len(tail) + index
            tail = data[-overlap:] if overlap else b""
            position += len(chunk)

    with path.open("rb") as handle:
        start = find_bytes(handle, marker)
        if start < 0:
            raise PadAugmentedCapacitanceError("SPD has no PadStack collection section")
        end_candidates = [
            position
            for value in end_markers
            if (position := find_bytes(handle, value, start=start + len(marker))) >= 0
        ]
        end = min(end_candidates, default=path.stat().st_size)
        handle.seek(start)
        while handle.tell() < end:
            raw = handle.readline()
            if not raw:
                break
            _parse_referenced_padstacks_from_line_stream(
                raw,
                referenced=None,
                state=state,
                result=result,
            )
    if state.get("name") is not None:
        raise PadAugmentedCapacitanceError("last PadStack is missing .EndPadStackDef")
    if not result:
        raise PadAugmentedCapacitanceError("SPD PadStack section contains no definitions")
    return result


@dataclass(frozen=True, slots=True)
class _HoleEnvelopeIndex:
    cell_um: float
    envelopes: tuple[tuple[float, float, float, float], ...]
    cells: dict[tuple[int, int], tuple[int, ...]]

    def contains_candidate_center(self, x_um: float, y_um: float) -> bool:
        cell = (int(x_um // self.cell_um), int(y_um // self.cell_um))
        for index in self.cells.get(cell, ()):
            min_x, min_y, max_x, max_y = self.envelopes[index]
            if min_x <= x_um <= max_x and min_y <= y_um <= max_y:
                return True
        return False


def _hole_envelope_index(
    holes: Sequence[Any],
    maximum_pad_reach_um: float,
) -> _HoleEnvelopeIndex:
    if not isfinite(maximum_pad_reach_um) or maximum_pad_reach_um <= 0.0:
        raise PadAugmentedCapacitanceError("source pad reach must be finite and positive")
    cell_um = max(250.0, 4.0 * maximum_pad_reach_um)
    envelopes = tuple(
        (
            float(hole.bounds[0]) - maximum_pad_reach_um,
            float(hole.bounds[1]) - maximum_pad_reach_um,
            float(hole.bounds[2]) + maximum_pad_reach_um,
            float(hole.bounds[3]) + maximum_pad_reach_um,
        )
        for hole in holes
    )
    mutable: dict[tuple[int, int], list[int]] = {}
    for index, (min_x, min_y, max_x, max_y) in enumerate(envelopes):
        for cell_x in range(int(min_x // cell_um), int(max_x // cell_um) + 1):
            for cell_y in range(int(min_y // cell_um), int(max_y // cell_um) + 1):
                mutable.setdefault((cell_x, cell_y), []).append(index)
    return _HoleEnvelopeIndex(
        cell_um,
        envelopes,
        {key: tuple(values) for key, values in mutable.items()},
    )


def stream_spd_reference_regular_pads(
    path: str | Path,
    models: Sequence[MultilayerCapacitanceModel],
    *,
    reference_net: str = "DGND",
    max_retained_nodes: int = 500_000,
    max_raw_candidates: int = 750_000,
    max_unique_hole_pads: int = 150_000,
    max_emitted_pads: int = 150_000,
    max_runtime_seconds: float = 480.0,
) -> StreamingReferencePadExtraction:
    """Stream hole-local reference-NET pads without materialising its graph.

    The Node/Via source is read linearly once.  A bounded mmap prepass reads
    only the PadStack section so each artwork-hole envelope can be expanded by
    the maximum source-proven pad half-diagonal on that layer.  Only
    reference-NET Nodes inside those conservative envelopes are retained.
    An exact geometry gate then keeps only pads intersecting a retained hole.
    Pads already covered by same-NET ``.Shape`` are counted as idempotent and
    not emitted.

    This bounded path is intended for very large reference nets (the sample
    DGND has over 1.5 million Nodes).  It does not infer an intermediate pad
    from a Via whose source endpoint is outside the retained layer/box scope.
    MLO microvia endpoint pads are preserved because their layer-local Node is
    retained; missing PadStack/layer/rotation evidence remains a hard error.
    """

    source_path = Path(path)
    if not source_path.is_file():
        raise PadAugmentedCapacitanceError(f"SPD source is not a file: {source_path}")
    if not models:
        raise PadAugmentedCapacitanceError("at least one exact artwork model is required")
    if min(
        max_retained_nodes,
        max_raw_candidates,
        max_unique_hole_pads,
        max_emitted_pads,
    ) <= 0:
        raise PadAugmentedCapacitanceError("streaming pad safety gates must be positive")
    if not isfinite(max_runtime_seconds) or max_runtime_seconds <= 0.0:
        raise PadAugmentedCapacitanceError("streaming pad runtime gate must be finite and positive")
    total_started = perf_counter()

    def check_runtime(phase: str) -> None:
        elapsed = perf_counter() - total_started
        if elapsed > max_runtime_seconds:
            raise PadAugmentedCapacitanceError(
                f"streamed reference pad runtime gate exceeded ({max_runtime_seconds:.3f}s) "
                f"during {phase} at {elapsed:.3f}s; full-P0 DGND augmentation is blocked"
            )
    reference = _name(reference_net, field="reference net")
    reference_key = reference.casefold()

    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        from shapely.strtree import STRtree
    except ImportError as exc:  # pragma: no cover
        raise PadAugmentedCapacitanceError("Shapely is required for streamed pad geometry") from exc

    layer_names: dict[str, str] = {}
    artwork_groups: dict[tuple[str, str], list[Any]] = {}
    for model in models:
        for layer in model.layer_order:
            key = layer.casefold()
            prior = layer_names.setdefault(key, layer)
            if prior != layer:
                raise PadAugmentedCapacitanceError(
                    f"case-variant requested layer evidence {prior!r}/{layer!r}"
                )
        for item in model.artwork:
            artwork_groups.setdefault(
                (item.layer.casefold(), item.net.casefold()), []
            ).append(item.geometry_um)
    artwork = {
        key: unary_union(values) for key, values in artwork_groups.items()
    }
    same_by_layer: dict[str, Any] = {}
    other_by_layer: dict[str, Any] = {}
    holes_by_layer: dict[str, tuple[Any, ...]] = {}
    for layer_key in layer_names:
        layer_items = [shape for (layer, _net), shape in artwork.items() if layer == layer_key]
        if not layer_items:
            continue
        same_items = [
            shape for (layer, net), shape in artwork.items()
            if layer == layer_key and net == reference_key
        ]
        other_items = [
            shape for (layer, net), shape in artwork.items()
            if layer == layer_key and net != reference_key
        ]
        same_by_layer[layer_key] = unary_union(same_items) if same_items else None
        other_by_layer[layer_key] = unary_union(other_items) if other_items else None
        holes: list[Any] = []
        seen_holes: set[bytes] = set()
        for shape in layer_items:
            for polygon in _polygon_parts(shape):
                for ring in polygon.interiors:
                    hole = Polygon(ring)
                    fingerprint = bytes(hole.wkb)
                    if hole.area > 1.0e-9 and fingerprint not in seen_holes:
                        holes.append(hole)
                        seen_holes.add(fingerprint)
        holes_by_layer[layer_key] = tuple(holes)
    if not holes_by_layer or not any(holes_by_layer.values()):
        raise PadAugmentedCapacitanceError("requested exact artwork contains no pad-hole scope")

    prepass_started = perf_counter()
    padstacks = _stream_all_padstacks(source_path)
    maximum_reach_by_layer: dict[str, float] = {}
    for layer_key, holes in holes_by_layer.items():
        if not holes:
            continue
        dimensions: list[float] = []
        for stack in padstacks.values():
            for pad in stack.pad_defs:
                if pad.layer.casefold() != layer_key:
                    continue
                if (
                    pad.kind == "UNSUPPORTED"
                    or pad.width_um is None
                    or pad.height_um is None
                    or pad.width_um <= 0.0
                    or pad.height_um <= 0.0
                ):
                    raise PadAugmentedCapacitanceError(
                        f"requested layer {layer_names[layer_key]!r} has unsupported source PadDef geometry"
                    )
                dimensions.append(sqrt(pad.width_um * pad.width_um + pad.height_um * pad.height_um) * 0.5)
        if not dimensions:
            raise PadAugmentedCapacitanceError(
                f"requested layer {layer_names[layer_key]!r} has holes but no source PadDef dimensions"
            )
        maximum_reach_by_layer[layer_key] = max(dimensions)
    hole_envelope_indices = {
        layer: _hole_envelope_index(holes, maximum_reach_by_layer[layer])
        for layer, holes in holes_by_layer.items()
        if holes
    }
    padstack_prepass_seconds = perf_counter() - prepass_started
    check_runtime("PadStack prepass")

    nodes: dict[str, _StreamingNode] = {}
    candidates: list[_StreamingCandidate] = []
    referenced_padstacks: set[str] = set()
    counts: Counter[str] = Counter()
    layer_node_counts: Counter[str] = Counter()
    layer_candidate_counts: Counter[str] = Counter()
    seen_candidate_vias: set[str] = set()
    scan_started = perf_counter()

    with source_path.open("rb") as handle:
        for raw_line in handle:
            raw = raw_line.rstrip(b"\r\n")
            if raw.startswith(b"Node"):
                identity = _streaming_node_identity(raw)
                if identity is None:
                    if b"::" + reference.encode("utf-8") in raw:
                        raise PadAugmentedCapacitanceError("malformed reference-NET Node record")
                    continue
                source_id, net = identity
                if net.casefold() != reference_key:
                    continue
                counts["scanned_reference_nodes"] += 1
                if counts["scanned_reference_nodes"] % 50_000 == 0:
                    check_runtime("reference Node scan")
                layer_raw = _spd._attribute(raw, b"Layer")
                if layer_raw is None:
                    raise PadAugmentedCapacitanceError(
                        f"reference Node {source_id!r} has no source layer"
                    )
                layer = _spd._decode(layer_raw)
                layer_key = layer.casefold()
                envelope_index = hole_envelope_indices.get(layer_key)
                if envelope_index is None:
                    continue
                counts["requested_layer_nodes"] += 1
                attributes = _spd._NODE_ATTR_RE.search(raw)
                if attributes is None:
                    raise PadAugmentedCapacitanceError(
                        f"reference Node {source_id!r} has no usable X/Y evidence"
                    )
                try:
                    x_um = _spd._length_um(attributes.group(1))
                    y_um = _spd._length_um(attributes.group(2))
                except ValueError as exc:
                    raise PadAugmentedCapacitanceError(
                        f"reference Node {source_id!r} has invalid X/Y evidence"
                    ) from exc
                if not envelope_index.contains_candidate_center(x_um, y_um):
                    continue
                key = source_id.casefold()
                if key in nodes:
                    raise PadAugmentedCapacitanceError(
                        f"duplicate retained reference Node {source_id!r}"
                    )
                padstack_raw = _spd._attribute(raw, b"PadStack")
                padstack = (
                    _spd._decode(padstack_raw)
                    if padstack_raw is not None
                    else (
                        _spd._decode(attributes.group(3))
                        if attributes.group(3) is not None
                        else None
                    )
                )
                decoded = _spd._decode(raw)
                node = _StreamingNode(source_id, layer_names[layer_key], x_um, y_um, padstack, decoded)
                nodes[key] = node
                counts["bbox_retained_nodes"] += 1
                layer_node_counts[layer_key] += 1
                if len(nodes) > max_retained_nodes:
                    raise PadAugmentedCapacitanceError(
                        f"streamed reference Node gate exceeded ({max_retained_nodes})"
                    )
                if padstack is not None:
                    referenced_padstacks.add(padstack.casefold())
                    candidates.append(_StreamingCandidate(
                        f"Node:{source_id}",
                        source_id,
                        node.layer,
                        x_um,
                        y_um,
                        padstack,
                        decoded,
                        f"streamed Node {source_id} {decoded}",
                    ))
                    layer_candidate_counts[layer_key] += 1
            elif raw.startswith(b"Via"):
                match = _spd._VIA_RE.match(raw)
                if match is None:
                    identity = _streaming_node_identity(raw)
                    if identity is not None and identity[1].casefold() == reference_key:
                        raise PadAugmentedCapacitanceError("malformed reference-NET Via record")
                    continue
                net = _spd._decode(match.group(2))
                if net.casefold() != reference_key:
                    continue
                counts["scanned_reference_vias"] += 1
                if counts["scanned_reference_vias"] % 50_000 == 0:
                    check_runtime("reference Via scan")
                for attribute in (b"UpperNode", b"LowerNode"):
                    token = _spd._attribute(raw, attribute)
                    if token is not None and b"::" in token:
                        endpoint_net = token.split(b"::", 1)[1]
                        if _spd._decode(endpoint_net).casefold() != reference_key:
                            raise PadAugmentedCapacitanceError(
                                "reference Via endpoint has conflicting NET evidence"
                            )
                upper_id = _spd._decode(match.group(3))
                lower_id = _spd._decode(match.group(4))
                retained = tuple(
                    item
                    for item in (
                        nodes.get(upper_id.casefold()),
                        nodes.get(lower_id.casefold()),
                    )
                    if item is not None
                )
                if not retained:
                    continue
                via_id = _spd._decode(match.group(1))
                via_key = via_id.casefold()
                if via_key in seen_candidate_vias:
                    raise PadAugmentedCapacitanceError(
                        f"duplicate retained reference Via {via_id!r}"
                    )
                seen_candidate_vias.add(via_key)
                if len(retained) == 2 and (
                    abs(retained[0].x_um - retained[1].x_um) > 1.0e-9
                    or abs(retained[0].y_um - retained[1].y_um) > 1.0e-9
                ):
                    raise PadAugmentedCapacitanceError(
                        f"reference Via {via_id!r} endpoints are not vertically co-located"
                    )
                counts["vias_with_retained_endpoint"] += 1
                padstack = _spd._decode(match.group(5))
                referenced_padstacks.add(padstack.casefold())
                tail = _spd._decode(match.group(6))
                for node in {item.source_id.casefold(): item for item in retained}.values():
                    candidates.append(_StreamingCandidate(
                        f"Via:{via_id}:{node.layer}",
                        node.source_id,
                        node.layer,
                        node.x_um,
                        node.y_um,
                        padstack,
                        tail,
                        f"streamed Via {via_id} PadStack {padstack} {tail}",
                    ))
                    layer_candidate_counts[node.layer.casefold()] += 1
                if len(candidates) > max_raw_candidates:
                    raise PadAugmentedCapacitanceError(
                        f"streamed reference pad candidate gate exceeded ({max_raw_candidates})"
                    )
    scan_seconds = perf_counter() - scan_started
    missing_padstacks = sorted(referenced_padstacks - set(padstacks))
    if missing_padstacks:
        raise PadAugmentedCapacitanceError(
            "streamed reference pads have missing PadStack definitions: "
            + ", ".join(missing_padstacks[:8])
        )

    classification_started = perf_counter()
    hole_trees = {
        layer: STRtree(list(holes)) for layer, holes in holes_by_layer.items() if holes
    }
    hole_identity_indices = {
        layer: {id(hole): index for index, hole in enumerate(holes)}
        for layer, holes in holes_by_layer.items()
    }
    seen_all_candidates: set[tuple[Any, ...]] = set()
    pad_union_by_hole: dict[str, dict[int, Any]] = {}
    added_union_by_hole: dict[str, dict[int, Any]] = {}
    emitted: list[PadPlacementEvidence] = []
    layer_unique: Counter[str] = Counter()
    layer_idempotent: Counter[str] = Counter()
    layer_partial: Counter[str] = Counter()
    layer_full: Counter[str] = Counter()
    outside_holes = 0
    for candidate_index, candidate in enumerate(candidates):
        if candidate_index % 256 == 0:
            check_runtime("exact hole-pad classification/union")
        stack = padstacks.get(candidate.padstack.casefold())
        if stack is None:
            raise PadAugmentedCapacitanceError(
                f"candidate {candidate.source_id!r} references missing PadStack {candidate.padstack!r}"
            )
        matches = tuple(
            item for item in stack.pad_defs
            if item.layer.casefold() == candidate.layer.casefold()
        )
        if len(matches) != 1:
            raise PadAugmentedCapacitanceError(
                f"candidate {candidate.source_id!r} PadStack {stack.name!r} has {len(matches)} PadDefs on {candidate.layer!r}"
            )
        pad_def = matches[0]
        rotation = _rotation_from_source(
            candidate.source_tail,
            required=pad_def.kind == "RECTANGLE",
        )
        placement = PadPlacementEvidence(
            candidate.source_id,
            candidate.layer,
            reference,
            f"stream:{candidate.node_id}",
            candidate.x_um,
            candidate.y_um,
            stack.name,
            pad_def,
            rotation,
            candidate.provenance,
        )
        dedupe_key = _dedupe_key(placement)
        if dedupe_key in seen_all_candidates:
            continue
        seen_all_candidates.add(dedupe_key)
        geometry, _error, _delta = _pad_polygon(placement)
        layer_key = placement.layer.casefold()
        tree = hole_trees.get(layer_key)
        if tree is None:
            outside_holes += 1
            continue
        query = tree.query(geometry)
        holes = holes_by_layer[layer_key]
        nearby = (
            [(int(item), holes[int(item)]) for item in query]
            if len(query) and isinstance(query[0], Integral)
            else [
                (hole_identity_indices[layer_key][id(item)], item)
                for item in query
            ]
        )
        intersecting_holes = tuple(
            index
            for index, hole in nearby
            if float(geometry.intersection(hole).area) > 1.0e-9
        )
        if not intersecting_holes:
            outside_holes += 1
            continue
        layer_unique[layer_key] += 1
        if sum(layer_unique.values()) > max_unique_hole_pads:
            raise PadAugmentedCapacitanceError(
                "streamed reference unique hole-pad gate exceeded "
                f"({max_unique_hole_pads}) at {sum(layer_unique.values())} pads; "
                f"scanned_nodes={counts['scanned_reference_nodes']}, "
                f"requested_layer_nodes={counts['requested_layer_nodes']}, "
                f"retained_nodes={counts['bbox_retained_nodes']}, "
                f"raw_candidates={len(candidates)}, "
                f"unique_candidates_seen={len(seen_all_candidates)}; "
                "full-P0 DGND augmentation is blocked"
            )
        other = other_by_layer.get(layer_key)
        tolerance = max(1.0e-6, float(geometry.area) * 1.0e-8)
        if other is not None and not bool(getattr(other, "is_empty", True)):
            overlap = float(geometry.intersection(other).area)
            if overlap > tolerance:
                raise PadAugmentedCapacitanceError(
                    f"reference pad {placement.source_id!r} overlaps other-NET Shape by {overlap:.9g} um^2"
                )
        same = same_by_layer.get(layer_key)
        added = geometry if same is None else geometry.difference(same)
        added_area = float(added.area)
        pad_area = float(geometry.area)
        pad_holes = pad_union_by_hole.setdefault(layer_key, {})
        for hole_index in intersecting_holes:
            prior = pad_holes.get(hole_index)
            pad_holes[hole_index] = geometry if prior is None else prior.union(geometry)
        if added_area <= tolerance:
            layer_idempotent[layer_key] += 1
            continue
        emitted.append(placement)
        added_holes = added_union_by_hole.setdefault(layer_key, {})
        for hole_index in intersecting_holes:
            prior = added_holes.get(hole_index)
            added_holes[hole_index] = added if prior is None else prior.union(added)
        if pad_area - added_area <= tolerance:
            layer_full[layer_key] += 1
        else:
            layer_partial[layer_key] += 1
        if len(emitted) > max_emitted_pads:
            raise PadAugmentedCapacitanceError(
                f"streamed reference emitted-pad gate exceeded ({max_emitted_pads})"
            )

    summaries: list[StreamingLayerPadSummary] = []
    added_artwork: list[CapacitanceArtwork] = []
    total_pad_area = 0.0
    total_added_area = 0.0
    for layer_key, display in sorted(layer_names.items(), key=lambda item: item[1].casefold()):
        check_runtime(f"final layer union {display}")
        pads = unary_union(tuple(pad_union_by_hole.get(layer_key, {}).values()))
        added = unary_union(tuple(added_union_by_hole.get(layer_key, {}).values()))
        pad_area = float(getattr(pads, "area", 0.0))
        added_area = float(getattr(added, "area", 0.0))
        if added_area > 1.0e-9:
            added_artwork.append(CapacitanceArtwork(display, reference, added))
        total_pad_area += pad_area
        total_added_area += added_area
        summaries.append(StreamingLayerPadSummary(
            display,
            len(holes_by_layer.get(layer_key, ())),
            maximum_reach_by_layer.get(layer_key, 0.0),
            layer_node_counts[layer_key],
            layer_candidate_counts[layer_key],
            layer_unique[layer_key],
            layer_idempotent[layer_key],
            layer_partial[layer_key],
            layer_full[layer_key],
            pad_area,
            added_area,
        ))
    classification_seconds = perf_counter() - classification_started
    ordered = tuple(sorted(
        emitted,
        key=lambda item: (
            item.layer.casefold(), item.x_um, item.y_um,
            item.padstack.casefold(), item.source_id.casefold(),
        ),
    ))
    diagnostics = StreamingReferencePadDiagnostics(
        str(source_path),
        source_path.stat().st_size,
        1,
        counts["scanned_reference_nodes"],
        counts["requested_layer_nodes"],
        counts["bbox_retained_nodes"],
        counts["scanned_reference_vias"],
        counts["vias_with_retained_endpoint"],
        len(referenced_padstacks),
        len(candidates),
        len(seen_all_candidates),
        outside_holes,
        sum(layer_unique.values()),
        len(ordered),
        total_pad_area,
        total_added_area,
        padstack_prepass_seconds,
        scan_seconds,
        classification_seconds,
        tuple(summaries),
    )
    return StreamingReferencePadExtraction(ordered, tuple(added_artwork), diagnostics)


def augment_multilayer_capacitance_with_streamed_reference(
    model: MultilayerCapacitanceModel,
    streamed: StreamingReferencePadExtraction,
) -> MultilayerCapacitanceModel:
    """Append already hole-unioned streamed reference copper to one slab."""

    layers = {item.casefold() for item in model.layer_order}
    artwork = tuple(
        item for item in streamed.added_artwork if item.layer.casefold() in layers
    )
    if not artwork:
        raise PadAugmentedCapacitanceError(
            "streamed reference extraction has no added copper in the requested slab"
        )
    return MultilayerCapacitanceModel(
        model.layer_order,
        model.gaps,
        model.artwork + artwork,
        model.opening_fill_by_layer,
        model.enable_nonadjacent_opening_coupling,
    )


def augment_multilayer_capacitance_with_pads(
    model: MultilayerCapacitanceModel,
    placements: Iterable[PadPlacementEvidence],
) -> PadAugmentedCapacitanceModel:
    """Append deterministic, source-proven pad copper to an exact Shape model."""

    layer_names = {item.casefold(): item for item in model.layer_order}
    source = tuple(placements)
    net_names = {item.net.casefold(): item.net for item in model.artwork}
    for item in source:
        key = item.net.casefold()
        prior = net_names.get(key)
        if prior is not None and prior != item.net:
            raise PadAugmentedCapacitanceError(
                f"pad NET {item.net!r} has ambiguous case-variant evidence {prior!r}"
            )
        # A source-proven pad may be the only copper for its NET in this slab.
        # Retain it as its own Maxwell conductor instead of assigning it to the
        # surrounding plane or dropping it merely because no .Shape exists.
        net_names[key] = item.net
    unique: dict[tuple[Any, ...], PadPlacementEvidence] = {}
    for item in source:
        if item.layer.casefold() not in layer_names:
            raise PadAugmentedCapacitanceError(f"pad layer {item.layer!r} is outside the requested slab")
        unique.setdefault(_dedupe_key(item), item)
    geometries: list[tuple[PadPlacementEvidence, Any, float, float]] = []
    for key in sorted(unique):
        item = unique[key]
        geometry, error, delta = _pad_polygon(item)
        geometries.append((item, geometry, error, delta))

    try:
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover
        raise PadAugmentedCapacitanceError("Shapely is required for pad copper union") from exc
    base_groups: dict[tuple[str, str], list[Any]] = {}
    pad_groups: dict[tuple[str, str], list[Any]] = {}
    counts: Counter[tuple[str, str]] = Counter()
    for item in model.artwork:
        base_groups.setdefault((item.layer.casefold(), item.net.casefold()), []).append(item.geometry_um)
    for item, geometry, _error, _delta in geometries:
        key = (item.layer.casefold(), item.net.casefold())
        pad_groups.setdefault(key, []).append(geometry)
        counts[key] += 1
    summaries: list[PadAreaSummary] = []
    for key in sorted(pad_groups):
        pads = unary_union(pad_groups[key])
        base = unary_union(base_groups.get(key, ()))
        added = pads.difference(base)
        layer = layer_names[key[0]]
        net = net_names[key[1]]
        summaries.append(PadAreaSummary(layer, net, counts[key], float(pads.area), float(added.area)))
    pad_artwork = tuple(
        CapacitanceArtwork(layer_names[item.layer.casefold()], net_names[item.net.casefold()], geometry)
        for item, geometry, _error, _delta in geometries
    )
    augmented = MultilayerCapacitanceModel(
        model.layer_order,
        model.gaps,
        model.artwork + pad_artwork,
        model.opening_fill_by_layer,
        model.enable_nonadjacent_opening_coupling,
    )
    errors = [error for _item, _geometry, error, _delta in geometries]
    deltas = [delta for _item, _geometry, _error, delta in geometries]
    # Shapely's ``quad_segs=q`` circle is a regular 4q-gon.  Record the
    # scale-independent analytical error sequence so JSON evidence contains
    # the complete 32 -> 64 -> 128 refinement, not only the final tolerance.
    exact_circle_errors = tuple(
        abs((2.0 * value * sin(pi / (2.0 * value))) - pi) / pi
        for value in _CIRCLE_QUAD_SEGS
    )
    exact_circle_deltas = tuple(
        abs(exact_circle_errors[index] - exact_circle_errors[index + 1])
        for index in range(len(exact_circle_errors) - 1)
    )
    ignored = int(getattr(placements, "ignored_trace_width_diagnostics", 0))
    diagnostics = PadAugmentationDiagnostics(
        len(source), len(unique), len(source) - len(unique),
        sum(item.pad_def.kind == "CIRCLE" for item in unique.values()),
        sum(item.pad_def.kind == "RECTANGLE" for item in unique.values()),
        _CIRCLE_QUAD_SEGS,
        exact_circle_errors if errors else (),
        exact_circle_deltas if errors else (),
        max(errors, default=0.0), max(deltas, default=0.0), ignored,
        tuple(summaries),
    )
    return PadAugmentedCapacitanceModel(augmented, diagnostics)


def extract_pad_augmented_bulk_capacitance(
    model: MultilayerCapacitanceModel,
    placements: Iterable[PadPlacementEvidence],
    *,
    reference_net: str = "DGND",
) -> PadAugmentedCapacitanceResult:
    """Extract a full Maxwell matrix; floating reduction remains a later step."""

    augmented = augment_multilayer_capacitance_with_pads(model, placements)
    result = extract_multilayer_bulk_capacitance(augmented.model, reference_net=reference_net)
    return PadAugmentedCapacitanceResult(result, augmented.diagnostics)


__all__ = [
    "PadAreaSummary",
    "PadAugmentationDiagnostics",
    "PadAugmentedCapacitanceError",
    "PadAugmentedCapacitanceModel",
    "PadAugmentedCapacitanceResult",
    "PadPlacementEvidence",
    "PadPlacementExtraction",
    "StreamingLayerPadSummary",
    "StreamingReferencePadDiagnostics",
    "StreamingReferencePadExtraction",
    "augment_multilayer_capacitance_with_pads",
    "augment_multilayer_capacitance_with_streamed_reference",
    "extract_pad_augmented_bulk_capacitance",
    "pad_placements_from_conductor_graph",
    "stream_spd_reference_regular_pads",
]
