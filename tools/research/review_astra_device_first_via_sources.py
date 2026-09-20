"""Review the saved selected Device first-via ledger without reopening caches."""
from pathlib import Path
from hashlib import sha256
from collections import Counter
import argparse
import json


ROOT = Path(__file__).resolve().parents[2]
RAIL = "ADC_VDD_075_VTRIP_SRAM/0"
PRODUCER = "tools/research/inspect_astra_device_first_via_sources.py"
RESULT = "outputs/research/astra-device-first-via-sources-01/result.json"
LEDGER = "outputs/research/astra-device-first-via-sources-01/selected-device-first-via-sources.json"
BASIS = "outputs/research/astra-step4-basis-01/ownership-basis.json"
STATIC_COMPILER = "src/spd_decap_pi/spd_adapter.py"
PINS = {
    PRODUCER: "b42668dba221ed95d8522f3d6f680e1cafa5757a9de696b41e7b050e042f3f5c",
    RESULT: "a755ffc21cbb5470dcae7941c5c9301c62bfb2f41f009247d7c524cb2b66b5f8",
    LEDGER: "35df74b796157bef2fdc4b2894e16838f872e7a9006dc34e3a9b841eacdf6303",
    BASIS: "3dadf2d8028a68fd98ba05c2bc90fc4a0cbb85a5b9a605ce73e4bc925526ce5c",
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def review(output):
    assert not output.exists()
    for relative, expected in PINS.items():
        assert digest(ROOT / relative) == expected, relative
    producer = (ROOT / PRODUCER).read_text(encoding="utf-8")
    compiler = (ROOT / STATIC_COMPILER).read_text(encoding="utf-8")
    assert "contact_path_kind" in producer and "external_endpoint_node_id" in producer
    assert '"direct_via_landing"\n                    if incident_via_id' in compiler
    assert "endpoint_node_id=source_node_id" in compiler
    assert "(path_kind == \"trace_component\") != (not via_id)" in compiler
    saved = json.loads((ROOT / RESULT).read_bytes())
    assert saved["status"] == "VERIFIED_SELECTED_DEVICE_FIRST_VIA_SOURCES__PHYSICAL_PIN_LOCATION_NOT_PROVEN"
    assert saved["driver_sha256"] == PINS[PRODUCER] and saved["ledger_sha256"] == PINS[LEDGER]
    assert saved["port"]["rail_id"] == saved["port"]["selected_net"] == RAIL and saved["port"]["reference_net"] == "DGND"
    ledger = json.loads((ROOT / LEDGER).read_bytes())
    records, nodes, vias = ledger["records"], ledger["source_nodes"], ledger["source_vias"]
    assert len(records) == len(vias) == 1956 and len(nodes) == 3912
    nodes_by_id = {row["node_id_fold"]: row for row in nodes}
    vias_by_id = {row["via_id_fold"]: row for row in vias}
    assert len(nodes_by_id) == 3912 and len(vias_by_id) == 1956
    checks = {}
    for role, net in (("power", RAIL), ("ground", "DGND")):
        rows = [row for row in records if row["role"] == role]
        assert len(rows) == 978
        pins = {row["anchor"]["pin_id"] for row in rows}
        first_vias = {row["contact"]["incident_via_id"].casefold() for row in rows}
        first_nodes = {row["first_via_source_node_id"].casefold() for row in rows}
        edges = {row["contact"]["first_via_quotient_edge_id"] for row in rows}
        vertices = {row["contact"]["exposed_quotient_vertex_id"] for row in rows}
        assert len(pins) == len(first_vias) == len(first_nodes) == len(edges) == 978
        assert all(row["anchor"]["rail_id"] == RAIL and row["anchor"]["role"] == role for row in rows)
        assert all(row["contact"]["pin_id"] == row["anchor"]["pin_id"] and row["contact"]["net"] == net and row["contact"]["status"] == "complete" for row in rows)
        assert all(row["physical_pin_source_node_proven"] is False for row in rows)
        assert all("contact_path_kind" not in row["contact"] and "external_endpoint_node_id" not in row["contact"] for row in rows)
        for row in rows:
            node = nodes_by_id[row["first_via_source_node_id"].casefold()]
            via = vias_by_id[row["contact"]["incident_via_id"].casefold()]
            assert node["node_id_fold"] in (via["start_node_id_fold"], via["end_node_id_fold"])
            assert node["net_name"] == net and node["layer_id"] == "Signal$TOP" and node["padstack_id"] == "DUT"
        checks[role] = dict(pin_count=len(pins), unique_first_vias=len(first_vias), unique_first_via_source_nodes=len(first_nodes),
            unique_selected_quotient_edges=len(edges), unique_exposed_quotient_vertices=len(vertices),
            physical_pin_source_node_proven_count=sum(row["physical_pin_source_node_proven"] for row in rows))
    basis = json.loads((ROOT / BASIS).read_bytes())
    assert basis["meta"]["target_rail_id"] != RAIL
    output.mkdir(parents=True)
    receipt = dict(program="SPD Decap PI Evaluator", version="0.23.1", status="ACCEPT_WITH_SCOPE", pins=PINS,
        reviewer_sha256=digest(Path(__file__)), selected_ledger=checks,
        source_counts=dict(records=len(records), raw_source_nodes=len(nodes), raw_source_vias=len(vias)),
        evidence_limits=dict(
            selected_edge_uniqueness_proven=True,
            raw_edge_candidate_count_one_proven_from_saved_ledger=False,
            source_node_quotient_membership_proven_from_saved_ledger=False,
            physical_pin_source_node_proven=False,
            trace_first_or_contact_path_kind_ruled_out=False,
            external_endpoint_node_id_retained=False,
            current_compiler_invariant_direct_when_incident_via_present=True,
            current_compiler_sha256=digest(ROOT / STATIC_COMPILER),
            current_compiler_provenance_for_saved_external_payload=False,
            ownership_basis_target_rail=basis["meta"]["target_rail_id"]),
        scope="Saved ledger only. Current compiler code maps a nonempty incident_via_id to direct_via_landing and copies source_node_id as the landing endpoint, but the saved external payload is pinned only by payload hash/compiler ID, not by that compiler source hash. The ledger omits candidate lists, landing-bindings membership, contact_path_kind, and external_endpoint_node_id. It therefore cannot establish candidate-count-one, physical pin location, direct-via contact, or exclude trace-first alternatives. The saved ownership basis targets another rail and supplies no target-specific proof.")
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
    print(json.dumps(dict(status=receipt["status"], receipt_sha256=digest(output / "independent-review.json"))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    review(parser.parse_args().output.resolve())
