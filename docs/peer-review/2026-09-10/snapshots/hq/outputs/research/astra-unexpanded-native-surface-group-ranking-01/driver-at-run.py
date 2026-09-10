"""Rank cached native surface groups from accepted stored finite currents only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "outputs" / "research-runtime"))
import reconstruct_astra_native_loaded_field as recon

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
NATIVE_POTENTIAL_COUNT = 756_889
R = ROOT / "outputs" / "research"
ALL01 = R / "astra-all-finite-current-l-ownership-01"
QUAL02 = R / "astra-all-finite-current-l-ownership-02"
PINS = {
    "cached_group_labels": (R / "astra-native-loaded-vtrip-field-02/source-sheet-current-ranking.json", "0a47ddd43d0e31a61eb31f285531976f24f5a63696bc4a9a682349ec5994786a"),
    "allfinite01_driver": (ALL01 / "driver-at-run.py", "4da0d49c62b1271d11fc80dded974c528078380eed42f41636a717009adab79e"),
    "allfinite01_result": (ALL01 / "result.json", "b9641892dd80cbbecd48ea1a30e58b7afee9ab589878ed613f797e863cb75e7f"),
    "allfinite01_arrays": (ALL01 / "all-finite-current-l-ownership.npz", "1b908c62e385ea1c227e77b1ccc37dd5c4b3376cce6f67d7b4e5c4a0c20508e6"),
    "qualification02_result": (QUAL02 / "result.json", "52f77a11fd723f3d013edcb4331c5ba040d6a1148a50edf57b5f487095231052"),
    "combined_map": (R / "astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz", "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469"),
    "combined_map_result": (R / "astra-l02-l14-l25-combined-assembly-map-02/result.json", "cdbb80f3412732f614c1d482a3cc8288c5c6134b5dfb1de2376711309039ae81"),
    "budget_helper": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def verify_pins() -> None:
    for name, (path, digest) in PINS.items():
        if sha(path) != digest:
            raise ValueError(f"{name} SHA-256 differs")


def pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def row_record(index: int, groups: list[dict], count: np.ndarray, absolute: np.ndarray, imbalance: np.ndarray) -> dict:
    label = groups[index]
    return {"active_index": int(label["active_index"]), "layer_net_sourcecomponent": label["source_components"],
            "finite_incident_row_count": int(count[index]), "finite_incident_absolute_current_a": float(absolute[index]),
            "finite_incident_imbalance_a": pair(imbalance[index]), "finite_incident_imbalance_abs_a": float(abs(imbalance[index]))}


def run(output: Path) -> None:
    if output.exists():
        raise ValueError("output already exists")
    budget = recon._Budget.create(60, 4)
    verify_pins()
    labels = json.loads(PINS["cached_group_labels"][0].read_text(encoding="utf-8"))
    prior = json.loads(PINS["allfinite01_result"][0].read_text(encoding="utf-8"))
    qualified = json.loads(PINS["qualification02_result"][0].read_text(encoding="utf-8"))
    if not (labels["status"] == "COMPLETED_NATIVE_SHEET_CURRENT_CENSUS" and labels["active_group_count"] == len(labels["groups"]) == 1323
            and prior["status"] == "COMPLETED_ACCEPTED_ALL_FINITE_CURRENT_L_OWNERSHIP_READBACK"
            and qualified["status"] == "COMPLETED_ATTEMPT01_UNITS_AND_FINITE_POWER_QUALIFICATION"
            and qualified["r_l_gates"] == {"all_inductance_nonnegative": True, "all_resistance_positive": True}):
        raise ValueError("cached labels or accepted all-finite qualification differs")
    groups = [{"active_index": int(item["active_index"]), "source_components": item["source_components"]} for item in labels["groups"]]
    active = np.asarray([item["active_index"] for item in groups], dtype=np.int64)
    if not (len(np.unique(active)) == len(active) and np.all((active >= 0) & (active < NATIVE_POTENTIAL_COUNT))):
        raise ValueError("cached group identities are not unique native-prefix coordinates")
    with np.load(PINS["combined_map"][0], allow_pickle=False) as mapping:
        map_first = np.asarray(mapping["final_finite_first_active_index"], dtype=np.int64)
        map_second = np.asarray(mapping["final_finite_second_active_index"], dtype=np.int64)
        map_native = np.asarray(mapping["final_finite_native_active_row"], dtype=np.int64)
        map_legs = np.asarray(mapping["final_finite_split_leg"], dtype=np.int8)
        targets = np.asarray(mapping["native_target_active_indices"], dtype=np.int64)
        ports = np.asarray(mapping["source_port_active_indices"], dtype=np.int64)
        l14_rows = np.asarray(mapping["l14_native_active_finite_rows"], dtype=np.int64)
        l02_rows = np.asarray(mapping["l02_native_active_finite_rows"], dtype=np.int64)
        l02_composites = np.asarray(mapping["l02_composite_active_finite_rows"], dtype=np.int64)
        l25_rows = np.asarray(mapping["l25_native_active_finite_rows"], dtype=np.int64)
    with np.load(PINS["allfinite01_arrays"][0], allow_pickle=False) as stored:
        final_rows = np.asarray(stored["final_active_current_row"], dtype=np.int64)
        native_rows = np.asarray(stored["native_active_current_row"], dtype=np.int64)
        split_legs = np.asarray(stored["final_split_leg"], dtype=np.int8)
        first = np.asarray(stored["first_active_node"], dtype=np.int64)
        second = np.asarray(stored["second_active_node"], dtype=np.int64)
        current = np.asarray(stored["finite_current_first_to_second_a"], dtype=np.complex128)
    if not (final_rows.shape == native_rows.shape == split_legs.shape == first.shape == second.shape == current.shape == (1_692_409,)
            and np.array_equal(final_rows, np.arange(len(final_rows), dtype=np.int64))
            and np.array_equal(native_rows, map_native) and np.array_equal(split_legs, map_legs)
            and np.array_equal(first, map_first) and np.array_equal(second, map_second)
            and len(np.unique(native_rows)) == 1_692_389):
        raise ValueError("accepted all-finite arrays do not reproduce the pinned final/native map identity")
    rebind_sets = (l14_rows, l02_rows, l02_composites, l25_rows)
    if not (set(map(int, targets)) == {349710, 718402, 258027} and len(np.unique(np.concatenate(rebind_sets))) == sum(len(rows) for rows in rebind_sets)
            and set(active[np.isin(active, targets)]) == set(targets)):
        raise ValueError("expanded L02/L14/L25 rebind identities differ")
    group_of_node = np.full(NATIVE_POTENTIAL_COUNT, -1, dtype=np.int64)
    group_of_node[active] = np.arange(len(active), dtype=np.int64)
    first_group = np.full(len(first), -1, dtype=np.int64)
    second_group = np.full(len(second), -1, dtype=np.int64)
    first_native = first < NATIVE_POTENTIAL_COUNT
    second_native = second < NATIVE_POTENTIAL_COUNT
    first_group[first_native] = group_of_node[first[first_native]]
    second_group[second_native] = group_of_node[second[second_native]]
    count, absolute, imbalance = (np.zeros(len(active), dtype=np.int64), np.zeros(len(active)), np.zeros(len(active), dtype=np.complex128))
    for group_index, sign in ((first_group, 1), (second_group, -1)):
        matched = group_index >= 0
        np.add.at(count, group_index[matched], 1)
        np.add.at(absolute, group_index[matched], np.abs(current[matched]))
        np.add.at(imbalance, group_index[matched], sign * current[matched])
    excluded_expanded = np.isin(active, targets)
    excluded_port = np.isin(active, ports)
    included = ~(excluded_expanded | excluded_port)
    if not (np.count_nonzero(excluded_expanded) == 3 and np.count_nonzero(excluded_port) == 1 and np.count_nonzero(included) == 1319):
        raise ValueError("cached-group exclusion coverage differs")
    current_rank = np.flatnonzero(included)[np.argsort(absolute[included])[::-1]]
    imbalance_rank = np.flatnonzero(included)[np.argsort(np.abs(imbalance[included]))[::-1]]
    records = [row_record(int(index), groups, count, absolute, imbalance) for index in np.flatnonzero(included)]
    artifact_arrays = {"active_index": active[included], "finite_incident_row_count": count[included],
                       "finite_incident_absolute_current_a": absolute[included], "finite_incident_imbalance_a": imbalance[included],
                       "source_labels_json_utf8": np.frombuffer(json.dumps(records, sort_keys=True).encode("utf-8"), dtype=np.uint8),
                       "top_by_absolute_current_group_index": current_rank[:20], "top_by_imbalance_group_index": imbalance_rank[:20]}
    budget.check("accepted current aggregation and cached-label rank")
    output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    artifact = output / "unexpanded-native-surface-group-ranking.npz"
    np.savez_compressed(artifact, **artifact_arrays)
    budget.check("ranking artifact")
    coverage = {"cached_groups": len(groups), "native_prefix_groups": int(np.count_nonzero(active < NATIVE_POTENTIAL_COUNT)),
                "expanded_l02_l14_l25_rebound_groups": int(np.count_nonzero(excluded_expanded)), "source_port_groups_excluded": int(np.count_nonzero(excluded_port)),
                "still_unexpanded_native_surface_groups": int(np.count_nonzero(included)), "included_groups_with_finite_incident_rows": int(np.count_nonzero(count[included])),
                "included_finite_incident_row_appearances": int(count[included].sum())}
    result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_ACCEPTED_UNEXPANDED_NATIVE_SURFACE_GROUP_RANKING",
              "driver": receipt(frozen), "inputs": {name: receipt(path) for name, (path, _digest) in PINS.items()}, "artifact": receipt(artifact),
              "native_prefix_identity": {"native_potential_count": NATIVE_POTENTIAL_COUNT, "all_cached_group_indices_in_prefix": True,
                                         "accepted_final_rows_and_endpoints_match_a6_map": True, "accepted_native_rows_and_split_legs_match_a6_map": True},
              "rebound_groups_excluded": [{"active_index": int(index), "reason": "expanded L02/L14/L25 native target"} for index in targets],
              "coverage": coverage, "top_groups_by_finite_incident_absolute_current": [row_record(int(index), groups, count, absolute, imbalance) for index in current_rank[:20]],
              "top_groups_by_finite_incident_imbalance": [row_record(int(index), groups, count, absolute, imbalance) for index in imbalance_rank[:20]],
              "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(60,4)", "cooperative_checks": True},
              "scope": "Accepted stored finite-current throughput/imbalance ranked by cached native layer/net/source-component identity labels only. Cached historical current values are not read or used. This is neither a cut, KCL closure, full return, magnetic action, nor an error bound; no SPD/SQLite, old field/potential, or new current calculation is used."}
    recon._atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({"status": result["status"], "artifact": result["artifact"], "coverage": coverage, "budget": result["budget"]}, sort_keys=True))


def self_check() -> None:
    verify_pins()
    assert NATIVE_POTENTIAL_COUNT == 756_889
    print(f"{PROGRAM} v{VERSION}: cached native-group ranking SELF_CHECK PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.output is None:
        parser.error("--output required")
    else:
        run(args.output)
