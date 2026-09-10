"""Saved three-layer FFT-grid cost census; no field, FFT, or magnetic action."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
from time import monotonic, perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "outputs" / "research-runtime"))
from probe_astra_l25_magnetic_grid_cost import grid_cost  # noqa: E402

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
PITCHES_UM = (128.0, 64.0)
MAX_SECONDS = 60.0
MAX_RSS_BYTES = 4 * 1024 ** 3
PINS = {
    "grid_cost_helper": (ROOT / "tools/research/probe_astra_l25_magnetic_grid_cost.py", "c5d6f301c0f01a8ed990538ef0a55e1acfa812feaf74c16d6ed12550b7441629"),
    "rt0_auxiliary_helper": (ROOT / "tools/research/probe_astra_rt0_fft_auxiliary.py", "ac40d35af2cd3aebc7b8b541f2b94de4195234ec6177d3f30190837971bc6002"),
    "stackup_inventory": (ROOT / "outputs/research/astra-3d-source-domain-inventory-01/result.json", "daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663"),
    "l02_mesh": (ROOT / "outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz", "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9"),
    "l02_rt0_map": (ROOT / "outputs/research/astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz", "7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f"),
    "l14_mesh": (ROOT / "outputs/research/astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz", "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779"),
    "l25_mesh": (ROOT / "outputs/research/astra-l25-adaptive-longest-pair-01/step-06/mesh.npz", "20b52d26783bf98197524c56f22d9872aee3b67b7e526ff20a02e396f39e89cb"),
    "l25_topology": (ROOT / "outputs/research/astra-l25-adaptive-longest-pair-01/step-06/topology.npz", "d70ceb1b38cede682247967495c6ef83d08ef2b0ef63e3543df908ba13412a4d"),
}
LAYERS = (
    ("L02", "l02_mesh", "l02_rt0_map", "Signal$L02(DGND)", 1_583_840),
    ("L14", "l14_mesh", None, "Signal$L14(MAIN_POWER4)", 214_873),
    ("L25", "l25_mesh", "l25_topology", "Signal$L25(MAIN_POWER4)", 579_177),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

    value = Counters()
    value.cb = ctypes.sizeof(value)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    get_info = psapi.GetProcessMemoryInfo
    get_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    get_info.restype = wintypes.BOOL
    if not get_info(kernel32.GetCurrentProcess(), ctypes.byref(value), value.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(value.working_set_size)


class Budget:
    def __init__(self) -> None:
        self.started = monotonic()
        self.peak_rss_bytes = _rss_bytes()

    def check(self, phase: str) -> None:
        self.peak_rss_bytes = max(self.peak_rss_bytes, _rss_bytes())
        elapsed = monotonic() - self.started
        if elapsed > MAX_SECONDS:
            raise TimeoutError(f"60 s budget exceeded during {phase}")
        if self.peak_rss_bytes > MAX_RSS_BYTES:
            raise MemoryError(f"4 GiB budget exceeded during {phase}")

    def report(self) -> dict:
        self.check("receipt")
        return {"max_runtime_s": MAX_SECONDS, "max_rss_bytes": MAX_RSS_BYTES,
                "elapsed_s": monotonic() - self.started, "peak_rss_bytes": self.peak_rss_bytes,
                "cooperative_checks": True}


def local_triangle_areas(vertices: np.ndarray, budget: Budget) -> tuple[float, float]:
    """Use a local origin for each triangle to avoid shoelace cancellation."""
    minimum, maximum = float("inf"), 0.0
    for start in range(0, len(vertices), 200_000):
        local = vertices[start:start + 200_000] - vertices[start:start + 200_000, :1, :]
        twice = local[:, 1, 0] * local[:, 2, 1] - local[:, 1, 1] * local[:, 2, 0]
        areas = 0.5 * np.abs(twice)
        if not np.isfinite(areas).all() or np.any(areas <= 0.0):
            raise ValueError("non-finite or degenerate original triangle")
        minimum, maximum = min(minimum, float(areas.min())), max(maximum, float(areas.max()))
        budget.check("local translated triangle-area census")
    return minimum, maximum


def mesh_vertices(path: Path, selector_path: Path | None, layer: str, expected_count: int, budget: Budget) -> np.ndarray:
    with np.load(path, allow_pickle=False) as archive:
        xy = np.asarray(archive["node_xy_um"])
        triangles = np.asarray(archive["triangles"])
    if not (xy.ndim == 2 and xy.shape[1] == 2 and triangles.ndim == 2 and triangles.shape[1] == 3
            and np.issubdtype(triangles.dtype, np.integer) and np.isfinite(xy).all()
            and int(triangles.min()) >= 0 and int(triangles.max()) < len(xy)):
        raise ValueError(f"{layer} original mesh schema differs")
    if selector_path is None:
        selected = np.arange(len(triangles), dtype=np.int64)
    else:
        with np.load(selector_path, allow_pickle=False) as archive:
            selected = np.asarray(archive["free_triangle_indices"])
        if not (selected.ndim == 1 and np.issubdtype(selected.dtype, np.integer)
                and len(np.unique(selected)) == len(selected) and int(selected.min()) >= 0
                and int(selected.max()) < len(triangles)):
            raise ValueError(f"{layer} accepted free-triangle map differs")
    if len(selected) != expected_count:
        raise ValueError(f"{layer} accepted source-current triangle count differs")
    vertices = xy[triangles[selected]]
    minimum, maximum = local_triangle_areas(vertices, budget)
    if not (np.isfinite(vertices).all() and minimum > 0.0):
        raise ValueError(f"{layer} original vertices differ")
    budget.check(f"{layer} original triangles")
    return vertices


def _row(vertices: np.ndarray, pitch_um: float, origin: np.ndarray) -> dict:
    lower, upper = vertices.min(axis=1), vertices.max(axis=1)
    first = np.floor((lower - origin) / pitch_um).astype(np.int64)
    last = np.floor((upper - origin) / pitch_um).astype(np.int64)
    touches = np.prod(last - first + 1, axis=1)
    return {"triangle_count": int(len(vertices)), "bbox_candidate_count": int(touches.sum()),
            "worst_one_triangle_bbox_candidates": int(touches.max()),
            "triangles_contained_in_one_grid_cell": int(np.count_nonzero(touches == 1))}


def self_check() -> None:
    vertices = np.array([[[0.0, 0.0], [2.0, 0.0], [0.0, 1.0]]])
    row = grid_cost(vertices, 1.0)
    assert row["grid_shape_yx"] == [2, 3]
    assert row["triangle_bbox_cell_intersections_upper_bound"] == 6
    translated = np.array([[[1.0e12, -1.0e12], [1.0e12 + 2.0, -1.0e12], [1.0e12, -1.0e12 + 1.0]]])
    local = translated - translated[:, :1, :]
    assert float(0.5 * abs(local[0, 1, 0] * local[0, 2, 1] - local[0, 1, 1] * local[0, 2, 0])) == 1.0


def run(output: Path) -> None:
    budget, started = Budget(), perf_counter()
    for name, (path, digest) in PINS.items():
        actual = sha(path)
        if actual != digest:
            raise ValueError(f"{name} SHA-256 differs")
    inventory = json.loads(PINS["stackup_inventory"][0].read_text(encoding="utf-8"))
    slabs = {item["name"]: [item["z_top_um"], item["z_bottom_um"]]
             for item in inventory["stackup"]["conductors"]}
    expected_slabs = {"Signal$L02(DGND)": [55.0, 75.0], "Signal$L14(MAIN_POWER4)": [655.0, 675.0],
                      "Signal$L25(MAIN_POWER4)": [1511.0, 1543.0]}
    if {name: slabs.get(name) for name in expected_slabs} != expected_slabs:
        raise ValueError("source stackup L02/L14/L25 z-slab contract differs")
    vertices_by_layer = {layer: mesh_vertices(PINS[mesh_pin][0], PINS[selector_pin][0] if selector_pin else None,
                                              layer, expected_count, budget)
                         for layer, mesh_pin, selector_pin, _name, expected_count in LAYERS}
    all_vertices = np.concatenate(tuple(vertices_by_layer.values()), axis=0)
    bounds = [all_vertices.min(axis=(0, 1)), all_vertices.max(axis=(0, 1))]
    rows = []
    for pitch_um in PITCHES_UM:
        origin = np.floor(bounds[0] / pitch_um) * pitch_um
        full = grid_cost(all_vertices, pitch_um)
        direct = _row(all_vertices, pitch_um, origin)
        if full["triangle_bbox_cell_intersections_upper_bound"] != direct["bbox_candidate_count"]:
            raise ValueError("reused grid-cost helper candidate formula differs")
        layer_rows = {layer: _row(vertices, pitch_um, origin) for layer, vertices in vertices_by_layer.items()}
        if sum(item["bbox_candidate_count"] for item in layer_rows.values()) != direct["bbox_candidate_count"]:
            raise ValueError("layer candidate totals differ")
        rows.append({"pitch_um": pitch_um, "common_xy_extent_um": [bounds[0].tolist(), bounds[1].tolist()],
                     "common_grid_origin_xy_um": origin.tolist(), "common_grid_shape_yx": full["grid_shape_yx"],
                     "grid_cells": full["grid_cells"], "linear_convolution_padded_shape_yx": full["fft_full_convolution_padded_shape_yx"],
                     "linear_convolution_padded_cells": int(np.prod(full["fft_full_convolution_padded_shape_yx"])),
                     "five_complex128_padded_arrays_bytes": full["five_complex128_padded_arrays_bytes"],
                     "all_layers": direct, "layers": layer_rows})
        budget.check(f"{pitch_um:g} um grid cost")
    arrays = {"pitch_um": np.asarray(PITCHES_UM), "grid_shape_yx": np.asarray([row["common_grid_shape_yx"] for row in rows]),
              "padded_shape_yx": np.asarray([row["linear_convolution_padded_shape_yx"] for row in rows]),
              "grid_cells": np.asarray([row["grid_cells"] for row in rows]),
              "candidate_counts": np.asarray([row["all_layers"]["bbox_candidate_count"] for row in rows]),
              "worst_candidates": np.asarray([row["all_layers"]["worst_one_triangle_bbox_candidates"] for row in rows]),
              "contained_one_cell": np.asarray([row["all_layers"]["triangles_contained_in_one_grid_cell"] for row in rows]),
              "layer_triangle_counts": np.asarray([len(vertices_by_layer[layer]) for layer, _mesh, _selector, _name, _count in LAYERS]),
              "z_slabs_um": np.asarray([expected_slabs[name] for _layer, _mesh, _selector, name, _count in LAYERS])}
    artifact = output / "three-layer-fft-grid-cost-census.npz"
    np.savez_compressed(artifact, **arrays)
    result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_THREE_LAYER_FFT_GRID_COST_CENSUS_ONLY",
              "driver": receipt(output / "driver-at-run.py"), "inputs": {name: receipt(path) for name, (path, _digest) in PINS.items()},
              "layers": [{"layer": layer, "source_layer_id": name, "z_slab_um": expected_slabs[name],
                          "accepted_current_source_triangle_count": len(vertices_by_layer[layer]),
                          "selection": "all saved triangles" if selector is None else "pinned free_triangle_indices"}
                         for layer, _mesh, selector, name, _count in LAYERS],
              "formula": {"candidate": "For each original triangle, floor((bbox_min-origin)/h) through floor((bbox_max-origin)/h), inclusive; candidate count is the product of inclusive cell spans. Boundary-only bbox touches are included.",
                          "common_grid": "origin=floor(common_xy_min/h)*h; shape=max(last)+1 per XY axis.",
                          "linear_padding": "scipy.fft.next_fast_len(3*n-2) for each unpadded axis, exactly as probe_astra_l25_magnetic_grid_cost.grid_cost.",
                          "area_gate": "Each triangle is translated to its first vertex before the 2x2 determinant area calculation."},
              "rows": rows, "artifact": receipt(artifact), "budget": budget.report(), "elapsed_s": perf_counter() - started,
              "scope": "Geometry-only grid-cost census over accepted L02 RT0, L14, and step-06 L25 current-source triangles. No FFT, kernel/action, pair-neighbor census, Green evaluation, clipping/integration, solve, remesh, SPD parse, field, or magnetic claim."}
    with (output / "result.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "artifact": result["artifact"], "budget": result["budget"]}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print(f"{PROGRAM} v{VERSION}: three-layer FFT grid-cost census SELF_CHECK PASS")
    elif args.output is None:
        parser.error("--output required")
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        run(args.output)
