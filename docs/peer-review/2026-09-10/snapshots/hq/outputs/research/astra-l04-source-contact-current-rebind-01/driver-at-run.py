"""Bind saved accepted branch currents once to the accepted L04 source-contact ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
sys.path.insert(0, str(ROOT / "outputs" / "research-runtime"))
import reconstruct_astra_native_loaded_field as recon

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
LEDGER = RESEARCH / "astra-l04-source-contact-inputs-01"
PINS = {
    "l04_contact_ledger": (LEDGER / "contact-ledger-inputs.npz", "6e5886f81a14c1f1fec43a4af64a175bca518486cfd74fbc7794c551bd74ec4a"),
    "l04_contact_result": (LEDGER / "result.json", "96c7396b9c4d8cba85677ca18e643bad1376e7f3047614c5a9b158071662861d"),
    "l04_contact_independent_review": (RESEARCH / "astra-l04-source-contact-inputs-review-01/independent-review.json", "3b144bee81a2ef83ed906968cf91240176e54f4f93d9e2e851cb5e58b9875240"),
    "accepted_all_finite_currents": (RESEARCH / "astra-all-finite-current-l-ownership-01/all-finite-current-l-ownership.npz", "1b908c62e385ea1c227e77b1ccc37dd5c4b3376cce6f67d7b4e5c4a0c20508e6"),
    "accepted_all_finite_result": (RESEARCH / "astra-all-finite-current-l-ownership-01/result.json", "b9641892dd80cbbecd48ea1a30e58b7afee9ab589878ed613f797e863cb75e7f"),
    "accepted_all_finite_qualification": (RESEARCH / "astra-all-finite-current-l-ownership-02/result.json", "52f77a11fd723f3d013edcb4331c5ba040d6a1148a50edf57b5f487095231052"),
    "accepted_all_finite_qualification_artifact": (RESEARCH / "astra-all-finite-current-l-ownership-02/corrected-units-and-power-metadata.npz", "73c011f0ae4cf650a3294e00ac9ab560324bbd296ca031aeac6abde902390f4a"),
    "combined_map": (RESEARCH / "astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz", "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469"),
    "budget_helper": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
}


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": digest(path), "size_bytes": path.stat().st_size}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def write_json_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def verify_pins() -> dict:
    receipts = {}
    for name, (path, expected) in PINS.items():
        require(digest(path) == expected, f"input SHA-256 differs: {name}")
        receipts[name] = receipt(path)
    return receipts


def check_self() -> None:
    verify_pins()
    with np.load(PINS["l04_contact_ledger"][0], allow_pickle=False) as ledger, \
         np.load(PINS["accepted_all_finite_currents"][0], allow_pickle=False) as currents, \
         np.load(PINS["combined_map"][0], allow_pickle=False) as mapping:
        category = np.asarray(ledger["category"], dtype=np.int8)
        parent = np.asarray(ledger["expanded_branch_index"], dtype=np.int64)
        active = np.asarray(ledger["active_finite_index"], dtype=np.int64)
        junction = np.asarray(ledger["junction_array_ordinal"], dtype=np.int64)
        native = np.asarray(currents["native_active_current_row"], dtype=np.int64)
        split = np.asarray(currents["final_split_leg"], dtype=np.int8)
        map_native = np.asarray(mapping["final_finite_native_active_row"], dtype=np.int64)
        map_split = np.asarray(mapping["final_finite_split_leg"], dtype=np.int8)
    ordinary = category == 0
    exceptions = category != 0
    require(np.bincount(category).tolist() == [76_136, 10, 20], "actual category counts")
    require(np.array_equal(native, map_native) and np.array_equal(split, map_split), "actual current/map identity")
    require(np.all(native[parent[ordinary]] == active[ordinary])
            and np.all(split[parent[ordinary]] == -1), "actual ordinary ledger/current identity")
    require(np.all(parent[exceptions] == 1_692_369 + junction[exceptions])
            and np.all(native[parent[exceptions]] == active[exceptions]) and np.all(split[parent[exceptions]] == 0),
            "actual exception ledger/current identity")
    print(f"{PROGRAM} v{VERSION}: L04 saved current rebind SELF_CHECK PASS")


def run(output: Path) -> None:
    require(not output.exists(), "output already exists")
    budget = recon._Budget.create(60, 4)
    output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    try:
        inputs = verify_pins()
        ledger_result = json.loads(PINS["l04_contact_result"][0].read_text(encoding="utf-8"))
        ledger_review = json.loads(PINS["l04_contact_independent_review"][0].read_text(encoding="utf-8"))
        qualify = json.loads(PINS["accepted_all_finite_qualification"][0].read_text(encoding="utf-8"))
        require(ledger_result["status"] == "COMPLETED_L04_SOURCE_CONTACT_INPUT_LEDGER", "ledger status")
        require(ledger_review["status"] == "ACCEPT_L04_SOURCE_CONTACT_INPUT_LEDGER_INDEPENDENT_REVIEW", "ledger review")
        require(qualify["status"] == "COMPLETED_ATTEMPT01_UNITS_AND_FINITE_POWER_QUALIFICATION", "current qualification")
        with np.load(PINS["l04_contact_ledger"][0], allow_pickle=False) as ledger, \
             np.load(PINS["accepted_all_finite_currents"][0], allow_pickle=False) as currents, \
             np.load(PINS["combined_map"][0], allow_pickle=False) as mapping:
            category = ledger["category"].astype(np.int8, copy=True)
            active_finite_index = ledger["active_finite_index"].astype(np.int64, copy=True)
            original_finite_index = ledger["original_finite_index"].astype(np.int64, copy=True)
            parent_final = ledger["expanded_branch_index"].astype(np.int64, copy=True)
            junction_array_ordinal = ledger["junction_array_ordinal"].astype(np.int64, copy=True)
            l02_contact_ordinal = ledger["l02_contact_ordinal"].astype(np.int64, copy=True)
            original_l04_target_side = ledger["original_l04_target_side"].astype(np.int8, copy=True)
            source_via_ordinal = ledger["source_via_ordinal"].astype(np.int64, copy=True)
            l04_endpoint_is_start = ledger["l04_endpoint_is_start"].astype(np.bool_, copy=True)
            source_via_ids = json.loads(ledger["via_ids_json_utf8"].tobytes())
            source_rows = json.loads(ledger["source_rows_json_utf8"].tobytes())
            path_maps = json.loads(ledger["path_maps_json_utf8"].tobytes())
            parent_native = currents["native_active_current_row"][parent_final].astype(np.int64, copy=True)
            parent_split_leg = currents["final_split_leg"][parent_final].astype(np.int8, copy=True)
            parent_current = currents["finite_current_first_to_second_a"][parent_final].astype(np.complex128, copy=True)
            current_final = currents["final_active_current_row"].astype(np.int64, copy=True)
            current_native = currents["native_active_current_row"].astype(np.int64, copy=True)
            current_split = currents["final_split_leg"].astype(np.int8, copy=True)
            map_native = mapping["final_finite_native_active_row"].astype(np.int64, copy=True)
            map_split = mapping["final_finite_split_leg"].astype(np.int8, copy=True)
            map_first = mapping["final_finite_first_active_index"].astype(np.int64, copy=True)
            map_second = mapping["final_finite_second_active_index"].astype(np.int64, copy=True)
            current_first = currents["first_active_node"].astype(np.int64, copy=True)
            current_second = currents["second_active_node"].astype(np.int64, copy=True)
        budget.check("pinned ledger/current/map arrays")
        n = len(category)
        require(n == 76_166 and all(array.shape == (n,) for array in (active_finite_index, original_finite_index, parent_final,
                junction_array_ordinal, l02_contact_ordinal, original_l04_target_side, source_via_ordinal,
                l04_endpoint_is_start, parent_native, parent_split_leg, parent_current)), "ledger/current row shapes")
        require(np.bincount(category).tolist() == [76_136, 10, 20], "category counts")
        require(np.array_equal(current_final, np.arange(len(current_final), dtype=np.int64)), "current final-row identity")
        require(len(current_final) == len(map_native) == len(map_split) == len(map_first) == len(map_second) == 1_692_409, "accepted expanded size")
        require(np.array_equal(current_native, map_native) and np.array_equal(current_split, map_split), "accepted current/map native split identity")
        require(np.array_equal(current_first, map_first) and np.array_equal(current_second, map_second), "accepted current/map endpoint identity")
        require(np.all(parent_native == map_native[parent_final]) and np.all(parent_split_leg == map_split[parent_final]), "selected parent current/map identity")
        require(len(source_via_ids) == len(source_rows) == n and len({item.casefold() for item in source_via_ids}) == n, "unique saved source records")
        require(len(np.unique(source_via_ordinal)) == n and all(row["via_id"] == via for row, via in zip(source_rows, source_via_ids, strict=True)), "source ordinal and ID identity")
        ordinary, remap, children = category == 0, category == 1, category == 2
        require(len(np.unique(parent_final[ordinary])) == 76_136 and len(np.unique(parent_final[remap])) == 10, "ordinary/remap unique parents")
        require(np.all(parent_native[ordinary] == active_finite_index[ordinary])
                and np.all(parent_split_leg[ordinary] == -1), "ordinary native-unsplit ledger identity")
        exceptions = remap | children
        require(np.all(original_finite_index == active_finite_index)
                and np.all(parent_final[exceptions] == 1_692_369 + junction_array_ordinal[exceptions])
                and np.all(parent_native[exceptions] == active_finite_index[exceptions])
                and np.all(parent_split_leg[exceptions] == 0), "exception original/junction/current identity")
        require(set(map(int, junction_array_ordinal[remap])) | set(map(int, junction_array_ordinal[children])) == set(range(20)),
                "all accepted junction ordinals represented")
        require(len(path_maps) == 20, "accepted path-map count")
        for path in path_maps:
            junction = int(path["junction_array_ordinal"])
            positions = np.flatnonzero(junction_array_ordinal == junction)
            expected_count = 1 if path["action"] == "REMAP_EXISTING_L02_FIRST_LEG_L04_ENDPOINT" else 2
            require(len(positions) == expected_count
                    and int(path["existing_first_leg_expanded_index"]) == 1_692_369 + junction
                    and np.all(parent_final[positions] == int(path["existing_first_leg_expanded_index"]))
                    and np.all(active_finite_index[positions] == int(path["original_active_finite_index"]))
                    and np.all(l02_contact_ordinal[positions] == int(path["l02_contact_ordinal"])),
                    "accepted exception path/current join")
        child_parent, child_inverse = np.unique(parent_final[children], return_inverse=True)
        require(len(child_parent) == 10 and np.array_equal(np.bincount(child_inverse), np.full(10, 2)), "paired hidden child parents")
        require(np.all(parent_split_leg[remap | children] == 0), "exception parents are split leg0")
        for parent in child_parent:
            positions = np.flatnonzero(children & (parent_final == parent))
            require(len(positions) == 2 and l04_endpoint_is_start[positions[0]] != l04_endpoint_is_start[positions[1]], "paired child L04 orientations")
        all_parent, parent_inverse = np.unique(parent_final, return_inverse=True)
        parent_multiplicity = np.bincount(parent_inverse)
        require(len(all_parent) == 76_156 and np.count_nonzero(parent_multiplicity == 1) == 76_146
                and np.count_nonzero(parent_multiplicity == 2) == 10 and np.all((parent_multiplicity == 1) | (parent_multiplicity == 2)),
                "no missing or duplicate parent source records")
        parent_category = category[np.unique(parent_inverse, return_index=True)[1]]
        unique_totals = []
        record_totals = []
        for value in range(3):
            record_values = parent_current[category == value]
            parent_values = record_values[np.unique(parent_final[category == value], return_index=True)[1]]
            record_totals.append([float(record_values.sum().real), float(record_values.sum().imag)])
            unique_totals.append([float(parent_values.sum().real), float(parent_values.sum().imag)])
        require(parent_category.shape == (76_156,), "unique parent categorization")
        native_boundary = ordinary | remap
        native_outward = np.where(original_l04_target_side[native_boundary] == 0,
                                  parent_current[native_boundary], -parent_current[native_boundary])
        require(np.all(original_l04_target_side[native_boundary] >= 0)
                and np.all(original_l04_target_side[children] == -1), "native versus hidden L04 side contract")
        for parent in child_parent:
            positions = np.flatnonzero(children & (parent_final == parent))
            require(parent_current[positions[0]] == parent_current[positions[1]], "hidden pair shares present parent current")
        source_hashes = [row["source_record_sha256"] for row in source_rows]
        artifact = output / "l04-source-contact-current-rebind.npz"
        np.savez_compressed(
            artifact,
            category=category,
            active_finite_index=active_finite_index,
            original_finite_index=original_finite_index,
            parent_final_active_current_row=parent_final,
            parent_native_active_current_row=parent_native,
            parent_split_leg=parent_split_leg,
            junction_array_ordinal=junction_array_ordinal,
            l02_contact_ordinal=l02_contact_ordinal,
            original_l04_target_side=original_l04_target_side,
            source_via_ordinal=source_via_ordinal,
            l04_endpoint_is_start=l04_endpoint_is_start,
            parent_current_first_to_second_a=parent_current,
            unique_parent_final_active_current_row=all_parent,
            record_parent_unique_index=parent_inverse.astype(np.int64),
            hidden_child_unique_parent_final_active_current_row=child_parent,
            hidden_child_record_unique_parent_index=child_inverse.astype(np.int64),
            source_via_ids_json_utf8=np.frombuffer(json.dumps(source_via_ids, separators=(",", ":")).encode(), dtype=np.uint8),
            source_record_sha256_json_utf8=np.frombuffer(json.dumps(source_hashes, separators=(",", ":")).encode(), dtype=np.uint8),
        )
        budget.check("saved L04 current rebind artifact")
        result = {
            "program": PROGRAM, "version": VERSION,
            "status": "COMPLETED_L04_SAVED_SOURCE_CONTACT_CURRENT_REBIND",
            "driver": receipt(frozen), "inputs": inputs, "artifact": receipt(artifact),
            "counts": {"records_by_category": [76_136, 10, 20], "unique_parent_rows_by_category": [76_136, 10, 10], "unique_parent_rows_total": 76_156, "hidden_child_records": 20, "hidden_child_unique_present_parents": 10},
            "current_totals_a": {"record_sum_by_category": record_totals, "unique_parent_sum_by_category": unique_totals, "child_record_sum_is_not_throughput": True},
            "native_l04_boundary_current": {"record_count": 76_146,
                "signed_outward_sum_a": [float(native_outward.sum().real), float(native_outward.sum().imag)],
                "absolute_incident_sum_a": float(np.abs(native_outward).sum()),
                "half_absolute_incident_sum_a": float(0.5 * np.abs(native_outward).sum())},
            "accepted_power_current_provenance": qualify["finite_power_replay"],
            "identity_gates": {"accepted_current_and_a6_full_identity": True, "ordinary_native_unsplit_identity": True,
                "exception_original_junction_path_identity": True, "selected_parent_identity": True,
                "source_record_ids_unique": True, "source_via_ordinals_unique": True, "category_counts_exact": True,
                "hidden_children_pair_per_parent_with_opposite_l04_orientation": True,
                "hidden_pair_present_parent_current_equal": True},
            "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(60,4)", "cooperative_checks": True},
            "scope": "Saved-array current rebind only. Category0 binds 76,136 ordinary unique native-unsplit rows; category1 binds 10 existing L02 first-leg split-leg0 endpoint remaps; category2 retains 20 explicitly named future L04 series child ownership records paired on 10 present split-leg0 parent currents with opposite L04 endpoint orientation. Child records are not summed as port, cut, net, or throughput current. No source database query, original composite re-addition, source geometry work, R/L change, field load, solve, or magnetic action was performed.",
        }
        write_json_new(output / "result.json", result)
        print(json.dumps({"status": result["status"], "artifact": result["artifact"], "counts": result["counts"], "runtime": result["runtime"]}, sort_keys=True))
    except BaseException as error:
        write_json_new(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_L04_SAVED_SOURCE_CONTACT_CURRENT_REBIND", "error_type": type(error).__name__, "error": str(error), "driver": receipt(frozen), "budget": budget.receipt()})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        check_self()
    elif args.output is None:
        parser.error("--output required")
    else:
        run(args.output.resolve())


if __name__ == "__main__":
    main()
