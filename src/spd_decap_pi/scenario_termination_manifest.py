"""Compile mounted Scenario decaps into fail-closed layer-surface terminations.

This adapter intentionally depends only on persisted Scenario connectivity,
an explicit source-Via-to-reduced-node binding, and caller-supplied electrical
models.  It neither opens the raw SPD nor reads a Touchstone reference.  The
output is the independent termination manifest consumed by the layer-surface
global-Y integration boundary.  Legacy certificates bind a physical Via to a
surface and keep that Via's calibrated ``plane -> top`` branch here.  The
finite-route certificate instead binds the exposed TOP terminal vertex and
declares that every raw Via is already owned by the base global network; in
that mode this manifest stamps capacitor/package bodies only.

An ``UNRESOLVED`` classification is not automatically rejected or accepted.
It is accepted only when both persisted top-pad graphs connect every cluster
member, every physical anchor Via has exactly one reduced-node binding, and
all PWR (and all GND) anchors collapse to one respective reduced supernode.
The original classification and reason remain hashed in the output manifest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from typing import Any, TypeAlias

from spd_decap_pi._core.models.impedance import (
    ImpedanceModel,
    ScaledImpedanceModel,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    CompiledLayerSurfaceTerminationManifest,
    LayerSurfaceTerminationBranch,
    LayerSurfaceTerminationCluster,
    LayerSurfaceTerminationError,
    compile_layer_surface_termination_manifest,
    impedance_model_identity_sha256,
    scoped_impedance_model_identity_cache,
)

from .scenario import (
    DecapConnectionKind,
    DecapPadState,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioSpec,
    ScenarioViaLanding,
    SharedPadCluster,
    SharedPadClusterState,
)


ReducedNodeBinding: TypeAlias = str | Sequence[str]


def _key(value: Any, *, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise LayerSurfaceTerminationError(
            "SCENARIO_TERMINATION_IDENTITY_MISSING", f"{name} must not be blank"
        )
    return text.casefold()


def _canonical_json(payload: Any) -> bytes:
    try:
        value = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LayerSurfaceTerminationError(
            "SCENARIO_TERMINATION_EVIDENCE_INVALID",
            f"scenario termination evidence is not canonical JSON: {exc}",
        ) from exc
    return (value + "\n").encode("utf-8")


def _sha256(payload: Any) -> str:
    return sha256(_canonical_json(payload)).hexdigest()


def _casefold_mapping(
    values: Mapping[str, Any], *, label: str
) -> dict[str, tuple[str, Any]]:
    result: dict[str, tuple[str, Any]] = {}
    for raw_id, value in values.items():
        display = str(raw_id).strip()
        key = _key(display, name=f"{label} ID")
        if key in result:
            raise LayerSurfaceTerminationError(
                "SCENARIO_TERMINATION_MAPPING_AMBIGUOUS",
                f"{label} mapping repeats case-insensitive ID {display!r}",
            )
        result[key] = (display, value)
    return result


def _resolved_via_node(
    via_id: str,
    bindings: Mapping[str, tuple[str, Any]],
) -> str:
    entry = bindings.get(_key(via_id, name="source Via ID"))
    if entry is None:
        raise LayerSurfaceTerminationError(
            "SOURCE_VIA_NODE_MISSING",
            f"source Via {via_id!r} has no reduced-network node binding",
        )
    _display, raw = entry
    if isinstance(raw, str):
        candidates = (raw.strip(),)
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        candidates = tuple(str(item).strip() for item in raw)
    else:
        raise LayerSurfaceTerminationError(
            "SOURCE_VIA_NODE_INVALID",
            f"source Via {via_id!r} has an invalid reduced-node binding",
        )
    if not candidates or any(not item for item in candidates):
        raise LayerSurfaceTerminationError(
            "SOURCE_VIA_NODE_MISSING",
            f"source Via {via_id!r} has no nonblank reduced-network node",
        )
    canonical = {_key(item, name="reduced-network node") for item in candidates}
    if len(candidates) != 1 or len(canonical) != 1:
        raise LayerSurfaceTerminationError(
            "SOURCE_VIA_NODE_AMBIGUOUS",
            f"source Via {via_id!r} maps to {candidates!r}; exactly one node is required",
        )
    return candidates[0]


def _unique_landings(
    connections: Sequence[ScenarioDecapConnection],
    *,
    terminal: str,
) -> tuple[ScenarioViaLanding, ...]:
    values: dict[str, ScenarioViaLanding] = {}
    for connection in connections:
        landings = (
            connection.power_vias if terminal == "PWR" else connection.ground_vias
        )
        for landing in landings:
            key = _key(landing.via_id, name=f"{terminal} Via ID")
            previous = values.get(key)
            if previous is not None and previous != landing:
                raise LayerSurfaceTerminationError(
                    "SOURCE_VIA_EVIDENCE_AMBIGUOUS",
                    f"source Via {landing.via_id!r} has conflicting landing evidence",
                )
            values.setdefault(key, landing)
    return tuple(values[key] for key in sorted(values))


def _terminal_supernode(
    landings: Sequence[ScenarioViaLanding],
    bindings: Mapping[str, tuple[str, Any]],
    *,
    context: str,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    if not landings:
        raise LayerSurfaceTerminationError(
            "SOURCE_ANCHOR_VIA_MISSING", f"{context} has no physical anchor Via"
        )
    resolved = tuple(
        (landing.via_id, _resolved_via_node(landing.via_id, bindings))
        for landing in landings
    )
    nodes: dict[str, str] = {}
    for _via_id, node in resolved:
        nodes.setdefault(_key(node, name="reduced-network node"), node)
    if len(nodes) != 1:
        raise LayerSurfaceTerminationError(
            "TERMINAL_SUPERNODE_AMBIGUOUS",
            f"{context} anchor Vias reach multiple reduced nodes: "
            f"{tuple(node for _via, node in resolved)!r}",
        )
    return next(iter(nodes.values())), resolved


def _model_index(
    models: Mapping[str, ImpedanceModel],
) -> dict[str, tuple[str, ImpedanceModel]]:
    indexed = _casefold_mapping(models, label="termination model")
    result: dict[str, tuple[str, ImpedanceModel]] = {}
    for key, (display, model) in indexed.items():
        if not isinstance(model, ImpedanceModel):
            raise LayerSurfaceTerminationError(
                "TERMINATION_MODEL_INVALID",
                f"model {display!r} does not provide two-terminal impedance",
            )
        # Evaluate deterministic evidence now so an opaque implementation is
        # rejected before partial cluster compilation.
        impedance_model_identity_sha256(model)
        result[key] = (display, model)
    return result


def _terminal_via_model_index(
    models: Mapping[str, ImpedanceModel],
) -> dict[str, tuple[str, ImpedanceModel, str, ScaledImpedanceModel]]:
    indexed = _casefold_mapping(models, label="terminal Via model")
    result: dict[
        str, tuple[str, ImpedanceModel, str, ScaledImpedanceModel]
    ] = {}
    half_model_by_identity: dict[str, ScaledImpedanceModel] = {}
    for key, (display, model) in indexed.items():
        if not isinstance(model, ImpedanceModel):
            raise LayerSurfaceTerminationError(
                "TERMINAL_VIA_MODEL_INVALID",
                f"source Via {display!r} does not provide two-terminal impedance",
            )
        identity = impedance_model_identity_sha256(model)
        half_model = half_model_by_identity.get(identity)
        if half_model is None:
            half_model = ScaledImpedanceModel(
                model_id=f"terminal-half:{identity}",
                source=model,
                scale=0.5,
            )
            half_model_by_identity[identity] = half_model
        result[key] = (display, model, identity, half_model)
    return result


def _terminal_via_model(
    via_id: str,
    models: Mapping[
        str, tuple[str, ImpedanceModel, str, ScaledImpedanceModel]
    ],
) -> tuple[str, ImpedanceModel, str, ScaledImpedanceModel]:
    result = models.get(_key(via_id, name="source Via ID"))
    if result is None:
        raise LayerSurfaceTerminationError(
            "TERMINAL_VIA_MODEL_MISSING",
            f"source Via {via_id!r} has no differential loop impedance model",
        )
    return result


def _decap_model(
    decap: ScenarioDecap,
    models: Mapping[str, tuple[str, ImpedanceModel]],
) -> tuple[str, ImpedanceModel]:
    if not decap.enabled:
        raise LayerSurfaceTerminationError(
            "INTERNAL_COMPILER_ERROR",
            f"disabled decap {decap.refdes!r} requested an electrical model",
        )
    if decap.model_id is None:
        raise LayerSurfaceTerminationError(
            "TERMINATION_MODEL_MISSING",
            f"enabled decap {decap.refdes!r} has no assigned electrical model",
        )
    result = models.get(_key(decap.model_id, name="decap model ID"))
    if result is None:
        raise LayerSurfaceTerminationError(
            "TERMINATION_MODEL_UNKNOWN",
            f"enabled decap {decap.refdes!r} references unknown model {decap.model_id!r}",
        )
    return result


def _cap_branches(
    decaps: Sequence[ScenarioDecap],
    models: Mapping[str, tuple[str, ImpedanceModel]],
    *,
    positive_node: str,
    negative_node: str,
) -> tuple[LayerSurfaceTerminationBranch, ...]:
    branches: list[LayerSurfaceTerminationBranch] = []
    for decap in sorted(decaps, key=lambda item: (item.refdes.casefold(), item.refdes)):
        if not decap.enabled:
            continue
        _model_id, model = _decap_model(decap, models)
        branches.append(
            LayerSurfaceTerminationBranch(
                branch_id=f"cap:{decap.refdes}",
                first_node_id=positive_node,
                second_node_id=negative_node,
                model=model,
                owner_ids=(f"component:{decap.refdes}",),
            )
        )
    return tuple(branches)


def _terminal_via_branches(
    resolved_vias: Sequence[tuple[str, str]],
    models: Mapping[
        str, tuple[str, ImpedanceModel, str, ScaledImpedanceModel]
    ],
    *,
    terminal: str,
    plane_node: str,
    top_node: str,
) -> tuple[LayerSurfaceTerminationBranch, ...]:
    branches: list[LayerSurfaceTerminationBranch] = []
    for via_id, _global_node in sorted(
        resolved_vias, key=lambda item: (item[0].casefold(), item[0])
    ):
        _mapping_key, _full_loop_model, _identity, terminal_model = (
            _terminal_via_model(via_id, models)
        )
        # SharedPadClusterModel's established convention treats every input
        # Via impedance as one calibrated differential PWR/GND loop and stamps
        # half of that loop on each physical terminal.  Preserve that exact
        # convention in the scalar layer-surface circuit.
        branches.append(
            LayerSurfaceTerminationBranch(
                branch_id=f"via:{terminal.casefold()}:{via_id}",
                first_node_id=plane_node,
                second_node_id=top_node,
                model=terminal_model,
                owner_ids=(f"via:{via_id}",),
            )
        )
    return tuple(branches)


def _single_rail_owner(decaps: Sequence[ScenarioDecap], *, context: str) -> str:
    active = tuple(
        item
        for item in decaps
        if item.pad_state != DecapPadState.ISOLATION_GAP
    )
    rails: dict[str, str] = {}
    nets: dict[str, str] = {}
    for decap in active:
        rails.setdefault(
            _key(decap.current_rail_id, name="current rail ID"),
            decap.current_rail_id,
        )
        nets.setdefault(_key(decap.current_net, name="current NET"), decap.current_net)
    if len(rails) != 1 or len(nets) != 1:
        raise LayerSurfaceTerminationError(
            "CROSS_RAIL_OWNERSHIP_UNRESOLVED",
            f"{context} does not have one physical PWR supernode owner "
            f"(rails={tuple(rails.values())!r}, nets={tuple(nets.values())!r})",
        )
    return next(iter(rails.values()))


def _source_evidence(
    scenario: ScenarioSpec,
    *,
    scenario_design_fingerprint: str,
    identity: str,
    classification: str,
    reason: str | None,
    decaps: Sequence[ScenarioDecap],
    connections: Sequence[ScenarioDecapConnection],
    resolved_power: Sequence[tuple[str, str]],
    resolved_ground: Sequence[tuple[str, str]],
    models: Mapping[str, tuple[str, ImpedanceModel]],
    terminal_via_models: Mapping[
        str, tuple[str, ImpedanceModel, str, ScaledImpedanceModel]
    ],
    base_owns_terminal_routes: bool,
    cluster: SharedPadCluster | None,
) -> str:
    model_manifest: list[dict[str, str]] = []
    for decap in decaps:
        if not decap.enabled or decap.model_id is None:
            continue
        model = models.get(_key(decap.model_id, name="decap model ID"))
        if model is None:
            # The actionable missing-model error is emitted while branches are
            # built; retain a deterministic marker if this helper is reached.
            model_manifest.append(
                {"refdes": decap.refdes.casefold(), "model": "<missing>"}
            )
        else:
            model_manifest.append(
                {
                    "refdes": decap.refdes.casefold(),
                    "model_id": model[0].casefold(),
                    "model_identity_sha256": impedance_model_identity_sha256(model[1]),
                }
            )
    via_model_manifest: list[dict[str, str | float]] = []
    for terminal, resolved in (
        ("PWR", resolved_power),
        ("GND", resolved_ground),
    ):
        for via_id, _node in resolved:
            if base_owns_terminal_routes:
                via_model_manifest.append(
                    {
                        "terminal": terminal,
                        "via_id": via_id.casefold(),
                        "route_owner": "base_global_finite_route",
                    }
                )
                continue
            mapping_key, model, model_identity, _half_model = (
                _terminal_via_model(via_id, terminal_via_models)
            )
            via_model_manifest.append(
                {
                    "terminal": terminal,
                    "via_id": via_id.casefold(),
                    "mapping_key": mapping_key.casefold(),
                    "model_identity_sha256": model_identity,
                    "terminal_impedance_scale": 0.5,
                }
            )
    analysis = scenario.connection_analysis
    assert analysis is not None
    return _sha256(
        {
            "scenario_design_fingerprint": scenario_design_fingerprint,
            "source_sha256": scenario.source.sha256,
            "connection_analysis_source_sha256": analysis.source_sha256,
            "connection_analysis_version": analysis.version,
            "termination_identity": identity.casefold(),
            "source_classification": classification,
            "source_reason": reason,
            "cluster": (
                None if cluster is None else cluster.model_dump(mode="json")
            ),
            "decaps": [item.model_dump(mode="json") for item in decaps],
            "connections": [
                item.model_dump(mode="json")
                for item in sorted(
                    connections,
                    key=lambda entry: (entry.refdes.casefold(), entry.refdes),
                )
            ],
            "resolved_power_vias": sorted(
                (via.casefold(), node.casefold()) for via, node in resolved_power
            ),
            "resolved_ground_vias": sorted(
                (via.casefold(), node.casefold()) for via, node in resolved_ground
            ),
            "models": sorted(model_manifest, key=lambda item: item["refdes"]),
            "terminal_via_models": sorted(
                via_model_manifest,
                key=lambda item: (str(item["terminal"]), str(item["via_id"])),
            ),
            "terminal_route_ownership": (
                "base_global_finite_route"
                if base_owns_terminal_routes
                else "local_calibrated_via_half_branches"
            ),
        }
    )


def _compile_direct_cluster(
    scenario: ScenarioSpec,
    scenario_design_fingerprint: str,
    decap: ScenarioDecap,
    connection: ScenarioDecapConnection,
    bindings: Mapping[str, tuple[str, Any]],
    models: Mapping[str, tuple[str, ImpedanceModel]],
    terminal_via_models: Mapping[
        str, tuple[str, ImpedanceModel, str, ScaledImpedanceModel]
    ],
    *,
    base_owns_terminal_routes: bool,
) -> LayerSurfaceTerminationCluster:
    if connection.kind not in {
        DecapConnectionKind.DIRECT,
        DecapConnectionKind.UNRESOLVED,
    }:
        raise LayerSurfaceTerminationError(
            "TERMINATION_CONNECTION_UNMODELABLE",
            f"enabled decap {decap.refdes!r} has {connection.kind.value} connectivity",
        )
    positive, resolved_power = _terminal_supernode(
        connection.power_vias,
        bindings,
        context=f"{decap.refdes} PWR terminal",
    )
    negative, resolved_ground = _terminal_supernode(
        connection.ground_vias,
        bindings,
        context=f"{decap.refdes} GND terminal",
    )
    if _key(positive, name="PWR reduced node") == _key(
        negative, name="GND reduced node"
    ):
        raise LayerSurfaceTerminationError(
            "TERMINATION_SURFACES_SHORTED",
            f"decap {decap.refdes!r} PWR/GND anchors reach one reduced node",
        )
    branches = (
        _cap_branches(
            (decap,), models, positive_node="top:PWR", negative_node="top:GND"
        )
        if base_owns_terminal_routes
        else (
            *_terminal_via_branches(
                resolved_power,
                terminal_via_models,
                terminal="PWR",
                plane_node="plane:PWR",
                top_node="top:PWR",
            ),
            *_cap_branches(
                (decap,),
                models,
                positive_node="top:PWR",
                negative_node="top:GND",
            ),
            *_terminal_via_branches(
                resolved_ground,
                terminal_via_models,
                terminal="GND",
                plane_node="plane:GND",
                top_node="top:GND",
            ),
        )
    )
    identity = f"scenario-direct:{decap.refdes}"
    evidence = _source_evidence(
        scenario,
        scenario_design_fingerprint=scenario_design_fingerprint,
        identity=identity,
        classification=connection.kind.value,
        reason=connection.reason,
        decaps=(decap,),
        connections=(connection,),
        resolved_power=resolved_power,
        resolved_ground=resolved_ground,
        models=models,
        terminal_via_models=terminal_via_models,
        base_owns_terminal_routes=base_owns_terminal_routes,
        cluster=None,
    )
    return LayerSurfaceTerminationCluster(
        cluster_id=identity,
        positive_surface_node_id=positive,
        negative_surface_node_id=negative,
        positive_terminal_node_id=(
            "top:PWR" if base_owns_terminal_routes else "plane:PWR"
        ),
        negative_terminal_node_id=(
            "top:GND" if base_owns_terminal_routes else "plane:GND"
        ),
        branches=branches,
        rail_owner_ids=(decap.current_rail_id,),
        ownership_status="complete",
        source_classification=connection.kind.value,
        source_reason=connection.reason,
        source_evidence_sha256=evidence,
    )


def _compile_shared_cluster(
    scenario: ScenarioSpec,
    scenario_design_fingerprint: str,
    cluster: SharedPadCluster,
    decaps: Sequence[ScenarioDecap],
    connections: Sequence[ScenarioDecapConnection],
    bindings: Mapping[str, tuple[str, Any]],
    models: Mapping[str, tuple[str, ImpedanceModel]],
    terminal_via_models: Mapping[
        str, tuple[str, ImpedanceModel, str, ScaledImpedanceModel]
    ],
    *,
    base_owns_terminal_routes: bool,
) -> LayerSurfaceTerminationCluster | None:
    populated = tuple(item for item in decaps if item.enabled)
    if not populated:
        return None
    if cluster.state == SharedPadClusterState.FLOATING:
        raise LayerSurfaceTerminationError(
            "SOURCE_ANCHOR_VIA_MISSING",
            f"populated shared cluster {cluster.cluster_id!r} is floating",
        )
    if not cluster.source_graph_is_connected(
        cluster.power_edges
    ) or not cluster.source_graph_is_connected(cluster.ground_edges):
        raise LayerSurfaceTerminationError(
            "SOURCE_PAD_GRAPH_INCOMPLETE",
            f"shared cluster {cluster.cluster_id!r} lacks complete PWR/GND source graphs",
        )
    expected_anchor_keys = {item.casefold() for item in cluster.anchor_refdes}
    observed_anchor_keys = {
        connection.refdes.casefold()
        for connection in connections
        if connection.power_vias or connection.ground_vias
    }
    if expected_anchor_keys != observed_anchor_keys:
        raise LayerSurfaceTerminationError(
            "SOURCE_ANCHOR_EVIDENCE_AMBIGUOUS",
            f"shared cluster {cluster.cluster_id!r} anchor manifest differs from "
            "connection Via evidence",
        )
    rail_id = _single_rail_owner(
        decaps, context=f"shared cluster {cluster.cluster_id!r}"
    )
    power_landings = _unique_landings(connections, terminal="PWR")
    ground_landings = _unique_landings(connections, terminal="GND")
    positive, resolved_power = _terminal_supernode(
        power_landings,
        bindings,
        context=f"shared cluster {cluster.cluster_id} PWR supernode",
    )
    negative, resolved_ground = _terminal_supernode(
        ground_landings,
        bindings,
        context=f"shared cluster {cluster.cluster_id} GND supernode",
    )
    if _key(positive, name="PWR reduced node") == _key(
        negative, name="GND reduced node"
    ):
        raise LayerSurfaceTerminationError(
            "TERMINATION_SURFACES_SHORTED",
            f"shared cluster {cluster.cluster_id!r} PWR/GND anchors reach one node",
        )
    branches = (
        _cap_branches(
            decaps, models, positive_node="top:PWR", negative_node="top:GND"
        )
        if base_owns_terminal_routes
        else (
            *_terminal_via_branches(
                resolved_power,
                terminal_via_models,
                terminal="PWR",
                plane_node="plane:PWR",
                top_node="top:PWR",
            ),
            *_cap_branches(
                decaps,
                models,
                positive_node="top:PWR",
                negative_node="top:GND",
            ),
            *_terminal_via_branches(
                resolved_ground,
                terminal_via_models,
                terminal="GND",
                plane_node="plane:GND",
                top_node="top:GND",
            ),
        )
    )
    identity = f"scenario-shared:{cluster.cluster_id}"
    evidence = _source_evidence(
        scenario,
        scenario_design_fingerprint=scenario_design_fingerprint,
        identity=identity,
        classification=cluster.state.value,
        reason=cluster.reason,
        decaps=decaps,
        connections=connections,
        resolved_power=resolved_power,
        resolved_ground=resolved_ground,
        models=models,
        terminal_via_models=terminal_via_models,
        base_owns_terminal_routes=base_owns_terminal_routes,
        cluster=cluster,
    )
    return LayerSurfaceTerminationCluster(
        cluster_id=identity,
        positive_surface_node_id=positive,
        negative_surface_node_id=negative,
        positive_terminal_node_id=(
            "top:PWR" if base_owns_terminal_routes else "plane:PWR"
        ),
        negative_terminal_node_id=(
            "top:GND" if base_owns_terminal_routes else "plane:GND"
        ),
        branches=branches,
        rail_owner_ids=(rail_id,),
        ownership_status="complete",
        source_classification=cluster.state.value,
        source_reason=cluster.reason,
        source_evidence_sha256=evidence,
    )


def _compile_scenario_termination_manifest(
    scenario: ScenarioSpec,
    global_reduced_node_ids: Sequence[str],
    source_via_to_reduced_node: Mapping[str, ReducedNodeBinding],
    model_by_id: Mapping[str, ImpedanceModel],
    terminal_via_model_by_id: Mapping[str, ImpedanceModel],
    *,
    base_owns_terminal_routes: bool,
) -> CompiledLayerSurfaceTerminationManifest:
    """Compile all mounted Scenario loads into source-owned cluster stamps.

    The returned manifest already supports selected-rail exclusion through its
    :meth:`CompiledLayerSurfaceTerminationManifest.evaluate` method.  Missing
    or ambiguous source ownership aborts the whole compile; no partial manifest
    is returned.  ``terminal_via_model_by_id`` is keyed by physical source Via
    ID and supplies the calibrated differential loop model whose impedance is
    split symmetrically as ``Zloop / 2`` on each terminal branch.  When
    ``base_owns_terminal_routes`` is true, bindings name exposed TOP quotient
    vertices, the terminal Via model map must be empty, and only cap/package
    branches are compiled.
    """

    if not isinstance(scenario, ScenarioSpec):
        raise LayerSurfaceTerminationError(
            "SCENARIO_INVALID", "scenario termination input must be ScenarioSpec"
        )
    analysis = scenario.connection_analysis
    if analysis is None:
        raise LayerSurfaceTerminationError(
            "CONNECTION_ANALYSIS_MISSING",
            "scenario has no source connection analysis",
        )
    if analysis.source_sha256.casefold() != scenario.source.sha256.casefold():
        raise LayerSurfaceTerminationError(
            "CONNECTION_ANALYSIS_SOURCE_MISMATCH",
            "connection analysis is not bound to the Scenario source SHA-256",
        )
    scenario_design_fingerprint = scenario.design_fingerprint
    bindings = _casefold_mapping(
        source_via_to_reduced_node, label="source Via"
    )
    models = _model_index(model_by_id)
    if base_owns_terminal_routes and terminal_via_model_by_id:
        raise LayerSurfaceTerminationError(
            "TERMINAL_VIA_OWNERSHIP_CONFLICT",
            "base-owned finite routes cannot also supply local terminal Via models",
        )
    terminal_via_models = _terminal_via_model_index(terminal_via_model_by_id)
    decaps = {
        _key(item.refdes, name="decap REFDES"): item for item in scenario.decaps
    }
    connections = {
        _key(item.refdes, name="connection REFDES"): item
        for item in analysis.connections.values()
    }
    clusters: list[LayerSurfaceTerminationCluster] = []
    consumed: set[str] = set()
    for cluster in analysis.clusters:
        member_keys = tuple(
            _key(item, name="shared-cluster REFDES")
            for item in cluster.member_refdes
        )
        missing_decaps = [key for key in member_keys if key not in decaps]
        missing_connections = [key for key in member_keys if key not in connections]
        if missing_decaps or missing_connections:
            raise LayerSurfaceTerminationError(
                "SHARED_CLUSTER_MEMBER_MISSING",
                f"shared cluster {cluster.cluster_id!r} is incomplete "
                f"(decaps={missing_decaps!r}, connections={missing_connections!r})",
            )
        member_decaps = tuple(decaps[key] for key in member_keys)
        member_connections = tuple(connections[key] for key in member_keys)
        compiled = _compile_shared_cluster(
            scenario,
            scenario_design_fingerprint,
            cluster,
            member_decaps,
            member_connections,
            bindings,
            models,
            terminal_via_models,
            base_owns_terminal_routes=base_owns_terminal_routes,
        )
        if compiled is not None:
            clusters.append(compiled)
        consumed.update(member_keys)

    for key in sorted(decaps):
        decap = decaps[key]
        if key in consumed or not decap.enabled:
            continue
        connection = connections.get(key)
        if connection is None:
            raise LayerSurfaceTerminationError(
                "TERMINATION_CONNECTION_MISSING",
                f"enabled decap {decap.refdes!r} has no connection record",
            )
        clusters.append(
            _compile_direct_cluster(
                scenario,
                scenario_design_fingerprint,
                decap,
                connection,
                bindings,
                models,
                terminal_via_models,
                base_owns_terminal_routes=base_owns_terminal_routes,
            )
        )

    return compile_layer_surface_termination_manifest(
        global_reduced_node_ids, clusters
    )


def compile_scenario_termination_manifest(
    scenario: ScenarioSpec,
    global_reduced_node_ids: Sequence[str],
    source_via_to_reduced_node: Mapping[str, ReducedNodeBinding],
    model_by_id: Mapping[str, ImpedanceModel],
    terminal_via_model_by_id: Mapping[str, ImpedanceModel],
    *,
    base_owns_terminal_routes: bool = False,
) -> CompiledLayerSurfaceTerminationManifest:
    """Compile one mounted state with a bounded exact model-identity cache."""

    with scoped_impedance_model_identity_cache():
        return _compile_scenario_termination_manifest(
            scenario,
            global_reduced_node_ids,
            source_via_to_reduced_node,
            model_by_id,
            terminal_via_model_by_id,
            base_owns_terminal_routes=bool(base_owns_terminal_routes),
        )


__all__ = [
    "ReducedNodeBinding",
    "compile_scenario_termination_manifest",
]
