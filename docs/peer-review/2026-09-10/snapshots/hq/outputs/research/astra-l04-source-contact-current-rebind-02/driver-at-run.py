"""Qualify the saved L04 current rebind after its post-write print-only failure."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
sys.path.insert(0, str(ROOT / "outputs" / "research-runtime"))
import reconstruct_astra_native_loaded_field as recon

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
ATTEMPT = R / "astra-l04-source-contact-current-rebind-01"
PINS = {
    "attempt_driver": (ATTEMPT / "driver-at-run.py", "e881e1194203f1201694ad999fac8081be85d077856e39e1a8d92bbc70905ee3"),
    "attempt_result": (ATTEMPT / "result.json", "41e909596444997160c2c3cc753a63b9d450ac8ceca4edec45c8062becb67fac"),
    "attempt_artifact": (ATTEMPT / "l04-source-contact-current-rebind.npz", "2cf4dcb6d97a31a01d818bbd600906a3f5f1dbcec8f0a7d0b3105f22b23635f7"),
    "attempt_failure": (ATTEMPT / "failure.json", "43440901ca22486df2bd565dd40bb67a26022662f2461412c7c328c590e91084"),
    "ranking_result": (R / "astra-unexpanded-native-surface-group-ranking-01/result.json", "558503143f8556535a29df47982bbdaa009fd9b930a927657dc4ac29d67b1175"),
    "ranking_artifact": (R / "astra-unexpanded-native-surface-group-ranking-01/unexpanded-native-surface-group-ranking.npz", "5ecb3515ceb502919e2b698fec0ce69e63c0523ac805cff2e8da876ef720c737"),
    "budget_helper": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def verify_pins() -> dict:
    values = {}
    for name, (path, expected) in PINS.items():
        require(path.is_file() and sha(path) == expected, f"pinned {name}")
        values[name] = receipt(path)
    return values


def pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def run(output: Path) -> None:
    require(not output.exists(), "output already exists")
    budget = recon._Budget.create(60, 4)
    inputs = verify_pins()
    prior = json.loads(PINS["attempt_result"][0].read_bytes())
    failure = json.loads(PINS["attempt_failure"][0].read_bytes())
    ranking = json.loads(PINS["ranking_result"][0].read_bytes())
    require(prior["status"] == "COMPLETED_L04_SAVED_SOURCE_CONTACT_CURRENT_REBIND"
            and prior["driver"]["sha256"] == PINS["attempt_driver"][1]
            and prior["artifact"]["sha256"] == PINS["attempt_artifact"][1], "attempt completed-result contract")
    require(failure["status"] == "STOP_L04_SAVED_SOURCE_CONTACT_CURRENT_REBIND"
            and failure["error_type"] == "KeyError" and failure["error"] == "'runtime'"
            and failure["driver"]["sha256"] == PINS["attempt_driver"][1], "attempt print-only failure contract")
    require(ranking["status"] == "COMPLETED_ACCEPTED_UNEXPANDED_NATIVE_SURFACE_GROUP_RANKING", "ranking status")
    with np.load(PINS["attempt_artifact"][0], allow_pickle=False) as archive:
        category = np.asarray(archive["category"], dtype=np.int8)
        active = np.asarray(archive["active_finite_index"], dtype=np.int64)
        original = np.asarray(archive["original_finite_index"], dtype=np.int64)
        parent = np.asarray(archive["parent_final_active_current_row"], dtype=np.int64)
        native = np.asarray(archive["parent_native_active_current_row"], dtype=np.int64)
        split = np.asarray(archive["parent_split_leg"], dtype=np.int8)
        junction = np.asarray(archive["junction_array_ordinal"], dtype=np.int64)
        side = np.asarray(archive["original_l04_target_side"], dtype=np.int8)
        physical_start = np.asarray(archive["l04_endpoint_is_start"], dtype=np.bool_)
        current = np.asarray(archive["parent_current_first_to_second_a"], dtype=np.complex128)
        unique_parent = np.asarray(archive["unique_parent_final_active_current_row"], dtype=np.int64)
        parent_inverse = np.asarray(archive["record_parent_unique_index"], dtype=np.int64)
        via_ids = json.loads(archive["source_via_ids_json_utf8"].tobytes())
        source_hashes = json.loads(archive["source_record_sha256_json_utf8"].tobytes())
    arrays = (category, active, original, parent, native, split, junction, side, physical_start, current, parent_inverse)
    require(all(value.shape == (76_166,) for value in arrays), "record array shapes")
    require(np.bincount(category).tolist() == [76_136, 10, 20]
            and len(unique_parent) == 76_156 and len(np.unique(unique_parent)) == 76_156, "record and parent counts")
    require(np.array_equal(original, active), "original/native ledger identity")
    ordinary, remap, children = category == 0, category == 1, category == 2
    require(np.all(native[ordinary] == active[ordinary]) and np.all(split[ordinary] == -1), "ordinary native-unsplit identity")
    require(np.all(parent[remap | children] == 1_692_369 + junction[remap | children])
            and np.all(native[remap | children] == active[remap | children])
            and np.all(split[remap | children] == 0), "exception junction identity")
    child_parent, child_inverse = np.unique(parent[children], return_inverse=True)
    require(len(child_parent) == 10 and np.array_equal(np.bincount(child_inverse), np.full(10, 2)), "hidden parent pairs")
    for value in child_parent:
        positions = np.flatnonzero(children & (parent == value))
        require(current[positions[0]] == current[positions[1]]
                and physical_start[positions[0]] != physical_start[positions[1]], "hidden pair current/orientation")
    require(len(via_ids) == len(source_hashes) == 76_166 and len({value.casefold() for value in via_ids}) == 76_166
            and all(isinstance(value, str) and len(value) == 64 for value in source_hashes), "source identity arrays")
    boundary = ordinary | remap
    outward = np.where(side[boundary] == 0, current[boundary], -current[boundary])
    absolute = float(np.abs(outward).sum())
    signed = complex(outward.sum())
    require(abs(absolute - float(prior["native_l04_boundary_current"]["absolute_incident_sum_a"])) < 1e-14
            and abs(signed - complex(*prior["native_l04_boundary_current"]["signed_outward_sum_a"])) < 1e-14,
            "attempt current summary replay")
    with np.load(PINS["ranking_artifact"][0], allow_pickle=False) as archive:
        positions = np.flatnonzero(np.asarray(archive["active_index"], dtype=np.int64) == 71_610)
        require(len(positions) == 1, "L04 ranking identity")
        index = int(positions[0])
        ranking_absolute = float(archive["finite_incident_absolute_current_a"][index])
        ranking_signed = complex(archive["finite_incident_imbalance_a"][index])
        ranking_count = int(archive["finite_incident_row_count"][index])
    # The two saved aggregations use different row orders, so compare well below any physical-current gate.
    require(ranking_count == 76_146 and abs(absolute - ranking_absolute) < 1e-11
            and abs(signed - ranking_signed) < 1e-12, "accepted ranking/current rebind agreement")
    budget.check("saved current-rebind qualification")
    output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    result = {
        "program": PROGRAM, "version": VERSION,
        "status": "QUALIFIED_L04_SAVED_SOURCE_CONTACT_CURRENT_REBIND",
        "driver": receipt(frozen), "inputs": inputs, "qualified_artifact": inputs["attempt_artifact"],
        "counts": {"records_by_category": [76_136, 10, 20], "unique_present_parent_rows": 76_156,
                   "native_boundary_rows": 76_146, "hidden_future_child_records_and_parents": [20, 10]},
        "native_l04_boundary_current": {"signed_outward_sum_a": pair(signed), "absolute_incident_sum_a": absolute,
                                        "half_absolute_incident_sum_a": 0.5 * absolute,
                                        "ranking_signed_sum_a": pair(ranking_signed), "ranking_absolute_incident_sum_a": ranking_absolute},
        "gates": {"ordinary_native_unsplit_identity": True, "exception_original_junction_identity": True,
                  "hidden_pair_same_parent_current_opposite_source_orientation": True,
                  "source_record_identity": True, "accepted_ranking_agreement": True,
                  "attempt_failure_occurred_after_result_and_artifact_write": True},
        "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(60,4)", "cooperative_checks": True},
        "scope": "Qualification of the preserved attempt01 artifact after a print-only KeyError. No branch current was recomputed and no field, SQLite, source parse, geometry, solve or magnetic action was used. Category-2 records describe two future series-child ownership records sharing one present parent current; they are not summed as port, cut or throughput current, and future coupled redistribution remains open.",
    }
    recon._atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({"status": result["status"], "counts": result["counts"], "native_l04_boundary_current": result["native_l04_boundary_current"], "budget": result["budget"]}, sort_keys=True))


def self_check() -> None:
    verify_pins()
    print(f"{PROGRAM} v{VERSION}: L04 saved current rebind qualification SELF_CHECK PASS")


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
        run(args.output.resolve())
