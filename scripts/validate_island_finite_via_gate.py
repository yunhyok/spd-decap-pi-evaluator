"""Revalidate current island-equivalence and finite-Via gates from a candidate.

The candidate supplies hash-verified retained artwork, terminal anchors, and
compact source ownership evidence.  The named raw SPD is reopened only for the
current same-layer Trace/full-reachability graph pass.  PowerSI Touchstone is
not read and no solver parameter is fitted from it.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_SOURCE_ROOT = (_REPOSITORY_ROOT / "src").resolve()
_EXPECTED_PACKAGE_ROOT = (_REPOSITORY_SOURCE_ROOT / "spd_decap_pi").resolve()
sys.path.insert(0, str(_REPOSITORY_SOURCE_ROOT))

import spd_decap_pi as _runtime_package

_runtime_package_file = getattr(_runtime_package, "__file__", None)
_runtime_package_root = (
    Path(_runtime_package_file).resolve().parent
    if _runtime_package_file is not None
    else None
)
if _runtime_package_root != _EXPECTED_PACKAGE_ROOT:
    raise RuntimeError(
        "active-checkout import guard failed: expected spd_decap_pi from "
        f"{_EXPECTED_PACKAGE_ROOT}, imported {_runtime_package_root!s}. "
        "A stale editable install or preloaded package from another worktree "
        "must not run this validation."
    )

from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.domain import ProjectSpec
from spd_decap_pi._core.io.spd import (
    SpdPadStack,
    SpdSourceInfo,
    recover_spd_ground_reachability,
)
from spd_decap_pi._core.solver import layerwise_network
from spd_decap_pi.compiled_topology_asset import load_compiled_topology_asset
from spd_decap_pi.scenario_io import load_scenario_bundle
from spd_decap_pi.spd_adapter import (
    _anchor_graph_landings,
    _compile_retarget_landing_destination_requests,
    _layer_surface_connectivity_certificate,
    _retained_surface_artwork,
)
from spd_decap_pi.surface_certificate_asset import (
    is_surface_certificate_compiled_only_stub,
)


def _retained_rail_anchor_bindings(
    project: ProjectSpec,
    attachments: Mapping[str, bytes],
    surface_certificate: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Read rail anchors without hydrating compiled-only surface evidence."""

    certificate_view = surface_certificate
    if is_surface_certificate_compiled_only_stub(surface_certificate):
        spd_import = project.metadata.get("spd_import")
        records = (
            spd_import.get("plane_geometries")
            if isinstance(spd_import, Mapping)
            else None
        )
        if not isinstance(records, Sequence) or isinstance(
            records, (str, bytes, bytearray)
        ):
            raise ValueError(
                "candidate is missing retained plane geometry for compiled topology"
            )
        if any(not isinstance(record, Mapping) for record in records):
            raise ValueError(
                "candidate retained plane geometry contains an invalid row"
            )
        artwork_node_ids = tuple(
            str(island_id).strip()
            for record in records
            for island_id in (
                record.get("island_ids", ())
                if isinstance(record.get("island_ids"), Sequence)
                and not isinstance(
                    record.get("island_ids"), (str, bytes, bytearray)
                )
                else ()
            )
        )
        compiled = load_compiled_topology_asset(
            project,
            attachments,
            artwork_node_ids,
        )
        if compiled is None:
            raise ValueError(
                "compiled-only surface evidence is missing its compiled topology"
            )
        certificate_view = compiled.external_port_proof_view

    bindings = certificate_view.get("rail_anchor_bindings")
    if not isinstance(bindings, Sequence) or isinstance(
        bindings, (str, bytes, bytearray)
    ):
        raise ValueError("candidate surface evidence is missing rail anchor bindings")
    if any(not isinstance(row, Mapping) for row in bindings):
        raise ValueError("candidate surface evidence has an invalid rail anchor binding")
    return list(bindings)


def _production_surface_gate(
    project: ProjectSpec,
    records: Sequence[Mapping[str, Any]],
    certificate: Mapping[str, Any],
) -> tuple[dict[str, str] | None, dict[str, Any]]:
    """Exercise the production v4/v3 certificate entrypoint explicitly."""

    try:
        if (
            certificate.get("schema_version")
            == layerwise_network.FINITE_VIA_SURFACE_SCHEMA
        ):
            artwork_node_ids = tuple(
                str(island_id).strip()
                for record in records
                for island_id in (
                    record.get("island_ids", ())
                    if isinstance(record.get("island_ids"), Sequence)
                    and not isinstance(
                        record.get("island_ids"), (str, bytes, bytearray)
                    )
                    else ()
                )
                if str(island_id).strip()
            )
            topology = layerwise_network.compile_finite_via_base_topology(
                project,
                records,
                artwork_node_ids,
                certificate=certificate,
                certificate_verified=True,
            )
            return None, {
                "schema_version": certificate.get("schema_version"),
                "quotient_vertex_count": len(topology.quotient_vertex_ids),
                "external_port_node_count": len(
                    topology.external_port_node_ids
                ),
                "topology_link_count": len(topology.topology_links),
                "finite_link_count": len(topology.finite_links),
                "rail_port_count": len(topology.rail_ports),
                "omitted_rail_count": len(topology.omitted_rail_ids),
                "owner_count": len(topology.owner_edge_by_id),
                "status": "complete",
            }
        layerwise_network._validated_surface_connectivity_certificate(
            project, records
        )
        return None, {
            "schema_version": certificate.get("schema_version"),
            "status": "complete",
        }
    except (
        layerwise_network.LayerwiseNetworkUnavailable,
        layerwise_network.FiniteViaCertificateError,
    ) as exc:
        return {
            "code": str(getattr(exc, "code", "SURFACE_GATE_FAILED")),
            "message": str(exc),
        }, {
            "schema_version": certificate.get("schema_version"),
            "status": "incomplete",
        }


def _first_gate_failure(
    *,
    surface_certificate: Mapping[str, Any],
    terminal_contacts: Sequence[Mapping[str, Any]],
    finite_quotient: Mapping[str, Any],
    scenario_terminal_topology: Mapping[str, Any],
    island_pair_aggregates: Sequence[Mapping[str, Any]],
    surface_gate_error: Mapping[str, Any] | None,
    port_audit: Mapping[str, Any],
    substrate_audit: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return only failures that can actually block the production gate.

    Raw landing rows remain useful diagnostics, but an isolated decap landing
    or a ``source-node:*`` trace anchor intentionally has no direct physical
    Via/contact component.  Their accepted electrical proof lives in the v4
    quotient terminal/scenario manifests and must not outrank those gates.
    """

    certificate_incomplete = (
        str(surface_certificate.get("status", "")).casefold()
        != "complete"
    )
    if certificate_incomplete:
        for row in terminal_contacts:
            if row.get("status") != "complete":
                return {
                    "kind": "terminal_contact",
                    "pin_id": row.get("pin_id"),
                    "net": row.get("net"),
                    "contact_path_kind": row.get("contact_path_kind"),
                    "contact_component_ids": row.get(
                        "contact_component_ids", []
                    ),
                    "issues": row.get("issues", []),
                }
        if finite_quotient.get("status") != "complete":
            return {
                "kind": "finite_via_quotient",
                "status": finite_quotient.get("status"),
                "coverage": finite_quotient.get("coverage"),
                "scenario_isolation_coverage": finite_quotient.get(
                    "scenario_isolation_coverage"
                ),
                "retarget_destination_coverage": finite_quotient.get(
                    "retarget_destination_coverage"
                ),
                "retarget_landing_xy_coverage": finite_quotient.get(
                    "retarget_landing_xy_coverage"
                ),
            }
        if scenario_terminal_topology.get("status") != "complete":
            return {
                "kind": "scenario_decap_terminal_topology",
                "status": scenario_terminal_topology.get("status"),
                "issues": list(
                    scenario_terminal_topology.get("issues", ())
                )[:30],
            }
        for row in island_pair_aggregates:
            if int(row.get("substrate_count") or 0) > 0 and (
                row.get("physical_model_status") != "complete"
                or row.get("component_binding_status") != "complete"
            ):
                return {
                    "kind": "via_island_pair_physical_model",
                    "net": row.get("net"),
                    "padstack": row.get("padstack"),
                    "start_layer": row.get("start_layer"),
                    "end_layer": row.get("end_layer"),
                    "physical_model_issues": row.get(
                        "physical_model_issues", []
                    ),
                    "component_binding_status": row.get(
                        "component_binding_status"
                    ),
                    "component_binding_issues": row.get(
                        "component_binding_issues", []
                    ),
                }
        return {
            "kind": "surface_certificate",
            "status": surface_certificate.get("status"),
        }
    if surface_gate_error is not None:
        return {"kind": "production_surface_gate", **surface_gate_error}
    if port_audit.get("status") != "complete":
        return {"kind": "rail_port_audit", **port_audit}
    if substrate_audit.get("status") != "complete":
        return {"kind": "substrate_audit", **substrate_audit}
    return None


def _via_v2_certificate(
    old_certificate: dict[str, Any],
    device_certificate: dict[str, Any],
    connection_rows: dict[str, Any],
    *,
    source_sha256: str,
) -> dict[str, Any]:
    decap_ids: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for row in connection_rows.values():
        for role in ("power_vias", "ground_vias"):
            for via in row.get(role, ()):
                decap_ids[
                    (via["net"].casefold(), via["padstack"].casefold())
                ].add(via["via_id"].casefold())

    device_ids: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    incomplete_by_group: defaultdict[tuple[str, str], set[str]] = defaultdict(
        set
    )
    unmapped_incomplete: set[str] = set()
    complete_device_ids: set[str] = set()
    device_owner_by_via: dict[str, tuple[str, str, str, str]] = {}
    for row in device_certificate["terminals"]:
        endpoint_id = str(row.get("endpoint_id", ""))
        incident_net = str(row.get("incident_net") or "").casefold()
        incident_padstack = str(row.get("incident_padstack") or "").casefold()
        incident_via = str(row.get("incident_via_id") or "").casefold()
        if row.get("status") == "complete":
            owner = (
                endpoint_id.casefold(),
                incident_net,
                incident_padstack,
                str(row.get("incident_opposite_node_id") or "").casefold(),
            )
            previous_owner = device_owner_by_via.get(incident_via)
            if previous_owner is not None and previous_owner != owner:
                raise ValueError(
                    "candidate assigns one physical Device Via ID to multiple "
                    "terminal endpoints"
                )
            device_owner_by_via[incident_via] = owner
            device_ids[(incident_net, incident_padstack)].add(incident_via)
            complete_device_ids.add(incident_via)
        elif endpoint_id and incident_net and incident_padstack:
            incomplete_by_group[(incident_net, incident_padstack)].add(endpoint_id)
        elif endpoint_id:
            unmapped_incomplete.add(endpoint_id)

    groups: list[dict[str, Any]] = []
    group_keys: set[tuple[str, str]] = set()
    for source in old_certificate["groups"]:
        row = deepcopy(source)
        key = (str(row["net"]).casefold(), str(row["padstack"]).casefold())
        group_keys.add(key)
        owned_ids = sorted(decap_ids[key] | device_ids[key])
        incomplete_ids = sorted(incomplete_by_group[key])
        issues: list[str] = []
        if len(owned_ids) > int(row["count"]):
            issues.append("terminal_owned_count_exceeds_via_count")
        if incomplete_ids:
            issues.append("incomplete_device_terminal_endpoint_affects_group")
        row.update(
            {
                "terminal_owned_count": len(owned_ids),
                "terminal_owned_via_ids_sha256": (
                    core_services._canonical_metadata_sha256(owned_ids)
                ),
                "terminal_ownership_method": (
                    "exact-decap-landing-and-device-incident-via-id-v2"
                ),
                "incomplete_device_terminal_endpoint_count": len(incomplete_ids),
                "incomplete_device_terminal_endpoint_ids_sha256": (
                    core_services._canonical_metadata_sha256(incomplete_ids)
                ),
                "ownership_status": "complete" if not issues else "unresolved",
                "ownership_issues": issues,
                "substrate_count": (
                    int(row["count"]) - len(owned_ids) if not issues else None
                ),
            }
        )
        groups.append(row)

    affecting = {
        endpoint
        for key, endpoints in incomplete_by_group.items()
        if key in group_keys
        for endpoint in endpoints
    }
    nonaffecting = set(unmapped_incomplete) | {
        endpoint
        for key, endpoints in incomplete_by_group.items()
        if key not in group_keys
        for endpoint in endpoints
    }
    incomplete_groups = [row for row in groups if row["status"] != "complete"]
    unresolved_groups = [
        row for row in groups if row["ownership_status"] != "complete"
    ]
    payload = {
        "schema_version": "spd-layerwise-via-groups-v2",
        "compiler_id": "powersi-via-usage-padstack-terminal-ownership-v2",
        "source_sha256": source_sha256,
        "raw_spd_embedded": False,
        "scope": deepcopy(old_certificate["scope"]),
        "groups": groups,
        "group_count": len(groups),
        "complete_group_count": len(groups) - len(incomplete_groups),
        "ownership_resolved_group_count": len(groups) - len(unresolved_groups),
        "device_terminal_via_evidence_sha256": device_certificate[
            "evidence_sha256"
        ],
        "device_terminal_certificate_status": device_certificate["status"],
        "complete_device_terminal_owned_via_count": len(complete_device_ids),
        "affecting_incomplete_device_terminal_endpoint_count": len(affecting),
        "nonaffecting_incomplete_device_terminal_endpoint_count": len(
            nonaffecting
        ),
        "nonaffecting_incomplete_device_terminal_endpoint_ids_sha256": (
            core_services._canonical_metadata_sha256(sorted(nonaffecting))
        ),
        "missing_power_nets": deepcopy(old_certificate["missing_power_nets"]),
        "missing_ground_nets": deepcopy(old_certificate["missing_ground_nets"]),
        "status": (
            "complete"
            if len(source_sha256) == 64
            and not incomplete_groups
            and not unresolved_groups
            and not old_certificate["missing_power_nets"]
            and bool(groups)
            else "incomplete"
        ),
    }
    return {
        **payload,
        "evidence_sha256": core_services._canonical_metadata_sha256(payload),
    }


def _padstacks_from_v2_certificate(
    certificate: dict[str, Any],
) -> tuple[SpdPadStack, ...]:
    """Rehydrate only source fields already hash-bound by the candidate."""

    rows: dict[str, SpdPadStack] = {}
    for group in certificate.get("groups", ()):
        name = str(group.get("padstack", "")).strip()
        if not name:
            continue
        row = SpdPadStack(
            name=name,
            drill_diameter_um=group.get("drill_diameter_um"),
            pad_width_um=group.get("pad_width_um"),
            pad_height_um=group.get("pad_height_um"),
            layers=tuple(group.get("declared_layers", ())),
            material=group.get("material"),
        )
        key = name.casefold()
        previous = rows.get(key)
        if previous is not None and previous != row:
            raise ValueError(
                f"candidate has conflicting v2 PadStack evidence for {name!r}"
            )
        rows[key] = row
    return tuple(rows[key] for key in sorted(rows))


def validate(
    raw_spd: Path,
    candidate: Path,
    output: Path,
    *,
    case: str,
) -> dict[str, Any]:
    started = perf_counter()
    progress_path = output.with_suffix(".progress.ndjson")
    output.parent.mkdir(parents=True, exist_ok=True)
    with progress_path.open("w", encoding="utf-8") as progress_file:

        def log(stage: str, value: int, message: str) -> None:
            row = {
                "elapsed_s": round(perf_counter() - started, 3),
                "stage": stage,
                "progress": int(value),
                "message": str(message),
            }
            progress_file.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            )
            progress_file.flush()

        log("bundle", 0, "Loading persisted candidate evidence")
        bundle = load_scenario_bundle(candidate)
        project = ProjectSpec.model_validate(bundle.scenario.normalized_project)
        spd_import = project.metadata["spd_import"]
        source_sha256 = str(spd_import["source_sha256"]).casefold()
        raw_stat = raw_spd.stat()
        if (
            str(bundle.scenario.source.sha256).casefold() != source_sha256
            or int(bundle.scenario.source.size_bytes) != int(raw_stat.st_size)
        ):
            raise ValueError(
                "candidate source identity does not match the named raw SPD size/hash manifest"
            )
        expected_source = SpdSourceInfo(
            path=raw_spd.resolve(),
            name=raw_spd.name,
            size_bytes=int(raw_stat.st_size),
            mtime_ns=int(raw_stat.st_mtime_ns),
            sha256=source_sha256,
            title="direct current-gate source identity",
        )
        old_surface = spd_import["layerwise_surface_connectivity_certificate"]
        device_certificate = spd_import[
            "layerwise_device_terminal_via_certificate"
        ]
        endpoints = tuple(
            SimpleNamespace(**row) for row in device_certificate["terminals"]
        )
        bindings = _retained_rail_anchor_bindings(
            project,
            bundle.attachments,
            old_surface,
        )
        landing_by_pin, contact_seeds = _anchor_graph_landings(bindings, endpoints)
        log(
            "bundle",
            100,
            f"Loaded {len(bindings)} bindings and {len(landing_by_pin)} landings",
        )

        connection_analysis = bundle.scenario.connection_analysis
        if connection_analysis is None:
            raise ValueError("candidate is missing shared-pad connection analysis")
        connection_rows = {
            refdes: item.model_dump(mode="json")
            for refdes, item in connection_analysis.connections.items()
        }
        scenario_topology_connection_kinds = {
            "DIRECT",
            "SHARED_ANCHOR",
            "SHARED_DUMMY",
        }
        certificate_connections = tuple(
            item
            for item in connection_analysis.connections.values()
            if str(getattr(item.kind, "value", item.kind)).strip().upper()
            in scenario_topology_connection_kinds
        )
        via_certificate = _via_v2_certificate(
            spd_import["layerwise_via_group_certificate"],
            device_certificate,
            connection_rows,
            source_sha256=source_sha256,
        )
        spd_import["layerwise_via_group_certificate"] = via_certificate
        physical_padstacks = _padstacks_from_v2_certificate(via_certificate)
        layerwise_network._validated_via_certificate(project)
        surfaces = {
            layerwise_network._surface_node_id(row["layer"], row["net"])
            for row in spd_import["plane_geometries"]
        }
        finite_links, finite_counts = layerwise_network._compile_via_links(
            project, surfaces, via_certificate
        )
        log(
            "via",
            100,
            f"Validated {len(via_certificate['groups'])} groups and compiled "
            f"{len(finite_links)} finite links",
        )

        (
            _covers,
            resolver,
            strict_resolver,
            geometry_assets,
            target_layers,
            island_inventory,
        ) = _retained_surface_artwork(
            project,
            bundle.attachments,
            progress=lambda value, message: log("artwork", value, message),
        )
        (
            retarget_landing_destination_requests,
            retarget_landing_scan_coverage,
        ) = _compile_retarget_landing_destination_requests(
            project=project,
            decap_connections=certificate_connections,
            geometry_assets=geometry_assets,
            strict_island_resolver=strict_resolver,
        )
        target_net_keys = set(target_layers)
        decap_terminal_landings = tuple(
            landing
            for connection in certificate_connections
            for landing in (*connection.power_vias, *connection.ground_vias)
            if landing.net.casefold() in target_net_keys
        )
        retarget_destination_requests = tuple(
            (
                landing.net,
                evidence.target_layer,
                evidence.target_node_id,
            )
            for landing in decap_terminal_landings
            for evidence in landing.path_evidence
        )
        terminal_contact_landings = (
            *decap_terminal_landings,
            *landing_by_pin.values(),
        )
        terminal_owned_via_ids = {
            str(getattr(landing, "via_id", "")).strip()
            for landing in decap_terminal_landings
            if str(getattr(landing, "via_id", "")).strip()
        }
        terminal_owned_via_ids.update(
            str(row.get("incident_via_id") or "").strip()
            for row in device_certificate["terminals"]
            if row.get("status") == "complete"
            and str(row.get("incident_net") or "").casefold()
            in target_net_keys
            and str(row.get("incident_via_id") or "").strip()
        )
        reachability = recover_spd_ground_reachability(
            raw_spd,
            landings=tuple(landing_by_pin.values()),
            terminal_contact_landings=terminal_contact_landings,
            scenario_isolated_terminal_landings=decap_terminal_landings,
            retarget_destination_requests=retarget_destination_requests,
            terminal_owned_via_ids=terminal_owned_via_ids,
            padstacks=physical_padstacks,
            stackup_layers=project.stackup_layers,
            target_layers_by_net=target_layers,
            target_node_predicate=None,
            target_node_surface_resolver=resolver,
            target_surface_island_ids=island_inventory,
            expected_source=expected_source,
            include_traces=True,
            progress=lambda value, message: log("reachability", value, message),
        )
        surface_certificate = _layer_surface_connectivity_certificate(
            project=project,
            source_sha256=source_sha256,
            geometry_assets=geometry_assets,
            rail_anchor_bindings=bindings,
            compile_failures=[],
            contact_seeds=contact_seeds,
            landing_by_pin=landing_by_pin,
            reachability=reachability,
            decap_connections=certificate_connections,
            shared_pad_clusters=connection_analysis.clusters,
            retarget_landing_destination_requests=(
                retarget_landing_destination_requests
            ),
            retarget_landing_scan_coverage=retarget_landing_scan_coverage,
        )
        spd_import["layerwise_surface_connectivity_certificate"] = (
            surface_certificate
        )
        surface_gate_error, production_compile = _production_surface_gate(
            project,
            tuple(spd_import["plane_geometries"]),
            surface_certificate,
        )

        proofs = tuple(surface_certificate["surface_equivalence_proofs"])
        equivalence_components = tuple(
            surface_certificate["surface_equivalence_components"]
        )
        incomplete_proofs = [
            row for row in proofs if row["status"] != "complete"
        ]
        terminal_contacts = tuple(surface_certificate["terminal_contacts"])
        terminal_landing_contacts = tuple(
            surface_certificate["terminal_landing_contacts"]
        )
        island_pair_aggregates = tuple(
            surface_certificate["via_island_pair_aggregates"]
        )
        via_pair_coverage = surface_certificate["via_island_pair_coverage"]
        finite_quotient = surface_certificate["finite_via_quotient"]
        finite_coverage = finite_quotient["coverage"]
        scenario_terminal_topology = surface_certificate[
            "scenario_decap_terminal_topology"
        ]
        project_rail_ids = {
            str(rail.rail_id).strip().casefold()
            for rail in project.rails
            if str(rail.rail_id).strip()
        }
        roles_by_branch: defaultdict[tuple[str, str], set[str]] = defaultdict(
            set
        )
        branch_ids_by_rail: defaultdict[str, set[str]] = defaultdict(set)
        binding_pin_ids: set[str] = set()
        for row in bindings:
            rail_key = str(row.get("rail_id", "")).strip().casefold()
            branch_key = str(row.get("branch_id", "")).strip().casefold()
            role = str(row.get("role", "")).strip().casefold()
            pin_key = str(row.get("pin_id", "")).strip().casefold()
            if rail_key and branch_key:
                branch_ids_by_rail[rail_key].add(branch_key)
                if role:
                    roles_by_branch[(rail_key, branch_key)].add(role)
            if pin_key:
                binding_pin_ids.add(pin_key)
        complete_contact_pin_ids = {
            str(row.get("pin_id", "")).strip().casefold()
            for row in terminal_contacts
            if row.get("status") == "complete"
            and str(row.get("pin_id", "")).strip()
        }
        fully_bound_rail_ids = {
            rail_key
            for rail_key in project_rail_ids
            if branch_ids_by_rail.get(rail_key)
            and all(
                roles_by_branch[(rail_key, branch_key)]
                == {"power", "ground"}
                for branch_key in branch_ids_by_rail[rail_key]
            )
        }
        port_audit = {
            "selected_rail_count": len(project_rail_ids),
            "fully_bound_rail_count": len(fully_bound_rail_ids),
            "branch_count": sum(len(rows) for rows in branch_ids_by_rail.values()),
            "fully_bound_branch_count": sum(
                roles == {"power", "ground"}
                for roles in roles_by_branch.values()
            ),
            "binding_count": len(bindings),
            "binding_pin_count": len(binding_pin_ids),
            "complete_binding_pin_count": len(
                binding_pin_ids & complete_contact_pin_ids
            ),
            "missing_or_incomplete_binding_pin_count": len(
                binding_pin_ids - complete_contact_pin_ids
            ),
            "status": (
                "complete"
                if fully_bound_rail_ids == project_rail_ids
                and binding_pin_ids <= complete_contact_pin_ids
                else "incomplete"
            ),
        }
        substrate_aggregates = tuple(
            row
            for row in island_pair_aggregates
            if int(row.get("substrate_count") or 0) > 0
        )
        substrate_audit = {
            "aggregate_count": len(substrate_aggregates),
            "population_count": sum(
                int(row.get("substrate_count") or 0)
                for row in substrate_aggregates
            ),
            "component_binding_complete_count": sum(
                row.get("component_binding_status") == "complete"
                for row in substrate_aggregates
            ),
            "physical_model_complete_count": sum(
                row.get("physical_model_status") == "complete"
                for row in substrate_aggregates
            ),
            "status": (
                "complete"
                if all(
                    row.get("component_binding_status") == "complete"
                    and row.get("physical_model_status") == "complete"
                    for row in substrate_aggregates
                )
                else "incomplete"
            ),
        }
        first_failure = _first_gate_failure(
            surface_certificate=surface_certificate,
            terminal_contacts=terminal_contacts,
            finite_quotient=finite_quotient,
            scenario_terminal_topology=scenario_terminal_topology,
            island_pair_aggregates=island_pair_aggregates,
            surface_gate_error=surface_gate_error,
            port_audit=port_audit,
            substrate_audit=substrate_audit,
        )
        non_gating_terminal_landing_diagnostics = {
            "policy": (
                "raw_landing_status_is_diagnostic_quotient_and_scenario_"
                "manifests_are_authoritative"
            ),
            "row_count": len(terminal_landing_contacts),
            "noncomplete_status_count": sum(
                row.get("status") != "complete"
                for row in terminal_landing_contacts
            ),
            "physical_incomplete_count": sum(
                row.get("physical_model_status") != "complete"
                for row in terminal_landing_contacts
            ),
            "component_binding_incomplete_count": sum(
                row.get("component_binding_status") != "complete"
                for row in terminal_landing_contacts
            ),
            "source_node_trace_anchor_count": sum(
                str(row.get("via_id") or "")
                .strip()
                .casefold()
                .startswith("source-node:")
                for row in terminal_landing_contacts
            ),
            "scenario_isolated_base_contact_count": sum(
                bool(row.get("scenario_isolated_base_contact"))
                for row in terminal_landing_contacts
            ),
        }
        groups = tuple(via_certificate["groups"])
        result = {
            "case": case,
            "raw_spd": str(raw_spd),
            "candidate": str(candidate),
            "elapsed_s": round(perf_counter() - started, 3),
            "source_sha256": source_sha256,
            "raw_spd_size_bytes": int(raw_stat.st_size),
            "raw_spd_mtime_ns": int(raw_stat.st_mtime_ns),
            "raw_spd_hash_verified_during_graph_pass": True,
            "surface": {
                "schema_version": surface_certificate["schema_version"],
                "compiler_id": surface_certificate["compiler_id"],
                "evidence_sha256": surface_certificate["evidence_sha256"],
                "status": surface_certificate["status"],
                "production_gate_error": surface_gate_error,
                "production_compile": production_compile,
                "first_failure_scope": "production_gates_only",
                "non_gating_terminal_landing_diagnostics": (
                    non_gating_terminal_landing_diagnostics
                ),
                "proof_count": len(proofs),
                "proof_status_counts": dict(
                    sorted(Counter(row["status"] for row in proofs).items())
                ),
                "component_contact_status_counts": dict(
                    sorted(
                        Counter(
                            row["contact_status"]
                            for row in equivalence_components
                        ).items()
                    )
                ),
                "artwork_island_count": sum(
                    len(row["island_ids"]) for row in proofs
                ),
                "multi_island_surface_count": sum(
                    len(row["island_ids"]) > 1 for row in proofs
                ),
                "max_islands_per_surface": max(
                    (len(row["island_ids"]) for row in proofs), default=0
                ),
                "noncomplete_proof_count": len(incomplete_proofs),
                "noncomplete_proof_examples": [
                    {
                        "net": row["net"],
                        "layer": row["layer"],
                        "status": row["status"],
                        "islands": len(row["island_ids"]),
                        "contacted": len(row["contacted_island_ids"]),
                        "graph_components": row["graph_component_count"],
                    }
                    for row in incomplete_proofs[:30]
                ],
                "terminal_contact_status_counts": dict(
                    sorted(
                        Counter(row["status"] for row in terminal_contacts).items()
                    )
                ),
                "terminal_contact_path_kind_counts": dict(
                    sorted(
                        Counter(
                            row["contact_path_kind"]
                            for row in terminal_contacts
                        ).items()
                    )
                ),
                "terminal_landing_contact_count": len(
                    terminal_landing_contacts
                ),
                "terminal_landing_contact_status_counts": dict(
                    sorted(
                        Counter(
                            row["status"] for row in terminal_landing_contacts
                        ).items()
                    )
                ),
                "terminal_landing_physical_status_counts": dict(
                    sorted(
                        Counter(
                            row["physical_model_status"]
                            for row in terminal_landing_contacts
                        ).items()
                    )
                ),
                "terminal_landing_component_binding_status_counts": dict(
                    sorted(
                        Counter(
                            row["component_binding_status"]
                            for row in terminal_landing_contacts
                        ).items()
                    )
                ),
                "terminal_owner_kind_counts": dict(
                    sorted(
                        Counter(
                            row["terminal_owner_kind"]
                            for row in terminal_landing_contacts
                        ).items()
                    )
                ),
                "via_island_pair_aggregate_count": len(
                    island_pair_aggregates
                ),
                "via_island_pair_physical_status_counts": dict(
                    sorted(
                        Counter(
                            row["physical_model_status"]
                            for row in island_pair_aggregates
                        ).items()
                    )
                ),
                "via_island_pair_component_binding_status_counts": dict(
                    sorted(
                        Counter(
                            row["component_binding_status"]
                            for row in island_pair_aggregates
                        ).items()
                    )
                ),
                "via_island_pair_coverage": via_pair_coverage,
                "finite_via_quotient": {
                    "status": finite_quotient["status"],
                    "vertex_count": len(finite_quotient["vertices"]),
                    "edge_count": len(finite_quotient["edges"]),
                    "retained_explicit_edge_count": sum(
                        row.get("mode") == "retained_explicit"
                        for row in finite_quotient["edges"]
                    ),
                    "contracted_series_edge_count": sum(
                        row.get("mode") == "contracted_series"
                        for row in finite_quotient["edges"]
                    ),
                    "raw_owner_count": sum(
                        len(row.get("owner_ids", ()))
                        for row in finite_quotient["edges"]
                    ),
                    "raw_owner_unique_count": len(
                        {
                            str(owner_id).casefold()
                            for row in finite_quotient["edges"]
                            for owner_id in row.get("owner_ids", ())
                        }
                    ),
                    "coverage": finite_coverage,
                    "scenario_isolation_coverage": finite_quotient.get(
                        "scenario_isolation_coverage"
                    ),
                    "retarget_destination_binding_count": len(
                        finite_quotient.get(
                            "retarget_destination_bindings", ()
                        )
                    ),
                    "retarget_destination_coverage": finite_quotient.get(
                        "retarget_destination_coverage"
                    ),
                    "retarget_landing_xy_binding_count": len(
                        finite_quotient.get(
                            "retarget_landing_xy_bindings", ()
                        )
                    ),
                    "retarget_landing_xy_coverage": finite_quotient.get(
                        "retarget_landing_xy_coverage"
                    ),
                    "incomplete_edge_examples": [
                        {
                            "edge_id": row.get("edge_id"),
                            "mode": row.get("mode"),
                            "endpoint_binding_issues": row.get(
                                "endpoint_binding_issues", []
                            ),
                            "physical_model_issues": row.get(
                                "physical_model_issues", []
                            ),
                        }
                        for row in finite_quotient["edges"]
                        if row.get("status") != "complete"
                    ][:30],
                },
                "scenario_decap_terminal_topology": {
                    "status": scenario_terminal_topology["status"],
                    "vertex_count": len(
                        scenario_terminal_topology.get("vertices", ())
                    ),
                    "conditional_contact_count": len(
                        scenario_terminal_topology.get(
                            "conditional_contacts", ()
                        )
                    ),
                    "conditional_shared_link_count": len(
                        scenario_terminal_topology.get(
                            "conditional_shared_links", ()
                        )
                    ),
                    "gap_cut_proof_count": len(
                        scenario_terminal_topology.get("gap_cut_proofs", ())
                    ),
                    "complete_gap_cut_proof_count": sum(
                        row.get("status") == "complete"
                        for row in scenario_terminal_topology.get(
                            "gap_cut_proofs", ()
                        )
                    ),
                    "issues": scenario_terminal_topology.get("issues", []),
                },
                "rail_port_audit": port_audit,
                "substrate_audit": substrate_audit,
                "first_failure": first_failure,
                "recovery_statistics": surface_certificate[
                    "recovery_statistics"
                ],
            },
            "via": {
                "schema_version": via_certificate["schema_version"],
                "compiler_id": via_certificate["compiler_id"],
                "evidence_sha256": via_certificate["evidence_sha256"],
                "status": via_certificate["status"],
                "group_count": via_certificate["group_count"],
                "complete_group_count": via_certificate["complete_group_count"],
                "ownership_resolved_group_count": via_certificate[
                    "ownership_resolved_group_count"
                ],
                "complete_device_terminal_owned_via_count": via_certificate[
                    "complete_device_terminal_owned_via_count"
                ],
                "affecting_incomplete_device_terminal_endpoint_count": (
                    via_certificate[
                        "affecting_incomplete_device_terminal_endpoint_count"
                    ]
                ),
                "nonaffecting_incomplete_device_terminal_endpoint_count": (
                    via_certificate[
                        "nonaffecting_incomplete_device_terminal_endpoint_count"
                    ]
                ),
                "terminal_owned_count_total": sum(
                    row["terminal_owned_count"] for row in groups
                ),
                "substrate_count_total": sum(
                    int(row["substrate_count"] or 0) for row in groups
                ),
                "finite_link_count": len(finite_links),
                "finite_link_population_count": sum(
                    link.count for link in finite_links
                ),
                "finite_compile_statistics": dict(finite_counts),
                "finite_link_mode_counts": dict(
                    sorted(Counter(link.mode for link in finite_links).items())
                ),
                "topology_only_ideal_link_count": sum(
                    link.mode == "topology_only_ideal" for link in finite_links
                ),
            },
        }
        temporary = output.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
        log("complete", 100, f"Wrote {output}")
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--raw-spd", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(
        args.raw_spd,
        args.candidate,
        args.output,
        case=args.case,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
