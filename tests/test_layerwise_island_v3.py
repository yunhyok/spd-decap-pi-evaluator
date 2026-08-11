from __future__ import annotations

from hashlib import sha256
import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.sparse import csc_matrix

from spd_decap_pi._core.solver import layerwise_network
from spd_decap_pi._core.solver.layer_surface_network import (
    LayerSurfacePort,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.layerwise_network import LayerwiseNetworkUnavailable
from spd_decap_pi._core.solver.modal import DielectricDispersion
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
)
from spd_decap_pi._core.solver.uniform_c00 import DispersiveAdjacentGap


P1 = "spd-surface-island:" + "1" * 24
P2 = "spd-surface-island:" + "2" * 24
G1 = "spd-surface-island:" + "3" * 24
P3 = "spd-surface-island:" + "4" * 24
SOURCE_SHA = "a" * 64
ASSET_P_SHA = "b" * 64
ASSET_G_SHA = "c" * 64
EMPTY_IDS_SHA = sha256(b"").hexdigest()


def _canonical_metadata_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _resigned(certificate: dict) -> dict:
    payload = {
        key: value for key, value in certificate.items() if key != "evidence_sha256"
    }
    return {**payload, "evidence_sha256": _canonical_metadata_sha256(payload)}


def _component(net: str, layer: str, island_ids: list[str]) -> dict:
    ordered = sorted(island_ids)
    evidence = _canonical_metadata_sha256(
        {
            "source_sha256": SOURCE_SHA,
            "net": net.casefold(),
            "layer": layer.casefold(),
            "island_ids": ordered,
        }
    )
    return {
        "component_id": f"spd-surface-equivalence-component:{evidence[:24]}",
        "net": net,
        "layer": layer,
        "island_ids": ordered,
        "representative_island_id": ordered[0],
        "component_evidence_sha256": evidence,
        "contact_status": "complete",
    }


def _surface_rows(*, equivalent_power_islands: bool) -> tuple[list[dict], list[dict]]:
    power_components = (
        [_component("VDD", "TOP", [P1, P2])]
        if equivalent_power_islands
        else [
            _component("VDD", "TOP", [P1]),
            _component("VDD", "TOP", [P2]),
        ]
    )
    components = [
        *power_components,
        _component("GND", "BOT", [G1]),
    ]
    proofs = [
        {
            "net": "VDD",
            "layer": "TOP",
            "island_ids": [P1, P2],
            "contacted_island_ids": [P1, P2],
            "graph_component_count": 1 if equivalent_power_islands else 2,
            "status": "complete",
        },
        {
            "net": "GND",
            "layer": "BOT",
            "island_ids": [G1],
            "contacted_island_ids": [G1],
            "graph_component_count": 1,
            "status": "complete",
        },
    ]
    return components, proofs


def _contact(
    pin_id: str,
    *,
    component: dict,
    suffix: str,
    path_kind: str = "direct_via_landing",
) -> dict:
    via_id = f"VIA_{suffix}"
    external = f"NODE_EXT_{suffix}"
    internal = f"NODE_INT_{suffix}"
    component_id = component["component_id"]
    evidence = component["component_evidence_sha256"]
    return {
        "pin_id": pin_id,
        "net": component["net"],
        "incident_via_id": via_id if path_kind == "direct_via_landing" else None,
        "incident_net": (
            component["net"] if path_kind == "direct_via_landing" else None
        ),
        "incident_padstack": (
            "PS1" if path_kind == "direct_via_landing" else None
        ),
        "external_endpoint_node_id": external,
        "internal_endpoint_node_id": (
            internal if path_kind == "direct_via_landing" else None
        ),
        "contact_path_kind": path_kind,
        "contact_component_ids": [component_id],
        "contact_component_evidence_sha256s": [evidence],
        "contact_component_id": component_id,
        "contact_component_evidence_sha256": evidence,
        "status": "complete",
    }


def _landing(
    *,
    via_id: str,
    external: str,
    internal: str,
    owner: str,
    component: dict,
    external_layer: str,
) -> dict:
    endpoint_layer = component["layer"]
    return {
        "via_id": via_id,
        "endpoint_node_id": external,
        "external_endpoint_node_id": external,
        "landing_key": [via_id.casefold(), external.casefold()],
        "internal_endpoint_node_id": internal,
        "terminal_owner_kind": owner,
        "contact_path_kind": "direct_via_landing",
        "endpoint_resolution_kind": "same_layer_trace_artwork_component",
        "external_endpoint_layer": external_layer,
        "padstack": "PS1",
        "drill_diameter_um": 80.0,
        "material": "COPPER",
        "segments": [
            {
                "ordinal": 0,
                "start_layer": external_layer,
                "end_layer": endpoint_layer,
                "length_um": 120.0,
            }
        ],
        "physical_model_status": "complete",
        "physical_model_issues": [],
        "net": component["net"],
        "contact_component_id": component["component_id"],
        "component_layer": endpoint_layer,
        "component_island_ids": list(component["island_ids"]),
        "representative_island_id": component["representative_island_id"],
        "component_evidence_sha256": component["component_evidence_sha256"],
        "component_binding_status": "complete",
        "component_binding_issues": [],
        "status": "complete",
    }


def _certificate(
    *,
    equivalent_power_islands: bool,
    second_power_branch: bool = False,
    include_full_v3_contract: bool = False,
) -> dict:
    components, proofs = _surface_rows(
        equivalent_power_islands=equivalent_power_islands
    )
    power_p1_component = next(
        item for item in components if P1 in item["island_ids"]
    )
    power_p2_component = next(
        item for item in components if P2 in item["island_ids"]
    )
    ground_component = next(
        item for item in components if G1 in item["island_ids"]
    )
    anchors = [
        {"rail_id": "R1", "branch_id": "B1", "role": "power", "pin_id": "U1:P1"},
        {"rail_id": "R1", "branch_id": "B1", "role": "ground", "pin_id": "U1:G1"},
    ]
    contacts = [
        _contact("U1:P1", component=power_p1_component, suffix="P1"),
        _contact("U1:G1", component=ground_component, suffix="G1"),
    ]
    if second_power_branch:
        anchors.extend(
            [
                {"rail_id": "R1", "branch_id": "B2", "role": "power", "pin_id": "U1:P2"},
                {"rail_id": "R1", "branch_id": "B2", "role": "ground", "pin_id": "U1:G2"},
            ]
        )
        contacts.extend(
            [
                _contact("U1:P2", component=power_p2_component, suffix="P2"),
                _contact("U1:G2", component=ground_component, suffix="G2"),
            ]
        )
    payload = {
        "schema_version": "spd-layer-surface-connectivity-v3",
        "compiler_id": "powersi-same-layer-trace-island-terminal-via-pair-v3",
        "source_sha256": SOURCE_SHA,
        "geometry_assets": [
            {
                "layer": "TOP",
                "net": "VDD",
                "asset": "p.json",
                "asset_sha256": ASSET_P_SHA,
                "island_ids": [P1, P2],
            },
            {
                "layer": "BOT",
                "net": "GND",
                "asset": "g.json",
                "asset_sha256": ASSET_G_SHA,
                "island_ids": [G1],
            },
        ],
        "components": [],
        "surface_equivalence_components": components,
        "surface_equivalence_proofs": proofs,
        "rail_anchor_bindings": anchors,
        "terminal_contacts": contacts,
        "terminal_landing_contacts": [],
        "via_island_pair_aggregates": [],
        "via_island_pair_coverage": {
            "raw_target_via_count": 0,
            "model_relevant_via_count": 0,
            "paired_via_count": 0,
            "terminal_owned_unpaired_count": 0,
            "terminal_owned_unpaired_via_ids_sha256": EMPTY_IDS_SHA,
            "unsupported_missing_endpoint_count": 0,
            "unsupported_missing_endpoint_via_ids_sha256": EMPTY_IDS_SHA,
            "outside_retained_interface_scope_count": 0,
            "outside_retained_interface_scope_via_ids_sha256": EMPTY_IDS_SHA,
            "terminal_owned_ids_supplied": True,
            "terminal_owned_declared_count": 0,
            "terminal_owned_observed_count": 0,
            "paired_terminal_owned_count": 0,
            "paired_substrate_count": 0,
            "status": "complete",
        },
        "compile_failures": [],
        "recovery_statistics": {},
        "status": "complete",
    }
    if include_full_v3_contract:
        component_by_id = {
            item["component_id"]: item for item in components
        }
        payload["terminal_landing_contacts"] = [
            _landing(
                via_id=str(contact["incident_via_id"]),
                external=str(contact["external_endpoint_node_id"]),
                internal=str(contact["internal_endpoint_node_id"]),
                owner="device",
                component=component_by_id[str(contact["contact_component_id"])],
                external_layer=(
                    "EXT_TOP"
                    if component_by_id[str(contact["contact_component_id"])]["layer"]
                    == "TOP"
                    else "EXT_BOT"
                ),
            )
            for contact in contacts
            if contact["contact_path_kind"] == "direct_via_landing"
        ]
        owned_count = len(payload["terminal_landing_contacts"])
        payload["via_island_pair_coverage"].update(
            {
                "raw_target_via_count": owned_count,
                "model_relevant_via_count": owned_count,
                "terminal_owned_unpaired_count": owned_count,
                "terminal_owned_declared_count": owned_count,
                "terminal_owned_observed_count": owned_count,
            }
        )
    return {**payload, "evidence_sha256": _canonical_metadata_sha256(payload)}


def _with_bottom_power_aggregate(
    certificate: dict,
    *,
    start_island_id: str = P1,
) -> dict:
    payload = json.loads(json.dumps(certificate))
    payload.pop("evidence_sha256", None)
    bottom_component = _component("VDD", "BOT", [P3])
    payload["geometry_assets"].append(
        {
            "layer": "BOT",
            "net": "VDD",
            "asset": "pbot.json",
            "asset_sha256": "d" * 64,
            "island_ids": [P3],
        }
    )
    payload["surface_equivalence_components"].append(bottom_component)
    payload["surface_equivalence_proofs"].append(
        {
            "net": "VDD",
            "layer": "BOT",
            "island_ids": [P3],
            "contacted_island_ids": [P3],
            "graph_component_count": 1,
            "status": "complete",
        }
    )
    start_component = next(
        item
        for item in payload["surface_equivalence_components"]
        if start_island_id in item["island_ids"]
    )
    payload["via_island_pair_aggregates"].append(
        {
            "net": "VDD",
            "padstack": "PS1",
            "start_layer": "TOP",
            "end_layer": "BOT",
            "start_island_id": start_component["representative_island_id"],
            "end_island_id": bottom_component["representative_island_id"],
            "start_component_id": start_component["component_id"],
            "start_component_island_ids": list(start_component["island_ids"]),
            "start_component_evidence_sha256": start_component[
                "component_evidence_sha256"
            ],
            "end_component_id": bottom_component["component_id"],
            "end_component_island_ids": list(bottom_component["island_ids"]),
            "end_component_evidence_sha256": bottom_component[
                "component_evidence_sha256"
            ],
            "count": 1,
            "via_ids_sha256": "e" * 64,
            "terminal_owned_count": 0,
            "substrate_count": 1,
            "drill_diameter_um": 100.0,
            "material": "COPPER",
            "segments": [
                {
                    "ordinal": 0,
                    "start_layer": "TOP",
                    "end_layer": "BOT",
                    "length_um": 120.0,
                }
            ],
            "physical_model_status": "complete",
            "physical_model_issues": [],
            "component_binding_status": "complete",
            "component_binding_issues": [],
        }
    )
    coverage = payload["via_island_pair_coverage"]
    coverage["raw_target_via_count"] += 1
    coverage["model_relevant_via_count"] += 1
    coverage["paired_via_count"] += 1
    coverage["paired_substrate_count"] += 1
    return {**payload, "evidence_sha256": _canonical_metadata_sha256(payload)}


def _stackup() -> tuple[SimpleNamespace, ...]:
    def layer(
        name: str,
        *,
        is_conductor: bool,
        thickness_um: float,
        dk: float | None = None,
        df: float | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            name=name,
            is_conductor=is_conductor,
            thickness_um=thickness_um,
            conductivity_s_m=5.8e7 if is_conductor else None,
            dk=dk,
            df=df,
            dielectric_properties=(),
            pwr_nets=(),
        )

    return (
        layer("EXT_TOP", is_conductor=True, thickness_um=20.0),
        layer("D1", is_conductor=False, thickness_um=100.0, dk=4.0, df=0.01),
        layer("TOP", is_conductor=True, thickness_um=20.0),
        layer("D2", is_conductor=False, thickness_um=100.0, dk=4.0, df=0.01),
        layer("BOT", is_conductor=True, thickness_um=20.0),
        layer("D3", is_conductor=False, thickness_um=100.0, dk=4.0, df=0.01),
        layer("EXT_BOT", is_conductor=True, thickness_um=20.0),
    )


def _pin_source_values(pin_id: str) -> dict[str, object]:
    suffix = pin_id.split(":", 1)[-1]
    is_ground = suffix.startswith("G")
    ordinal = {"P1": 1.0, "P2": 2.0, "G1": 3.0, "G2": 4.0}[suffix]
    return {
        "pin_id": pin_id,
        "refdes": pin_id.split(":", 1)[0],
        "pin": suffix,
        "terminal": "GND" if is_ground else "PWR",
        "net": "GND" if is_ground else "VDD",
        "source_node_id": f"NODE_EXT_{suffix}",
        "source_layer": "EXT_BOT" if is_ground else "EXT_TOP",
        "source_padstack": "DUT",
        "source_x_um": ordinal * 10.0,
        "source_y_um": ordinal * 20.0,
    }


def _legacy_terminal_certificate(certificate: dict) -> dict:
    landing_by_via = {
        str(item["via_id"]).casefold(): item
        for item in certificate["terminal_landing_contacts"]
    }
    rows: list[dict] = []
    for contact in certificate["terminal_contacts"]:
        source = _pin_source_values(str(contact["pin_id"]))
        via_id = str(contact.get("incident_via_id") or "")
        landing = landing_by_via.get(via_id.casefold()) if via_id else None
        direct = contact["contact_path_kind"] == "direct_via_landing"
        candidate_ids = (via_id,) if direct else ()
        endpoint_evidence = _canonical_metadata_sha256(
            {
                "source_sha256": SOURCE_SHA,
                "pin_id_key": str(contact["pin_id"]).casefold(),
                "source_node_id_key": str(source["source_node_id"]).casefold(),
            }
        )
        rows.append(
            {
                "endpoint_id": f"spd-device-terminal-via:{endpoint_evidence[:24]}",
                **source,
                "incident_via_id": via_id or None,
                "incident_net": str(contact["net"]) if direct else None,
                "incident_padstack": (
                    str(landing["padstack"])
                    if direct and landing is not None
                    else None
                ),
                "incident_opposite_node_id": (
                    str(contact["internal_endpoint_node_id"])
                    if direct
                    else None
                ),
                "candidate_count": len(candidate_ids),
                "candidate_via_ids_sha256": _canonical_metadata_sha256(
                    candidate_ids
                ),
                "status": "complete" if direct else "missing_incident_via",
                "issues": [] if direct else ["missing_incident_via"],
            }
        )
    complete_count = sum(item["status"] == "complete" for item in rows)
    payload = {
        "schema_version": "spd-layerwise-device-terminal-vias-v1",
        "compiler_id": "powersi-direct-device-top-via-v1",
        "source_sha256": SOURCE_SHA,
        "raw_spd_embedded": False,
        "scope": {
            "selected_power_nets": ["VDD"],
            "ground_nets": ["GND"],
        },
        "terminals": rows,
        "terminal_count": len(rows),
        "complete_terminal_count": complete_count,
        "incomplete_terminal_count": len(rows) - complete_count,
        "status": (
            "complete" if rows and complete_count == len(rows) else "incomplete"
        ),
    }
    return {**payload, "evidence_sha256": _canonical_metadata_sha256(payload)}


def _project(certificate: dict) -> SimpleNamespace:
    records = tuple(
        {
            "layer": item["layer"],
            "net": item["net"],
            "asset": item["asset"],
            "asset_sha256": item["asset_sha256"],
        }
        for item in certificate["geometry_assets"]
    )
    return SimpleNamespace(
        metadata={
            "spd_import": {
                "source_sha256": SOURCE_SHA,
                "plane_geometries": records,
                "layerwise_device_terminal_via_certificate": (
                    _legacy_terminal_certificate(certificate)
                ),
                "layerwise_surface_connectivity_certificate": certificate,
            }
        },
        stackup_layers=_stackup(),
    )


def _network(certificate: dict):
    nodes = (P1, P2, G1)
    equivalence_links, _counts = layerwise_network._compile_surface_equivalence_links(
        certificate, set(nodes)
    )
    matrix = csc_matrix(
        np.asarray(
            [
                [1.0e-12, 0.0, -1.0e-12],
                [0.0, 1.0e-12, -1.0e-12],
                [-1.0e-12, -1.0e-12, 2.0e-12],
            ],
            dtype=np.float64,
        )
    )
    partial = AdjacentGapMaxwellPartial(
        "TOP", "BOT", 4.0, nodes, matrix, separation_m=100.0e-6
    )
    dispersion = DielectricDispersion((1.0e6,), (4.0,), (0.01,))
    port = LayerSurfacePort("R1", P1, G1)
    network = compile_layer_surface_network(
        nodes,
        partials=(DispersiveAdjacentGap(partial, dispersion),),
        via_links=equivalence_links,
        ports=(port,),
    )
    return port, network


def _rail() -> SimpleNamespace:
    return SimpleNamespace(rail_id="R1", net="VDD", pwr_layer="TOP", gnd_layer="BOT")


def _branch(
    branch_id: str, power_pin: str, ground_pin: str = "U1:G1"
) -> SimpleNamespace:
    return SimpleNamespace(
        branch_id=branch_id,
        source_power_pin_id=power_pin,
        source_ground_pin_id=ground_pin,
        series_path={
            "model": "synthetic-differential-via-template",
            "branch_id": branch_id,
        },
    )


def _pin(pin_id: str) -> SimpleNamespace:
    values = _pin_source_values(pin_id)
    return SimpleNamespace(
        pin_id=values["pin_id"],
        refdes=values["refdes"],
        pin=values["pin"],
        terminal=values["terminal"],
        net=values["net"],
        source_node_id=values["source_node_id"],
        source_layer=values["source_layer"],
        source_padstack=values["source_padstack"],
        x_um=values["source_x_um"],
        y_um=values["source_y_um"],
    )


def test_v2_surface_certificate_is_rejected_before_any_legacy_cross_reference() -> None:
    certificate = {
        "schema_version": "spd-layer-surface-connectivity-v2",
        "compiler_id": "powersi-trace-via-surface-reachability-v2",
    }
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "source_sha256": SOURCE_SHA,
                "layerwise_surface_connectivity_certificate": certificate,
            }
        }
    )
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_surface_connectivity_certificate(project, ())
    assert error.value.code == "SURFACE_CONNECTIVITY_CERTIFICATE_UNSUPPORTED"


def test_complete_self_contained_v3_certificate_validates_terminal_physics() -> None:
    certificate = _certificate(
        equivalent_power_islands=False, include_full_v3_contract=True
    )
    project = _project(certificate)
    records = project.metadata["spd_import"]["plane_geometries"]
    assert (
        layerwise_network._validated_surface_connectivity_certificate(project, records)
        is certificate
    )


def test_trace_first_device_contact_needs_component_proof_but_no_landing_row() -> None:
    certificate = _certificate(
        equivalent_power_islands=False, include_full_v3_contract=True
    )
    payload = {
        key: value for key, value in certificate.items() if key != "evidence_sha256"
    }
    power_contact = next(
        item for item in payload["terminal_contacts"] if item["pin_id"] == "U1:P1"
    )
    power_contact["contact_path_kind"] = "trace_component"
    power_contact["incident_via_id"] = None
    power_contact["incident_net"] = None
    power_contact["incident_padstack"] = None
    power_contact["internal_endpoint_node_id"] = None
    payload["terminal_landing_contacts"] = [
        item
        for item in payload["terminal_landing_contacts"]
        if item["terminal_owner_kind"] != "device"
        or item["net"] != "VDD"
    ]
    payload["via_island_pair_coverage"].update(
        {
            "raw_target_via_count": 1,
            "model_relevant_via_count": 1,
            "terminal_owned_unpaired_count": 1,
            "terminal_owned_declared_count": 1,
            "terminal_owned_observed_count": 1,
        }
    )
    certificate = {**payload, "evidence_sha256": _canonical_metadata_sha256(payload)}
    project = _project(certificate)
    records = project.metadata["spd_import"]["plane_geometries"]
    assert (
        layerwise_network._validated_surface_connectivity_certificate(project, records)
        is certificate
    )


def test_uncontacted_island_is_nonblocking_only_as_its_own_physical_component() -> None:
    certificate = _certificate(equivalent_power_islands=False)
    proof = next(
        item
        for item in certificate["surface_equivalence_proofs"]
        if item["net"] == "VDD"
    )
    proof["contacted_island_ids"] = [P1]
    proof["graph_component_count"] = 1
    proof["status"] = "uncontacted_island"
    next(
        item
        for item in certificate["surface_equivalence_components"]
        if item["island_ids"] == [P2]
    )["contact_status"] = "uncontacted"
    _display, islands_by_surface = layerwise_network._certificate_island_inventory(
        certificate
    )
    component_by_island, _components = layerwise_network._surface_equivalence_partition(
        certificate, islands_by_surface
    )
    assert component_by_island[P1][2] != component_by_island[P2][2]

    invalid = _certificate(equivalent_power_islands=True)
    invalid_proof = next(
        item
        for item in invalid["surface_equivalence_proofs"]
        if item["net"] == "VDD"
    )
    invalid_proof["contacted_island_ids"] = [P1]
    invalid_proof["graph_component_count"] = 1
    invalid_proof["status"] = "uncontacted_island"
    _display, invalid_inventory = layerwise_network._certificate_island_inventory(
        invalid
    )
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._surface_equivalence_partition(invalid, invalid_inventory)
    assert error.value.code == "SURFACE_EQUIVALENCE_PROOF_INVALID"


def test_uncontacted_component_cannot_serve_a_device_terminal_or_landing() -> None:
    certificate = json.loads(
        json.dumps(
            _certificate(
                equivalent_power_islands=False,
                include_full_v3_contract=True,
            )
        )
    )
    proof = next(
        item
        for item in certificate["surface_equivalence_proofs"]
        if item["net"] == "VDD"
    )
    proof.update(
        {
            "contacted_island_ids": [P1],
            "graph_component_count": 1,
            "status": "uncontacted_island",
        }
    )
    p2_component = next(
        item
        for item in certificate["surface_equivalence_components"]
        if item["island_ids"] == [P2]
    )
    p2_component["contact_status"] = "uncontacted"
    contact = next(
        item for item in certificate["terminal_contacts"] if item["net"] == "VDD"
    )
    contact.update(
        {
            "contact_component_ids": [p2_component["component_id"]],
            "contact_component_evidence_sha256s": [
                p2_component["component_evidence_sha256"]
            ],
            "contact_component_id": p2_component["component_id"],
            "contact_component_evidence_sha256": p2_component[
                "component_evidence_sha256"
            ],
        }
    )
    landing = next(
        item
        for item in certificate["terminal_landing_contacts"]
        if item["net"] == "VDD"
    )
    landing.update(
        {
            "contact_component_id": p2_component["component_id"],
            "component_island_ids": [P2],
            "representative_island_id": P2,
            "component_evidence_sha256": p2_component[
                "component_evidence_sha256"
            ],
        }
    )
    certificate = _resigned(certificate)
    project = _project(certificate)
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_surface_connectivity_certificate(
            project, project.metadata["spd_import"]["plane_geometries"]
        )
    assert error.value.code == "TERMINAL_CONTACT_CERTIFICATE_INVALID"


def test_valid_same_net_cross_layer_aggregate_validates_and_stamps_finite_rl() -> None:
    certificate = _with_bottom_power_aggregate(
        _certificate(
            equivalent_power_islands=False,
            include_full_v3_contract=True,
        )
    )
    project = _project(certificate)
    records = project.metadata["spd_import"]["plane_geometries"]
    assert (
        layerwise_network._validated_surface_connectivity_certificate(
            project, records
        )
        is certificate
    )
    links, counts = layerwise_network._compile_v3_via_island_links(
        project, {P1, P2, P3, G1}, certificate
    )
    assert len(links) == 1
    assert {links[0].first_node_id, links[0].second_node_id} == {P1, P3}
    assert counts["substrate_via_count"] == 1


def test_uncontacted_component_cannot_serve_a_via_aggregate_endpoint() -> None:
    certificate = json.loads(
        json.dumps(
            _with_bottom_power_aggregate(
                _certificate(
                    equivalent_power_islands=False,
                    include_full_v3_contract=True,
                ),
                start_island_id=P2,
            )
        )
    )
    proof = next(
        item
        for item in certificate["surface_equivalence_proofs"]
        if item["net"] == "VDD" and item["layer"] == "TOP"
    )
    proof.update(
        {
            "contacted_island_ids": [P1],
            "graph_component_count": 1,
            "status": "uncontacted_island",
        }
    )
    next(
        item
        for item in certificate["surface_equivalence_components"]
        if item["island_ids"] == [P2]
    )["contact_status"] = "uncontacted"
    certificate = _resigned(certificate)
    project = _project(certificate)
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_surface_connectivity_certificate(
            project, project.metadata["spd_import"]["plane_geometries"]
        )
    assert error.value.code == "VIA_ISLAND_PAIR_CERTIFICATE_INVALID"


def test_terminal_via_segment_length_must_match_current_stackup_centers() -> None:
    certificate = _certificate(
        equivalent_power_islands=False, include_full_v3_contract=True
    )
    payload = {
        key: value for key, value in certificate.items() if key != "evidence_sha256"
    }
    payload["terminal_landing_contacts"][0]["segments"][0]["length_um"] = 121.0
    certificate = {**payload, "evidence_sha256": _canonical_metadata_sha256(payload)}
    project = _project(certificate)
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_surface_connectivity_certificate(
            project, project.metadata["spd_import"]["plane_geometries"]
        )
    assert error.value.code == "VIA_SEGMENT_STACKUP_MISMATCH"


def test_terminal_via_segment_path_cannot_detour_and_return() -> None:
    certificate = json.loads(
        json.dumps(
            _certificate(
                equivalent_power_islands=False,
                include_full_v3_contract=True,
            )
        )
    )
    landing = next(
        item
        for item in certificate["terminal_landing_contacts"]
        if item["net"] == "VDD"
    )
    landing["segments"] = [
        {
            "ordinal": 0,
            "start_layer": "EXT_TOP",
            "end_layer": "BOT",
            "length_um": 240.0,
        },
        {
            "ordinal": 1,
            "start_layer": "BOT",
            "end_layer": "TOP",
            "length_um": 120.0,
        },
    ]
    certificate = _resigned(certificate)
    project = _project(certificate)
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_surface_connectivity_certificate(
            project, project.metadata["spd_import"]["plane_geometries"]
        )
    assert error.value.code == "VIA_SEGMENT_STACKUP_MISMATCH"


def test_aggregate_via_segment_path_cannot_detour_and_return() -> None:
    certificate = json.loads(
        json.dumps(
            _with_bottom_power_aggregate(
                _certificate(
                    equivalent_power_islands=False,
                    include_full_v3_contract=True,
                )
            )
        )
    )
    certificate["via_island_pair_aggregates"][0]["segments"] = [
        {
            "ordinal": 0,
            "start_layer": "TOP",
            "end_layer": "EXT_BOT",
            "length_um": 240.0,
        },
        {
            "ordinal": 1,
            "start_layer": "EXT_BOT",
            "end_layer": "BOT",
            "length_um": 120.0,
        },
    ]
    certificate = _resigned(certificate)
    project = _project(certificate)
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_surface_connectivity_certificate(
            project, project.metadata["spd_import"]["plane_geometries"]
        )
    assert error.value.code == "VIA_SEGMENT_STACKUP_MISMATCH"


def test_direct_device_via_id_is_globally_unique_across_terminal_roles() -> None:
    original = _certificate(
        equivalent_power_islands=False,
        include_full_v3_contract=True,
    )
    project = _project(original)
    certificate = json.loads(json.dumps(original))
    ground_contact = next(
        item for item in certificate["terminal_contacts"] if item["net"] == "GND"
    )
    ground_contact["incident_via_id"] = "VIA_P1"
    ground_landing = next(
        item
        for item in certificate["terminal_landing_contacts"]
        if item["net"] == "GND"
    )
    ground_landing["via_id"] = "VIA_P1"
    ground_landing["landing_key"] = [
        "via_p1",
        str(ground_landing["external_endpoint_node_id"]).casefold(),
    ]
    certificate = _resigned(certificate)
    project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ] = certificate
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_surface_connectivity_certificate(
            project, project.metadata["spd_import"]["plane_geometries"]
        )
    assert error.value.code == "TERMINAL_CONTACT_CERTIFICATE_INVALID"


def test_terminal_landing_population_must_equal_coverage_owner_counts() -> None:
    certificate = json.loads(
        json.dumps(
            _certificate(
                equivalent_power_islands=False,
                include_full_v3_contract=True,
            )
        )
    )
    coverage = certificate["via_island_pair_coverage"]
    coverage.update(
        {
            "raw_target_via_count": 0,
            "model_relevant_via_count": 0,
            "terminal_owned_unpaired_count": 0,
            "terminal_owned_unpaired_via_ids_sha256": EMPTY_IDS_SHA,
            "terminal_owned_declared_count": 0,
            "terminal_owned_observed_count": 0,
        }
    )
    certificate = _resigned(certificate)
    project = _project(certificate)
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_surface_connectivity_certificate(
            project, project.metadata["spd_import"]["plane_geometries"]
        )
    assert error.value.code == "VIA_ISLAND_PAIR_COVERAGE_INVALID"


def test_surplus_complete_device_via_owner_needs_no_anchor_landing_row() -> None:
    certificate = json.loads(
        json.dumps(
            _certificate(
                equivalent_power_islands=False,
                include_full_v3_contract=True,
            )
        )
    )
    coverage = certificate["via_island_pair_coverage"]
    coverage.update(
        {
            "raw_target_via_count": 3,
            "model_relevant_via_count": 3,
            "terminal_owned_unpaired_count": 3,
            "terminal_owned_unpaired_via_ids_sha256": "f" * 64,
            "terminal_owned_declared_count": 3,
            "terminal_owned_observed_count": 3,
        }
    )
    certificate = _resigned(certificate)
    project = _project(certificate)
    terminal_certificate = project.metadata["spd_import"][
        "layerwise_device_terminal_via_certificate"
    ]
    surplus = dict(terminal_certificate["terminals"][0])
    surplus.update(
        {
            "endpoint_id": (
                "spd-device-terminal-via:"
                + _canonical_metadata_sha256(
                    {
                        "source_sha256": SOURCE_SHA,
                        "pin_id_key": "u1:p9",
                        "source_node_id_key": "node_ext_p9",
                    }
                )[:24]
            ),
            "pin_id": "U1:P9",
            "pin": "P9",
            "source_node_id": "NODE_EXT_P9",
            "source_x_um": 90.0,
            "source_y_um": 180.0,
            "incident_via_id": "VIA_P9_SURPLUS",
            "incident_opposite_node_id": "NODE_INT_P9",
            "candidate_via_ids_sha256": _canonical_metadata_sha256(
                ("VIA_P9_SURPLUS",)
            ),
        }
    )
    terminal_certificate["terminals"].append(surplus)
    terminal_certificate.update(
        {
            "terminal_count": 3,
            "complete_terminal_count": 3,
            "incomplete_terminal_count": 0,
            "status": "complete",
        }
    )
    unsigned = {
        key: value
        for key, value in terminal_certificate.items()
        if key != "evidence_sha256"
    }
    terminal_certificate["evidence_sha256"] = _canonical_metadata_sha256(
        unsigned
    )
    assert (
        layerwise_network._validated_surface_connectivity_certificate(
            project, project.metadata["spd_import"]["plane_geometries"]
        )
        is certificate
    )
    disclosure = layerwise_network._device_terminal_ownership_disclosure(
        project, certificate
    )
    assert disclosure["exact_device_terminal_owned_via_count"] == 3
    assert disclosure["anchor_device_terminal_landing_via_count"] == 2
    assert disclosure["surplus_device_terminal_owned_via_count"] == 1
    assert len(disclosure["surplus_device_terminal_owned_via_ids_sha256"]) == 64


def test_compile_rejects_non_mapping_geometry_manifest_row_before_filtering() -> None:
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "plane_geometries": (
                    {"layer": "TOP", "net": "VDD"},
                    "not-a-mapping",
                )
            }
        }
    )
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network.compile_layerwise_substrate(project, {"x": b"x"})
    assert error.value.code == "SURFACE_GEOMETRY_MANIFEST_INVALID"


def test_same_net_split_islands_remain_distinct_without_trace_equivalence() -> None:
    certificate = _certificate(equivalent_power_islands=False)
    _port, network = _network(certificate)
    assert network.reduced_node_index(P1) != network.reduced_node_index(P2)


def test_same_layer_trace_equivalence_is_the_only_ideal_island_collapse() -> None:
    certificate = _certificate(equivalent_power_islands=True)
    _port, network = _network(certificate)
    assert network.reduced_node_index(P1) == network.reduced_node_index(P2)
    assert network.reduced_node_index(P1) != network.reduced_node_index(G1)


def test_full_v3_substrate_compile_requests_island_artwork_and_keeps_sparse_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachments = {"p.json": b"power-artwork", "g.json": b"ground-artwork"}
    certificate = _certificate(
        equivalent_power_islands=False, include_full_v3_contract=True
    )
    payload = {
        key: value for key, value in certificate.items() if key != "evidence_sha256"
    }
    payload["geometry_assets"][0]["asset_sha256"] = sha256(
        attachments["p.json"]
    ).hexdigest()
    payload["geometry_assets"][1]["asset_sha256"] = sha256(
        attachments["g.json"]
    ).hexdigest()
    certificate = {**payload, "evidence_sha256": _canonical_metadata_sha256(payload)}
    records = tuple(
        {
            "layer": item["layer"],
            "net": item["net"],
            "asset": item["asset"],
            "asset_sha256": item["asset_sha256"],
        }
        for item in payload["geometry_assets"]
    )
    rail = _rail()
    rail.mixed_reference_certificate = None
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "source_sha256": SOURCE_SHA,
                "plane_geometries": records,
                "layerwise_device_terminal_via_certificate": (
                    _legacy_terminal_certificate(certificate)
                ),
                "layerwise_surface_connectivity_certificate": certificate,
            }
        },
        stackup_layers=_stackup(),
        gnd_aliases=("GND",),
        rails=(rail,),
    )
    source_model = SimpleNamespace(
        artwork=(
            SimpleNamespace(layer="TOP", net="VDD", node_name=P1),
            SimpleNamespace(layer="TOP", net="VDD", node_name=P2),
            SimpleNamespace(layer="BOT", net="GND", node_name=G1),
        )
    )
    calls: list[dict[str, object]] = []

    def source_builder(*_args: object, **kwargs: object) -> SimpleNamespace:
        calls.append(dict(kwargs))
        return source_model

    partial = AdjacentGapMaxwellPartial(
        "TOP",
        "BOT",
        4.0,
        (P1, P2, G1),
        csc_matrix(
            np.asarray(
                [
                    [1.0e-12, 0.0, -1.0e-12],
                    [0.0, 1.0e-12, -1.0e-12],
                    [-1.0e-12, -1.0e-12, 2.0e-12],
                ]
            )
        ),
        separation_m=100.0e-6,
    )
    monkeypatch.setattr(
        layerwise_network, "capacitance_model_from_project", source_builder
    )
    monkeypatch.setattr(
        layerwise_network,
        "extract_sparse_adjacent_gap_island_capacitance",
        lambda model, **_kwargs: (partial,) if model is source_model else (),
    )
    monkeypatch.setattr(
        layerwise_network,
        "_gap_dispersion",
        lambda *_args: DielectricDispersion((1.0e6,), (4.0,), (0.01,)),
    )
    layerwise_network.clear_layerwise_substrate_cache()
    try:
        substrate = layerwise_network.compile_layerwise_substrate(
            project, attachments, required_rail_id="R1"
        )
    finally:
        layerwise_network.clear_layerwise_substrate_cache()

    assert calls and calls[0]["island_resolved"] is True
    assert calls[0]["validate_artwork"] is False
    assert substrate.network.surface_node_ids == (P1, P2, G1)
    assert isinstance(
        substrate.network.partials[0].partial.maxwell_capacitance_f, csc_matrix
    )
    assert substrate.provenance["dense_raw_matrix_materialized"] is False
    assert substrate.provenance["exact_device_terminal_owned_via_count"] == 2
    assert substrate.provenance["anchor_device_terminal_landing_via_count"] == 2
    assert substrate.provenance["surplus_device_terminal_owned_via_count"] == 0
    assert len(
        substrate.provenance[
            "surplus_device_terminal_owned_via_ids_sha256"
        ]
    ) == 64


def test_v3_terminal_proof_accepts_all_anchors_on_selected_reduced_components() -> None:
    certificate = _certificate(
        equivalent_power_islands=True,
        second_power_branch=True,
        include_full_v3_contract=True,
    )
    project = _project(certificate)
    port, network = _network(certificate)
    branches = (
        _branch("B1", "U1:P1"),
        _branch("B2", "U1:P2", "U1:G2"),
    )
    relevant = (
        _pin("U1:P1"),
        _pin("U1:P2"),
        _pin("U1:G1"),
        _pin("U1:G2"),
    )
    proof = layerwise_network._prove_v3_terminal_island_components(
        certificate,
        _rail(),
        "GND",
        port,
        network,
        relevant,
        branches,
        project=project,
    )
    assert proof.ready
    assert proof.manifest_dict()["schema_version"] == (
        "layerwise-terminal-island-component-proof-v3"
    )
    assert proof.manifest_dict()["terminal_via_stamp_scope"] == (
        "direct_via_exact_owner_and_landing_bound_to_runtime_branch_series_template;"
        "certified_landing_rl_not_substrate_stamped;trace_component_no_incident_via"
    )
    assert len(proof.manifest_dict()["anchor_contacts"]) == 4


def test_v3_terminal_proof_rejects_anchors_on_multiple_power_components() -> None:
    certificate = _certificate(
        equivalent_power_islands=False,
        second_power_branch=True,
        include_full_v3_contract=True,
    )
    project = _project(certificate)
    port, network = _network(certificate)
    branches = (
        _branch("B1", "U1:P1"),
        _branch("B2", "U1:P2", "U1:G2"),
    )
    relevant = (
        _pin("U1:P1"),
        _pin("U1:P2"),
        _pin("U1:G1"),
        _pin("U1:G2"),
    )
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._prove_v3_terminal_island_components(
            certificate,
            _rail(),
            "GND",
            port,
            network,
            relevant,
            branches,
            project=project,
        )
    assert error.value.code == "RAIL_PORT_MULTI_ISLAND_UNSUPPORTED"


def test_v3_terminal_proof_rejects_changed_exact_first_via_owner_row() -> None:
    certificate = _certificate(
        equivalent_power_islands=False,
        include_full_v3_contract=True,
    )
    project = _project(certificate)
    terminal_certificate = project.metadata["spd_import"][
        "layerwise_device_terminal_via_certificate"
    ]
    owner = next(
        item for item in terminal_certificate["terminals"] if item["net"] == "VDD"
    )
    owner["incident_via_id"] = "VIA_TAMPERED"
    owner["candidate_via_ids_sha256"] = _canonical_metadata_sha256(
        ("VIA_TAMPERED",)
    )
    unsigned = {
        key: value
        for key, value in terminal_certificate.items()
        if key != "evidence_sha256"
    }
    terminal_certificate["evidence_sha256"] = _canonical_metadata_sha256(
        unsigned
    )
    port, network = _network(certificate)
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._prove_v3_terminal_island_components(
            certificate,
            _rail(),
            "GND",
            port,
            network,
            (_pin("U1:P1"), _pin("U1:G1")),
            (_branch("B1", "U1:P1"),),
            project=project,
        )
    assert error.value.code == "TERMINAL_CONTACT_LANDING_MISMATCH"


def test_v3_terminal_proof_rejects_landing_padstack_tampered_with_v3_contact() -> None:
    original = _certificate(
        equivalent_power_islands=False,
        include_full_v3_contract=True,
    )
    project = _project(original)
    tampered = json.loads(json.dumps(original))
    contact = next(
        item for item in tampered["terminal_contacts"] if item["net"] == "VDD"
    )
    landing = next(
        item
        for item in tampered["terminal_landing_contacts"]
        if item["net"] == "VDD"
    )
    contact["incident_padstack"] = "PS_TAMPERED"
    landing["padstack"] = "PS_TAMPERED"
    tampered = _resigned(tampered)
    project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ] = tampered
    port, network = _network(tampered)
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._prove_v3_terminal_island_components(
            tampered,
            _rail(),
            "GND",
            port,
            network,
            (_pin("U1:P1"), _pin("U1:G1")),
            (_branch("B1", "U1:P1"),),
            project=project,
        )
    assert error.value.code == "DEVICE_TERMINAL_VIA_OWNER_MISMATCH"


def test_v3_trace_first_proof_rejects_contradictory_direct_via_owner_row() -> None:
    certificate = json.loads(
        json.dumps(
            _certificate(
                equivalent_power_islands=False,
                include_full_v3_contract=True,
            )
        )
    )
    contact = next(
        item for item in certificate["terminal_contacts"] if item["net"] == "VDD"
    )
    contact.update(
        {
            "contact_path_kind": "trace_component",
            "incident_via_id": None,
            "incident_net": None,
            "incident_padstack": None,
            "internal_endpoint_node_id": None,
        }
    )
    certificate["terminal_landing_contacts"] = [
        item
        for item in certificate["terminal_landing_contacts"]
        if item["net"] != "VDD"
    ]
    certificate["via_island_pair_coverage"].update(
        {
            "raw_target_via_count": 2,
            "model_relevant_via_count": 2,
            "terminal_owned_unpaired_count": 2,
            "terminal_owned_declared_count": 2,
            "terminal_owned_observed_count": 2,
        }
    )
    certificate = _resigned(certificate)
    project = _project(certificate)
    terminal_certificate = project.metadata["spd_import"][
        "layerwise_device_terminal_via_certificate"
    ]
    owner = next(
        item for item in terminal_certificate["terminals"] if item["net"] == "VDD"
    )
    owner.update(
        {
            "incident_via_id": "VIA_CONTRADICTORY",
            "incident_net": "VDD",
            "incident_padstack": "PS1",
            "incident_opposite_node_id": "NODE_INT_CONTRADICTORY",
            "candidate_count": 1,
            "candidate_via_ids_sha256": _canonical_metadata_sha256(
                ("VIA_CONTRADICTORY",)
            ),
            "status": "complete",
            "issues": [],
        }
    )
    terminal_certificate.update(
        {
            "complete_terminal_count": 2,
            "incomplete_terminal_count": 0,
            "status": "complete",
        }
    )
    unsigned = {
        key: value
        for key, value in terminal_certificate.items()
        if key != "evidence_sha256"
    }
    terminal_certificate["evidence_sha256"] = _canonical_metadata_sha256(
        unsigned
    )
    port, network = _network(certificate)
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._prove_v3_terminal_island_components(
            certificate,
            _rail(),
            "GND",
            port,
            network,
            (_pin("U1:P1"), _pin("U1:G1")),
            (_branch("B1", "U1:P1"),),
            project=project,
        )
    assert error.value.code == "DEVICE_TERMINAL_TRACE_OWNER_MISMATCH"


def test_v3_terminal_proof_rejects_branch_without_series_path_evidence() -> None:
    certificate = _certificate(
        equivalent_power_islands=False,
        include_full_v3_contract=True,
    )
    project = _project(certificate)
    port, network = _network(certificate)
    branch = SimpleNamespace(
        branch_id="B1",
        source_power_pin_id="U1:P1",
        source_ground_pin_id="U1:G1",
    )
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._prove_v3_terminal_island_components(
            certificate,
            _rail(),
            "GND",
            port,
            network,
            (_pin("U1:P1"), _pin("U1:G1")),
            (branch,),
            project=project,
        )
    assert error.value.code == "DEVICE_TERMINAL_VIA_SERIES_PATH_UNBOUND"


def test_v3_terminal_owned_via_rows_are_provenance_only_not_substrate_stamps() -> None:
    certificate = {
        "via_island_pair_aggregates": [
            {
                "net": "VDD",
                "padstack": "PS1",
                "start_layer": "TOP",
                "end_layer": "BOT",
                "start_island_id": P1,
                "end_island_id": G1,
                "count": 1,
                "via_ids_sha256": "d" * 64,
                "terminal_owned_count": 1,
                "substrate_count": 0,
            }
        ],
        "terminal_landing_contacts": [
            _landing(
                via_id="VIA_P1",
                external="NODE_EXT_P1",
                internal="NODE_INT_P1",
                owner="device",
                component=_component("VDD", "TOP", [P1]),
                external_layer="EXT_TOP",
            )
        ],
    }
    project = SimpleNamespace(stackup_layers=_stackup())
    links, counts = layerwise_network._compile_v3_via_island_links(
        project, {P1, G1}, certificate
    )
    assert links == ()
    assert counts["terminal_owned_via_count"] == 1
    assert counts["finite_parallel_rl"] == 0


def test_v3_substrate_aggregate_stamps_only_positive_substrate_population() -> None:
    certificate = {
        "via_island_pair_aggregates": [
            {
                "net": "VDD",
                "padstack": "PS1",
                "start_layer": "TOP",
                "end_layer": "BOT",
                "start_island_id": P1,
                "end_island_id": G1,
                "count": 3,
                "via_ids_sha256": "e" * 64,
                "terminal_owned_count": 1,
                "substrate_count": 2,
                "drill_diameter_um": 100.0,
                "material": "COPPER",
                "segments": [
                    {
                        "ordinal": 0,
                        "start_layer": "TOP",
                        "end_layer": "BOT",
                        "length_um": 120.0,
                    }
                ],
                "physical_model_status": "complete",
                "physical_model_issues": [],
            }
        ]
    }
    project = SimpleNamespace(stackup_layers=_stackup())
    links, counts = layerwise_network._compile_v3_via_island_links(
        project, {P1, G1}, certificate
    )
    assert len(links) == 1
    assert links[0].mode == "finite_parallel_rl"
    assert links[0].count == 2
    assert links[0].resistance_ohm_per_via > 0.0
    assert links[0].inductance_h_per_via > 0.0
    assert counts["substrate_via_count"] == 2


def test_v3_substrate_aggregate_rejects_length_changed_from_current_stackup() -> None:
    certificate = {
        "via_island_pair_aggregates": [
            {
                "net": "VDD",
                "padstack": "PS1",
                "start_layer": "TOP",
                "end_layer": "BOT",
                "start_island_id": P1,
                "end_island_id": G1,
                "count": 1,
                "via_ids_sha256": "f" * 64,
                "terminal_owned_count": 0,
                "substrate_count": 1,
                "drill_diameter_um": 100.0,
                "material": None,
                "segments": [
                    {
                        "ordinal": 0,
                        "start_layer": "TOP",
                        "end_layer": "BOT",
                        "length_um": 121.0,
                    }
                ],
                "physical_model_status": "complete",
                "physical_model_issues": [],
            }
        ]
    }
    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._compile_v3_via_island_links(
            SimpleNamespace(stackup_layers=_stackup()), {P1, G1}, certificate
        )
    assert error.value.code == "VIA_SEGMENT_STACKUP_MISMATCH"
