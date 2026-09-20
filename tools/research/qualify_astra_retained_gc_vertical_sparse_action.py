"""Qualify retained-GC face spread/gather through the fixed real z basis."""

from __future__ import annotations

import gc
import json
import os
import traceback
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import sparse

import astra_retained_gc_vertical_sparse_action as adapter
import qualify_astra_retained_gc_streamed_sparse_grid_map as pilot


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
OUTPUT = RESEARCH / "astra-retained-gc-vertical-sparse-action-20260913-01"
FULL_MAP = RESEARCH / "astra-retained-gc-native-sparse-grid-map-20260912-01" / "native-grid-to-unique-island-map.npz"
FULL_REGISTRY = RESEARCH / "astra-retained-gc-native-sparse-grid-map-20260912-01" / "native-face-registry.npz"
FULL_QUALIFICATION = RESEARCH / "astra-retained-gc-native-sparse-grid-map-20260912-02-saved-qualification" / "result.json"
BASIS = RESEARCH / "astra-fixed-vertical-hankel-20260912-01" / "basis.npz"
BASIS_RESULT = RESEARCH / "astra-fixed-vertical-hankel-20260912-01" / "result.json"
REMAP_RESULT = RESEARCH / "astra-native-grid-integer-remap-20260912" / "result.json"
COMMON_BOUNDS = RESEARCH / "astra-combined-charge-grid-bounds-20260912.json"
HELPER = ROOT / "tools" / "research" / "astra_retained_gc_vertical_sparse_action.py"

PINS = {
    FULL_MAP: "fe738fe9009c908785e092fbec7f9114b0090c2dd79a858bb79cdc92e1f3e1d3",
    FULL_REGISTRY: "becd1f3fc5c66772c2eb477625ef31184a815697e7b862d69d4db3fc110bb094",
    FULL_QUALIFICATION: "b3be0f9ff52bd534e01287a25c8778f3c18b0bd421f7a4dc08627d1973527856",
    BASIS: "46fcf657c20d3c5fcfd4fd14b91e8ff2c894c37f7ad0839fc1b0fc57ef8a53ad",
    BASIS_RESULT: "c73b6afff1fcb72c25eb270da595eae330765aac41288acb979bc082e03c650d",
    REMAP_RESULT: "63bce57b3ee59fbff400e6e0ae7cfc8ae311f0b98daca3ba99e24caac02bb324",
    COMMON_BOUNDS: "a1083fe4a534ac033862129abaac08b735f95323714ac9c30ca1f923318ae154",
    HELPER: "a0aafa0f99d74fcd47b3ca1295db95de978f1fc52740ca3d8982e70f0a4fa223",
}

TIME_CAP_S = 120.0
LIVE_ARRAY_PLAN_CAP_BYTES = 1_500_000_000


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def csc_bytes(value: sparse.csc_matrix) -> int:
    return int(value.data.nbytes + value.indices.nbytes + value.indptr.nbytes)


def relative(actual: np.ndarray | complex, expected: np.ndarray | complex) -> float:
    numerator = float(np.linalg.norm(np.asarray(actual) - np.asarray(expected)))
    denominator = max(float(np.linalg.norm(np.asarray(expected))), 1e-300)
    return numerator / denominator


def atomic_sparse(path: Path, value: sparse.csc_matrix) -> None:
    temporary = path.with_name(path.name + ".partial.npz")
    sparse.save_npz(temporary, value, compressed=True)
    os.replace(temporary, path)


def run() -> dict:
    started = perf_counter()
    initial_working_set, _ = pilot.process_memory()
    for path, expected in PINS.items():
        if digest(path) != expected:
            raise ValueError(f"pinned input changed: {path}")
    adapter.self_check()
    full_qualification = json.loads(FULL_QUALIFICATION.read_text(encoding="utf-8"))
    basis_result = json.loads(BASIS_RESULT.read_text(encoding="utf-8"))
    remap_result = json.loads(REMAP_RESULT.read_text(encoding="utf-8"))
    common_bounds = json.loads(COMMON_BOUNDS.read_text(encoding="utf-8"))
    if full_qualification["status"] != "PASS_RETAINED_GC_NATIVE_SPARSE_GRID_SAVED_QUALIFICATION":
        raise ValueError("full native sparse map is not qualified")
    if basis_result["status"] != "PASS_BOUNDED_FIXED_VERTICAL_HANKEL_PILOT":
        raise ValueError("fixed vertical basis is not qualified")
    if remap_result["status"] != "PASS_EXACT_INTEGER_LATTICE_REMAP":
        raise ValueError("native-to-common integer remap is not qualified")
    if common_bounds["status"] != "PASS_CURRENT_QUALIFIED_CHARGE_DOMAINS_COMMON_GRID_COVERAGE":
        raise ValueError("current common grid is not qualified")

    with np.load(FULL_REGISTRY, allow_pickle=False) as saved:
        face_to_island = saved["support_island_index"].astype(np.int64, copy=True)
        face_z_um = saved["support_face_z_um"].astype(np.float64, copy=True)
        native_origin = saved["grid_origin_m"].astype(np.float64, copy=True)
        native_shape = tuple(map(int, saved["grid_shape"]))
        spacing_m = float(saved["grid_spacing_m"][0])
    with np.load(BASIS, allow_pickle=False) as saved:
        exact_z_m = saved["exact_z_m"].astype(np.float64, copy=True)
        basis_archive = saved["basis_real"].astype(np.float64, copy=True)
        singular_values = saved["singular_values"].astype(np.float64, copy=True)
        ranks = saved["ranks"].astype(np.int64, copy=True)
    basis24 = basis_archive[:, :24].copy()
    face_z_row = adapter.exact_height_rows(face_z_um, exact_z_m)
    if basis_archive.shape != (104, 32) or basis24.shape != (104, 24) or ranks.tolist() != [20, 24, 32]:
        raise ValueError("fixed real basis dimensions/ranks changed")
    if not np.array_equal(basis24[:, :20], basis_archive[:, :20]):
        raise ValueError("rank20 is not the unchanged prefix of rank24")
    if len(np.unique(face_z_row)) != 62 or not np.array_equal(exact_z_m[face_z_row], face_z_um * 1e-6):
        raise ValueError("retained face heights do not exactly match basis rows")

    native = sparse.load_npz(FULL_MAP).tocsc()
    destination_shape = tuple(map(int, common_bounds["shape"]))
    destination_origin = np.asarray(common_bounds["origin_m"], dtype=np.float64)
    shift = tuple(map(int, remap_result["integer_shift"]))
    if (native.shape != (native_shape[0] * native_shape[1], 2467) or native_shape != (795, 795) or
            destination_shape != (801, 801) or shift != (3, 3) or spacing_m != 125e-6 or
            not np.array_equal(native_origin, [-0.049625, -0.049625]) or
            not np.array_equal(destination_origin, [-0.05, -0.05])):
        raise ValueError("pinned native/common grid contract changed")

    grid_cells = int(np.prod(destination_shape))
    rank = 24
    complex_grid_bytes = grid_cells * rank * np.dtype(np.complex128).itemsize
    native_raw = csc_bytes(native)
    # Conservative simultaneous-array plan: native+common CSC, four full
    # complex rank grids, registry/basis, and one int64 remap index vector.
    planned_live_arrays = int(2 * native_raw + 4 * complex_grid_bytes +
                              native.nnz * np.dtype(np.int64).itemsize +
                              face_to_island.nbytes + face_z_um.nbytes +
                              basis24.nbytes + exact_z_m.nbytes)
    if planned_live_arrays > LIVE_ARRAY_PLAN_CAP_BYTES:
        raise MemoryError(f"planned live arrays {planned_live_arrays} exceed 1.5 GB")

    remap_started = perf_counter()
    common = adapter.remap_columns_integer(native, native_shape, destination_shape, shift)
    remap_s = perf_counter() - remap_started
    expected_rows = ((native.indices.astype(np.int64) // native_shape[1] + shift[0]) * destination_shape[1] +
                     (native.indices.astype(np.int64) % native_shape[1] + shift[1]))
    remap_exact = bool(
        common.shape == (grid_cells, native.shape[1]) and common.nnz == native.nnz and
        np.array_equal(common.data, native.data) and np.array_equal(common.indptr, native.indptr) and
        np.array_equal(common.indices, expected_rows))
    common_path = OUTPUT / "common-grid-to-unique-island-map.npz"
    atomic_sparse(common_path, common)
    saved_common = sparse.load_npz(common_path).tocsc()
    common_readback = bool(
        np.array_equal(saved_common.indptr, common.indptr) and
        np.array_equal(saved_common.indices, common.indices) and
        np.array_equal(saved_common.data, common.data))
    del native, expected_rows
    gc.collect()

    face_index = np.arange(len(face_to_island), dtype=np.float64)
    face_charge = np.sin(face_index * 0.017) + 1j * np.cos(face_index * 0.011)
    unique24 = adapter.face_to_unique_rank(face_charge, face_to_island, face_z_row, basis24, common.shape[1], 24)
    unique20 = adapter.face_to_unique_rank(face_charge, face_to_island, face_z_row, basis24, common.shape[1], 20)
    rank_source_prefix_error = relative(unique24[:, :20], unique20)
    spread20 = np.asarray(common @ unique20)
    spread_started = perf_counter()
    spread24 = adapter.spread_faces(common, face_charge, face_to_island, face_z_row, basis24, 24)
    spread_s = perf_counter() - spread_started
    rank_grid_prefix_error = relative(spread24[:, :20], spread20)
    del spread20, unique20
    gc.collect()

    grid_index = np.arange(grid_cells, dtype=np.float64)
    field24 = np.empty((grid_cells, 24), dtype=np.complex128)
    for column in range(24):
        field24[:, column] = (np.sin(grid_index * (0.0011 + column * 0.000013)) +
                              1j * np.cos(grid_index * (0.0017 + column * 0.000019)))
    gather_started = perf_counter()
    face_potential24 = adapter.gather_faces(common, field24, face_to_island, face_z_row, basis24, 24)
    gather_s = perf_counter() - gather_started
    lhs = np.sum(field24 * spread24)
    rhs = np.sum(face_potential24 * face_charge)
    ordinary_error = float(abs(lhs - rhs) / max(abs(lhs), abs(rhs), 1e-300))
    unique_field24 = np.asarray(common.T @ field24)
    face_potential20 = adapter.gather_faces(common, field24[:, :20], face_to_island, face_z_row, basis24, 20)
    expected_face20 = np.sum(basis24[face_z_row, :20] * unique_field24[face_to_island, :20], axis=1)
    rank_gather_prefix_error = relative(face_potential20, expected_face20)
    del spread24
    gc.collect()

    selected_faces = np.asarray([4202, 4210], dtype=np.int64)
    selected_island = int(face_to_island[selected_faces[0]])
    if selected_island != int(face_to_island[selected_faces[1]]) or face_z_um[selected_faces].tolist() != [1780.0, 1812.0]:
        raise ValueError("selected different-height shared island changed")
    selected_charge = np.asarray([0.625 - 0.75j, -0.25 + 1.0j])
    selected_coefficients = np.sum(selected_charge[:, None] * basis24[face_z_row[selected_faces], :], axis=0)
    selected_unique = np.zeros((common.shape[1], 24), dtype=np.complex128)
    selected_unique[selected_island] = selected_coefficients
    selected_column = common[:, selected_island]
    decomposition_error = 0.0
    for column in range(24):
        actual = np.asarray(common @ selected_unique[:, column]).ravel()
        expected = selected_column.toarray().ravel() * selected_coefficients[column]
        decomposition_error = max(decomposition_error, relative(actual, expected))
    selected_gather = adapter.gather_faces(
        common, field24, face_to_island[selected_faces], face_z_row[selected_faces], basis24, 24)
    column_field = np.asarray(selected_column.T @ field24).reshape(-1)
    expected_selected_gather = np.sum(basis24[face_z_row[selected_faces], :] * column_field[None, :], axis=1)
    selected_gather_error = relative(selected_gather, expected_selected_gather)
    independent_height_rows = bool(face_z_row[selected_faces[0]] != face_z_row[selected_faces[1]] and
                                   np.linalg.matrix_rank(basis24[face_z_row[selected_faces], :]) == 2)

    adapter_path = OUTPUT / "vertical-face-adapter.npz"
    temporary = adapter_path.with_name(adapter_path.name + ".partial.npz")
    np.savez_compressed(
        temporary,
        basis_real_104x24=basis24,
        exact_z_m=exact_z_m,
        singular_values=singular_values,
        face_z_row=face_z_row,
        support_face_z_um=face_z_um,
        support_island_index=face_to_island,
        native_grid_origin_m=native_origin,
        native_grid_shape=np.asarray(native_shape, dtype=np.int64),
        common_grid_origin_m=destination_origin,
        common_grid_shape=np.asarray(destination_shape, dtype=np.int64),
        integer_shift=np.asarray(shift, dtype=np.int64),
        spacing_m=np.asarray([spacing_m]),
    )
    os.replace(temporary, adapter_path)
    with np.load(adapter_path, allow_pickle=False) as saved:
        adapter_readback = bool(
            np.array_equal(saved["basis_real_104x24"], basis24) and
            np.array_equal(saved["exact_z_m"], exact_z_m) and
            np.array_equal(saved["face_z_row"], face_z_row) and
            np.array_equal(saved["support_face_z_um"], face_z_um) and
            np.array_equal(saved["support_island_index"], face_to_island) and
            np.array_equal(saved["integer_shift"], shift))

    elapsed = perf_counter() - started
    _, peak_working_set = pilot.process_memory()
    gates = {
        "all_inputs_and_helper_pinned": True,
        "planned_live_arrays_below_1_5GB_before_allocation": planned_live_arrays <= LIVE_ARRAY_PLAN_CAP_BYTES,
        "native_to_common_exact_integer_remap": remap_exact and common_readback,
        "all_face_heights_exactly_match_fixed_basis_rows": np.array_equal(exact_z_m[face_z_row], face_z_um * 1e-6),
        "basis_is_fixed_real_104x24": basis24.shape == (104, 24) and np.isrealobj(basis24),
        "rank20_is_unchanged_prefix_of_rank24": rank_source_prefix_error == 0.0 and rank_grid_prefix_error == 0.0 and rank_gather_prefix_error == 0.0,
        "same_island_different_height_faces_remain_independent": independent_height_rows,
        "selected_shared_island_spread_direct_decomposition": decomposition_error <= 2e-14,
        "selected_shared_island_gather_direct_decomposition": selected_gather_error <= 2e-14,
        "whole_map_ordinary_bilinear_transpose": ordinary_error <= 2e-13,
        "serialized_adapter_exact_readback": adapter_readback,
        "elapsed_below_120_seconds": elapsed <= TIME_CAP_S,
    }
    gates = {name: bool(value) for name, value in gates.items()}
    if not all(gates.values()):
        raise ValueError(f"vertical sparse action gates failed: {gates}")

    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_RETAINED_GC_VERTICAL_SPARSE_ACTION",
        "driver_sha256": digest(Path(__file__)),
        "helper_sha256": PINS[HELPER],
        "inputs": {str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        "artifacts": {str(common_path.relative_to(ROOT)): digest(common_path), str(adapter_path.relative_to(ROOT)): digest(adapter_path)},
        "counts": {
            "faces": len(face_to_island), "unique_xy_islands": common.shape[1],
            "exact_basis_heights": len(exact_z_m), "used_face_heights": len(np.unique(face_z_row)),
            "rank20": 20, "rank24": 24, "common_grid_cells": grid_cells,
            "common_map_nnz": int(common.nnz),
        },
        "grid_binding": {
            "native_origin_m": native_origin.tolist(), "native_shape": list(native_shape),
            "common_origin_m": destination_origin.tolist(), "common_shape": list(destination_shape),
            "spacing_m": spacing_m, "integer_shift": list(shift),
            "mapping": "native(ix,iy) -> common(ix+3,iy+3), exact coefficient reindex with no interpolation",
            "coverage_scope": common_bounds["scope"],
        },
        "selected_shared_island": {
            "face_indices": selected_faces.tolist(), "island_index": selected_island,
            "face_z_um": face_z_um[selected_faces].tolist(),
            "basis_row_indices": face_z_row[selected_faces].tolist(),
            "basis_row_rank": 2,
        },
        "metrics": {
            "elapsed_s": elapsed, "remap_s": remap_s, "spread24_s": spread_s, "gather24_s": gather_s,
            "planned_live_array_bytes": planned_live_arrays,
            "process_initial_working_set_bytes": initial_working_set,
            "process_peak_working_set_bytes": peak_working_set,
            "common_map_raw_csc_bytes": csc_bytes(common),
            "common_map_disk_bytes": common_path.stat().st_size,
            "adapter_disk_bytes": adapter_path.stat().st_size,
            "rank20_source_prefix_relative": rank_source_prefix_error,
            "rank20_grid_prefix_relative": rank_grid_prefix_error,
            "rank20_gather_prefix_relative": rank_gather_prefix_error,
            "selected_spread_decomposition_relative": decomposition_error,
            "selected_gather_decomposition_relative": selected_gather_error,
            "whole_map_ordinary_bilinear_relative": ordinary_error,
        },
        "gates": gates,
        "scope": "Sparse source/gather conversion for the fixed real rank24 vertical basis and current qualified native charge faces. No point array, Green kernel, Hankel/FFT initialization, direct/singular term, FMM, L14/L25 completion, or board solve.",
    }


def atomic_json(path: Path, value: dict) -> None:
    payload = json.dumps(value, indent=2, allow_nan=False) + "\n"
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    OUTPUT.mkdir(exist_ok=False)
    (OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        result = run()
    except Exception:
        result = {"program": PROGRAM, "version": VERSION,
                  "status": "STOP_RETAINED_GC_VERTICAL_SPARSE_ACTION",
                  "driver_sha256": digest(Path(__file__)), "traceback": traceback.format_exc()}
        atomic_json(OUTPUT / "result.json", result)
        raise
    atomic_json(OUTPUT / "result.json", result)
    print(json.dumps({"status": result["status"], "counts": result["counts"], "metrics": result["metrics"]}), flush=True)


if __name__ == "__main__":
    main()
