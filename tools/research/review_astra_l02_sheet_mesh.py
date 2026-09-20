"""Review a saved L02 mesh/stiffness snapshot without rebuilding geometry."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from math import sin, pi
from pathlib import Path
import time
from zipfile import ZipFile

import numpy as np
import shapely
from scipy.sparse import coo_matrix, csc_matrix


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def direct_areas(xy: np.ndarray, triangles: np.ndarray, *, batch: int = 131_072) -> tuple[np.ndarray, float]:
    areas = np.empty(len(triangles), dtype=np.float64)
    minimum = float("inf")
    for first in range(0, len(triangles), batch):
        last = min(first + batch, len(triangles))
        points = xy[triangles[first:last]]
        det = ((points[:, 1, 0] - points[:, 0, 0]) * (points[:, 2, 1] - points[:, 0, 1])
               - (points[:, 2, 0] - points[:, 0, 0]) * (points[:, 1, 1] - points[:, 0, 1]))
        require(np.all(np.isfinite(det)) and np.all(det != 0.0), "saved mesh has nonfinite/zero-area triangle")
        values = 0.5 * np.abs(det)
        areas[first:last] = values
        minimum = min(minimum, float(values.min()))
    return areas, minimum


def sampled_direct_rows(
    xy: np.ndarray,
    triangles: np.ndarray,
    matrix: csc_matrix,
    contact_nodes: np.ndarray,
) -> tuple[int, float]:
    node_count = len(xy)
    triangle_samples = np.linspace(0, len(triangles) - 1, 257, dtype=np.int64)
    contact_samples = contact_nodes[np.linspace(0, len(contact_nodes) - 1, 257, dtype=np.int64)]
    nodes = np.unique(np.r_[np.linspace(0, node_count - 1, 257, dtype=np.int64),
                            triangles[triangle_samples].ravel(), contact_samples])
    node_to_row = np.full(node_count, -1, dtype=np.int32)
    node_to_row[nodes] = np.arange(len(nodes), dtype=np.int32)
    touching = np.any(node_to_row[triangles] >= 0, axis=1)
    tri = triangles[touching]
    points = xy[tri]
    det = ((points[:, 1, 0] - points[:, 0, 0]) * (points[:, 2, 1] - points[:, 0, 1])
           - (points[:, 2, 0] - points[:, 0, 0]) * (points[:, 1, 1] - points[:, 0, 1]))
    require(np.all(det != 0.0), "sampled incident triangle is degenerate")
    b = points[:, [1, 2, 0], 1] - points[:, [2, 0, 1], 1]
    c = points[:, [2, 0, 1], 0] - points[:, [1, 2, 0], 0]
    local = (b[:, :, None] * b[:, None, :] + c[:, :, None] * c[:, None, :]) / (2.0 * np.abs(det[:, None, None]))
    mapped = node_to_row[tri]
    row_parts: list[np.ndarray] = []
    column_parts: list[np.ndarray] = []
    value_parts: list[np.ndarray] = []
    for local_row in range(3):
        selected = mapped[:, local_row] >= 0
        row_parts.append(np.repeat(mapped[selected, local_row], 3))
        column_parts.append(tri[selected].ravel())
        value_parts.append(local[selected, local_row, :].ravel())
    reference = coo_matrix((np.concatenate(value_parts),
                            (np.concatenate(row_parts), np.concatenate(column_parts))),
                           shape=(len(nodes), node_count)).tocsr()
    actual = matrix.tocsr()[nodes]
    difference = actual - reference
    error = 0.0
    for row in range(len(nodes)):
        actual_scale = float(np.max(np.abs(actual.data[actual.indptr[row]:actual.indptr[row + 1]]), initial=0.0))
        reference_scale = float(np.max(np.abs(reference.data[reference.indptr[row]:reference.indptr[row + 1]]), initial=0.0))
        row_difference = float(np.max(np.abs(difference.data[difference.indptr[row]:difference.indptr[row + 1]]), initial=0.0))
        error = max(error, row_difference / max(actual_scale, reference_scale, 1.0))
    return len(nodes), error


def self_check() -> None:
    xy = np.asarray([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    tri = np.asarray([[0, 1, 2], [1, 2, 3]], dtype=np.int64)
    expected = coo_matrix((np.asarray([1, -0.5, -0.5, -0.5, 0.5, 0, -0.5, 0, 0.5,
                                      0.5, 0, -0.5, 0, 0.5, -0.5, -0.5, -0.5, 1.0]),
                           (np.repeat(tri, 3, axis=1).ravel(), np.tile(tri, (1, 3)).ravel())),
                          shape=(4, 4)).tocsc()
    areas, minimum = direct_areas(xy, tri)
    require(np.array_equal(areas, [0.5, 0.5]) and minimum == 0.5, "area self-check failed")
    count, error = sampled_direct_rows(xy, tri, expected, np.asarray([0, 3]))
    require(count == 4 and error < 1e-15, "sampled stiffness self-check failed")


def run(args: argparse.Namespace) -> dict[str, object]:
    started = time.monotonic()
    run_root = args.run.resolve()
    result_path = run_root / "result.json"
    driver_path = run_root / "driver-at-run.py"
    require(sha(result_path) == args.result_sha256, "mesh result SHA-256 differs")
    require(sha(args.guard) == args.guard_sha256, "external guard SHA-256 differs")
    result = json.loads(result_path.read_bytes())
    guard = json.loads(args.guard.read_bytes())
    require(result["status"] == "COMPLETED_CONDITIONAL_L02_SHEET_MESH_PREFLIGHT", "mesh status differs")
    clean_exit = guard.get("status") == "COMPLETED_NATIVE_WORKER" and guard.get("exit_code") == 0
    execution = {"clean_worker_exit": clean_exit, "guard_status": guard.get("status"),
                 "guard_exit_code": guard.get("exit_code"), "acceptance_basis": "clean worker completion"}
    if not clean_exit:
        # This exact completed artifact was retained before an observed cleanup wait.
        # No other failed worker/result can enter this recovery path.
        require(args.post_result_termination is not None, "nonclean mesh worker requires exact recovery evidence")
        require(args.result_sha256 == "2ff4183b727373d8f6d9dbd32a39fcfc55a92b19bc55328b7e79082e766bfe59"
                and result["script_sha256"] == "176ae7bd1aa0d9727cc905b4f805fa613a04988bd2a3fce58268351a2ad70a9c"
                and result["snapshot"]["sha256"] == "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9"
                and args.guard_sha256 == "eb8bd673d37164bb83c7496949f0da8864f4c921e684f2a0ed7b098f0531f142",
                "failed worker is outside the single reviewed recovery tuple")
        recovery_sha = sha(args.post_result_termination)
        require(recovery_sha == args.post_result_termination_sha256
                == "7d1920380b67703c8a7d0b73332b912ee1eb88ffc498cf1f2a1b0de41941692c",
                "post-result termination evidence differs")
        recovery = json.loads(args.post_result_termination.read_text(encoding="utf-8-sig"))
        require(guard.get("status") == "STOP_NATIVE_WORKER_EXIT" and guard.get("owned_pid") == 17564
                and guard.get("exit_code") == 4294967295 and recovery["owned_pid"] == 17564
                and recovery["status"] == "OWNED_POST_RESULT_WORKER_TERMINATED" and recovery["has_exited"] is True,
                "owned worker termination was not confirmed")
        require(recovery["driver_sha256"] == result["script_sha256"]
                and recovery["result_sha256"] == args.result_sha256
                and recovery["snapshot_sha256"] == result["snapshot"]["sha256"]
                and recovery["snapshot_bytes"] == Path(result["snapshot"]["path"]).stat().st_size,
                "recovery evidence does not bind the saved outputs")
        wait_s = (datetime.fromisoformat(recovery["observed_at_utc"])
                  - datetime.fromisoformat(recovery["result_saved_at_utc"])).total_seconds()
        require(wait_s > 180 and recovery["observed_cpu_s"] == recovery["earlier_cpu_s"],
                "post-result wait evidence differs")
        execution.update(acceptance_basis="independently checked saved artifact after supervised post-result termination",
            post_result_wait_s=wait_s, post_result_termination={"path": str(args.post_result_termination), "sha256": recovery_sha},
            blocked_call="unconfirmed; watchdog cleanup is an inference", supervisor_exit_code_available=False)
    require(guard["sampled_peak_private_bytes"] <= guard["max_memory_bytes"] and
            guard["elapsed_s"] <= guard["max_runtime_s"], "mesh external resource bound failed")
    require(sha(driver_path) == result["script_sha256"], "frozen mesh driver differs")
    require(result["source_status"] == "COMPLETED_CONDITIONAL_L02_PAD_AUGMENTED_DOMAIN_WITH_PAD_COVERAGE_RESIDUE", "source residue status was lost")
    require(result["source_pad_coverage"]["strict"] is False and result["source_pad_coverage"]["uncovered_area_um2"] > 0.0,
            "source pad residue was not preserved")
    source_result_path = Path(result["source_result"]["path"])
    require(sha(source_result_path) == result["source_result"]["sha256"], "source result differs")
    source = json.loads(source_result_path.read_bytes())
    source_review_path = source_result_path.parent / "independent-review.json"
    require(sha(source_review_path) == result["source_review_sha256"], "source review differs")
    for metadata in result["source_outputs"].values():
        require(sha(Path(metadata["path"])) == metadata["sha256"], "source output differs")
    snapshot = Path(result["snapshot"]["path"])
    require(sha(snapshot) == result["snapshot"]["sha256"], "mesh snapshot differs")
    with ZipFile(snapshot) as archive:
        require(archive.testzip() is None, "mesh snapshot ZIP CRC failed")

    with np.load(snapshot, allow_pickle=False) as saved:
        require(all(saved[name].dtype.kind != "O" for name in saved.files), "object array in mesh snapshot")
        xy = np.asarray(saved["node_xy_um"], dtype=np.float64)
        triangles = np.asarray(saved["triangles"], dtype=np.int64)
        contact_support = np.asarray(saved["contact_support_index"], dtype=np.int64)
        contact_nodes = np.asarray(saved["contact_node_indices"], dtype=np.int64)
        node_ptr = np.asarray(saved["contact_node_indptr"], dtype=np.int64)
        contact_triangles = np.asarray(saved["contact_triangle_indices"], dtype=np.int64)
        triangle_ptr = np.asarray(saved["contact_triangle_indptr"], dtype=np.int64)
        native = np.asarray(saved["native_drill_support_index"], dtype=np.int64)
        excluded = np.asarray(saved["excluded_drill_support_index"], dtype=np.int64)
        native_active = np.asarray(saved["native_active_finite_index"], dtype=np.int64)
        native_ordinal = np.asarray(saved["native_source_via_ordinal"], dtype=np.int64)
        excluded_ids = json.loads(np.asarray(saved["excluded_via_ids_json_utf8"], dtype=np.uint8).tobytes().decode("utf-8"))
        shape = tuple(map(int, saved["stiffness_shape"]))
        matrix = csc_matrix((saved["stiffness_data"], saved["stiffness_indices"], saved["stiffness_indptr"]), shape=shape)

    require(xy.shape == (result["mesh_nodes"], 2) and triangles.shape == (result["mesh_triangles"], 3), "mesh result counts differ")
    require(np.all(np.isfinite(xy)) and triangles.min() >= 0 and triangles.max() < len(xy), "mesh arrays are invalid")
    contact_count = result["contact_count"]
    require(contact_support.shape == (contact_count,) and node_ptr.shape == triangle_ptr.shape == (contact_count + 1,), "contact pointer shape differs")
    require(node_ptr[0] == triangle_ptr[0] == 0 and node_ptr[-1] == len(contact_nodes) and triangle_ptr[-1] == len(contact_triangles), "contact pointer endpoint differs")
    node_counts, triangle_counts = np.diff(node_ptr), np.diff(triangle_ptr)
    require(np.all(node_counts == 16) and np.all(triangle_counts == 14), "saved contact topology is not 16-node/14-triangle")
    require(contact_nodes.min() >= 0 and contact_nodes.max() < len(xy) and len(np.unique(contact_nodes)) == len(contact_nodes), "contact node indices overlap/out of range")
    require(contact_triangles.min() >= 0 and contact_triangles.max() < len(triangles) and len(np.unique(contact_triangles)) == len(contact_triangles), "contact triangle indices overlap/out of range")

    support_path = Path(source["outputs"]["support_map"]["path"])
    with np.load(support_path, allow_pickle=False) as supports:
        support_xy = np.column_stack((supports["support_x_pm"], supports["support_y_pm"])) .astype(np.float64) * 1e-6
        support_diameter = np.asarray(supports["support_diameter_pm"], dtype=np.float64) * 1e-6
        expected_support = np.flatnonzero(supports["support_is_drill"])
        require(np.array_equal(native, supports["native_drill_support_index"]), "native support map differs")
        require(np.array_equal(excluded, supports["excluded_drill_support_index"]), "excluded support map differs")
        require(np.array_equal(native_active, supports["native_active_finite_index"]), "native active map differs")
        require(np.array_equal(native_ordinal, supports["native_source_via_ordinal"]), "native ordinal map differs")
        require(excluded_ids == json.loads(supports["excluded_via_ids_json_utf8"].tobytes().decode("utf-8")), "excluded via IDs differ")
    require(np.array_equal(contact_support, expected_support), "contact support order differs")

    node_owner = np.full(len(xy), -1, dtype=np.int32)
    repeated_owner = np.repeat(np.arange(contact_count, dtype=np.int32), node_counts)
    node_owner[contact_nodes] = repeated_owner
    triangle_owner = np.full(len(triangles), -1, dtype=np.int32)
    repeated_triangle_owner = np.repeat(np.arange(contact_count, dtype=np.int32), triangle_counts)
    triangle_owner[contact_triangles] = repeated_triangle_owner
    require(np.all(node_owner[triangles[contact_triangles]] == repeated_triangle_owner[:, None]), "contact triangle uses a node outside its contact")
    centers = support_xy[contact_support]
    radii = 0.5 * support_diameter[contact_support]
    node_centers = np.repeat(centers, node_counts, axis=0)
    node_radii = np.repeat(radii, node_counts)
    radius_squared_error = np.abs(np.sum((xy[contact_nodes] - node_centers) ** 2, axis=1) - node_radii ** 2)
    radius_relative_error = float(np.max(radius_squared_error / np.maximum(node_radii ** 2, 1.0)))
    require(radius_relative_error < 2e-12, "contact node is not on its source drill circle")

    areas, minimum_area = direct_areas(xy, triangles)
    domain = shapely.from_wkb(Path(source["outputs"]["domain"]["path"]).read_bytes())
    area_sum = float(np.sum(areas, dtype=np.longdouble))
    domain_area_relative = abs(area_sum - float(domain.area)) / float(domain.area)
    require(domain_area_relative < 2e-11, "triangle area sum differs from source domain")
    contact_area = np.bincount(repeated_triangle_owner, weights=areas[contact_triangles], minlength=contact_count)
    exact_contact_area = 8.0 * radii ** 2 * sin(pi / 8.0)
    contact_area_relative = float(np.max(np.abs(contact_area - exact_contact_area) / exact_contact_area))
    require(contact_area_relative < 2e-11, "contact triangle area differs from 16-gon source support")

    matrix.check_format(full_check=True)
    require(matrix.shape == (len(xy), len(xy)) and matrix.nnz == result["stiffness_nnz"] and np.all(np.isfinite(matrix.data)), "stiffness shape/data differs")
    matrix_scale = max(float(np.max(np.abs(matrix.data), initial=0.0)), 1.0)
    symmetry = float(np.max(np.abs((matrix - matrix.T).data), initial=0.0)) / matrix_scale
    row_sum = float(np.max(np.abs(np.asarray(matrix.sum(axis=1)).ravel()))) / matrix_scale
    sample_count, formula_error = sampled_direct_rows(xy, triangles, matrix, contact_nodes)
    require(symmetry < 2e-12 and row_sum < 2e-12 and formula_error < 2e-12, "stiffness identity gate failed")

    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_SAVED_L02_PAD_DOMAIN_MESH_AND_STIFFNESS_INDEPENDENT_REVIEW",
        "reviewed": {
            "result": {"path": str(result_path), "sha256": args.result_sha256},
            "frozen_driver": {"path": str(driver_path), "sha256": result["script_sha256"]},
            "snapshot": result["snapshot"],
            "guard": {"path": str(args.guard), "sha256": args.guard_sha256, "status": guard.get("status")},
            "source_result": result["source_result"],
            "source_review_sha256": result["source_review_sha256"],
        },
        "checks": {
            "mesh_nodes": len(xy), "mesh_triangles": len(triangles), "minimum_triangle_area_um2": minimum_area,
            "triangle_area_sum_um2": area_sum, "source_domain_area_um2": float(domain.area),
            "domain_area_relative_error": domain_area_relative,
            "contact_count": contact_count, "contact_node_count": len(contact_nodes),
            "contact_triangle_count": len(contact_triangles), "all_contact_nodes_disjoint": True,
            "all_contact_triangles_disjoint": True, "contact_node_radius_squared_relative_error": radius_relative_error,
            "contact_area_max_relative_error": contact_area_relative,
            "stiffness_nnz": matrix.nnz, "stiffness_relative_symmetry_error": symmetry,
            "stiffness_relative_row_sum": row_sum, "sampled_stiffness_row_count": sample_count,
            "sampled_direct_element_stiffness_relative_error": formula_error,
        },
        "source_pad_coverage": result["source_pad_coverage"],
        "execution": execution,
        "scope": "Independent saved-array/hash/contact/source-map/area/stiffness review. It does not repeat domain union, contact predicates, triangulation, full element reassembly, G/C projection, contact reduction, Kron, LU, circuit solve, convergence, return closure, or PowerSI comparison.",
        "elapsed_s": time.monotonic() - started,
    }
    output = run_root / "independent-review.json"
    with output.open("xb") as stream:
        stream.write((json.dumps(review, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
    print(json.dumps({"status": review["status"], "review_sha256": sha(output), "checks": review["checks"]}, sort_keys=True))
    return review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--run", type=Path)
    parser.add_argument("--result-sha256")
    parser.add_argument("--guard", type=Path)
    parser.add_argument("--guard-sha256")
    parser.add_argument("--post-result-termination", type=Path)
    parser.add_argument("--post-result-termination-sha256")
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print("PASS_L02_SAVED_MESH_REVIEW_SELF_CHECK")
        return
    if any(value is None for value in (args.run, args.result_sha256, args.guard, args.guard_sha256)):
        parser.error("--run/--result-sha256/--guard/--guard-sha256 are required")
    run(args)


if __name__ == "__main__":
    main()
