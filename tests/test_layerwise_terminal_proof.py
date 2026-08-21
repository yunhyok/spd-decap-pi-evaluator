from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, Mapping

import numpy as np

from spd_decap_pi._core import services
from spd_decap_pi._core.models.impedance import SeriesRLModel
from spd_decap_pi._core.solver.layer_surface_network import (
    CompiledLayerSurfaceNetwork,
    LayerSurfacePort,
    LayerSurfaceViaLink,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.layerwise_network import _surface_node_id
from spd_decap_pi._core.solver.layerwise_terminal_proof import (
    PROOF_SCHEMA_VERSION,
    prove_layerwise_terminal_landings,
)
from spd_decap_pi._core.solver.modal import (
    DeviceBranch,
    DielectricDispersion,
    FinitePort,
)
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
)
from spd_decap_pi._core.solver.uniform_c00 import DispersiveAdjacentGap


_SOURCE_SHA256 = "a" * 64
_PWR_TOP = _surface_node_id("TOP", "VDD")
_PWR_TARGET = _surface_node_id("L1", "VDD")
_GND_TOP = _surface_node_id("TOP", "DGND")
_GND_TARGET = _surface_node_id("L2", "DGND")
_SURFACES = (_PWR_TOP, _PWR_TARGET, _GND_TOP, _GND_TARGET)


@dataclass(frozen=True)
class _Case:
    project: Any
    attachments: Mapping[str, bytes]
    rail: Any
    reference_net: str
    selected_port: LayerSurfacePort
    network: CompiledLayerSurfaceNetwork
    pins: tuple[Any, ...]
    branches: tuple[DeviceBranch, ...]


def _rectangle(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _asset(layer: str, net: str, polygon: list[list[float]]) -> tuple[bytes, str]:
    content, _size = services._compress_spd_geometry_payload(
        layer=layer,
        net=net,
        positive_polygons=(polygon,),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=1,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )
    return content, sha256(content).hexdigest()


def _pin(
    pin_id: str,
    *,
    terminal: str,
    net: str,
    x_um: float,
    y_um: float = 50.0,
) -> Any:
    refdes, number = pin_id.split(":", 1)
    return SimpleNamespace(
        pin_id=pin_id,
        refdes=refdes,
        pin=number,
        net=net,
        x_um=x_um,
        y_um=y_um,
        kind="DEVICE_BUMP",
        terminal=terminal,
        domain="CORE" if terminal == "PWR" else None,
        site="0",
        bump_group="BG1",
        via_template_id="VIA1",
        source_node_id=f"Node-{pin_id}",
        source_layer="TOP",
        source_padstack="DEVICE-TOP-PAD",
    )


def _branch(
    power: Any,
    ground: Any,
    *,
    branch_id: str,
    port_x_um: float | None = None,
) -> DeviceBranch:
    return DeviceBranch(
        branch_id=branch_id,
        port=FinitePort(
            x_m=float(power.x_um if port_x_um is None else port_x_um) * 1.0e-6,
            y_m=float(power.y_um) * 1.0e-6,
            width_m=10.0e-6,
            height_m=10.0e-6,
            port_id=branch_id,
        ),
        series_path=SeriesRLModel(
            "VIA1", resistance_ohm=0.01, inductance_h=50.0e-12
        ),
        source_power_pin_id=power.pin_id,
        source_ground_pin_id=ground.pin_id,
    )


def _edge() -> DispersiveAdjacentGap:
    matrix = np.zeros((len(_SURFACES), len(_SURFACES)), dtype=np.float64)
    capacitance_f = 1.0e-9
    first = _SURFACES.index(_PWR_TARGET)
    second = _SURFACES.index(_GND_TARGET)
    matrix[first, first] += capacitance_f
    matrix[second, second] += capacitance_f
    matrix[first, second] -= capacitance_f
    matrix[second, first] -= capacitance_f
    return DispersiveAdjacentGap(
        partial=AdjacentGapMaxwellPartial(
            upper_layer="L1",
            lower_layer="L2",
            nominal_relative_permittivity=4.0,
            net_names=_SURFACES,
            maxwell_capacitance_f=matrix,
        ),
        dispersion=DielectricDispersion(
            frequencies_hz=(1.0e6,),
            relative_permittivities=(4.0,),
            loss_tangents=(0.0,),
        ),
    )


def _network(*, disconnect_power_contact: bool = False) -> tuple[
    LayerSurfacePort, CompiledLayerSurfaceNetwork
]:
    selected_port = LayerSurfacePort("R1", _PWR_TARGET, _GND_TARGET)
    links = [
        LayerSurfaceViaLink(
            "GND-COMPONENT", _GND_TOP, _GND_TARGET, 1, "topology_only_ideal"
        )
    ]
    if not disconnect_power_contact:
        links.append(
            LayerSurfaceViaLink(
                "PWR-COMPONENT",
                _PWR_TOP,
                _PWR_TARGET,
                1,
                "topology_only_ideal",
            )
        )
    network = compile_layer_surface_network(
        _SURFACES,
        partials=(_edge(),),
        via_links=tuple(links),
        ports=(selected_port,),
    )
    return selected_port, network


def _certificate(
    *,
    geometry_assets: list[dict[str, str]],
    branches: tuple[DeviceBranch, ...],
    pins_by_id: Mapping[str, Any],
) -> dict[str, Any]:
    bindings: list[dict[str, str]] = []
    anchor_ids: set[str] = set()
    for branch in branches:
        assert branch.source_power_pin_id is not None
        assert branch.source_ground_pin_id is not None
        bindings.extend(
            (
                {
                    "rail_id": "R1",
                    "branch_id": branch.branch_id,
                    "role": "power",
                    "pin_id": branch.source_power_pin_id,
                },
                {
                    "rail_id": "R1",
                    "branch_id": branch.branch_id,
                    "role": "ground",
                    "pin_id": branch.source_ground_pin_id,
                },
            )
        )
        anchor_ids.update(
            (branch.source_power_pin_id, branch.source_ground_pin_id)
        )
    contacts = [
        {
            "pin_id": pin_id,
            "net": pins_by_id[pin_id].net,
            "source_node_id": f"Node-{pin_id}",
            "incident_via_id": None,
            "source_layer": "TOP",
            "contact_layers": ["TOP"],
            "status": "complete",
            "issues": ["first_via_status:missing_incident_via"],
        }
        for pin_id in sorted(anchor_ids, key=str.casefold)
    ]
    components = [
        {"component_id": "PWR-COMPONENT", "net": "VDD", "layers": ["TOP", "L1"]},
        {
            "component_id": "GND-COMPONENT",
            "net": "DGND",
            "layers": ["TOP", "L2"],
        },
    ]
    payload: dict[str, Any] = {
        "schema_version": "spd-layer-surface-connectivity-v2",
        "compiler_id": "powersi-same-layer-trace-artwork-island-equivalence-v2",
        "source_sha256": _SOURCE_SHA256,
        "geometry_assets": geometry_assets,
        "components": components,
        "rail_anchor_bindings": bindings,
        "terminal_contacts": contacts,
        "compile_failures": [],
        "recovery_statistics": {"reachable": len(contacts), "requested": len(contacts)},
        "status": "complete",
    }
    return {
        **payload,
        "evidence_sha256": services._canonical_metadata_sha256(payload),
    }


def _case(
    *,
    disconnect_power_contact: bool = False,
    extra_branch_token: bool = False,
    two_branches: bool = False,
    reverse: bool = False,
    port_x_um: float | None = None,
) -> _Case:
    # Both anchors lie on exact TOP artwork but outside the deep target artwork.
    # The proof must follow raw component contacts into the compiled ideal node,
    # rather than projecting either TOP coordinate onto L1/L2.
    geometry = (
        ("TOP", "VDD", _rectangle(0.0, 0.0, 40.0, 100.0)),
        ("L1", "VDD", _rectangle(100.0, 0.0, 200.0, 100.0)),
        ("TOP", "DGND", _rectangle(0.0, 0.0, 40.0, 100.0)),
        ("L2", "DGND", _rectangle(100.0, 0.0, 200.0, 100.0)),
    )
    records: list[dict[str, str]] = []
    attachments: dict[str, bytes] = {}
    for index, (layer, net, polygon) in enumerate(geometry):
        content, digest = _asset(layer, net, polygon)
        asset = f"geometry/{index}-{layer}-{net}.spdgeom.zlib"
        records.append(
            {
                "layer": layer,
                "net": net,
                "asset": asset,
                "asset_sha256": digest,
            }
        )
        attachments[asset] = content

    power = _pin("U1:P1", terminal="PWR", net="VDD", x_um=20.0)
    ground = _pin("U1:G1", terminal="GND", net="DGND", x_um=30.0)
    extra = _pin("U1:EXTRA", terminal="PWR", net="VDD", x_um=21.0)
    branch_token = "|U1:EXTRA" if extra_branch_token else ""
    branches = (
        _branch(
            power,
            ground,
            branch_id=f"cluster-1|U1:P1|U1:G1{branch_token}",
            port_x_um=port_x_um,
        ),
    )
    anchor_pins: tuple[Any, ...] = (power, ground)
    project_pins: tuple[Any, ...] = (power, ground, extra)
    if two_branches:
        power2 = _pin("U1:P2", terminal="PWR", net="VDD", x_um=22.0)
        ground2 = _pin("U1:G2", terminal="GND", net="DGND", x_um=32.0)
        branches += (
            _branch(
                power2,
                ground2,
                branch_id="cluster-2|U1:P2|U1:G2",
            ),
        )
        anchor_pins += (power2, ground2)
        project_pins += (power2, ground2)

    pins_by_id = {pin.pin_id: pin for pin in project_pins}
    certificate = _certificate(
        geometry_assets=[dict(item) for item in records],
        branches=branches,
        pins_by_id=pins_by_id,
    )
    rail = SimpleNamespace(
        rail_id="R1",
        net="VDD",
        pwr_layer="L1",
        gnd_layer="L2",
        domain="CORE",
        site="0",
    )
    if reverse:
        records.reverse()
        project_pins = tuple(reversed(project_pins))
        anchor_pins = tuple(reversed(anchor_pins))
        branches = tuple(reversed(branches))
        attachments = dict(reversed(tuple(attachments.items())))
    project = SimpleNamespace(
        rails=(rail,),
        pins=project_pins,
        gnd_aliases=("DGND",),
        metadata={
            "spd_import": {
                "source_sha256": _SOURCE_SHA256,
                "plane_geometries": records,
                "layerwise_surface_connectivity_certificate": certificate,
            }
        },
    )
    selected_port, network = _network(
        disconnect_power_contact=disconnect_power_contact
    )
    return _Case(
        project,
        attachments,
        rail,
        "DGND",
        selected_port,
        network,
        anchor_pins,
        branches,
    )


def _prove(case: _Case):
    return prove_layerwise_terminal_landings(
        case.project,
        case.attachments,
        case.rail,
        case.reference_net,
        case.selected_port,
        case.network,
        case.pins,
        case.branches,
        origin_um=(0.0, 0.0),
    )


def _codes(proof: Any) -> set[str]:
    return {item.code for item in proof.diagnostics}


def test_top_contact_reaches_deep_port_through_same_ideal_reduced_node() -> None:
    case = _case()
    proof = _prove(case)

    assert proof.ready
    assert proof.status == "proven"
    assert proof.source_terminal_artwork_proven
    assert proof.reference_terminal_artwork_proven
    assert case.network.surfaces_share_ideal_node(_PWR_TOP, _PWR_TARGET)
    assert case.network.surfaces_share_ideal_node(_GND_TOP, _GND_TARGET)
    manifest = proof.manifest_dict()
    assert manifest["schema"] == PROOF_SCHEMA_VERSION
    assert "no TOP coordinate projection" in manifest["geometry_basis"]
    assert manifest["rail"]["reference_net"] == "DGND"
    assert manifest["selected_port"]["port_id"] == "R1"
    assert len(manifest["surface_connectivity_evidence_sha256"]) == 64
    assert manifest["artwork"]
    assert manifest["branches"]
    assert manifest["terminal_contacts"]
    contacts_by_role = {
        item["role"]: item for item in manifest["terminal_contacts"]
    }
    assert contacts_by_role["power"]["target_reduced_node"] == (
        case.network.reduced_node_index(_PWR_TARGET)
    )
    assert contacts_by_role["ground"]["target_reduced_node"] == (
        case.network.reduced_node_index(_GND_TARGET)
    )


def test_ground_anchor_net_must_match_selected_reference_net() -> None:
    case = _case()
    mismatched = _Case(
        case.project,
        case.attachments,
        case.rail,
        "AGND",
        case.selected_port,
        case.network,
        case.pins,
        case.branches,
    )

    proof = _prove(mismatched)

    assert not proof.ready
    assert proof.status == "blocked_fail_closed"
    assert not proof.reference_terminal_artwork_proven
    assert "TERMINAL_REFERENCE_NET_MISMATCH" in _codes(proof)


def test_contact_surface_must_share_the_selected_port_ideal_node() -> None:
    proof = _prove(_case(disconnect_power_contact=True))

    assert not proof.ready
    assert proof.status == "blocked_fail_closed"
    assert not proof.source_terminal_artwork_proven
    assert "TERMINAL_SURFACE_NOT_CONNECTED" in _codes(proof)


def test_certificate_evidence_tamper_fails_closed() -> None:
    case = _case()
    certificate = case.project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    certificate["terminal_contacts"][0]["contact_layers"] = ["L1"]

    proof = _prove(case)

    assert not proof.ready
    assert "SURFACE_CONNECTIVITY_CERTIFICATE_INTEGRITY_FAILED" in _codes(proof)


def test_exact_artwork_attachment_tamper_fails_closed() -> None:
    case = _case()
    asset = next(iter(case.attachments))
    tampered = {**case.attachments, asset: case.attachments[asset] + b"tampered"}
    proof = prove_layerwise_terminal_landings(
        case.project,
        tampered,
        case.rail,
        case.reference_net,
        case.selected_port,
        case.network,
        case.pins,
        case.branches,
        origin_um=(0.0, 0.0),
    )

    assert not proof.ready
    assert "ARTWORK_ASSET_INTEGRITY_FAILED" in _codes(proof)


def test_non_anchor_branch_token_does_not_expand_explicit_anchor_union() -> None:
    case = _case(extra_branch_token=True)

    assert "U1:EXTRA" in {pin.pin_id for pin in case.project.pins}
    assert "U1:EXTRA" not in {pin.pin_id for pin in case.pins}
    assert _prove(case).ready


def test_proof_hash_is_deterministic_under_all_input_orderings() -> None:
    first = _prove(_case(two_branches=True))
    second = _prove(_case(two_branches=True, reverse=True))

    assert first.ready and second.ready
    assert first.manifest_dict() == second.manifest_dict()
    assert first.evidence_sha256 == second.evidence_sha256


def test_finite_port_coordinate_must_match_explicit_pwr_anchor() -> None:
    proof = _prove(_case(port_x_um=20.5))

    assert not proof.ready
    assert proof.status == "blocked_fail_closed"
    assert "DEVICE_PORT_ANCHOR_MISMATCH" in _codes(proof)
