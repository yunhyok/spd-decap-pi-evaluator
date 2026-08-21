"""Apply one immutable Scenario topology plan to a verified v4 substrate.

The base layerwise substrate owns exact artwork capacitance and every retained
raw Via exactly once.  A Distribution state is a topology transformation over
that base: ideal top-pad contacts may be removed, an eligible first-Via edge
may be replaced by one certified destination route, and populated capacitor
bodies are then stamped between synthetic PWR/GND terminal nodes.

This compiler is intentionally independent of SPD parsing.  The application
adapter builds the typed, hash-bound :class:`ScenarioTopologyPlan`; this module
only verifies and applies it without inventing a nearest surface or silently
dropping an electrical owner.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping, Sequence

from spd_decap_pi._core.models.impedance import ImpedanceModel
from spd_decap_pi._core.solver.layer_surface_network import (
    LayerSurfaceNetworkError,
    LayerSurfaceViaLink,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationBranch,
    LayerSurfaceTerminationCluster,
    LayerSurfaceTerminationError,
    compile_layer_surface_termination_manifest,
)
from spd_decap_pi._core.solver.layerwise_network import (
    LayerwiseNetworkSubstrate,
    LayerwiseNetworkUnavailable,
    LayerwiseScenarioNetworkBinding,
)

from .scenario_topology_plan import (
    SCENARIO_TOPOLOGY_PLAN_SCHEMA,
    ScenarioTopologyPlan,
    SourceContactDisposition,
)


SCENARIO_NETWORK_COMPILER_ID = "layerwise-scenario-topology-global-mna-v2"


class LayerwiseScenarioTopologyError(ValueError):
    """Fail-closed scenario transformation error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code).strip().upper() or "SCENARIO_TOPOLOGY_INVALID"
        super().__init__(f"Layerwise scenario topology [{self.code}]: {message}")


def _key(value: Any, *, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise LayerwiseScenarioTopologyError(
            "IDENTITY_MISSING", f"{label} must not be blank"
        )
    return text.casefold()


def _canonical_json(payload: Any) -> bytes:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LayerwiseScenarioTopologyError(
            "EVIDENCE_NOT_CANONICAL", f"scenario evidence is not canonical JSON: {exc}"
        ) from exc
    return (encoded + "\n").encode("utf-8")


def _sha256(payload: Any) -> str:
    return sha256(_canonical_json(payload)).hexdigest()


def _canonical_sequence_sha256(values: Iterable[Any]) -> str:
    """Hash the exact canonical JSON sequence with O(one row) memory."""

    digest = sha256()
    digest.update(b"[")
    first = True
    for value in values:
        if not first:
            digest.update(b",")
        encoded = _canonical_json(value)
        if not encoded.endswith(b"\n"):
            raise LayerwiseScenarioTopologyError(
                "EVIDENCE_NOT_CANONICAL",
                "canonical scenario row lacks its expected terminator",
            )
        digest.update(encoded[:-1])
        first = False
    digest.update(b"]\n")
    return digest.hexdigest()


def _validate_plan(plan: ScenarioTopologyPlan) -> None:
    if not isinstance(plan, ScenarioTopologyPlan):
        raise LayerwiseScenarioTopologyError(
            "PLAN_INVALID", "scenario topology plan is absent"
        )
    if plan.schema_version != SCENARIO_TOPOLOGY_PLAN_SCHEMA:
        raise LayerwiseScenarioTopologyError(
            "PLAN_SCHEMA_UNSUPPORTED",
            f"unsupported scenario topology schema {plan.schema_version!r}",
        )
    payload = asdict(plan)
    observed = str(payload.pop("plan_sha256", "")).strip().casefold()
    expected = _sha256(payload)
    if observed != expected:
        raise LayerwiseScenarioTopologyError(
            "PLAN_INTEGRITY_FAILED",
            "scenario topology plan SHA-256 does not match its payload",
        )


def _casefold_index(
    values: Sequence[Any], attribute: str, *, label: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        display = str(getattr(value, attribute, "")).strip()
        key = _key(display, label=f"{label} ID")
        if key in result:
            raise LayerwiseScenarioTopologyError(
                "IDENTITY_AMBIGUOUS",
                f"{label} identity {display!r} is duplicated case-insensitively",
            )
        result[key] = value
    return result


def _cap_model_index(
    cap_models: Mapping[str, ImpedanceModel],
) -> dict[str, tuple[str, ImpedanceModel]]:
    result: dict[str, tuple[str, ImpedanceModel]] = {}
    for raw_id, model in cap_models.items():
        display = str(raw_id).strip()
        key = _key(display, label="capacitor model ID")
        if key in result:
            raise LayerwiseScenarioTopologyError(
                "CAP_MODEL_AMBIGUOUS",
                f"capacitor model {display!r} is duplicated case-insensitively",
            )
        if not isinstance(model, ImpedanceModel):
            raise LayerwiseScenarioTopologyError(
                "CAP_MODEL_INVALID",
                f"capacitor model {display!r} does not implement ImpedanceModel",
            )
        result[key] = (display, model)
    return result


def _link_manifest(link: LayerSurfaceViaLink) -> dict[str, Any]:
    return {
        "link_id": link.link_id.casefold(),
        "first_node_id": link.first_node_id.casefold(),
        "second_node_id": link.second_node_id.casefold(),
        "count": link.count,
        "mode": link.mode,
        "resistance_ohm_per_via": link.resistance_ohm_per_via,
        "inductance_h_per_via": link.inductance_h_per_via,
        "owner_ids": sorted(owner.casefold() for owner in link.owner_ids),
    }


def compile_layerwise_scenario_network(
    *,
    base_substrate: LayerwiseNetworkSubstrate,
    plan: ScenarioTopologyPlan,
    cap_models: Mapping[str, ImpedanceModel],
) -> LayerwiseScenarioNetworkBinding:
    """Compile one Original/Tuned board state over a verified base network."""

    if not isinstance(base_substrate, LayerwiseNetworkSubstrate):
        raise LayerwiseScenarioTopologyError(
            "BASE_SUBSTRATE_INVALID", "base layerwise substrate is absent"
        )
    _validate_plan(plan)
    source_sha = str(base_substrate.provenance.get("source_sha256", "")).casefold()
    if source_sha and source_sha != plan.source_sha256.casefold():
        raise LayerwiseScenarioTopologyError(
            "SOURCE_IDENTITY_MISMATCH",
            "scenario plan is not bound to the base substrate SPD source",
        )
    certificate_sha = str(
        base_substrate.provenance.get(
            "surface_connectivity_evidence_sha256",
            base_substrate.provenance.get(
                "finite_route_certificate_sha256",
                base_substrate.provenance.get("finite_via_certificate_sha256", ""),
            ),
        )
    ).casefold()
    if certificate_sha and certificate_sha != plan.finite_route_certificate_sha256.casefold():
        raise LayerwiseScenarioTopologyError(
            "FINITE_ROUTE_CERTIFICATE_MISMATCH",
            "scenario plan names a different finite-route certificate",
        )

    base_network = base_substrate.network
    partition_by_cut = _casefold_index(
        plan.source_owner_partition, "cut_link_id", label="source cut"
    )
    suppressed_keys = tuple(
        _key(item, label="suppressed base cut")
        for item in plan.suppressed_base_cut_ids
    )
    if len(set(suppressed_keys)) != len(suppressed_keys):
        raise LayerwiseScenarioTopologyError(
            "SUPPRESSED_CUT_DUPLICATED", "suppressed base cut IDs are duplicated"
        )
    expected_suppressed = {
        key
        for key, item in partition_by_cut.items()
        if item.disposition == SourceContactDisposition.SUPPRESSED_FOR_RETARGET
    }
    suppressed_key_set = frozenset(suppressed_keys)
    if suppressed_key_set != expected_suppressed:
        raise LayerwiseScenarioTopologyError(
            "SUPPRESSED_CUT_PARTITION_MISMATCH",
            "suppressed base cuts do not equal the eligible retarget owner partition",
        )

    # Keep only requested source cuts instead of a full million-link ID map.
    # One base-owner set remains necessary to enforce case-insensitive
    # exact-once and to exclude scenario-only topology owners.
    base_cut_links: dict[str, LayerSurfaceViaLink] = {}
    raw_base_owner_ids: set[str] = set()
    for link in base_network.via_links:
        link_key = _key(link.link_id, label="base link")
        if link_key in partition_by_cut:
            previous_link = base_cut_links.setdefault(link_key, link)
            if previous_link is not link:
                raise LayerwiseScenarioTopologyError(
                    "BASE_CUT_AMBIGUOUS",
                    f"source cut {link.link_id!r} is duplicated case-insensitively",
                )
        for owner in link.owner_ids:
            owner_key = _key(owner, label="base physical owner")
            if owner_key in raw_base_owner_ids:
                raise LayerwiseScenarioTopologyError(
                    "BASE_OWNER_DUPLICATED",
                    f"owner {owner!r} appears more than once in the base network",
                )
            raw_base_owner_ids.add(owner_key)

    for cut_key, partition in partition_by_cut.items():
        base_link = base_cut_links.get(cut_key)
        if base_link is None:
            raise LayerwiseScenarioTopologyError(
                "SOURCE_CUT_UNKNOWN",
                f"source cut {partition.cut_link_id!r} is absent from the base network",
            )
        if (
            base_link.mode != "finite_parallel_rl"
            or base_link.count != 1
            or len(base_link.owner_ids) != 1
            or base_link.owner_ids[0].casefold()
            != partition.cut_owner_id.casefold()
            or partition.cut_owner_id.casefold()
            != f"via:{partition.via_id}".casefold()
        ):
            raise LayerwiseScenarioTopologyError(
                "SOURCE_CUT_OWNER_INVALID",
                f"source cut {partition.cut_link_id!r} is not the exclusive canonical Via owner",
            )

    terminal_node_ids = tuple(
        node_id
        for terminal in plan.terminal_nodes
        for node_id in (terminal.power_node_id, terminal.ground_node_id)
    )
    base_node_keys = {
        _key(node_id, label="base surface node")
        for node_id in base_network.surface_node_ids
    }
    terminal_node_keys = [
        _key(node_id, label="scenario terminal node")
        for node_id in terminal_node_ids
    ]
    if (
        len(set(terminal_node_keys)) != len(terminal_node_keys)
        or base_node_keys.intersection(terminal_node_keys)
    ):
        raise LayerwiseScenarioTopologyError(
            "SCENARIO_TERMINAL_NODE_CONFLICT",
            "synthetic scenario terminal nodes must be unique and disjoint from the base substrate",
        )
    all_nodes_by_key: dict[str, str] = {}
    for node_id in (*base_network.surface_node_ids, *terminal_node_ids):
        key = _key(node_id, label="scenario surface node")
        previous = all_nodes_by_key.setdefault(key, str(node_id).strip())
        if previous != str(node_id).strip():
            raise LayerwiseScenarioTopologyError(
                "SCENARIO_NODE_AMBIGUOUS",
                f"scenario node {node_id!r} collides case-insensitively",
            )

    topology_links: list[LayerSurfaceViaLink] = []
    topology_owner_ids: set[str] = set()
    for item in plan.active_topology_links:
        if (
            item.first_node_id.casefold() not in all_nodes_by_key
            or item.second_node_id.casefold() not in all_nodes_by_key
        ):
            raise LayerwiseScenarioTopologyError(
                "TOPOLOGY_LINK_NODE_UNKNOWN",
                f"scenario topology link {item.link_id!r} references an unknown node",
            )
        for owner in item.owner_ids:
            owner_key = _key(owner, label="scenario topology owner")
            if owner_key in topology_owner_ids or owner_key in raw_base_owner_ids:
                raise LayerwiseScenarioTopologyError(
                    "SCENARIO_OWNER_DUPLICATED",
                    f"scenario topology owner {owner!r} is not exclusive",
                )
            topology_owner_ids.add(owner_key)
        topology_links.append(
            LayerSurfaceViaLink(
                link_id=item.link_id,
                first_node_id=item.first_node_id,
                second_node_id=item.second_node_id,
                count=1,
                mode="topology_only_ideal",
                owner_ids=item.owner_ids,
            )
        )

    retarget_by_owner: dict[str, Any] = {}
    retarget_links: list[LayerSurfaceViaLink] = []
    for route in plan.active_retarget_routes:
        owner_key = _key(route.route_owner_id, label="retarget owner")
        if owner_key in retarget_by_owner:
            raise LayerwiseScenarioTopologyError(
                "RETARGET_OWNER_DUPLICATED",
                f"retarget owner {route.route_owner_id!r} is repeated",
            )
        if (
            route.first_node_id.casefold() not in all_nodes_by_key
            or route.target_node_id.casefold() not in all_nodes_by_key
        ):
            raise LayerwiseScenarioTopologyError(
                "RETARGET_NODE_UNKNOWN",
                f"retarget route {route.route_id!r} references an unknown node",
            )
        retarget_by_owner[owner_key] = route
        retarget_links.append(
            LayerSurfaceViaLink(
                link_id=route.route_id,
                first_node_id=route.first_node_id,
                second_node_id=route.target_node_id,
                count=1,
                mode="finite_parallel_rl",
                resistance_ohm_per_via=route.resistance_ohm,
                inductance_h_per_via=route.inductance_h,
                owner_ids=(route.route_owner_id,),
            )
        )
    expected_retarget_owners = {
        item.cut_owner_id.casefold()
        for item in plan.source_owner_partition
        if item.disposition == SourceContactDisposition.SUPPRESSED_FOR_RETARGET
    }
    if set(retarget_by_owner) != expected_retarget_owners:
        raise LayerwiseScenarioTopologyError(
            "RETARGET_OWNER_TRANSFER_INCOMPLETE",
            "every suppressed base owner must be transferred to exactly one route",
        )

    retained_base_links = tuple(
        link
        for link in base_network.via_links
        if link.link_id.casefold() not in suppressed_key_set
    )
    final_links = tuple(
        sorted(
            (*retained_base_links, *topology_links, *retarget_links),
            key=lambda item: (item.link_id.casefold(), item.link_id),
        )
    )
    if any(
        first.link_id.casefold() == second.link_id.casefold()
        for first, second in zip(final_links, final_links[1:])
    ):
        raise LayerwiseScenarioTopologyError(
            "SCENARIO_LINK_ID_AMBIGUOUS",
            "final scenario link IDs must be case-insensitively unique",
        )
    # Exact owner coverage follows constructively: retained links preserve all
    # non-suppressed unique base owners; every suppressed one-owner cut is
    # transferred to its already-validated one-owner retarget; and topology
    # owners were proven unique/disjoint above.  A second million-entry owner
    # count dictionary would add no evidence.
    raw_base_owner_count = len(raw_base_owner_ids)
    del raw_base_owner_ids
    del base_cut_links

    try:
        network = compile_layer_surface_network(
            tuple(value for _key_value, value in sorted(all_nodes_by_key.items())),
            partials=base_network.partials,
            via_links=final_links,
            ports=tuple(
                sorted(
                    base_network.ports,
                    key=lambda item: (item.port_id.casefold(), item.port_id),
                )
            ),
        )
    except LayerSurfaceNetworkError as exc:
        raise LayerwiseScenarioTopologyError(
            "SCENARIO_NETWORK_COMPILE_FAILED", str(exc)
        ) from exc

    models = _cap_model_index(cap_models)
    clusters: list[LayerSurfaceTerminationCluster] = []
    for request in plan.cap_body_requests:
        model_row = models.get(request.model_id.casefold())
        if model_row is None:
            raise LayerwiseScenarioTopologyError(
                "CAP_MODEL_UNKNOWN",
                f"enabled decap {request.refdes!r} references absent model {request.model_id!r}",
            )
        if (
            request.positive_node_id.casefold() not in all_nodes_by_key
            or request.negative_node_id.casefold() not in all_nodes_by_key
        ):
            raise LayerwiseScenarioTopologyError(
                "CAP_TERMINAL_NODE_UNKNOWN",
                f"enabled decap {request.refdes!r} references an unknown terminal node",
            )
        _model_id, model = model_row
        positive_local = f"scenario-cap-local:{request.refdes}:PWR"
        negative_local = f"scenario-cap-local:{request.refdes}:GND"
        clusters.append(
            LayerSurfaceTerminationCluster(
                cluster_id=f"scenario-cap:{request.refdes}",
                positive_surface_node_id=request.positive_node_id,
                negative_surface_node_id=request.negative_node_id,
                positive_terminal_node_id=positive_local,
                negative_terminal_node_id=negative_local,
                branches=(
                    LayerSurfaceTerminationBranch(
                        branch_id=f"cap:{request.refdes}",
                        first_node_id=positive_local,
                        second_node_id=negative_local,
                        model=model,
                        owner_ids=(f"component:{request.refdes}",),
                    ),
                ),
                rail_owner_ids=(request.rail_id,),
                ownership_status="complete",
                source_classification="SCENARIO_PLAN_V1",
                source_evidence_sha256=plan.plan_sha256,
            )
        )
    try:
        manifest = compile_layer_surface_termination_manifest(
            network.surface_node_ids, clusters
        )
        network.termination_reduced_node_mapping(manifest)
    except (LayerSurfaceTerminationError, LayerSurfaceNetworkError) as exc:
        raise LayerwiseScenarioTopologyError(
            "SCENARIO_TERMINATION_COMPILE_FAILED", str(exc)
        ) from exc

    surface_node_manifest_sha256 = _canonical_sequence_sha256(
        node.casefold() for node in network.surface_node_ids
    )
    link_manifest_sha256 = _canonical_sequence_sha256(
        _link_manifest(link) for link in network.via_links
    )
    scenario_identity = _sha256(
        {
            "identity_schema": "layerwise-scenario-network-identity-v2",
            "compiler_id": SCENARIO_NETWORK_COMPILER_ID,
            "base_substrate_identity_sha256": (
                base_substrate.substrate_identity_sha256.casefold()
            ),
            "plan_sha256": plan.plan_sha256.casefold(),
            "surface_node_manifest": {
                "schema": "canonical-json-sequence-sha256-v1",
                "count": len(network.surface_node_ids),
                "sha256": surface_node_manifest_sha256,
            },
            "link_manifest": {
                "schema": "canonical-json-sequence-sha256-v1",
                "count": len(network.via_links),
                "sha256": link_manifest_sha256,
            },
            "ports": sorted(
                (
                    {
                        "port_id": port.port_id.casefold(),
                        "positive_node_id": port.positive_node_id.casefold(),
                        "negative_node_id": port.negative_node_id.casefold(),
                    }
                    for port in network.ports
                ),
                key=lambda item: item["port_id"],
            ),
            "termination_manifest_sha256": manifest.manifest_sha256,
        }
    )
    provenance = {
        "scenario_network_compiler_id": SCENARIO_NETWORK_COMPILER_ID,
        "scenario_plan_sha256": plan.plan_sha256,
        "scenario_identity_sha256": scenario_identity,
        "scenario_surface_node_manifest_sha256": (
            surface_node_manifest_sha256
        ),
        "scenario_link_manifest_sha256": link_manifest_sha256,
        "scenario_terminal_node_count": len(terminal_node_ids),
        "scenario_topology_only_link_count": len(topology_links),
        "scenario_retarget_route_count": len(retarget_links),
        "scenario_suppressed_base_cut_count": len(suppressed_keys),
        "scenario_cap_body_count": len(clusters),
        "scenario_raw_via_owner_count": raw_base_owner_count,
        "scenario_raw_via_owner_exact_once": True,
    }
    try:
        return LayerwiseScenarioNetworkBinding(
            network=network,
            termination_manifest=manifest,
            base_substrate_identity_sha256=(
                base_substrate.substrate_identity_sha256
            ),
            scenario_identity_sha256=scenario_identity,
            plan_sha256=plan.plan_sha256,
            provenance=provenance,
        )
    except LayerwiseNetworkUnavailable as exc:
        raise LayerwiseScenarioTopologyError(exc.code, str(exc)) from exc


__all__ = [
    "LayerwiseScenarioTopologyError",
    "SCENARIO_NETWORK_COMPILER_ID",
    "compile_layerwise_scenario_network",
]
