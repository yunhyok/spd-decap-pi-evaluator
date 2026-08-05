"""Source-only bridge for the guarded uniform-admittance research profile.

This module never reads a Touchstone file or comparison result.  It promotes
only a deliberately narrow manufactured/single-component path: complete
retained SPD artwork, one selected-net island, a pure common reference, and
confirmed partition/device evidence.  Anything less is an actionable
fail-closed result, never an automatic legacy fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
from numpy.typing import NDArray

from .modal import DielectricDispersion
from .multilayer_capacitance import (
    MultilayerCapacitanceError,
    capacitance_model_from_project,
    extract_multilayer_bulk_capacitance,
)
from .profiles import solver_profile_static_identity_sha256
from .uniform_c00 import (
    DispersiveAdjacentGap,
    UniformC00Assembly,
    UniformC00Error,
    UniformPortConnectivityEvidence,
    assemble_uniform_effective_admittance,
)


PROFILE_COMPILER_VERSION = "research-uniform-source-v1"


def _material_manifest_hash(project: Any, layer_order: Sequence[str]) -> str:
    """Hash only source stack/material facts consumed by the C00 compiler."""

    wanted = {_key(layer) for layer in layer_order}
    rows: list[dict[str, Any]] = []
    for layer in project.stackup_layers:
        if _key(layer.name) not in wanted and not layer.is_conductor:
            # A dielectric belongs to this manifest only when it is physically
            # between two selected retained conductors.
            positions = {_key(item.name): index for index, item in enumerate(project.stackup_layers)}
            index = positions[_key(layer.name)]
            above = any(
                _key(item.name) in wanted and item.is_conductor
                for item in project.stackup_layers[:index]
            )
            below = any(
                _key(item.name) in wanted and item.is_conductor
                for item in project.stackup_layers[index + 1 :]
            )
            if not (above and below):
                continue
        if _key(layer.name) not in wanted and layer.is_conductor:
            continue
        properties = [
            {
                "frequency_hz": float(point.frequency_hz),
                "dk": float(point.dk),
                "df": float(point.df),
            }
            for point in getattr(layer, "dielectric_properties", ())
        ]
        rows.append(
            {
                "name": _key(layer.name),
                "is_conductor": bool(layer.is_conductor),
                "thickness_um": float(layer.thickness_um),
                "conductivity_s_m": (
                    float(layer.conductivity_s_m)
                    if layer.conductivity_s_m is not None
                    else None
                ),
                "dk": float(layer.dk) if layer.dk is not None else None,
                "df": float(layer.df) if layer.df is not None else None,
                "dielectric_properties": properties,
            }
        )
    return sha256(_canonical_json(rows)).hexdigest()


def _geometry_manifest_hash(records: Sequence[Mapping[str, Any]]) -> str:
    manifest = [
        {
            "layer": _key(record["layer"]),
            "net": _key(record["net"]),
            "asset_sha256": str(record["asset_sha256"]).lower(),
        }
        for record in records
    ]
    manifest.sort(key=lambda item: (item["layer"], item["net"], item["asset_sha256"]))
    return sha256(_canonical_json(manifest)).hexdigest()


class ResearchProfileUnavailable(ValueError):
    """Structured, actionable evidence-gate failure with no fallback curve."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code).strip().upper()
        if not self.code:
            self.code = "EVIDENCE_INCOMPLETE"
        self.details = MappingProxyType(dict(details or {}))
        super().__init__(
            f"Research profile unavailable [{self.code}]: {message} "
            "Switch to Legacy modal or repair/reimport the named source evidence."
        )


@dataclass(frozen=True, slots=True)
class UniformC00SourceModel:
    """Immutable source-derived adjacent-gap data reusable on refined grids."""

    partials: tuple[DispersiveAdjacentGap, ...]
    reference_net: str
    selected_net: str
    port_connectivity: UniformPortConnectivityEvidence
    evidence_sha256: str
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.partials:
            raise ResearchProfileUnavailable(
                "UNIFORM_PARTIALS_MISSING",
                "no adjacent physical-gap Maxwell partials were compiled",
            )
        if not self.reference_net.strip() or not self.selected_net.strip():
            raise ResearchProfileUnavailable(
                "UNIFORM_NET_IDENTITY_MISSING",
                "selected and reference net identities must be explicit",
            )
        if (
            len(self.evidence_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.evidence_sha256)
        ):
            raise ResearchProfileUnavailable(
                "UNIFORM_EVIDENCE_HASH_INVALID",
                "the source evidence manifest hash is invalid",
            )
        provenance = dict(self.provenance)
        required_identity_hashes = (
            "research_identity_sha256",
            "static_compiler_algorithm_sha256",
            "geometry_manifest_sha256",
            "component_manifest_sha256",
            "material_manifest_sha256",
        )
        if provenance.get("research_identity_sha256") != self.evidence_sha256:
            raise ResearchProfileUnavailable(
                "UNIFORM_IDENTITY_INCOMPLETE",
                "research result identity does not bind the exact source evidence manifest",
            )
        if any(
            len(str(provenance.get(name, ""))) != 64
            or any(
                character not in "0123456789abcdef"
                for character in str(provenance.get(name, ""))
            )
            for name in required_identity_hashes
        ):
            raise ResearchProfileUnavailable(
                "UNIFORM_IDENTITY_INCOMPLETE",
                "research result identity lacks a static/compiler/source/material/component/geometry hash",
            )
        object.__setattr__(self, "partials", tuple(self.partials))
        object.__setattr__(self, "provenance", MappingProxyType(provenance))

    def assemble(self, frequencies_hz: Sequence[float] | NDArray[np.float64]) -> UniformC00Assembly:
        """Return the frequency-local one-net replacement or fail closed."""

        try:
            assembly = assemble_uniform_effective_admittance(
                self.partials,
                frequencies_hz,
                reference_net=self.reference_net,
                selected_nets=(self.selected_net,),
                port_connectivity=self.port_connectivity,
            )
        except UniformC00Error as exc:
            raise ResearchProfileUnavailable(
                "UNIFORM_ASSEMBLY_FAILED",
                str(exc),
                details={"evidence_sha256": self.evidence_sha256},
            ) from exc
        if assembly.status != "ok" or assembly.effective_admittance_s is None:
            raise ResearchProfileUnavailable(
                "UNIFORM_ASSEMBLY_BLOCKED",
                assembly.reason or "uniform C00 assembly was blocked",
                details={"evidence_sha256": self.evidence_sha256},
            )
        return assembly


def _key(value: Any) -> str:
    result = str(value).strip().casefold()
    if not result:
        raise ResearchProfileUnavailable(
            "SOURCE_IDENTITY_MISSING", "a source layer/net identity is blank"
        )
    return result


def _canonical_json(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _layer_gap_dispersion(project: Any, upper_layer: str, lower_layer: str) -> DielectricDispersion:
    positions = {_key(layer.name): index for index, layer in enumerate(project.stackup_layers)}
    upper = positions[_key(upper_layer)]
    lower = positions[_key(lower_layer)]
    if upper > lower:
        upper, lower = lower, upper
    rows = tuple(project.stackup_layers[upper + 1 : lower])
    if not rows or any(row.is_conductor or row.dk is None for row in rows):
        raise ResearchProfileUnavailable(
            "DIELECTRIC_GAP_INCOMPLETE",
            f"{upper_layer}/{lower_layer} does not have a complete dielectric-only source gap",
        )
    knots = sorted(
        {
            float(point.frequency_hz)
            for row in rows
            for point in getattr(row, "dielectric_properties", ())
        }
    )
    if not knots:
        knots = [1.0e9]
    frequencies = np.asarray(knots, dtype=np.float64)
    total_thickness = sum(float(row.thickness_um) for row in rows)
    inverse = np.zeros(frequencies.shape, dtype=np.complex128)
    for row in rows:
        points = tuple(getattr(row, "dielectric_properties", ()))
        if points:
            row_dispersion = DielectricDispersion(
                frequencies_hz=tuple(float(point.frequency_hz) for point in points),
                relative_permittivities=tuple(float(point.dk) for point in points),
                loss_tangents=tuple(float(point.df) for point in points),
            )
            dk, df = row_dispersion.interpolate(frequencies)
        else:
            dk = np.full(frequencies.shape, float(row.dk), dtype=np.float64)
            df = np.full(frequencies.shape, float(row.df or 0.0), dtype=np.float64)
        inverse += float(row.thickness_um) / (dk * (1.0 - 1j * df))
    effective = total_thickness / inverse
    dk_values = np.asarray(effective.real, dtype=np.float64)
    df_values = np.asarray(-effective.imag / effective.real, dtype=np.float64)
    if (
        not np.all(np.isfinite(dk_values))
        or not np.all(np.isfinite(df_values))
        or np.any(dk_values <= 0.0)
        or np.any(df_values < -1.0e-12)
    ):
        raise ResearchProfileUnavailable(
            "DIELECTRIC_DISPERSION_NONPASSIVE",
            f"source Dk/Df rows for {upper_layer}/{lower_layer} do not form a passive finite series dielectric",
        )
    return DielectricDispersion(
        frequencies_hz=tuple(float(value) for value in frequencies),
        relative_permittivities=tuple(float(value) for value in dk_values),
        loss_tangents=tuple(float(max(value, 0.0)) for value in df_values),
    )


def _physical_layer_order(project: Any, rail: Any, template: Any) -> tuple[str, ...]:
    layers = list(project.stackup_layers)
    positions = {_key(layer.name): index for index, layer in enumerate(layers)}
    try:
        pwr_index = positions[_key(rail.pwr_layer)]
        gnd_index = positions[_key(rail.gnd_layer)]
    except KeyError as exc:  # pragma: no cover - ProjectSpec normally prevents this
        raise ResearchProfileUnavailable(
            "STACK_LAYER_MISSING", f"selected stack layer {exc.args[0]!r} is absent"
        ) from exc
    selected = {pwr_index, gnd_index}
    if len(template.parallel_planes) > 1:
        raise ResearchProfileUnavailable(
            "PARALLEL_REFERENCE_UNSUPPORTED",
            "more than one parallel return component cannot be represented by the scalar research bridge",
        )
    if template.parallel_planes:
        direction = -1 if gnd_index > pwr_index else 1
        candidate = pwr_index + direction
        while 0 <= candidate < len(layers) and not layers[candidate].is_conductor:
            candidate += direction
        if not (0 <= candidate < len(layers)):
            raise ResearchProfileUnavailable(
                "PARALLEL_REFERENCE_MISSING",
                "the compiled parallel return has no matching adjacent source conductor",
            )
        selected.add(candidate)
    ordered = tuple(layers[index].name for index in sorted(selected))
    conductor_positions = [positions[_key(name)] for name in ordered]
    for left, right in zip(conductor_positions, conductor_positions[1:]):
        if any(layer.is_conductor for layer in layers[left + 1 : right]):
            raise ResearchProfileUnavailable(
                "NONCONTIGUOUS_CONDUCTOR_SLAB",
                "the research profile requires a complete contiguous adjacent-conductor slab",
            )
    return ordered


def _record_index(project: Any, layer_order: Sequence[str]) -> tuple[Mapping[str, Any], ...]:
    spd_import = getattr(project, "metadata", {}).get("spd_import")
    if not isinstance(spd_import, Mapping):
        raise ResearchProfileUnavailable(
            "SPD_SOURCE_METADATA_MISSING",
            "the project has no retained SPD source manifest",
        )
    source_hash = str(spd_import.get("source_sha256", "")).lower()
    if len(source_hash) != 64 or any(character not in "0123456789abcdef" for character in source_hash):
        raise ResearchProfileUnavailable(
            "SPD_SOURCE_HASH_MISSING",
            "the retained SPD source SHA-256 is absent or invalid",
        )
    records = spd_import.get("plane_geometries")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ResearchProfileUnavailable(
            "SPD_ARTWORK_INDEX_MISSING",
            "the SPD manifest has no complete retained plane-geometry index",
        )
    wanted = {_key(item) for item in layer_order}
    selected = tuple(
        record
        for record in records
        if isinstance(record, Mapping) and _key(record.get("layer", "")) in wanted
    )
    by_layer: dict[str, set[str]] = {item: set() for item in wanted}
    for record in selected:
        layer = _key(record.get("layer"))
        net = _key(record.get("net"))
        asset = str(record.get("asset", "")).strip()
        digest = str(record.get("asset_sha256", "")).strip().lower()
        if not asset or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ResearchProfileUnavailable(
                "SPD_ARTWORK_RECORD_INVALID",
                f"retained artwork record for {layer}/{net} lacks a safe asset and SHA-256",
            )
        by_layer[layer].add(net)
    stack_by_key = {_key(layer.name): layer for layer in project.stackup_layers}
    for layer_key in wanted:
        declared = {_key(net) for net in stack_by_key[layer_key].pwr_nets}
        retained = by_layer[layer_key]
        if not declared or retained != declared:
            raise ResearchProfileUnavailable(
                "SPD_ARTWORK_LAYER_INCOMPLETE",
                f"layer {stack_by_key[layer_key].name!r} retained nets {sorted(retained)!r} do not exactly match source-declared nets {sorted(declared)!r}",
                details={"layer": stack_by_key[layer_key].name},
            )
    return selected


def _require_topology_certificate_presence(project: Any) -> None:
    """Fail on the source compiler certificate before downstream model gates."""

    spd_import = getattr(project, "metadata", {}).get("spd_import")
    certificate = (
        spd_import.get("uniform_component_connectivity_certificate")
        if isinstance(spd_import, Mapping)
        else None
    )
    if not isinstance(certificate, Mapping):
        raise ResearchProfileUnavailable(
            "TOPOLOGY_CERTIFICATE_MISSING",
            "no source compiler certificate proves that PWR and reference launches reach their respective retained conductor components",
        )


def _single_shape(artwork: Sequence[Any], *, layer: str, net: str, role: str) -> Any:
    try:
        from shapely.ops import unary_union
    except ImportError as exc:  # pragma: no cover - packaged dependency
        raise ResearchProfileUnavailable(
            "SHAPELY_UNAVAILABLE", "exact-artwork validation requires Shapely"
        ) from exc
    shapes = [
        item.geometry_um
        for item in artwork
        if _key(item.layer) == _key(layer) and _key(item.net) == _key(net)
    ]
    if not shapes:
        raise ResearchProfileUnavailable(
            "REQUIRED_ARTWORK_MISSING", f"{role} artwork {layer}/{net} is absent"
        )
    merged = unary_union(shapes)
    if merged.geom_type != "Polygon" or not merged.is_valid or merged.is_empty:
        raise ResearchProfileUnavailable(
            "DISCONNECTED_NET_ISLANDS",
            f"{role} artwork {layer}/{net} is not one source-proven connected polygon; disconnected islands are never shorted",
        )
    return merged


def _all_single_component_shapes(artwork: Sequence[Any]) -> Mapping[tuple[str, str], Any]:
    """Reject every implicit same-net island short, including floating nets."""

    grouped: dict[tuple[str, str], list[Any]] = {}
    for item in artwork:
        grouped.setdefault((_key(item.layer), _key(item.net)), []).append(
            item.geometry_um
        )
    result: dict[tuple[str, str], Any] = {}
    for (layer, net), _shapes in grouped.items():
        result[(layer, net)] = _single_shape(
            artwork, layer=layer, net=net, role="retained conductor"
        )
    return MappingProxyType(result)


def _component_manifest_hash(
    records: Sequence[Mapping[str, Any]], groups: Sequence[tuple[str, str]]
) -> str:
    wanted = {(_key(layer), _key(net)) for layer, net in groups}
    manifest = [
        {
            "layer": _key(record["layer"]),
            "net": _key(record["net"]),
            "asset_sha256": str(record["asset_sha256"]).lower(),
        }
        for record in records
        if (_key(record["layer"]), _key(record["net"])) in wanted
    ]
    manifest.sort(key=lambda item: (item["layer"], item["net"], item["asset_sha256"]))
    if not manifest or { (item["layer"], item["net"]) for item in manifest } != wanted:
        raise ResearchProfileUnavailable(
            "TOPOLOGY_COMPONENT_ASSET_MISSING",
            "a certified conductor component has no complete retained artwork asset",
        )
    return sha256(_canonical_json(manifest)).hexdigest()


def _gate_topology_certificate(
    project: Any,
    *,
    rail_id: str,
    layer_order: Sequence[str],
    selected_net: str,
    reference_net: str,
    ground_layers: Sequence[str],
    records: Sequence[Mapping[str, Any]],
    source_sha256: str,
) -> tuple[Mapping[str, Any], str]:
    """Require Phase-1-equivalent launch/component proof; XY overlap is not proof."""

    spd_import = project.metadata["spd_import"]
    certificate = spd_import.get("uniform_component_connectivity_certificate")
    if not isinstance(certificate, Mapping):
        raise ResearchProfileUnavailable(
            "TOPOLOGY_CERTIFICATE_MISSING",
            "no source compiler certificate proves that PWR and reference launches reach their respective retained conductor components",
        )
    selected_component_sha256 = _component_manifest_hash(
        records, ((next(layer for layer in layer_order if _key(layer) not in {_key(item) for item in ground_layers}), selected_net),)
    )
    reference_component_sha256 = _component_manifest_hash(
        records, tuple((layer, reference_net) for layer in ground_layers)
    )
    unresolved = certificate.get("unresolved_codes")
    expected = {
        "version": "uniform-component-connectivity-v1",
        "source_sha256": source_sha256,
        "rail_id": rail_id,
        "layer_order": [_key(item) for item in layer_order],
        "selected_net": _key(selected_net),
        "reference_net": _key(reference_net),
        "selected_component_sha256": selected_component_sha256,
        "reference_component_sha256": reference_component_sha256,
        "source_terminal_component_proven": True,
        "reference_terminal_component_proven": True,
        "unresolved_codes": [],
    }
    actual = {
        "version": str(certificate.get("version", "")),
        "source_sha256": str(certificate.get("source_sha256", "")).lower(),
        "rail_id": str(certificate.get("rail_id", "")),
        "layer_order": [_key(item) for item in certificate.get("layer_order", ())],
        "selected_net": _key(certificate.get("selected_net", "")),
        "reference_net": _key(certificate.get("reference_net", "")),
        "selected_component_sha256": str(certificate.get("selected_component_sha256", "")).lower(),
        "reference_component_sha256": str(certificate.get("reference_component_sha256", "")).lower(),
        "source_terminal_component_proven": certificate.get("source_terminal_component_proven") is True,
        "reference_terminal_component_proven": certificate.get("reference_terminal_component_proven") is True,
        "unresolved_codes": list(unresolved) if isinstance(unresolved, (list, tuple)) else ["INVALID_UNRESOLVED_LIST"],
    }
    if actual != expected:
        differing = sorted(key for key in expected if actual.get(key) != expected[key])
        raise ResearchProfileUnavailable(
            "TOPOLOGY_CERTIFICATE_INCOMPLETE",
            "the uniform connectivity certificate is stale, unresolved, or does not bind the exact rail/layers/components: "
            + ", ".join(differing),
            details={"differing_fields": tuple(differing)},
        )
    certificate_sha256 = sha256(_canonical_json(expected)).hexdigest()
    return MappingProxyType(expected), certificate_sha256


def _gate_device_landings(project: Any, rail: Any, power_shape: Any, reference_shapes: Sequence[Any]) -> None:
    try:
        from shapely.geometry import Point
    except ImportError as exc:  # pragma: no cover
        raise ResearchProfileUnavailable(
            "SHAPELY_UNAVAILABLE", "source landing validation requires Shapely"
        ) from exc
    power_pins = [
        pin
        for pin in project.pins
        if str(getattr(pin.kind, "value", pin.kind)) == "DEVICE_BUMP"
        and str(getattr(pin.terminal, "value", pin.terminal)) == "PWR"
        and _key(pin.net) == _key(rail.net)
        and (pin.domain is None or pin.domain == rail.domain)
        and (pin.site is None or pin.site == rail.site)
    ]
    ground_pins = [
        pin
        for pin in project.pins
        if str(getattr(pin.kind, "value", pin.kind)) == "DEVICE_BUMP"
        and str(getattr(pin.terminal, "value", pin.terminal)) == "GND"
        and (pin.site is None or pin.site == rail.site)
    ]
    if not power_pins or not ground_pins:
        raise ResearchProfileUnavailable(
            "DEVICE_LANDING_EVIDENCE_MISSING",
            "selected Device PWR and GND bump landings are both required",
        )
    outside_power = [pin.pin_id for pin in power_pins if not power_shape.covers(Point(pin.x_um, pin.y_um))]
    outside_ground = [
        pin.pin_id
        for pin in ground_pins
        if not any(shape.covers(Point(pin.x_um, pin.y_um)) for shape in reference_shapes)
    ]
    if outside_power or outside_ground:
        raise ResearchProfileUnavailable(
            "DEVICE_LANDING_OUTSIDE_ARTWORK",
            "Device landing coordinates are not covered by the retained selected/reference artwork",
            details={"power": tuple(outside_power), "ground": tuple(outside_ground)},
        )


def build_uniform_c00_source_model(
    project: Any,
    attachments: Mapping[str, bytes],
    rail_id: str,
    template: Any,
) -> UniformC00SourceModel:
    """Compile the only promoted research bridge from source evidence alone."""

    rails = {item.rail_id: item for item in project.rails}
    try:
        rail = rails[rail_id]
    except KeyError as exc:
        raise ResearchProfileUnavailable(
            "RAIL_UNKNOWN", f"rail {rail_id!r} is absent from the source project"
        ) from exc
    # Do not let a later pairing/material/geometry diagnostic mask the exact
    # missing import certificate.  The full certificate binding is verified
    # after the physical component identities are derived below.
    _require_topology_certificate_presence(project)
    if not bool(getattr(template, "partition_confirmed", False)):
        raise ResearchProfileUnavailable(
            "PARTITION_UNCONFIRMED",
            "the selected PWR partition must be explicitly confirmed",
        )
    if not isinstance(attachments, Mapping) or not attachments:
        raise ResearchProfileUnavailable(
            "SPD_ARTWORK_ATTACHMENTS_MISSING",
            "retained SPD geometry attachments are required",
        )

    layer_order = _physical_layer_order(project, rail, template)
    records = _record_index(project, layer_order)
    stack_by_key = {_key(layer.name): layer for layer in project.stackup_layers}
    ground_aliases = {_key(item) for item in project.gnd_aliases}
    ground_layers = [
        layer
        for layer in layer_order
        if _key(layer) != _key(rail.pwr_layer)
    ]
    if not ground_layers:
        raise ResearchProfileUnavailable(
            "REFERENCE_LAYER_MISSING", "the contiguous slab has no reference conductor"
        )
    reference_sets = [
        {_key(net) for net in stack_by_key[_key(layer)].pwr_nets}
        for layer in ground_layers
    ]
    if any(not values or not values.issubset(ground_aliases) for values in reference_sets):
        raise ResearchProfileUnavailable(
            "REFERENCE_NOT_PURE_GROUND",
            "every selected return layer must contain only configured source GND aliases",
        )
    reference_keys = set().union(*reference_sets)
    if len(reference_keys) != 1:
        raise ResearchProfileUnavailable(
            "REFERENCE_NET_AMBIGUOUS",
            "the scalar bridge requires one exact reference-net identity across all return components",
        )
    reference_key = next(iter(reference_keys))
    reference_net = next(
        net
        for layer in ground_layers
        for net in stack_by_key[_key(layer)].pwr_nets
        if _key(net) == reference_key
    )
    source_hash = str(project.metadata["spd_import"]["source_sha256"]).lower()

    # Topology evidence is the first source-specific safety gate after the
    # minimum stack/artwork identities are known.  In particular, a named SPD
    # without the compiler certificate must report that exact defect even if a
    # later Device-pairing gate would also fail.
    _topology_certificate, topology_certificate_sha256 = _gate_topology_certificate(
        project,
        rail_id=rail_id,
        layer_order=layer_order,
        selected_net=rail.net,
        reference_net=reference_net,
        ground_layers=ground_layers,
        records=records,
        source_sha256=source_hash,
    )

    if getattr(rail, "mixed_reference_certificate", None) is not None:
        raise ResearchProfileUnavailable(
            "MIXED_REFERENCE_UNSUPPORTED",
            "uniform C00 requires a pure source-proven reference, not a mixed-reference rectangular certificate",
        )

    if len(ground_layers) > 1:
        tie = project.metadata["spd_import"].get("uniform_reference_component_tie")
        expected_layers = sorted(_key(item) for item in ground_layers)
        if not (
            isinstance(tie, Mapping)
            and tie.get("connected") is True
            and str(tie.get("source_sha256", "")).lower() == source_hash
            and sorted(_key(item) for item in tie.get("layers", ())) == expected_layers
            and _key(tie.get("reference_net", "")) == reference_key
        ):
            raise ResearchProfileUnavailable(
                "PARALLEL_REFERENCE_TIE_UNPROVEN",
                "multiple return components require an explicit source-hash-bound common-reference tie certificate",
            )
    if not bool(getattr(template, "pairing_confident", False)):
        raise ResearchProfileUnavailable(
            "DEVICE_PAIRING_UNCONFIRMED",
            "Device PWR/GND pairing must be source-derived and explicitly confirmed",
        )

    try:
        model = capacitance_model_from_project(project, attachments, layer_order)
        _all_single_component_shapes(model.artwork)
        result = extract_multilayer_bulk_capacitance(
            model,
            reference_net=reference_net,
            selected_nets=(rail.net,),
        )
    except ResearchProfileUnavailable:
        raise
    except (MultilayerCapacitanceError, KeyError, ValueError) as exc:
        raise ResearchProfileUnavailable(
            "ARTWORK_CAPACITANCE_EXTRACTION_FAILED", str(exc)
        ) from exc
    if result.selected_capacitance_f is None or result.selected_capacitance_f.shape != (1, 1):
        raise ResearchProfileUnavailable(
            "SCALAR_REDUCTION_UNAVAILABLE",
            "exact artwork did not produce one selected-net scalar uniform port",
        )
    selected_capacitance = float(result.selected_capacitance_f[0, 0])
    if not isfinite(selected_capacitance) or selected_capacitance <= 0.0:
        raise ResearchProfileUnavailable(
            "SCALAR_CAPACITANCE_NONPASSIVE",
            "the selected exact-artwork uniform capacitance is not finite and positive",
        )

    power_shape = _single_shape(
        model.artwork, layer=rail.pwr_layer, net=rail.net, role="selected PWR"
    )
    reference_shapes = tuple(
        _single_shape(model.artwork, layer=layer, net=reference_net, role="reference")
        for layer in ground_layers
    )
    _gate_device_landings(project, rail, power_shape, reference_shapes)

    partials = tuple(
        DispersiveAdjacentGap(
            partial=partial,
            dispersion=_layer_gap_dispersion(
                project, partial.upper_layer, partial.lower_layer
            ),
        )
        for partial in result.adjacent_gap_partials
    )
    geometry_manifest_sha256 = _geometry_manifest_hash(records)
    component_manifest_sha256 = _component_manifest_hash(
        records,
        (
            (rail.pwr_layer, rail.net),
            *((layer, reference_net) for layer in ground_layers),
        ),
    )
    material_manifest_sha256 = _material_manifest_hash(project, layer_order)
    static_compiler_algorithm_sha256 = solver_profile_static_identity_sha256(
        "research_uniform_admittance"
    )
    manifest = {
        "compiler_version": PROFILE_COMPILER_VERSION,
        "static_compiler_algorithm_sha256": static_compiler_algorithm_sha256,
        "source_sha256": source_hash,
        "rail_id": rail_id,
        "selected_net": rail.net,
        "reference_net": reference_net,
        "layer_order": list(layer_order),
        "geometry_manifest_sha256": geometry_manifest_sha256,
        "component_manifest_sha256": component_manifest_sha256,
        "material_manifest_sha256": material_manifest_sha256,
        "selected_capacitance_f": selected_capacitance,
        "partition_confirmed": True,
        "device_pairing_confirmed": True,
        "topology_certificate_sha256": topology_certificate_sha256,
    }
    evidence_sha256 = sha256(_canonical_json(manifest)).hexdigest()
    provenance = {
        "profile_key": "research_uniform_admittance",
        "profile_badge": "RESEARCH",
        "status": "source_only_research",
        "source_only": True,
        "powersi_used_for_parameters": False,
        "validation_status": "research_not_validated",
        "source_sha256": source_hash,
        "artwork_evidence_sha256": evidence_sha256,
        "research_identity_sha256": evidence_sha256,
        "static_compiler_algorithm_sha256": static_compiler_algorithm_sha256,
        "geometry_manifest_sha256": geometry_manifest_sha256,
        "component_manifest_sha256": component_manifest_sha256,
        "material_manifest_sha256": material_manifest_sha256,
        "topology_certificate_sha256": topology_certificate_sha256,
        "reference_net": reference_net,
        "selected_net": rail.net,
        "layer_order": list(layer_order),
        "nominal_selected_c00_f": selected_capacitance,
        "gate_summary": (
            "complete contiguous artwork; every retained net is one component; "
            "pure reference; certificate-proven PWR/reference launch connectivity; "
            "confirmed partition and Device pairing"
        ),
    }
    return UniformC00SourceModel(
        partials=partials,
        reference_net=reference_net,
        selected_net=rail.net,
        port_connectivity=UniformPortConnectivityEvidence(
            reference_net=reference_net,
            source_net=rail.net,
            source_terminal_component_proven=True,
            reference_terminal_component_proven=True,
            evidence=(
                f"{PROFILE_COMPILER_VERSION}; source={source_hash}; "
                f"artwork={evidence_sha256}; topology={topology_certificate_sha256}"
            ),
        ),
        evidence_sha256=evidence_sha256,
        provenance=provenance,
    )


__all__ = [
    "PROFILE_COMPILER_VERSION",
    "ResearchProfileUnavailable",
    "UniformC00SourceModel",
    "build_uniform_c00_source_model",
]
