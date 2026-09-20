"""Refine only the frozen 415 shared-edge order-64 tails at order 128; no solve."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import types
from typing import Any, Callable

import numpy as np


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
SOURCE = RESEARCH / "astra-l25-shared-edge-magnetic-01"
STEP = RESEARCH / "astra-l25-adaptive-longest-pair-01" / "step-06"
THRESHOLD = 1.0e-3
TAIL_COUNT = 415
MAX_RUNTIME_S = 120.0
MAX_RSS_BYTES = 1 << 30

PINS = {
    "accepted_result": (SOURCE / "result.json", "6b4df2af404ec46fdd983efc5fc6e00a3fed4c8babf05a3eaa30cf239261f174"),
    "accepted_local_corrections": (SOURCE / "local-corrections.npz", "55aec2b70ce99ed7d0f68165aa9b390de33c0cfe896fc5733694550837107421"),
    "accepted_frozen_driver": (SOURCE / "driver-at-run.py", "b6f1c0a6b7a2414112aa47181f62a8138d94c0c8c7f2ab8edafbe6b17b01ad87"),
    "current_shared_edge_assembler": (ROOT / "tools/research/assemble_astra_l25_shared_edge_magnetic.py", "b6f1c0a6b7a2414112aa47181f62a8138d94c0c8c7f2ab8edafbe6b17b01ad87"),
    "vectorized_pair_integrator": (ROOT / "tools/research/probe_astra_rt0_near_batch.py", "283711e7f8037c12ef54baa3748e684c0135f9700aab5f1bca6de1b5abfeeaa7"),
    "mesh": (STEP / "mesh.npz", "20b52d26783bf98197524c56f22d9872aee3b67b7e526ff20a02e396f39e89cb"),
    "topology": (STEP / "topology.npz", "d70ceb1b38cede682247967495c6ef83d08ef2b0ef63e3543df908ba13412a4d"),
}

TAIL_ARRAY_SHA256 = {
    "source_pair_ordinal": "32747c2f6b34e1578baae795854a1366a6816dc60c8a6b2928a13440e9055ee2",
    "source_shared_edge_branch_index": "2f79ef000f56d09ed4590a9d9aeff50d499f03d2042ab7d7c7e9c16295401bd8",
    "first_free_triangle": "c3fc67376583c748786cca06973ce2a6608a8fe97d3b99204201a16307b23cd5",
    "second_free_triangle": "1b7527c0e62f6b4aaff3fd7fa9322aee709e223b7a999c2f9f961aa752c6090a",
    "old_order64_local_correction_block_h": "cec2e6e11c7e7ea8c85914cca691ebf541b8bc3378bcff01b20aa60962c6d9be",
    "saved_order64_vs_order32_relative": "a8309b5449a2cd13fc3f22c68a2407638e16fc32634cca4b5454ff6ebe434f93",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha256(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _pin(names: tuple[str, ...] | None = None) -> dict[str, dict[str, Any]]:
    selected = names or tuple(PINS)
    receipts = {}
    for name in selected:
        path, expected = PINS[name]
        actual = _sha256(path)
        _require(actual == expected, f"input hash mismatch: {name}")
        receipts[name] = {
            "path": str(path.relative_to(ROOT)),
            "bytes": int(path.stat().st_size),
            "sha256": actual,
        }
    return receipts


def _atomic_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            np.savez_compressed(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_receipt(path: Path) -> dict[str, Any]:
    return {"path": path.name, "bytes": int(path.stat().st_size), "sha256": _sha256(path)}


def _load_pair_batch() -> Callable[[np.ndarray, np.ndarray, int], np.ndarray]:
    """Load the exact pinned function without importing its unused research drivers."""
    _pin(("accepted_frozen_driver", "current_shared_edge_assembler", "vectorized_pair_integrator"))
    partial = types.ModuleType("probe_astra_rt0_partial_inductance")
    partial.triangle_pair = None
    saved = types.ModuleType("probe_astra_l25_saved_current_moments")
    saved.checked = saved.BOARD = saved.BOARD_SHA = saved.STEP = None
    stubs = {partial.__name__: partial, saved.__name__: saved}
    missing = object()
    prior = {name: sys.modules.get(name, missing) for name in stubs}
    try:
        sys.modules.update(stubs)
        path = PINS["vectorized_pair_integrator"][0]
        spec = importlib.util.spec_from_file_location("_pinned_astra_rt0_near_batch", path)
        _require(spec is not None and spec.loader is not None, "cannot load pinned pair integrator")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, value in prior.items():
            if value is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module.triangle_pair_batch


def _relative_difference(new: np.ndarray, old: np.ndarray) -> np.ndarray:
    return np.linalg.norm(new - old, axis=(1, 2)) / np.maximum(
        np.linalg.norm(new, axis=(1, 2)), 1.0e-30
    )


def _centroid_blocks(observer: np.ndarray, source: np.ndarray) -> np.ndarray:
    center_o, center_s = observer.mean(axis=1), source.mean(axis=1)

    def basis(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        edge1, edge2 = vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]
        area2 = np.abs(edge1[:, 0] * edge2[:, 1] - edge1[:, 1] * edge2[:, 0])
        _require(bool(np.all(area2 > 0.0)), "degenerate selected triangle")
        return (center_o if vertices is observer else center_s)[:, None] - vertices, area2

    offset_o, area2_o = basis(observer)
    offset_s, area2_s = basis(source)
    distance = np.linalg.norm(center_o - center_s, axis=1)
    _require(bool(np.all(distance > 0.0)), "selected triangle centroids coincide")
    scale = 1.0e-7 * area2_o * area2_s / (4.0 * distance)
    return scale[:, None, None] * np.einsum(
        "tid,tjd->tij", offset_o / area2_o[:, None, None], offset_s / area2_s[:, None, None]
    )


def self_check() -> dict[str, Any]:
    pair_batch = _load_pair_batch()
    observer = np.asarray(((0.0, 0.0), (0.004, 0.0), (0.004, 0.001)))
    source = np.asarray(((0.0, 0.0), (0.004, 0.001), (0.0, 0.001)))
    flip = np.asarray((0, 2, 1))
    forward, reverse, source_flipped, both_flipped = pair_batch(
        np.asarray((observer, source, observer, observer[flip])),
        np.asarray((source, observer, source[flip], source[flip])),
        32,
    )
    scale = max(float(np.linalg.norm(forward)), np.finfo(float).tiny)
    metrics = {
        "raw_forward_reverse_transpose_relative": float(np.linalg.norm(forward - reverse.T) / scale),
        "source_winding_permutation_relative": float(np.linalg.norm(source_flipped - forward[:, flip]) / scale),
        "both_winding_permutation_relative": float(np.linalg.norm(both_flipped - forward[np.ix_(flip, flip)]) / scale),
    }
    assembled = np.block([[np.zeros((3, 3)), forward], [forward.T, np.zeros((3, 3))]])
    cross = lambda value: value[1, 0] * value[2, 1] - value[1, 1] * value[2, 0]
    _require(np.sign(cross(observer - observer[0])) == 1.0, "forward orientation fixture changed")
    flipped = observer[flip]
    _require(np.sign(cross(flipped - flipped[0])) == -1.0, "reverse orientation fixture changed")
    _require(metrics["raw_forward_reverse_transpose_relative"] < 1.0e-6, "synthetic forward/reverse check failed")
    _require(metrics["source_winding_permutation_relative"] < 1.0e-12, "synthetic source winding check failed")
    _require(metrics["both_winding_permutation_relative"] < 1.0e-6, "synthetic orientation check failed")
    _require(bool(np.array_equal(assembled, assembled.T)), "analytic reciprocal assembly is not symmetric")
    metrics["analytic_reciprocal_orientation_exact"] = True
    return metrics


def estimate() -> dict[str, Any]:
    reference_work = 601_143 * (8**2 + 16**2) + (18_958 + 2_154) * 32**2 + 2_154 * 64**2
    order128_work = TAIL_COUNT * 128**2
    scaled_s = 182.60712859994965 * order128_work / reference_work
    estimated_s = 6.0 * scaled_s
    estimated_peak = 256 * 2**20
    return {
        "program": PROGRAM,
        "version": VERSION,
        "mode": "DRY_COST_MEMORY_ESTIMATE_ONLY",
        "source_pairs": TAIL_COUNT,
        "fresh_order": 128,
        "quadrature_node_pair_evaluations": order128_work,
        "accepted_run_elapsed_s": 182.60712859994965,
        "accepted_run_quadrature_node_pair_evaluations": reference_work,
        "linear_scaled_runtime_s": scaled_s,
        "runtime_safety_factor": 6.0,
        "estimated_runtime_s": estimated_s,
        "estimated_peak_rss_bytes": estimated_peak,
        "limits": {"runtime_s": MAX_RUNTIME_S, "rss_bytes": MAX_RSS_BYTES},
        "within_limits": bool(estimated_s < MAX_RUNTIME_S and estimated_peak < MAX_RSS_BYTES),
    }


def _load_tail() -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, dict[str, Any]]]:
    inputs = _pin()
    receipt = json.loads(PINS["accepted_result"][0].read_text(encoding="utf-8"))
    _require(receipt.get("program") == PROGRAM and receipt.get("version") == VERSION, "accepted result identity differs")
    _require(receipt.get("status") == "COMPLETED_CONDITIONAL_SHARED_EDGE_CENTROID_CORRECTION", "accepted result status differs")
    _require(receipt.get("shared_edge_pairs") == 601_143 and receipt.get("pairs_still_over_1e_3_at_order64") == TAIL_COUNT, "accepted pair counts differ")
    _require(receipt.get("script_sha256") == PINS["accepted_frozen_driver"][1], "accepted driver receipt differs")
    _require(receipt.get("local_corrections", {}).get("sha256") == PINS["accepted_local_corrections"][1], "accepted local artifact receipt differs")

    local_path = PINS["accepted_local_corrections"][0]
    with np.load(local_path, allow_pickle=False) as archive:
        expected = {"source_branch_indices", "first_free_triangle", "second_free_triangle", "correction_blocks_h", "selected_orders", "last_refinement_relative"}
        _require(set(archive.files) == expected, "accepted local correction schema differs")
        errors = np.asarray(archive["last_refinement_relative"])
        _require(errors.shape == (601_143,) and errors.dtype == np.float64 and np.all(np.isfinite(errors)), "saved error array differs")
        orders = np.asarray(archive["selected_orders"])
        _require(orders.shape == errors.shape and orders.dtype == np.int16, "saved order array differs")
        mask = (orders == 64) & (errors > THRESHOLD)
        rows = np.flatnonzero(mask)
        _require(rows.size == TAIL_COUNT, "strict saved order64 tail selection is not 415 pairs")
        tail = {
            "source_pair_ordinal": rows,
            "source_shared_edge_branch_index": np.asarray(archive["source_branch_indices"])[mask].copy(),
            "first_free_triangle": np.asarray(archive["first_free_triangle"])[mask].copy(),
            "second_free_triangle": np.asarray(archive["second_free_triangle"])[mask].copy(),
            "old_order64_local_correction_block_h": np.asarray(archive["correction_blocks_h"])[mask].copy(),
            "saved_order64_vs_order32_relative": errors[mask].copy(),
        }
    for name, expected_sha in TAIL_ARRAY_SHA256.items():
        _require(_array_sha256(tail[name]) == expected_sha, f"frozen 415-tail identity differs: {name}")

    with np.load(PINS["topology"][0], allow_pickle=False) as archive:
        free = np.asarray(archive["free_triangle_indices"])
        first_node = np.asarray(archive["branch_first_node"])
        second_node = np.asarray(archive["branch_second_node"])
        local_branch = np.asarray(archive["local_facet_branch_index"])
        local_sign = np.asarray(archive["local_outward_flux_sign"])
    _require(free.shape == (579_177,) and local_branch.shape == local_sign.shape == (579_177, 3), "topology dimensions differ")
    shared = tail["source_shared_edge_branch_index"]
    first, second = tail["first_free_triangle"], tail["second_free_triangle"]
    _require(np.array_equal(first_node[shared], first) and np.array_equal(second_node[shared], second), "saved shared-edge branch mapping differs")
    tail["first_source_triangle_index"] = free[first].copy()
    tail["second_source_triangle_index"] = free[second].copy()
    tail["first_local_branch_index"] = local_branch[first].copy()
    tail["second_local_branch_index"] = local_branch[second].copy()
    tail["first_local_outward_flux_sign"] = local_sign[first].copy()
    tail["second_local_outward_flux_sign"] = local_sign[second].copy()

    with np.load(PINS["mesh"][0], allow_pickle=False) as archive:
        points = np.asarray(archive["node_xy_um"])
        triangles = np.asarray(archive["triangles"])
    _require(points.shape == (554_965, 2) and triangles.shape == (581_715, 3), "mesh dimensions differ")
    observer = points[triangles[tail["first_source_triangle_index"]]] * 1.0e-6
    source = points[triangles[tail["second_source_triangle_index"]]] * 1.0e-6
    _require(np.all(np.isfinite(observer)) and np.all(np.isfinite(source)), "nonfinite selected geometry")
    return tail, observer, source, inputs


def run(output: Path) -> dict[str, Any]:
    started = time.perf_counter()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    frozen = Path(__file__).read_bytes()
    with (output / "driver-at-run.py").open("xb") as handle:
        handle.write(frozen)
    try:
        tail, observer, source, inputs = _load_tail()
        pair_batch = _load_pair_batch()
        centroid = _centroid_blocks(observer, source)
        raw128 = pair_batch(observer, source, 128)
        order128 = raw128 - centroid
        old64 = tail["old_order64_local_correction_block_h"]
        delta = order128 - old64
        error = _relative_difference(raw128, old64 + centroid)
        _require(order128.shape == old64.shape == (TAIL_COUNT, 3, 3), "tail block dimensions differ")
        _require(np.all(np.isfinite(order128)) and np.all(np.isfinite(delta)) and np.all(np.isfinite(error)), "nonfinite order128 result")
        arrays = tail | {
            "new_order128_local_correction_block_h": order128,
            "order128_minus_order64_local_correction_block_h": delta,
            "order128_vs_order64_relative": error,
        }
        checkpoint_npz = output / "order128-checkpoint.npz"
        _atomic_npz(checkpoint_npz, **arrays)
        checkpoint = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "ORDER128_TAIL_CHECKPOINT",
            "script_sha256": hashlib.sha256(frozen).hexdigest(),
            "inputs": inputs,
            "selection": {"predicate": "saved last_refinement_relative > 1e-3", "pairs": TAIL_COUNT, "saved_order": 64, "fresh_order": 128},
            "artifact": _file_receipt(checkpoint_npz),
            "elapsed_s": time.perf_counter() - started,
        }
        _atomic_json(output / "checkpoint.json", checkpoint)
        result = {
            **checkpoint,
            "status": "COMPLETED_FROZEN_415_SHARED_EDGE_ORDER128_TAIL",
            "order128_vs_order64_relative_quantiles": np.quantile(error, (0.0, 0.5, 0.9, 0.99, 1.0)).tolist(),
            "pairs_still_over_1e_3_at_order128_vs_order64": int(np.count_nonzero(error > THRESHOLD)),
            "checkpoint": _file_receipt(output / "checkpoint.json"),
            "elapsed_s": time.perf_counter() - started,
            "limits": {"target_runtime_s": MAX_RUNTIME_S, "target_rss_bytes": MAX_RSS_BYTES},
            "scope": "The exact frozen 415 saved order64 tails only: one fresh directed order128 block per pair, with the identical centroid block subtracted. Per-pair local blocks and source/free/local branch provenance are saved; no sparse operator, source geometry regeneration, FMM, LU, field solve, order256 pass, PSD or PowerSI claim.",
        }
        _atomic_json(output / "result.json", result)
        return result
    except BaseException as exc:
        try:
            _atomic_json(output / "failure.json", {
                "program": PROGRAM,
                "version": VERSION,
                "status": "STOP_SHARED_EDGE_TAIL",
                "error": {"type": type(exc).__name__, "message": str(exc)},
                "script_sha256": hashlib.sha256(frozen).hexdigest(),
                "elapsed_s": time.perf_counter() - started,
            })
        except BaseException:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION}: {__doc__}")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-check", action="store_true")
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(f"{PROGRAM} v{VERSION} - frozen shared-edge tail refinement", flush=True)
    if args.self_check:
        print(json.dumps({"status": "SELF_CHECK_PASS", **self_check()}, sort_keys=True, allow_nan=False))
    elif args.estimate:
        print(json.dumps(estimate(), sort_keys=True, allow_nan=False))
    else:
        result = run(args.output)
        print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
