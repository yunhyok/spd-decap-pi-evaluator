#!/usr/bin/env python3
"""Bounded source-bound boundary audit for the loaded SRAM development rail."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Iterable
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from spd_decap_pi._core import services as core_services


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
RAIL = "ADC_VDD_075_VTRIP_SRAM/0"
REFERENCE_NET = "DGND"
PWR_LAYER = "Signal$L14(MAIN_POWER4)"
GND_LAYER = "Signal$L13(DGND)"
PWR_COMPONENT = "spd-surface-equivalence-component:c10b41be0dd935e2f2a20a3c"
EXPECTED_SOURCE_SHA256 = "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
EXPECTED_SCENARIO_SHA256 = "2f5ae107221857f7f093ab8d6cbcff4c286a0c6cee5b6a9994de1c384cf39c83"
EXPECTED_PWR_ASSET_SHA256 = "eb5c10758ed3e08d19db505d10e3efe35d1c764145fbe29d457719075b2c7838"
EXPECTED_GND_ASSET_SHA256 = "1c42beade69d1493eb00d6c6cb61bfd3c05320ef657571661f5873817ffc0d6a"
DEFAULT_BUNDLE = Path(
    r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab"
    r"\260902-d115b-source-plane-ownership-materialization-04"
    r"\source_plane_ownership_candidate.spdpi"
)
RAW_DB = ROOT / "outputs/research/astra-step4-basis-01/indexes/raw-spatial.sqlite"
COMPILED_DB = ROOT / "outputs/research/astra-step4-basis-01/indexes/compiled-topology.sqlite"
OWNERSHIP_DB = ROOT / "outputs/research/astra-step4-basis-01/ownership.sqlite"
DEFAULT_OUTPUT = (
    ROOT
    / "outputs/research/astra-step6e-loaded-boundary-01"
    / "loaded-sheet-candidate-final.json"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    )


def _open_db(path: Path) -> sqlite3.Connection:
    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _check_time(started: float, maximum_s: float) -> None:
    if time.monotonic() - started >= maximum_s:
        raise TimeoutError("loaded sheet candidate audit exceeded its runtime bound")


def _iter_rows(
    connection: sqlite3.Connection,
    sql: str,
    args: tuple[Any, ...],
    *,
    started: float,
    maximum_s: float,
) -> Iterable[tuple[Any, ...]]:
    cursor = connection.execute(sql, args)
    while True:
        _check_time(started, maximum_s)
        batch = cursor.fetchmany(20_000)
        if not batch:
            return
        yield from batch


def _flat_node(
    connection: sqlite3.Connection,
    flat: int,
    counts: dict[int, int],
) -> dict[str, Any]:
    offset = 0
    for kind in sorted(counts):
        next_offset = offset + counts[kind]
        if offset <= flat < next_offset:
            local = flat - offset
            row = connection.execute(
                "SELECT node_id FROM nodes WHERE kind=? AND ordinal=?", (kind, local)
            ).fetchone()
            if row is None:
                raise ValueError("flat node did not resolve")
            return {"flat_index": flat, "kind": kind, "local_ordinal": local, "node_id": row[0]}
        offset = next_offset
    raise ValueError("flat node is outside persisted node groups")


def _geometry_stats(archive: ZipFile, record: dict[str, Any]) -> dict[str, Any]:
    name = "attachments/" + record["asset"]
    compressed = archive.read(name)
    digest = _sha256(compressed)
    if digest != record["asset_sha256"]:
        raise ValueError(f"geometry asset hash mismatch: {name}")
    payload = core_services._decode_spd_geometry_asset(digest, compressed)
    shape = core_services._ordered_spd_geometry(payload)
    if shape is None or shape.is_empty or not shape.is_valid:
        raise ValueError(f"geometry asset does not produce a valid non-empty shape: {name}")
    components = (shape,) if shape.geom_type == "Polygon" else tuple(shape.geoms)
    return {
        "member": record["asset"],
        "compressed_size_bytes": len(compressed),
        "compressed_sha256": digest,
        "format": payload.get("format"),
        "layer": payload.get("layer"),
        "net": payload.get("net"),
        "positive_polygon_count": len(payload["positive_polygons_um"]),
        "negative_polygon_count": len(payload["negative_polygons_um"]),
        "positive_circle_count": len(payload["positive_circles_um"]),
        "negative_circle_count": len(payload["negative_circles_um"]),
        "primitive_order_count": len(payload["primitive_order"]),
        "ordered_shape_type": shape.geom_type,
        "component_count": len(components),
        "hole_count": sum(len(item.interiors) for item in components),
        "area_um2": float(shape.area),
        "bounds_um": [float(value) for value in shape.bounds],
    }


def build_result(bundle: Path, maximum_s: float) -> dict[str, Any]:
    started = time.monotonic()
    with ZipFile(bundle) as archive:
        scenario_bytes = archive.read("scenario.json")
        if _sha256(scenario_bytes) != EXPECTED_SCENARIO_SHA256:
            raise ValueError("scenario member hash differs from the pinned D115b basis")
        scenario = json.loads(scenario_bytes)
        if scenario.get("app_version") != VERSION or scenario["source"]["sha256"] != EXPECTED_SOURCE_SHA256:
            raise ValueError("scenario program/source identity differs from the pinned basis")

        selected = [
            item
            for item in scenario["decaps"]
            if item["enabled"] and item["source_mounted"] and item["current_rail_id"] == RAIL
        ]
        if len(selected) != 421 or len({item["refdes"] for item in selected}) != 421:
            raise ValueError("selected source-mounted population is not 421 unique refdes")
        same_source_state = all(
            item["source_rail_id"] == item["current_rail_id"] == RAIL
            and item["source_net"] == item["current_net"] == RAIL
            for item in selected
        )
        if not same_source_state:
            raise ValueError("selected development population contains a rerouted member")
        connections = scenario["connection_analysis"]["connections"]
        selected_connections = [connections[item["refdes"]] for item in selected]
        if len(selected_connections) != 421:
            raise ValueError("selected connection join is incomplete")
        power_vias = {
            landing["via_id"].casefold(): landing
            for row in selected_connections
            for landing in row.get("power_vias", ())
        }
        ground_vias = {
            landing["via_id"].casefold(): landing
            for row in selected_connections
            for landing in row.get("ground_vias", ())
        }
        if len(power_vias) != 450 or len(ground_vias) != 450:
            raise ValueError("selected physical source contact count differs from 450 per role")

        compiled = _open_db(COMPILED_DB)
        raw = _open_db(RAW_DB)
        ownership = _open_db(OWNERSHIP_DB)
        try:
            node_counts = {
                int(kind): int(count)
                for kind, count in compiled.execute(
                    "SELECT kind,COUNT(*) FROM nodes GROUP BY kind ORDER BY kind"
                )
            }
            port = compiled.execute(
                "SELECT ordinal,positive_node,negative_node FROM rail_ports WHERE rail_id=?",
                (RAIL,),
            ).fetchone()
            if port is None:
                raise ValueError("target rail port is absent")
            port_ordinal, positive_flat, negative_flat = map(int, port)
            positive_node = _flat_node(compiled, positive_flat, node_counts)
            negative_node = _flat_node(compiled, negative_flat, node_counts)
            if positive_node["node_id"] != "spd-device-port-node:820d47f6bf1a0a52a37b4706":
                raise ValueError("positive Device node identity mismatch")
            if negative_node["node_id"] != "spd-finite-via-vertex:1e9e3a9fb6978e92411bce4c":
                raise ValueError("negative Device node identity mismatch")

            members = {
                kind: {
                    value
                    for (value,) in compiled.execute(
                        "SELECT value FROM rail_port_members WHERE port_ordinal=? AND kind=?",
                        (port_ordinal, kind),
                    )
                }
                for kind in range(4)
            }
            external = json.loads(
                compiled.execute("SELECT payload FROM views WHERE name='external'").fetchone()[0]
            )
            surface = json.loads(
                compiled.execute("SELECT payload FROM views WHERE name='surface'").fetchone()[0]
            )
            scenario_view = json.loads(
                compiled.execute("SELECT payload FROM views WHERE name='scenario'").fetchone()[0]
            )
            anchor_rows = [row for row in external["rail_anchor_bindings"] if row["rail_id"] == RAIL]
            contact_index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in external["terminal_contacts"]:
                contact_index[(row["pin_id"], row["net"])].append(row)
            contact_by_role: dict[str, list[dict[str, Any]]] = {}
            for role, net in (("power", RAIL), ("ground", REFERENCE_NET)):
                rows = [row for row in anchor_rows if row["role"] == role]
                contacts: list[dict[str, Any]] = []
                for row in rows:
                    matches = contact_index[(row["pin_id"], net)]
                    if len(matches) != 1 or matches[0]["status"] != "complete":
                        raise ValueError("Device pin-to-contact join is not exact and complete")
                    contacts.append(matches[0])
                contact_by_role[role] = contacts
            power_pin_set = {row["pin_id"] for row in anchor_rows if row["role"] == "power"}
            ground_pin_set = {row["pin_id"] for row in anchor_rows if row["role"] == "ground"}
            power_anchor_set = {row["exposed_quotient_vertex_id"] for row in contact_by_role["power"]}
            ground_anchor_set = {row["exposed_quotient_vertex_id"] for row in contact_by_role["ground"]}
            if not (
                members[0] == power_pin_set
                and members[1] == ground_pin_set
                and members[2] == power_anchor_set
                and members[3] == ground_anchor_set
                and tuple(map(len, (members[0], members[1], members[2], members[3]))) == (978, 978, 45, 1)
            ):
                raise ValueError("rail port member semantics disagree with exact view joins")

            topology_links = list(
                compiled.execute(
                    "SELECT ordinal,link_id,first_node,second_node FROM links WHERE kind=0"
                )
            )
            star_links = [
                row
                for row in topology_links
                if positive_flat in (int(row[2]), int(row[3]))
            ]
            anchor_ordinals = {
                int(ordinal)
                for ordinal, node_id in compiled.execute(
                    "SELECT ordinal,node_id FROM nodes WHERE kind=0"
                )
                if node_id in power_anchor_set
            }
            star_neighbors = {
                int(second) if int(first) == positive_flat else int(first)
                for _, _, first, second in star_links
            }
            if len(star_links) != 45 or star_neighbors != anchor_ordinals:
                raise ValueError("Device positive supernode is not an exact 45-anchor star")

            conditional = scenario_view["scenario_decap_terminal_topology"]["conditional_contacts"]
            power_contacts = [
                row
                for row in conditional
                if str(row["landing_key"][0]).casefold() in power_vias
            ]
            ground_contacts = [
                row
                for row in conditional
                if str(row["landing_key"][0]).casefold() in ground_vias
            ]
            if (
                len(power_contacts) != 450
                or len(ground_contacts) != 450
                or any(row["status"] != "complete" for row in power_contacts + ground_contacts)
            ):
                raise ValueError("actual 421 conditional source contact join is incomplete")

            candidates = [
                row
                for row in surface["surface_equivalence_components"]
                if row["component_id"] == PWR_COMPONENT
                and row["net"] == RAIL.casefold()
                and row["layer"] == PWR_LAYER
            ]
            if len(candidates) != 1:
                raise ValueError("the selected L14 component is absent or ambiguous")
            candidate = candidates[0]
            geometry_records = {
                (row["net"].casefold(), row["layer"]): row
                for row in surface["geometry_assets"]
            }
            power_geometry_record = geometry_records[(RAIL.casefold(), PWR_LAYER)]
            ground_geometry_record = geometry_records[(REFERENCE_NET.casefold(), GND_LAYER)]
            if (
                power_geometry_record["asset_sha256"] != EXPECTED_PWR_ASSET_SHA256
                or ground_geometry_record["asset_sha256"] != EXPECTED_GND_ASSET_SHA256
            ):
                raise ValueError("selected adjacent geometry asset hash mismatch")

            kind2_offset = node_counts[0] + node_counts[1]
            candidate_islands = set(candidate["island_ids"])
            island_global = {
                kind2_offset + int(ordinal)
                for ordinal, node_id in compiled.execute(
                    "SELECT ordinal,node_id FROM nodes WHERE kind=2"
                )
                if node_id in candidate_islands
            }
            component_links = [
                row
                for row in topology_links
                if int(row[2]) in island_global or int(row[3]) in island_global
            ]
            component_neighbors = {
                int(second) if int(first) in island_global else int(first)
                for _, _, first, second in component_links
                if not (int(first) in island_global and int(second) in island_global)
            }
            if len(island_global) != 110 or len(component_links) != 110 or len(component_neighbors) != 1:
                raise ValueError("candidate island-to-quotient boundary is not the expected 110-to-1 map")
            plane_vertex_ordinal = next(iter(component_neighbors))
            plane_vertex_row = compiled.execute(
                "SELECT node_id FROM nodes WHERE kind=0 AND ordinal=?", (plane_vertex_ordinal,)
            ).fetchone()
            if plane_vertex_row is None:
                raise ValueError("candidate plane quotient vertex is absent")
            plane_vertex_id = plane_vertex_row[0]

            target_via_ids = {
                via_id
                for (via_id,) in raw.execute(
                    "SELECT via_id_fold FROM vias WHERE net_fold=?", (RAIL.casefold(),)
                )
            }
            finite_link_ordinals: set[int] = set()
            owners_by_link: dict[int, list[str]] = defaultdict(list)
            for link_ordinal, owner_id in _iter_rows(
                compiled,
                "SELECT link_ordinal,owner_id FROM link_owners WHERE kind=1",
                (),
                started=started,
                maximum_s=maximum_s,
            ):
                owner = str(owner_id)
                if owner.casefold().startswith("via:") and owner.split(":", 1)[1].casefold() in target_via_ids:
                    finite_link_ordinals.add(int(link_ordinal))
                    owners_by_link[int(link_ordinal)].append(owner)
            target_finite_links: list[tuple[Any, ...]] = []
            for row in _iter_rows(
                compiled,
                "SELECT ordinal,link_id,first_node,second_node,parallel_count,resistance_ohm,inductance_h FROM links WHERE kind=1",
                (),
                started=started,
                maximum_s=maximum_s,
            ):
                if int(row[0]) in finite_link_ordinals:
                    target_finite_links.append(row)
            if len(target_finite_links) != len(finite_link_ordinals):
                raise ValueError("target finite-link owner join is incomplete")

            wanted_vertex_ids = (
                {row["landing_vertex_id"] for row in power_contacts}
                | power_anchor_set
                | {plane_vertex_id}
            )
            vertex_ordinals = {
                node_id: int(ordinal)
                for ordinal, node_id in compiled.execute(
                    "SELECT ordinal,node_id FROM nodes WHERE kind=0"
                )
                if node_id in wanted_vertex_ids
            }
            if set(vertex_ordinals) != wanted_vertex_ids:
                raise ValueError("selected source contact vertex is absent from the compiled node table")

            graph: dict[int, set[int]] = defaultdict(set)
            for _, _, first, second, *_ in target_finite_links:
                graph[int(first)].add(int(second))
                graph[int(second)].add(int(first))
            target_graph_nodes = set(graph)
            target_graph_nodes.update(island_global)
            target_graph_nodes.add(positive_flat)
            for _, _, first, second in topology_links:
                first = int(first)
                second = int(second)
                if first in target_graph_nodes or second in target_graph_nodes:
                    graph[first].add(second)
                    graph[second].add(first)
            distance = {plane_vertex_ordinal: 0}
            queue = deque([plane_vertex_ordinal])
            while queue:
                current = queue.popleft()
                for neighbor in graph[current]:
                    if neighbor not in distance:
                        distance[neighbor] = distance[current] + 1
                        queue.append(neighbor)
            power_landing_ordinals = {
                vertex_ordinals[row["landing_vertex_id"]] for row in power_contacts
            }
            power_anchor_ordinals = {vertex_ordinals[value] for value in power_anchor_set}
            if not power_landing_ordinals <= set(distance) or not power_anchor_ordinals <= set(distance):
                raise ValueError("actual power contacts or Device anchors do not reach the L14 component")

            finite_boundary = [
                row
                for row in target_finite_links
                if plane_vertex_ordinal in (int(row[2]), int(row[3]))
            ]
            boundary_via_ids = {
                owner.split(":", 1)[1].casefold()
                for row in finite_boundary
                for owner in owners_by_link[int(row[0])]
            }
            placeholders = ",".join("?" for _ in boundary_via_ids)
            raw_boundary = list(
                raw.execute(
                    "SELECT via_id,net_name,start_layer_id,end_layer_id,start_node_id,end_node_id,"
                    "padstack_id,owner_id,start_x_pm,start_y_pm,end_x_pm,end_y_pm,source_record_sha256 "
                    f"FROM vias WHERE via_id_fold IN ({placeholders})",
                    tuple(sorted(boundary_via_ids)),
                )
            )
            if len(raw_boundary) != len(finite_boundary) or not raw_boundary:
                raise ValueError("candidate finite boundary does not match raw source via rows")

            stackup = list(
                raw.execute(
                    "SELECT layer_ordinal,layer_name,layer_kind,thickness_um,conductivity_s_per_m,material_name "
                    "FROM stackup_layers WHERE layer_name IN (?,?,?) ORDER BY layer_ordinal",
                    (PWR_LAYER, GND_LAYER, "Medium$DR1314"),
                )
            )
            dielectric_ordinal = next(int(row[0]) for row in stackup if row[1] == "Medium$DR1314")
            dielectric = list(
                raw.execute(
                    "SELECT frequency_hz,epsilon_r,loss_tangent FROM dielectric_points "
                    "WHERE layer_ordinal=? ORDER BY point_ordinal",
                    (dielectric_ordinal,),
                )
            )
            ownership_counts = {
                "rail_bindings": int(
                    ownership.execute("SELECT COUNT(*) FROM rail_bindings WHERE rail_id=?", (RAIL,)).fetchone()[0]
                ),
                "terminal_bindings": int(
                    ownership.execute("SELECT COUNT(*) FROM terminal_bindings WHERE rail_id=?", (RAIL,)).fetchone()[0]
                ),
                "contact_boundary": int(
                    ownership.execute("SELECT COUNT(*) FROM contact_boundary WHERE net=?", (RAIL,)).fetchone()[0]
                ),
                "retained_owner_refs": int(
                    ownership.execute("SELECT COUNT(*) FROM retained_owner_refs WHERE rail_id=?", (RAIL,)).fetchone()[0]
                ),
            }

            power_geometry = _geometry_stats(archive, power_geometry_record)
            ground_geometry = _geometry_stats(archive, ground_geometry_record)
        finally:
            ownership.close()
            raw.close()
            compiled.close()

    _check_time(started, maximum_s)
    raw_boundary_canonical = sorted([list(row) for row in raw_boundary])
    spans = Counter(f"{row[2]} -> {row[3]}" for row in raw_boundary)
    padstacks = Counter(row[6] for row in raw_boundary)
    source_contact_kind_counts = Counter(row["kind"] for row in selected_connections)
    power_distances = Counter(distance[index] for index in power_landing_ordinals)
    anchor_distances = Counter(distance[index] for index in power_anchor_ordinals)
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PARTIAL_SOURCE_COMPONENT_AND_CONTACT_GRAPH_PROVEN_REPLACEMENT_BOUNDARY_INCOMPLETE",
        "rail_id": RAIL,
        "inputs": {
            "bundle_path": str(bundle),
            "bundle_size_bytes": bundle.stat().st_size,
            "scenario_sha256": EXPECTED_SCENARIO_SHA256,
            "source_sha256": EXPECTED_SOURCE_SHA256,
            "raw_spatial_db": str(RAW_DB),
            "compiled_topology_db": str(COMPILED_DB),
            "ownership_db": str(OWNERSHIP_DB),
            "script_sha256": _sha256(Path(__file__).read_bytes()),
        },
        "actual_scenario_state": {
            "enabled_source_mounted_count": 421,
            "unique_refdes_count": 421,
            "all_source_and_current_rail_equal": True,
            "all_source_and_current_net_equal": True,
            "rerouted_count": 0,
            "connection_kind_counts": dict(sorted(source_contact_kind_counts.items())),
            "shared_cluster_count": len(
                {row["cluster_id"] for row in selected_connections if row.get("cluster_id")}
            ),
            "physical_anchor_or_direct_refdes_count": sum(
                bool(row.get("power_vias")) for row in selected_connections
            ),
            "shared_dummy_refdes_count": sum(
                not bool(row.get("power_vias")) for row in selected_connections
            ),
            "power_source_via_count": len(power_vias),
            "ground_source_via_count": len(ground_vias),
            "complete_conditional_power_contact_count": len(power_contacts),
            "complete_conditional_ground_contact_count": len(ground_contacts),
        },
        "device_port_reduction": {
            "port_ordinal": port_ordinal,
            "positive_node": positive_node,
            "negative_node": negative_node,
            "member_kind_semantics": {
                "0": "positive_pin_ids",
                "1": "negative_pin_ids",
                "2": "positive_anchor_node_ids",
                "3": "negative_anchor_node_ids",
            },
            "member_counts": {str(kind): len(values) for kind, values in members.items()},
            "pin_to_complete_contact_join": {"power": 978, "ground": 978},
            "deduplicated_quotient_vertices": {"power": 45, "ground": 1},
            "positive_supernode_star_link_count": len(star_links),
            "positive_supernode_neighbors_equal_positive_anchor_set": star_neighbors == anchor_ordinals,
            "interpretation": (
                "45/1 are certified finite-via quotient vertices after exact terminal selection; "
                "they are not counts of raw pins, artwork islands, or assumed physical current splits."
            ),
        },
        "candidate_component": {
            "component_id": candidate["component_id"],
            "component_evidence_sha256": candidate["component_evidence_sha256"],
            "representative_island_id": candidate["representative_island_id"],
            "layer": candidate["layer"],
            "net": candidate["net"],
            "artwork_island_count": len(candidate["island_ids"]),
            "contact_status": candidate["contact_status"],
            "surface_link_count": len(component_links),
            "common_plane_quotient_vertex_id": plane_vertex_id,
            "common_plane_quotient_vertex_ordinal": plane_vertex_ordinal,
            "all_actual_power_contact_vertices_reach_component": True,
            "actual_power_contact_vertex_count": len(power_landing_ordinals),
            "power_contact_graph_distance_histogram": {
                str(key): value for key, value in sorted(power_distances.items())
            },
            "all_device_power_anchors_reach_component": True,
            "device_power_anchor_count": len(power_anchor_ordinals),
            "device_anchor_graph_distance_histogram": {
                str(key): value for key, value in sorted(anchor_distances.items())
            },
            "reachable_target_graph_node_count": len(distance),
            "target_raw_via_count": len(target_via_ids),
            "target_finite_link_count": len(target_finite_links),
            "geometry": power_geometry,
            "stackup_rows": [list(row) for row in stackup],
            "adjacent_dielectric_points": [list(row) for row in dielectric],
        },
        "finite_rl_boundary": {
            "edge_count": len(finite_boundary),
            "owner_count": sum(len(owners_by_link[int(row[0])]) for row in finite_boundary),
            "raw_source_via_count": len(raw_boundary),
            "raw_source_rows_sha256": _canonical_sha256(raw_boundary_canonical),
            "span_counts": dict(sorted(spans.items())),
            "padstack_counts": dict(sorted(padstacks.items())),
            "resistance_ohm_range": [
                min(float(row[5]) for row in finite_boundary),
                max(float(row[5]) for row in finite_boundary),
            ],
            "inductance_h_range": [
                min(float(row[6]) for row in finite_boundary),
                max(float(row[6]) for row in finite_boundary),
            ],
            "records_sha256": _canonical_sha256(
                [
                    [*row, sorted(owners_by_link[int(row[0])])]
                    for row in sorted(finite_boundary)
                ]
            ),
        },
        "adjacent_reference_geometry": ground_geometry,
        "replacement_readiness": {
            "status": "STOP_REPLACEMENT_BOUNDARY_INCOMPLETE",
            "target_ownership_rows": ownership_counts,
            "candidate_is_not_one_connected_hole_free_gap_domain": (
                power_geometry["component_count"] != 1 or power_geometry["hole_count"] != 0
            ),
            "missing": [
                "VTRIP-specific geometry/contact ownership mapping from each of the 110 artwork islands to the retained or replaced boundary",
                "complete original dispersive G/C partial ordinal and fingerprint partition for every old-to-retained overlap",
                "replacement ledger proving that the 2,110 finite-R/L owners are rewired exactly once while all non-boundary owners remain",
                "eligible connected and hole-free local overlap partitions if compile_tri_fem_gap is used",
            ],
            "next_bounded_use": (
                "Use the observed 1 MHz native physical node/branch field to rank which of these "
                "source-proven contacts carries Device current before materializing a VTRIP ownership replacement."
            ),
        },
        "superseded_evidence": {
            "loaded-development-boundary.json": "rejected: flat positive node was decoded with a kind-local ordinal",
            "loaded-development-boundary-02.json": (
                "partial only: flat-node correction is valid, but its member-kind descriptions and "
                "cross-table member evidence are not used by this receipt"
            ),
        },
        "limitations": [
            "The target-net graph proves connectivity in the existing compiled topology; it does not assign current sharing or certify a sheet replacement.",
            "The component is a 110-island, 1,732-hole ordered source geometry, not one admissible connected hole-free tri_fem_gap input.",
            "No native solve, controller replay, raw SPD scan, PowerSI fit, or product/source edit was performed.",
        ],
        "resources": {
            "elapsed_s": time.monotonic() - started,
            "maximum_runtime_s": maximum_s,
            "bounded": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-runtime-s", type=float, default=55.0)
    args = parser.parse_args()
    if not 1.0 <= args.max_runtime_s <= 55.0:
        raise ValueError("--max-runtime-s must be within [1, 55]")
    if args.output.exists():
        raise FileExistsError(args.output)
    result = build_result(args.bundle, args.max_runtime_s)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(
        f"{PROGRAM} v{VERSION} {result['status']} "
        f"elapsed={result['resources']['elapsed_s']:.3f}s output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
