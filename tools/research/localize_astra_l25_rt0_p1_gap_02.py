"""Freeze and localize the saved L25 RT0/P1 gap for refinement selection."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import shutil
import time
from pathlib import Path

import numpy as np


PROGRAM = "SPD Decap PI Evaluator v0.23.1"
ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "outputs" / "research" / "astra-l25-rt0-p1-pair-01"
BASE_PRODUCER = ROOT / "tools" / "research" / "localize_astra_l25_rt0_p1_gap.py"
OUTPUT_DEFAULT = ROOT / "outputs" / "research" / "astra-l25-rt0-p1-gap-localization-02"
RESULT = PAIR_DIR / "result.json"
RT0_FIELD = PAIR_DIR / "rt0-field.npz"
P1_FIELD = PAIR_DIR / "p1-field.npz"
DRIVER = PAIR_DIR / "driver-at-run.py"
TOPOLOGY = ROOT / "outputs" / "research" / "astra-l25-dual-cell-topology-01" / "dual-cell-topology.npz"
MESH = ROOT / "outputs" / "research" / "astra-l25-sheet-mesh-preflight-01" / "mesh-stiffness.npz"
DRIVE_RESULT = ROOT / "outputs" / "research" / "astra-l25-sheet-drive-02" / "sheet-electrode-drive.json"
DRIVE_NPZ = ROOT / "outputs" / "research" / "astra-l25-sheet-drive-02" / "sheet-electrode-drive.npz"

RESULT_SHA256 = "2111aeac692b1f8b7a991331264dc4c35fb434c5e846ccf7031f9228c5da4777"
RT0_FIELD_SHA256 = "2e8395c9af501db022525ec1a2adc78bd853ad0e93e738f6251a6bdbaddba89e"
P1_FIELD_SHA256 = "6b14724911d98c834540a1f6d8e599d05e2c3086fa5d09def5ad185cc9c21550"
FROZEN_DRIVER_SHA256 = "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"
TOPOLOGY_SHA256 = "eb33117f14a3e5725e9efeafd2a0a083961ad83abaf022b7fe78b94bc8b5265f"
MESH_SHA256 = "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"
DRIVE_RESULT_SHA256 = "ada249febeb03dd48b57bc54294d5360477534d33c4c8586fdf089eb998906b5"
DRIVE_NPZ_SHA256 = "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266"
BASE_PRODUCER_SHA256 = "37d17daffd6abf99bd8b1aa5146a2d7bdf0ec754401067ef30e1a4b032e704c0"
REL_TOL = 1.0e-10


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _pin(path: Path, expected: str) -> None:
    if not path.is_file() or _sha256(path) != expected:
        raise ValueError(f"pinned input mismatch: {path}")


def _load_npz(path: Path, expected: str) -> dict[str, np.ndarray]:
    _pin(path, expected)
    with np.load(path, allow_pickle=False) as archive:
        return {key: np.asarray(archive[key]) for key in archive.files}


def _load_base():
    _pin(BASE_PRODUCER, BASE_PRODUCER_SHA256)
    spec = importlib.util.spec_from_file_location("saved_gap_localizer", BASE_PRODUCER)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load pinned gap localizer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _freeze_output_root(output_root: Path) -> Path:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite {output_root}")
    output_root.mkdir(parents=True)
    frozen = output_root / "driver-at-run.py"
    with frozen.open("xb") as handle:
        handle.write(Path(__file__).read_bytes())
    return frozen


def _aspect(mesh: dict[str, np.ndarray], data: dict) -> np.ndarray:
    xy = np.asarray(mesh["node_xy_um"], dtype=np.float64)
    triangles = np.asarray(mesh["triangles"], dtype=np.int64)[data["free"]]
    vertices = xy[triangles] * 1.0e-6
    edge2 = np.stack((np.sum((vertices[:, 1] - vertices[:, 0]) ** 2, axis=1),
                      np.sum((vertices[:, 2] - vertices[:, 1]) ** 2, axis=1),
                      np.sum((vertices[:, 0] - vertices[:, 2]) ** 2, axis=1)), axis=1)
    aspect = np.max(edge2, axis=1) / (2.0 * data["areas"])
    if not np.all(np.isfinite(aspect)) or np.any(aspect <= 0.0):
        raise ValueError("invalid triangle aspect metric")
    return aspect


def _mark(data: dict, topology: dict, order: np.ndarray, total_gap: float, target: float, free_count: int) -> tuple[dict, dict[str, np.ndarray]]:
    local_branch = np.asarray(data["local_branch"], dtype=np.int64)
    local_sign = np.asarray(data["local_sign"], dtype=np.int8)
    branch_count = int(topology["branch_first_node"].size)
    valid = local_branch >= 0
    coverage = np.bincount(local_branch[valid], minlength=branch_count)
    safe = np.maximum(local_branch, 0)
    interior_local = valid & (coverage[safe] == 2)
    electrode_local = valid & (coverage[safe] == 1)
    natural_local = ~valid
    cumulative = np.cumsum(data["gap"][order])
    count = int(np.searchsorted(cumulative, target * total_gap, side="left") + 1)
    cells = np.asarray(order[:count], dtype=np.int64)
    marked_local = local_branch[cells]
    marked_sign = local_sign[cells]
    marked_interior = interior_local[cells]
    branch_ids = np.unique(marked_local[marked_interior])
    if branch_ids.size and not np.all(coverage[branch_ids] == 2):
        raise ValueError("selected branch has non-two original free-cell coverage")
    first = np.asarray(topology["branch_first_node"], dtype=np.int64)[branch_ids]
    second = np.asarray(topology["branch_second_node"], dtype=np.int64)[branch_ids]
    if branch_ids.size and (np.any(first >= free_count) or np.any(second >= free_count)):
        raise ValueError("selected interior branch reaches an electrode node")
    prefix_gap = float(np.sum(data["gap"][cells]))
    details = {
        "target_fraction": target,
        "selected_cell_count": count,
        "selected_source_triangle_count": int(np.unique(data["free"][cells]).size),
        "selected_gap_ohm": prefix_gap,
        "selected_gap_fraction": prefix_gap / total_gap,
        "selected_interior_local_facet_count": int(np.count_nonzero(marked_interior)),
        "excluded_natural_local_facet_count": int(np.count_nonzero(natural_local[cells])),
        "excluded_electrode_rim_local_facet_count": int(np.count_nonzero(electrode_local[cells])),
        "selected_interior_branch_count": int(branch_ids.size),
        "selected_branch_original_coverage_min": int(np.min(coverage[branch_ids])) if branch_ids.size else 0,
        "selected_branch_original_coverage_max": int(np.max(coverage[branch_ids])) if branch_ids.size else 0,
        "selected_branch_original_coverage_all_two": bool(not branch_ids.size or np.all(coverage[branch_ids] == 2)),
        "selected_branch_endpoint_nodes_all_free": bool(not branch_ids.size or (np.all(first < free_count) and np.all(second < free_count))),
        "selection_method": "stable descending cell gap; minimal prefix reaching target; unique sorted free/free branch union",
        "rim_policy": "natural (-1) and electrode-rim (original free-cell coverage 1) local facets are explicitly excluded",
    }
    arrays = {
        "selected_cell_ordinals": cells,
        "selected_source_triangle_indices": np.asarray(data["free"][cells], dtype=np.int64),
        "selected_local_branch_ids": marked_local,
        "selected_local_outward_flux_signs": marked_sign,
        "selected_interior_branch_ids": branch_ids,
        "selected_interior_branch_first_node": first,
        "selected_interior_branch_second_node": second,
        "selected_interior_branch_original_coverage": np.asarray(coverage[branch_ids], dtype=np.int8),
    }
    return details, arrays


def _relative(actual: float, expected: float) -> float:
    return abs(actual - expected) / max(abs(expected), np.finfo(float).tiny)


def _run(output_root: Path) -> int:
    started = time.perf_counter()
    frozen_driver = _freeze_output_root(output_root)
    pair_result = json.loads(RESULT.read_text(encoding="utf-8"))
    _pin(RESULT, RESULT_SHA256)
    if pair_result.get("status") != "COMPLETED_CONDITIONAL_L25_RT0_P1_PAIR":
        raise ValueError("pair result is not accepted")
    base = _load_base()
    rt0 = _load_npz(RT0_FIELD, RT0_FIELD_SHA256)
    p1 = _load_npz(P1_FIELD, P1_FIELD_SHA256)
    drive_npz = _load_npz(DRIVE_NPZ, DRIVE_NPZ_SHA256)
    topology = _load_npz(TOPOLOGY, TOPOLOGY_SHA256)
    mesh = _load_npz(MESH, MESH_SHA256)
    data = base._triangle_geometry(mesh, topology, drive_npz, rt0, p1)
    expected = pair_result["gap"]
    actual = {"rt0_energy_ohm": float(data["rt_energy"]), "p1_gradient_energy_ohm": float(data["p1_energy"]),
              "cross_integral_ohm": float(data["cross_energy"]), "gap_ohm": float(np.sum(data["gap"]))}
    expected4 = {key: float(expected[key]) for key in actual}
    relative = {key: _relative(actual[key], expected4[key]) for key in actual}
    if any(value > REL_TOL for value in relative.values()):
        raise ValueError(f"saved pair energy reproduction gate failed: {relative}")
    if not np.all(np.isfinite(data["gap"])) or np.any(data["gap"] < -np.finfo(float).eps * max(abs(actual["gap_ohm"]), 1.0)):
        raise ValueError("per-cell gap is not finite/nonnegative")
    aspect = _aspect(mesh, data)
    total_gap = actual["gap_ohm"]
    order = np.argsort(-data["gap"], kind="stable")
    free_count = int(data["free"].size)
    mark90, arrays90 = _mark(data, topology, order, total_gap, 0.90, free_count)
    mark99, arrays99 = _mark(data, topology, order, total_gap, 0.99, free_count)
    top_entries = []
    for index in order[:24]:
        entry = base._top_entry(int(index), data)
        entry.pop("aspect_longest_over_shortest", None)
        entry["aspect_longest_edge_squared_over_2area"] = float(aspect[index])
        top_entries.append(entry)
    npz_path = output_root / "gap-localization-02.npz"
    arrays = {
        "free_triangle_indices": np.asarray(data["free"], dtype=np.int64),
        "cell_gap_ohm": np.asarray(data["gap"], dtype=np.float64),
        "cell_area_m2": np.asarray(data["areas"], dtype=np.float64),
        "cell_centroid_um": np.asarray(data["centroids"], dtype=np.float64),
        "cell_aspect_longest_edge_squared_over_2area": aspect,
    }
    for prefix, selection in (("selected90", arrays90), ("selected99", arrays99)):
        arrays.update({f"{prefix}_{key}": value for key, value in selection.items()})
    with npz_path.open("xb") as handle:
        np.savez_compressed(handle, **arrays)
    npz_sha = _sha256(npz_path)
    cumulative = []
    cumulative_gap = np.cumsum(data["gap"][order])
    for n in (1, 10, 100, 1000, 10000, 100000, int(order.size)):
        if n <= order.size:
            cumulative.append({"n": n, "gap_ohm": float(cumulative_gap[n - 1]), "fraction_of_total": float(cumulative_gap[n - 1] / total_gap)})
    all_local_branch = np.asarray(data["local_branch"], dtype=np.int64)
    local_valid = all_local_branch >= 0
    branch_coverage = np.bincount(all_local_branch[local_valid], minlength=int(topology["branch_first_node"].size))
    safe_local_branch = np.maximum(all_local_branch, 0)
    natural_facet_count = int(np.count_nonzero(~local_valid))
    electrode_rim_facet_count = int(np.count_nonzero(local_valid & (branch_coverage[safe_local_branch] == 1)))
    interior_facet_count = int(np.count_nonzero(local_valid & (branch_coverage[safe_local_branch] == 2)))
    result = {
        "program": PROGRAM, "version": "0.23.1", "status": "COMPLETED_L25_RT0_P1_GAP_LOCALIZATION_02",
        "producer": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__)), "bytes": Path(__file__).stat().st_size, "frozen_driver": str(frozen_driver), "frozen_driver_sha256": _sha256(frozen_driver)},
        "inputs": {"pair_result": {"path": str(RESULT), "sha256": RESULT_SHA256}, "pair_frozen_driver": {"path": str(DRIVER), "sha256": FROZEN_DRIVER_SHA256}, "rt0_field": {"path": str(RT0_FIELD), "sha256": RT0_FIELD_SHA256}, "p1_field": {"path": str(P1_FIELD), "sha256": P1_FIELD_SHA256}, "drive_npz": {"path": str(DRIVE_NPZ), "sha256": DRIVE_NPZ_SHA256}, "topology_npz": {"path": str(TOPOLOGY), "sha256": TOPOLOGY_SHA256}, "mesh_npz": {"path": str(MESH), "sha256": MESH_SHA256}, "drive_result": {"path": str(DRIVE_RESULT), "sha256": DRIVE_RESULT_SHA256}, "base_localizer": {"path": str(BASE_PRODUCER), "sha256": BASE_PRODUCER_SHA256}},
        "gates": {"relative_tolerance": REL_TOL, "accepted_pair_energy_reproduction": {"actual": actual, "expected": expected4, "relative_error": relative, "all_relative_le_1e-10": True}, "finite_per_cell_gap": True, "nonnegative_per_cell_gap": True},
        "counts": {"free_cell_count": free_count, "positive_winding": int(data["positive"]), "negative_winding": int(data["negative"]), "natural_boundary_facet_count": natural_facet_count, "electrode_rim_facet_count": electrode_rim_facet_count, "interior_free_free_facet_count": interior_facet_count, "branch_count": int(branch_coverage.size), "original_coverage_two_branch_count": int(np.count_nonzero(branch_coverage == 2))},
        "total": {"gap_ohm": total_gap, "gap_milliohm": total_gap * 1e3, "receipt_gap_ohm": float(expected["gap_ohm"]), "receipt_identity_abs_error_ohm": abs(total_gap - float(expected["gap_ohm"])), "rt0_energy_ohm": actual["rt0_energy_ohm"], "cross_integral_ohm": actual["cross_integral_ohm"], "p1_gradient_energy_ohm": actual["p1_gradient_energy_ohm"], "three_point_degree": 2},
        "ranking": {"top_n_cumulative": cumulative, "top_entries": top_entries, "selected90": mark90, "selected99": mark99},
        "compact_npz": {"path": str(npz_path), "sha256": npz_sha, "arrays": sorted(arrays), "free_cell_vector_count": free_count},
        "coverage_contract": "Selected interior branches are the unique sorted union of all free/free local facets touching marked cells; every selected branch has original free-cell coverage exactly 2. Natural and electrode-rim facets remain explicitly excluded.",
        "nonclaims": ["Saved-field localization only; no LU, remeshing, convergence, causality, return-path, current-sharing, full-board, or PowerSI accuracy claim."],
        "elapsed_s": time.perf_counter() - started,
    }
    json_path = output_root / "gap-localization-02.json"
    with json_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"status": result["status"], "json": str(json_path), "npz": str(npz_path), "json_sha256": _sha256(json_path), "npz_sha256": npz_sha, "gap_milliohm": result["total"]["gap_milliohm"], "selected90_cells": mark90["selected_cell_count"], "selected99_cells": mark99["selected_cell_count"], "elapsed_s": result["elapsed_s"]}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="localize_astra_l25_rt0_p1_gap_02.py", description=PROGRAM)
    parser.add_argument("--output-root", default=str(OUTPUT_DEFAULT))
    args = parser.parse_args()
    return _run(Path(args.output_root).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
