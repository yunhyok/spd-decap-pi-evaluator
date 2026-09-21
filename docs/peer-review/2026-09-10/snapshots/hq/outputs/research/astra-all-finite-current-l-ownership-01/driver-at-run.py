"""Accepted all-finite current and existing scalar-L ownership readback only."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
from time import monotonic, perf_counter

import numpy as np

import compare_astra_hybrid_board_1mhz as accepted
from measure_astra_hybrid_return_currents import unique_join

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
FREQUENCY_HZ = 1.0e6
TOTAL = 1_692_409
RESULT = R / "astra-l02-hybrid-right-correction-01" / "result.json"
RESULT_SHA = "7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3"
FIELD = RESULT.parent / "field.npz"
FIELD_SHA = "960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b"
PINS = {
    "accepted_result": (RESULT, RESULT_SHA),
    "accepted_driver": (RESULT.parent / "driver-at-run.py", "91e792487f371d14c21e9ce6729f17b34b93386f9f1d0c599302b129d2d755a6"),
    "comparator": (Path(accepted.__file__), "e9ce9ad63366586293364c80ec49bae77c1812c423811f06bce1c5c9a44dc1fa"),
    "fd28_validator": (ROOT / "tools/research/validate_astra_l02_hybrid_field.py", "fd28d5a3eef17d7d85e0fab52de1a88e1910faa2ad46a4cde3b16d3607c8abd9"),
    "return_current_helper": (ROOT / "tools/research/measure_astra_hybrid_return_currents.py", "cce067c0e3f4276ebff982815a9118531e283106d6d55a1d5fb90c0fecf0cbdf"),
    "pack06": (R / "astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz", "01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c"),
    "combined_map": (R / "astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz", "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469"),
    "composite_result": (R / "astra-full-return-composite-sources-03/result.json", "01cfbb2cbb324a2c7421d46e112a17e8ece2d3a6137aecbad12cc74dd07ff0c3"),
    "composite_delta": (R / "astra-full-return-composite-sources-03/composite-source-segment-delta.npz", "b47b6031c901e50e41c7dc12519a5fcadb7699e236d461df4d159189cd625faa"),
}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def _rss_bytes() -> int:
    if os.name != "nt":
        return 0

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
                   ("peak_working_set_size", ctypes.c_size_t), ("working_set_size", ctypes.c_size_t),
                   ("quota_peak_paged_pool_usage", ctypes.c_size_t), ("quota_paged_pool_usage", ctypes.c_size_t),
                   ("quota_peak_non_paged_pool_usage", ctypes.c_size_t), ("quota_non_paged_pool_usage", ctypes.c_size_t),
                   ("pagefile_usage", ctypes.c_size_t), ("peak_pagefile_usage", ctypes.c_size_t),
                   ("private_usage", ctypes.c_size_t)]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel32, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    getter = psapi.GetProcessMemoryInfo
    getter.argtypes, getter.restype = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD), wintypes.BOOL
    if not getter(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.working_set_size)


class Budget:
    def __init__(self) -> None:
        self.started, self.peak_rss_bytes = monotonic(), _rss_bytes()

    def check(self, phase: str) -> None:
        self.peak_rss_bytes = max(self.peak_rss_bytes, _rss_bytes())
        if monotonic() - self.started > 60.0:
            raise TimeoutError(f"60 s budget exceeded during {phase}")
        if self.peak_rss_bytes > 4 * 1024 ** 3:
            raise MemoryError(f"4 GiB budget exceeded during {phase}")

    def report(self) -> dict:
        self.check("receipt")
        return {"max_runtime_s": 60.0, "max_rss_bytes": 4 * 1024 ** 3,
                "elapsed_s": monotonic() - self.started, "peak_rss_bytes": self.peak_rss_bytes,
                "cooperative_checks": True}


def verify_pins() -> None:
    for name, (path, digest) in PINS.items():
        if sha(path) != digest:
            raise ValueError(f"{name} SHA-256 differs")


def top_rows(metric: np.ndarray, final_rows: np.ndarray, native_rows: np.ndarray, count: int = 20) -> list[dict]:
    positions = np.argpartition(np.abs(metric), -count)[-count:]
    positions = positions[np.argsort(np.abs(metric[positions]))[::-1]]
    return [{"final_active_current_row": int(final_rows[index]), "native_active_current_row": int(native_rows[index]),
             "metric": [float(metric[index].real), float(metric[index].imag)]} for index in positions]


def collect(budget: Budget) -> tuple[dict, dict[str, np.ndarray], dict]:
    verify_pins()
    # Comparator acceptance performs every numerical/physical gate before field.npz is opened.
    actual, voltage = accepted.load_accepted_field(RESULT, RESULT_SHA)
    if not (actual["field"]["sha256"] == FIELD_SHA and sha(FIELD) == FIELD_SHA and voltage.shape == (3_178_104,)
            and np.all(np.isfinite(voltage))):
        raise ValueError("accepted field receipt or voltage contract differs")
    budget.check("accepted field gate and voltage readback")
    with np.load(PINS["pack06"][0], allow_pickle=False) as pack:
        first = np.asarray(pack["finite_first_active_index"], dtype=np.int64)
        second = np.asarray(pack["finite_second_active_index"], dtype=np.int64)
        admittance = np.asarray(pack["finite_admittance_s"], dtype=np.complex128)
        native_rows = np.asarray(pack["final_finite_native_active_row"], dtype=np.int64)
        split_legs = np.asarray(pack["final_finite_split_leg"], dtype=np.int8)
    with np.load(PINS["combined_map"][0], allow_pickle=False) as mapping:
        map_native_rows = np.asarray(mapping["final_finite_native_active_row"], dtype=np.int64)
        map_split_legs = np.asarray(mapping["final_finite_split_leg"], dtype=np.int8)
    if not (first.shape == second.shape == admittance.shape == native_rows.shape == split_legs.shape == (TOTAL,)
            and np.array_equal(native_rows, map_native_rows) and np.array_equal(split_legs, map_split_legs)
            and np.all(first >= 0) and np.all(second >= 0) and np.all(first < len(voltage)) and np.all(second < len(voltage))
            and np.all(first != second) and np.all(np.isfinite(admittance)) and np.all(admittance.real > 0.0)):
        raise ValueError("pack06/combined-map finite identity differs")
    final_rows = np.arange(TOTAL, dtype=np.int64)
    impedance = 1.0 / admittance
    resistance, inductance = impedance.real, impedance.imag / (2.0 * np.pi * FREQUENCY_HZ)
    current = admittance * (voltage[first] - voltage[second])
    hermitian_i2_l = np.abs(current) ** 2 * inductance
    ordinary_i2_l = current ** 2 * inductance
    if not (np.all(np.isfinite(impedance)) and np.all(np.isfinite(resistance)) and np.all(np.isfinite(inductance))
            and np.all(np.isfinite(current)) and np.all(np.isfinite(hermitian_i2_l)) and np.all(np.isfinite(ordinary_i2_l))):
        raise ValueError("finite current/L readback differs")
    budget.check("all finite branch currents and scalar-L metrics")
    with np.load(PINS["composite_delta"][0], allow_pickle=False) as archive:
        composite_records = json.loads(archive["records_json_utf8"].tobytes())
    leg0_rows = np.flatnonzero(split_legs == 0)
    leg1_rows = np.flatnonzero(split_legs == 1)
    source_by_leg0 = {int(record["final_legs"][0]["final_finite_row_id"]): record for record in composite_records}
    if not (len(leg0_rows) == len(leg1_rows) == len(composite_records) == len(source_by_leg0) == 20
            and set(leg0_rows.tolist()) == set(source_by_leg0)
            and np.count_nonzero(split_legs == -1) == TOTAL - 40):
        raise ValueError("composite split-leg ownership join differs")
    segment_count = sum(len(record["final_legs"][0]["ordered_source_segments"]) for record in composite_records)
    via_count = sum(segment["kind"] == "via" for record in composite_records for segment in record["final_legs"][0]["ordered_source_segments"])
    trace_count = sum(segment["kind"] == "trace" for record in composite_records for leg in record["final_legs"] for segment in leg["ordered_source_segments"])
    if not (segment_count == 78 and via_count == 73 and trace_count == 5):
        raise ValueError("composite source segment ownership differs")
    composite_json = np.frombuffer(json.dumps([source_by_leg0[int(row)] for row in leg0_rows], sort_keys=True).encode("utf-8"), dtype=np.uint8)
    arrays = {"final_active_current_row": final_rows, "native_active_current_row": native_rows,
              "final_split_leg": split_legs, "first_active_node": first, "second_active_node": second,
              "impedance_ohm": impedance, "resistance_ohm": resistance, "inductance_h": inductance,
              "finite_current_first_to_second_a": current, "hermitian_abs_i_squared_l_h": hermitian_i2_l,
              "ordinary_i_squared_l_h": ordinary_i2_l, "composite_leg0_final_active_current_row": leg0_rows,
              "composite_leg0_native_active_current_row": native_rows[leg0_rows],
              "composite_leg0_finite_current_first_to_second_a": current[leg0_rows],
              "composite_leg0_source_segments_json_utf8": composite_json}
    totals = {"hermitian_abs_i_squared_l_h": float(hermitian_i2_l.sum()),
              "ordinary_i_squared_l_h": [float(ordinary_i2_l.sum().real), float(ordinary_i2_l.sum().imag)]}
    summary = {"counts": {"final_finite_rows": TOTAL, "native_rows": int(len(np.unique(native_rows)),),
                           "split_leg0_rows_joined_to_composite_ownership": 20, "composite_leg0_source_vias": via_count,
                           "composite_source_trace_segments": trace_count}, "totals": totals,
               "top_rows": {"hermitian_abs_i_squared_l_h": top_rows(hermitian_i2_l.astype(np.complex128), final_rows, native_rows),
                            "ordinary_i_squared_l_h": top_rows(ordinary_i2_l, final_rows, native_rows)}}
    budget.check("composite ownership and metric ranking")
    return actual, arrays, summary


def self_check() -> None:
    verify_pins()
    with np.load(PINS["pack06"][0], allow_pickle=False) as pack, np.load(PINS["combined_map"][0], allow_pickle=False) as mapping:
        assert np.array_equal(pack["final_finite_native_active_row"], mapping["final_finite_native_active_row"])
        assert np.array_equal(pack["final_finite_split_leg"], mapping["final_finite_split_leg"])
    assert unique_join(np.array([7, 3, 8]), np.array([8, 7])).tolist() == [2, 0]
    print(f"{PROGRAM} v{VERSION}: all-finite readback identity SELF_CHECK PASS")


def run(output: Path) -> None:
    if output.exists():
        raise ValueError("output already exists")
    budget, started = Budget(), perf_counter()
    actual, arrays, summary = collect(budget)
    output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    artifact = output / "all-finite-current-l-ownership.npz"
    np.savez_compressed(artifact, **arrays)
    budget.check("artifact write")
    result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_ACCEPTED_ALL_FINITE_CURRENT_L_OWNERSHIP_READBACK",
              "frequency_hz": FREQUENCY_HZ, "accepted_result": receipt(RESULT), "accepted_field": receipt(FIELD),
              "driver": receipt(frozen), "inputs": {name: receipt(path) for name, (path, _digest) in PINS.items()},
              "artifact": receipt(artifact), "summary": summary, "budget": budget.report(), "elapsed_s": perf_counter() - started,
              "scope": "Accepted-voltage finite-branch readback only: I=Y*(Vfirst-Vsecond), with existing scalar R+j omega L ownership reported. The twenty split-leg0 rows are joined to saved composite Via/trace ownership without assigning any current to its 93 source segments. No historical current array, raw SPD/SQLite access, matrix build, magnetic action, solve, reference, or accuracy claim."}
    with (output / "result.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "artifact": result["artifact"], "summary": summary, "budget": result["budget"]}, sort_keys=True))


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
