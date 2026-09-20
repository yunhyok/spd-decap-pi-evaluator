"""Bind the saved direct G-port-to-L02 source contacts without a solve.

The preceding owner inventory deliberately leaves rows with no terminal-landing
proof unresolved.  This helper binds only the separately saved L02 circuit
contacts for that exact class; it does not identify a DUT pad or electrode.
"""
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
OUTPUT_NAME = "astra-full-return-source-contacts-01"
EXPECTED_COUNT = 4005
EXPECTED_STATUS = "BOUND_SAVED_L02_CONTACT_WITHOUT_TERMINAL_LANDING"
PINS = {
    "inventory_result": (
        R / "astra-full-return-source-owners-04" / "result.json",
        "1f423c564ae922bcf9ec066cfba7881b915723cf33af2ef7cf17865c20931e01",
    ),
    "inventory_ledger": (
        R / "astra-full-return-source-owners-04" / "source-owner-inventory.json",
        "4cdca3e08018ed552d987a0d23489499d03fc4365d6d5d37cfb498c9478caa82",
    ),
    "circuit_result": (
        R / "astra-l02-circuit-contact-binding-01" / "result.json",
        "1ef00f9c88553faf71f6cd87b4899c23d4a1be598a18a08c19b8776148690efc",
    ),
    "circuit_binding": (
        R / "astra-l02-circuit-contact-binding-01" / "circuit-contact-binding.npz",
        "61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def check_pins() -> dict[str, dict[str, object]]:
    receipts = {}
    for name, (path, expected) in PINS.items():
        require(path.is_file(), f"missing {name}: {path}")
        actual = sha256(path)
        require(actual == expected, f"{name} hash differs")
        receipts[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
    return receipts


def unpack_text(array: np.ndarray, label: str) -> list[str]:
    require(array.dtype == np.uint8 and array.ndim == 1, f"{label} text encoding")
    value = json.loads(array.tobytes().decode("utf-8"))
    require(isinstance(value, list) and all(isinstance(item, str) for item in value), f"{label} text list")
    return value


def run(output: Path) -> None:
    budget = recon._Budget.create(45, 2)
    inputs = check_pins()
    output.mkdir(parents=True, exist_ok=False)
    frozen_driver = output / "driver-at-run.py"
    frozen_driver.write_bytes(Path(__file__).read_bytes())
    try:
        inventory_result = json.loads(PINS["inventory_result"][0].read_bytes())
        require(inventory_result["status"] == "COMPLETED_ALL_PORT_SOURCE_OWNER_INVENTORY_NO_FIELD", "inventory status")
        require(inventory_result["output"]["sha256"] == PINS["inventory_ledger"][1], "inventory output receipt")
        circuit_result = json.loads(PINS["circuit_result"][0].read_bytes())
        require(circuit_result["status"] == "COMPLETED_SOURCE_L02_CIRCUIT_CONTACT_BINDING", "circuit status")
        require(circuit_result["output"]["sha256"] == PINS["circuit_binding"][1], "circuit output receipt")
        budget.check("pinned receipts")

        inventory = json.loads(PINS["inventory_ledger"][0].read_bytes())
        edges = inventory["edges"]
        source_vias = inventory["source_vias"]
        source_nodes = inventory["source_nodes"]
        inherited = Counter(edge["binding_status"] for edge in edges)
        require(
            inherited == Counter({
                "DERIVED_SINGLE_VIA_ENDPOINT_COMPLEMENT": 22765,
                "EXISTING_SPLIT_COMPOSITE_LEG_SOURCE_PENDING": 20,
                "UNRESOLVED_SOURCE_ENDPOINT_JOIN": EXPECTED_COUNT,
            }),
            f"unexpected inherited statuses: {inherited}",
        )
        unresolved = [edge for edge in edges if edge["binding_status"] == "UNRESOLVED_SOURCE_ENDPOINT_JOIN"]
        require(len(unresolved) == EXPECTED_COUNT, "unresolved count")
        budget.check("saved inventory parse")

        with np.load(PINS["circuit_binding"][0], allow_pickle=False) as binding:
            via_ids = unpack_text(binding["via_ids_json_utf8"], "via IDs")
            l02_node_ids = unpack_text(binding["l02_endpoint_node_ids_json_utf8"], "L02 node IDs")
            active_index = np.asarray(binding["active_finite_index"], dtype=np.int64)
            first_index = np.asarray(binding["first_active_index"], dtype=np.int64)
            second_index = np.asarray(binding["second_active_index"], dtype=np.int64)
            target_side = np.asarray(binding["target_side"], dtype=np.int8)
            l02_is_start = np.asarray(binding["l02_endpoint_is_start"], dtype=np.bool_)
        require(len(via_ids) == len(l02_node_ids) == len(active_index), "circuit row length")
        contact_row_by_via = {via.casefold(): row for row, via in enumerate(via_ids)}
        require(len(contact_row_by_via) == len(via_ids), "circuit via IDs duplicate")
        budget.check("circuit binding parse")

        delta = []
        for edge in unresolved:
            require(edge["role"] == "ground" and edge["outward_sign"] == 1, "unresolved role/sign")
            require(edge["existing_split_leg"] == -1 and edge["native_parallel_count"] == 1, "unresolved native class")
            require(edge["compiled_landing_source_node_ids"] == [], "unexpected terminal landing")
            require(edge["source_endpoint_candidates"] == [[], []], "unexpected quotient vertex candidate")
            owners = edge["source_owners"]
            require(len(owners) == 1 and owners[0].startswith("via:"), "unresolved source owner")
            via_id = owners[0][4:]
            contact_row = contact_row_by_via.get(via_id)
            require(contact_row is not None, f"missing circuit binding for {via_id}")
            require(int(active_index[contact_row]) == edge["native_active_edge_index"], "native active row")
            require((int(first_index[contact_row]), int(second_index[contact_row])) == (2656, 349710), "direct G/L02 endpoints")
            require(int(target_side[contact_row]) == 1 and not bool(l02_is_start[contact_row]), "L02 side")
            via = source_vias[via_id]
            upper = via["start_node_id_fold"]
            lower = via["end_node_id_fold"]
            require(upper != lower, "source via endpoints collide")
            require(source_nodes[upper]["node_id_fold"] == upper, "upper source node")
            require(source_nodes[lower]["node_id_fold"] == lower, "lower source node")
            require(source_nodes[lower]["layer_id_fold"] == "signal$l02(dgnd)", "lower L02 layer")
            require(l02_node_ids[contact_row].casefold() == lower, "saved L02 node differs")
            delta.append({
                "final_finite_edge_id": edge["quotient_edge_id"],
                "final_finite_edge_index": edge["final_finite_edge_index"],
                "native_active_edge_index": edge["native_active_edge_index"],
                "via_id": via_id,
                "contact_binding_row": contact_row,
                "upper_raw_source_node_id": upper,
                "lower_raw_source_node_id": lower,
                "status": EXPECTED_STATUS,
            })
        require(len(delta) == EXPECTED_COUNT, "delta count")
        require(len({row["via_id"] for row in delta}) == EXPECTED_COUNT, "delta via duplicate")
        require(len({row["native_active_edge_index"] for row in delta}) == EXPECTED_COUNT, "delta native edge duplicate")
        require(len({row["contact_binding_row"] for row in delta}) == EXPECTED_COUNT, "delta contact row duplicate")
        budget.check("all 4005 direct contact joins")

        delta_path = output / "bound-source-contact-delta.json"
        recon._atomic_exclusive_json(delta_path, {"records": delta})
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "COMPLETED_SAVED_FULL_RETURN_SOURCE_CONTACT_BINDING",
            "driver": receipt(frozen_driver),
            "inputs": inputs,
            "inherited_source_owner_statuses": dict(sorted(inherited.items())),
            "bound_contact_count": len(delta),
            "checks": {
                "all_4005_unique_via_ids": True,
                "all_4005_unique_native_active_edges": True,
                "all_4005_unique_contact_binding_rows": True,
                "all_4005_ground_port_to_l02_target": True,
                "all_4005_saved_l02_node_equals_raw_lower_node": True,
                "terminal_landing_proof_absent_for_each_bound_record": True,
            },
            "output": receipt(delta_path),
            "budget": budget.receipt(),
            "scope": "Binds saved source-segment endpoints to the existing conditional G-port-to-L02 circuit contact for 4005 exact singleton rows. It does not identify DUT electrode pads, construct a 3-D mesh, reuse or solve currents, evaluate a Green/FMM action, establish a magnetic model, or claim board accuracy.",
        }
        recon._atomic_exclusive_json(output / "result.json", result)
        print(json.dumps({"status": result["status"], "bound_contact_count": len(delta), "output": result["output"], "budget": result["budget"]}), flush=True)
    except BaseException as error:
        failure = {
            "status": "STOP_SAVED_FULL_RETURN_SOURCE_CONTACT_BINDING",
            "error_type": type(error).__name__,
            "error": str(error),
            "driver": receipt(frozen_driver),
            "inputs": inputs,
            "budget": budget.receipt(),
        }
        recon._atomic_exclusive_json(output / "failure.json", failure)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.output.resolve())


if __name__ == "__main__":
    main()
