"""Normalize saved first-post source Via identities without computing currents."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import traceback

import numpy as np

import reconstruct_astra_native_loaded_field as recon


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
TOTAL = 26_790
PINS = {
    "inventory_result": (R / "astra-full-return-source-owners-04" / "result.json", "1f423c564ae922bcf9ec066cfba7881b915723cf33af2ef7cf17865c20931e01"),
    "inventory_ledger": (R / "astra-full-return-source-owners-04" / "source-owner-inventory.json", "4cdca3e08018ed552d987a0d23489499d03fc4365d6d5d37cfb498c9478caa82"),
    "direct_result": (R / "astra-full-return-source-contacts-01" / "result.json", "4b34dcac22060bb5f8804d4efa1ec0488173d293ca345bee45e16ef55964b72d"),
    "direct_delta": (R / "astra-full-return-source-contacts-01" / "bound-source-contact-delta.json", "9140efecb0455766f39c960855bf3beecd57162774afdd403897a758d85937d3"),
    "composite_result": (R / "astra-full-return-composite-sources-03" / "result.json", "01cfbb2cbb324a2c7421d46e112a17e8ece2d3a6137aecbad12cc74dd07ff0c3"),
    "composite_delta": (R / "astra-full-return-composite-sources-03" / "composite-source-segment-delta.npz", "b47b6031c901e50e41c7dc12519a5fcadb7699e236d461df4d159189cd625faa"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def pin_receipts() -> dict[str, dict[str, object]]:
    values = {}
    for name, (path, digest) in PINS.items():
        require(path.is_file() and sha256(path) == digest, f"{name} pin")
        values[name] = receipt(path)
    return values


def source_record(via_id: str, final_row: int, native_row: int, role: str,
                  port_sign: int, outward_to_raw_sign: int, source_class: str,
                  vias: dict[str, dict], nodes: dict[str, dict], reference: dict | None = None) -> dict:
    via = vias[via_id]
    top_fold, lower_fold = via["start_node_id_fold"], via["end_node_id_fold"]
    require(via["status"] == "EXACT" and via["padstack_id_fold"] == "dr-0102_60", "Via source contract")
    require(via["start_layer_id_fold"] == "signal$top" and via["end_layer_id_fold"] == "signal$l02(dgnd)", "Via layer contract")
    require(via["start_x_pm"] == via["end_x_pm"] and via["start_y_pm"] == via["end_y_pm"], "Via XY coincidence")
    require(nodes[top_fold]["node_id_fold"] == top_fold and nodes[lower_fold]["node_id_fold"] == lower_fold, "source nodes")
    require((nodes[top_fold]["x_pm"], nodes[top_fold]["y_pm"], nodes[top_fold]["layer_id_fold"]) == (via["start_x_pm"], via["start_y_pm"], "signal$top"), "top node geometry")
    require((nodes[lower_fold]["x_pm"], nodes[lower_fold]["y_pm"], nodes[lower_fold]["layer_id_fold"]) == (via["end_x_pm"], via["end_y_pm"], "signal$l02(dgnd)"), "lower node geometry")
    require(port_sign in (-1, 1) and outward_to_raw_sign in (-1, 1), "orientation signs")
    row = {
        "final_active_current_row": final_row,
        "native_active_current_row": native_row,
        "role": role,
        "source_class": source_class,
        "via_id": via_id,
        "top_raw_node_id": via["start_node_id"],
        "lower_raw_node_id": via["end_node_id"],
        "top_node_id_fold": top_fold,
        "lower_node_id_fold": lower_fold,
        "x_pm": int(via["start_x_pm"]),
        "y_pm": int(via["start_y_pm"]),
        "source_record_sha256": via["source_record_sha256"],
        "padstack_id": via["padstack_id"],
        "port_outward_sign": port_sign,
        "port_outward_to_raw_top_to_l02_sign": outward_to_raw_sign,
        "final_first_to_second_raw_top_to_l02_sign": port_sign * outward_to_raw_sign,
    }
    if reference is not None:
        row["composite_leg0_reference"] = reference
    return row


def run(output: Path) -> None:
    budget = recon._Budget.create(45, 2)
    inputs = pin_receipts()
    output.mkdir(parents=True, exist_ok=False)
    frozen_driver = output / "driver-at-run.py"
    frozen_driver.write_bytes(Path(__file__).read_bytes())
    try:
        inventory_result = json.loads(PINS["inventory_result"][0].read_bytes())
        direct_result = json.loads(PINS["direct_result"][0].read_bytes())
        composite_result = json.loads(PINS["composite_result"][0].read_bytes())
        require(inventory_result["status"] == "COMPLETED_ALL_PORT_SOURCE_OWNER_INVENTORY_NO_FIELD", "inventory status")
        require(direct_result["status"] == "COMPLETED_SAVED_FULL_RETURN_SOURCE_CONTACT_BINDING", "direct status")
        require(composite_result["status"] == "COMPLETED_QUALIFIED_FULL_RETURN_COMPOSITE_SOURCE_DELTA", "composite status")
        budget.check("pinned receipts")

        inventory = json.loads(PINS["inventory_ledger"][0].read_bytes())
        edges = inventory["edges"]
        vias, nodes = inventory["source_vias"], inventory["source_nodes"]
        statuses = Counter(edge["binding_status"] for edge in edges)
        require(statuses == Counter({"DERIVED_SINGLE_VIA_ENDPOINT_COMPLEMENT": 22765, "UNRESOLVED_SOURCE_ENDPOINT_JOIN": 4005, "EXISTING_SPLIT_COMPOSITE_LEG_SOURCE_PENDING": 20}), "inventory partition")
        by_native = {int(edge["native_active_edge_index"]): edge for edge in edges}
        require(len(by_native) == TOTAL, "native rows duplicate")

        direct = json.loads(PINS["direct_delta"][0].read_bytes())["records"]
        require(len(direct) == 4005 and len({row["native_active_edge_index"] for row in direct}) == 4005, "direct delta rows")
        with np.load(PINS["composite_delta"][0], allow_pickle=False) as archive:
            composites = json.loads(archive["records_json_utf8"].tobytes())
        require(len(composites) == 20, "composite delta rows")
        budget.check("saved delta parse")

        records = []
        derived = [edge for edge in edges if edge["binding_status"] == "DERIVED_SINGLE_VIA_ENDPOINT_COMPLEMENT"]
        for edge in derived:
            owners = edge["source_owners"]
            outward_nodes = edge["outward_source_node_ids"]
            require(len(owners) == 1 and owners[0].startswith("via:") and len(outward_nodes) == 2, "derived singleton")
            via_id = owners[0][4:]
            via = vias[via_id]
            expected = [via["start_node_id_fold"], via["end_node_id_fold"]]
            outward_to_raw = 1 if outward_nodes == expected else -1 if outward_nodes == expected[::-1] else 0
            require(outward_to_raw != 0, "derived outward source endpoints")
            records.append(source_record(via_id, int(edge["final_finite_edge_index"]), int(edge["native_active_edge_index"]), edge["role"], int(edge["outward_sign"]), outward_to_raw, "DERIVED_SINGLE_VIA_ENDPOINT_COMPLEMENT", vias, nodes))

        for row in direct:
            native = int(row["native_active_edge_index"])
            edge = by_native[native]
            require(edge["binding_status"] == "UNRESOLVED_SOURCE_ENDPOINT_JOIN" and edge["role"] == "ground", "direct inventory relation")
            require(int(edge["final_finite_edge_index"]) == int(row["final_finite_edge_index"]), "direct final row")
            require(row["via_id"] == edge["source_owners"][0][4:], "direct Via identity")
            records.append(source_record(row["via_id"], int(row["final_finite_edge_index"]), native, "ground", int(edge["outward_sign"]), 1, "BOUND_SAVED_L02_CONTACT_WITHOUT_TERMINAL_LANDING", vias, nodes))

        leg0_via_reference_count = 0
        for composite in composites:
            native = int(composite["native_active_finite_row"])
            edge = by_native[native]
            require(edge["binding_status"] == "EXISTING_SPLIT_COMPOSITE_LEG_SOURCE_PENDING" and edge["existing_split_leg"] == 1 and edge["role"] == "ground", "composite inventory relation")
            legs = {int(leg["final_split_leg"]): leg for leg in composite["final_legs"]}
            require(set(legs) == {0, 1}, "composite leg IDs")
            leg0, leg1 = legs[0], legs[1]
            leg0_vias = [segment for segment in leg0["ordered_source_segments"] if segment["kind"] == "via"]
            leg1_vias = [segment for segment in leg1["ordered_source_segments"] if segment["kind"] == "via"]
            leg0_via_reference_count += len(leg0_vias)
            require(len(leg1_vias) == 1 and int(leg1["final_finite_row_id"]) == int(edge["final_finite_edge_index"]), "port composite leg")
            segment = leg1_vias[0]
            require(segment["source_orientation"] == -1, "port leg raw Via orientation")
            require(segment["path_start_node_id"] == composite["l02_junction_source_node_id"], "port leg junction")
            records.append(source_record(segment["via_id"], int(leg1["final_finite_row_id"]), native, "ground", int(leg1["port_outward_sign"]), 1, "BOUND_QUALIFIED_COMPOSITE_PORT_LEG_1", vias, nodes, {"composite_native_active_finite_row": native, "leg0_via_count": len(leg0_vias), "composite_delta": PINS["composite_delta"][1]}))

        require(len(records) == TOTAL, "normalized record count")
        require(Counter(row["role"] for row in records) == Counter({"power": 978, "ground": 25812}), "role count")
        require(len({row["final_active_current_row"] for row in records}) == TOTAL, "final current rows duplicate")
        require(len({row["native_active_current_row"] for row in records}) == TOTAL, "native current rows duplicate")
        require(len({row["via_id"] for row in records}) == TOTAL, "source Via IDs duplicate")
        require(Counter(row["source_class"] for row in records) == Counter({"DERIVED_SINGLE_VIA_ENDPOINT_COMPLEMENT": 22765, "BOUND_SAVED_L02_CONTACT_WITHOUT_TERMINAL_LANDING": 4005, "BOUND_QUALIFIED_COMPOSITE_PORT_LEG_1": 20}), "source classes")
        require(leg0_via_reference_count == 73, "composite leg-0 Via reference count")
        require(all(row["port_outward_to_raw_top_to_l02_sign"] == 1 for row in records), "outward/raw orientation")
        budget.check("normalized all first-post source rows")

        artifact = output / "first-post-source-map.npz"
        np.savez_compressed(artifact, records_json_utf8=np.frombuffer(json.dumps(records, separators=(",", ":"), allow_nan=False).encode("utf-8"), dtype=np.uint8))
        result = {
            "program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "COMPLETED_NORMALIZED_FULL_RETURN_FIRST_POST_SOURCE_MAP",
            "driver": receipt(frozen_driver), "inputs": inputs,
            "counts": {"records": TOTAL, "power": 978, "ground": 25812, "composite_port_leg1_vias": 20, "composite_leg0_vias_referenced_only": leg0_via_reference_count},
            "checks": {"unique_final_rows": True, "unique_native_rows": True, "unique_source_vias": True, "all_exact_dr0102_60_top_to_l02": True, "all_via_xy_coincident": True, "all_port_outward_to_raw_top_to_l02_sign_positive": True, "composite_leg0_not_assigned_port_current": True},
            "output": receipt(artifact), "budget": budget.receipt(),
            "budget_kind": "cooperative in-process elapsed/RSS check; not an external guard",
            "scope": "Saved source-segment identity/orientation map only. It does not load a field or current, infer a DUT electrode, construct a material or solid-shape model, evaluate Green/FMM, or make magnetic, physical, or board-accuracy claims. The 73 composite leg-0 Via segments remain only references to the pinned composite delta and are not assigned port current.",
        }
        recon._atomic_exclusive_json(output / "result.json", result)
        print(json.dumps({"status": result["status"], "counts": result["counts"], "output": result["output"], "budget": result["budget"]}), flush=True)
    except BaseException as error:
        recon._atomic_exclusive_json(output / "failure.json", {"status": "STOP_NORMALIZED_FULL_RETURN_FIRST_POST_SOURCE_MAP", "error_type": type(error).__name__, "error": str(error), "driver": receipt(frozen_driver), "inputs": inputs, "budget": budget.receipt()})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args().output.resolve())


if __name__ == "__main__":
    main()
