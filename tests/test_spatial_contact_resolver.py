from dataclasses import dataclass
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from spd_decap_pi._core.solver.spatial_contact_resolver import (
    SpatialContactResolutionError,
    resolve_spatial_contacts,
)
from spd_decap_pi._core.solver.finite_via_layerwise import (
    FINITE_VIA_SURFACE_COMPILER,
    FINITE_VIA_SURFACE_SCHEMA,
    compiled_finite_via_topology_identity_sha256,
)


HASH = "a" * 64


def _canonical_hash(value):
    return sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class Link:
    link_id: str
    first_node_id: str
    second_node_id: str
    owner_ids: tuple[str, ...] = ()
    count: int = 1
    mode: str = "finite_parallel_rl"
    resistance_ohm_per_via: float = 0.01
    inductance_h_per_via: float = 1e-9


def _fixture(*, retarget: bool = False, owner: tuple[str, ...] = ("via:V1",)):
    path = SimpleNamespace(
        target_layer="L2", target_node_id="n2", target_pad_kind="circle",
        target_pad_width_um=40.0, target_pad_height_um=40.0,
    )
    landing = SimpleNamespace(
        via_id="V1", net="VDD", endpoint_node_id="n1", x_um=10.0, y_um=20.0,
        rotation_degrees=17.5, path_evidence=(path,),
    )
    connection = SimpleNamespace(
        refdes="C1", kind="DIRECT", cluster_id=None,
        power_vias=(landing,), ground_vias=(),
    )
    decap = SimpleNamespace(
        refdes="C1", current_net="VDD", pwr_pad=SimpleNamespace(),
        gnd_pad=SimpleNamespace(), current_rail_id="R1",
    )
    cert = {
        "schema_version": FINITE_VIA_SURFACE_SCHEMA,
        "compiler_id": FINITE_VIA_SURFACE_COMPILER,
        "source_sha256": HASH,
        "status": "complete",
        "geometry_assets": [{
            "layer": "L2", "net": "VDD", "asset": "g.bin",
            "asset_sha256": "b" * 64,
            "island_ids": ["artwork:L2:VDD:1"],
        }],
        "terminal_landing_contacts": [{
            "via_id": "V1", "target_layer": "L2", "target_pad_kind": "circle",
            "target_pad_width_um": 40.0, "target_pad_height_um": 40.0,
            "target_island_id": "artwork:L2:VDD:1",
        }],
    }
    vertices = {("v1", "n1"): "q2"}
    edges = {("v1", "n1"): "e1"}
    links = (Link("e1", "q2", "q3", owner),)
    if retarget:
        vertices = {}
        edges = {}
        cert["retarget_destination_bindings"] = [{
            "source_landing_key": ["v1", "n1"],
            "target_layer": "L2", "target_node_id": "n2",
            "destination_vertex_id": "q2", "status": "complete", "issues": [],
        }]
    evidence = _canonical_hash(cert)
    cert["evidence_sha256"] = evidence
    scenario = SimpleNamespace(
        source=SimpleNamespace(sha256=HASH),
        connection_analysis=SimpleNamespace(source_sha256=HASH,
                                             connections={"C1": connection}),
        decaps=(decap,), normalized_project=SimpleNamespace(
            pins=(), gnd_aliases=("GND",),
            rails=(SimpleNamespace(
                rail_id="R1", net="VDD", pwr_layer="L2", gnd_layer="L3"
            ),),
        ),
    )
    topology = SimpleNamespace(
        certificate=cert, certificate_evidence_sha256=evidence, source_sha256=HASH,
        vertex_by_landing_key=vertices, first_edge_by_landing_key=edges,
        quotient_vertex_ids=("q2", "q3"), external_port_node_ids=(),
        topology_links=(), finite_links=links, rail_ports=(), omitted_rail_ids=(),
    )
    topology.topology_identity_sha256 = compiled_finite_via_topology_identity_sha256(
        certificate_evidence_sha256=evidence,
        artwork_node_ids=("artwork:L2:VDD:1",),
        quotient_vertex_ids=topology.quotient_vertex_ids,
        external_port_node_ids=topology.external_port_node_ids,
        topology_links=topology.topology_links,
        finite_links=topology.finite_links,
        rail_ports=topology.rail_ports,
        omitted_rail_ids=topology.omitted_rail_ids,
    )
    return scenario, topology


def _rebind_certificate(topology):
    unsigned = {
        key: value
        for key, value in topology.certificate.items()
        if key != "evidence_sha256"
    }
    evidence = _canonical_hash(unsigned)
    topology.certificate["evidence_sha256"] = evidence
    topology.certificate_evidence_sha256 = evidence
    topology.topology_identity_sha256 = compiled_finite_via_topology_identity_sha256(
        certificate_evidence_sha256=evidence,
        artwork_node_ids=("artwork:L2:VDD:1",),
        quotient_vertex_ids=topology.quotient_vertex_ids,
        external_port_node_ids=topology.external_port_node_ids,
        topology_links=topology.topology_links,
        finite_links=topology.finite_links,
        rail_ports=topology.rail_ports,
        omitted_rail_ids=topology.omitted_rail_ids,
    )


def test_direct_contact_preserves_rotation_and_exact_source_owner():
    scenario, topology = _fixture()
    result = resolve_spatial_contacts(scenario, topology)
    assert len(result.contacts) == 1
    contact = result.contacts[0]
    assert contact.rotation_degrees == 17.5
    assert contact.quotient_vertex_id == "q2"
    assert contact.first_edge_id == "e1"
    assert contact.owner_id == "via:V1"
    assert contact.island_id == "artwork:L2:VDD:1"
    assert result.provenance["substrate_via_spatial_coverage"] is False
    result.verify_current_identity()


def test_retarget_without_complete_source_incidence_fails_closed():
    scenario, topology = _fixture(retarget=True, owner=("via:V1",))
    with pytest.raises(SpatialContactResolutionError, match="TOPOLOGY_INCIDENCE_MISSING"):
        resolve_spatial_contacts(scenario, topology)


def test_missing_or_ambiguous_pad_certificate_fails_closed():
    scenario, topology = _fixture()
    topology.certificate["terminal_landing_contacts"].append({
        "via_id": "V1", "target_layer": "L2", "target_pad_kind": "square",
        "target_pad_width_um": 40.0, "target_pad_height_um": 40.0,
        "target_island_id": "artwork:L2:VDD:1",
    })
    _rebind_certificate(topology)
    with pytest.raises(SpatialContactResolutionError, match="PAD_CERTIFICATE_AMBIGUOUS"):
        resolve_spatial_contacts(scenario, topology)


def test_tampered_certificate_hash_fails_closed():
    scenario, topology = _fixture()
    topology.certificate["evidence_sha256"] = "b" * 64
    with pytest.raises(SpatialContactResolutionError, match="HASH_MISMATCH"):
        resolve_spatial_contacts(scenario, topology)


def test_tampered_certificate_content_with_unchanged_hash_fails_closed():
    scenario, topology = _fixture()
    topology.certificate["terminal_landing_contacts"][0]["target_pad_width_um"] = 41.0
    with pytest.raises(SpatialContactResolutionError, match="EVIDENCE_TAMPERED"):
        resolve_spatial_contacts(scenario, topology)


def test_current_saved_empty_path_evidence_fails_closed():
    scenario, topology = _fixture()
    scenario.connection_analysis.connections["C1"].power_vias[0].path_evidence = ()
    with pytest.raises(SpatialContactResolutionError, match="CONTACT_INCOMPLETE"):
        resolve_spatial_contacts(scenario, topology)


def test_wrong_or_nonexclusive_first_edge_owner_fails_closed():
    scenario, topology = _fixture(owner=("via:OTHER",))
    with pytest.raises(SpatialContactResolutionError, match="OWNER_MISMATCH"):
        resolve_spatial_contacts(scenario, topology)


def test_connection_analysis_must_exactly_cover_decaps():
    scenario, topology = _fixture()
    scenario.connection_analysis.connections.clear()
    with pytest.raises(SpatialContactResolutionError, match="CONTACT_INCOMPLETE"):
        resolve_spatial_contacts(scenario, topology)


def test_contact_buffer_mutation_is_detected():
    scenario, topology = _fixture()
    result = resolve_spatial_contacts(scenario, topology)
    object.__setattr__(result.contacts[0], "x_um", 99.0)
    with pytest.raises(SpatialContactResolutionError, match="CONTACT_IDENTITY_MISMATCH"):
        result.verify_current_identity()
