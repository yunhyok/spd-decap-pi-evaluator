"""Independently replay the saved no-LU L02/L14/L25 assembly map."""
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
RUNTIME = ROOT / "outputs/research-runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
FREQUENCY_HZ = 1.0e6
NATIVE_SIZE, L14_SIZE, L25_SIZE, COMBINED_SIZE = 756_889, 903_945, 1_483_296, 2_340_069
L02_TARGET, L14_TARGET, L25_TARGET = 349_710, 718_402, 258_027

PINS = {
    "producer": (ROOT / "tools/research/prepare_astra_l02_l14_l25_combined_assembly_map.py",
                 "2e28deb06c9807d60ffc11af3d2248c7c603128a9d5004ca5bb5aee8a14b1a43"),
    "result": (R / "astra-l02-l14-l25-combined-assembly-map-02/result.json",
               "cdbb80f3412732f614c1d482a3cc8288c5c6134b5dfb1de2376711309039ae81"),
    "map": (R / "astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz",
            "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469"),
    "raw": (R / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz",
            "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
    "l14": (ROOT / "tools/research/run_astra_l14_sheet_r_shadow.py",
            "ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803"),
    "mass": (R / "astra-l14-gc-mass-05/receipt.json",
             "dd41a5a2772a280d98a73943eb276b94d3eeadd502c981f328eb7548cfb5cdeb"),
    "l02_binding": (R / "astra-l02-circuit-contact-binding-01/circuit-contact-binding.npz",
                    "61ed51f68ae0cc39cdd5ee07e919e105fd15aee5a6b4c8374b063f7d72221020"),
    "l25_binding": (R / "astra-l25-dual-cell-source-binding-01/native-source-binding.npz",
                    "c9d41f1e2b870dd854affa1db2c31bb86f24c472866fcd2125f313cb29981c6a"),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path), "sha256": sha(path), "size_bytes": path.stat().st_size}


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    temporary.replace(path)


class Budget:
    def __init__(self, path: Path):
        self.path, self.started, self.runtime_s = path, time.perf_counter(), 120.0

    def elapsed(self):
        return time.perf_counter() - self.started

    def emit(self, event, **payload):
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"event": event, "elapsed_s": self.elapsed(), **payload},
                                    sort_keys=True, allow_nan=False) + "\n")


def load_l14():
    spec = importlib.util.spec_from_file_location("astra_review_l14", PINS["l14"][0])
    require(spec is not None and spec.loader is not None, "cannot load L14 helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collapse(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.int64).copy()
    values[(values >= NATIVE_SIZE) & (values < L14_SIZE)] = L14_TARGET
    values[(values >= L14_SIZE) & (values < L25_SIZE)] = L25_TARGET
    values[(values >= L25_SIZE) & (values < COMBINED_SIZE)] = L02_TARGET
    return values


def run(output: Path) -> dict:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    driver = output / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    reviewed = {}
    for name, (path, expected) in PINS.items():
        actual = sha(path)
        require(actual == expected, f"{name} SHA-256 differs")
        reviewed[name] = receipt(path)
    result = json.loads(PINS["result"][0].read_text(encoding="utf-8"))
    require(result.get("status") == "PASS_L02_L14_L25_COMBINED_ASSEMBLY_MAP_NO_LU"
            and result.get("output", {}).get("sha256") == PINS["map"][1],
            "producer result/output relation differs")

    nested = output / "l14-return-replay"
    nested.mkdir()
    l14 = load_l14()
    old = l14.run(SimpleNamespace(mass_receipt=PINS["mass"][0],
                                  mass_receipt_sha256=PINS["mass"][1],
                                  output=nested, return_assembly=True),
                  Budget(output / "progress.jsonl"))
    base_first = np.asarray(old["finite_first"], dtype=np.int64).copy()
    base_second = np.asarray(old["finite_second"], dtype=np.int64).copy()
    l14_active = np.asarray(old["sheet_active"], dtype=np.int64).copy()
    old_port = np.asarray((old["positive"], old["negative"], old["gauge"]), dtype=np.int64)
    del old
    gc.collect()

    with (np.load(PINS["map"][0], allow_pickle=False) as saved,
          np.load(PINS["raw"][0], allow_pickle=False) as raw,
          np.load(PINS["l02_binding"][0], allow_pickle=False) as l02,
          np.load(PINS["l25_binding"][0], allow_pickle=False) as l25):
        raw_first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
        raw_second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
        resistance = np.asarray(raw["finite_resistance_ohm_per_via"], dtype=np.float64)
        inductance = np.asarray(raw["finite_inductance_h_per_via"], dtype=np.float64)
        count = np.asarray(raw["finite_count"], dtype=np.float64)
        native_y = count / (resistance + 2j*np.pi*FREQUENCY_HZ*inductance)

        l14_rows = np.flatnonzero((raw_first == L14_TARGET) ^ (raw_second == L14_TARGET))
        l02_rows = np.asarray(l02["active_finite_index"], dtype=np.int64)
        l25_rows = np.asarray(l25["native_active_finite_index"], dtype=np.int64)
        require(np.array_equal(saved["l14_native_active_finite_rows"], l14_rows)
                and np.array_equal(saved["l02_native_active_finite_rows"], l02_rows)
                and np.array_equal(saved["l25_native_active_finite_rows"], l25_rows),
                "saved finite owner row maps differ")

        work_first, work_second = base_first.copy(), base_second.copy()
        l25_active = np.asarray(saved["l25_potential_active_indices"], dtype=np.int64)
        l25_ordinal = np.asarray(l25["l25_local_electrode_potential_index"], dtype=np.int64) - 539_641
        l25_first_target = np.asarray(l25["target_is_native_first_endpoint"], dtype=bool)
        electrode = l25_active[579_177 + l25_ordinal]
        work_first[l25_rows[l25_first_target]] = electrode[l25_first_target]
        work_second[l25_rows[~l25_first_target]] = electrode[~l25_first_target]

        l02_contact = np.asarray(saved["l02_contact_active_indices"], dtype=np.int64)
        l02_ordinal = np.asarray(l02["native_contact_ordinal"], dtype=np.int64)
        l02_side = np.asarray(l02["target_side"], dtype=np.int8)
        work_first[l02_rows[l02_side == 0]] = l02_contact[l02_ordinal[l02_side == 0]]
        work_second[l02_rows[l02_side == 1]] = l02_contact[l02_ordinal[l02_side == 1]]

        junctions = json.loads(np.asarray(l02["junctions_json_utf8"], dtype=np.uint8).tobytes())
        composite = np.asarray([row["replaced_active_finite_index"] for row in junctions], dtype=np.int64)
        contact_ordinal = np.asarray([row["contact_ordinal"] for row in junctions], dtype=np.int64)
        leg_r = np.asarray([[row["first_leg_resistance_ohm"], row["second_leg_resistance_ohm"]]
                            for row in junctions], dtype=np.float64)
        leg_l = np.asarray([[row["first_leg_inductance_h"], row["second_leg_inductance_h"]]
                            for row in junctions], dtype=np.float64)
        leg_y = 1.0/(leg_r + 2j*np.pi*FREQUENCY_HZ*leg_l)
        keep = np.ones(len(raw_first), dtype=bool)
        keep[composite] = False
        junction_active = l02_contact[contact_ordinal]
        expected_first = np.r_[work_first[keep], work_first[composite], junction_active]
        expected_second = np.r_[work_second[keep], junction_active, work_second[composite]]
        expected_y = np.r_[native_y[keep], leg_y[:, 0], leg_y[:, 1]]
        expected_row = np.r_[np.flatnonzero(keep), composite, composite]
        expected_leg = np.r_[np.full(np.count_nonzero(keep), -1, dtype=np.int8),
                              np.zeros(20, dtype=np.int8), np.ones(20, dtype=np.int8)]

        final_first = np.asarray(saved["final_finite_first_active_index"], dtype=np.int64)
        final_second = np.asarray(saved["final_finite_second_active_index"], dtype=np.int64)
        final_y = np.asarray(saved["final_finite_admittance_s"], dtype=np.complex128)
        exact_arrays = {
            "first": bool(np.array_equal(final_first, expected_first)),
            "second": bool(np.array_equal(final_second, expected_second)),
            "admittance": bool(np.array_equal(final_y, expected_y)),
            "native_row": bool(np.array_equal(saved["final_finite_native_active_row"], expected_row)),
            "split_leg": bool(np.array_equal(saved["final_finite_split_leg"], expected_leg)),
        }
        require(all(exact_arrays.values()), "saved final branch reconstruction differs")

        coordinate_gates = {
            "l14": bool(np.array_equal(saved["l14_sheet_active_indices"], l14_active)),
            "l25": bool(np.array_equal(l25_active,
                np.r_[L25_TARGET, np.arange(L14_SIZE, L25_SIZE, dtype=np.int64)])),
            "l02": bool(np.array_equal(saved["l02_combined_sheet_active_indices"],
                np.r_[L02_TARGET, np.arange(L25_SIZE, COMBINED_SIZE, dtype=np.int64)])),
            "port": bool(np.array_equal(np.r_[saved["source_port_active_indices"], saved["gauge_active_index"]],
                                         old_port)),
        }
        require(all(coordinate_gates.values()), "saved coordinate/port reconstruction differs")
        owner_gates = {
            "l02": bool(np.array_equal(saved["gc_l02_source_partial_ordinals"], [0])),
            "l14": bool(np.array_equal(saved["gc_l14_source_partial_ordinals"], [6, 7])),
            "l25": bool(np.array_equal(saved["gc_l25_source_partial_ordinals"], [13, 14])),
            "retained": bool(np.array_equal(saved["gc_retained_native_partial_ordinals"],
                sorted(set(range(36)) - {0, 6, 7, 13, 14}))),
        }
        require(all(owner_gates.values()), "saved G/C owner partition differs")

        expected_collapsed_first = np.r_[raw_first[keep], raw_first[composite],
                                          np.full(20, L02_TARGET, dtype=np.int64)]
        expected_collapsed_second = np.r_[raw_second[keep],
                                           np.full(20, L02_TARGET, dtype=np.int64), raw_second[composite]]
        collapse_exact = bool(np.array_equal(collapse(final_first), expected_collapsed_first)
                              and np.array_equal(collapse(final_second), expected_collapsed_second))
        require(collapse_exact, "saved final branches do not collapse to corrected native endpoints")

        index = np.arange(COMBINED_SIZE, dtype=np.int64)
        voltage = ((index % 1009) / 1009.0 + 1j*((17*index) % 1013) / 1013.0).astype(np.complex128)
        difference = voltage[final_first] - voltage[final_second]
        branch_current = final_y * difference
        action = np.zeros(COMBINED_SIZE, dtype=np.complex128)
        np.add.at(action, final_first, branch_current)
        np.add.at(action, final_second, -branch_current)
        branch_power = np.sum(np.conj(difference)*branch_current)
        action_power = np.vdot(voltage, action)
        power_relative = float(abs(action_power-branch_power)/max(abs(branch_power), np.finfo(float).tiny))
        kcl_relative = float(abs(np.sum(action))/max(float(np.sum(abs(branch_current))), np.finfo(float).tiny))
        passive = bool(branch_power.real > 0)
        require(power_relative <= 2e-12 and kcl_relative <= 2e-14 and passive,
                "finite branch action virtual-work/KCL/passivity gate failed")

    metrics = {
        "exact_full_branch_reconstruction": exact_arrays,
        "coordinate_reconstruction": coordinate_gates,
        "gc_owner_partition": owner_gates,
        "native_collapse_exact": collapse_exact,
        "finite_branch_count": int(len(final_y)),
        "virtual_work_relative_error": power_relative,
        "global_kcl_relative_error": kcl_relative,
        "deterministic_branch_power_real_w": float(branch_power.real),
        "positive_dissipation": passive,
    }
    require(result["coordinate_contract"]["combined_potential_count"] == COMBINED_SIZE
            and result["coordinate_contract"]["combined_mixed_unknown_count"] == 2_944_100
            and result["finite_branch_contract"]["final_branch_count"] == len(final_y)
            and all(value == 0 for value in result["finite_branch_contract"]
                    ["pairwise_row_intersection_counts"].values()),
            "producer result counts/intersection gates differ")
    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_L02_L14_L25_COMBINED_ASSEMBLY_MAP_INDEPENDENT_REVIEW",
        "reviewed": reviewed,
        "metrics": metrics,
        "driver": receipt(driver),
        "elapsed_s": time.perf_counter() - started,
        "findings": [],
        "scope": "Independent exact replay of all saved final branch rows from the pinned L14 return assembly plus L02/L25 bindings, coordinate and owner maps, native collapse, and a full-list virtual-work/KCL check. No combined sparse matrix, LU, field or Green operator.",
    }
    atomic_json(output / "independent-review.json", review)
    return review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=R / "astra-l02-l14-l25-combined-assembly-map-review-01")
    args = parser.parse_args()
    review = run(args.output.resolve())
    print(json.dumps({"status": review["status"], "metrics": review["metrics"],
                      "elapsed_s": review["elapsed_s"]}, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
