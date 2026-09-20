"""Qualify the immutable full native sparse map after receipt serialization failed.

The original process completed every atomic column and both final artifacts, but
could not JSON-encode one NumPy boolean.  This checker reads saved arrays only;
it does not rebuild a stencil column or read any quadrature checkpoint.
"""

from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import sparse

import assemble_astra_retained_gc_native_sparse_grid_map as build
import qualify_astra_retained_gc_streamed_sparse_grid_map as pilot


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
SOURCE_OUTPUT = RESEARCH / "astra-retained-gc-native-sparse-grid-map-20260912-01"
OUTPUT = RESEARCH / "astra-retained-gc-native-sparse-grid-map-20260912-02-saved-qualification"
DRIVER_AT_RUN = SOURCE_OUTPUT / "driver-at-run.py"
FULL_MAP = SOURCE_OUTPUT / "native-grid-to-unique-island-map.npz"
REGISTRY = SOURCE_OUTPUT / "native-face-registry.npz"

PINS = {
    DRIVER_AT_RUN: "3ba823e3b708f6a7de303231b4a88c9412200670b6965a2bf600e88110d9a7b0",
    FULL_MAP: "fe738fe9009c908785e092fbec7f9114b0090c2dd79a858bb79cdc92e1f3e1d3",
    REGISTRY: "becd1f3fc5c66772c2eb477625ef31184a815697e7b862d69d4db3fc110bb094",
    build.SUPPORT: "8aa88cfc280a2ea011b572a7571a8a166f8d48cb09404fd7c365894ecf16464d",
    build.ACCEPTED_MAP: "7d8bba97e941aef5bd3e4fee5c827ac8a13b3facb2bae610c46a813243b63132",
    build.PILOT_MAP: "38f0c36d7c1b6b7ba539fa2d7e0816d7219cd39af70f14bc51a75d728ca14564",
    build.REMAP_RESULT: "63bce57b3ee59fbff400e6e0ae7cfc8ae311f0b98daca3ba99e24caac02bb324",
    build.COMMON_GRID_BOUNDS: "a1083fe4a534ac033862129abaac08b735f95323714ac9c30ca1f923318ae154",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def raw_csc_bytes(value: sparse.csc_matrix) -> int:
    return int(value.data.nbytes + value.indices.nbytes + value.indptr.nbytes)


def run() -> dict:
    started = perf_counter()
    initial_working_set, _ = pilot.process_memory()
    for path, expected in PINS.items():
        if digest(path) != expected:
            raise ValueError(f"pinned saved input changed: {path}")
    if digest(ROOT / "tools" / "research" / "assemble_astra_retained_gc_native_sparse_grid_map.py") != PINS[DRIVER_AT_RUN]:
        raise ValueError("current builder differs from immutable driver-at-run")

    full = sparse.load_npz(FULL_MAP).tocsc()
    with np.load(REGISTRY, allow_pickle=False) as saved:
        registry = {name: saved[name].copy() for name in saved.files}
    with np.load(build.SUPPORT, allow_pickle=False) as saved:
        expected_islands = json.loads(saved["island_ids_json_utf8"].tobytes().decode("utf-8"))
        expected_face_island = saved["support_island_index"].astype(np.int64, copy=True)
        expected_face_z = saved["support_face_z_um"].astype(np.float64, copy=True)

    island_ids = json.loads(registry["island_ids_json_utf8"].tobytes().decode("utf-8"))
    checkpoint_names = json.loads(registry["checkpoint_names_json_utf8"].tobytes().decode("utf-8"))
    face_island = registry["support_island_index"]
    face_z = registry["support_face_z_um"]
    column_hashes = registry["column_sha256"].astype("U64")
    checkpoint_hashes = registry["checkpoint_sha256"].astype("U64")
    stored_nnz = registry["column_nnz"]
    stored_mass = registry["compensated_mass_abs_error"]
    stored_raw = registry["column_raw_csc_bytes"]
    reused_kind = registry["reused_kind"]
    origin = registry["grid_origin_m"]
    shape = tuple(map(int, registry["grid_shape"]))
    spacing = float(registry["grid_spacing_m"][0])
    cells = int(np.prod(shape))

    if island_ids != expected_islands or not np.array_equal(face_island, expected_face_island) or not np.array_equal(face_z, expected_face_z):
        raise ValueError("saved face/island/z ownership differs from the qualified support")
    if full.shape != (cells, len(island_ids)) or len(island_ids) != 2467 or len(face_island) != 4386:
        raise ValueError("full saved map dimensions changed")
    if len(checkpoint_names) != len(island_ids) or len(set(checkpoint_names)) != len(island_ids):
        raise ValueError("checkpoint identity registry is incomplete")
    if checkpoint_hashes.shape != (len(island_ids),) or any(len(value) != 64 for value in checkpoint_hashes.tolist()):
        raise ValueError("checkpoint hash registry is incomplete")

    maximum_mass_error = 0.0
    column_disk_bytes = 0
    for index, island_id in enumerate(island_ids):
        path = build.column_path(index, island_id)
        if not path.is_file() or digest(path) != column_hashes[index]:
            raise ValueError(f"atomic column hash failed at {index}")
        column_disk_bytes += path.stat().st_size
        column = sparse.load_npz(path).tocsc()
        first = int(full.indptr[index])
        last = int(full.indptr[index + 1])
        if (column.shape != (cells, 1) or column.nnz != stored_nnz[index] or
                not np.array_equal(column.indices, full.indices[first:last]) or
                not np.array_equal(column.data, full.data[first:last])):
            raise ValueError(f"atomic-to-full column equality failed at {index}")
        mass_error = abs(math.fsum(map(float, column.data)) - 1.0)
        maximum_mass_error = max(maximum_mass_error, mass_error)
        if mass_error > 2e-15 or mass_error != stored_mass[index] or raw_csc_bytes(column) != stored_raw[index]:
            raise ValueError(f"atomic column mass/accounting failed at {index}")

    accepted = sparse.load_npz(build.ACCEPTED_MAP).tocsc()
    streamed = sparse.load_npz(build.PILOT_MAP).tocsc()
    reuse_indices = [int(face_island[0]), int(face_island[1011]), int(face_island[4202])]
    reuse_exact = bool(
        np.array_equal(full[:, reuse_indices[0]].toarray(), accepted[:, 0].toarray()) and
        np.array_equal(full[:, reuse_indices[1]].toarray(), accepted[:, 1].toarray()) and
        np.array_equal(full[:, reuse_indices[2]].toarray(), streamed[:, 0].toarray()) and
        np.count_nonzero(reused_kind == 1) == 2 and np.count_nonzero(reused_kind == 2) == 1)

    grid_index = np.arange(cells, dtype=np.float64)
    field = np.sin(grid_index * 0.0037) + 1j * np.cos(grid_index * 0.0061)
    face_index = np.arange(len(face_island), dtype=np.float64)
    face_charge = np.sin(face_index * 0.017) + 1j * np.cos(face_index * 0.011)
    unique_charge = np.bincount(face_island, weights=face_charge.real, minlength=len(island_ids)) + 1j * np.bincount(
        face_island, weights=face_charge.imag, minlength=len(island_ids))
    spread = full @ unique_charge
    unique_gather = full.T @ field
    face_gather = unique_gather[face_island]
    ordinary_error = float(abs(field @ spread - face_gather @ face_charge) /
                           max(abs(field @ spread), abs(face_gather @ face_charge), 1e-300))

    island_counts = np.bincount(face_island, minlength=len(island_ids))
    registry_raw_bytes = int(sum(value.nbytes for value in registry.values()))
    per_column_raw_bytes = int(stored_raw.sum())
    full_raw_bytes = raw_csc_bytes(full)
    total_output_array_bytes = per_column_raw_bytes + full_raw_bytes + registry_raw_bytes
    output_disk_bytes = int(column_disk_bytes + FULL_MAP.stat().st_size + REGISTRY.stat().st_size)
    original_artifact_span_s = REGISTRY.stat().st_mtime - DRIVER_AT_RUN.stat().st_mtime
    _, qualification_peak = pilot.process_memory()
    elapsed = perf_counter() - started

    gates = {
        "immutable_builder_and_artifacts_pinned": True,
        "all_4386_faces_preserve_z_and_map_to_2467_islands": len(face_island) == 4386 and len(island_ids) == 2467,
        "every_atomic_column_hash_and_full_scatter_exact": True,
        "every_column_compensated_mass_2e_15": maximum_mass_error <= 2e-15,
        "full_map_finite_and_indexed": bool(np.all(np.isfinite(full.data)) and np.all(full.indices >= 0) and np.all(full.indices < cells)),
        "accepted_three_columns_reused_bit_exact": reuse_exact,
        "face_coverage_and_maximum_two_reuse": bool(np.all(island_counts >= 1) and int(island_counts.max()) == 2),
        "whole_map_ordinary_bilinear_transpose": ordinary_error <= 2e-13,
        "total_output_arrays_below_2GB": total_output_array_bytes <= build.OUTPUT_ARRAY_CAP_BYTES,
        "native_grid_binding_exact": bool(shape == (795, 795) and np.array_equal(origin, [-0.049625, -0.049625]) and spacing == 125e-6),
    }
    gates = {name: bool(value) for name, value in gates.items()}
    if not all(gates.values()):
        raise ValueError(f"saved full-map qualification gates failed: {gates}")

    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_RETAINED_GC_NATIVE_SPARSE_GRID_SAVED_QUALIFICATION",
        "driver_sha256": digest(Path(__file__)),
        "inputs": {str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        "artifacts": {str(FULL_MAP.relative_to(ROOT)): PINS[FULL_MAP], str(REGISTRY.relative_to(ROOT)): PINS[REGISTRY]},
        "failure_recovery": {
            "original_exit": 1,
            "cause": "The completed builder result held a NumPy boolean in gates, and json.dumps could not serialize it. No result.json was written.",
            "recomputation": "None. This qualification reads the immutable atomic columns, final map, registry, and ownership metadata only.",
            "original_driver_sha256": PINS[DRIVER_AT_RUN],
            "original_artifact_completion_span_from_file_timestamps_s": original_artifact_span_s,
            "original_process_peak_working_set": "Unavailable because receipt serialization failed after artifact completion.",
        },
        "grid_binding": {
            "kind": "NATIVE_FACE_ONLY_795_GRID",
            "origin_m": origin.tolist(),
            "shape": list(shape),
            "spacing_m": spacing,
            "verified_common_grid": {"origin_m": [-0.05, -0.05], "shape": [801, 801], "integer_shift": [3, 3]},
            "remap": "Map native (ix,iy) to common (ix+3,iy+3), preserving every coefficient without interpolation. The pinned receipts cover current qualified charge domains; separate L14/L25 binding remains outside this artifact.",
        },
        "counts": {
            "faces": len(face_island), "unique_xy_columns": len(island_ids),
            "islands_with_one_face": int(np.count_nonzero(island_counts == 1)),
            "islands_with_two_faces": int(np.count_nonzero(island_counts == 2)),
            "unique_rule_points": int(registry["point_count"].sum()),
            "full_map_nnz": int(full.nnz),
        },
        "metrics": {
            "saved_qualification_elapsed_s": elapsed,
            "saved_qualification_initial_working_set_bytes": initial_working_set,
            "saved_qualification_peak_working_set_bytes": qualification_peak,
            "maximum_compensated_mass_abs_error": maximum_mass_error,
            "ordinary_bilinear_transpose_relative": ordinary_error,
            "atomic_column_disk_bytes": column_disk_bytes,
            "full_map_disk_bytes": FULL_MAP.stat().st_size,
            "registry_disk_bytes": REGISTRY.stat().st_size,
            "total_output_disk_bytes": output_disk_bytes,
            "per_column_raw_csc_bytes": per_column_raw_bytes,
            "full_map_raw_csc_bytes": full_raw_bytes,
            "registry_raw_array_bytes": registry_raw_bytes,
            "total_output_array_bytes": total_output_array_bytes,
        },
        "gates": gates,
        "scope": "Saved-array qualification of the native-grid cubic smooth-remainder spread/gather cache. No quadrature checkpoint read, stencil rebuild, geometry, Green kernel, singular/direct term, FMM, common-grid materialization, or board solve.",
    }


def main() -> None:
    OUTPUT.mkdir(exist_ok=False)
    (OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        result = run()
    except Exception:
        result = {"program": PROGRAM, "version": VERSION,
                  "status": "STOP_RETAINED_GC_NATIVE_SPARSE_GRID_SAVED_QUALIFICATION",
                  "driver_sha256": digest(Path(__file__)), "traceback": traceback.format_exc()}
        payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
        temporary = OUTPUT / "result.json.partial"
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(OUTPUT / "result.json")
        raise
    payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
    temporary = OUTPUT / "result.json.partial"
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(OUTPUT / "result.json")
    print(json.dumps({"status": result["status"], "counts": result["counts"], "metrics": result["metrics"]}), flush=True)


if __name__ == "__main__":
    main()
