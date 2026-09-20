"""SPD Decap PI Evaluator v0.23.1: all single-prism L02 RT0 self blocks."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
REGISTRY = R / "astra-l02-single-support-self-20260912-smoke/registry.npz"
REGISTRY_RESULT = R / "astra-l02-single-support-self-20260912-smoke/result.json"
CURRENT = R / "astra-retained-sheet-current-green-20260912-02/current-support.npz"
CURRENT_DRIVER = ROOT / "tools/research/prepare_astra_retained_sheet_current_green.py"
RT0 = ROOT / "tools/research/astra_prism_rt0_self.py"
MATERIAL = ROOT / "tools/research/astra_stratified_charge_green.py"
ANGULAR = R / "astra-prism-rt0-angular-20260912/result.json"
SMOKE = R / "astra-l02-single-prism-current-self-20260912-smoke-04/result.json"
SMOKE_DRIVER = R / "astra-l02-single-prism-current-self-20260912-smoke-04/driver-at-run.py"
MAPPING = R / "astra-l02-single-prism-current-self-20260912-mapping-01/result.json"
PINS = {REGISTRY: "e2e4627ae4ab9b60876b74dc99f8951890fec5865a31a6184c6dfa1fc9e76c6b",
        REGISTRY_RESULT: "dac43f396658176836214c641747fb1c2eff14f4b1a0449165a7cef9fc88181b",
        CURRENT: "24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b",
        CURRENT_DRIVER: "967a64f8fdc7468a01aeefd8c319cd4e4a2c84c6a8c54d0a75899bfebca9eb9b",
        RT0: "5ba0637a2c3ded46c1e2203abaa7f56bf5b96f49d0d4108f13df23a38e65b5ad",
        MATERIAL: "5808ad457d3d58b47413f47f02e30ce5f51c3365f16eb4d2562b2a742007451b",
        ANGULAR: "6849d54a81560c6fee7d67a0f00c18c91764968bcf079d4370c5a724350fb77a",
        SMOKE: "9f1f427a615f5bec8d15a84a106a7a24f4f6260a19f152c77163405760cfe3db",
        SMOKE_DRIVER: "93a87a20fa5b0aa46654d3376f3177226f1e3a1d103abc1d467d593f7ebc13ae",
        MAPPING: "a9fea871abe8c9c3a45f8f798fb13a32a0d2256469669477cd1396eb45e05683"}
HEIGHT_M = 20e-6
MU0_4PI = 1e-7
TARGET = 5e-5
MAX_ORDER = 256
FRONTIER_GROUPS = 256


def sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def beta_l(beta_q: float, reflection: complex) -> float:
    factor = (1 + abs(reflection)) / (1 - abs(reflection))
    eta = beta_q / (factor + beta_q)
    lower = (1 - eta) ** 4 / (1 + eta) ** 2
    upper = (1 + eta) ** 4 / (1 - eta) ** 2
    return float(max(1 - lower, upper - 1))


def energy_indicator(previous: np.ndarray, current: np.ndarray) -> float:
    chol = np.linalg.cholesky((current + current.T) / 2)
    transformed = np.linalg.solve(chol, previous - current)
    transformed = np.linalg.solve(chol, transformed.T).T
    return float(np.max(abs(np.linalg.eigvalsh((transformed + transformed.T) / 2))))


def evaluate_batch(jobs: list[tuple[int, np.ndarray, float]], reflection: complex) -> list[dict]:
    import astra_prism_rt0_self as rt0
    rows = []
    for group, triangle, beta_q in jobs:
        try:
            geometry_bound = beta_l(beta_q, reflection)
            previous = None
            history = []
            for order in (8, 16, 32, 64, 128, MAX_ORDER):
                current = rt0.prism_rt0_self(triangle, HEIGHT_M, order, angular_transform="hyperbolic")
                quadrature = None if previous is None else energy_indicator(previous, current)
                combined = None if quadrature is None else float((quadrature + geometry_bound) / (1 - geometry_bound))
                history.append({"order": order, "quadrature_energy_indicator": quadrature, "combined_indicator": combined})
                if combined is not None and combined <= TARGET:
                    break
                previous = current
            if history[-1]["combined_indicator"] is None or history[-1]["combined_indicator"] > TARGET:
                raise RuntimeError(f"group did not converge by order {MAX_ORDER}: {history}")
            symmetry = float(np.linalg.norm(current - current.T) / np.linalg.norm(current))
            minimum_eigenvalue = float(np.linalg.eigvalsh((current + current.T) / 2).min())
            if symmetry > 2e-12 or minimum_eigenvalue <= 0 or not np.isfinite(current).all():
                raise RuntimeError("nonfinite, nonsymmetric, or nonpositive RT0 self block")
            rows.append({"group": group, "block": current, "order": history[-1]["order"],
                         "quadrature": history[-1]["quadrature_energy_indicator"],
                         "combined": history[-1]["combined_indicator"], "symmetry": symmetry,
                         "minimum_eigenvalue_h": minimum_eigenvalue, "error": None})
        except Exception:
            rows.append({"group": group, "error": traceback.format_exc()})
    return rows


def point_blocks(payload: dict, batch_size: int = 2048):
    from prepare_astra_retained_sheet_current_green import chunks
    for points, _, basis, weights in chunks(payload, chunk_size=batch_size):
        distance = np.linalg.norm(points[:, :, None, :] - points[:, None, :, :], axis=-1)
        inverse = np.divide(1.0, distance, out=np.zeros_like(distance), where=distance > 0)
        yield MU0_4PI * np.einsum("tq,tqic,tqr,trjc,tr->tij", weights, basis, inverse, basis, weights)


def checkpoint(output: Path, block: np.ndarray, order: np.ndarray, quadrature: np.ndarray,
               combined: np.ndarray, completed: np.ndarray, failures: list[dict], stage: str) -> None:
    temporary = output / "checkpoint.partial.npz"
    np.savez_compressed(temporary, canonical_physical_block_h=block, order=order,
                        quadrature_energy_indicator=quadrature, combined_indicator=combined, completed=completed)
    temporary.replace(output / "checkpoint.npz")
    receipt = {"stage": stage, "completed_groups": int(np.count_nonzero(completed)), "failures": failures}
    staged = output / "checkpoint.json.tmp"
    staged.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    staged.replace(output / "checkpoint.json")


def load_inputs():
    for path, expected in PINS.items():
        if sha(path) != expected:
            raise ValueError(f"input hash differs: {path.name}")
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    if mapping["status"] != "PASS_SAVED_RT0_POINT_MAPPING" or not all(mapping["gates"].values()):
        raise ValueError("saved eight-case point mapping is not qualified")
    with np.load(REGISTRY, allow_pickle=False) as z:
        registry = {key: z[key] for key in z.files}
    with np.load(CURRENT, allow_pickle=False) as z:
        current = {key: z[key] for key in z.files}
    prism_columns = int(registry["prism_columns"])
    parent = current["piece_parent_free_ordinal"]
    counts = np.bincount(parent, minlength=len(current["original_free_triangle_xy_m"]))
    single_piece_indices = np.flatnonzero(counts[parent] == 1)
    single_rows = parent[single_piece_indices]
    if (len(single_rows) != prism_columns or
            not np.array_equal(registry["original_rows"][:prism_columns], single_rows) or
            len(registry["prisms"]) != 116253 or len(np.unique(registry["columns"][:prism_columns])) != prism_columns):
        raise ValueError("single-prism registry/current ownership differs")
    exact_uncut = np.all(current["piece_triangle_xy_m"][single_piece_indices] ==
                         current["original_free_triangle_xy_m"][single_rows], axis=(1, 2))
    clipped_single_rows = single_rows[~exact_uncut]
    expected_clipped = np.asarray([1368287, 1368290, 1371074, 1373742, 1373745], dtype=np.int64)
    if not np.array_equal(np.sort(clipped_single_rows), expected_clipped):
        raise ValueError("clipped count-one current ownership differs")
    member_indices = np.flatnonzero(exact_uncut)
    return (registry, current, single_piece_indices[exact_uncut], single_rows[exact_uncut],
            member_indices, clipped_single_rows)


def reuse_smoke(block, order, quadrature, combined, completed, current, required_group):
    saved = json.loads(SMOKE.read_text(encoding="utf-8"))
    reused = []
    for case in saved["cases"]:
        group = int(case["group"])
        if not required_group[group]:
            continue
        row = int(case["original_charge_row"])
        permutation = np.asarray(case["permutation_old_of_canonical"], dtype=np.int64)
        signs = current["local_signs"][row]
        physical = np.asarray(case["physical_block_h"])
        canonical = physical[np.ix_(permutation, permutation)] * signs[permutation, None] * signs[None, permutation]
        block[group] = canonical
        order[group] = int(case["history"][-1]["order"])
        quadrature[group] = float(case["history"][-1]["quadrature_energy_indicator"])
        combined[group] = float(case["history"][-1]["combined_indicator"])
        completed[group] = True
        reused.append(group)
    return sorted(reused)


def run(output: Path, resume: bool) -> None:
    started = monotonic()
    if output.exists() and not resume:
        raise FileExistsError(output)
    if not output.exists():
        output.mkdir(parents=True)
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    elif not (output / "driver-at-run.py").is_file() or (output / "driver-at-run.py").read_bytes() != Path(__file__).read_bytes():
        raise ValueError("resume source differs")
    failures = []
    try:
        registry, current, single_piece_indices, single_rows, member_indices, clipped_single_rows = load_inputs()
        groups = len(registry["prisms"])
        member_group = registry["group"][member_indices]
        required_group = np.zeros(groups, dtype=bool)
        required_group[np.unique(member_group)] = True
        if resume and (output / "checkpoint.npz").is_file():
            with np.load(output / "checkpoint.npz", allow_pickle=False) as z:
                block, order = z["canonical_physical_block_h"], z["order"]
                quadrature, combined, completed = z["quadrature_energy_indicator"], z["combined_indicator"], z["completed"]
        else:
            block = np.full((groups, 3, 3), np.nan)
            order = np.zeros(groups, dtype=np.int16)
            quadrature = np.full(groups, np.nan)
            combined = np.full(groups, np.nan)
            completed = np.zeros(groups, dtype=bool)
        reused = reuse_smoke(block, order, quadrature, combined, completed, current, required_group)
        from astra_stratified_charge_green import source_background
        _, eps, _ = source_background()
        reflection = complex((eps[1] - eps[0]) / (eps[1] + eps[0]))

        def calculate(group_ids: np.ndarray, stage: str) -> None:
            nonlocal failures
            jobs = [(int(group), registry["prisms"][group], float(registry["group_bound"][group])) for group in group_ids]
            last_checkpoint = monotonic()
            with ProcessPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(evaluate_batch, jobs[first:first + 64], reflection)
                           for first in range(0, len(jobs), 64)]
                for future in as_completed(futures):
                    for row in future.result():
                        group = int(row["group"])
                        if row["error"] is not None:
                            failures.append({"group": group, "traceback": row["error"]})
                            continue
                        block[group], order[group] = row["block"], row["order"]
                        quadrature[group], combined[group], completed[group] = row["quadrature"], row["combined"], True
                    if failures:
                        checkpoint(output, block, order, quadrature, combined, completed, failures, stage)
                        for pending in futures:
                            pending.cancel()
                        raise RuntimeError("RT0 representative group failure")
                    if monotonic() - last_checkpoint >= 5:
                        checkpoint(output, block, order, quadrature, combined, completed, failures, stage)
                        print(json.dumps({"stage": stage, "completed_groups": int(np.count_nonzero(completed)),
                                          "failures": len(failures), "elapsed_s": monotonic() - started}), flush=True)
                        last_checkpoint = monotonic()
            checkpoint(output, block, order, quadrature, combined, completed, failures, stage)

        frontier = np.flatnonzero(required_group & ~completed)[:FRONTIER_GROUPS]
        if len(frontier):
            calculate(frontier, "frontier")
        frontier_receipt = {"new_groups": int(len(frontier)), "elapsed_s": monotonic() - started,
                            "maximum_order": int(order[frontier].max(initial=0)),
                            "maximum_combined_indicator": float(combined[frontier].max(initial=0.0))}
        if failures or frontier_receipt["maximum_order"] > MAX_ORDER or frontier_receipt["maximum_combined_indicator"] > TARGET:
            raise RuntimeError("frontier qualification failed")
        remaining = np.flatnonzero(required_group & ~completed)
        if len(remaining):
            calculate(remaining, "all_representatives")
        if (not np.all(completed[required_group]) or not np.isfinite(block[required_group]).all() or
                np.max(combined[required_group]) > TARGET):
            raise RuntimeError("representative completion failed")

        prism_columns = len(member_indices)
        piece = current["piece_triangle_xy_m"][single_piece_indices]
        if not np.array_equal(piece, current["original_free_triangle_xy_m"][single_rows]):
            raise RuntimeError("included current support is not the exact original RT0 prism")
        opposite = np.sum((piece[:, [1, 2, 0]] - piece[:, [2, 0, 1]]) ** 2, axis=2)
        permutation = np.argsort(opposite, axis=1, kind="stable").astype(np.int8)
        signs = current["local_signs"][single_rows]
        physical = np.zeros((prism_columns, 3, 3))
        canonical = block[member_group]
        indices = np.arange(prism_columns)
        for left in range(3):
            for right in range(3):
                physical[indices, permutation[:, left], permutation[:, right]] = canonical[:, left, right]
        physical *= signs[:, :, None] * signs[:, None, :]
        payload = {"piece_triangle_xy_m": piece, "piece_parent_free_ordinal": np.arange(prism_columns),
                   "original_free_triangle_xy_m": current["original_free_triangle_xy_m"][single_rows],
                   "compact_local_columns": current["compact_local_columns"][single_rows],
                   "local_signs": signs}
        point = np.empty_like(physical)
        offset = 0
        for values in point_blocks(payload):
            point[offset:offset + len(values)] = values
            offset += len(values)
        if offset != prism_columns or not np.isfinite(point).all():
            raise RuntimeError("actual six-point current self did not cover every single prism")
        member_indicator = combined[member_group]
        artifact = output / "single-prism-current-self.npz"
        np.savez_compressed(artifact, charge_columns=registry["columns"][member_indices], original_free_ordinals=single_rows,
                            global_current_columns=current["compact_local_columns"][single_rows], local_signs=signs,
                            canonical_vertex_permutation=permutation, representative_group=member_group,
                            physical_block_h=physical, point_block_h=point, physical_minus_point_block_h=physical - point,
                            combined_refinement_geometry_indicator=member_indicator,
                            representative_physical_block_h=block, representative_order=order,
                            representative_quadrature_energy_indicator=quadrature,
                            representative_combined_indicator=combined, required_representative_group=required_group,
                            excluded_clipped_single_free_ordinals=clipped_single_rows)
        physical_reciprocity = float(np.linalg.norm(physical - physical.transpose(0, 2, 1)) /
                                     max(np.linalg.norm(physical), 1e-300))
        point_reciprocity = float(np.linalg.norm(point - point.transpose(0, 2, 1)) /
                                  max(np.linalg.norm(point), 1e-300))
        gates = {"all_required_representative_groups": bool(np.all(completed[required_group])), "all_uncut_single_prism_members": offset == prism_columns,
                 "every_included_piece_equals_original": bool(np.array_equal(piece, current["original_free_triangle_xy_m"][single_rows])),
                 "five_clipped_single_cells_excluded": bool(np.array_equal(np.sort(clipped_single_rows), np.asarray([1368287, 1368290, 1371074, 1373742, 1373745]))),
                 "eight_linear_smoke_blocks_reused": len(reused) == 8,
                 "maximum_indicator": float(np.max(member_indicator)) <= TARGET,
                 "finite_physical_point_and_delta": bool(np.isfinite(physical).all() and np.isfinite(point).all() and np.isfinite(physical - point).all()),
                 "physical_reciprocity": physical_reciprocity <= 2e-12,
                 "point_reciprocity": point_reciprocity <= 2e-12, "no_full_board_green_or_solve": True}
        report = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                  "status": "PASS_ALL_SINGLE_PRISM_RT0_CURRENT_SELF" if all(gates.values()) else "STOP_ALL_SINGLE_PRISM_RT0_CURRENT_SELF",
                  "driver_sha256": sha(Path(__file__)), "artifact_sha256": sha(artifact),
                  "inputs": {str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
                  "counts": {"registry_representative_groups": groups, "required_uncut_representative_groups": int(np.count_nonzero(required_group)),
                             "uncut_single_prism_members": prism_columns, "excluded_clipped_single_cells": len(clipped_single_rows),
                             "reused_linear_smoke_groups": len(reused)},
                  "frontier": frontier_receipt,
                  "metrics": {"maximum_order": int(order[required_group].max()),
                              "maximum_combined_indicator": float(combined[required_group].max()),
                              "physical_reciprocity_relative": physical_reciprocity,
                              "point_reciprocity_relative": point_reciprocity},
                  "gates": gates, "elapsed_s": monotonic() - started,
                  "scope": "Finite-thickness same-prism 3x3 RT0 current self and matching authoritative six-point subtraction for single-piece retained L02 cells only. No cross-prism, union, nonself, global magnetic action, or board solve."}
        (output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if not all(gates.values()):
            raise RuntimeError("final gates failed")
    except Exception:
        failure = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                   "status": "STOP_ALL_SINGLE_PRISM_RT0_CURRENT_SELF", "driver_sha256": sha(Path(__file__)),
                   "failures": failures, "traceback": traceback.format_exc(), "elapsed_s": monotonic() - started}
        (output / "failure.json").write_text(json.dumps(failure, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise


def preflight() -> None:
    registry, current, single_piece_indices, single_rows, member_indices, clipped_single_rows = load_inputs()
    print(json.dumps({"status": "PASS_SINGLE_PRISM_CURRENT_SELF_PREFLIGHT",
                      "registry_representative_groups": len(registry["prisms"]),
                      "required_uncut_representative_groups": len(np.unique(registry["group"][member_indices])),
                      "uncut_single_members": len(single_rows), "excluded_clipped_single_rows": clipped_single_rows.tolist(),
                      "piece_indices": len(single_piece_indices), "driver_sha256": sha(Path(__file__))}, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        preflight()
    elif args.output is None:
        parser.error("--output is required for full assembly")
    else:
        run(args.output.resolve(), args.resume)
