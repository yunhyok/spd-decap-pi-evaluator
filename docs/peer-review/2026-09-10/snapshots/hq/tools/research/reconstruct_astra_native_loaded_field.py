#!/usr/bin/env python3
"""Reconstruct one saved VTRIP 1 MHz field from Run02 NPZ artifacts only.

This research receipt deliberately consumes only the numeric/raw/derived field
snapshots produced by the observer.  It neither loads a bundle nor imports the
product solver.  The rebuilt matrix and SuperLU factor stay in memory; the
only output is a compact JSON receipt.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu


ROOT = Path(__file__).resolve().parents[2]
FREQUENCY_HZ = 1.0e6
EXPECTED_INPUT_SHA256 = {
    "numeric": "c54d9e73a47a862396e6f70347ad9e319a6fbc1a3310e95feeeedf2b3cf1e292",
    "raw": "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    "derived": "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0",
}
EXPECTED_OBSERVER_SHA256 = (
    "5d09ee1ffc413215e641275b9dd1851ff716f7f86aea279a10228c099ee452cd"
)
EXPECTED_ZDD_OHM = complex(
    0.00010031137009881143,
    -0.0006057593380320584,
)
VOLTAGE_REL_TOL = 1.0e-10
VOLTAGE_ABS_TOL = 1.0e-15


class ReconstructionError(ValueError):
    """Raised when saved artifacts cannot support this exact reconstruction."""


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _complex(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def _max_abs(values: np.ndarray[Any, Any]) -> float:
    return float(np.max(np.abs(values))) if values.size else 0.0


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReconstructionError(message)


def _decode_text_vector(array: np.ndarray[Any, Any], label: str) -> tuple[str, ...]:
    _require(
        array.dtype == np.dtype(np.uint8) and array.ndim == 1,
        f"{label} is not a packed uint8 text vector",
    )
    value = json.loads(array.tobytes().decode("utf-8"))
    _require(
        isinstance(value, list) and all(isinstance(item, str) for item in value),
        f"{label} is not a JSON string list",
    )
    return tuple(value)


def _decode_scalar_text(array: np.ndarray[Any, Any], label: str) -> str:
    _require(
        array.dtype == np.dtype(np.uint8) and array.ndim == 1,
        f"{label} is not a packed uint8 scalar",
    )
    value = json.loads(array.tobytes().decode("utf-8"))
    _require(isinstance(value, str), f"{label} is not a JSON string")
    return value


def _verify_text_encoding(
    archive: Mapping[str, np.ndarray[Any, Any]],
    label: str,
) -> None:
    _require(
        np.array_equal(
            archive["text_encoding_schema_version"],
            np.asarray([1], dtype=np.int64),
        ),
        f"{label} text schema is not version 1",
    )
    _require(
        archive["text_vector_encoding_tag_utf8"].tobytes().decode("utf-8")
        == "utf8_json_uint8",
        f"{label} vector text encoding differs",
    )
    _require(
        archive["text_scalar_encoding_tag_utf8"].tobytes().decode("utf-8")
        == "utf8_json_uint8_scalar",
        f"{label} scalar text encoding differs",
    )


def _csc_from_snapshot(
    archive: Mapping[str, np.ndarray[Any, Any]],
    prefix: str,
) -> sparse.csc_matrix:
    shape = tuple(int(value) for value in archive[f"{prefix}_nominal_c_shape"])
    indptr = np.asarray(archive[f"{prefix}_nominal_c_indptr"], dtype=np.int64)
    indices = np.asarray(archive[f"{prefix}_nominal_c_indices"], dtype=np.int64)
    data = np.asarray(archive[f"{prefix}_nominal_c_data"], dtype=np.float64)
    _require(len(shape) == 2 and shape[0] == shape[1], f"{prefix} has invalid C shape")
    _require(
        indptr.ndim == indices.ndim == data.ndim == 1
        and indptr.size == shape[1] + 1
        and int(indptr[0]) == 0
        and int(indptr[-1]) == indices.size
        and indices.size == data.size,
        f"{prefix} has invalid CSC arrays",
    )
    _require(
        not indices.size
        or (int(indices.min()) >= 0 and int(indices.max()) < shape[0]),
        f"{prefix} CSC indices are out of bounds",
    )
    _require(np.all(np.isfinite(data)), f"{prefix} C contains non-finite data")
    result = sparse.csc_matrix((data, indices, indptr), shape=shape)
    result.sum_duplicates()
    result.sort_indices()
    return result


def _rss_bytes() -> int:
    """Read the current Windows process working/private bytes without psutil."""

    if os.name != "nt":
        return 0

    class _ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32),
            ("PageFaultCount", ctypes.c_uint32),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    counters = _ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    current_process.argtypes = ()
    current_process.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCountersEx),
        wintypes.DWORD,
    )
    get_memory.restype = wintypes.BOOL
    ok = get_memory(
        current_process(),
        ctypes.byref(counters),
        counters.cb,
    )
    if not ok:
        raise OSError("GetProcessMemoryInfo failed")
    return int(max(counters.WorkingSetSize, counters.PrivateUsage))


@dataclass(slots=True)
class _Budget:
    max_runtime_s: float
    max_rss_bytes: int
    started: float
    peak_rss_bytes: int = 0

    @classmethod
    def create(cls, max_runtime_s: float, max_rss_gib: float) -> _Budget:
        return cls(max_runtime_s, int(max_rss_gib * 2**30), time.monotonic())

    def check(self, phase: str) -> None:
        elapsed = time.monotonic() - self.started
        rss = _rss_bytes()
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        if elapsed > self.max_runtime_s:
            raise ReconstructionError(
                f"runtime budget exceeded after {phase}: {elapsed:.3f}s"
            )
        if rss and rss > self.max_rss_bytes:
            raise ReconstructionError(
                f"RSS budget exceeded after {phase}: {rss} bytes"
            )

    def receipt(self) -> dict[str, float | int]:
        return {
            "elapsed_s": float(time.monotonic() - self.started),
            "peak_rss_bytes": self.peak_rss_bytes,
            "max_runtime_s": self.max_runtime_s,
            "max_rss_bytes": self.max_rss_bytes,
        }


def _atomic_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    """Create one output atomically without overwriting an existing receipt."""

    if path.exists():
        raise ReconstructionError(f"refusing to overwrite existing output: {path}")
    encoded = (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # NTFS hard-link creation fails if a concurrent process created path,
        # preserving exclusive-create semantics after the durable temp write.
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _input_receipt(paths: Mapping[str, Path]) -> dict[str, Any]:
    receipt: dict[str, Any] = {}
    for name, path in paths.items():
        _require(path.is_file(), f"missing {name} NPZ: {path}")
        actual = _sha256_file(path)
        _require(
            actual == EXPECTED_INPUT_SHA256[name],
            f"{name} NPZ hash differs from the Run02 pin",
        )
        receipt[name] = {
            "path": str(path),
            "sha256": actual,
            "size_bytes": path.stat().st_size,
        }
    return receipt


def _sparse_symmetry_metrics(matrix: sparse.csc_matrix) -> dict[str, float | int]:
    difference = (matrix - matrix.T).tocsc()
    return {
        "matrix_nnz": int(matrix.nnz),
        "max_abs_entry": _max_abs(matrix.data),
        "transpose_symmetry_max_abs": _max_abs(difference.data),
        "row_sum_max_abs": _max_abs(np.asarray(matrix.sum(axis=1)).ravel()),
    }


def _build_partial_matrix(
    raw: Mapping[str, np.ndarray[Any, Any]],
    *,
    surface_lookup: Mapping[str, int],
    surface_to_reduced: np.ndarray[Any, Any],
    global_to_active: np.ndarray[Any, Any],
    active_size: int,
) -> tuple[sparse.csc_matrix, dict[str, int | float]]:
    coefficients = np.asarray(
        raw["partial_actual_1mhz_dispersion_admittance_scale_s"],
        dtype=np.complex128,
    )
    _require(coefficients.shape == (36,), "expected 36 one-MHz partial scales")
    _require(
        np.all(np.isfinite(coefficients)), "one-MHz partial scale is non-finite"
    )
    all_rows: list[np.ndarray[Any, Any]] = []
    all_columns: list[np.ndarray[Any, Any]] = []
    all_data: list[np.ndarray[Any, Any]] = []
    aliases_coalesced = 0
    pruned_entries = 0
    source_nnz = 0
    active_nnz = 0
    for index in range(36):
        prefix = f"partial_{index:02d}"
        names = _decode_text_vector(raw[f"{prefix}_net_names"], prefix)
        local_surface = np.asarray(
            [surface_lookup.get(name, -1) for name in names],
            dtype=np.int64,
        )
        _require(
            np.all(local_surface >= 0),
            f"{prefix} net name has no source surface alias",
        )
        local_active = global_to_active[surface_to_reduced[local_surface]]
        active_aliases = local_active[local_active >= 0]
        aliases_coalesced += int(active_aliases.size - np.unique(active_aliases).size)
        local_c = _csc_from_snapshot(raw, prefix).tocoo(copy=False)
        _require(
            local_c.shape == (len(names), len(names)),
            f"{prefix} C shape differs from its names",
        )
        source_nnz += int(local_c.nnz)
        row = local_active[local_c.row]
        column = local_active[local_c.col]
        mask = (row >= 0) & (column >= 0)
        pruned_entries += int(mask.size - np.count_nonzero(mask))
        if not np.any(mask):
            continue
        all_rows.append(np.asarray(row[mask], dtype=np.int32))
        all_columns.append(np.asarray(column[mask], dtype=np.int32))
        all_data.append(
            np.asarray(
                coefficients[index] * local_c.data[mask],
                dtype=np.complex128,
            )
        )
        active_nnz += int(np.count_nonzero(mask))
    if not all_data:
        raise ReconstructionError("all saved partial C entries were pruned")
    matrix = sparse.coo_matrix(
        (
            np.concatenate(all_data),
            (np.concatenate(all_rows), np.concatenate(all_columns)),
        ),
        shape=(active_size, active_size),
        dtype=np.complex128,
    ).tocsc()
    matrix.sum_duplicates()
    matrix.sort_indices()
    return matrix, {
        "partial_count": 36,
        "source_nominal_c_nnz": source_nnz,
        "active_precoalesce_nnz": active_nnz,
        "coalesced_surface_alias_occurrences": aliases_coalesced,
        "pruned_c_entries": pruned_entries,
        "partial_matrix_nnz": int(matrix.nnz),
        "minimum_partial_scale_real_s": float(np.min(coefficients.real)),
    }


def _finite_matrix(
    raw: Mapping[str, np.ndarray[Any, Any]],
    active_size: int,
) -> tuple[sparse.csc_matrix, np.ndarray[Any, Any], dict[str, float | int]]:
    first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
    second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
    count = np.asarray(raw["finite_count"], dtype=np.float64)
    resistance = np.asarray(
        raw["finite_resistance_ohm_per_via"],
        dtype=np.float64,
    )
    inductance = np.asarray(
        raw["finite_inductance_h_per_via"],
        dtype=np.float64,
    )
    edge_count = first.size
    _require(
        all(array.shape == (edge_count,) for array in (second, count, resistance, inductance)),
        "finite numeric arrays differ in length",
    )
    _require(
        np.all((first >= 0) & (first < active_size))
        and np.all((second >= 0) & (second < active_size))
        and np.all(first != second),
        "finite link active endpoints are invalid",
    )
    _require(
        np.all(np.isfinite(count))
        and np.all(np.isfinite(resistance))
        and np.all(np.isfinite(inductance))
        and np.all(count > 0.0)
        and np.all(resistance > 0.0)
        and np.all(inductance >= 0.0),
        "finite R/L/count values are not passive finite values",
    )
    admittance = count / (
        resistance + 1j * (2.0 * np.pi * FREQUENCY_HZ) * inductance
    )
    _require(np.all(np.isfinite(admittance)), "finite admittance is non-finite")
    row = np.empty(4 * edge_count, dtype=np.int32)
    column = np.empty(4 * edge_count, dtype=np.int32)
    values = np.empty(4 * edge_count, dtype=np.complex128)
    row[0:edge_count] = first
    column[0:edge_count] = first
    values[0:edge_count] = admittance
    row[edge_count : 2 * edge_count] = second
    column[edge_count : 2 * edge_count] = second
    values[edge_count : 2 * edge_count] = admittance
    row[2 * edge_count : 3 * edge_count] = first
    column[2 * edge_count : 3 * edge_count] = second
    values[2 * edge_count : 3 * edge_count] = -admittance
    row[3 * edge_count :] = second
    column[3 * edge_count :] = first
    values[3 * edge_count :] = -admittance
    matrix = sparse.coo_matrix(
        (values, (row, column)),
        shape=(active_size, active_size),
        dtype=np.complex128,
    ).tocsc()
    matrix.sum_duplicates()
    matrix.sort_indices()
    return matrix, admittance, {
        "finite_link_count": int(edge_count),
        "finite_matrix_nnz": int(matrix.nnz),
        "minimum_finite_admittance_real_s": float(np.min(admittance.real)),
        "maximum_finite_admittance_abs_s": _max_abs(admittance),
    }


def _termination_matrix(
    derived: Mapping[str, np.ndarray[Any, Any]],
    active_size: int,
) -> tuple[sparse.csc_matrix, np.ndarray[Any, Any], dict[str, float | int]]:
    positive = np.asarray(
        derived["termination_positive_active_indices"],
        dtype=np.int64,
    )
    negative = np.asarray(
        derived["termination_negative_active_indices"],
        dtype=np.int64,
    )
    admittance = np.asarray(derived["termination_admittance_s"], dtype=np.complex128)
    count = positive.size
    _require(
        negative.shape == admittance.shape == (count,),
        "termination arrays differ in length",
    )
    _require(
        np.all((positive >= 0) & (positive < active_size))
        and np.all((negative >= 0) & (negative < active_size))
        and np.all(positive != negative)
        and np.all(np.isfinite(admittance)),
        "termination endpoints or admittances are invalid",
    )
    row = np.empty(4 * count, dtype=np.int32)
    column = np.empty(4 * count, dtype=np.int32)
    values = np.empty(4 * count, dtype=np.complex128)
    row[0:count] = positive
    column[0:count] = positive
    values[0:count] = admittance
    row[count : 2 * count] = negative
    column[count : 2 * count] = negative
    values[count : 2 * count] = admittance
    row[2 * count : 3 * count] = positive
    column[2 * count : 3 * count] = negative
    values[2 * count : 3 * count] = -admittance
    row[3 * count :] = negative
    column[3 * count :] = positive
    values[3 * count :] = -admittance
    matrix = sparse.coo_matrix(
        (values, (row, column)),
        shape=(active_size, active_size),
        dtype=np.complex128,
    ).tocsc()
    matrix.sum_duplicates()
    matrix.sort_indices()
    return matrix, admittance, {
        "termination_count": int(count),
        "termination_matrix_nnz": int(matrix.nnz),
        "minimum_termination_admittance_real_s": float(np.min(admittance.real)),
    }


def _run(args: argparse.Namespace, budget: _Budget) -> dict[str, Any]:
    input_paths = {
        "numeric": args.numeric.resolve(),
        "raw": args.raw.resolve(),
        "derived": args.derived.resolve(),
    }
    inputs = _input_receipt(input_paths)
    budget.check("input pins")
    with (
        np.load(input_paths["numeric"], allow_pickle=False) as numeric,
        np.load(input_paths["raw"], allow_pickle=False) as raw,
        np.load(input_paths["derived"], allow_pickle=False) as derived,
    ):
        _verify_text_encoding(raw, "raw")
        _verify_text_encoding(derived, "derived")
        _require(
            _decode_scalar_text(raw["raw_snapshot_status"], "raw status")
            == "UNVALIDATED_RAW_SOLVER_FRAME",
            "raw snapshot status differs",
        )
        _require(
            _decode_scalar_text(raw["observer_driver_sha256"], "observer hash")
            == EXPECTED_OBSERVER_SHA256,
            "raw observer driver hash differs",
        )
        shared_keys = (
            "active_global_reduced_indices",
            "global_to_active_indices",
            "retained_active_indices",
            "gauge_active_index",
            "gauge_global_reduced_index",
            "row_scale",
            "scaled_rhs",
            "scaled_solution",
            "unscaled_solution_retained",
            "active_voltage",
            "batch_port_indices",
            "solve_port_reduced_nodes",
            "surface_to_reduced_indices",
        )
        _require(
            all(np.array_equal(numeric[key], raw[key]) for key in shared_keys),
            "numeric checkpoint differs from raw snapshot",
        )

        active_global = np.asarray(raw["active_global_reduced_indices"], dtype=np.int64)
        global_to_active = np.asarray(raw["global_to_active_indices"], dtype=np.int64)
        retained = np.asarray(raw["retained_active_indices"], dtype=np.int64)
        row_scale = np.asarray(raw["row_scale"], dtype=np.float64)
        saved_scaled_rhs = np.asarray(raw["scaled_rhs"], dtype=np.complex128)
        saved_scaled_solution = np.asarray(raw["scaled_solution"], dtype=np.complex128)
        saved_unscaled = np.asarray(
            raw["unscaled_solution_retained"],
            dtype=np.complex128,
        )
        saved_active_voltage = np.asarray(raw["active_voltage"], dtype=np.complex128)
        active_size = active_global.size
        gauge = int(raw["gauge_active_index"][0])
        _require(
            global_to_active.ndim == active_global.ndim == retained.ndim == 1
            and active_size == global_to_active.size
            and np.array_equal(
                global_to_active[active_global],
                np.arange(active_size, dtype=np.int64),
            )
            and retained.size == row_scale.size == saved_scaled_rhs.shape[0]
            and saved_scaled_rhs.shape == saved_scaled_solution.shape
            and saved_scaled_rhs.shape[1] == 1
            and saved_unscaled.shape == saved_scaled_solution.shape
            and saved_active_voltage.shape == (active_size, 1),
            "saved active/retained/row-scale mapping is invalid",
        )
        _require(
            0 <= gauge < active_size
            and gauge not in set(int(value) for value in retained)
            and np.array_equal(
                saved_active_voltage[gauge, :],
                np.zeros(1, dtype=np.complex128),
            )
            and np.array_equal(
                saved_unscaled,
                row_scale[:, None] * saved_scaled_solution,
            )
            and np.array_equal(saved_active_voltage[retained, :], saved_unscaled),
            "saved gauge or physical row-scale field is invalid",
        )
        _require(
            np.all(np.isfinite(row_scale)) and np.all(row_scale > 0.0),
            "saved row scale is not strictly positive finite",
        )
        surface_to_reduced = np.asarray(
            raw["surface_to_reduced_indices"],
            dtype=np.int64,
        )
        surface_ids = _decode_text_vector(raw["surface_node_ids"], "surface IDs")
        _require(
            len(surface_ids) == surface_to_reduced.size
            and np.all(
                (surface_to_reduced >= 0)
                & (surface_to_reduced < global_to_active.size)
            ),
            "saved surface alias map is invalid",
        )
        surface_lookup = {node_id: index for index, node_id in enumerate(surface_ids)}
        _require(
            len(surface_lookup) == len(surface_ids),
            "saved surface IDs are unexpectedly non-unique",
        )
        budget.check("saved mapping decode")

        partial_y, partial_metrics = _build_partial_matrix(
            raw,
            surface_lookup=surface_lookup,
            surface_to_reduced=surface_to_reduced,
            global_to_active=global_to_active,
            active_size=active_size,
        )
        del surface_lookup
        del surface_ids
        budget.check("partial reconstruction")

        finite_y, finite_admittance, finite_metrics = _finite_matrix(
            raw,
            active_size,
        )
        budget.check("finite reconstruction")

        termination_y, termination_admittance, termination_metrics = _termination_matrix(
            derived,
            active_size,
        )
        budget.check("termination reconstruction")

        full_y = (partial_y + finite_y + termination_y).tocsc()
        full_y.sum_duplicates()
        full_y.sort_indices()
        _require(
            np.all(np.isfinite(full_y.data)), "reconstructed Y is non-finite"
        )
        matrix_metrics = _sparse_symmetry_metrics(full_y)
        budget.check("full Y coalescence")

        batches = np.asarray(raw["batch_port_indices"], dtype=np.int64)
        port_nodes = np.asarray(raw["solve_port_reduced_nodes"], dtype=np.int64)
        port_ids = _decode_text_vector(raw["batch_port_ids"], "batch port IDs")
        _require(
            batches.shape == (1,)
            and port_ids == ("ADC_VDD_075_VTRIP_SRAM/0",)
            and port_nodes.ndim == 2
            and port_nodes.shape[1] == 2
            and 0 <= int(batches[0]) < port_nodes.shape[0],
            "saved Device port batch is invalid",
        )
        positive_global, negative_global = (
            int(value) for value in port_nodes[int(batches[0])]
        )
        positive_active = int(global_to_active[positive_global])
        negative_active = int(global_to_active[negative_global])
        _require(
            positive_active >= 0
            and negative_active >= 0
            and positive_active != negative_active,
            "Device port is not in the active matrix",
        )
        physical_rhs = np.zeros(active_size, dtype=np.complex128)
        physical_rhs[positive_active] = 1.0
        physical_rhs[negative_active] = -1.0
        retained_rhs = physical_rhs[retained]
        reconstructed_scaled_rhs = row_scale * retained_rhs
        _require(
            np.array_equal(
                reconstructed_scaled_rhs[:, None],
                saved_scaled_rhs,
            ),
            "saved scaled RHS is not the Device one-amp row-scaled RHS",
        )

        retained_y = full_y[retained, :][:, retained].tocsc()
        scaled_y = (
            sparse.diags(row_scale, format="csc")
            @ retained_y
            @ sparse.diags(row_scale, format="csc")
        ).tocsc()
        scaled_y.sum_duplicates()
        scaled_y.sort_indices()
        _require(
            np.all(np.isfinite(scaled_y.data)), "row-scaled Y is non-finite"
        )
        budget.check("row-scaled gauge elimination")

        # Exactly one SuperLU factor/solve is used for the saved one-amp Device
        # RHS.  It is never serialized.
        lu = splu(scaled_y, permc_spec="COLAMD")
        reconstructed_scaled_solution = np.asarray(
            lu.solve(reconstructed_scaled_rhs),
            dtype=np.complex128,
        )
        _require(
            reconstructed_scaled_solution.shape == row_scale.shape,
            "SuperLU returned an unexpected solution shape",
        )
        reconstructed_unscaled = row_scale * reconstructed_scaled_solution
        reconstructed_active = np.zeros(active_size, dtype=np.complex128)
        reconstructed_active[retained] = reconstructed_unscaled
        _require(
            np.array_equal(
                reconstructed_active[gauge],
                np.asarray(0.0 + 0.0j, dtype=np.complex128),
            ),
            "reconstructed gauge is nonzero",
        )
        physical_residual = retained_y @ reconstructed_unscaled - retained_rhs
        scaled_residual = scaled_y @ reconstructed_scaled_solution - reconstructed_scaled_rhs
        pivot = np.asarray(lu.U.diagonal(), dtype=np.complex128)
        _require(np.all(np.isfinite(pivot)), "SuperLU pivot is non-finite")
        budget.check("one LU Device solve")

        reconstructed_zdd = complex(
            reconstructed_active[positive_active] - reconstructed_active[negative_active]
        )
        saved_zdd = complex(
            saved_active_voltage[positive_active, 0]
            - saved_active_voltage[negative_active, 0]
        )
        voltage_error = reconstructed_active - saved_active_voltage[:, 0]
        scaled_solution_error = reconstructed_scaled_solution - saved_scaled_solution[:, 0]
        zdd_error = reconstructed_zdd - saved_zdd
        zdd_prior_error = reconstructed_zdd - EXPECTED_ZDD_OHM
        voltage_tolerance = max(
            VOLTAGE_ABS_TOL,
            VOLTAGE_REL_TOL * _max_abs(saved_active_voltage[:, 0]),
        )
        zdd_tolerance = max(VOLTAGE_ABS_TOL, VOLTAGE_REL_TOL * abs(saved_zdd))
        _require(
            _max_abs(voltage_error) <= voltage_tolerance,
            "reconstructed full physical voltage differs from saved field",
        )
        _require(
            abs(zdd_error) <= zdd_tolerance,
            "reconstructed Device Zdd differs from saved field",
        )

        y_times_v = full_y @ reconstructed_active
        driven_vhy = complex(np.vdot(reconstructed_active, y_times_v))
        terminal_positive = reconstructed_active[positive_active]
        terminal_negative = reconstructed_active[negative_active]
        terminal_denominator = abs(terminal_positive) + abs(terminal_negative)
        cancellation_ratio = (
            float(abs(reconstructed_zdd) / terminal_denominator)
            if terminal_denominator
            else None
        )
        pivot_abs = np.abs(pivot)
        result = {
            "program": "SPD Decap PI Evaluator",
            "status": "COMPLETED_SAVED_NATIVE_FIELD_RECONSTRUCTION",
            "scope": (
                "One 1 MHz Device one-amp reconstruction from the three pinned "
                "Run02 NPZs only. No bundle, geometry, native compile, product "
                "source, old controller, PowerSI fit, source-trace expansion, or "
                "replacement claim was used."
            ),
            "frequency_hz": FREQUENCY_HZ,
            "inputs": inputs,
            "observer_driver_sha256": EXPECTED_OBSERVER_SHA256,
            "matrix_construction": {
                "partial": partial_metrics,
                "finite": finite_metrics,
                "termination": termination_metrics,
                "surface_node_count": len(surface_to_reduced),
                "active_node_count": int(active_size),
                "retained_node_count": int(retained.size),
                "gauge_active_index": gauge,
                "coalesced_surface_aliases": True,
                "full_y_persisted": False,
                "factor_persisted": False,
            },
            "numerical_checks": {
                "one_superlu_factor_and_device_rhs_solve": True,
                "saved_row_scale_reused": True,
                "saved_scaled_rhs_exact": True,
                "matrix": matrix_metrics,
                "scaled_matrix_nnz": int(scaled_y.nnz),
                "physical_residual_max_abs_a": _max_abs(physical_residual),
                "scaled_residual_max_abs": _max_abs(scaled_residual),
                "pivot_min_abs": float(np.min(pivot_abs)),
                "pivot_max_abs": float(np.max(pivot_abs)),
                "pivot_abs_ratio": float(np.max(pivot_abs) / np.min(pivot_abs)),
                "driven_vhy_ohm": _complex(driven_vhy),
                "driven_conjugated_power_ohm": _complex(np.conj(driven_vhy)),
                "finite_min_real_admittance_s": float(np.min(finite_admittance.real)),
                "termination_min_real_admittance_s": float(
                    np.min(termination_admittance.real)
                ),
                "observed_driven_passivity_real_ohm": float(driven_vhy.real),
                "passivity_scope": (
                    "nonnegative finite/termination conductance and nonnegative "
                    "driven Hermitian power only; no global PSD eigen-certificate"
                ),
                "terminal_cancellation_ratio_abs_vdiff_over_abs_vp_plus_abs_vn": cancellation_ratio,
            },
            "comparison_to_saved_field": {
                "full_physical_voltage_max_abs_error_ohm": _max_abs(voltage_error),
                "full_physical_voltage_tolerance_ohm": voltage_tolerance,
                "scaled_solution_max_abs_error": _max_abs(scaled_solution_error),
                "saved_zdd_ohm": _complex(saved_zdd),
                "reconstructed_zdd_ohm": _complex(reconstructed_zdd),
                "reconstructed_minus_saved_zdd_ohm": _complex(zdd_error),
                "reconstructed_minus_prior_zdd_ohm": _complex(zdd_prior_error),
                "zdd_tolerance_ohm": zdd_tolerance,
                "pass": True,
            },
            "resource": budget.receipt(),
            "limitations": [
                "The result reconstructs the persisted native baseline only; it does not replace, exclude, or re-own any G/C partial or finite R/L link.",
                "No source-trace, island-sensitivity, finite-electrode, current-sharing, AC-convergence, PowerSI, or source-bound readiness claim follows from this one point.",
                "The saved field JSON was known truncated; this reconstruction relies only on the three independently pinned NPZ inputs.",
            ],
        }
        return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--numeric",
        type=Path,
        default=ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/numeric-capture-checkpoint.npz",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz",
    )
    parser.add_argument(
        "--derived",
        type=Path,
        default=ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/derived-field-observation.npz",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/evaluation-research/astra_native_loaded_field_reconstruction_2026-09-07.json",
    )
    parser.add_argument("--max-runtime-s", type=float, default=180.0)
    parser.add_argument("--max-rss-gib", type=float, default=24.0)
    args = parser.parse_args()
    if args.max_runtime_s <= 0.0 or args.max_runtime_s > 180.0:
        parser.error("--max-runtime-s must be in (0, 180]")
    if args.max_rss_gib <= 0.0 or args.max_rss_gib > 24.0:
        parser.error("--max-rss-gib must be in (0, 24]")
    args.output = args.output.resolve()
    if args.output.exists():
        parser.error("--output must be a new file")
    if not args.output.parent.is_dir():
        parser.error("--output parent directory must exist")
    return args


def main() -> int:
    args = _parse_args()
    budget = _Budget.create(args.max_runtime_s, args.max_rss_gib)
    try:
        result = _run(args, budget)
        code = 0
    except BaseException as exc:
        result = {
            "program": "SPD Decap PI Evaluator",
            "status": "STOP_SAVED_NATIVE_FIELD_RECONSTRUCTION",
            "scope": "Saved-NPZ-only reconstruction stopped before any result promotion.",
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "resource": budget.receipt(),
            "limitations": [
                "No replacement, source-trace, island-sensitivity, or PowerSI claim is made after a stopped reconstruction."
            ],
        }
        code = 1
    _atomic_exclusive_json(args.output, result)
    print(f"{result['status']} -> {args.output}", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
