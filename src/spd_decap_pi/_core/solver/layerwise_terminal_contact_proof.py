"""Fail-closed Device-anchor proof for the layer-surface network.

The source Device terminals live on their raw SPD source nodes (normally
``TOP``).  They are therefore never projected onto a selected internal plane.
Instead, the import certificate identifies the exact same-NET Trace/Via
component contacted by each terminal, and this module proves that at least one
of that component's retained artwork surfaces is the same ideal topology node
as the selected layer-surface port.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from math import isclose, isfinite
from typing import Any, Mapping, Sequence

from .layer_surface_network import (
    CompiledLayerSurfaceNetwork,
    LayerSurfaceNetworkError,
    LayerSurfacePort,
)
from .layerwise_terminal_proof import (
    PROOF_SCHEMA_VERSION,
    LayerwiseTerminalArtworkProof,
    LayerwiseTerminalProofDiagnostic,
    SourcePadFootprintEvidence,
    _canonical_json,
    _diagnostic_sort_key,
    _freeze,
    _key,
    _kind_value,
    _load_artwork,
    _pin_manifest,
    _terminal_value,
)


_CERTIFICATE_SCHEMA = "spd-layer-surface-connectivity-v2"
_CERTIFICATE_COMPILER = "powersi-same-layer-trace-artwork-island-equivalence-v2"
_ANCHOR_TOLERANCE_UM = 1.0e-9


def _surface_node_id(layer: Any, net: Any) -> str:
    """Mirror the public, collision-safe physical-surface identity encoding."""

    layer_key, net_key = _key(layer), _key(net)
    return f"surface|{len(layer_key)}:{layer_key}|{len(net_key)}:{net_key}"


def _metadata_sha256(payload: Any) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _source_pin_manifest(pin: Any) -> dict[str, Any]:
    value = _pin_manifest(pin)
    value.update(
        {
            "source_node_id": (
                None
                if getattr(pin, "source_node_id", None) is None
                else str(getattr(pin, "source_node_id"))
            ),
            "source_layer": (
                None
                if getattr(pin, "source_layer", None) is None
                else str(getattr(pin, "source_layer"))
            ),
            "source_padstack": (
                None
                if getattr(pin, "source_padstack", None) is None
                else str(getattr(pin, "source_padstack"))
            ),
        }
    )
    return value


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def prove_terminal_surface_contacts(
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
    """Prove explicit Device anchors through exact raw surface components."""

    diagnostics: list[LayerwiseTerminalProofDiagnostic] = []
    source_valid = True
    reference_valid = True

    def fail(
        code: str,
        message: str,
        *,
        role: str | None = None,
        pin_id: str | None = None,
        branch_id: str | None = None,
        layer: str | None = None,
        net: str | None = None,
        asset: str | None = None,
    ) -> None:
        nonlocal source_valid, reference_valid
        if role == "power":
            source_valid = False
        elif role == "ground":
            reference_valid = False
        else:
            source_valid = False
            reference_valid = False
        diagnostics.append(
            LayerwiseTerminalProofDiagnostic(
                code,
                message,
                pin_id=pin_id,
                branch_id=branch_id,
                layer=layer,
                net=net,
                asset=asset,
            )
        )

    try:
        origin_x_um, origin_y_um = map(float, origin_um)
    except (TypeError, ValueError, OverflowError):
        origin_x_um, origin_y_um = 0.0, 0.0
        fail("TERMINAL_ORIGIN_INVALID", "terminal origin must contain two finite coordinates")
    if not isfinite(origin_x_um) or not isfinite(origin_y_um):
        origin_x_um, origin_y_um = 0.0, 0.0
        fail("TERMINAL_ORIGIN_INVALID", "terminal origin must contain two finite coordinates")
    if source_pad_footprints:
        fail(
            "SOURCE_PAD_FOOTPRINT_UNSUPPORTED",
            "surface-contact proof does not use a pad footprint to project TOP coordinates onto deep artwork",
        )

    rail_id = str(getattr(rail, "rail_id", "")).strip()
    rail_net = str(getattr(rail, "net", "")).strip()
    pwr_layer = str(getattr(rail, "pwr_layer", "")).strip()
    gnd_layer = str(getattr(rail, "gnd_layer", "")).strip()
    reference_net = str(reference_net).strip()
    project_rails: dict[str, Any] = {}
    for item in getattr(project, "rails", ()):
        key = _key(getattr(item, "rail_id", ""))
        if key in project_rails:
            fail("RAIL_IDENTITY_AMBIGUOUS", f"project rail identity {key!r} is duplicated")
        project_rails[key] = item
    project_rail = project_rails.get(_key(rail_id))
    if project_rail is None:
        fail("RAIL_NOT_IN_PROJECT", f"rail {rail_id!r} is absent from the project")
    elif any(
        _key(getattr(project_rail, field, "")) != _key(getattr(rail, field, ""))
        for field in ("net", "pwr_layer", "gnd_layer")
    ):
        fail("RAIL_IDENTITY_MISMATCH", f"rail {rail_id!r} differs from the project rail")

    expected_positive = _surface_node_id(pwr_layer, rail_net)
    expected_negative = _surface_node_id(gnd_layer, reference_net)
    if not isinstance(network, CompiledLayerSurfaceNetwork):
        fail("LAYER_SURFACE_NETWORK_INVALID", "terminal proof requires a compiled layer-surface network")
    if not isinstance(selected_port, LayerSurfacePort):
        fail("LAYER_SURFACE_PORT_INVALID", "selected rail port is not a layer-surface port")
    elif (
        _key(selected_port.port_id) != _key(rail_id)
        or selected_port.positive_node_id != expected_positive
        or selected_port.negative_node_id != expected_negative
    ):
        fail(
            "LAYER_SURFACE_PORT_MISMATCH",
            f"selected port does not bind exact surfaces {pwr_layer}/{rail_net} and {gnd_layer}/{reference_net}",
        )
    elif isinstance(network, CompiledLayerSurfaceNetwork) and selected_port not in network.ports:
        fail("LAYER_SURFACE_PORT_MISMATCH", "selected rail port is absent from the compiled network")

    before_artwork = len(diagnostics)
    artwork, _ground_aliases, source_sha256 = _load_artwork(
        project, attachments, rail, diagnostics
    )
    if len(diagnostics) != before_artwork:
        source_valid = False
        reference_valid = False
    exact_reference_artwork = [
        item
        for item in artwork
        if _key(item.layer) == _key(gnd_layer)
        and _key(item.net) == _key(reference_net)
        and item.decoded
    ]
    if not exact_reference_artwork:
        fail(
            "REQUIRED_REFERENCE_ARTWORK_MISSING",
            f"no hash-verified exact reference artwork exists for {gnd_layer}/{reference_net}",
            layer=gnd_layer,
            net=reference_net,
        )

    metadata = getattr(project, "metadata", {})
    spd_import = metadata.get("spd_import") if isinstance(metadata, Mapping) else None
    certificate = (
        spd_import.get("layerwise_surface_connectivity_certificate")
        if isinstance(spd_import, Mapping)
        else None
    )
    certificate_valid = True
    if not isinstance(certificate, Mapping):
        fail(
            "SURFACE_CONNECTIVITY_CERTIFICATE_MISSING",
            "reimport the SPD to retain exact Device-to-surface connectivity evidence",
        )
        certificate = {}
        certificate_valid = False
    if certificate and (
        certificate.get("schema_version") != _CERTIFICATE_SCHEMA
        or certificate.get("compiler_id") != _CERTIFICATE_COMPILER
    ):
        fail(
            "SURFACE_CONNECTIVITY_CERTIFICATE_UNSUPPORTED",
            "surface-connectivity certificate schema/compiler is unsupported",
        )
        certificate_valid = False
    certificate_source = str(certificate.get("source_sha256", "")).strip().casefold()
    if certificate and certificate_source != source_sha256:
        fail(
            "SURFACE_CONNECTIVITY_SOURCE_MISMATCH",
            "surface-connectivity certificate is not bound to the retained SPD source",
        )
        certificate_valid = False
    certificate_evidence = str(certificate.get("evidence_sha256", "")).strip().casefold()
    unsigned_certificate = {
        str(key): value for key, value in certificate.items() if str(key) != "evidence_sha256"
    }
    try:
        calculated_certificate_evidence = _metadata_sha256(unsigned_certificate)
    except (TypeError, ValueError, OverflowError):
        calculated_certificate_evidence = ""
    if certificate and (
        len(certificate_evidence) != 64
        or calculated_certificate_evidence != certificate_evidence
    ):
        fail(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INTEGRITY_FAILED",
            "surface-connectivity evidence SHA-256 does not match its payload",
        )
        certificate_valid = False

    certificate_assets = certificate.get("geometry_assets", ())
    if certificate and not _sequence(certificate_assets):
        fail(
            "SURFACE_CONNECTIVITY_CERTIFICATE_INVALID",
            "surface-connectivity certificate has no geometry-asset manifest",
        )
        certificate_assets = ()
        certificate_valid = False

    def artwork_identity(item: Any) -> tuple[str, str, str, str] | None:
        if isinstance(item, Mapping):
            layer = item.get("layer", "")
            net = item.get("net", "")
            asset = item.get("asset", "")
            digest = item.get("asset_sha256", "")
        else:
            layer = getattr(item, "layer", "")
            net = getattr(item, "net", "")
            asset = getattr(item, "asset", "")
            digest = getattr(item, "expected_sha256", "")
        identity = (_key(layer), _key(net), str(asset).strip(), str(digest).strip().casefold())
        return identity if all(identity) else None

    retained_geometry_records = (
        spd_import.get("plane_geometries", ()) if isinstance(spd_import, Mapping) else ()
    )
    if not _sequence(retained_geometry_records):
        retained_geometry_records = ()
    expected_asset_counts = Counter(
        identity
        for item in retained_geometry_records
        if (identity := artwork_identity(item)) is not None
    )
    observed_asset_counts = Counter(
        identity
        for item in certificate_assets
        if (identity := artwork_identity(item)) is not None
    )
    if certificate and observed_asset_counts != expected_asset_counts:
        fail(
            "SURFACE_CONNECTIVITY_GEOMETRY_MISMATCH",
            "certificate geometry identities differ from the retained hash-verified artwork index",
        )
        certificate_valid = False
    for identity, count in sorted(expected_asset_counts.items()):
        _layer_key, _net_key, asset, digest = identity
        content = attachments.get(asset) if isinstance(attachments, Mapping) else None
        if count != 1 or not isinstance(content, (bytes, bytearray)):
            fail(
                "ARTWORK_ATTACHMENT_MISSING",
                f"retained surface attachment {asset!r} is missing or ambiguous",
                asset=asset,
            )
            certificate_valid = False
        elif sha256(bytes(content)).hexdigest() != digest:
            fail(
                "ARTWORK_ASSET_INTEGRITY_FAILED",
                f"retained surface attachment {asset!r} fails SHA-256",
                asset=asset,
            )
            certificate_valid = False

    compile_failures = certificate.get("compile_failures", ())
    if certificate and not _sequence(compile_failures):
        fail("SURFACE_CONNECTIVITY_CERTIFICATE_INVALID", "compile-failure manifest is invalid")
        compile_failures = ()
        certificate_valid = False
    for failure in compile_failures:
        if isinstance(failure, Mapping) and _key(failure.get("rail_id", "")) == _key(rail_id):
            fail(
                "RAIL_ANCHOR_CERTIFICATE_COMPILE_FAILED",
                f"rail anchor certificate compilation failed: {failure.get('code', 'unknown')}: {failure.get('message', '')}",
            )

    project_pin_by_key: dict[str, Any] = {}
    for pin in getattr(project, "pins", ()):
        pin_id = str(getattr(pin, "pin_id", "")).strip()
        pin_key = _key(pin_id)
        if not pin_id or pin_key in project_pin_by_key:
            fail("DEVICE_PIN_IDENTITY_AMBIGUOUS", f"Device pin identity {pin_id!r} is blank or duplicated")
        else:
            project_pin_by_key[pin_key] = pin

    branch_by_key: dict[str, Any] = {}
    expected_anchor_keys: set[str] = set()
    branches_manifest: list[dict[str, Any]] = []
    branch_roles: list[tuple[Any, str, str, str]] = []
    for branch in sorted(
        device_branches,
        key=lambda item: (_key(getattr(item, "branch_id", "")), str(getattr(item, "branch_id", ""))),
    ):
        branch_id = str(getattr(branch, "branch_id", "")).strip()
        branch_key = _key(branch_id)
        if not branch_id or branch_key in branch_by_key:
            fail("DEVICE_BRANCH_IDENTITY_AMBIGUOUS", f"Device branch identity {branch_id!r} is blank or duplicated")
            continue
        branch_by_key[branch_key] = branch
        token_keys = {_key(token) for token in branch_id.split("|") if token.strip()}
        power_id = str(getattr(branch, "source_power_pin_id", "") or "").strip()
        ground_id = str(getattr(branch, "source_ground_pin_id", "") or "").strip()
        if not power_id or not ground_id or _key(power_id) not in token_keys or _key(ground_id) not in token_keys:
            fail(
                "DEVICE_PORT_ANCHOR_EVIDENCE_MISSING",
                f"branch {branch_id!r} does not name explicit PWR/GND anchors contained in its source tokens",
                branch_id=branch_id,
            )
            continue
        expected_anchor_keys.update((_key(power_id), _key(ground_id)))
        branch_roles.extend(
            ((branch, branch_id, "power", power_id), (branch, branch_id, "ground", ground_id))
        )
        branches_manifest.append(
            {
                "branch_id": branch_id,
                "source_power_pin_id": power_id,
                "source_ground_pin_id": ground_id,
                "member_pin_ids": sorted(
                    {
                        str(getattr(project_pin_by_key[token], "pin_id", ""))
                        for token in token_keys
                        if token in project_pin_by_key
                    },
                    key=lambda value: (value.casefold(), value),
                ),
                "port": {
                    "port_id": str(getattr(getattr(branch, "port", None), "port_id", "")),
                    "x_m": float(getattr(getattr(branch, "port", None), "x_m", float("nan"))),
                    "y_m": float(getattr(getattr(branch, "port", None), "y_m", float("nan"))),
                    "geometry_tolerance_allowed": False,
                },
            }
        )

    supplied_pin_by_key: dict[str, Any] = {}
    for pin in device_pins:
        pin_id = str(getattr(pin, "pin_id", "")).strip()
        pin_key = _key(pin_id)
        if not pin_id or pin_key in supplied_pin_by_key:
            fail("DEVICE_PIN_EVIDENCE_AMBIGUOUS", f"supplied Device pin {pin_id!r} is blank or duplicated")
        else:
            supplied_pin_by_key[pin_key] = pin
    if set(supplied_pin_by_key) != expected_anchor_keys:
        fail(
            "DEVICE_PORT_ANCHOR_SET_MISMATCH",
            "supplied Device pins must be exactly the explicit branch-anchor union",
        )
    for pin_key, supplied in supplied_pin_by_key.items():
        retained = project_pin_by_key.get(pin_key)
        if retained is None or _source_pin_manifest(supplied) != _source_pin_manifest(retained):
            fail(
                "DEVICE_PIN_SOURCE_MISMATCH",
                f"supplied Device pin {getattr(supplied, 'pin_id', '')!r} differs from retained source evidence",
                pin_id=str(getattr(supplied, "pin_id", "")) or None,
            )

    bindings = certificate.get("rail_anchor_bindings", ())
    if certificate and not _sequence(bindings):
        fail("SURFACE_CONNECTIVITY_CERTIFICATE_INVALID", "rail-anchor binding manifest is invalid")
        bindings = ()
        certificate_valid = False
    binding_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw in bindings:
        if not isinstance(raw, Mapping) or _key(raw.get("rail_id", "")) != _key(rail_id):
            continue
        branch_key = _key(raw.get("branch_id", ""))
        role_key = _key(raw.get("role", ""))
        key = (branch_key, role_key)
        if not branch_key or role_key not in {"power", "ground"} or key in binding_by_key:
            fail("RAIL_ANCHOR_BINDING_AMBIGUOUS", f"rail {rail_id!r} has an invalid or duplicate anchor binding")
        else:
            binding_by_key[key] = raw

    contacts = certificate.get("terminal_contacts", ())
    if certificate and not _sequence(contacts):
        fail("SURFACE_CONNECTIVITY_CERTIFICATE_INVALID", "terminal-contact manifest is invalid")
        contacts = ()
        certificate_valid = False
    contact_by_key: dict[str, Mapping[str, Any]] = {}
    duplicate_contact_keys: set[str] = set()
    for raw in contacts:
        if not isinstance(raw, Mapping):
            fail("TERMINAL_CONTACT_INVALID", "terminal-contact row is not a mapping")
            continue
        pin_key = _key(raw.get("pin_id", ""))
        if not pin_key or pin_key in contact_by_key:
            duplicate_contact_keys.add(pin_key)
            fail("TERMINAL_CONTACT_AMBIGUOUS", f"terminal contact {pin_key!r} is blank or duplicated")
        else:
            contact_by_key[pin_key] = raw

    terminal_manifest: list[dict[str, Any]] = []
    for branch, branch_id, role, pin_id in branch_roles:
        pin_key = _key(pin_id)
        pin = project_pin_by_key.get(pin_key)
        target_surface = expected_positive if role == "power" else expected_negative
        if pin is None:
            fail(
                "DEVICE_PORT_ANCHOR_MISSING",
                f"branch {branch_id!r} anchor {pin_id!r} is absent from the project",
                role=role,
                pin_id=pin_id,
                branch_id=branch_id,
            )
            continue
        terminal = _terminal_value(getattr(pin, "terminal", ""))
        kind = _kind_value(getattr(pin, "kind", ""))
        pin_net = str(getattr(pin, "net", "")).strip()
        if kind != "DEVICE_BUMP" or terminal != ("PWR" if role == "power" else "GND"):
            fail(
                "DEVICE_PORT_ANCHOR_TYPE_MISMATCH",
                f"anchor {pin_id!r} is not the expected Device {role} terminal",
                role=role,
                pin_id=pin_id,
                branch_id=branch_id,
            )
        expected_net = rail_net if role == "power" else reference_net
        if _key(pin_net) != _key(expected_net):
            fail(
                "TERMINAL_SOURCE_NET_MISMATCH" if role == "power" else "TERMINAL_REFERENCE_NET_MISMATCH",
                f"anchor {pin_id!r} NET {pin_net!r} does not equal exact {role} NET {expected_net!r}",
                role=role,
                pin_id=pin_id,
                branch_id=branch_id,
                net=pin_net,
            )
        if role == "power":
            try:
                port_x_um = origin_x_um + float(branch.port.x_m) * 1.0e6
                port_y_um = origin_y_um + float(branch.port.y_m) * 1.0e6
                pin_x_um = float(getattr(pin, "x_um"))
                pin_y_um = float(getattr(pin, "y_um"))
            except (AttributeError, TypeError, ValueError, OverflowError):
                port_x_um = port_y_um = pin_x_um = pin_y_um = float("nan")
            if not all(isfinite(value) for value in (port_x_um, port_y_um, pin_x_um, pin_y_um)) or not (
                isclose(port_x_um, pin_x_um, rel_tol=0.0, abs_tol=_ANCHOR_TOLERANCE_UM)
                and isclose(port_y_um, pin_y_um, rel_tol=0.0, abs_tol=_ANCHOR_TOLERANCE_UM)
            ):
                fail(
                    "DEVICE_PORT_ANCHOR_MISMATCH",
                    f"branch {branch_id!r} finite port is not located at explicit PWR anchor {pin_id!r}",
                    role=role,
                    pin_id=pin_id,
                    branch_id=branch_id,
                )

        binding = binding_by_key.get((_key(branch_id), role))
        if binding is None or _key(binding.get("pin_id", "")) != pin_key:
            fail(
                "RAIL_ANCHOR_BINDING_MISMATCH",
                f"certificate does not bind branch {branch_id!r} {role} role to {pin_id!r}",
                role=role,
                pin_id=pin_id,
                branch_id=branch_id,
            )
        contact = contact_by_key.get(pin_key)
        matched_layers: list[str] = []
        contact_layers: list[str] = []
        if contact is None or pin_key in duplicate_contact_keys:
            fail(
                "TERMINAL_CONTACT_MISSING",
                f"certificate has no unique contact for anchor {pin_id!r}",
                role=role,
                pin_id=pin_id,
                branch_id=branch_id,
            )
        else:
            source_node_id = str(getattr(pin, "source_node_id", "") or "").strip()
            source_layer = str(getattr(pin, "source_layer", "") or "").strip()
            if (
                _key(contact.get("pin_id", "")) != pin_key
                or _key(contact.get("net", "")) != _key(pin_net)
                or not source_node_id
                or _key(contact.get("source_node_id", "")) != _key(source_node_id)
                or not source_layer
                or _key(contact.get("source_layer", "")) != _key(source_layer)
            ):
                fail(
                    "TERMINAL_CONTACT_SOURCE_MISMATCH",
                    f"contact evidence for {pin_id!r} differs from its retained source node/layer/NET",
                    role=role,
                    pin_id=pin_id,
                    branch_id=branch_id,
                )
            if _key(contact.get("status", "")) != "complete":
                fail(
                    "TERMINAL_CONTACT_INCOMPLETE",
                    f"contact evidence for {pin_id!r} is incomplete",
                    role=role,
                    pin_id=pin_id,
                    branch_id=branch_id,
                )
            raw_layers = contact.get("contact_layers", ())
            if not _sequence(raw_layers):
                raw_layers = ()
            contact_layers = sorted(
                {str(layer).strip() for layer in raw_layers if str(layer).strip()},
                key=lambda value: (value.casefold(), value),
            )
            if not contact_layers:
                fail(
                    "TERMINAL_CONTACT_LAYERS_MISSING",
                    f"contact evidence for {pin_id!r} reaches no retained artwork surface",
                    role=role,
                    pin_id=pin_id,
                    branch_id=branch_id,
                )
            elif isinstance(network, CompiledLayerSurfaceNetwork):
                for layer in contact_layers:
                    contact_surface = _surface_node_id(layer, pin_net)
                    try:
                        if network.surfaces_share_ideal_node(contact_surface, target_surface):
                            matched_layers.append(layer)
                    except LayerSurfaceNetworkError:
                        continue
                if not matched_layers:
                    fail(
                        "TERMINAL_SURFACE_NOT_CONNECTED",
                        f"raw component for {pin_id!r} does not coalesce with selected {role} surface {target_surface!r}",
                        role=role,
                        pin_id=pin_id,
                        branch_id=branch_id,
                    )
        terminal_manifest.append(
            {
                "branch_id": branch_id,
                "role": role,
                "pin_id": pin_id,
                "pin_net": pin_net,
                "source_node_id": getattr(pin, "source_node_id", None),
                "source_layer": getattr(pin, "source_layer", None),
                "target_surface_node_id": target_surface,
                "contact_layers": contact_layers,
                "matched_contact_layers": matched_layers,
                "target_reduced_node": (
                    network.reduced_node_index(target_surface)
                    if isinstance(network, CompiledLayerSurfaceNetwork)
                    and network.has_exact_surface_node(target_surface)
                    else None
                ),
                "first_via_issue_is_provenance_only": True,
                "issues": list(contact.get("issues", ())) if isinstance(contact, Mapping) and _sequence(contact.get("issues", ())) else [],
            }
        )

    expected_binding_keys = {
        (_key(branch_id), role) for _branch, branch_id, role, _pin_id in branch_roles
    }
    if set(binding_by_key) != expected_binding_keys:
        fail(
            "RAIL_ANCHOR_BINDING_SET_MISMATCH",
            f"certificate bindings for rail {rail_id!r} differ from the compiled branch roles",
        )

    diagnostics = sorted(diagnostics, key=_diagnostic_sort_key)
    ready = not diagnostics and source_valid and reference_valid and certificate_valid
    manifest: dict[str, Any] = {
        "schema": PROOF_SCHEMA_VERSION,
        "status": "proven" if ready else "blocked_fail_closed",
        "geometry_basis": (
            "raw same-NET Trace/Via component contacts on hash-verified retained artwork; "
            "no TOP coordinate projection onto deep artwork and no finite-port geometry tolerance"
        ),
        "source_sha256": source_sha256,
        "surface_connectivity_evidence_sha256": certificate_evidence,
        "rail": {
            "rail_id": rail_id,
            "net": rail_net,
            "pwr_layer": pwr_layer,
            "gnd_layer": gnd_layer,
            "reference_net": reference_net,
        },
        "selected_port": {
            "port_id": str(getattr(selected_port, "port_id", "")),
            "positive_node_id": str(getattr(selected_port, "positive_node_id", "")),
            "negative_node_id": str(getattr(selected_port, "negative_node_id", "")),
        },
        "origin_um": [origin_x_um, origin_y_um],
        "artwork": [
            item.manifest_value()
            for item in sorted(
                artwork,
                key=lambda item: (_key(item.layer), _key(item.net), item.asset, item.expected_sha256),
            )
        ],
        "device_anchor_pins": [
            _source_pin_manifest(supplied_pin_by_key[key]) for key in sorted(supplied_pin_by_key)
        ],
        "branches": sorted(branches_manifest, key=lambda item: _key(item.get("branch_id", ""))),
        "terminal_contacts": sorted(
            terminal_manifest,
            key=lambda item: (_key(item["branch_id"]), _key(item["role"]), _key(item["pin_id"])),
        ),
        "diagnostics": [item.manifest_value() for item in diagnostics],
        "limitations": [
            "uniform potential is assumed on each retained layer/NET artwork surface",
            "raw branched components establish exact topology but add no invented serial R/L",
            "Device branch series R/L remains externally owned and is not stamped again",
        ],
    }
    evidence_sha256 = sha256(_canonical_json(manifest)).hexdigest()
    return LayerwiseTerminalArtworkProof(
        status="proven" if ready else "blocked_fail_closed",
        source_terminal_artwork_proven=bool(ready and source_valid),
        reference_terminal_artwork_proven=bool(ready and reference_valid),
        evidence_sha256=evidence_sha256,
        manifest=_freeze(manifest),
        diagnostics=tuple(diagnostics),
    )


__all__ = ["prove_terminal_surface_contacts"]
