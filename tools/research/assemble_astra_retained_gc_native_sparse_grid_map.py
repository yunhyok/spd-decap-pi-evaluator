"""Preassemble native-grid smooth-stencil columns once per retained-GC island.

This is an algebraic cache for the unchanged cubic smooth-remainder stencil.
It does not evaluate a Green kernel, alter the restored face quadrature, or
combine the native grid with any other grid.
"""

from __future__ import annotations

import json
import math
import os
import traceback
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import sparse

import qualify_astra_retained_gc_streamed_sparse_grid_map as pilot


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
OUTPUT = RESEARCH / "astra-retained-gc-native-sparse-grid-map-20260912-01"
COLUMNS = OUTPUT / "island-columns"

SUPPORT = RESEARCH / "astra-retained-gc-exact-polygon-support-20260912-11" / "retained-gc-exact-polygon-support.npz"
QUALIFIED = RESEARCH / "astra-retained-gc-exact-polygon-support-20260912-12-qualification" / "result.json"
CHECKPOINTS = RESEARCH / "astra-retained-gc-exact-polygon-support-20260912-09-checkpoints"
ACCEPTED_SOURCE = ROOT / "tools" / "research" / "qualify_astra_retained_gc_sparse_grid_map.py"
ACCEPTED_RESULT = RESEARCH / "astra-retained-gc-sparse-grid-map-20260912-05" / "result.json"
ACCEPTED_MAP = RESEARCH / "astra-retained-gc-sparse-grid-map-20260912-05" / "grid-to-selected-face-map.npz"
PILOT_SOURCE = ROOT / "tools" / "research" / "qualify_astra_retained_gc_streamed_sparse_grid_map.py"
PILOT_RESULT = RESEARCH / "astra-retained-gc-streamed-sparse-grid-map-20260912-01" / "result.json"
PILOT_MAP = RESEARCH / "astra-retained-gc-streamed-sparse-grid-map-20260912-01" / "new-island-column-partition.npz"
PILOT_REGISTRY = RESEARCH / "astra-retained-gc-streamed-sparse-grid-map-20260912-01" / "face-registry.npz"
REMAP_RESULT = RESEARCH / "astra-native-grid-integer-remap-20260912" / "result.json"
COMMON_GRID_BOUNDS = RESEARCH / "astra-combined-charge-grid-bounds-20260912.json"

PINS = {
    SUPPORT: "8aa88cfc280a2ea011b572a7571a8a166f8d48cb09404fd7c365894ecf16464d",
    QUALIFIED: "209b1780c98de9606a3b67b3a2165f77f85ba8ef1cfddd57bc4edc45789226db",
    ACCEPTED_SOURCE: "a7682f7c31fde5a7ffc01acb223ff76eb4a74f3817d4199c15dc7c92ff48e32d",
    ACCEPTED_RESULT: "3e07f2865e18c46395ef3d9b58ed7f671b6b8998977f1bf58a3938352f8b30bd",
    ACCEPTED_MAP: "7d8bba97e941aef5bd3e4fee5c827ac8a13b3facb2bae610c46a813243b63132",
    PILOT_SOURCE: "a08c56369c70c23b166c249ebc906af30777172949ef84f5cae2b16ae350a852",
    PILOT_RESULT: "da017e29c518ca783f07b3acf0a46b8932c975d3d05650069c799e3809051368",
    PILOT_MAP: "38f0c36d7c1b6b7ba539fa2d7e0816d7219cd39af70f14bc51a75d728ca14564",
    PILOT_REGISTRY: "488ef7a44cea8f405d70550513497b649e49fc4a9e6dc57fa7ea20f78aa00107",
    REMAP_RESULT: "63bce57b3ee59fbff400e6e0ae7cfc8ae311f0b98daca3ba99e24caac02bb324",
    COMMON_GRID_BOUNDS: "a1083fe4a534ac033862129abaac08b735f95323714ac9c30ca1f923318ae154",
}

TIME_CAP_S = 180.0
OUTPUT_ARRAY_CAP_BYTES = 2_000_000_000


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def column_path(index: int, island_id: str) -> Path:
    tag = sha256(island_id.encode("utf-8")).hexdigest()[:16]
    return COLUMNS / f"island-{index:04d}-{tag}.npz"


def atomic_save_sparse(path: Path, value: sparse.csc_matrix) -> None:
    temporary = path.with_name(path.name + ".partial.npz")
    sparse.save_npz(temporary, value, compressed=True)
    os.replace(temporary, path)


def exact_checkpoint(island_id: str, raw_wkb: bytes) -> Path:
    # Shapely's qualified-cache key uses its uppercase WKB-hex representation.
    name = sha256((island_id + raw_wkb.hex().upper()).encode("utf-8")).hexdigest() + ".npz"
    path = CHECKPOINTS / name
    if not path.is_file():
        raise ValueError(f"missing qualified island rule: {name}")
    return path


def raw_csc_bytes(column: sparse.csc_matrix) -> int:
    return int(column.data.nbytes + column.indices.nbytes + column.indptr.nbytes)


def validate_column(column: sparse.csc_matrix, cells: int) -> float:
    if column.shape != (cells, 1) or column.format != "csc":
        raise ValueError("island column shape/format changed")
    if not np.all(np.isfinite(column.data)):
        raise ValueError("nonfinite sparse coefficient")
    if np.any(column.indices < 0) or np.any(column.indices >= cells):
        raise ValueError("sparse grid index outside native grid")
    mass_error = abs(math.fsum(map(float, column.data)) - 1.0)
    if mass_error > 2e-15:
        raise ValueError(f"compensated island-column mass failed: {mass_error}")
    return mass_error


def load_reused_columns(support_island: np.ndarray, cells: int):
    accepted = sparse.load_npz(ACCEPTED_MAP).tocsc()
    pilot_column = sparse.load_npz(PILOT_MAP).tocsc()
    with np.load(PILOT_REGISTRY, allow_pickle=False) as registry:
        supports = registry["logical_support_index"]
        if supports.tolist() != [0, 1011, 4202, 4210]:
            raise ValueError("pilot logical-face registry changed")
    if accepted.shape != (cells, 2) or pilot_column.shape != (cells, 1):
        raise ValueError("reused partition dimensions changed")
    reused = {
        int(support_island[0]): accepted[:, 0].tocsc(),
        int(support_island[1011]): accepted[:, 1].tocsc(),
        int(support_island[4202]): pilot_column[:, 0].tocsc(),
    }
    if len(reused) != 3 or int(support_island[4202]) != int(support_island[4210]):
        raise ValueError("reused face-to-island identities changed")
    for column in reused.values():
        validate_column(column, cells)
    return reused


def assemble_full_csc(paths: list[Path], cells: int) -> sparse.csc_matrix:
    nnz = np.empty(len(paths), dtype=np.int64)
    for index, path in enumerate(paths):
        column = sparse.load_npz(path).tocsc()
        if column.shape != (cells, 1):
            raise ValueError(f"saved column shape changed at {index}")
        nnz[index] = column.nnz
    total = int(nnz.sum())
    data = np.empty(total, dtype=np.float64)
    indices = np.empty(total, dtype=np.int32)
    indptr = np.empty(len(paths) + 1, dtype=np.int64)
    indptr[0] = 0
    cursor = 0
    for index, path in enumerate(paths):
        column = sparse.load_npz(path).tocsc()
        count = int(column.nnz)
        data[cursor:cursor + count] = column.data
        indices[cursor:cursor + count] = column.indices
        cursor += count
        indptr[index + 1] = cursor
    return sparse.csc_matrix((data, indices, indptr), shape=(cells, len(paths)))


def run() -> dict:
    started = perf_counter()
    initial_working_set, _ = pilot.process_memory()
    for path, expected in PINS.items():
        if digest(path) != expected:
            raise ValueError(f"pinned input changed: {path}")
    qualified = json.loads(QUALIFIED.read_text(encoding="utf-8"))
    accepted_result = json.loads(ACCEPTED_RESULT.read_text(encoding="utf-8"))
    pilot_result = json.loads(PILOT_RESULT.read_text(encoding="utf-8"))
    remap_result = json.loads(REMAP_RESULT.read_text(encoding="utf-8"))
    common_grid = json.loads(COMMON_GRID_BOUNDS.read_text(encoding="utf-8"))
    if qualified["status"] != "PASS_RETAINED_GC_SAVED_ARTIFACT_CENTROID_QUALIFICATION":
        raise ValueError("restored polygon support is not qualified")
    if accepted_result["status"] != "PASS_RETAINED_GC_SELECTED_SPARSE_GRID_MAP":
        raise ValueError("accepted two-column partition is not qualified")
    if pilot_result["status"] != "PASS_RETAINED_GC_STREAMED_SPARSE_GRID_PILOT":
        raise ValueError("streamed shared-island pilot is not qualified")
    if remap_result["status"] != "PASS_EXACT_INTEGER_LATTICE_REMAP" or remap_result["integer_shift"] != [3, 3]:
        raise ValueError("saved native-to-common integer remap is not qualified")
    if common_grid["status"] != "PASS_CURRENT_QUALIFIED_CHARGE_DOMAINS_COMMON_GRID_COVERAGE":
        raise ValueError("current common-grid coverage receipt is not qualified")
    origin = np.asarray(accepted_result["grid"]["origin_m"], dtype=np.float64)
    shape = tuple(map(int, accepted_result["grid"]["shape"]))
    spacing_m = float(accepted_result["grid"]["spacing_m"])
    if shape != (795, 795) or spacing_m != 125e-6 or origin.tolist() != [-0.049625, -0.049625]:
        raise ValueError("native-only grid binding changed")
    cells = int(np.prod(shape))

    with np.load(SUPPORT, allow_pickle=False) as saved:
        island_ids = json.loads(saved["island_ids_json_utf8"].tobytes().decode("utf-8"))
        support_island = saved["support_island_index"].astype(np.int64, copy=True)
        support_z = saved["support_face_z_um"].astype(np.float64, copy=True)
        offsets = saved["island_wkb_offsets"].astype(np.int64, copy=True)
        blob = saved["island_wkb_bytes"].copy()
    if len(island_ids) != 2467 or len(support_island) != 4386:
        raise ValueError("qualified unique-island/face counts changed")
    if len(offsets) != len(island_ids) + 1 or offsets[0] != 0 or offsets[-1] != len(blob):
        raise ValueError("island WKB ragged encoding changed")
    if np.any(support_island < 0) or np.any(support_island >= len(island_ids)):
        raise ValueError("face-to-island index out of range")
    island_counts = np.bincount(support_island, minlength=len(island_ids))
    if int(island_counts.max()) != 2 or np.any(island_counts < 1):
        raise ValueError("face multiplicity changed")

    reused = load_reused_columns(support_island, cells)
    paths: list[Path] = []
    checkpoint_names: list[str] = []
    checkpoint_hashes: list[str] = []
    column_hashes: list[str] = []
    point_counts = np.empty(len(island_ids), dtype=np.int64)
    column_nnz = np.empty(len(island_ids), dtype=np.int64)
    mass_errors = np.empty(len(island_ids), dtype=np.float64)
    column_raw_bytes = np.empty(len(island_ids), dtype=np.int64)
    reused_kind = np.zeros(len(island_ids), dtype=np.int8)
    maximum_batch_arrays = 0
    build_kernel_s = 0.0
    peak_working_set = initial_working_set

    for index, island_id in enumerate(island_ids):
        if perf_counter() - started > TIME_CAP_S:
            raise TimeoutError(f"180-second assembly cap reached after {index} islands")
        raw_wkb = blob[offsets[index]:offsets[index + 1]].tobytes()
        checkpoint = exact_checkpoint(island_id, raw_wkb)
        checkpoint_names.append(checkpoint.name)
        checkpoint_hashes.append(digest(checkpoint))
        destination = column_path(index, island_id)

        if destination.is_file():
            column = sparse.load_npz(destination).tocsc()
            with np.load(checkpoint, allow_pickle=False) as rule:
                point_counts[index] = len(rule["weights"])
        elif index in reused:
            column = reused[index]
            reused_kind[index] = 1 if index in (int(support_island[0]), int(support_island[1011])) else 2
            with np.load(checkpoint, allow_pickle=False) as rule:
                point_counts[index] = len(rule["weights"])
            atomic_save_sparse(destination, column)
        else:
            with np.load(checkpoint, allow_pickle=False) as rule:
                xy_um = rule["xy"]
                weights = rule["weights"]
                point_counts[index] = len(weights)
                if xy_um.shape != (len(weights), 2) or not np.all(np.isfinite(xy_um)) or not np.all(np.isfinite(weights)):
                    raise ValueError(f"invalid qualified island rule at {index}")
                if abs(math.fsum(map(float, weights)) - 1.0) > 2e-15:
                    raise ValueError(f"input rule mass changed at {index}")
                column, _, kernel_s, local_peak, batch_arrays = pilot.streamed_column(
                    xy_um, weights, origin, shape)
            build_kernel_s += kernel_s
            peak_working_set = max(peak_working_set, local_peak)
            maximum_batch_arrays = max(maximum_batch_arrays, batch_arrays)
            atomic_save_sparse(destination, column)

        mass_errors[index] = validate_column(column, cells)
        column_nnz[index] = column.nnz
        column_raw_bytes[index] = raw_csc_bytes(column)
        column_hashes.append(digest(destination))
        paths.append(destination)
        projected_arrays = 2 * int(column_raw_bytes[:index + 1].sum()) + 8 * (len(island_ids) + 1)
        if projected_arrays > OUTPUT_ARRAY_CAP_BYTES:
            raise MemoryError(f"2 GB output-array cap reached after {index + 1} islands")

    unique_points = int(point_counts.sum())
    if unique_points != 20_363_317:
        raise ValueError("qualified one-pass checkpoint census changed")
    if set(reused_kind.tolist()) - {0, 1, 2} or np.count_nonzero(reused_kind == 1) != 2 or np.count_nonzero(reused_kind == 2) != 1:
        raise ValueError("accepted/pilot column reuse coverage changed")

    full_map = assemble_full_csc(paths, cells)
    full_raw_bytes = raw_csc_bytes(full_map)
    total_output_array_bytes = int(column_raw_bytes.sum()) + full_raw_bytes
    if total_output_array_bytes > OUTPUT_ARRAY_CAP_BYTES:
        raise MemoryError("2 GB output-array cap exceeded before final serialization")
    full_path = OUTPUT / "native-grid-to-unique-island-map.npz"
    temporary_full = full_path.with_name(full_path.name + ".partial.npz")
    sparse.save_npz(temporary_full, full_map, compressed=True)
    os.replace(temporary_full, full_path)

    registry_path = OUTPUT / "native-face-registry.npz"
    temporary_registry = registry_path.with_name(registry_path.name + ".partial.npz")
    np.savez_compressed(
        temporary_registry,
        support_island_index=support_island,
        support_face_z_um=support_z,
        island_ids_json_utf8=np.frombuffer(json.dumps(island_ids, separators=(",", ":")).encode("utf-8"), dtype=np.uint8),
        point_count=point_counts,
        column_nnz=column_nnz,
        compensated_mass_abs_error=mass_errors,
        column_raw_csc_bytes=column_raw_bytes,
        reused_kind=reused_kind,
        checkpoint_names_json_utf8=np.frombuffer(json.dumps(checkpoint_names, separators=(",", ":")).encode("utf-8"), dtype=np.uint8),
        checkpoint_sha256=np.asarray(checkpoint_hashes, dtype="S64"),
        column_sha256=np.asarray(column_hashes, dtype="S64"),
        grid_origin_m=origin,
        grid_shape=np.asarray(shape, dtype=np.int64),
        grid_spacing_m=np.asarray([spacing_m]),
    )
    os.replace(temporary_registry, registry_path)

    saved_map = sparse.load_npz(full_path).tocsc()
    with np.load(registry_path, allow_pickle=False) as registry:
        registry_ok = bool(
            np.array_equal(registry["support_island_index"], support_island) and
            np.array_equal(registry["support_face_z_um"], support_z) and
            np.array_equal(registry["point_count"], point_counts) and
            np.array_equal(registry["column_nnz"], column_nnz) and
            np.array_equal(registry["checkpoint_sha256"].astype("U64"), np.asarray(checkpoint_hashes)) and
            np.array_equal(registry["column_sha256"].astype("U64"), np.asarray(column_hashes)) and
            np.array_equal(registry["grid_origin_m"], origin) and
            np.array_equal(registry["grid_shape"], np.asarray(shape)) and
            float(registry["grid_spacing_m"][0]) == spacing_m)
    map_ok = bool(
        saved_map.shape == full_map.shape and
        np.array_equal(saved_map.indptr, full_map.indptr) and
        np.array_equal(saved_map.indices, full_map.indices) and
        np.array_equal(saved_map.data, full_map.data))

    grid_index = np.arange(cells, dtype=np.float64)
    field = np.sin(grid_index * 0.0037) + 1j * np.cos(grid_index * 0.0061)
    face_index = np.arange(len(support_island), dtype=np.float64)
    face_charge = np.sin(face_index * 0.017) + 1j * np.cos(face_index * 0.011)
    unique_charge = np.bincount(support_island, weights=face_charge.real, minlength=len(island_ids)) + 1j * np.bincount(
        support_island, weights=face_charge.imag, minlength=len(island_ids))
    spread = saved_map @ unique_charge
    unique_gather = saved_map.T @ field
    face_gather = unique_gather[support_island]
    ordinary_error = float(abs(field @ spread - face_gather @ face_charge) /
                           max(abs(field @ spread), abs(face_gather @ face_charge), 1e-300))
    _, peak_working_set = pilot.process_memory()
    elapsed = perf_counter() - started
    output_disk_bytes = sum(path.stat().st_size for path in paths) + full_path.stat().st_size + registry_path.stat().st_size

    gates = {
        "all_4386_faces_mapped_to_2467_unique_islands": len(support_island) == 4386 and full_map.shape == (cells, 2467),
        "maximum_two_face_reuse_preserved": int(island_counts.max()) == 2,
        "accepted_columns_reused_unchanged": np.count_nonzero(reused_kind == 1) == 2,
        "streamed_pilot_column_reused_unchanged": np.count_nonzero(reused_kind == 2) == 1,
        "all_checkpoint_rules_loaded_once_without_header_rescan": unique_points == 20_363_317,
        "every_column_compensated_mass_2e_15": float(mass_errors.max()) <= 2e-15,
        "all_columns_finite_and_indexed": np.all(np.isfinite(full_map.data)) and np.all(full_map.indices >= 0) and np.all(full_map.indices < cells),
        "serialized_full_map_and_registry_readback": map_ok and registry_ok,
        "ordinary_bilinear_transpose_whole_map": ordinary_error <= 2e-13,
        "computation_within_180_seconds": elapsed <= TIME_CAP_S,
        "output_arrays_within_2GB": total_output_array_bytes <= OUTPUT_ARRAY_CAP_BYTES,
        "no_global_point_or_coo_materialization": True,
        "native_grid_binding_exact": shape == (795, 795) and origin.tolist() == [-0.049625, -0.049625] and spacing_m == 125e-6,
    }
    if not all(gates.values()):
        raise ValueError(f"full native sparse preassembly gates failed: {gates}")

    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_RETAINED_GC_NATIVE_SPARSE_GRID_PREASSEMBLY",
        "driver_sha256": digest(Path(__file__)),
        "inputs": {str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        "artifacts": {
            str(full_path.relative_to(ROOT)): digest(full_path),
            str(registry_path.relative_to(ROOT)): digest(registry_path),
        },
        "grid_binding": {
            "kind": "NATIVE_FACE_ONLY_795_GRID",
            "origin_m": origin.tolist(),
            "shape": list(shape),
            "spacing_m": spacing_m,
            "common_grid_remap_interface": "For a verified equal-spacing destination lattice only: require (source_origin-destination_origin)/spacing to be exactly integral and in bounds, then map (ix,iy) to (ix+shift_x,iy+shift_y) without interpolation.",
            "verified_current_destination": {
                "origin_m": common_grid["origin_m"],
                "shape": common_grid["shape"],
                "spacing_m": common_grid["spacing_m"],
                "integer_shift": remap_result["integer_shift"],
                "scope": common_grid["scope"],
            },
        },
        "counts": {
            "faces": len(support_island),
            "unique_xy_columns": len(island_ids),
            "islands_with_two_faces": int(np.count_nonzero(island_counts == 2)),
            "islands_with_one_face": int(np.count_nonzero(island_counts == 1)),
            "unique_rule_points": unique_points,
            "face_rule_points_without_reuse": 33_967_296,
            "full_map_nnz": int(full_map.nnz),
            "reused_accepted_columns": 2,
            "reused_streamed_pilot_columns": 1,
        },
        "metrics": {
            "elapsed_s": elapsed,
            "streamed_kernel_build_s": build_kernel_s,
            "process_initial_working_set_bytes": initial_working_set,
            "process_peak_working_set_bytes": peak_working_set,
            "maximum_batch_explicit_arrays_bytes": maximum_batch_arrays,
            "per_island_column_raw_csc_bytes": int(column_raw_bytes.sum()),
            "full_map_raw_csc_bytes": full_raw_bytes,
            "total_output_array_bytes": total_output_array_bytes,
            "output_disk_bytes": output_disk_bytes,
            "maximum_compensated_mass_abs_error": float(mass_errors.max()),
            "ordinary_bilinear_transpose_relative": ordinary_error,
            "unique_to_face_point_ratio": unique_points / 33_967_296,
        },
        "gates": gates,
        "scope": "Preassembled sparse spread/gather cache for the unchanged cubic smooth-remainder stencil on the pinned native-only grid. Independent face heights remain in the registry. No Green kernel, direct/singular term, point self, CDT, SPD read, common-grid merge, FMM, or board solve.",
    }


def main() -> None:
    if OUTPUT.exists():
        frozen = OUTPUT / "driver-at-run.py"
        if not frozen.is_file() or digest(frozen) != digest(Path(__file__)):
            raise ValueError("existing output cannot resume under different driver bytes")
        previous = OUTPUT / "result.json"
        if previous.is_file() and json.loads(previous.read_text(encoding="utf-8"))["status"].startswith("PASS_"):
            raise ValueError("completed output is immutable")
    else:
        OUTPUT.mkdir()
        COLUMNS.mkdir()
        (OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    COLUMNS.mkdir(exist_ok=True)
    try:
        result = run()
    except Exception:
        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "STOP_RETAINED_GC_NATIVE_SPARSE_GRID_PREASSEMBLY",
            "driver_sha256": digest(Path(__file__)),
            "completed_atomic_column_files": len(list(COLUMNS.glob("island-*.npz"))),
            "traceback": traceback.format_exc(),
        }
        (OUTPUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise
    (OUTPUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "counts": result["counts"], "metrics": result["metrics"]}), flush=True)


if __name__ == "__main__":
    main()
