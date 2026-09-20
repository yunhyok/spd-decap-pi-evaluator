"""Join accepted port-return currents to normalized first-post source Vias."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
TOTAL = 26_790
CURRENT = R / "astra-hybrid-return-currents-01"
SOURCES = R / "astra-full-return-first-post-source-map-01"
PINS = {
    "accepted_current_driver": (CURRENT / "driver-at-run.py", "cce067c0e3f4276ebff982815a9118531e283106d6d55a1d5fb90c0fecf0cbdf"),
    "accepted_current_result": (CURRENT / "result.json", "89b0b54703340c0fd7e57197130c0e34ad58c59e2a6838427842de337b1ca001"),
    "accepted_current_artifact": (CURRENT / "hybrid-port-return-currents.npz", "934c58c9d82c8c29a6c03f2d0b1552e68c5158416670272de0a299bdcb133210"),
    "source_map_driver": (SOURCES / "driver-at-run.py", "4fd764709c5ff7bfd3ce5aef4f01f7abdc2b6788576413049321c7fbb414143a"),
    "source_map_result": (SOURCES / "result.json", "4de096cce09cb2827572c866b5989c32475a8e3af710b72630cdb838d4663963"),
    "source_map_artifact": (SOURCES / "first-post-source-map.npz", "87db96a71522fb4d3a28ece5106ffbf2a099f8003c5bf8e6fb5c69e48a81e900"),
}


def _rss_bytes() -> int:
    """Return the current Windows process working set without extra packages."""
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
                   ("peak_working_set_size", ctypes.c_size_t), ("working_set_size", ctypes.c_size_t),
                   ("quota_peak_paged_pool_usage", ctypes.c_size_t), ("quota_paged_pool_usage", ctypes.c_size_t),
                   ("quota_peak_non_paged_pool_usage", ctypes.c_size_t), ("quota_non_paged_pool_usage", ctypes.c_size_t),
                   ("pagefile_usage", ctypes.c_size_t), ("peak_pagefile_usage", ctypes.c_size_t),
                   ("private_usage", ctypes.c_size_t)]

    counters = _Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    get_memory_info = psapi.GetProcessMemoryInfo
    get_memory_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD)
    get_memory_info.restype = wintypes.BOOL
    if not get_memory_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.working_set_size)


class _Budget:
    def __init__(self, seconds: float, gibibytes: float) -> None:
        self.seconds = seconds
        self.max_rss_bytes = int(gibibytes * 1024 ** 3)
        self.started = monotonic()
        self.peak_rss_bytes = _rss_bytes()

    def check(self, phase: str) -> None:
        elapsed = monotonic() - self.started
        self.peak_rss_bytes = max(self.peak_rss_bytes, _rss_bytes())
        if elapsed > self.seconds:
            raise RuntimeError(f"budget time exceeded during {phase}: {elapsed:.3f}s")
        if self.peak_rss_bytes > self.max_rss_bytes:
            raise RuntimeError(f"budget RSS exceeded during {phase}: {self.peak_rss_bytes} bytes")

    def receipt(self) -> dict:
        self.check("receipt")
        return {"max_runtime_s": self.seconds, "max_rss_bytes": self.max_rss_bytes,
                "elapsed_s": monotonic() - self.started, "peak_rss_bytes": self.peak_rss_bytes}


def _atomic_exclusive_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def join_rows(current_rows: np.ndarray, source_rows: np.ndarray) -> np.ndarray:
    order = np.argsort(current_rows)
    sorted_rows = current_rows[order]
    low = np.searchsorted(sorted_rows, source_rows, side="left")
    high = np.searchsorted(sorted_rows, source_rows, side="right")
    if np.any(high - low != 1):
        raise ValueError("final current/source row join is duplicate or missing")
    result = order[low]
    if not np.array_equal(current_rows[result], source_rows):
        raise ValueError("final current/source row lookup differs")
    return result


def main() -> None:
    output = R / "astra-accepted-current-first-post-join-01"
    if output.exists():
        raise ValueError("output already exists")
    budget = _Budget(45, 2)
    for name, (path, digest) in PINS.items():
        if sha(path) != digest:
            raise ValueError(f"{name} SHA-256 differs")
    current_result = json.loads(PINS["accepted_current_result"][0].read_bytes())
    source_result = json.loads(PINS["source_map_result"][0].read_bytes())
    if not (current_result["status"] == "COMPLETED_ACCEPTED_HYBRID_PORT_RETURN_CURRENTS"
            and current_result["counts"]["all_incident_original_finite_edges"] == TOTAL
            and source_result["status"] == "COMPLETED_NORMALIZED_FULL_RETURN_FIRST_POST_SOURCE_MAP"
            and source_result["counts"] == {"records": TOTAL, "power": 978, "ground": 25_812, "composite_port_leg1_vias": 20, "composite_leg0_vias_referenced_only": 73}):
        raise ValueError("accepted current/source-map receipt contract differs")
    with np.load(PINS["accepted_current_artifact"][0], allow_pickle=False) as archive:
        current = {key: np.asarray(archive[key]) for key in archive.files if key not in ("counts_json_utf8", "role_totals_json_utf8")}
    if not (len(current["final_active_current_row"]) == len(current["native_active_current_row"]) == TOTAL
            and len(np.unique(current["final_active_current_row"])) == TOTAL
            and len(np.unique(current["native_active_current_row"])) == TOTAL
            and np.count_nonzero(current["role"] == "power") == 978
            and np.count_nonzero(current["role"] == "ground") == 25_812):
        raise ValueError("accepted-current row/role contract differs")
    with np.load(PINS["source_map_artifact"][0], allow_pickle=False) as archive:
        source = json.loads(archive["records_json_utf8"].tobytes())
    if not (len(source) == TOTAL and len({row["final_active_current_row"] for row in source}) == TOTAL
            and len({row["native_active_current_row"] for row in source}) == TOTAL
            and len({row["via_id"] for row in source}) == TOTAL
            and all(row["port_outward_to_raw_top_to_l02_sign"] == 1 for row in source)):
        raise ValueError("normalized source-map orientation/identity contract differs")
    source_final = np.asarray([row["final_active_current_row"] for row in source], dtype=np.int64)
    positions = join_rows(current["final_active_current_row"], source_final)
    source_native = np.asarray([row["native_active_current_row"] for row in source], dtype=np.int64)
    if not np.array_equal(current["native_active_current_row"][positions], source_native):
        raise ValueError("final/native row join differs")
    source_role = np.asarray([row["role"] for row in source])
    source_sign = np.asarray([row["port_outward_sign"] for row in source], dtype=np.int8)
    if not (np.array_equal(current["role"][positions], source_role)
            and np.array_equal(current["port_outward_sign"][positions], source_sign)):
        raise ValueError("current/source role or orientation differs")
    composite = np.asarray([row["source_class"] == "BOUND_QUALIFIED_COMPOSITE_PORT_LEG_1" for row in source])
    if not (np.count_nonzero(composite) == 20 and np.all(source_role[composite] == "ground")
            and np.all(current["final_split_leg"][positions][composite] == 1)
            and np.count_nonzero(current["final_split_leg"][positions] == 0) == 0):
        raise ValueError("composite port-leg current contract differs")
    budget.check("pinned current/source join")
    output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    arrays = {
        "final_active_current_row": current["final_active_current_row"][positions],
        "native_active_current_row": current["native_active_current_row"][positions],
        "original_finite_index": current["original_finite_index"][positions],
        "role": current["role"][positions], "pin_id": current["pin_id"][positions],
        "final_link_id": current["final_link_id"][positions], "native_link_id": current["native_link_id"][positions],
        "source_link_id": current["source_link_id"][positions], "current_owner_ids_json": current["owner_ids_json"][positions],
        "first_active_node": current["first_active_node"][positions], "second_active_node": current["second_active_node"][positions],
        "port_outward_sign": current["port_outward_sign"][positions], "final_split_leg": current["final_split_leg"][positions],
        "finite_current_first_to_second_a": current["finite_current_first_to_second_a"][positions],
        "port_outward_signed_current_a": current["port_outward_signed_current_a"][positions],
        "via_id": np.asarray([row["via_id"] for row in source]),
        "source_via_owner_id": np.asarray(["via:" + row["via_id"] for row in source]),
        "padstack_id": np.asarray([row["padstack_id"] for row in source]),
        "source_record_sha256": np.asarray([row["source_record_sha256"] for row in source]),
        "top_raw_node_id": np.asarray([row["top_raw_node_id"] for row in source]),
        "lower_raw_node_id": np.asarray([row["lower_raw_node_id"] for row in source]),
        "top_node_id_fold": np.asarray([row["top_node_id_fold"] for row in source]),
        "lower_node_id_fold": np.asarray([row["lower_node_id_fold"] for row in source]),
        "source_xy_pm": np.asarray([[row["x_pm"], row["y_pm"]] for row in source], dtype=np.int64),
        "port_outward_to_raw_top_to_l02_sign": np.asarray([row["port_outward_to_raw_top_to_l02_sign"] for row in source], dtype=np.int8),
        "final_first_to_second_raw_top_to_l02_sign": np.asarray([row["final_first_to_second_raw_top_to_l02_sign"] for row in source], dtype=np.int8),
        "source_class": np.asarray([row["source_class"] for row in source]),
        "composite_port_leg1": composite,
    }
    artifact = output / "accepted-current-first-post.npz"
    np.savez_compressed(artifact, **arrays)
    result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_ACCEPTED_CURRENT_FIRST_POST_JOIN",
              "driver": receipt(frozen), "inputs": {name: receipt(path) for name, (path, _digest) in PINS.items()},
              "artifact": receipt(artifact), "counts": {"records": TOTAL, "power": 978, "ground": 25_812, "composite_port_leg1_current_records": 20, "composite_leg0_via_current_records": 0},
              "checks": {"unique_final_and_native_rows": True, "source_orientation_matches_current": True, "all_raw_top_to_l02_outward": True, "composite_leg0_not_expanded": True},
              "budget": budget.receipt(), "scope": "Accepted finite-branch currents joined once to saved first-post Via identities. No solve, reference, SQL, FMM, geometry integration, magnetic model, or accuracy claim."}
    _atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({"status": result["status"], "artifact": result["artifact"], "counts": result["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
