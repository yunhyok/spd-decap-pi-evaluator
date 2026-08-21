"""Contracts for applying a v4 scenario plan to the finite layer network.

These tests intentionally exercise the final compiled network rather than only
the declarative :mod:`scenario_topology_plan` output.  The production adapter
is expected to expose::

    compile_layerwise_scenario_network(
        base_substrate=LayerwiseNetworkSubstrate,
        plan=ScenarioTopologyPlan,
        cap_models=Mapping[str, ImpedanceModel],
    )

The returned object may be a ``LayerwiseNetworkSubstrate`` directly or a small
binding object exposing it through ``substrate``.  In either case the compiled
network, mounted-termination manifest, and scenario identity must be visible.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import json
from typing import Any, Mapping

import numpy as np

from spd_decap_pi._core.models.impedance import ConstantImpedanceModel
from spd_decap_pi._core.solver.layer_surface_network import (
    CompiledLayerSurfaceNetwork,
    LayerSurfacePort,
    LayerSurfaceViaLink,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.layerwise_network import LayerwiseNetworkSubstrate
from spd_decap_pi._core.solver.modal import DielectricDispersion
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
)
from spd_decap_pi._core.solver.uniform_c00 import DispersiveAdjacentGap
from spd_decap_pi.scenario_topology_plan import (
    SCENARIO_TOPOLOGY_PLAN_SCHEMA,
    ScenarioCapBodyRequest,
    ScenarioRetargetRoute,
    ScenarioTerminalNodes,
    ScenarioTopologyLink,
    ScenarioTopologyPlan,
    SourceContactDisposition,
    SourceContactOwnerPartition,
    TopologyLinkKind,
)

try:
    from spd_decap_pi.layerwise_scenario_topology import (
        _canonical_sequence_sha256 as _streamed_sequence_sha256,
        _link_manifest,
        compile_layerwise_scenario_network,
    )
except ModuleNotFoundError:  # Deliberate red contract until the adapter lands.
    compile_layerwise_scenario_network = None  # type: ignore[assignment]


SOURCE_SHA = "1" * 64
CONNECTION_SHA = "2" * 64
CONTACT_MANIFEST_SHA = "3" * 64
RETARGET_MANIFEST_SHA = "4" * 64
ROUTE_CERT_SHA = "5" * 64
LINK_EVIDENCE_SHA = "6" * 64
BASE_IDENTITY_SHA = "7" * 64

R1_NODE = "surface:R1:VDD"
R2_NODE = "surface:R2:VDD2"
GROUND_NODE = "surface:DGND"


def _canonical_sha256(payload: object) -> str:
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _finish_plan(plan: ScenarioTopologyPlan) -> ScenarioTopologyPlan:
    payload = asdict(plan)
    payload.pop("plan_sha256")
    return replace(plan, plan_sha256=_canonical_sha256(payload))


def _plan(
    *,
    terminal_nodes: tuple[ScenarioTerminalNodes, ...],
    active_links: tuple[ScenarioTopologyLink, ...] = (),
    retargets: tuple[ScenarioRetargetRoute, ...] = (),
    bodies: tuple[ScenarioCapBodyRequest, ...] = (),
    suppressed: tuple[str, ...] = (),
    partition: tuple[SourceContactOwnerPartition, ...] = (),
) -> ScenarioTopologyPlan:
    display_key = lambda value: (value.casefold(), value)
    provisional = ScenarioTopologyPlan(
        schema_version=SCENARIO_TOPOLOGY_PLAN_SCHEMA,
        source_sha256=SOURCE_SHA,
        connection_evidence_sha256=CONNECTION_SHA,
        source_contact_manifest_sha256=CONTACT_MANIFEST_SHA,
        retarget_route_manifest_sha256=RETARGET_MANIFEST_SHA,
        finite_route_certificate_sha256=ROUTE_CERT_SHA,
        terminal_nodes=tuple(
            sorted(terminal_nodes, key=lambda item: display_key(item.refdes))
        ),
        active_topology_links=tuple(
            sorted(active_links, key=lambda item: display_key(item.link_id))
        ),
        active_retarget_routes=tuple(
            sorted(retargets, key=lambda item: display_key(item.route_id))
        ),
        cap_body_requests=tuple(
            sorted(bodies, key=lambda item: display_key(item.refdes))
        ),
        suppressed_base_cut_ids=tuple(sorted(suppressed, key=display_key)),
        source_owner_partition=tuple(
            sorted(partition, key=lambda item: display_key(item.via_id))
        ),
        plan_sha256="0" * 64,
    )
    return _finish_plan(provisional)


def _terminal(refdes: str) -> ScenarioTerminalNodes:
    return ScenarioTerminalNodes(
        refdes=refdes,
        power_node_id=f"scenario:{refdes}:PWR",
        ground_node_id=f"scenario:{refdes}:GND",
    )


def _topology_link(
    link_id: str,
    kind: TopologyLinkKind,
    first: str,
    second: str,
) -> ScenarioTopologyLink:
    return ScenarioTopologyLink(
        link_id=link_id,
        kind=kind,
        first_node_id=first,
        second_node_id=second,
        owner_ids=(f"topology-owner:{link_id}",),
        evidence_sha256=LINK_EVIDENCE_SHA,
    )


def _contact_link(
    refdes: str,
    terminal: str,
    exposed_node: str,
    via_id: str,
) -> ScenarioTopologyLink:
    node = f"scenario:{refdes}:{terminal}"
    kind = (
        TopologyLinkKind.SOURCE_PWR_CONTACT
        if terminal == "PWR"
        else TopologyLinkKind.SOURCE_GND_CONTACT
    )
    return ScenarioTopologyLink(
        link_id=f"contact:{refdes}:{terminal}",
        kind=kind,
        first_node_id=node,
        second_node_id=exposed_node,
        owner_ids=(f"contact-owner:{via_id}",),
        evidence_sha256=LINK_EVIDENCE_SHA,
    )


def _partition(
    via_id: str,
    terminal: str,
    disposition: SourceContactDisposition,
) -> SourceContactOwnerPartition:
    return SourceContactOwnerPartition(
        via_id=via_id,
        terminal=terminal,  # type: ignore[arg-type]
        contact_owner_id=f"contact-owner:{via_id}",
        cut_link_id=f"cut:{via_id}",
        cut_owner_id=f"via:{via_id}",
        disposition=disposition,
    )


def _partial() -> DispersiveAdjacentGap:
    matrix = np.asarray([[2.0e-9, -2.0e-9], [-2.0e-9, 2.0e-9]])
    return DispersiveAdjacentGap(
        partial=AdjacentGapMaxwellPartial(
            upper_layer="TOP",
            lower_layer="PWR1",
            nominal_relative_permittivity=4.0,
            net_names=(R1_NODE, GROUND_NODE),
            maxwell_capacitance_f=matrix,
        ),
        dispersion=DielectricDispersion(
            frequencies_hz=(1.0e6,),
            relative_permittivities=(4.0,),
            loss_tangents=(0.0,),
        ),
    )


def _base_substrate(
    via_specs: tuple[tuple[str, str, str, str], ...],
    *,
    reverse_input_order: bool = False,
    resistance_ohm: float = 0.01,
) -> LayerwiseNetworkSubstrate:
    extra_nodes = {
        node
        for _link_id, first, second, _owner in via_specs
        for node in (first, second)
    }
    nodes = tuple(sorted({R1_NODE, R2_NODE, GROUND_NODE, *extra_nodes}))
    links = tuple(
        LayerSurfaceViaLink(
            link_id=link_id,
            first_node_id=first,
            second_node_id=second,
            count=1,
            mode="finite_parallel_rl",
            resistance_ohm_per_via=resistance_ohm,
            inductance_h_per_via=0.5e-9,
            owner_ids=(owner,),
        )
        for link_id, first, second, owner in via_specs
    )
    ports = (
        LayerSurfacePort("R1", R1_NODE, GROUND_NODE),
        LayerSurfacePort("R2", R2_NODE, GROUND_NODE),
    )
    if reverse_input_order:
        nodes = tuple(reversed(nodes))
        links = tuple(reversed(links))
        ports = tuple(reversed(ports))
    network = compile_layer_surface_network(
        nodes,
        partials=(_partial(),),
        via_links=links,
        ports=ports,
    )
    port_by_id = {port.port_id: port for port in network.ports}
    return LayerwiseNetworkSubstrate(
        network=network,
        port_by_rail_key={"r1": port_by_id["R1"], "r2": port_by_id["R2"]},
        selected_net_by_rail_key={"r1": "VDD", "r2": "VDD2"},
        reference_net_by_rail_key={"r1": "DGND", "r2": "DGND"},
        layer_blocks=(("TOP", "PWR1", "PWR2"),),
        substrate_identity_sha256=BASE_IDENTITY_SHA,
        provenance={
            "source_sha256": SOURCE_SHA,
            "finite_route_certificate_sha256": ROUTE_CERT_SHA,
            "finite_via_certificate_sha256": ROUTE_CERT_SHA,
        },
    )


def _compile(
    base: LayerwiseNetworkSubstrate,
    plan: ScenarioTopologyPlan,
    cap_models: Mapping[str, ConstantImpedanceModel] | None = None,
) -> Any:
    assert compile_layerwise_scenario_network is not None, (
        "TODO: implement spd_decap_pi.layerwise_scenario_topology."
        "compile_layerwise_scenario_network"
    )
    return compile_layerwise_scenario_network(
        base_substrate=base,
        plan=plan,
        cap_models=dict(cap_models or {}),
    )


def _substrate(result: Any) -> Any:
    return getattr(result, "substrate", result)


def _network(result: Any) -> CompiledLayerSurfaceNetwork:
    network = getattr(_substrate(result), "network", None)
    assert isinstance(network, CompiledLayerSurfaceNetwork), (
        "scenario compiler must expose its CompiledLayerSurfaceNetwork"
    )
    return network


def _manifest(result: Any) -> Any | None:
    direct = getattr(result, "termination_manifest", None)
    if direct is not None:
        return direct
    return getattr(_substrate(result), "termination_manifest", None)


def _scenario_identity(result: Any) -> str:
    value = getattr(result, "scenario_identity_sha256", None)
    if value is None:
        value = getattr(_substrate(result), "substrate_identity_sha256", None)
    assert isinstance(value, str) and len(value) == 64
    assert all(character in "0123456789abcdef" for character in value.casefold())
    return value.casefold()


def _manifest_terminal_pairs(result: Any) -> set[tuple[str, str]]:
    manifest = _manifest(result)
    if manifest is None:
        return set()
    return {
        (
            item.source.positive_surface_node_id,
            item.source.negative_surface_node_id,
        )
        for item in manifest.clusters
    }


def _owner_occurrences(network: CompiledLayerSurfaceNetwork, owner_id: str) -> int:
    key = owner_id.casefold()
    return sum(
        owner.casefold() == key
        for link in network.via_links
        for owner in link.owner_ids
    )


def test_a_b_c_shared_chain_middle_gap_does_not_reconnect_a_and_c() -> None:
    via_specs = tuple(
        (
            f"cut:V{terminal}-{refdes}",
            f"base:{refdes}:{terminal}:TOP",
            f"base:{refdes}:{terminal}:DEEP",
            f"via:V{terminal}-{refdes}",
        )
        for refdes in ("A", "C")
        for terminal in ("PWR", "GND")
    )
    base = _base_substrate(via_specs)
    terminals = tuple(_terminal(refdes) for refdes in ("A", "B", "C"))
    # B is an isolation gap: neither A-B nor B-C conductor is present.  A and
    # C keep their own independently source-proven contacts and cap bodies.
    contacts = tuple(
        _contact_link(
            refdes,
            terminal,
            f"base:{refdes}:{terminal}:TOP",
            f"V{terminal}-{refdes}",
        )
        for refdes in ("A", "C")
        for terminal in ("PWR", "GND")
    )
    partitions = tuple(
        _partition(
            f"V{terminal}-{refdes}",
            terminal,
            SourceContactDisposition.ACTIVE_SOURCE_CONTACT,
        )
        for refdes in ("A", "C")
        for terminal in ("PWR", "GND")
    )
    plan = _plan(
        terminal_nodes=terminals,
        active_links=contacts,
        bodies=tuple(
            ScenarioCapBodyRequest(
                refdes=refdes,
                model_id="M1",
                rail_id="R1",
                positive_node_id=f"scenario:{refdes}:PWR",
                negative_node_id=f"scenario:{refdes}:GND",
            )
            for refdes in ("A", "C")
        ),
        partition=partitions,
    )

    compiled = _compile(
        base,
        plan,
        {"M1": ConstantImpedanceModel("M1", 0.05 + 0.0j)},
    )
    network = _network(compiled)

    assert not network.surfaces_share_ideal_node(
        "scenario:A:PWR", "scenario:C:PWR"
    )
    assert not network.surfaces_share_ideal_node(
        "scenario:A:GND", "scenario:C:GND"
    )
    assert _manifest_terminal_pairs(compiled) == {
        ("scenario:A:PWR", "scenario:A:GND"),
        ("scenario:C:PWR", "scenario:C:GND"),
    }


def test_moved_direct_multi_via_replaces_only_eligible_root_owner_exactly_once() -> None:
    base = _base_substrate(
        (
            ("cut:VP1", "base:VP1:TOP", "base:VP1:DEEP", "via:VP1"),
            ("cut:VP2", "base:VP2:TOP", "base:VP2:DEEP", "via:VP2"),
            ("cut:VG1", "base:VG1:TOP", "base:VG1:DEEP", "via:VG1"),
        )
    )
    plan = _plan(
        terminal_nodes=(_terminal("C1"),),
        active_links=(
            _contact_link("C1", "GND", "base:VG1:TOP", "VG1"),
        ),
        retargets=(
            ScenarioRetargetRoute(
                route_id="retarget:VP1",
                via_id="VP1",
                rail_id="R2",
                first_node_id="scenario:C1:PWR",
                target_node_id=R2_NODE,
                route_owner_id="via:VP1",
                target_net="VDD2",
                target_layer="PWR2",
                via_template_id="VT2",
                resistance_ohm=0.012,
                inductance_h=0.44e-9,
                evidence_sha256=LINK_EVIDENCE_SHA,
            ),
        ),
        bodies=(
            ScenarioCapBodyRequest(
                refdes="C1",
                model_id="M1",
                rail_id="R2",
                positive_node_id="scenario:C1:PWR",
                negative_node_id="scenario:C1:GND",
            ),
        ),
        suppressed=("cut:VP1",),
        partition=(
            _partition(
                "VP1", "PWR", SourceContactDisposition.SUPPRESSED_FOR_RETARGET
            ),
            _partition(
                "VP2",
                "PWR",
                SourceContactDisposition.MOVED_CONTACT_REMOVED_BASE_RETAINED,
            ),
            _partition(
                "VG1", "GND", SourceContactDisposition.ACTIVE_SOURCE_CONTACT
            ),
        ),
    )

    compiled = _compile(
        base,
        plan,
        {"M1": ConstantImpedanceModel("M1", 0.05 + 0.0j)},
    )
    network = _network(compiled)
    links = {link.link_id: link for link in network.via_links}

    assert "cut:VP1" not in links
    assert links["retarget:VP1"].mode == "finite_parallel_rl"
    assert links["retarget:VP1"].owner_ids == ("via:VP1",)
    assert links["retarget:VP1"].resistance_ohm_per_via == 0.012
    assert links["retarget:VP1"].inductance_h_per_via == 0.44e-9
    assert _owner_occurrences(network, "via:VP1") == 1

    # The ineligible root keeps its immutable finite base edge/owner, but its
    # source ideal terminal contact is absent, so it is only a detached stub.
    assert links["cut:VP2"].owner_ids == ("via:VP2",)
    assert _owner_occurrences(network, "via:VP2") == 1
    assert not network.surfaces_share_ideal_node(
        "scenario:C1:PWR", "base:VP2:TOP"
    )
    assert not network.surfaces_share_ideal_node("scenario:C1:PWR", R2_NODE)
    assert _manifest_terminal_pairs(compiled) == {
        ("scenario:C1:PWR", "scenario:C1:GND")
    }


def test_dnp_normal_keeps_contacts_while_gap_removes_contacts_and_body() -> None:
    base = _base_substrate(
        (
            ("cut:VP1", "base:VP1:TOP", "base:VP1:DEEP", "via:VP1"),
            ("cut:VG1", "base:VG1:TOP", "base:VG1:DEEP", "via:VG1"),
        )
    )
    terminal = _terminal("C1")
    dnp = _plan(
        terminal_nodes=(terminal,),
        active_links=(
            _contact_link("C1", "PWR", "base:VP1:TOP", "VP1"),
            _contact_link("C1", "GND", "base:VG1:TOP", "VG1"),
        ),
        partition=(
            _partition(
                "VP1", "PWR", SourceContactDisposition.ACTIVE_SOURCE_CONTACT
            ),
            _partition(
                "VG1", "GND", SourceContactDisposition.ACTIVE_SOURCE_CONTACT
            ),
        ),
    )
    gap = _plan(
        terminal_nodes=(terminal,),
        partition=(
            _partition(
                "VP1", "PWR", SourceContactDisposition.CONTACT_REMOVED_BY_GAP
            ),
            _partition(
                "VG1", "GND", SourceContactDisposition.CONTACT_REMOVED_BY_GAP
            ),
        ),
    )

    compiled_dnp = _compile(base, dnp)
    compiled_gap = _compile(base, gap)
    dnp_network = _network(compiled_dnp)
    gap_network = _network(compiled_gap)

    assert dnp_network.surfaces_share_ideal_node(
        "scenario:C1:PWR", "base:VP1:TOP"
    )
    assert dnp_network.surfaces_share_ideal_node(
        "scenario:C1:GND", "base:VG1:TOP"
    )
    assert not gap_network.surfaces_share_ideal_node(
        "scenario:C1:PWR", "base:VP1:TOP"
    )
    assert not gap_network.surfaces_share_ideal_node(
        "scenario:C1:GND", "base:VG1:TOP"
    )
    assert _manifest_terminal_pairs(compiled_dnp) == set()
    assert _manifest_terminal_pairs(compiled_gap) == set()


def test_scenario_identity_is_stable_under_base_input_permutation() -> None:
    via_specs = (
        ("cut:VP1", "base:VP1:TOP", "base:VP1:DEEP", "via:VP1"),
        ("cut:VG1", "base:VG1:TOP", "base:VG1:DEEP", "via:VG1"),
    )
    first = _base_substrate(via_specs, reverse_input_order=False)
    permuted = _base_substrate(via_specs, reverse_input_order=True)
    plan = _plan(
        terminal_nodes=(_terminal("C1"),),
        active_links=(
            _contact_link("C1", "PWR", "base:VP1:TOP", "VP1"),
            _contact_link("C1", "GND", "base:VG1:TOP", "VG1"),
        ),
        bodies=(
            ScenarioCapBodyRequest(
                refdes="C1",
                model_id="M1",
                rail_id="R1",
                positive_node_id="scenario:C1:PWR",
                negative_node_id="scenario:C1:GND",
            ),
        ),
        partition=(
            _partition(
                "VP1", "PWR", SourceContactDisposition.ACTIVE_SOURCE_CONTACT
            ),
            _partition(
                "VG1", "GND", SourceContactDisposition.ACTIVE_SOURCE_CONTACT
            ),
        ),
    )
    cap = ConstantImpedanceModel("M1", 0.05 + 0.0j)

    compiled_first = _compile(first, plan, {"M1": cap})
    compiled_permuted = _compile(permuted, plan, {"M1": cap})

    assert _scenario_identity(compiled_first) == _scenario_identity(
        compiled_permuted
    )
    assert _manifest(compiled_first).manifest_sha256 == _manifest(
        compiled_permuted
    ).manifest_sha256


def test_streamed_link_manifest_matches_canonical_sequence_and_is_sensitive() -> None:
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
    rows = [_link_manifest(link) for link in links]

    streamed = _streamed_sequence_sha256(iter(rows))

    assert streamed == _canonical_sha256(rows)
    changed = [dict(rows[0]), dict(rows[1])]
    changed[1]["resistance_ohm_per_via"] = 0.021
    assert _streamed_sequence_sha256(iter(changed)) != streamed


def test_scenario_identity_changes_when_one_finite_link_value_changes() -> None:
    via_specs = (
        ("cut:VP1", "base:VP1:TOP", "base:VP1:DEEP", "via:VP1"),
        ("cut:VG1", "base:VG1:TOP", "base:VG1:DEEP", "via:VG1"),
    )
    first = _base_substrate(via_specs, resistance_ohm=0.01)
    changed = _base_substrate(via_specs, resistance_ohm=0.02)
    plan = _plan(terminal_nodes=())

    first_binding = _compile(first, plan)
    changed_binding = _compile(changed, plan)

    assert _scenario_identity(first_binding) != _scenario_identity(
        changed_binding
    )
