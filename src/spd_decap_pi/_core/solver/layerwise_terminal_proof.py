"""Hash-bound source-topology proof for layerwise Device terminals.

The production v2 proof binds every compiled branch anchor to its exact raw
SPD source node and to the same-NET Trace/Via connected component recovered by
the importer.  A terminal is accepted only when that component contacts exact
retained artwork on a surface that the compiled layer network places on the
requested ideal node.  This permits a real trace-first escape route while
forbidding coordinate projection from TOP onto unrelated deep artwork.

The proof deliberately knows nothing about the rectangular modal envelope and
is kept separate from the layerwise network compiler so it can be audited as a
fail-closed gate before ``UniformPortConnectivityEvidence`` is constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isclose, isfinite
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from .. import services


PROOF_SCHEMA_VERSION = "layerwise-terminal-surface-contact-proof-v2"
_BRANCH_ANCHOR_TOLERANCE_UM = 1.0e-9


def _key(value: Any) -> str:
    return str(value).strip().casefold()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _terminal_value(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().upper()


def _kind_value(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().upper()


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class SourcePadFootprintEvidence:
    """One source-proven rectangular pad footprint, centred on a terminal.

    This is not a caller-selected numerical tolerance. ``source_identity``
    must identify the retained source record that supplied the dimensions.
    Rotation is applied about the terminal coordinate.
    """

    width_um: float
    height_um: float
    source_identity: str
    source_sha256: str
    layer: str | None = None
    rotation_degrees: float = 0.0

    def __post_init__(self) -> None:
        if (
            not isfinite(float(self.width_um))
            or not isfinite(float(self.height_um))
            or float(self.width_um) <= 0.0
            or float(self.height_um) <= 0.0
        ):
            raise ValueError("source pad dimensions must be finite and > 0")
        if not isfinite(float(self.rotation_degrees)):
            raise ValueError("source pad rotation must be finite")
        if not self.source_identity.strip():
            raise ValueError("source pad identity must not be blank")
        if not _is_sha256(self.source_sha256.strip().lower()):
            raise ValueError("source pad evidence must bind a valid source SHA-256")
        if self.layer is not None and not self.layer.strip():
            raise ValueError("source pad layer must not be blank")


@dataclass(frozen=True, slots=True)
class LayerwiseTerminalProofDiagnostic:
    """One deterministic fail-closed diagnostic."""

    code: str
    message: str
    pin_id: str | None = None
    branch_id: str | None = None
    layer: str | None = None
    net: str | None = None
    asset: str | None = None

    def manifest_value(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "pin_id": self.pin_id,
            "branch_id": self.branch_id,
            "layer": self.layer,
            "net": self.net,
            "asset": self.asset,
        }


@dataclass(frozen=True, slots=True)
class LayerwiseTerminalArtworkProof:
    """Immutable proof result suitable for a layerwise compiler gate."""

    status: Literal["proven", "blocked_fail_closed"]
    source_terminal_artwork_proven: bool
    reference_terminal_artwork_proven: bool
    evidence_sha256: str
    manifest: Mapping[str, Any]
    diagnostics: tuple[LayerwiseTerminalProofDiagnostic, ...]

    @property
    def ready(self) -> bool:
        return (
            self.status == "proven"
            and self.source_terminal_artwork_proven
            and self.reference_terminal_artwork_proven
            and not self.diagnostics
        )

    def manifest_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible copy for provenance embedding."""

        return _thaw(self.manifest)


@dataclass(frozen=True, slots=True)
class _ArtworkRecord:
    layer: str
    net: str
    asset: str
    expected_sha256: str
    actual_sha256: str | None
    shape: Any | None
    decoded: bool

    def manifest_value(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "net": self.net,
            "asset": self.asset,
            "asset_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "decoded": self.decoded,
        }


def _diagnostic_sort_key(item: LayerwiseTerminalProofDiagnostic) -> tuple[str, ...]:
    return tuple(
        _key(value)
        for value in (
            item.code,
            item.branch_id or "",
            item.pin_id or "",
            item.layer or "",
            item.net or "",
            item.asset or "",
            item.message,
        )
    )


def _pin_manifest(pin: Any) -> dict[str, Any]:
    def coordinate(name: str) -> float | None:
        try:
            value = float(getattr(pin, name))
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
        return value if isfinite(value) else None

    return {
        "pin_id": str(getattr(pin, "pin_id", "")),
        "refdes": str(getattr(pin, "refdes", "")),
        "pin": str(getattr(pin, "pin", "")),
        "net": str(getattr(pin, "net", "")),
        "x_um": coordinate("x_um"),
        "y_um": coordinate("y_um"),
        "kind": _kind_value(getattr(pin, "kind", "")),
        "terminal": _terminal_value(getattr(pin, "terminal", "")),
        "domain": (
            None if getattr(pin, "domain", None) is None else str(getattr(pin, "domain"))
        ),
        "site": None if getattr(pin, "site", None) is None else str(getattr(pin, "site")),
        "bump_group": (
            None
            if getattr(pin, "bump_group", None) is None
            else str(getattr(pin, "bump_group"))
        ),
        "via_template_id": (
            None
            if getattr(pin, "via_template_id", None) is None
            else str(getattr(pin, "via_template_id"))
        ),
    }


def _same_pin(left: Any, right: Any) -> bool:
    try:
        return _pin_manifest(left) == _pin_manifest(right)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def _embedded_pad_footprint(
    pin: Any, source_sha256: str
) -> SourcePadFootprintEvidence | None:
    """Read only explicit landing geometry carried by the terminal itself."""

    width = getattr(pin, "landing_pad_width_um", None)
    height = getattr(pin, "landing_pad_height_um", None)
    if width is None and height is None:
        return None
    if width is None or height is None:
        raise ValueError("embedded source landing geometry is incomplete")
    return SourcePadFootprintEvidence(
        width_um=float(width),
        height_um=float(height),
        source_identity=str(
            getattr(pin, "terminal_provenance", None)
            or f"{getattr(pin, 'pin_id', '')}:embedded-landing-geometry"
        ),
        source_sha256=source_sha256,
        layer=(
            None
            if getattr(pin, "landing_layer", None) is None
            else str(getattr(pin, "landing_layer"))
        ),
        rotation_degrees=float(getattr(pin, "landing_rotation_degrees", 0.0)),
    )


def _source_pad_shape(
    x_um: float, y_um: float, footprint: SourcePadFootprintEvidence
) -> Any:
    from shapely.affinity import rotate
    from shapely.geometry import box

    shape = box(
        x_um - footprint.width_um / 2.0,
        y_um - footprint.height_um / 2.0,
        x_um + footprint.width_um / 2.0,
        y_um + footprint.height_um / 2.0,
    )
    if footprint.rotation_degrees:
        shape = rotate(
            shape,
            footprint.rotation_degrees,
            origin=(x_um, y_um),
            use_radians=False,
        )
    return shape


def _load_artwork(
    project: Any,
    attachments: Mapping[str, bytes],
    rail: Any,
    diagnostics: list[LayerwiseTerminalProofDiagnostic],
) -> tuple[list[_ArtworkRecord], tuple[str, ...], str]:
    metadata = getattr(project, "metadata", {})
    spd_import = metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    source_sha256 = (
        str(spd_import.get("source_sha256", "")).strip().lower()
        if isinstance(spd_import, Mapping)
        else ""
    )
    if not _is_sha256(source_sha256):
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "SOURCE_IDENTITY_MISSING",
                "the retained SPD import has no valid source SHA-256",
            )
        )
    records = spd_import.get("plane_geometries") if isinstance(spd_import, Mapping) else None
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "ARTWORK_INDEX_MISSING",
                "the project has no retained SPD plane-geometry index",
            )
        )
        return [], (), source_sha256

    ground_aliases = tuple(
        sorted(
            {_key(alias) for alias in getattr(project, "gnd_aliases", ()) if _key(alias)}
        )
    )
    if not ground_aliases:
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "GND_ALIAS_MISSING", "the project has no configured GND aliases"
            )
        )
    pwr_layer_key = _key(getattr(rail, "pwr_layer", ""))
    gnd_layer_key = _key(getattr(rail, "gnd_layer", ""))
    pwr_net_key = _key(getattr(rail, "net", ""))
    selected: list[Mapping[str, Any]] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        layer_key = _key(raw.get("layer", ""))
        net_key = _key(raw.get("net", ""))
        if (layer_key == pwr_layer_key and net_key == pwr_net_key) or (
            layer_key == gnd_layer_key and net_key in ground_aliases
        ):
            selected.append(raw)
    selected.sort(
        key=lambda raw: (
            _key(raw.get("layer", "")),
            _key(raw.get("net", "")),
            _key(raw.get("asset", "")),
            _key(raw.get("asset_sha256", "")),
        )
    )
    seen: set[tuple[str, str, str, str]] = set()
    result: list[_ArtworkRecord] = []
    for raw in selected:
        layer = str(raw.get("layer", "")).strip()
        net = str(raw.get("net", "")).strip()
        asset = str(raw.get("asset", "")).strip()
        digest = str(raw.get("asset_sha256", "")).strip().lower()
        identity = (_key(layer), _key(net), _key(asset), digest)
        if identity in seen:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "ARTWORK_RECORD_DUPLICATE",
                    f"duplicate retained artwork record {layer}/{net}/{asset}",
                    layer=layer or None,
                    net=net or None,
                    asset=asset or None,
                )
            )
            continue
        seen.add(identity)
        content = attachments.get(asset) if isinstance(attachments, Mapping) else None
        actual_digest = (
            sha256(content).hexdigest() if isinstance(content, (bytes, bytearray)) else None
        )
        shape = None
        decoded = False
        if not layer or not net or not asset or not _is_sha256(digest):
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "ARTWORK_RECORD_INVALID",
                    "a required retained artwork record has an invalid layer, net, asset, or SHA-256",
                    layer=layer or None,
                    net=net or None,
                    asset=asset or None,
                )
            )
        elif not isinstance(content, (bytes, bytearray)):
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "ARTWORK_ATTACHMENT_MISSING",
                    f"retained artwork attachment {asset!r} is missing",
                    layer=layer,
                    net=net,
                    asset=asset,
                )
            )
        elif actual_digest != digest:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "ARTWORK_ASSET_INTEGRITY_FAILED",
                    f"retained artwork attachment {asset!r} fails SHA-256",
                    layer=layer,
                    net=net,
                    asset=asset,
                )
            )
        else:
            try:
                payload = services._decode_spd_geometry_asset(digest, bytes(content))
                services._validate_spd_geometry_payload(
                    payload, expected_layer=layer, expected_net=net
                )
                shape = services._ordered_spd_geometry(payload)
            except (KeyError, TypeError, ValueError) as exc:
                diagnostics.append(
                    LayerwiseTerminalProofDiagnostic(
                        "ARTWORK_GEOMETRY_INVALID",
                        f"retained artwork attachment {asset!r} cannot be decoded: {exc}",
                        layer=layer,
                        net=net,
                        asset=asset,
                    )
                )
            if shape is None:
                if not any(
                    item.code == "ARTWORK_GEOMETRY_INVALID" and item.asset == asset
                    for item in diagnostics
                ):
                    diagnostics.append(
                        LayerwiseTerminalProofDiagnostic(
                            "ARTWORK_GEOMETRY_INVALID",
                            f"retained artwork attachment {asset!r} does not form valid ordered copper",
                            layer=layer,
                            net=net,
                            asset=asset,
                        )
                    )
            else:
                decoded = True
        result.append(
            _ArtworkRecord(
                layer,
                net,
                asset,
                digest,
                actual_digest,
                shape,
                decoded,
            )
        )

    if not any(
        _key(item.layer) == pwr_layer_key
        and _key(item.net) == pwr_net_key
        and item.decoded
        for item in result
    ):
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "REQUIRED_PWR_ARTWORK_MISSING",
                f"no valid retained artwork exists for {rail.pwr_layer}/{rail.net}",
                layer=str(getattr(rail, "pwr_layer", "")),
                net=str(getattr(rail, "net", "")),
            )
        )
    if not any(
        _key(item.layer) == gnd_layer_key
        and _key(item.net) in ground_aliases
        and item.decoded
        for item in result
    ):
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "REQUIRED_GND_ARTWORK_MISSING",
                f"no valid configured-GND artwork exists on {rail.gnd_layer}",
                layer=str(getattr(rail, "gnd_layer", "")),
            )
        )
    return result, ground_aliases, source_sha256


def _landing_match(
    *,
    pin: Any,
    role: Literal["PWR", "GND"],
    rail: Any,
    ground_aliases: Sequence[str],
    artwork: Sequence[_ArtworkRecord],
    footprint: SourcePadFootprintEvidence | None,
) -> tuple[dict[str, Any], LayerwiseTerminalProofDiagnostic | None]:
    pin_id = str(getattr(pin, "pin_id", ""))
    x_um = float(getattr(pin, "x_um"))
    y_um = float(getattr(pin, "y_um"))
    layer = str(rail.pwr_layer if role == "PWR" else rail.gnd_layer)
    target_net = str(rail.net if role == "PWR" else getattr(pin, "net", ""))
    layer_key = _key(layer)
    net_keys = (
        {_key(rail.net)} if role == "PWR" else set(ground_aliases)
    )
    candidates = [
        item
        for item in artwork
        if item.decoded
        and _key(item.layer) == layer_key
        and _key(item.net) in net_keys
        and item.shape is not None
    ]
    base_landing = {
        "pin_id": pin_id,
        "role": role,
        "x_um": x_um if isfinite(x_um) else None,
        "y_um": y_um if isfinite(y_um) else None,
        "target_layer": layer,
        "pin_net": str(getattr(pin, "net", "")),
        "matched": False,
        "method": None,
        "matched_artwork": [],
        "source_pad_footprint": None,
        "pad_overlap_area_um2": 0.0,
    }
    if not isfinite(x_um) or not isfinite(y_um):
        return base_landing, LayerwiseTerminalProofDiagnostic(
            "DEVICE_PIN_COORDINATE_INVALID",
            f"Device pin {pin_id!r} has a non-finite coordinate",
            pin_id=pin_id,
            layer=layer,
            net=target_net,
        )
    try:
        from shapely.geometry import Point
    except ImportError:
        return base_landing, LayerwiseTerminalProofDiagnostic(
            "SHAPELY_UNAVAILABLE",
            "exact terminal artwork proof requires Shapely",
            pin_id=pin_id,
            layer=layer,
            net=target_net,
        )
    point = Point(x_um, y_um)
    try:
        point_matches = [item for item in candidates if bool(item.shape.covers(point))]
    except Exception as exc:
        return base_landing, LayerwiseTerminalProofDiagnostic(
            "ARTWORK_GEOMETRY_QUERY_FAILED",
            f"exact artwork query failed for terminal {pin_id!r}: {exc}",
            pin_id=pin_id,
            layer=layer,
            net=target_net,
        )
    method = "point_covers" if point_matches else None
    pad_overlap_area_um2 = 0.0
    pad_matches: list[_ArtworkRecord] = []
    footprint_manifest: dict[str, Any] | None = None
    if footprint is not None:
        footprint_manifest = {
            "width_um": float(footprint.width_um),
            "height_um": float(footprint.height_um),
            "rotation_degrees": float(footprint.rotation_degrees),
            "layer": footprint.layer,
            "source_identity": footprint.source_identity,
            "source_sha256": footprint.source_sha256,
        }
        if footprint.layer is not None and _key(footprint.layer) != layer_key:
            diagnostic = LayerwiseTerminalProofDiagnostic(
                "SOURCE_PAD_LAYER_MISMATCH",
                f"source pad evidence for {pin_id!r} names {footprint.layer!r}, not target layer {layer!r}",
                pin_id=pin_id,
                layer=layer,
                net=target_net,
            )
            landing = {
                "pin_id": pin_id,
                "role": role,
                "x_um": x_um,
                "y_um": y_um,
                "target_layer": layer,
                "pin_net": str(getattr(pin, "net", "")),
                "matched": False,
                "method": None,
                "matched_artwork": [],
                "source_pad_footprint": footprint_manifest,
                "pad_overlap_area_um2": 0.0,
            }
            return landing, diagnostic
        if not point_matches:
            pad_shape = _source_pad_shape(x_um, y_um, footprint)
            try:
                for item in candidates:
                    area = float(item.shape.intersection(pad_shape).area)
                    if area > 0.0:
                        pad_matches.append(item)
                        pad_overlap_area_um2 += area
            except Exception as exc:
                return base_landing, LayerwiseTerminalProofDiagnostic(
                    "ARTWORK_GEOMETRY_QUERY_FAILED",
                    f"source-pad artwork query failed for terminal {pin_id!r}: {exc}",
                    pin_id=pin_id,
                    layer=layer,
                    net=target_net,
                )
            if pad_matches:
                method = "source_pad_positive_area_overlap"
    matches = point_matches or pad_matches
    landing = {
        "pin_id": pin_id,
        "role": role,
        "x_um": x_um,
        "y_um": y_um,
        "target_layer": layer,
        "pin_net": str(getattr(pin, "net", "")),
        "matched": bool(matches),
        "method": method,
        "matched_artwork": [
            {
                "layer": item.layer,
                "net": item.net,
                "asset": item.asset,
                "asset_sha256": item.expected_sha256,
            }
            for item in sorted(
                matches,
                key=lambda item: (
                    _key(item.layer),
                    _key(item.net),
                    _key(item.asset),
                    item.expected_sha256,
                ),
            )
        ],
        "source_pad_footprint": footprint_manifest,
        "pad_overlap_area_um2": float(f"{pad_overlap_area_um2:.12g}"),
    }
    if matches:
        return landing, None
    diagnostic = LayerwiseTerminalProofDiagnostic(
        "TERMINAL_OUTSIDE_EXACT_ARTWORK",
        (
            f"{role} terminal {pin_id!r} at ({x_um:.12g}, {y_um:.12g}) um is not "
            f"covered by retained {layer!r} artwork; no modal envelope or bounding-box expansion is allowed"
        ),
        pin_id=pin_id,
        layer=layer,
        net=target_net,
    )
    return landing, diagnostic


def prove_layerwise_terminal_landings(
    project: Any,
    attachments: Mapping[str, bytes],
    rail: Any,
    reference_net: str,
    selected_port: Any,
    network: Any,
    device_pins: Sequence[Any],
    device_branches: Sequence[Any],
    *,
    origin_um: tuple[float, float],
    source_pad_footprints: Mapping[str, SourcePadFootprintEvidence] | None = None,
) -> LayerwiseTerminalArtworkProof:
    """Prove Device anchors reach the selected layer-surface port.

    The v2 implementation is isolated in a small module so the former exact
    point-on-deep-artwork helpers remain readable for legacy audit history.
    Production never projects a TOP Device coordinate onto a deep plane.
    """

    from .layerwise_terminal_contact_proof import prove_terminal_surface_contacts

    return prove_terminal_surface_contacts(
        project,
        attachments,
        rail,
        reference_net,
        selected_port,
        network,
        device_pins,
        device_branches,
        origin_um=origin_um,
        source_pad_footprints=source_pad_footprints,
    )

    """Legacy v1 implementation retained below for audit-only source history.

    ``device_pins`` must be precisely the pins referenced by the supplied
    ``DeviceBranch.branch_id`` values.  Branch finite-port coordinates are
    checked against their source PWR anchor, but finite-port dimensions never
    relax artwork containment.  ``source_pad_footprints`` is optional, explicit
    source evidence keyed by ``pin_id``; an arbitrary numeric tolerance is not
    accepted by this API.
    """

    diagnostics: list[LayerwiseTerminalProofDiagnostic] = []
    try:
        origin_x_um, origin_y_um = map(float, origin_um)
    except (TypeError, ValueError, OverflowError):
        origin_x_um, origin_y_um = 0.0, 0.0
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "TERMINAL_ORIGIN_INVALID", "terminal envelope origin must be two finite coordinates"
            )
        )
    if not isfinite(origin_x_um) or not isfinite(origin_y_um):
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "TERMINAL_ORIGIN_INVALID", "terminal envelope origin must be two finite coordinates"
            )
        )
        origin_x_um, origin_y_um = 0.0, 0.0

    project_rails = {
        _key(getattr(item, "rail_id", "")): item for item in getattr(project, "rails", ())
    }
    rail_id = str(getattr(rail, "rail_id", ""))
    project_rail = project_rails.get(_key(rail_id))
    if project_rail is None:
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "RAIL_NOT_IN_PROJECT", f"rail {rail_id!r} is absent from the project"
            )
        )
    elif any(
        _key(getattr(project_rail, field, "")) != _key(getattr(rail, field, ""))
        for field in ("net", "pwr_layer", "gnd_layer")
    ):
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "RAIL_IDENTITY_MISMATCH",
                f"rail {rail_id!r} does not match the project's net/layer identity",
            )
        )

    artwork, ground_aliases, source_sha256 = _load_artwork(
        project, attachments, rail, diagnostics
    )
    project_pin_by_key: dict[str, Any] = {}
    for pin in getattr(project, "pins", ()):
        pin_id = str(getattr(pin, "pin_id", ""))
        key = _key(pin_id)
        if not key or key in project_pin_by_key:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "PROJECT_PIN_IDENTITY_AMBIGUOUS",
                    f"project Device pin identity {pin_id!r} is blank or duplicated",
                    pin_id=pin_id or None,
                )
            )
        else:
            project_pin_by_key[key] = pin

    supplied_pin_by_key: dict[str, Any] = {}
    for pin in device_pins:
        pin_id = str(getattr(pin, "pin_id", ""))
        key = _key(pin_id)
        try:
            x_um = float(getattr(pin, "x_um"))
            y_um = float(getattr(pin, "y_um"))
        except (AttributeError, TypeError, ValueError, OverflowError):
            x_um = y_um = float("nan")
        if not key or key in supplied_pin_by_key:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_PIN_IDENTITY_AMBIGUOUS",
                    f"supplied Device pin identity {pin_id!r} is blank or duplicated",
                    pin_id=pin_id or None,
                )
            )
            continue
        if not isfinite(x_um) or not isfinite(y_um):
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_PIN_COORDINATE_INVALID",
                    f"Device pin {pin_id!r} has a non-finite coordinate",
                    pin_id=pin_id,
                )
            )
        project_pin = project_pin_by_key.get(key)
        if project_pin is None:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_PIN_NOT_IN_PROJECT",
                    f"Device pin {pin_id!r} is absent from the project",
                    pin_id=pin_id,
                )
            )
        elif not _same_pin(pin, project_pin):
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_PIN_SOURCE_MISMATCH",
                    f"Device pin {pin_id!r} differs from the project source record",
                    pin_id=pin_id,
                )
            )
        supplied_pin_by_key[key] = pin
    if not supplied_pin_by_key:
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "DEVICE_PIN_EVIDENCE_MISSING", "no relevant Device pins were supplied"
            )
        )

    explicit_footprints: dict[str, SourcePadFootprintEvidence] = {}
    if source_pad_footprints is not None and not isinstance(
        source_pad_footprints, Mapping
    ):
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "SOURCE_PAD_EVIDENCE_INVALID",
                "source pad evidence must be a mapping keyed by Device pin ID",
            )
        )
        footprint_items: Sequence[tuple[Any, Any]] = ()
    else:
        footprint_items = tuple((source_pad_footprints or {}).items())
    for raw_id, footprint in footprint_items:
        key = _key(raw_id)
        if key not in supplied_pin_by_key:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "SOURCE_PAD_PIN_UNKNOWN",
                    f"source pad evidence names unused Device pin {raw_id!r}",
                    pin_id=str(raw_id),
                )
            )
        elif not isinstance(footprint, SourcePadFootprintEvidence):
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "SOURCE_PAD_EVIDENCE_INVALID",
                    f"source pad evidence for {raw_id!r} has an unsupported type",
                    pin_id=str(raw_id),
                )
            )
        elif footprint.source_sha256.casefold() != source_sha256.casefold():
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "SOURCE_PAD_SOURCE_MISMATCH",
                    f"source pad evidence for {raw_id!r} is not bound to the imported SPD SHA-256",
                    pin_id=str(raw_id),
                )
            )
        else:
            explicit_footprints[key] = footprint

    used_pin_keys: set[str] = set()
    used_once: set[str] = set()
    branch_manifest: list[dict[str, Any]] = []
    branch_ids: set[str] = set()
    for branch in sorted(device_branches, key=lambda item: _key(getattr(item, "branch_id", ""))):
        branch_id = str(getattr(branch, "branch_id", "")).strip()
        branch_key = _key(branch_id)
        if not branch_key or branch_key in branch_ids:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_BRANCH_IDENTITY_AMBIGUOUS",
                    f"Device branch identity {branch_id!r} is blank or duplicated",
                    branch_id=branch_id or None,
                )
            )
            continue
        branch_ids.add(branch_key)
        tokens = {_key(token) for token in branch_id.split("|") if _key(token)}
        project_member_keys = sorted(tokens & set(project_pin_by_key))
        missing_keys = [key for key in project_member_keys if key not in supplied_pin_by_key]
        if missing_keys:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_BRANCH_PIN_EVIDENCE_MISSING",
                    f"Device branch {branch_id!r} references project pins omitted from the supplied evidence",
                    branch_id=branch_id,
                )
            )
        member_keys = [key for key in project_member_keys if key in supplied_pin_by_key]
        members = [supplied_pin_by_key[key] for key in member_keys]
        used_pin_keys.update(member_keys)
        repeated = sorted(set(member_keys) & used_once)
        if repeated:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_PIN_MULTIPLE_BRANCHES",
                    f"Device branch {branch_id!r} reuses a terminal already owned by another branch",
                    branch_id=branch_id,
                )
            )
        used_once.update(member_keys)
        pwr_members = [
            pin
            for pin in members
            if _kind_value(getattr(pin, "kind", "")) == "DEVICE_BUMP"
            and _terminal_value(getattr(pin, "terminal", "")) == "PWR"
            and _key(getattr(pin, "net", "")) == _key(getattr(rail, "net", ""))
        ]
        gnd_members = [
            pin
            for pin in members
            if _kind_value(getattr(pin, "kind", "")) == "DEVICE_BUMP"
            and _terminal_value(getattr(pin, "terminal", "")) == "GND"
            and _key(getattr(pin, "net", "")) in set(ground_aliases)
        ]
        if not pwr_members or not gnd_members or len(members) != len(pwr_members) + len(gnd_members):
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_BRANCH_MEMBERSHIP_INVALID",
                    f"Device branch {branch_id!r} is not bound only to selected-PWR and configured-GND Device bumps",
                    branch_id=branch_id,
                )
            )
        port = getattr(branch, "port", None)
        try:
            port_x_um = origin_x_um + float(port.x_m) * 1.0e6
            port_y_um = origin_y_um + float(port.y_m) * 1.0e6
            port_width_um = float(port.width_m) * 1.0e6
            port_height_um = float(port.height_m) * 1.0e6
            port_valid = all(
                isfinite(value)
                for value in (port_x_um, port_y_um, port_width_um, port_height_um)
            ) and port_width_um > 0.0 and port_height_um > 0.0
        except (AttributeError, TypeError, ValueError, OverflowError):
            port_x_um = port_y_um = port_width_um = port_height_um = float("nan")
            port_valid = False
        anchor = next(
            (
                pin
                for pin in pwr_members
                if isclose(
                    port_x_um,
                    float(pin.x_um),
                    rel_tol=1.0e-12,
                    abs_tol=_BRANCH_ANCHOR_TOLERANCE_UM,
                )
                and isclose(
                    port_y_um,
                    float(pin.y_um),
                    rel_tol=1.0e-12,
                    abs_tol=_BRANCH_ANCHOR_TOLERANCE_UM,
                )
            ),
            None,
        ) if port_valid else None
        if not port_valid:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_BRANCH_PORT_INVALID",
                    f"Device branch {branch_id!r} has an invalid finite port",
                    branch_id=branch_id,
                )
            )
        elif anchor is None:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "DEVICE_BRANCH_ANCHOR_MISMATCH",
                    f"Device branch {branch_id!r} finite-port centre does not match a source PWR member coordinate",
                    branch_id=branch_id,
                )
            )
        branch_manifest.append(
            {
                "branch_id": branch_id,
                "member_pin_ids": sorted(
                    (str(pin.pin_id) for pin in members), key=str.casefold
                ),
                "pwr_anchor_pin_id": None if anchor is None else str(anchor.pin_id),
                "port": {
                    "port_id": str(getattr(port, "port_id", "")),
                    "x_um": port_x_um if isfinite(port_x_um) else None,
                    "y_um": port_y_um if isfinite(port_y_um) else None,
                    "width_um": port_width_um if isfinite(port_width_um) else None,
                    "height_um": (
                        port_height_um if isfinite(port_height_um) else None
                    ),
                    "geometry_tolerance_allowed": False,
                },
            }
        )
    if not branch_manifest:
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "DEVICE_BRANCH_EVIDENCE_MISSING", "no relevant Device branches were supplied"
            )
        )
    unused = sorted(set(supplied_pin_by_key) - used_pin_keys)
    for key in unused:
        pin_id = str(supplied_pin_by_key[key].pin_id)
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "DEVICE_PIN_UNUSED",
                f"supplied Device pin {pin_id!r} is not referenced by a Device branch",
                pin_id=pin_id,
            )
        )

    landings: list[dict[str, Any]] = []
    pwr_landing_count = 0
    gnd_landing_count = 0
    pwr_matches = 0
    gnd_matches = 0
    for key in sorted(used_pin_keys):
        pin = supplied_pin_by_key[key]
        terminal = _terminal_value(getattr(pin, "terminal", ""))
        pin_net_key = _key(getattr(pin, "net", ""))
        role: Literal["PWR", "GND"] | None = None
        if terminal == "PWR" and pin_net_key == _key(getattr(rail, "net", "")):
            role = "PWR"
            pwr_landing_count += 1
        elif terminal == "GND" and pin_net_key in set(ground_aliases):
            role = "GND"
            gnd_landing_count += 1
        if role is None:
            continue
        footprint = explicit_footprints.get(key)
        try:
            embedded = _embedded_pad_footprint(pin, source_sha256)
        except (TypeError, ValueError, OverflowError) as exc:
            embedded = None
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "SOURCE_PAD_EVIDENCE_INVALID",
                    f"embedded source pad evidence for {pin.pin_id!r} is invalid: {exc}",
                    pin_id=str(pin.pin_id),
                )
            )
        if footprint is not None and embedded is not None and footprint != embedded:
            diagnostics.append(
                LayerwiseTerminalProofDiagnostic(
                    "SOURCE_PAD_EVIDENCE_CONFLICT",
                    f"two different source pad footprints were supplied for {pin.pin_id!r}",
                    pin_id=str(pin.pin_id),
                )
            )
            footprint = None
        elif footprint is None:
            footprint = embedded
        landing, error = _landing_match(
            pin=pin,
            role=role,
            rail=rail,
            ground_aliases=ground_aliases,
            artwork=artwork,
            footprint=footprint,
        )
        landings.append(landing)
        if error is not None:
            diagnostics.append(error)
        elif role == "PWR":
            pwr_matches += 1
        else:
            gnd_matches += 1

    source_proven = pwr_landing_count > 0 and pwr_matches == pwr_landing_count
    reference_proven = gnd_landing_count > 0 and gnd_matches == gnd_landing_count
    if pwr_landing_count == 0:
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "PWR_TERMINAL_EVIDENCE_MISSING",
                "no selected-net PWR Device terminal is bound to a supplied branch",
                layer=str(getattr(rail, "pwr_layer", "")),
                net=str(getattr(rail, "net", "")),
            )
        )
    if gnd_landing_count == 0:
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                "GND_TERMINAL_EVIDENCE_MISSING",
                "no configured-GND Device terminal is bound to a supplied branch",
                layer=str(getattr(rail, "gnd_layer", "")),
            )
        )

    diagnostics = sorted(set(diagnostics), key=_diagnostic_sort_key)
    status: Literal["proven", "blocked_fail_closed"] = (
        "proven"
        if source_proven and reference_proven and not diagnostics
        else "blocked_fail_closed"
    )
    manifest = {
        "schema": PROOF_SCHEMA_VERSION,
        "status": status,
        "source_sha256": source_sha256,
        "rail": {
            "rail_id": rail_id,
            "net": str(getattr(rail, "net", "")),
            "pwr_layer": str(getattr(rail, "pwr_layer", "")),
            "gnd_layer": str(getattr(rail, "gnd_layer", "")),
        },
        "configured_gnd_aliases": list(ground_aliases),
        "geometry_basis": (
            "hash-verified ordered PowerSI add/subtract attachments; no modal envelope, "
            "bounding rectangle, clamp, or arbitrary expansion"
        ),
        "landing_rule": (
            "exact artwork covers source point; otherwise only an explicitly source-identified "
            "pad rectangle with positive-area artwork overlap"
        ),
        "branch_anchor_coordinate_tolerance_um": _BRANCH_ANCHOR_TOLERANCE_UM,
        "artwork_assets": [
            item.manifest_value()
            for item in sorted(
                artwork,
                key=lambda item: (
                    _key(item.layer),
                    _key(item.net),
                    _key(item.asset),
                    item.expected_sha256,
                ),
            )
        ],
        "device_pins": [
            _pin_manifest(supplied_pin_by_key[key]) for key in sorted(supplied_pin_by_key)
        ],
        "device_branches": sorted(
            branch_manifest, key=lambda item: _key(item["branch_id"])
        ),
        "landings": sorted(
            landings, key=lambda item: (_key(item["role"]), _key(item["pin_id"]))
        ),
        "source_terminal_artwork_proven": source_proven,
        "reference_terminal_artwork_proven": reference_proven,
        "diagnostics": [item.manifest_value() for item in diagnostics],
    }
    evidence_sha256 = sha256(_canonical_json(manifest)).hexdigest()
    return LayerwiseTerminalArtworkProof(
        status=status,
        source_terminal_artwork_proven=source_proven,
        reference_terminal_artwork_proven=reference_proven,
        evidence_sha256=evidence_sha256,
        manifest=_freeze(manifest),
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "PROOF_SCHEMA_VERSION",
    "LayerwiseTerminalArtworkProof",
    "LayerwiseTerminalProofDiagnostic",
    "SourcePadFootprintEvidence",
    "prove_layerwise_terminal_landings",
]
