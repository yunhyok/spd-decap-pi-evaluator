"""SPD Decap PI Evaluator v0.23.1: streamed native-face XY stencil pilot.

The immutable 795x795 grid is native-face-only.  Existing columns are reused
without rewriting, and one new island column is built in bounded point batches.
"""
from __future__ import annotations

from hashlib import sha256
import ctypes
import json
import math
import os
from pathlib import Path
from time import perf_counter
import traceback
import zipfile

import numpy as np
from scipy import sparse
from shapely import wkb as shapely_wkb

from astra_layered_charge_action import _cubic_weights


PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
OUTPUT = RESEARCH / "astra-retained-gc-streamed-sparse-grid-map-20260912-01"
ARTIFACT = RESEARCH / "astra-retained-gc-exact-polygon-support-20260912-11" / "retained-gc-exact-polygon-support.npz"
QUALIFICATION = RESEARCH / "astra-retained-gc-exact-polygon-support-20260912-12-qualification" / "result.json"
MANIFEST = RESEARCH / "astra-retained-gc-exact-polygon-support-20260912-11" / "manifest.json"
ACCEPTED_SOURCE = ROOT / "tools" / "research" / "qualify_astra_retained_gc_sparse_grid_map.py"
ACCEPTED_RESULT = RESEARCH / "astra-retained-gc-sparse-grid-map-20260912-05" / "result.json"
ACCEPTED_MAP = ACCEPTED_RESULT.with_name("grid-to-selected-face-map.npz")
CUBIC_SOURCE = ROOT / "tools" / "research" / "astra_layered_charge_action.py"
CHECKPOINTS = RESEARCH / "astra-retained-gc-exact-polygon-support-20260912-09-checkpoints"
NEW_ISLAND = "spd-surface-island:91e78f77e4a9196f2836b442"
NEW_CHECKPOINT = CHECKPOINTS / "5ff9e97cfeb3bd4eb73786884345c7d9308293f0a1f904e60eb20d9cc297329f.npz"
PINS = {
    ARTIFACT: "8aa88cfc280a2ea011b572a7571a8a166f8d48cb09404fd7c365894ecf16464d",
    QUALIFICATION: "209b1780c98de9606a3b67b3a2165f77f85ba8ef1cfddd57bc4edc45789226db",
    MANIFEST: "ef14e6d8ddce70dc3a6b2d0ecaddb609bfc5269c54c4ba5907a473e4b9355178",
    ACCEPTED_SOURCE: "a7682f7c31fde5a7ffc01acb223ff76eb4a74f3817d4199c15dc7c92ff48e32d",
    ACCEPTED_RESULT: "3e07f2865e18c46395ef3d9b58ed7f671b6b8998977f1bf58a3938352f8b30bd",
    ACCEPTED_MAP: "7d8bba97e941aef5bd3e4fee5c827ac8a13b3facb2bae610c46a813243b63132",
    CUBIC_SOURCE: "210d5627323262480625b8fac806ddf7484d85e7f19662e1503b8c787e4b1e81",
    NEW_CHECKPOINT: "72b2afbfc74d32781459b9cce3603da46dd811958e97b665c62f361803fa948b",
}
BATCH = 65_536


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def process_memory() -> tuple[int, int]:
    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    current_process.restype = ctypes.c_void_p
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), ctypes.c_ulong]
    get_memory.restype = ctypes.c_int
    ok = get_memory(current_process(), ctypes.byref(counters), counters.cb)
    if not ok:
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)


def npy_shape_in_npz(path: Path, member: str) -> tuple[int, ...]:
    with zipfile.ZipFile(path) as archive, archive.open(member + ".npy") as stream:
        version = np.lib.format.read_magic(stream)
        if version == (1, 0):
            shape, _, _ = np.lib.format.read_array_header_1_0(stream)
        elif version in ((2, 0), (3, 0)):
            shape, _, _ = np.lib.format.read_array_header_2_0(stream)
        else:
            raise ValueError(f"unsupported NPY header version: {version}")
    return tuple(map(int, shape))


def stencil(points_m: np.ndarray, origin_m: np.ndarray, shape: tuple[int, int]):
    scaled = (points_m - origin_m) / 125e-6
    cell = np.floor(scaled).astype(np.int64)
    offsets = np.arange(-1, 3)
    ix = cell[:, 0, None] + offsets
    iy = cell[:, 1, None] + offsets
    if not (np.all(ix >= 0) and np.all(iy >= 0) and np.all(ix < shape[0]) and np.all(iy < shape[1])):
        raise ValueError("point falls outside the pinned native-only grid")
    return ix, iy, _cubic_weights(scaled[:, 0] - cell[:, 0]), _cubic_weights(scaled[:, 1] - cell[:, 1])


def streamed_column(xy_um: np.ndarray, weights: np.ndarray, origin_m: np.ndarray,
                    shape: tuple[int, int]):
    cells = int(np.prod(shape))
    accumulated = np.zeros(cells, dtype=np.float64)
    _, peak_working_set = process_memory()
    maximum_batch_arrays = 0
    started = perf_counter()
    for first in range(0, len(weights), BATCH):
        last = min(first + BATCH, len(weights))
        points_m = xy_um[first:last] * 1e-6
        weight = weights[first:last]
        ix, iy, wx, wy = stencil(points_m, origin_m, shape)
        fixed_arrays = sum(value.nbytes for value in (points_m, weight, ix, iy, wx, wy))
        for x_offset in range(4):
            for y_offset in range(4):
                index = ix[:, x_offset] * shape[1] + iy[:, y_offset]
                data = weight * wx[:, x_offset] * wy[:, y_offset]
                np.add.at(accumulated, index, data)
                maximum_batch_arrays = max(maximum_batch_arrays, fixed_arrays + index.nbytes + data.nbytes)
        _, peak_working_set = process_memory()
    column = sparse.csc_matrix(accumulated[:, None])
    return column, accumulated, perf_counter() - started, peak_working_set, maximum_batch_arrays


def direct_apply(xy_um: np.ndarray, weights: np.ndarray, origin_m: np.ndarray,
                 shape: tuple[int, int], charge: complex, field: np.ndarray):
    output = np.zeros(int(np.prod(shape)), dtype=np.complex128)
    gathered = 0j
    started = perf_counter()
    for first in range(0, len(weights), BATCH):
        last = min(first + BATCH, len(weights))
        points_m = xy_um[first:last] * 1e-6
        weight = weights[first:last]
        ix, iy, wx, wy = stencil(points_m, origin_m, shape)
        for x_offset in range(4):
            for y_offset in range(4):
                index = ix[:, x_offset] * shape[1] + iy[:, y_offset]
                coefficient = weight * wx[:, x_offset] * wy[:, y_offset]
                np.add.at(output, index, charge * coefficient)
                gathered += np.sum(coefficient * field[index])
    return output, gathered, perf_counter() - started


def run() -> dict:
    started = perf_counter()
    initial_working_set, _ = process_memory()
    for path, expected in PINS.items():
        if digest(path) != expected:
            raise ValueError(f"pinned input changed: {path}")
    accepted_result = json.loads(ACCEPTED_RESULT.read_text(encoding="utf-8"))
    if accepted_result["status"] != "PASS_RETAINED_GC_SELECTED_SPARSE_GRID_MAP":
        raise ValueError("accepted partition did not pass")
    origin = np.asarray(accepted_result["grid"]["origin_m"], dtype=np.float64)
    shape = tuple(accepted_result["grid"]["shape"])
    if shape != (795, 795) or accepted_result["grid"]["spacing_m"] != 125e-6:
        raise ValueError("accepted native-only grid changed")
    accepted = sparse.load_npz(ACCEPTED_MAP).tocsc()
    if accepted.shape != (int(np.prod(shape)), 2):
        raise ValueError("accepted two-column partition shape changed")

    with np.load(ARTIFACT, allow_pickle=False) as saved:
        island_ids = json.loads(saved["island_ids_json_utf8"].tobytes().decode("utf-8"))
        support_island = saved["support_island_index"]
        support_z = saved["support_face_z_um"]
        offsets = saved["island_wkb_offsets"]
        blob = saved["island_wkb_bytes"]
    island_counts = np.bincount(support_island, minlength=len(island_ids))
    maximum_faces_per_island = int(island_counts.max())
    if maximum_faces_per_island != 2 or np.any(island_counts < 1):
        raise ValueError("face-to-island multiplicity changed")
    island_index = island_ids.index(NEW_ISLAND)
    new_supports = np.flatnonzero(support_island == island_index)
    if new_supports.tolist() != [4202, 4210] or support_z[new_supports].tolist() != [1780.0, 1812.0]:
        raise ValueError("selected shared-island face metadata changed")
    raw_wkb = blob[offsets[island_index]:offsets[island_index + 1]].tobytes()
    geometry = shapely_wkb.loads(raw_wkb)
    if geometry.wkb != raw_wkb:
        raise ValueError("saved WKB round trip changed")
    expected_name = sha256((NEW_ISLAND + geometry.wkb_hex).encode()).hexdigest() + ".npz"
    if NEW_CHECKPOINT.name != expected_name:
        raise ValueError("selected checkpoint is not the WKB-keyed island rule")
    with np.load(NEW_CHECKPOINT, allow_pickle=False) as rule:
        xy_um = rule["xy"]
        weights = rule["weights"]
    if len(weights) != 1_219_572 or abs(math.fsum(map(float, weights)) - 1.0) > 2e-15:
        raise ValueError("selected shared-island rule changed")

    new_column, dense_column, build_s, peak_working_set, maximum_batch_arrays = streamed_column(
        xy_um, weights, origin, shape)
    compensated_mass = math.fsum(map(float, new_column.data))
    field = (np.sin(np.arange(new_column.shape[0]) * 0.0037) +
             1j * np.cos(np.arange(new_column.shape[0]) * 0.0061))
    charge = np.asarray([0.75 + 0.25j, -1.125 + 0.5j, 0.625 - 0.75j, -0.25 + 1.0j])

    apply_started = perf_counter()
    partitioned_spread = accepted @ charge[:2] + new_column @ np.asarray([charge[2] + charge[3]])
    accepted_gather = accepted.T @ field
    new_gather = complex((new_column.T @ field)[0])
    partitioned_gather = np.r_[accepted_gather, new_gather, new_gather]
    sparse_apply_s = perf_counter() - apply_started

    direct_new_spread, direct_new_gather, direct_s = direct_apply(
        xy_um, weights, origin, shape, charge[2] + charge[3], field)
    reference_spread = accepted @ charge[:2] + direct_new_spread
    reference_gather = np.r_[accepted_gather, direct_new_gather, direct_new_gather]
    spread_error = float(np.linalg.norm(partitioned_spread - reference_spread) /
                         max(np.linalg.norm(reference_spread), 1e-300))
    gather_error = float(np.linalg.norm(partitioned_gather - reference_gather) /
                         max(np.linalg.norm(reference_gather), 1e-300))
    bilinear_error = float(abs(field @ partitioned_spread - partitioned_gather @ charge) /
                           max(abs(field @ partitioned_spread), abs(partitioned_gather @ charge), 1e-300))
    individual_errors = []
    for value in charge[2:]:
        sparse_value = new_column @ np.asarray([value])
        direct_value, _, _ = direct_apply(xy_um, weights, origin, shape, value, field)
        individual_errors.append(float(np.linalg.norm(sparse_value - direct_value) /
                                       max(np.linalg.norm(direct_value), 1e-300)))
    _, peak_working_set = process_memory()

    unique_point_count = 0
    checkpoint_names = []
    for index, island_id in enumerate(island_ids):
        polygon = shapely_wkb.loads(blob[offsets[index]:offsets[index + 1]].tobytes())
        name = sha256((island_id + polygon.wkb_hex).encode()).hexdigest() + ".npz"
        checkpoint = CHECKPOINTS / name
        if not checkpoint.is_file():
            raise ValueError(f"missing qualified island checkpoint: {name}")
        unique_point_count += npy_shape_in_npz(checkpoint, "weights")[0]
        checkpoint_names.append(name)
    face_point_count = json.loads(QUALIFICATION.read_text(encoding="utf-8"))["counts"]["quadrature_points"]
    if unique_point_count != 20_363_317 or face_point_count != 33_967_296:
        raise ValueError("qualified unique/face point census changed")

    csc_bytes = new_column.data.nbytes + new_column.indices.nbytes + new_column.indptr.nbytes
    accepted_csc_bytes = accepted.data.nbytes + accepted.indices.nbytes + accepted.indptr.nbytes
    live_array_bound = (xy_um.nbytes + weights.nbytes + dense_column.nbytes + csc_bytes +
                        accepted_csc_bytes + field.nbytes + partitioned_spread.nbytes +
                        reference_spread.nbytes + maximum_batch_arrays)
    new_map_path = OUTPUT / "new-island-column-partition.npz"
    sparse.save_npz(new_map_path, new_column, compressed=True)
    registry_path = OUTPUT / "face-registry.npz"
    logical_supports = np.asarray([0, 1011, *new_supports], dtype=np.int64)
    np.savez_compressed(
        registry_path,
        logical_support_index=logical_supports,
        face_to_partition=np.asarray([0, 0, 1, 1], dtype=np.int8),
        face_to_partition_column=np.asarray([0, 1, 0, 0], dtype=np.int64),
        face_z_um=np.asarray([375.0, 475.0, *support_z[new_supports]], dtype=np.float64),
        accepted_partition_relative_path=np.asarray([str(ACCEPTED_MAP.relative_to(ROOT))]),
        new_partition_relative_path=np.asarray([str(new_map_path.relative_to(ROOT))]),
        unique_island_id=np.asarray([NEW_ISLAND]),
    )
    saved_column = sparse.load_npz(new_map_path).tocsc()
    with np.load(registry_path, allow_pickle=False) as saved_registry:
        registry_round_trip = bool(
            np.array_equal(saved_registry["logical_support_index"], logical_supports) and
            np.array_equal(saved_registry["face_to_partition"], np.asarray([0, 0, 1, 1], dtype=np.int8)) and
            np.array_equal(saved_registry["face_to_partition_column"], np.asarray([0, 1, 0, 0], dtype=np.int64)) and
            np.array_equal(saved_registry["face_z_um"], np.asarray([375.0, 475.0, *support_z[new_supports]])))
    partition_round_trip = bool(saved_column.shape == new_column.shape and
                                np.array_equal(saved_column.indptr, new_column.indptr) and
                                np.array_equal(saved_column.indices, new_column.indices) and
                                np.array_equal(saved_column.data, new_column.data))
    elapsed = perf_counter() - started
    gates = {
        "accepted_two_columns_reused_unchanged": digest(ACCEPTED_MAP) == PINS[ACCEPTED_MAP],
        "actual_maximum_two_faces_per_island": maximum_faces_per_island == 2,
        "shared_island_xy_computed_once": len(new_supports) == 2,
        "checkpoint_wkb_and_face_metadata_identity": True,
        "compensated_mass_2e_15": abs(compensated_mass - 1.0) <= 2e-15,
        "new_combined_spread_direct_equivalence": spread_error <= 2e-14,
        "new_face_gather_direct_equivalence": gather_error <= 2e-14,
        "each_shared_face_independently_matches": max(individual_errors) <= 2e-14,
        "ordinary_bilinear_transpose": bilinear_error <= 2e-14,
        "compressed_partition_and_registry_round_trip": partition_round_trip and registry_round_trip,
        "bounded_120_seconds": elapsed <= 120.0,
        "no_global_point_or_coo_materialization": True,
    }
    if not all(gates.values()):
        raise ValueError(f"streamed sparse pilot gates failed: {gates}")

    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_RETAINED_GC_STREAMED_SPARSE_GRID_PILOT",
        "driver_sha256": digest(Path(__file__)),
        "inputs": {str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        "artifacts": {str(new_map_path.relative_to(ROOT)): digest(new_map_path),
                      str(registry_path.relative_to(ROOT)): digest(registry_path)},
        "grid_binding": {
            "kind": "NATIVE_FACE_ONLY_795_GRID",
            "origin_m": origin.tolist(),
            "shape": list(shape),
            "spacing_m": 125e-6,
            "restriction": "Do not combine with another grid unless physical nodes are related by an exact integer-index remap; no interpolation is authorized.",
        },
        "face_reuse": {
            "face_specific_supports": len(support_island),
            "unique_islands": len(island_ids),
            "maximum_faces_per_island": maximum_faces_per_island,
            "islands_with_two_faces": int(np.count_nonzero(island_counts == 2)),
            "islands_with_one_face": int(np.count_nonzero(island_counts == 1)),
            "three_face_example_available": False,
            "selected_shared_island": NEW_ISLAND,
            "selected_support_indices": new_supports.tolist(),
            "selected_face_z_um": support_z[new_supports].tolist(),
            "selected_rule_points": len(weights),
        },
        "counts": {"logical_faces": 4, "partition_files": 2, "new_unique_xy_columns": 1,
                   "new_column_nnz": int(new_column.nnz), "unique_rule_points_all_islands": unique_point_count,
                   "face_rule_points_without_reuse": face_point_count},
        "metrics": {
            "build_new_partition_s": build_s,
            "sparse_partitioned_apply_s": sparse_apply_s,
            "direct_new_island_apply_s": direct_s,
            "new_partition_csc_bytes": int(csc_bytes),
            "accepted_partition_csc_bytes_in_memory": int(accepted_csc_bytes),
            "maximum_batch_explicit_arrays_bytes": int(maximum_batch_arrays),
            "explicit_live_array_accounting_estimate_bytes": int(live_array_bound),
            "process_initial_working_set_bytes": int(initial_working_set),
            "process_peak_working_set_bytes": int(peak_working_set),
            "compensated_mass_abs_error": abs(compensated_mass - 1.0),
            "spread_direct_relative": spread_error,
            "gather_direct_relative": gather_error,
            "individual_shared_face_spread_relative": individual_errors,
            "ordinary_bilinear_transpose_relative": bilinear_error,
            "measured_unique_point_reuse_ratio": unique_point_count / face_point_count,
            "estimated_all_unique_island_build_s_from_selected_point_rate": build_s * unique_point_count / len(weights),
            "estimated_all_columns_uncompressed_bytes_from_selected_nnz_rate":
                int(csc_bytes * unique_point_count / len(weights)),
        },
        "estimate_limits": "Whole-set time/storage estimates scale one large selected island by point count. Explicit array accounting includes named arrays but can double-count views and omit temporary ufunc arrays; measured process peak working set is authoritative for this pilot. Grid-node saturation and geometry vary, so whole-set values are planning estimates, not measurements or launch approval.",
        "gates": gates,
        "elapsed_s": elapsed,
        "scope": "Exact algebraic reuse of the unchanged cubic smooth-remainder XY stencil only. No point downsampling, global COO, kernel, direct/singular term, point self, Green action, SPD read, or board solve.",
    }


def main() -> None:
    OUTPUT.mkdir(exist_ok=False)
    (OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        result = run()
    except Exception:
        result = {"program": PROGRAM, "version": VERSION,
                  "status": "STOP_RETAINED_GC_STREAMED_SPARSE_GRID_PILOT",
                  "driver_sha256": digest(Path(__file__)), "traceback": traceback.format_exc()}
        (OUTPUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise
    (OUTPUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "counts": result["counts"], "metrics": result["metrics"]}), flush=True)


if __name__ == "__main__":
    main()
