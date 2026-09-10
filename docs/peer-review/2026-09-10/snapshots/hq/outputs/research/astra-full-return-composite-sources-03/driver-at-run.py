"""Persist the 20 already-qualified split-composite source paths as final-leg deltas."""
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
EXPECTED_COMPOSITES = 20
FREQUENCY_HZ = 1_000_000.0
PINS = {
    "inventory_result": (R / "astra-full-return-source-owners-04" / "result.json", "1f423c564ae922bcf9ec066cfba7881b915723cf33af2ef7cf17865c20931e01"),
    "inventory_ledger": (R / "astra-full-return-source-owners-04" / "source-owner-inventory.json", "4cdca3e08018ed552d987a0d23489499d03fc4365d6d5d37cfb498c9478caa82"),
    "circuit_result": (R / "astra-l02-circuit-contact-binding-01" / "result.json", "1ef00f9c88553faf71f6cd87b4899c23d4a1be598a18a08c19b8776148690efc"),
    "circuit_binding": (R / "astra-l02-circuit-contact-binding-01" / "circuit-contact-binding.npz", "61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020"),
    "source_paths_result": (R / "astra-l02-composite-source-paths-03" / "result.json", "e534da4f465ecb081ae3ec0bcdf96a1ba5329bd4f5c1353b6dfa262ef4f858ff"),
    "source_paths_review": (R / "astra-l02-composite-source-paths-03" / "independent-review.json", "3ae31ad5e917cd6d207e90fdf654918fc8dfb51f31f1c20113529df4ed4c98be"),
    "combined_map_result": (R / "astra-l02-l14-l25-combined-assembly-map-02" / "result.json", "cdbb80f3412732f614c1d482a3cc8288c5c6134b5dfb1de2376711309039ae81"),
    "combined_map": (R / "astra-l02-l14-l25-combined-assembly-map-02" / "combined-assembly-map.npz", "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469"),
    "native_selected_result": (R / "astra-native-selected-first-via-map-02" / "result.json", "509f52f940ea43c7869256f4b36eb12adeb7c70dea9ea41e9cb5c56894b17308"),
    "native_selected_map": (R / "astra-native-selected-first-via-map-02" / "selected-native-first-vias.npz", "aa8349094aae8a27c5f2f05cda41e69bb40171ff76911f3507b499673efe8500"),
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


def pin_receipts() -> dict[str, dict[str, object]]:
    values = {}
    for name, (path, expected) in PINS.items():
        require(path.is_file(), f"missing {name}")
        actual = sha256(path)
        require(actual == expected, f"{name} hash differs")
        values[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
    return values


def packed_json(array: np.ndarray, label: str) -> object:
    require(array.dtype == np.uint8 and array.ndim == 1, f"{label} encoding")
    return json.loads(array.tobytes().decode("utf-8"))


def path_segments(link: dict[str, object]) -> tuple[list[dict[str, object]], int]:
    """Order each saved Via/trace segment by the saved source-node path."""
    nodes = list(link["source_path_node_ids_fold"])
    junction = str(link["l02_junction_node_id_fold"])
    require(nodes.count(junction) == 1, "junction node is not unique in source path")
    junction_offset = nodes.index(junction)
    by_pair: dict[frozenset[str], list[tuple[str, dict[str, object]]]] = {}
    for row in link["source_vias_in_native_path_order"]:
        pair = frozenset((str(row["start_node_id_fold"]), str(row["end_node_id_fold"])))
        by_pair.setdefault(pair, []).append(("via", row))
    for row in link["source_ideal_trace_bridges"]:
        pair = frozenset((str(row["start_node_id_fold"]), str(row["end_node_id_fold"])))
        by_pair.setdefault(pair, []).append(("trace", row))
    segments = []
    for index, (start, end) in enumerate(zip(nodes, nodes[1:])):
        candidates = by_pair.get(frozenset((start, end)), [])
        require(len(candidates) == 1, f"source path segment {index} is ambiguous or missing")
        kind, row = candidates[0]
        forward = str(row["start_node_id_fold"]) == start
        require(forward or str(row["end_node_id_fold"]) == start, "segment source orientation")
        value = {
            "kind": kind,
            "owner_id": str(row["owner_id_fold"]),
            "path_start_node_id": start,
            "path_end_node_id": end,
            "source_orientation": 1 if forward else -1,
        }
        if kind == "via":
            value.update({
                "via_id": str(row["via_id_fold"]),
                "resistance_ohm": float(row["resistance_ohm"]),
                "inductance_h": float(row["inductance_h"]),
                "source_status": str(row["status"]),
            })
        else:
            value.update({"trace_id": str(row["trace_id_fold"]), "source_status": str(row["geometry_status"])})
        segments.append(value)
    return segments, junction_offset


def relative_close(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(abs(expected), 1.0e-30) * 2.0e-13


def run(output: Path) -> None:
    budget = recon._Budget.create(45, 2)
    inputs = pin_receipts()
    output.mkdir(parents=True, exist_ok=False)
    frozen_driver = output / "driver-at-run.py"
    frozen_driver.write_bytes(Path(__file__).read_bytes())
    try:
        inventory_result = json.loads(PINS["inventory_result"][0].read_bytes())
        circuit_result = json.loads(PINS["circuit_result"][0].read_bytes())
        source_result = json.loads(PINS["source_paths_result"][0].read_bytes())
        source_review = json.loads(PINS["source_paths_review"][0].read_bytes())
        combined_result = json.loads(PINS["combined_map_result"][0].read_bytes())
        native_result = json.loads(PINS["native_selected_result"][0].read_bytes())
        require(inventory_result["status"] == "COMPLETED_ALL_PORT_SOURCE_OWNER_INVENTORY_NO_FIELD", "inventory status")
        require(inventory_result["output"]["sha256"] == PINS["inventory_ledger"][1], "inventory receipt")
        require(circuit_result["status"] == "COMPLETED_SOURCE_L02_CIRCUIT_CONTACT_BINDING", "circuit status")
        require(circuit_result["output"]["sha256"] == PINS["circuit_binding"][1], "circuit receipt")
        require(source_result["composite_count"] == EXPECTED_COMPOSITES, "source path count")
        require(source_review["status"] == "ACCEPT_L02_COMPOSITE_SOURCE_PATH_RECOVERY_INDEPENDENT_REVIEW", "source path review")
        require(source_review["checks"]["all_l02_splits_and_leg_sums_exact"], "source path leg review")
        require(combined_result["status"] == "PASS_L02_L14_L25_COMBINED_ASSEMBLY_MAP_NO_LU", "combined map status")
        require(native_result["status"] == "VERIFIED_NATIVE_INTERFACE_MAP_AND_SAVED_FIELD_DIAGNOSTIC", "native map status")
        budget.check("pinned source artifacts")

        inventory = json.loads(PINS["inventory_ledger"][0].read_bytes())
        inherited = Counter(edge["binding_status"] for edge in inventory["edges"])
        require(inherited == Counter({"DERIVED_SINGLE_VIA_ENDPOINT_COMPLEMENT": 22765, "EXISTING_SPLIT_COMPOSITE_LEG_SOURCE_PENDING": 20, "UNRESOLVED_SOURCE_ENDPOINT_JOIN": 4005}), "inventory status partition")
        pending = [edge for edge in inventory["edges"] if edge["binding_status"] == "EXISTING_SPLIT_COMPOSITE_LEG_SOURCE_PENDING"]
        require(len(pending) == EXPECTED_COMPOSITES and all(edge["role"] == "ground" and edge["existing_split_leg"] == 1 for edge in pending), "pending composite class")
        pending_by_native = {int(edge["native_active_edge_index"]): edge for edge in pending}
        require(len(pending_by_native) == EXPECTED_COMPOSITES, "pending native rows duplicate")
        budget.check("inventory composite selection")

        links_by_native = {int(link["active_finite_index"]): link for link in source_result["links"]}
        require(len(links_by_native) == EXPECTED_COMPOSITES == len(source_result["links"]), "source link rows")
        with np.load(PINS["circuit_binding"][0], allow_pickle=False) as binding:
            junctions = packed_json(binding["junctions_json_utf8"], "junctions")
        junction_by_native = {int(row["replaced_active_finite_index"]): row for row in junctions}
        require(len(junction_by_native) == EXPECTED_COMPOSITES, "junction rows")

        with np.load(PINS["combined_map"][0], allow_pickle=False) as mapping:
            final_first = np.asarray(mapping["final_finite_first_active_index"], dtype=np.int64)
            final_second = np.asarray(mapping["final_finite_second_active_index"], dtype=np.int64)
            final_admittance = np.asarray(mapping["final_finite_admittance_s"], dtype=np.complex128)
            final_native = np.asarray(mapping["final_finite_native_active_row"], dtype=np.int64)
            final_leg = np.asarray(mapping["final_finite_split_leg"], dtype=np.int8)
            contact_active = np.asarray(mapping["l02_contact_active_indices"], dtype=np.int64)
        with np.load(PINS["native_selected_map"][0], allow_pickle=False) as selected:
            outside_ground_rows = set(np.asarray(selected["ground_outside_active_edge_index"], dtype=np.int64).tolist())
        require(set(pending_by_native).issubset(outside_ground_rows), "composites absent from saved external G rows")
        budget.check("combined and native row maps")

        records = []
        for native_row in sorted(pending_by_native):
            pending_row = pending_by_native[native_row]
            link = links_by_native.get(native_row)
            junction = junction_by_native.get(native_row)
            require(link is not None and junction is not None, "missing qualified composite")
            require(str(pending_row["quotient_edge_id"]) == str(link["link_id"]), "inventory/source link ID")
            require(str(link["link_id"]) == str(junction["link_id"]), "source/circuit link ID")
            require(list(pending_row["source_owners"]) == list(link["owners_sorted"]), "inventory/source Via ownership")
            require(list(link["active_endpoints"]) == list(junction["original_active_endpoints"]), "source/circuit original endpoint order")
            require(int(junction["l02_split_after_source_via_count"]) == int(link["l02_split_after_source_via_count"]), "source split count")
            require(relative_close(float(junction["first_leg_resistance_ohm"]), float(link["first_leg_resistance_ohm"])), "first leg resistance")
            require(relative_close(float(junction["second_leg_resistance_ohm"]), float(link["second_leg_resistance_ohm"])), "second leg resistance")
            require(relative_close(float(junction["first_leg_inductance_h"]), float(link["first_leg_inductance_h"])), "first leg inductance")
            require(relative_close(float(junction["second_leg_inductance_h"]), float(link["second_leg_inductance_h"])), "second leg inductance")

            segments, junction_offset = path_segments(link)
            first_segments, second_segments = segments[:junction_offset], segments[junction_offset:]
            split_count = int(link["l02_split_after_source_via_count"])
            require(sum(item["kind"] == "via" for item in first_segments) == split_count, "first leg Via count")
            require(sum(item["kind"] == "via" for item in second_segments) + split_count == len(link["source_vias_in_native_path_order"]), "second leg Via count")
            for segment_group, r_key, l_key in ((first_segments, "first_leg_resistance_ohm", "first_leg_inductance_h"), (second_segments, "second_leg_resistance_ohm", "second_leg_inductance_h")):
                actual_r = sum(float(item.get("resistance_ohm", 0.0)) for item in segment_group)
                actual_l = sum(float(item.get("inductance_h", 0.0)) for item in segment_group)
                require(relative_close(actual_r, float(link[r_key])), f"{r_key} source sum")
                require(relative_close(actual_l, float(link[l_key])), f"{l_key} source sum")

            final_rows = np.flatnonzero(final_native == native_row)
            require(final_rows.size == 2 and set(final_leg[final_rows].tolist()) == {0, 1}, "final split rows")
            by_leg = {int(final_leg[row]): int(row) for row in final_rows}
            require(by_leg[1] == int(pending_row["final_finite_edge_index"]), "inventory final leg-1 row")
            junction_active = int(contact_active[int(junction["contact_ordinal"])])
            original_first, original_second = (int(value) for value in link["active_endpoints"])
            require(int(pending_row["first_active_index"]) == junction_active, "port leg starts at junction contact")
            require(int(pending_row["second_active_index"]) == original_second, "port leg preserves native port endpoint")
            expected_endpoints = {0: (original_first, junction_active), 1: (junction_active, original_second)}
            expected_rl = {
                0: (float(link["first_leg_resistance_ohm"]), float(link["first_leg_inductance_h"]), first_segments),
                1: (float(link["second_leg_resistance_ohm"]), float(link["second_leg_inductance_h"]), second_segments),
            }
            final_legs = []
            for leg in (0, 1):
                row = by_leg[leg]
                expected_first, expected_second = expected_endpoints[leg]
                require((int(final_first[row]), int(final_second[row])) == (expected_first, expected_second), "final leg endpoints")
                resistance, inductance, leg_segments = expected_rl[leg]
                expected_admittance = 1.0 / complex(resistance, 2.0 * np.pi * FREQUENCY_HZ * inductance)
                require(abs(final_admittance[row] - expected_admittance) <= abs(expected_admittance) * 2.0e-13, "final leg admittance")
                port_sign = 1 if expected_first == 2656 else -1 if expected_second == 2656 else 0
                final_legs.append({
                    "final_finite_row_id": row,
                    "final_split_leg": leg,
                    "final_first_active_index": expected_first,
                    "final_second_active_index": expected_second,
                    "port_outward_sign": port_sign,
                    "resistance_ohm": resistance,
                    "inductance_h": inductance,
                    "ordered_source_segments": leg_segments,
                })
            require(final_legs[0]["port_outward_sign"] == 0 and final_legs[1]["port_outward_sign"] == -1, "port incident final leg")
            records.append({
                "original_compiled_link_id": str(link["link_id"]),
                "native_active_finite_row": native_row,
                "original_active_endpoints": list(link["active_endpoints"]),
                "port_incident_final_active_index": int(pending_row["final_finite_edge_index"]),
                "source_path_endpoint_ids": [str(link["source_path_node_ids_fold"][0]), str(link["source_path_node_ids_fold"][-1])],
                "l02_junction_source_node_id": str(link["l02_junction_node_id_fold"]),
                "l02_split_after_source_via_count": split_count,
                "final_link_identity": "combined-map final_finite_row_id; no textual final link ID is serialized",
                "final_legs": final_legs,
                "status": "BOUND_QUALIFIED_COMPOSITE_SOURCE_SEGMENTS",
            })
        require(len(records) == EXPECTED_COMPOSITES, "composite record count")
        require(sum(len(row["final_legs"]) for row in records) == 40, "final leg count")
        require(len({leg["final_finite_row_id"] for row in records for leg in row["final_legs"]}) == 40, "final rows duplicate")
        require(len({segment["owner_id"] for row in records for leg in row["final_legs"] for segment in leg["ordered_source_segments"] if segment["kind"] == "via"}) == 93, "Via source ownership duplicate")
        budget.check("all composite recomposition gates")

        delta_path = output / "composite-source-segment-delta.npz"
        np.savez_compressed(delta_path, records_json_utf8=np.frombuffer(json.dumps(records, separators=(",", ":"), allow_nan=False).encode("utf-8"), dtype=np.uint8))
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "COMPLETED_QUALIFIED_FULL_RETURN_COMPOSITE_SOURCE_DELTA",
            "driver": receipt(frozen_driver),
            "inputs": inputs,
            "inherited_source_owner_statuses": dict(sorted(inherited.items())),
            "counts": {"native_composites": 20, "final_legs": 40, "source_vias": 93},
            "checks": {"each_native_composite_has_two_final_legs": True, "all_40_final_rows_unique": True, "all_native_composites_recompose_saved_rl_exactly": True, "source_via_ownership_exact_once": True, "all_port_incident_rows_are_leg_1_with_negative_outward_sign": True, "all_composites_are_saved_native_ground_outside_rows": True},
            "output": receipt(delta_path),
            "budget": budget.receipt(),
            "budget_kind": "cooperative in-process elapsed/RSS check; not an external guard",
            "scope": "Preserves the qualified ordered source Via/ideal-trace ownership behind the 20 native composites and their 40 already-assembled final legs. It does not reopen the 42 excluded boundary owners, bind the 4005 direct contacts, identify DUT pads, build a mesh, read currents, solve a field, evaluate Green/FMM, or claim magnetic or board accuracy.",
        }
        recon._atomic_exclusive_json(output / "result.json", result)
        print(json.dumps({"status": result["status"], "counts": result["counts"], "output": result["output"], "budget": result["budget"]}), flush=True)
    except BaseException as error:
        failure = {"status": "STOP_QUALIFIED_FULL_RETURN_COMPOSITE_SOURCE_DELTA", "error_type": type(error).__name__, "error": str(error), "driver": receipt(frozen_driver), "inputs": inputs, "budget": budget.receipt()}
        recon._atomic_exclusive_json(output / "failure.json", failure)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args().output.resolve())


if __name__ == "__main__":
    main()
