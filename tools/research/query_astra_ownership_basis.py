"""Restore the saved D115b ownership witness; never import or compile the SPD."""

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from time import monotonic
from zipfile import ZipFile
import zlib


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-step4-basis-01"
SOURCE = Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04")
D101 = Path(r"D:\SPD-Decap-PI-Evaluator-W7\a5dea56b6fd5fe8eb345420dddb16a88c10bc224\260729-d101-source-plane-ownership-reproduction-fringe-01\source_plane_ownership_reproduction_observation.json")
D096 = Path(r"D:\SPD-Decap-PI-Evaluator-W7\23e5d3c6b43064b8fd805c234da5f0ccc86b6d4f\260729-a2-d096-source-block-census-01\source_block_census_report.json")


def checked_json(path, expected):
    data = path.read_bytes()
    if sha256(data).hexdigest() != expected:
        raise ValueError(f"receipt identity differs: {path.name}")
    return json.loads(data)


def main():
    started = monotonic()
    receipt = checked_json(SOURCE / "d115b_source_plane_ownership_materialization_receipt.json", "c69dce134ca02ff263b75f5df930106a7d8d75d05cccf2acd13b7d9b1919aa49")
    observation = checked_json(D101, "1b4699e6f067329edcbf397f776afb618dc91d9c3a66b22884de21e5721542f3")
    census = checked_json(D096, "bb2ad70bbbb5e39af6a673d29adb2e5543675e680d68491cf906aabc2c16f473")
    bundle = SOURCE / "source_plane_ownership_candidate.spdpi"
    assert receipt["status"] == "PASS" and bundle.stat().st_size == receipt["candidate"]["size_bytes"]
    with bundle.open("rb") as stream:
        digest = sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            if monotonic() - started > 60:
                raise TimeoutError("saved-bundle identity budget exceeded")
    assert digest.hexdigest() == receipt["candidate"]["sha256"]
    expected = observation["observed_manifest"]
    with ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        attachment = next(a for a in manifest["attachments"] if a["name"] == expected["asset_name"])
        assert attachment["size"] == expected["compressed_size_bytes"]
        assert attachment["sha256"] == expected["compressed_sha256"]
        compressed = archive.read(attachment["path"])
        assert len(compressed) == attachment["size"] and sha256(compressed).hexdigest() == attachment["sha256"]
    inflater = zlib.decompressobj()
    decoded = inflater.decompress(compressed, expected["uncompressed_size_bytes"] + 1)
    assert inflater.eof and not inflater.unconsumed_tail and not inflater.unused_data
    assert len(decoded) == expected["uncompressed_size_bytes"]
    assert sha256(decoded).hexdigest() == expected["uncompressed_sha256"]
    OUTPUT.mkdir(parents=True, exist_ok=False)
    database = OUTPUT / "ownership.sqlite"
    database.write_bytes(decoded)
    with sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        deadline = monotonic() + 10
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        meta = dict(db.execute("SELECT key,value FROM meta"))
        comparisons = {}
        for key, value in meta.items():
            if key in expected:
                comparisons[key] = value == str(expected[key])
        assert all(comparisons.values())
        sections = [dict(r) for r in db.execute("SELECT * FROM section_ledger ORDER BY section_name")]
        assert sections == observation["section_ledger"]
        counts = [dict(r) for r in db.execute("SELECT island_id, endpoint_layer, status, COUNT(*) AS count FROM contact_boundary GROUP BY island_id,endpoint_layer,status")]
        ledger = [dict(r) for r in db.execute("SELECT * FROM replacement_ledger")]
        terminals = [dict(r) for r in db.execute("SELECT ordinal,pin_id,role,status,endpoint_node_id,layer,island_id,via_record_id FROM terminal_bindings ORDER BY ordinal")]
        surfaces = [dict(r) for r in db.execute("SELECT * FROM surfaces ORDER BY ordinal")]
    mappings = {"certificate_evidence_sha256": "ownership_certificate_evidence_sha256", "logical_rows_sha256": "ownership_logical_rows_sha256"}
    census_matches = {key: meta[key] == value for key, value in census["query_key"].items() if key in meta}
    for meta_key, query_key in mappings.items():
        census_matches[query_key] = meta[meta_key] == census["query_key"][query_key]
    assert all(census_matches.values())
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "ACCEPT_SAVED_OWNERSHIP_BASIS_ONLY", "elapsed_s": monotonic() - started,
        "bundle": {"path": str(bundle), "size_bytes": bundle.stat().st_size, "sha256": digest.hexdigest()},
        "member": attachment, "decoded_bytes": len(decoded), "decoded_sha256": sha256(decoded).hexdigest(),
        "database": str(database), "meta": meta, "d101_meta_matches": comparisons,
        "d101_section_ledger_exact_match": True, "d096_present_identity_field_matches": census_matches,
        "d096_project_binding_present": "project_binding_sha256" in census["query_key"],
        "contact_boundary_counts": counts, "replacement_ledger": ledger, "terminal_bindings": terminals,
        "surfaces": surfaces, "attachment_manifest": manifest["attachments"],
        "scope": "Saved ownership bytes match D101 and D096 present fields. No numeric substrate matrix, complete return/current model, exact replacement or PowerSI improvement is certified. D096 missing binding is not retroactively repaired."
    }
    (OUTPUT / "ownership-basis.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in ("attachment_manifest", "meta", "surfaces")}))


def query_replacement_boundary():
    """Query the saved boundary; a census identity never stands in for live G/C."""
    target = OUTPUT / "replacement-boundary.json"
    if target.exists():
        raise FileExistsError(target)
    started = monotonic()
    basis = json.loads((OUTPUT / "ownership-basis.json").read_text(encoding="utf-8"))
    restored = json.loads((OUTPUT / "indexes/restoration.json").read_text(encoding="utf-8"))
    raw, topology = (r["meta"] for r in restored["members"])
    own = basis["meta"]
    identities = {key: own[key] == raw[key] == topology[key] for key in (
        "source_sha256", "project_binding_sha256", "certificate_evidence_sha256")}
    identities.update(
        topology=own["compiled_topology_identity_sha256"] == raw["compiled_topology_identity_sha256"] == topology["topology_identity_sha256"],
        raw_geometry=own["raw_geometry_identity_sha256"] == raw["geometry_identity_sha256"],
        raw_rows=own["raw_logical_rows_sha256"] == raw["logical_rows_sha256"],
        plane_sheet=own["raw_plane_sheet_sha256"] == raw["plane_sheet_payload_sha256"],
    )
    assert all(identities.values())
    census = checked_json(D096, "bb2ad70bbbb5e39af6a673d29adb2e5543675e680d68491cf906aabc2c16f473")
    old = {i for c in census["closures"].values() for i in c["islands"]}
    escaping = [r for r in census["rows"] if (r["upper_island_id"] in old) != (r["lower_island_id"] in old)]
    candidate = [r for r in census["rows"] if r["action"] == "candidate"]
    for row in census["rows"]:
        payload = {k: row[k] for k in ("substrate_identity_sha256", "upper_layer", "lower_layer", "upper_island_id", "lower_island_id", "capacitance_f_hex")}
        assert sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest() == row["fingerprint"]
    assert len(escaping) == 9 and len(candidate) == 1
    with sqlite3.connect((OUTPUT / "ownership.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        deadline = monotonic() + 10
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        contacts = [dict(r) for r in db.execute("SELECT * FROM contact_boundary WHERE via_id IN ('via1306795','via1306796','via336236') ORDER BY ordinal")]
        terminal_edges = [r[0] for r in db.execute("SELECT finite_edge_id FROM terminal_bindings ORDER BY ordinal")]
    edge_ids = terminal_edges + [r["finite_edge_id"] for r in contacts]
    with sqlite3.connect((OUTPUT / "indexes/compiled-topology.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        deadline = monotonic() + 10
        db.set_progress_handler(lambda: int(monotonic() > deadline), 10000)
        links = [dict(r) for r in db.execute("SELECT * FROM links WHERE link_id IN (" + ",".join("?" * len(edge_ids)) + ")", edge_ids)]
        assert len(links) == len(edge_ids)
        counts = list(db.execute("SELECT kind,COUNT(*) FROM nodes GROUP BY kind ORDER BY kind"))
        for link in links:
            link["owners"] = [r[0] for r in db.execute("SELECT owner_id FROM link_owners WHERE kind=? AND link_ordinal=? ORDER BY owner_ordinal", (link["kind"], link["ordinal"]))]
            for endpoint in ("first_node", "second_node"):
                index = link[endpoint]
                for kind, count in counts:
                    if index < count:
                        link[endpoint + "_id"] = db.execute("SELECT node_id FROM nodes WHERE kind=? AND ordinal=?", (kind, index)).fetchone()[0]
                        break
                    index -= count
                else:
                    raise ValueError("compiled endpoint escapes node inventory")
        port = dict(db.execute("SELECT * FROM rail_ports WHERE rail_id=?", (census["rail_id"],)).fetchone())
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "PARTIAL_SAME_BASIS_REPLACEMENT_NOT_READY",
        "elapsed_s": monotonic() - started, "basis_matches": identities,
        "candidate_c_f": float.fromhex(candidate[0]["capacitance_f_hex"]), "candidate_row": candidate[0],
        "historical_retained_cross_boundary_rows": escaping,
        "historical_retained_cross_boundary_sum_f": sum(float.fromhex(r["capacitance_f_hex"]) for r in escaping),
        "selected_source_contact_rows": contacts, "selected_compiled_links": links, "compiled_rail_port": port,
        "native_numeric_substrate_loaded": False, "replacement_executed": False, "powersi_comparison_executed": False,
        "limitations": [
            "The D096 numeric rows retain their original identity and missing project-binding field.",
            "The restored topology stores R/L links but no Maxwell G/C partial matrices or frequency factorization.",
            "Existing full-old-class shadow embedding rejects old-to-retained couplings; the nine historical rows are a preflight obstruction, not a newly executed production STOP.",
            "The existing P7 local-replacement recipe additionally fixes frequency to 1 GHz; a 1 MHz run is not directly supported by that recipe.",
            "A small rank-one G/C correction could retain other rows only with an independently certified replacement capacitance and a bound numeric base; it cannot represent a spatially distributed RL plane replacement.",
            "Source/owner/contact association does not establish complete physical return current, plating/fill or accuracy."
        ],
    }
    target.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("historical_retained_cross_boundary_rows", "selected_source_contact_rows", "selected_compiled_links")}))


def restore_basis_indexes():
    """Materialize exactly the two saved indexes, without loading scenario.json."""
    basis = json.loads((OUTPUT / "ownership-basis.json").read_text(encoding="utf-8"))
    target = OUTPUT / "indexes"
    target.mkdir(exist_ok=False)
    started = monotonic()
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "members": []}
    # ponytail: two fixed artifacts only; a reusable archive service is unnecessary.
    requested = {
        "spatial/raw-spatial-contact-v3-40cb44b2376f59d6.sqlite.zlib": ("raw-spatial.sqlite", 2752458752),
        "topology/layerwise-compiled-topology-v1-a149542975eb2374.sqlite.zlib": ("compiled-topology.sqlite", 461893632),
    }
    with ZipFile(basis["bundle"]["path"]) as archive:
        for name, (filename, limit) in requested.items():
            row = next(a for a in basis["attachment_manifest"] if a["name"] == name)
            compressed_hash, decoded_hash = sha256(), sha256()
            read = written = 0
            inflater = zlib.decompressobj()
            partial = target / (filename + ".partial")
            with archive.open(row["path"]) as source, partial.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    compressed_hash.update(chunk)
                    read += len(chunk)
                    pending = chunk
                    while pending:
                        data = inflater.decompress(pending, 1024 * 1024)
                        pending = inflater.unconsumed_tail
                        written += len(data)
                        if written > limit or inflater.unused_data:
                            raise ValueError("saved index expansion or trailing-data mismatch")
                        if monotonic() - started > 60:
                            raise TimeoutError("saved-index restoration budget exceeded")
                        decoded_hash.update(data)
                        output.write(data)
            assert inflater.eof and not inflater.unused_data
            assert written == limit and read == row["size"] and compressed_hash.hexdigest() == row["sha256"]
            path = target / filename
            partial.rename(path)
            with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA trusted_schema=OFF")
                meta = dict(db.execute("SELECT key,value FROM meta"))
            result["members"].append({"attachment": row, "path": str(path), "decoded_bytes": written,
                                      "decoded_sha256": decoded_hash.hexdigest(), "meta": meta})
    result.update(status="ACCEPT_BYTE_IDENTICAL_SAVED_INDEXES_ONLY", elapsed_s=monotonic() - started)
    (target / "restoration.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    if sys.argv[1:] == ["--indexes"]:
        restore_basis_indexes()
    elif sys.argv[1:] == ["--boundary"]:
        query_replacement_boundary()
    elif sys.argv[1:]:
        raise SystemExit("usage: query_astra_ownership_basis.py [--indexes|--boundary]")
    else:
        main()
