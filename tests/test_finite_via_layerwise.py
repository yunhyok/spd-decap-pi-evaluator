from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from spd_decap_pi._core.solver.finite_via_layerwise import (
    FINITE_VIA_QUOTIENT_SCHEMA,
    FINITE_VIA_SURFACE_COMPILER,
    FINITE_VIA_SURFACE_SCHEMA,
    FiniteViaCertificateError,
    _canonical_sequence_sha256 as _streamed_sequence_sha256,
    _finite_link_identity_manifest,
    compiled_finite_via_topology_identity_sha256,
    compile_finite_via_base_topology,
)
from spd_decap_pi._core.solver.layer_surface_network import LayerSurfaceViaLink


SOURCE_SHA = "1" * 64
ASSET_SHA = "2" * 64
COMPONENT_SHA = "3" * 64
ROW_SHA = "4" * 64

PWR_ARTWORK = "artwork:VDD:L3:1"
GND_ARTWORK = "artwork:DGND:L4:1"
PWR_TOP = "spd-finite-via-vertex:pwr-top"
PWR_SURFACE = "spd-finite-via-vertex:pwr-surface"
GND_TOP = "spd-finite-via-vertex:gnd-top"
GND_SURFACE = "spd-finite-via-vertex:gnd-surface"
PWR_EDGE = "spd-finite-via-edge:pwr"
GND_EDGE = "spd-finite-via-edge:gnd"


def _canonical_sha256(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _assignment_sha256(assignments: list[tuple[str, str]]) -> str:
    digest = sha256()
    for owner, edge_id in sorted(
        ((owner.casefold(), edge_id) for owner, edge_id in assignments),
        key=lambda item: (item[0], item[1].casefold(), item[1]),
    ):
        for token in (owner, edge_id.casefold()):
            encoded = token.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def _owner_sha256(assignments: list[tuple[str, str]]) -> str:
    digest = sha256()
    for owner in sorted(owner.casefold() for owner, _edge_id in assignments):
        encoded = owner.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _vertex(
    vertex_id: str,
    net: str,
    layer: str,
    *,
    component_id: str | None = None,
    artwork_id: str | None = None,
) -> dict[str, object]:
    retained_ids = [] if component_id is None else [component_id]
    retained_hashes = [] if component_id is None else [COMPONENT_SHA]
    retained_by_layer = {} if artwork_id is None else {layer: [artwork_id]}
    return {
        "vertex_id": vertex_id,
        "net": net,
        "layer": layer,
        "source_node_count": 1,
        "source_node_ids_sha256": ROW_SHA,
        "component_binding_status": "complete",
        "component_binding_issues": [],
        "retained_component_ids": retained_ids,
        "retained_component_evidence_sha256s": retained_hashes,
        "retained_component_island_ids_by_layer": retained_by_layer,
    }


def _edge(
    edge_id: str,
    net: str,
    first: str,
    second: str,
    owner: str,
) -> dict[str, object]:
    return {
        "edge_id": edge_id,
        "net": net,
        "start_vertex_id": first,
        "end_vertex_id": second,
        "parallel_path_count": 1,
        "per_path_via_count": 1,
        "raw_via_count": 1,
        "resistance_ohm": 0.006,
        "inductance_h": 0.31e-9,
        "owner_ids": [owner],
        "status": "complete",
        "physical_model_status": "complete",
        "physical_model_issues": [],
        "endpoint_binding_status": "complete",
        "endpoint_binding_issues": [],
    }


def _signed_certificate() -> dict[str, object]:
    assignments = [("via:VP1", PWR_EDGE), ("via:VG1", GND_EDGE)]
    certificate: dict[str, object] = {
        "schema_version": FINITE_VIA_SURFACE_SCHEMA,
        "compiler_id": FINITE_VIA_SURFACE_COMPILER,
        "source_sha256": SOURCE_SHA,
        "geometry_assets": [
            {
                "layer": "L3",
                "net": "VDD",
                "asset": "pwr.bin",
                "asset_sha256": ASSET_SHA,
                "island_ids": [PWR_ARTWORK],
            },
            {
                "layer": "L4",
                "net": "DGND",
                "asset": "gnd.bin",
                "asset_sha256": ASSET_SHA,
                "island_ids": [GND_ARTWORK],
            },
        ],
        "surface_equivalence_components": [
            {
                "component_id": "surface-component:pwr",
                "net": "VDD",
                "layer": "L3",
                "component_evidence_sha256": COMPONENT_SHA,
                "contact_status": "complete",
                "island_ids": [PWR_ARTWORK],
            },
            {
                "component_id": "surface-component:gnd",
                "net": "DGND",
                "layer": "L4",
                "component_evidence_sha256": COMPONENT_SHA,
                "contact_status": "complete",
                "island_ids": [GND_ARTWORK],
            },
        ],
        "finite_via_quotient": {
            "schema_version": FINITE_VIA_QUOTIENT_SCHEMA,
            "status": "complete",
            "vertices": [
                _vertex(PWR_TOP, "VDD", "TOP"),
                _vertex(
                    PWR_SURFACE,
                    "VDD",
                    "L3",
                    component_id="surface-component:pwr",
                    artwork_id=PWR_ARTWORK,
                ),
                _vertex(GND_TOP, "DGND", "TOP"),
                _vertex(
                    GND_SURFACE,
                    "DGND",
                    "L4",
                    component_id="surface-component:gnd",
                    artwork_id=GND_ARTWORK,
                ),
            ],
            "edges": [
                _edge(PWR_EDGE, "VDD", PWR_TOP, PWR_SURFACE, "via:VP1"),
                _edge(GND_EDGE, "DGND", GND_TOP, GND_SURFACE, "via:VG1"),
            ],
            "terminal_bindings": [
                {
                    "landing_key": ["vp1", "node-pwr-top"],
                    "exposed_quotient_vertex_id": PWR_TOP,
                    "first_via_quotient_edge_id": PWR_EDGE,
                    "first_via_owner_id": "via:VP1",
                    "global_quotient_binding_status": "complete",
                },
                {
                    "landing_key": ["vg1", "node-gnd-top"],
                    "exposed_quotient_vertex_id": GND_TOP,
                    "first_via_quotient_edge_id": GND_EDGE,
                    "first_via_owner_id": "via:VG1",
                    "global_quotient_binding_status": "complete",
                },
            ],
            "coverage": {
                "status": "complete",
                "owner_ledger_status": "complete",
                "raw_target_via_count": 2,
                "modeled_global_via_count": 2,
                "outside_scope_via_count": 0,
                "pruned_dangling_via_count": 0,
                "physical_complete_via_count": 2,
                "physical_incomplete_via_count": 0,
                "terminal_exclusive_via_count": 0,
                "modeled_owner_count": 2,
                "modeled_owner_unique_count": 2,
                "raw_target_via_ids_sha256": ROW_SHA,
                "modeled_global_via_ids_sha256": ROW_SHA,
                "modeled_owner_ledger_sha256": ROW_SHA,
                "modeled_owner_canonical_sha256": _owner_sha256(assignments),
                "pruned_dangling_via_ids_sha256": ROW_SHA,
                "modeled_owner_edge_assignment_sha256": (
                    _assignment_sha256(assignments)
                ),
            },
        },
        "rail_anchor_bindings": [
            {
                "rail_id": "R1",
                "branch_id": "SITE0",
                "role": "power",
                "pin_id": "SITE0:1",
            },
            {
                "rail_id": "R1",
                "branch_id": "SITE0",
                "role": "ground",
                "pin_id": "SITE0:2",
            },
        ],
        "terminal_contacts": [
            {
                "pin_id": "SITE0:1",
                "net": "VDD",
                "exposed_quotient_vertex_id": PWR_TOP,
                "status": "complete",
            },
            {
                "pin_id": "SITE0:2",
                "net": "DGND",
                "exposed_quotient_vertex_id": GND_TOP,
                "status": "complete",
            },
        ],
        "status": "complete",
    }
    certificate["evidence_sha256"] = _canonical_sha256(certificate)
    return certificate


def _resign(certificate: dict[str, object]) -> None:
    certificate.pop("evidence_sha256", None)
    certificate["evidence_sha256"] = _canonical_sha256(certificate)


def _inputs(certificate: dict[str, object] | None = None):
    value = certificate or _signed_certificate()
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "source_sha256": SOURCE_SHA,
                "layerwise_surface_connectivity_certificate": value,
            }
        },
        rails=(SimpleNamespace(rail_id="R1", net="VDD"),),
        gnd_aliases=("DGND",),
    )
    geometry = deepcopy(value["geometry_assets"])
    return project, geometry, (PWR_ARTWORK, GND_ARTWORK)


def test_complete_v4_certificate_decodes_base_topology_and_external_device_port() -> None:
    project, geometry, artwork = _inputs()
    source_certificate = project.metadata["spd_import"][
        "layerwise_surface_connectivity_certificate"
    ]
    before = _canonical_sha256(source_certificate)

    compiled = compile_finite_via_base_topology(
        project,
        geometry,
        artwork,
        required_rail_id="R1",
    )

    assert compiled.source_sha256 == SOURCE_SHA
    assert len(compiled.quotient_vertex_ids) == 4
    assert len(compiled.topology_links) == 2
    assert len(compiled.finite_links) == 2
    assert len(compiled.via_links) == 4
    assert compiled.vertex_by_landing_key == {
        ("vp1", "node-pwr-top"): PWR_TOP,
        ("vg1", "node-gnd-top"): GND_TOP,
    }
    assert compiled.first_edge_by_landing_key == {
        ("vp1", "node-pwr-top"): PWR_EDGE,
        ("vg1", "node-gnd-top"): GND_EDGE,
    }
    assert compiled.omitted_rail_ids == ()
    assert len(compiled.rail_ports) == 1
    external = compiled.rail_ports[0]
    assert external.rail_id == "R1"
    assert external.selected_net == "VDD"
    assert external.reference_net == "DGND"
    assert external.positive_node_id == PWR_TOP
    assert external.negative_node_id == GND_TOP
    assert external.positive_pin_ids == ("SITE0:1",)
    assert external.negative_pin_ids == ("SITE0:2",)
    assert external.port.positive_node_id == PWR_TOP
    assert external.port.negative_node_id == GND_TOP
    assert _canonical_sha256(source_certificate) == before
    assert compiled.certificate["finite_via_quotient"]["vertices"][0] == (
        source_certificate["finite_via_quotient"]["vertices"][0]
    )
    with pytest.raises(TypeError):
        compiled.certificate["status"] = "changed"


def test_direct_trace_terminal_binding_accepts_null_first_via_fields() -> None:
    certificate = _signed_certificate()
    quotient = certificate["finite_via_quotient"]
    binding = quotient["terminal_bindings"][0]
    binding["landing_key"] = [
        "source-node:node-pwr-top",
        "node-pwr-top",
    ]
    binding["first_via_quotient_edge_id"] = None
    binding["first_via_owner_id"] = None
    _resign(certificate)
    project, geometry, artwork = _inputs(certificate)

    compiled = compile_finite_via_base_topology(
        project,
        geometry,
        artwork,
        required_rail_id="R1",
    )

    landing_key = ("source-node:node-pwr-top", "node-pwr-top")
    assert compiled.vertex_by_landing_key[landing_key] == PWR_TOP
    assert landing_key not in compiled.first_edge_by_landing_key


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("first_via_quotient_edge_id", PWR_EDGE),
        ("first_via_owner_id", "via:VP1"),
    ),
)
def test_direct_trace_terminal_binding_rejects_nonblank_first_via_fields(
    field: str,
    value: str,
) -> None:
    certificate = _signed_certificate()
    quotient = certificate["finite_via_quotient"]
    binding = quotient["terminal_bindings"][0]
    binding["landing_key"] = [
        "source-node:node-pwr-top",
        "node-pwr-top",
    ]
    binding["first_via_quotient_edge_id"] = None
    binding["first_via_owner_id"] = None
    binding[field] = value
    _resign(certificate)
    project, geometry, artwork = _inputs(certificate)

    with pytest.raises(FiniteViaCertificateError) as error:
        compile_finite_via_base_topology(
            project,
            geometry,
            artwork,
            required_rail_id="R1",
        )

    assert error.value.code == "TERMINAL_BINDING_INVALID"


def test_streamed_finite_link_manifest_matches_canonical_sequence() -> None:
    links = (
        LayerSurfaceViaLink(
            "a",
            "n1",
            "n2",
            1,
            "finite_parallel_rl",
            0.01,
            0.2e-9,
            owner_ids=("via:a",),
        ),
        LayerSurfaceViaLink(
            "b",
            "n2",
            "n3",
            2,
            "finite_parallel_rl",
            0.02,
            0.5e-9,
            owner_ids=("via:b1", "via:b2"),
        ),
    )
    rows = [_finite_link_identity_manifest(link) for link in links]

    streamed = _streamed_sequence_sha256(iter(rows))

    assert streamed == _canonical_sha256(rows)
    changed = [dict(rows[0]), dict(rows[1])]
    changed[0]["owners"] = ["via:changed"]
    assert _streamed_sequence_sha256(iter(changed)) != streamed


def test_base_topology_identity_changes_with_finite_link_physics() -> None:
    first_project, first_geometry, artwork = _inputs()
    first = compile_finite_via_base_topology(
        first_project, first_geometry, artwork
    )
    changed_certificate = _signed_certificate()
    changed_certificate["finite_via_quotient"]["edges"][0][
        "resistance_ohm"
    ] = 0.0065
    _resign(changed_certificate)
    changed_project, changed_geometry, artwork = _inputs(changed_certificate)
    changed = compile_finite_via_base_topology(
        changed_project, changed_geometry, artwork
    )

    assert first.topology_identity_sha256 != changed.topology_identity_sha256


def test_base_topology_identity_binds_topology_link_endpoints_and_owners() -> None:
    def identity(link: LayerSurfaceViaLink) -> str:
        return compiled_finite_via_topology_identity_sha256(
            certificate_evidence_sha256="a" * 64,
            artwork_node_ids=("artwork:a", "artwork:b", "artwork:c"),
            quotient_vertex_ids=(),
            external_port_node_ids=(),
            topology_links=(link,),
            finite_links=(),
            rail_ports=(),
            omitted_rail_ids=(),
        )

    baseline = identity(
        LayerSurfaceViaLink(
            "topology:1",
            "artwork:a",
            "artwork:b",
            1,
            "topology_only_ideal",
            owner_ids=("surface-owner:a",),
        )
    )
    changed_endpoint = identity(
        LayerSurfaceViaLink(
            "topology:1",
            "artwork:a",
            "artwork:c",
            1,
            "topology_only_ideal",
            owner_ids=("surface-owner:a",),
        )
    )
    changed_owner = identity(
        LayerSurfaceViaLink(
            "topology:1",
            "artwork:a",
            "artwork:b",
            1,
            "topology_only_ideal",
            owner_ids=("surface-owner:changed",),
        )
    )

    assert len({baseline, changed_endpoint, changed_owner}) == 3


def test_topology_identity_stream_honors_mid_sequence_cancellation() -> None:
    cancellation_checks = 0

    def cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 2

    with pytest.raises(FiniteViaCertificateError) as error:
        compiled_finite_via_topology_identity_sha256(
            certificate_evidence_sha256="a" * 64,
            artwork_node_ids=("artwork:a",),
            quotient_vertex_ids=tuple(
                f"quotient:{ordinal}" for ordinal in range(10_001)
            ),
            external_port_node_ids=(),
            topology_links=(),
            finite_links=(),
            rail_ports=(),
            omitted_rail_ids=(),
            is_cancelled=cancelled,
        )

    assert error.value.code == "COMPILED_TOPOLOGY_CANCELLED"
    assert cancellation_checks == 2


def test_raw_via_owner_ledger_is_exactly_once_and_bound_to_the_first_edge() -> None:
    project, geometry, artwork = _inputs()

    compiled = compile_finite_via_base_topology(project, geometry, artwork)

    assert compiled.owner_edge_by_id == {
        "via:vp1": PWR_EDGE,
        "via:vg1": GND_EDGE,
    }
    assert {
        owner.casefold()
        for link in compiled.finite_links
        for owner in link.owner_ids
    } == set(compiled.owner_edge_by_id)
    with pytest.raises(TypeError):
        compiled.owner_edge_by_id["via:new"] = PWR_EDGE  # type: ignore[index]

    duplicated = _signed_certificate()
    quotient = duplicated["finite_via_quotient"]
    quotient["edges"][1]["owner_ids"] = ["via:VP1"]
    _resign(duplicated)
    project, geometry, artwork = _inputs(duplicated)
    with pytest.raises(FiniteViaCertificateError) as error:
        compile_finite_via_base_topology(project, geometry, artwork)
    assert error.value.code == "FINITE_OWNER_LEDGER_INVALID"

    stale_binding = _signed_certificate()
    quotient = stale_binding["finite_via_quotient"]
    quotient["terminal_bindings"][0]["first_via_quotient_edge_id"] = GND_EDGE
    _resign(stale_binding)
    project, geometry, artwork = _inputs(stale_binding)
    with pytest.raises(FiniteViaCertificateError) as error:
        compile_finite_via_base_topology(project, geometry, artwork)
    assert error.value.code == "TERMINAL_BINDING_INVALID"


def test_external_device_port_net_tamper_fails_closed() -> None:
    tampered = _signed_certificate()
    tampered["terminal_contacts"][0]["net"] = "VDD_WRONG"
    _resign(tampered)
    project, geometry, artwork = _inputs(tampered)

    with pytest.raises(FiniteViaCertificateError) as error:
        compile_finite_via_base_topology(
            project,
            geometry,
            artwork,
            required_rail_id="R1",
        )

    assert error.value.code == "RAIL_PORT_NET_MISMATCH"


def test_unsigned_nested_certificate_tamper_fails_outer_integrity_check() -> None:
    tampered = _signed_certificate()
    tampered["finite_via_quotient"]["edges"][0]["resistance_ohm"] = 99.0
    project, geometry, artwork = _inputs(tampered)

    with pytest.raises(FiniteViaCertificateError) as error:
        compile_finite_via_base_topology(project, geometry, artwork)

    assert error.value.code == "CERTIFICATE_INTEGRITY_FAILED"
