from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RAIL_ID = "ADC_VDD_075_VTRIP_SRAM/0"
RAIL_RECEIPT = ROOT / "docs/evaluation-research/astra_native_loaded_development_rail_2026-09-07.json"
MODEL_RECEIPT = ROOT / "docs/evaluation-research/astra_loaded_component_models_2026-09-07-02.json"
RAW_DB = ROOT / "outputs/research/astra-step4-basis-01/indexes/raw-spatial.sqlite"
COMPILED_DB = ROOT / "outputs/research/astra-step4-basis-01/indexes/compiled-topology.sqlite"
OWNERSHIP_DB = ROOT / "outputs/research/astra-step4-basis-01/ownership.sqlite"
DEFAULT_OUTPUT = ROOT / "outputs/research/astra-step6e-loaded-boundary-01/loaded-development-boundary-02.json"


def _hash(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {"path": str(path), "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _open_db(path: Path) -> sqlite3.Connection:
    uri = "file:" + str(path).replace("\\", "/") + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _query(connection: sqlite3.Connection, sql: str, args: tuple[object, ...] = ()) -> tuple[list[tuple[object, ...]], float]:
    started = time.monotonic()
    deadline = started + 10.0
    connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        rows = connection.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(f"bounded SQL failed or exceeded 10 seconds: {exc}") from exc
    finally:
        connection.set_progress_handler(None, 0)
    elapsed = time.monotonic() - started
    if elapsed > 10.0:
        raise TimeoutError(f"bounded SQL exceeded 10 seconds: {elapsed:.6f}s")
    return rows, elapsed


def _db_identity(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {"path": str(path), "size_bytes": int(stat.st_size)}


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def build_result() -> dict[str, object]:
    started = time.monotonic()
    rail_receipt = _load(RAIL_RECEIPT)
    model_receipt = _load(MODEL_RECEIPT)
    if str(rail_receipt.get("program", "")) not in (PROGRAM, f"{PROGRAM} v{VERSION}"):
        raise ValueError("native rail receipt program/version mismatch")
    if model_receipt.get("program") != PROGRAM or model_receipt.get("version") != VERSION:
        raise ValueError("component model receipt program/version mismatch")
    if rail_receipt.get("rail_id") != RAIL_ID:
        raise ValueError("native rail receipt rail mismatch")
    declared_population = int(
        rail_receipt["scenario_population"]["enabled_source_mounted_by_rail"][RAIL_ID]
    )
    if declared_population != 421:
        raise ValueError(f"source-mounted cluster count mismatch: {declared_population}")

    compiled = _open_db(COMPILED_DB)
    ownership = _open_db(OWNERSHIP_DB)
    timings: dict[str, float] = {}
    try:
        def q(label: str, connection: sqlite3.Connection, sql: str, args: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
            rows, elapsed = _query(connection, sql, args)
            timings[label] = elapsed
            return rows

        port_rows = q(
            "compiled.rail_ports",
            compiled,
            "SELECT ordinal,rail_id,selected_net,reference_net,positive_node,negative_node "
            "FROM rail_ports WHERE rail_id=? LIMIT 2",
            (RAIL_ID,),
        )
        if len(port_rows) != 1:
            raise ValueError(f"compiled rail port row count is {len(port_rows)}")
        port_ordinal, rail_id, selected_net, reference_net, positive_node, negative_node = port_rows[0]
        node_counts = q(
            "compiled.node_kind_counts",
            compiled,
            "SELECT kind,COUNT(*) FROM nodes GROUP BY kind ORDER BY kind",
        )

        def flat_pair(flat_index: int) -> tuple[int, int]:
            remaining = int(flat_index)
            for kind, count in node_counts:
                if remaining < int(count):
                    return int(kind), remaining
                remaining -= int(count)
            raise ValueError(f"flat compiled node index escapes inventory: {flat_index}")

        positive_pair = flat_pair(int(positive_node))
        negative_pair = flat_pair(int(negative_node))
        boundary_rows = q(
            "compiled.boundary_nodes",
            compiled,
            "SELECT kind,ordinal,node_id FROM nodes WHERE "
            "(kind=? AND ordinal=?) OR (kind=? AND ordinal=?) ORDER BY kind,ordinal LIMIT 4",
            positive_pair + negative_pair,
        )
        boundary_map = {(int(row[0]), int(row[1])): str(row[2]) for row in boundary_rows}
        member_counts = q(
            "compiled.rail_port_members",
            compiled,
            "SELECT kind,COUNT(*) FROM rail_port_members WHERE port_ordinal=? GROUP BY kind ORDER BY kind",
            (port_ordinal,),
        )
        member_kind_evidence = q(
            "compiled.rail_port_member_kind_evidence",
            compiled,
            "SELECT rpm.kind,rpm.ordinal,rpm.value,l.link_id "
            "FROM rail_port_members rpm LEFT JOIN links l ON l.kind=rpm.kind AND l.ordinal=rpm.ordinal "
            "WHERE rpm.port_ordinal=? AND rpm.kind IN (0,1) ORDER BY rpm.kind,rpm.ordinal LIMIT 8",
            (port_ordinal,),
        )
        member_anchor_rows = q(
            "compiled.rail_port_member_anchor_ids",
            compiled,
            "SELECT kind,ordinal,value FROM rail_port_members WHERE port_ordinal=? AND kind IN (2,3) "
            "ORDER BY kind,ordinal LIMIT 1000",
            (port_ordinal,),
        )
        owner_rows = q(
            "compiled.rail_owner_links",
            compiled,
            "SELECT l.kind,l.ordinal,l.link_id,l.first_node,l.second_node,lo.owner_id "
            "FROM links l JOIN link_owners lo ON lo.kind=l.kind AND lo.link_ordinal=l.ordinal "
            "WHERE lo.owner_id LIKE ? ORDER BY l.kind,l.ordinal LIMIT 1000",
            (f"device-port-supernode:{RAIL_ID}:%",),
        )
        if len(owner_rows) > 1000:
            raise ValueError("selected owner links exceed bounded limit")
        anchor_pairs = {flat_pair(int(row[4])) for row in owner_rows}
        anchor_clauses = " OR ".join("(kind=? AND ordinal=?)" for _ in anchor_pairs) or "0"
        anchor_nodes = q(
            "compiled.rail_owner_anchor_nodes",
            compiled,
            f"SELECT kind,ordinal,node_id FROM nodes WHERE {anchor_clauses} ORDER BY kind,ordinal LIMIT 1000",
            tuple(value for pair in sorted(anchor_pairs) for value in pair),
        )
        anchor_map = {(int(row[0]), int(row[1])): str(row[2]) for row in anchor_nodes}

        ownership_rail = q(
            "ownership.rail_bindings",
            ownership,
            "SELECT COUNT(*) FROM rail_bindings WHERE rail_id=?",
            (RAIL_ID,),
        )
        ownership_terminals = q(
            "ownership.terminal_bindings",
            ownership,
            "SELECT COUNT(*),COUNT(DISTINCT island_id),COUNT(DISTINCT component_id),"
            "COUNT(DISTINCT branch_id),COUNT(DISTINCT source_node_record_id) "
            "FROM terminal_bindings WHERE rail_id=?",
            (RAIL_ID,),
        )
        ownership_contacts = q(
            "ownership.contact_boundary",
            ownership,
            "SELECT COUNT(*),COUNT(DISTINCT island_id),COUNT(DISTINCT component_id),COUNT(DISTINCT via_id) "
            "FROM contact_boundary WHERE net=?",
            (RAIL_ID,),
        )
        ownership_refs = q(
            "ownership.retained_owner_refs",
            ownership,
            "SELECT COUNT(*) FROM retained_owner_refs WHERE rail_id=?",
            (RAIL_ID,),
        )
    finally:
        compiled.close()
        ownership.close()

    positive_joined = positive_pair in boundary_map
    negative_joined = negative_pair in boundary_map
    owner_anchor_ids = {
        anchor_map.get(flat_pair(int(row[4])))
        for row in owner_rows
    }
    member_kind2_ids = {str(row[2]) for row in member_anchor_rows if int(row[0]) == 2}
    owner_anchor_ids.discard(None)
    owner_anchor_matches_kind2 = len(owner_rows) == 45 and owner_anchor_ids == member_kind2_ids
    missing_join = {
        "compiled_positive_node": {
            "flat_index": int(positive_node),
            "kind": positive_pair[0],
            "ordinal": positive_pair[1],
            "source_node_id_from_receipt": rail_receipt["device_port"]["positive_node_id"],
            "resolved_node_id": boundary_map.get(positive_pair),
            "resolved_in_compiled_nodes": positive_joined,
        },
        "compiled_negative_node": {
            "flat_index": int(negative_node),
            "kind": negative_pair[0],
            "ordinal": negative_pair[1],
            "source_node_id_from_receipt": rail_receipt["device_port"]["negative_node_id"],
            "resolved_node_id": boundary_map.get(negative_pair),
            "resolved_in_compiled_nodes": negative_joined,
        },
        "source_terminal_and_contact_ownership": {
            "rail_bindings_rows": int(ownership_rail[0][0]),
            "terminal_bindings_rows": int(ownership_terminals[0][0]),
            "contact_boundary_rows": int(ownership_contacts[0][0]),
            "retained_owner_refs_rows": int(ownership_refs[0][0]),
        },
        "external_via_rl_and_gc_boundary": "not enumerable for this rail from the selected same-basis ownership DB",
    }
    checks = {
        "receipt_population_is_421": declared_population == 421,
        "compiled_port_identified": str(rail_id) == RAIL_ID and str(selected_net) == RAIL_ID and str(reference_net) == "DGND",
        "negative_compiled_boundary_resolved": negative_joined,
        "positive_compiled_boundary_resolved": positive_joined,
        "45_device_links_match_kind2_anchor_members": owner_anchor_matches_kind2,
        "source_terminal_contact_ownership_complete": all(value > 0 for value in missing_join["source_terminal_and_contact_ownership"].values()),
        "candidate_island_admissible": False,
    }
    elapsed = time.monotonic() - started
    if elapsed >= 55.0:
        raise TimeoutError(f"Step6E boundary query exceeded 55 seconds: {elapsed:.6f}s")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "STOP_MISSING_SOURCE_TERMINAL_GEOMETRY_JOIN",
        "rail_id": RAIL_ID,
        "scope": "SAME_BASIS_INDEXED_METADATA_AND_SELECTED_SOURCE_NET_COUNTS_ONLY",
        "superseded_prior_diagnostic": {
            "path": str(ROOT / "outputs/research/astra-step6e-loaded-boundary-01/loaded-development-boundary.json"),
            "status": "STOP_MISSING_SOURCE_COMPILED_BOUNDARY_JOIN",
            "reason": "rejected diagnostic used the flat positive node index as a per-kind ordinal; corrected mapping is kind1 ordinal77",
        },
        "inputs": {
            "native_rail_receipt": _hash(RAIL_RECEIPT),
            "loaded_component_model_receipt": _hash(MODEL_RECEIPT),
            "raw_spatial_db": _db_identity(RAW_DB),
            "compiled_topology_db": _db_identity(COMPILED_DB),
            "ownership_db": _db_identity(OWNERSHIP_DB),
            "rail_receipt_bundle_source_sha256": rail_receipt["bundle"]["source_sha256"],
            "rail_receipt_scenario_sha256": rail_receipt["bundle"]["scenario_sha256"],
        },
        "device_port_receipt": rail_receipt["device_port"],
        "source_mounted_cluster_count": declared_population,
        "compiled_boundary": {
            "rail_port_row": {
                "ordinal": int(port_ordinal),
                "rail_id": rail_id,
                "selected_net": selected_net,
                "reference_net": reference_net,
                "positive_node_flat_index": int(positive_node),
                "negative_node_flat_index": int(negative_node),
            },
            "node_kind_counts": [[int(row[0]), int(row[1])] for row in node_counts],
            "resolved_node_rows": [list(row) for row in boundary_rows],
            "member_kind_semantics": {
                "0": "persisted device-port-supernode links (link_id prefix device-port-supernode-link)",
                "1": "persisted finite-via edges (link_id prefix spd-finite-via-edge)",
                "2": "persisted finite-via-vertex power-anchor members",
                "3": "persisted finite-via-vertex negative representative member",
            },
            "port_member_counts_by_kind": [[int(row[0]), int(row[1])] for row in member_counts],
            "member_kind_evidence": [list(row) for row in member_kind_evidence],
            "anchor_member_ids_kind2_and_kind3": [list(row) for row in member_anchor_rows],
            "device_supernode_owner_link_count": len(owner_rows),
            "device_supernode_owner_links": [
                {"kind": int(row[0]), "ordinal": int(row[1]), "link_id": row[2], "first_flat_index": int(row[3]),
                 "second_flat_index": int(row[4]), "second_node_id": anchor_map.get(flat_pair(int(row[4]))), "owner_id": row[5]}
                for row in owner_rows
            ],
            "negative_g1_quotient": {
                "representative_kind": negative_pair[0],
                "representative_ordinal": negative_pair[1],
                "representative_node_id": boundary_map.get(negative_pair),
                "kind3_member_count": sum(int(row[0] == 3) for row in member_anchor_rows),
                "kind1_member_count": next((int(row[1]) for row in member_counts if int(row[0]) == 1), 0),
                "kind2_anchor_member_count": len(member_kind2_ids),
                "physical_merge_claim": False,
            },
        },
        "missing_join": missing_join,
        "checks": checks,
        "candidate": None,
        "nonclaims": [
            "No physical multi-terminal artwork island candidate is accepted.",
            "Source-mounted count is receipt aggregate only; no per-branch scenario row was available in the selected inputs for the 421-to-terminal join.",
            "Compiled owner links and receipt Device IDs do not substitute for the missing source terminal/contact and artwork-island geometry join.",
            "No native solve, import, full graph traversal, fit, or PowerSI comparison was performed.",
        ],
        "query_timings_s": timings,
        "resources": {"elapsed_s": elapsed, "bounded_under_55s": elapsed < 55.0, "max_rows_per_selected_query": 1000},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} Step6E loaded development boundary query")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(f"{PROGRAM} v{VERSION} - Step6E loaded development boundary query")
    output = args.output.resolve()
    if output.exists():
        print(f"{PROGRAM} v{VERSION}: ERROR: refusing to overwrite existing output: {output}")
        return 2
    try:
        result = build_result()
        serialized = json.dumps(_jsonable(result), indent=2, sort_keys=True, allow_nan=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f"{PROGRAM} v{VERSION}: ERROR: {exc}")
        return 2
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": result["status"], "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
