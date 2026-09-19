"""Release-disabled adapter for future accepted hybrid port-return currents."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

import compare_astra_hybrid_board_1mhz as accepted


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
RUN_RELEASED = True
POTENTIAL_COUNT = 3_178_104
PORTS = (("power", 2699, 978), ("ground", 2656, 25_812))
COMPARATOR_SHA = "e9ce9ad63366586293364c80ec49bae77c1812c423811f06bce1c5c9a44dc1fa"
FUTURE_ACCEPTED_DRIVER_SHA = "91e792487f371d14c21e9ce6729f17b34b93386f9f1d0c599302b129d2d755a6"
PINS = {
    "comparator": (Path(accepted.__file__), COMPARATOR_SHA),
    "full_face_pack": (
        RESEARCH / "astra-l02-full-face-hybrid-operator-06" / "hybrid-operator-pack.npz",
        "01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c",
    ),
    "combined_map_result": (
        RESEARCH / "astra-l02-l14-l25-combined-assembly-map-02" / "result.json",
        "cdbb80f3412732f614c1d482a3cc8288c5c6134b5dfb1de2376711309039ae81",
    ),
    "combined_map": (
        RESEARCH / "astra-l02-l14-l25-combined-assembly-map-02" / "combined-assembly-map.npz",
        "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469",
    ),
    "native_selected_metadata": (
        RESEARCH / "astra-native-selected-first-via-map-02" / "selected-native-first-vias.npz",
        "aa8349094aae8a27c5f2f05cda41e69bb40171ff76911f3507b499673efe8500",
    ),
    "raw_finite_ledger": (
        RESEARCH / "astra-native-loaded-vtrip-field-02" / "raw-field-snapshot.npz",
        "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    ),
}
FAILED_RESULTS = (
    ("astra-l02-conditional-hybrid-block-lgmres-01", "458d8afca94ccbdd5227c7fffb1f7b6049848d6c677a2b194bd50c3d1a9c2fab"),
    ("astra-l02-conditional-hybrid-galerkin-lgmres-01", "3ad844ac4dd7530175acaa75daa0ab08071da089558441d524e4d8a1294fe4b7"),
    ("astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01", "08f0a9e115a6bdc27a4ebd86253b0575146f7bec6726d9ad5ea4b36006759ac6"),
    ("astra-l02-conditional-hybrid-release-basis-lgmres-01", "e77b5f7054d49ab8b2e4a7c388162c3da23fcf3acded6b50b399a121004bfc79"),
)
DEFAULT_SELF_CHECK_RECEIPT = RESEARCH / "astra-hybrid-return-currents-adapter-01" / "self-check.json"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def unique_join(rows: np.ndarray, selected: np.ndarray) -> np.ndarray:
    """Return the one row for each selected identity; reject duplicate/missing joins."""
    order = np.argsort(rows)
    sorted_rows = rows[order]
    low = np.searchsorted(sorted_rows, selected, side="left")
    high = np.searchsorted(sorted_rows, selected, side="right")
    if np.any(high - low != 1):
        raise ValueError("selected identity is duplicate or missing")
    result = order[low]
    if not np.array_equal(rows[result], selected):
        raise ValueError("selected identity lookup differs")
    return result


def verify_pins() -> None:
    for name, (path, digest) in PINS.items():
        if sha256(path) != digest:
            raise ValueError(f"{name} SHA-256 differs")


def decode_text_vector(array: np.ndarray, label: str) -> list[str]:
    value = json.loads(np.asarray(array, dtype=np.uint8).tobytes().decode("utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} text vector differs")
    return value


def final_link_ids(native_link_ids: np.ndarray, split_legs: np.ndarray) -> np.ndarray:
    return np.asarray(
        [f"{link}:final-split-leg={int(leg)}" for link, leg in zip(native_link_ids, split_legs)],
        dtype="U",
    )


def pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def collect(result: Path, result_sha256: str) -> tuple[dict, dict[str, np.ndarray]]:
    verify_pins()
    # The comparator runs every numerical/physical acceptance gate before it opens field.npz.
    actual, voltage = accepted.load_accepted_field(result, result_sha256)
    if voltage.shape != (POTENTIAL_COUNT,) or not np.all(np.isfinite(voltage)):
        raise ValueError("accepted hybrid voltage contract differs")

    with np.load(PINS["full_face_pack"][0], allow_pickle=False) as pack:
        first = np.asarray(pack["finite_first_active_index"], dtype=np.int64)
        second = np.asarray(pack["finite_second_active_index"], dtype=np.int64)
        admittance = np.asarray(pack["finite_admittance_s"], dtype=np.complex128)
        pack_native_rows = np.asarray(pack["final_finite_native_active_row"], dtype=np.int64)
        pack_split_legs = np.asarray(pack["final_finite_split_leg"], dtype=np.int8)
    with np.load(PINS["combined_map"][0], allow_pickle=False) as mapping:
        native_rows = np.asarray(mapping["final_finite_native_active_row"], dtype=np.int64)
        split_legs = np.asarray(mapping["final_finite_split_leg"], dtype=np.int8)
    if not (np.array_equal(native_rows, pack_native_rows) and np.array_equal(split_legs, pack_split_legs)):
        raise ValueError("pack and combined-map final metadata differ")
    if not (len(first) == len(second) == len(admittance) == len(native_rows) == len(split_legs) == 1_692_409):
        raise ValueError("full-face finite row count differs")
    if not (np.all(first >= 0) and np.all(second >= 0) and np.all(first < POTENTIAL_COUNT)
            and np.all(second < POTENTIAL_COUNT) and np.all(first != second)
            and np.all(np.isfinite(admittance)) and np.all(admittance.real > 0)):
        raise ValueError("full-face finite endpoint/admittance contract differs")

    role_parts, row_parts = [], []
    for role, port, expected_count in PORTS:
        rows = np.flatnonzero((first == port) ^ (second == port))
        if len(rows) != expected_count:
            raise ValueError(f"{role} incident finite count differs")
        row_parts.append(rows)
        role_parts.append(np.full(len(rows), role, dtype="U6"))
    final_rows = np.concatenate(row_parts)
    roles = np.concatenate(role_parts)
    selected_first, selected_second = first[final_rows], second[final_rows]
    selected_native = native_rows[final_rows]
    selected_legs = split_legs[final_rows]
    if len(np.unique(final_rows)) != 26_790 or len(np.unique(selected_native)) != 26_790:
        raise ValueError("port finite/native map is not one-to-one")
    if not (np.array_equal(np.unique(split_legs), np.array([-1, 0, 1], dtype=np.int8))
            and np.count_nonzero(split_legs == -1) == 1_692_369
            and np.count_nonzero(split_legs == 0) == 20
            and np.count_nonzero(split_legs == 1) == 20
            and np.count_nonzero(selected_legs != -1) == 20
            and np.all(selected_legs[selected_legs != -1] == 1)
            and np.all(roles[selected_legs != -1] == "ground")):
        raise ValueError("saved split-leg convention differs")
    ports = np.repeat([2699, 2656], [978, 25_812])
    signs = np.where(selected_first == ports, 1, -1).astype(np.int8)
    if not np.all((selected_first == ports) ^ (selected_second == ports)):
        raise ValueError("port sign contract differs")
    impedance = 1.0 / admittance[final_rows]
    branch_current = admittance[final_rows] * (voltage[selected_first] - voltage[selected_second])
    outward_current = signs * branch_current

    with np.load(PINS["native_selected_metadata"][0], allow_pickle=False) as native:
        selected_active = np.asarray(native["active_edge_index"], dtype=np.int64)
        selected_role = np.asarray(native["role"])
        selected_pin = np.asarray(native["pin_id"])
        selected_orientation = np.asarray(native["native_orientation_from_top"], dtype=np.int8)
        selected_top = np.asarray(native["native_top_row"], dtype=np.int64)
        selected_resistance = np.asarray(native["resistance_ohm"], dtype=np.float64)
        selected_inductance = np.asarray(native["inductance_h"], dtype=np.float64)
    if not (len(selected_active) == len(selected_role) == len(selected_pin) == len(selected_orientation) == len(selected_top) == 1_956):
        raise ValueError("native selected metadata count differs")
    selected_positions = unique_join(selected_native, selected_active)
    if not (np.array_equal(selected_native[selected_positions], selected_active)
            and np.array_equal(roles[selected_positions], selected_role)
            and np.array_equal(np.where(selected_orientation == 1, selected_first[selected_positions], selected_second[selected_positions]), selected_top)):
        raise ValueError("native selected metadata join differs")
    selected_series = selected_resistance + 2j * np.pi * 1e6 * selected_inductance
    if not (np.all(selected_legs[selected_positions] == -1)
            and np.all(np.abs(impedance[selected_positions] - selected_series) <= np.abs(selected_series) * 1e-13)):
        raise ValueError("native selected series impedance or split-leg contract differs")
    selected_row_by_final = np.full(len(final_rows), -1, dtype=np.int64)
    selected_row_by_final[selected_positions] = np.arange(len(selected_active), dtype=np.int64)
    pin_id = np.full(len(final_rows), "", dtype=selected_pin.dtype)
    pin_id[selected_positions] = selected_pin

    with np.load(PINS["raw_finite_ledger"][0], allow_pickle=False) as raw:
        original_indices = np.asarray(raw["finite_active_original_indices"], dtype=np.int64)[selected_native]
        raw_first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)[selected_native]
        raw_second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)[selected_native]
        original_first_global = np.asarray(raw["all_finite_first_global_reduced_indices"], dtype=np.int64)[original_indices]
        original_second_global = np.asarray(raw["all_finite_second_global_reduced_indices"], dtype=np.int64)[original_indices]
        if not (np.array_equal(raw_first == 2699, selected_first == 2699)
                and np.array_equal(raw_second == 2699, selected_second == 2699)
                and np.array_equal(raw_first == 2656, selected_first == 2656)
                and np.array_equal(raw_second == 2656, selected_second == 2656)):
            raise ValueError("original/native port incidence differs")
        native_link_vector = decode_text_vector(raw["all_finite_link_ids"], "native link IDs")
        owner_vector = decode_text_vector(raw["all_finite_link_owner_ids_json"], "owner IDs")
        original_first_node_vector = decode_text_vector(raw["all_finite_first_node_ids"], "original first node IDs")
        original_second_node_vector = decode_text_vector(raw["all_finite_second_node_ids"], "original second node IDs")
    if len(native_link_vector) != len(owner_vector):
        raise ValueError("raw link identity vectors differ")
    native_link_id = np.asarray([native_link_vector[index] for index in original_indices], dtype="U")
    owner_ids_json = np.asarray([owner_vector[index] for index in original_indices], dtype="U")
    original_first_node_id = np.asarray([original_first_node_vector[index] for index in original_indices], dtype="U")
    original_second_node_id = np.asarray([original_second_node_vector[index] for index in original_indices], dtype="U")
    if len(np.unique(original_indices)) != len(original_indices):
        raise ValueError("original finite index join is duplicate")

    arrays = {
        "original_finite_index": original_indices,
        "final_active_current_row": final_rows,
        "native_active_current_row": selected_native,
        "role": roles,
        "pin_id": pin_id,
        "native_selected_metadata_row": selected_row_by_final,
        "owner_ids_json": owner_ids_json,
        "source_link_id": native_link_id,
        "native_link_id": native_link_id,
        "final_link_id": final_link_ids(native_link_id, selected_legs),
        "original_first_node_id": original_first_node_id,
        "original_second_node_id": original_second_node_id,
        "original_first_global_reduced_index": original_first_global,
        "original_second_global_reduced_index": original_second_global,
        "first_active_node": selected_first,
        "second_active_node": selected_second,
        "port_outward_sign": signs,
        "final_split_leg": selected_legs,
        "impedance_ohm": impedance,
        "finite_current_first_to_second_a": branch_current,
        "port_outward_signed_current_a": outward_current,
    }
    counts = {
        "power_incident_original_finite_edges": int(np.count_nonzero(roles == "power")),
        "ground_incident_original_finite_edges": int(np.count_nonzero(roles == "ground")),
        "all_incident_original_finite_edges": int(len(final_rows)),
        "unique_final_active_current_rows": int(len(np.unique(final_rows))),
        "unique_native_active_current_rows": int(len(np.unique(selected_native))),
        "unique_original_finite_indices": int(len(np.unique(original_indices))),
        "native_selected_metadata_rows": int(len(selected_active)),
        "split_leg_rows": int(np.count_nonzero(selected_legs != -1)),
    }
    role_totals = {}
    for role, _port, _expected_count in PORTS:
        mask = roles == role
        selected_mask = mask & (selected_row_by_final >= 0)
        outside_mask = mask & ~selected_mask
        target = 1.0 if role == "power" else -1.0
        total = outward_current[mask].sum()
        if abs(total - target) >= 1e-7:
            raise ValueError(f"{role} outward current closure differs")
        role_totals[role] = {
            "outward_current_sum_a": pair(total),
            "target_outward_current_a": pair(complex(target)),
            "selected_count": int(np.count_nonzero(selected_mask)),
            "outside_selected_count": int(np.count_nonzero(outside_mask)),
            "selected_outward_current_sum_a": pair(outward_current[selected_mask].sum()),
            "outside_selected_outward_current_sum_a": pair(outward_current[outside_mask].sum()),
        }
    arrays["counts_json_utf8"] = np.frombuffer(json.dumps(counts, sort_keys=True).encode("utf-8"), dtype=np.uint8)
    arrays["role_totals_json_utf8"] = np.frombuffer(json.dumps(role_totals, sort_keys=True).encode("utf-8"), dtype=np.uint8)
    return actual, arrays


def run(result: Path, result_sha256: str, output: Path) -> dict:
    if not RUN_RELEASED:
        raise RuntimeError("RUN_RELEASED=False; a future accepted result is required before measurement execution")
    if output.exists():
        raise ValueError("output already exists")
    actual, arrays = collect(result.resolve(), result_sha256)
    output.mkdir(parents=True)
    frozen_driver = output / "driver-at-run.py"
    frozen_driver.write_bytes(Path(__file__).read_bytes())
    artifact = output / "hybrid-port-return-currents.npz"
    np.savez_compressed(artifact, **arrays)
    counts = json.loads(arrays["counts_json_utf8"].tobytes())
    role_totals = json.loads(arrays["role_totals_json_utf8"].tobytes())
    result_document = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "COMPLETED_ACCEPTED_HYBRID_PORT_RETURN_CURRENTS",
        "run_released": RUN_RELEASED,
        "frequency_hz": 1e6,
        "inputs": {name: receipt(path) for name, (path, _digest) in PINS.items()},
        "accepted_result": {"path": str(result.resolve()), "sha256": result_sha256},
        "accepted_field_driver_sha256": FUTURE_ACCEPTED_DRIVER_SHA,
        "field": actual["field"],
        "driver": receipt(frozen_driver),
        "artifact": receipt(artifact),
        "counts": counts,
        "role_outward_totals": role_totals,
        "joins": {
            "full_face_final_to_native_one_to_one": True,
            "native_to_original_finite_one_to_one": True,
            "native_selected_metadata_duplicate_or_missing_rejected": True,
            "port_incident_duplicate_or_missing_rejected": True,
            "native_selected_series_impedance_1e-13": True,
            "saved_split_leg_convention": True,
            "role_outward_current_closure_1e-7": True,
        },
        "link_identity_contract": {
            "owner_ids_json": "immutable raw finite-ledger owner IDs",
            "source_link_id": "immutable raw finite-ledger link ID",
            "native_link_id": "same raw finite-ledger ID; no separate native-link ID exists in the pinned schema",
            "final_link_id": "derived native link ID plus the saved final split-leg identity",
        },
        "scope": "Future accepted-field readback only. Currents are recomputed from the accepted hybrid voltage and pack06 finite admittances; no historical current array is loaded or stored, and no solve, reference comparison, magnetic operator, calibration, or accuracy claim is made.",
    }
    (output / "result.json").write_text(json.dumps(result_document, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return result_document


def self_check() -> dict:
    verify_pins()
    with np.load(PINS["full_face_pack"][0], allow_pickle=False) as pack:
        pack_native_rows = np.asarray(pack["final_finite_native_active_row"], dtype=np.int64)
        pack_split_legs = np.asarray(pack["final_finite_split_leg"], dtype=np.int8)
    with np.load(PINS["combined_map"][0], allow_pickle=False) as mapping:
        map_native_rows = np.asarray(mapping["final_finite_native_active_row"], dtype=np.int64)
        map_split_legs = np.asarray(mapping["final_finite_split_leg"], dtype=np.int8)
    if not (len(map_native_rows) == len(map_split_legs) == 1_692_409
            and np.array_equal(map_native_rows, pack_native_rows)
            and np.array_equal(map_split_legs, pack_split_legs)):
        raise AssertionError("combined-map final metadata differs from pack copy")
    if unique_join(np.array([7, 3, 8]), np.array([8, 7])).tolist() != [2, 0]:
        raise AssertionError("unique join ordering differs")
    for rows, selected in ((np.array([3, 3]), np.array([3])), (np.array([3, 7]), np.array([8]))):
        try:
            unique_join(rows, selected)
        except ValueError:
            pass
        else:
            raise AssertionError("duplicate or missing join was accepted")
    rejected = []
    for name, digest in FAILED_RESULTS:
        result = RESEARCH / name / "unvalidated-field.json"
        try:
            accepted.load_accepted_field(result, digest)
        except ValueError:
            rejected.append(name)
        else:
            raise AssertionError(f"failed conditional result was accepted: {name}")
    if len(rejected) != len(FAILED_RESULTS):
        raise AssertionError("real failed conditional rejection count differs")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_ACCEPTED_HYBRID_RETURN_ADAPTER_DISABLED",
        "run_released": RUN_RELEASED,
        "adapter_sha256": sha256(Path(__file__)),
        "comparator_sha256": COMPARATOR_SHA,
        "accepted_field_driver_sha256": FUTURE_ACCEPTED_DRIVER_SHA,
        "combined_map_final_metadata_identity_rows": int(len(map_native_rows)),
        "rejected_before_field_or_reference_access": rejected,
        "join_gates": {"duplicate_rejected": True, "missing_rejected": True},
        "future_contract": {"voltage_length": POTENTIAL_COUNT, "power_edges": 978, "ground_edges": 25_812},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION}: {__doc__}")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--self-check-receipt", type=Path, default=DEFAULT_SELF_CHECK_RECEIPT)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--result-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        report = self_check()
        args.self_check_receipt.parent.mkdir(parents=True, exist_ok=True)
        args.self_check_receipt.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
        return
    if not (args.result and args.result_sha256 and args.output):
        parser.error("--result, --result-sha256, and --output are required")
    print(json.dumps(run(args.result, args.result_sha256, args.output), sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
