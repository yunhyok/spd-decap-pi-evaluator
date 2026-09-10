"""Prepare one no-LU L02/L14/L25 circuit coordinate and finite-branch map.

This helper composes the three already-qualified source replacements on one
native finite-edge list.  It deliberately does not build the combined sparse
operator, factor it, or solve a field.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import time

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
RESEARCH_RUNTIME = ROOT / "outputs/research-runtime"
if str(RESEARCH_RUNTIME) not in sys.path:
    sys.path.insert(0, str(RESEARCH_RUNTIME))
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
FREQUENCY_HZ = 1.0e6

NATIVE_SIZE = 756_889
L14_SIZE = 903_945
L25_SIZE = 1_483_296
COMBINED_POTENTIAL_SIZE = 2_340_069
L25_CURRENT_SIZE = 604_031

L14_TARGET = 718_402
L02_TARGET = 349_710
L25_TARGET = 258_027

PINS = {
    "l14_helper": (
        ROOT / "tools/research/run_astra_l14_sheet_r_shadow.py",
        "ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803",
    ),
    "l02_frozen_driver": (
        R / "astra-l02-sheet-r-board-1mhz-01/driver-at-run.py",
        "8437a7eb0398fdef919704c33a6ba2e9498c810b79e04b5df533462c8ce55105",
    ),
    "l25_frozen_driver": (
        R / "astra-l25-rt0-board-1mhz-01/driver-at-run.py",
        "4a6bf51cf37c64e3e86c1ad982ad7f73cf77d1990a40f011381b4238707ab8ae",
    ),
    "raw": (
        R / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz",
        "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    ),
    "l14_result": (
        R / "astra-l14-sheet-r-shadow-01/result.json",
        "b0401b240cd855dc4beffbc2f8920c2f285022885c2940660601cc41aa2adca5",
    ),
    "l14_field": (
        R / "astra-l14-sheet-r-shadow-01/epsilon-1-field.npz",
        "15265003d3d93e3765cc22f4985c6a5edda77628fa57912b9676f4952dcc9f9a",
    ),
    "l14_inventory": (
        R / "astra-native-loaded-vtrip-field-02/l14-gc-projection-inventory.json",
        "4409cdccc3b3d488d8f3abdd2f1e8b09a488d3c18a5bace010c765a240bc397f",
    ),
    "l02_result": (
        R / "astra-l02-sheet-r-board-1mhz-01/result.json",
        "a491c951b7ec8c209fb194cb8dfa82d25a94230abefffbb97c94af55179b0e9b",
    ),
    "l02_field": (
        R / "astra-l02-sheet-r-board-1mhz-01/epsilon-1-field.npz",
        "40ff6697ba5a0b1f21c558d71c788b49a37d6ccc878dfd12e3aa7c3aafbac18a",
    ),
    "l02_binding": (
        R / "astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz",
        "61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020",
    ),
    "l02_binding_result": (
        R / "astra-l02-circuit-contact-binding-01/result.json",
        "1ef00f9c88553faf71f6cd87b4899c23d4a1be598a18a08c19b8776148690efc",
    ),
    "l25_result": (
        R / "astra-l25-rt0-board-1mhz-01/result.json",
        "61a48c10c054e65accc32203429d6b1c49b215693e12d439493a106d7c9e1752",
    ),
    "l25_field": (
        R / "astra-l25-rt0-board-1mhz-01/field.npz",
        "be2a3515dca426a45a5fe8c0eb696e3aa59544ee32e2d93971917e19c32b5ce1",
    ),
    "l25_binding": (
        R / "astra-l25-dual-cell-source-binding-01/native-source-binding.npz",
        "c9d41f1e2b870dd854affa1db2c31bb86f24c472866fcd2125f313cb29981c6a",
    ),
    "l25_binding_result": (
        R / "astra-l25-dual-cell-source-binding-01/result.json",
        "874f36da7dc906171d7761510902432752a3164b4ec055835064e389c4d427e6",
    ),
    "l25_inventory": (
        R / "astra-l25-source-sheet-01/l25-gc-projection-inventory.json",
        "703e8cdf9988805a2fb05a40ff659bc9890ba999e2eef00c741a112aa5976a77",
    ),
    "l14_mass_receipt": (
        R / "astra-l14-gc-mass-05/receipt.json",
        "dd41a5a2772a280d98a73943eb276b94d3eeadd502c981f328eb7548cfb5cdeb",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def array_sha256(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def file_receipt(path: Path) -> dict:
    return {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    temporary.replace(path)


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as handle:
        np.savez_compressed(handle, **arrays)
    temporary.replace(path)


def packed_json(archive, key: str):
    value = np.asarray(archive[key])
    require(value.dtype == np.uint8 and value.ndim == 1, f"{key} is not packed UTF-8 JSON")
    return json.loads(value.tobytes().decode("utf-8"))


class LedgerBudget:
    """Small event sink satisfying the frozen L14 assembly API; no solver budget."""

    def __init__(self, path: Path):
        self.path = path
        self.started = time.perf_counter()
        self.runtime_s = 120.0

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def emit(self, event: str, **payload) -> None:
        record = {"event": event, "elapsed_s": self.elapsed(), **payload}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")


def load_helper(path: Path):
    spec = importlib.util.spec_from_file_location("astra_combined_l14", path)
    require(spec is not None and spec.loader is not None, "cannot load pinned L14 helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collapse_to_native(indices: np.ndarray) -> np.ndarray:
    result = np.asarray(indices, dtype=np.int64).copy()
    l14 = (result >= NATIVE_SIZE) & (result < L14_SIZE)
    l25 = (result >= L14_SIZE) & (result < L25_SIZE)
    l02 = (result >= L25_SIZE) & (result < COMBINED_POTENTIAL_SIZE)
    result[l14] = L14_TARGET
    result[l25] = L25_TARGET
    result[l02] = L02_TARGET
    return result


def run(output: Path) -> dict:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    driver = output / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    inputs: dict[str, dict] = {}
    documents: dict[str, dict] = {}
    for name, (path, expected) in PINS.items():
        actual = sha256_file(path)
        require(actual == expected, f"{name} SHA-256 differs")
        inputs[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
        if path.suffix == ".json":
            documents[name] = json.loads(path.read_text(encoding="utf-8"))

    require(documents["l14_result"].get("status") == "COMPLETED_CONDITIONAL_L14_SHEET_R_SHADOW",
            "saved L14 result status differs")
    require(documents["l02_result"].get("status") == "COMPLETED_CONDITIONAL_L02_SHEET_R_SHADOW",
            "saved L02 result status differs")
    require(documents["l25_result"].get("status") == "COMPLETED_CONDITIONAL_L14_P1_L25_RT0_BOARD_1MHZ",
            "saved L25 result status differs")
    require(documents["l02_result"]["point"]["field"]["sha256"] == PINS["l02_field"][1],
            "L02 result-to-field binding differs")
    require(documents["l14_result"]["points"][0]["field"]["sha256"] == PINS["l14_field"][1],
            "L14 result-to-field binding differs")
    require(documents["l25_result"]["field"]["sha256"] == PINS["l25_field"][1],
            "L25 result-to-field binding differs")
    require(documents["l02_binding_result"]["output"]["sha256"] == PINS["l02_binding"][1],
            "L02 result-to-binding relation differs")
    require(documents["l25_binding_result"]["output"]["sha256"] == PINS["l25_binding"][1],
            "L25 result-to-binding relation differs")

    l14_module = load_helper(PINS["l14_helper"][0])
    assembly_dir = output / "l14-return-assembly"
    assembly_dir.mkdir()
    l14_assembly = l14_module.run(
        SimpleNamespace(
            mass_receipt=PINS["l14_mass_receipt"][0],
            mass_receipt_sha256=PINS["l14_mass_receipt"][1],
            output=assembly_dir,
            return_assembly=True,
        ),
        LedgerBudget(output / "progress.jsonl"),
    )
    l14_first = np.asarray(l14_assembly["finite_first"], dtype=np.int64).copy()
    l14_second = np.asarray(l14_assembly["finite_second"], dtype=np.int64).copy()
    l14_sheet_active = np.asarray(l14_assembly["sheet_active"], dtype=np.int64).copy()
    l14_contract = dict(l14_assembly["contract"])
    port = {
        "gauge_active_index": int(l14_assembly["gauge"]),
        "positive_active_index": int(l14_assembly["positive"]),
        "negative_active_index": int(l14_assembly["negative"]),
    }
    del l14_assembly
    gc.collect()

    with (np.load(PINS["raw"][0], allow_pickle=False) as raw,
          np.load(PINS["l14_field"][0], allow_pickle=False) as l14_field,
          np.load(PINS["l02_field"][0], allow_pickle=False) as l02_field,
          np.load(PINS["l25_field"][0], allow_pickle=False) as l25_field,
          np.load(PINS["l02_binding"][0], allow_pickle=False) as l02_binding,
          np.load(PINS["l25_binding"][0], allow_pickle=False) as l25_binding):
        raw_first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
        raw_second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
        count = np.asarray(raw["finite_count"], dtype=np.float64)
        resistance = np.asarray(raw["finite_resistance_ohm_per_via"], dtype=np.float64)
        inductance = np.asarray(raw["finite_inductance_h_per_via"], dtype=np.float64)
        original = np.asarray(raw["finite_active_original_indices"], dtype=np.int64)
        require(raw_first.shape == raw_second.shape == count.shape == resistance.shape == inductance.shape,
                "native finite arrays have different shapes")
        require(l14_first.shape == l14_second.shape == raw_first.shape,
                "returned L14 finite arrays have wrong shape")
        native_admittance = count / (resistance + 2j * np.pi * FREQUENCY_HZ * inductance)
        require(np.all(np.isfinite(native_admittance)) and np.all(native_admittance.real > 0),
                "native finite admittance is invalid")

        expected_l14_active = np.r_[L14_TARGET, np.arange(NATIVE_SIZE, L14_SIZE, dtype=np.int64)]
        require(np.array_equal(l14_sheet_active, expected_l14_active)
                and np.array_equal(l14_field["sheet_active_indices"], expected_l14_active)
                and l14_field["active_voltage"].shape == (L14_SIZE,),
                "L14 coordinate map or saved field differs")
        l25_potential_active = np.asarray(l25_field["l25_potential_active_indices"], dtype=np.int64)
        expected_l25_active = np.r_[L25_TARGET, np.arange(L14_SIZE, L25_SIZE, dtype=np.int64)]
        require(np.array_equal(l25_potential_active, expected_l25_active)
                and np.array_equal(l25_field["l14_sheet_active_indices"], expected_l14_active)
                and l25_field["active_voltage_v"].shape == (L25_SIZE,)
                and l25_field["l25_branch_current_a"].shape == (L25_CURRENT_SIZE,),
                "L25 coordinate map or saved field differs")
        saved_l02_active = np.asarray(l02_field["sheet_active_indices"], dtype=np.int64)
        expected_saved_l02_active = np.r_[L02_TARGET,
            np.arange(NATIVE_SIZE, int(l02_field["active_voltage"].shape[0]), dtype=np.int64)]
        require(saved_l02_active.shape == (856_774,)
                and np.array_equal(saved_l02_active, expected_saved_l02_active)
                and l02_field["active_voltage"].shape == (1_613_662,),
                "saved L02 coordinate map differs")
        combined_l02_active = np.r_[L02_TARGET,
            np.arange(L25_SIZE, COMBINED_POTENTIAL_SIZE, dtype=np.int64)]
        require(combined_l02_active.shape == saved_l02_active.shape,
                "combined L02 coordinate count differs")
        l02_contact_active = combined_l02_active[:38_856]

        l14_rows = np.flatnonzero((raw_first == L14_TARGET) ^ (raw_second == L14_TARGET))
        require(len(l14_rows) == l14_contract["finite_endpoints_relocated"] == 2_110
                and not np.any((raw_first == L14_TARGET) & (raw_second == L14_TARGET)),
                "L14 native finite incidence differs")
        l14_first_target = raw_first[l14_rows] == L14_TARGET
        require(np.array_equal(l14_second[l14_rows[l14_first_target]],
                               raw_second[l14_rows[l14_first_target]])
                and np.array_equal(l14_first[l14_rows[~l14_first_target]],
                                   raw_first[l14_rows[~l14_first_target]])
                and np.all(np.isin(l14_first[l14_rows[l14_first_target]], l14_sheet_active[:1_660]))
                and np.all(np.isin(l14_second[l14_rows[~l14_first_target]], l14_sheet_active[:1_660])),
                "L14 relocation changes a non-target endpoint or misses an electrode")
        untouched_l14 = np.ones(len(raw_first), dtype=bool)
        untouched_l14[l14_rows] = False
        require(np.array_equal(l14_first[untouched_l14], raw_first[untouched_l14])
                and np.array_equal(l14_second[untouched_l14], raw_second[untouched_l14]),
                "L14 helper changes a nonincident finite row")

        l02_rows = np.asarray(l02_binding["active_finite_index"], dtype=np.int64)
        l02_incidence = np.flatnonzero((raw_first == L02_TARGET) ^ (raw_second == L02_TARGET))
        require(l02_rows.shape == (76_139,) and len(np.unique(l02_rows)) == len(l02_rows)
                and np.array_equal(np.sort(l02_rows), l02_incidence),
                "L02 native finite row coverage differs")
        for saved_key, source in (("original_finite_index", original),
                                  ("first_active_index", raw_first),
                                  ("second_active_index", raw_second),
                                  ("native_count", count),
                                  ("resistance_ohm", resistance),
                                  ("inductance_h", inductance)):
            require(np.array_equal(l02_binding[saved_key], source[l02_rows]),
                    f"L02 binding {saved_key} differs from native")
        l02_ordinal = np.asarray(l02_binding["native_contact_ordinal"], dtype=np.int64)
        l02_side = np.asarray(l02_binding["target_side"], dtype=np.int8)
        require(l02_ordinal.shape == l02_side.shape == l02_rows.shape
                and np.all((l02_ordinal >= 0) & (l02_ordinal < 38_856))
                and len(np.unique(l02_ordinal)) == 38_836,
                "L02 native contact mapping differs")

        l25_rows = np.asarray(l25_binding["native_active_finite_index"], dtype=np.int64)
        l25_incidence = np.flatnonzero((raw_first == L25_TARGET) ^ (raw_second == L25_TARGET))
        l25_ordinal = np.asarray(l25_binding["l25_local_electrode_potential_index"], dtype=np.int64) - 539_641
        l25_first_target = np.asarray(l25_binding["target_is_native_first_endpoint"], dtype=bool)
        require(l25_rows.shape == l25_ordinal.shape == l25_first_target.shape == (350,)
                and len(np.unique(l25_rows)) == 350
                and np.array_equal(np.sort(l25_rows), l25_incidence)
                and np.all((l25_ordinal >= 0) & (l25_ordinal < 175))
                and np.array_equal(np.bincount(l25_ordinal, minlength=175), np.full(175, 2)),
                "L25 native finite/contact coverage differs")
        require(np.array_equal(raw_first[l25_rows] == L25_TARGET, l25_first_target)
                and np.array_equal(np.where(l25_first_target, raw_second[l25_rows], raw_first[l25_rows]),
                                   l25_binding["native_other_active_index"]),
                "L25 native target orientation differs")
        for source_key, saved_key in (("finite_resistance_ohm_per_via", "native_resistance_ohm_per_via"),
                                      ("finite_inductance_h_per_via", "native_inductance_h_per_via"),
                                      ("finite_count", "native_parallel_count")):
            require(np.array_equal(raw[source_key][l25_rows], l25_binding[saved_key]),
                    f"L25 binding {saved_key} differs from native")

        junctions = packed_json(l02_binding, "junctions_json_utf8")
        require(len(junctions) == 20, "L02 composite junction count differs")
        composite_rows = np.asarray([row["replaced_active_finite_index"] for row in junctions], dtype=np.int64)
        composite_ordinal = np.asarray([row["contact_ordinal"] for row in junctions], dtype=np.int64)
        require(len(np.unique(composite_rows)) == len(np.unique(composite_ordinal)) == 20
                and not np.intersect1d(l02_rows, composite_rows).size
                and not np.intersect1d(np.unique(l02_ordinal), composite_ordinal).size
                and np.array_equal(np.unique(np.r_[l02_ordinal, composite_ordinal]),
                                   np.arange(38_856, dtype=np.int64)),
                "L02 native/composite contact partition differs")
        require(all([int(raw_first[i]), int(raw_second[i])] == row["original_active_endpoints"]
                    for i, row in zip(composite_rows, junctions, strict=True))
                and np.all(count[composite_rows] == 1.0),
                "L02 composite native endpoints/count differ")
        all_supports = np.asarray(l02_binding["contact_support_index"], dtype=np.int64)
        junction_supports = np.asarray([row["drill_support_index"] for row in junctions], dtype=np.int64)
        require(np.array_equal(all_supports[composite_ordinal], junction_supports),
                "L02 composite support mapping differs")
        leg_r = np.asarray([[row["first_leg_resistance_ohm"], row["second_leg_resistance_ohm"]]
                            for row in junctions], dtype=np.float64)
        leg_l = np.asarray([[row["first_leg_inductance_h"], row["second_leg_inductance_h"]]
                            for row in junctions], dtype=np.float64)
        require(np.all(leg_r > 0) and np.all(leg_l > 0)
                and np.allclose(leg_r.sum(axis=1), resistance[composite_rows], rtol=1e-13, atol=0)
                and np.allclose(leg_l.sum(axis=1), inductance[composite_rows], rtol=1e-13, atol=0),
                "L02 composite leg R/L sum differs")
        source_owners = [owner for row in junctions for owner in row["source_vias_in_native_path_order"]]
        require(len(source_owners) == len({row["via_id_fold"] for row in source_owners}) == 93,
                "L02 composite ordered owner set differs")
        for row, row_r, row_l in zip(junctions, leg_r, leg_l, strict=True):
            split = int(row["l02_split_after_source_via_count"])
            ordered = row["source_vias_in_native_path_order"]
            require(0 < split < len(ordered), "L02 composite owner split is invalid")
            for name, values in (("resistance_ohm", row_r), ("inductance_h", row_l)):
                sums = np.asarray((sum(owner[name] for owner in ordered[:split]),
                                   sum(owner[name] for owner in ordered[split:])))
                require(np.allclose(sums, values, rtol=1e-13, atol=0),
                        f"L02 composite ordered owner {name} differs")

        row_sets = {
            "l14": l14_rows,
            "l02_native": l02_rows,
            "l02_composite": composite_rows,
            "l25": l25_rows,
        }
        row_set_names = tuple(row_sets)
        row_intersections = {}
        for position, left_name in enumerate(row_set_names):
            for right_name in row_set_names[position + 1:]:
                overlap = np.intersect1d(row_sets[left_name], row_sets[right_name])
                row_intersections[f"{left_name}__{right_name}"] = int(len(overlap))
                require(not overlap.size, f"finite row ownership overlaps: {left_name}/{right_name}")

        first = l14_first.copy()
        second = l14_second.copy()
        l25_electrode_active = l25_potential_active[579_177 + l25_ordinal]
        first[l25_rows[l25_first_target]] = l25_electrode_active[l25_first_target]
        second[l25_rows[~l25_first_target]] = l25_electrode_active[~l25_first_target]
        l02_first_rows = l02_rows[l02_side == 0]
        l02_second_rows = l02_rows[l02_side == 1]
        require(np.all(first[l02_first_rows] == L02_TARGET)
                and np.all(second[l02_second_rows] == L02_TARGET)
                and len(l02_first_rows) + len(l02_second_rows) == len(l02_rows),
                "L02 target-side mapping differs after disjoint upstream relocation")
        first[l02_first_rows] = l02_contact_active[l02_ordinal[l02_side == 0]]
        second[l02_second_rows] = l02_contact_active[l02_ordinal[l02_side == 1]]
        require(np.array_equal(first[composite_rows], raw_first[composite_rows])
                and np.array_equal(second[composite_rows], raw_second[composite_rows]),
                "another transform changes an L02 composite row")

        keep = np.ones(len(raw_first), dtype=bool)
        keep[composite_rows] = False
        junction_active = l02_contact_active[composite_ordinal]
        final_first = np.r_[first[keep], first[composite_rows], junction_active]
        final_second = np.r_[second[keep], junction_active, second[composite_rows]]
        leg_y = 1.0 / (leg_r + 2j * np.pi * FREQUENCY_HZ * leg_l)
        final_y = np.r_[native_admittance[keep], leg_y[:, 0], leg_y[:, 1]]
        final_native_row = np.r_[np.flatnonzero(keep), composite_rows, composite_rows]
        final_split_leg = np.r_[np.full(np.count_nonzero(keep), -1, dtype=np.int8),
                                  np.zeros(20, dtype=np.int8), np.ones(20, dtype=np.int8)]
        require(len(final_y) == len(raw_first) + 20 == 1_692_409
                and final_first.shape == final_second.shape == final_y.shape == final_native_row.shape
                and np.all((final_first >= 0) & (final_first < COMBINED_POTENTIAL_SIZE))
                and np.all((final_second >= 0) & (final_second < COMBINED_POTENTIAL_SIZE))
                and np.all(final_first != final_second)
                and np.all(np.isfinite(final_y)) and np.all(final_y.real > 0),
                "combined finite branch arrays are invalid")

        collapsed_first = collapse_to_native(final_first)
        collapsed_second = collapse_to_native(final_second)
        expected_collapsed_first = np.r_[raw_first[keep], raw_first[composite_rows],
                                          np.full(20, L02_TARGET, dtype=np.int64)]
        expected_collapsed_second = np.r_[raw_second[keep],
                                           np.full(20, L02_TARGET, dtype=np.int64),
                                           raw_second[composite_rows]]
        expected_collapsed_y = np.r_[native_admittance[keep], leg_y[:, 0], leg_y[:, 1]]
        require(np.array_equal(collapsed_first, expected_collapsed_first)
                and np.array_equal(collapsed_second, expected_collapsed_second)
                and np.array_equal(final_y, expected_collapsed_y),
                "combined finite list does not collapse to the corrected native branch list")

        field_coordinate_hashes = {
            "l14_sheet_active_indices": array_sha256(l14_sheet_active),
            "l25_potential_active_indices": array_sha256(l25_potential_active),
            "l02_saved_sheet_active_indices": array_sha256(saved_l02_active),
            "l02_combined_sheet_active_indices": array_sha256(combined_l02_active),
        }

    l14_partials = {int(row["ordinal"]) for row in l14_contract["original_gc_partition"]}
    l02_partials = {int(documents["l02_result"]["assembly"]["gc_owner_partition"]["partial_ordinal"])}
    l25_partials = {int(row["partial_ordinal"]) for row in documents["l25_binding_result"]["gc_owners"]}
    require(l14_partials == {6, 7} and l02_partials == {0} and l25_partials == {13, 14},
            "source G/C partial ownership differs")
    require(not (l14_partials & l02_partials or l14_partials & l25_partials or l02_partials & l25_partials),
            "source G/C partial ownership overlaps")
    retained_partials = sorted(set(range(36)) - l14_partials - l02_partials - l25_partials)

    artifact = output / "combined-assembly-map.npz"
    atomic_npz(
        artifact,
        final_finite_first_active_index=final_first,
        final_finite_second_active_index=final_second,
        final_finite_admittance_s=final_y,
        final_finite_native_active_row=final_native_row,
        final_finite_split_leg=final_split_leg,
        l14_native_active_finite_rows=l14_rows,
        l02_native_active_finite_rows=l02_rows,
        l02_composite_active_finite_rows=composite_rows,
        l25_native_active_finite_rows=l25_rows,
        l14_sheet_active_indices=l14_sheet_active,
        l25_potential_active_indices=l25_potential_active,
        l02_saved_sheet_active_indices=saved_l02_active,
        l02_combined_sheet_active_indices=combined_l02_active,
        l02_contact_active_indices=l02_contact_active,
        l25_electrode_active_indices=l25_potential_active[579_177:579_352],
        gc_l02_source_partial_ordinals=np.asarray(sorted(l02_partials), dtype=np.int8),
        gc_l14_source_partial_ordinals=np.asarray(sorted(l14_partials), dtype=np.int8),
        gc_l25_source_partial_ordinals=np.asarray(sorted(l25_partials), dtype=np.int8),
        gc_retained_native_partial_ordinals=np.asarray(retained_partials, dtype=np.int8),
        native_target_active_indices=np.asarray((L02_TARGET, L14_TARGET, L25_TARGET), dtype=np.int64),
        source_port_active_indices=np.asarray((port["positive_active_index"], port["negative_active_index"]), dtype=np.int64),
        gauge_active_index=np.asarray((port["gauge_active_index"],), dtype=np.int64),
    )

    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_L02_L14_L25_COMBINED_ASSEMBLY_MAP_NO_LU",
        "frequency_hz": FREQUENCY_HZ,
        "inputs": inputs,
        "driver": file_receipt(driver),
        "output": file_receipt(artifact),
        "coordinate_contract": {
            "native_potential_count": NATIVE_SIZE,
            "l14_sheet_coordinate_count_including_native_target": len(l14_sheet_active),
            "l14_new_potential_range_half_open": [NATIVE_SIZE, L14_SIZE],
            "l25_coordinate_count_including_native_target": len(l25_potential_active),
            "l25_new_potential_range_half_open": [L14_SIZE, L25_SIZE],
            "l02_sheet_coordinate_count_including_native_target": len(combined_l02_active),
            "l02_new_potential_range_half_open": [L25_SIZE, COMBINED_POTENTIAL_SIZE],
            "combined_potential_count": COMBINED_POTENTIAL_SIZE,
            "l25_current_count": L25_CURRENT_SIZE,
            "combined_mixed_unknown_count": COMBINED_POTENTIAL_SIZE + L25_CURRENT_SIZE,
            "gauge_eliminated_unknown_count": COMBINED_POTENTIAL_SIZE + L25_CURRENT_SIZE - 1,
            "source_targets_remain_native_coordinates": {
                "l02": L02_TARGET, "l14": L14_TARGET, "l25": L25_TARGET,
            },
            "saved_coordinate_array_sha256": field_coordinate_hashes,
        },
        "finite_branch_contract": {
            "native_branch_count": int(len(raw_first)),
            "final_branch_count": int(len(final_y)),
            "l14_endpoint_rows": int(len(l14_rows)),
            "l14_numerically_changed_rows": int(np.count_nonzero(
                (l14_first != raw_first) | (l14_second != raw_second))),
            "l02_native_endpoint_rows": int(len(l02_rows)),
            "l02_composite_rows_removed": int(len(composite_rows)),
            "l02_positive_rl_legs_added": 40,
            "l25_endpoint_rows": int(len(l25_rows)),
            "pairwise_row_intersection_counts": row_intersections,
            "single_final_branch_list": True,
            "all_endpoints_in_range": True,
            "all_branches_nonself": True,
            "all_admittances_finite_positive_real": True,
            "collapse_matches_corrected_native_branch_list_exactly": True,
            "branch_provenance": "Each final row stores its native active-finite row; split_leg=-1 is unsplit,0/1 are the ordered L02 composite legs.",
        },
        "owner_contract": {
            "l02_distributed_gc_partial_ordinals": sorted(l02_partials),
            "l14_distributed_gc_partial_ordinals": sorted(l14_partials),
            "l25_distributed_gc_partial_ordinals": sorted(l25_partials),
            "retained_native_gc_partial_ordinals": retained_partials,
            "owner_partial_sets_pairwise_disjoint": True,
            "finite_row_ownership_is_distinct_from_gc_matrix_support": True,
        },
        "port_contract": port,
        "elapsed_s": time.perf_counter() - started,
        "scope": "Pinned coordinate, owner and single finite-branch composition for the existing conditional L02, L14 and L25 assemblies. No sparse combined matrix, LU, field, FMM, new G/C projection or PowerSI comparison is computed.",
        "next_assembly": "Place the existing L14/L02 nodal sheet blocks and L25 R/B blocks on these coordinates, distribute partials0/6/7/13/14 exactly once, and construct finite branch_action/Laplacian once from the saved final list before any guarded solve.",
        "limitations": [
            "The L02 and L14 contact contractions and the L25 RT0/P0 sheet remain their previously qualified conditional approximations.",
            "This artifact proves disjoint source ownership and executable coordinate/branch composition; it does not certify the combined physical field or explain the remaining PowerSI difference.",
            "The full conditional G current/charge and magnetic coupling are separate pending operators; no return path is clamped, removed or newly grounded here.",
        ],
    }
    atomic_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=R / "astra-l02-l14-l25-combined-assembly-map-02")
    args = parser.parse_args()
    try:
        result = run(args.output.resolve())
        print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"],
                          "output": result["output"]}, sort_keys=True, allow_nan=False))
    except Exception as error:
        if args.output.exists():
            failure = {"program": PROGRAM, "version": VERSION,
                       "status": "STOP_L02_L14_L25_COMBINED_ASSEMBLY_MAP",
                       "error": f"{type(error).__name__}: {error}"}
            failure_path = args.output / "failure.json"
            if not failure_path.exists():
                atomic_json(failure_path, failure)
        raise


if __name__ == "__main__":
    main()
