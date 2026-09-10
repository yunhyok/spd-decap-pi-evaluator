"""Saved L25 geometry cost bounds for an auxiliary magnetic grid; no field solve."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.fft import next_fast_len
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[2]
STEP = ROOT / "outputs/research/astra-l25-adaptive-longest-pair-01/step-06/result.json"
STEP_SHA = "9734d6b5222ad72488a07bea7a901874cb983c2a6710d862faaee07233865217"


def checked(path, digest):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"input hash mismatch: {path}")
    return data


def grid_cost(vertices, pitch):
    lower, upper = vertices.min(axis=1), vertices.max(axis=1)
    origin = np.floor(lower.min(axis=0) / pitch) * pitch
    first = np.floor((lower - origin) / pitch).astype(np.int64)
    last = np.floor((upper - origin) / pitch).astype(np.int64)
    shape_xy = last.max(axis=0) + 1
    touches = np.prod(last - first + 1, axis=1)
    padded = [next_fast_len(int(3 * n - 2)) for n in shape_xy]
    return {
        "pitch_um": pitch,
        "grid_origin_xy_um": origin.tolist(),
        "grid_shape_yx": shape_xy[::-1].tolist(),
        "grid_cells": int(np.prod(shape_xy)),
        "triangle_bbox_cell_intersections_upper_bound": int(touches.sum()),
        "bbox_cell_touches_median_p99_max": np.quantile(touches, [.5, .99, 1.]).tolist(),
        "fft_full_convolution_padded_shape_yx": padded[::-1],
        "five_complex128_padded_arrays_bytes": int(np.prod(padded)) * 16 * 5,
        "near_distance_um": 2 * pitch,
    }


def self_check():
    vertices = np.array([[[0., 0.], [2., 0.], [0., 1.]]])
    row = grid_cost(vertices, 1.)
    assert row["grid_shape_yx"] == [2, 3]
    assert row["triangle_bbox_cell_intersections_upper_bound"] == 6
    assert row["fft_full_convolution_padded_shape_yx"] == [4, 7]
    points = np.array([[0., 0.], [0., 0.], [1., 0.], [3., 0.]])
    tree = cKDTree(points)
    for radius in (0., 1., 2.):
        count = (int(tree.count_neighbors(tree, radius)) - len(points)) // 2
        exact = sum(np.linalg.norm(points[i] - points[j]) <= radius
                    for i in range(len(points)) for j in range(i))
        assert count == exact


def run(output):
    started = perf_counter()
    step = json.loads(checked(STEP, STEP_SHA))
    arrays = {}
    inputs = {"step": {"path": str(STEP), "sha256": STEP_SHA}}
    for name in ("mesh", "topology"):
        artifact = step["artifacts"][name]
        path = STEP.parent / artifact["path"]
        checked(path, artifact["sha256"])
        with np.load(path, allow_pickle=False) as archive:
            names = ("node_xy_um", "triangles") if name == "mesh" else ("free_triangle_indices",)
            arrays.update({key: archive[key] for key in names})
        inputs[name] = {"path": str(path), "sha256": artifact["sha256"]}
    vertices = arrays["node_xy_um"][arrays["triangles"][arrays["free_triangle_indices"]]]
    if len(vertices) != 579177 or not np.isfinite(vertices).all():
        raise ValueError("final L25 free-cell geometry changed")
    tree = cKDTree(vertices.mean(axis=1))
    pitches = (64., 128., 256.)
    counts = tree.count_neighbors(tree, np.array(pitches) * 2)
    rows = []
    for pitch, count in zip(pitches, counts, strict=True):
        row = grid_cost(vertices, pitch)
        # Centroids lie inside their triangles, so these pairs are only a lower bound.
        row["unordered_distinct_triangle_near_pairs_lower_bound"] = (int(count) - len(vertices)) // 2
        rows.append(row)
    report = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "COMPLETED_GEOMETRY_COST_BOUNDS_ONLY", "inputs": inputs,
        "free_triangles": len(vertices), "rows": rows, "elapsed_s": perf_counter() - started,
        "scope": "No current field, magnetic integration, clipping, FFT, matrix or LU. Bbox counts include boundary-only touches. Near counts exclude self and lower-bound support pairs within2*pitch; this distance policy is a cost probe, not validated magnetic accuracy. Five complex128 buffers are a transparent estimate, not a total memory bound. Grid shape is auxiliary only; source unknowns remain RT0.",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    with (output / "result.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print("SPD Decap PI Evaluator v0.23.1: magnetic grid cost SELF_CHECK PASS")
    elif args.output is None:
        parser.error("--output required")
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        run(args.output)
