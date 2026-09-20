"""SPD Decap PI Evaluator v0.23.1: smoke-only single-prism RT0 self blocks."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]; R = ROOT / "outputs/research"
RT0 = ROOT / "tools/research/astra_prism_rt0_self.py"
REGISTRY = R / "astra-l02-single-support-self-20260912-smoke/registry.npz"
REGISTRY_RESULT = R / "astra-l02-single-support-self-20260912-smoke/result.json"
CURRENT = R / "astra-retained-sheet-current-green-20260912-02/current-support.npz"
CURRENT_DRIVER = ROOT / "tools/research/prepare_astra_retained_sheet_current_green.py"
PINS = {RT0: "9f8e2facac380694878d96970d74d348b5949eaef2573e1f4cb77ce4fa26e8d3",
        REGISTRY: "e2e4627ae4ab9b60876b74dc99f8951890fec5865a31a6184c6dfa1fc9e76c6b",
        REGISTRY_RESULT: "dac43f396658176836214c641747fb1c2eff14f4b1a0449165a7cef9fc88181b",
        CURRENT: "24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b",
        CURRENT_DRIVER: "967a64f8fdc7468a01aeefd8c319cd4e4a2c84c6a8c54d0a75899bfebca9eb9b"}
MU0_4PI = 1e-7; H = 20e-6


def sha(path: Path) -> str: return sha256(path.read_bytes()).hexdigest()


def rt0_signs_old_to_canonical(old_triangle: np.ndarray, canonical_triangle: np.ndarray) -> np.ndarray:
    """Explicit old-vertex→canonical column permutation and orientation signs."""
    permutation = np.asarray([np.flatnonzero(np.all(old_triangle == p, axis=1))[0] for p in canonical_triangle], dtype=np.int64)
    return permutation


def saved_six_point_block(triangle: np.ndarray, local_signs: np.ndarray) -> np.ndarray:
    """Call the authoritative ``chunks()`` basis×volume-weight arithmetic."""
    from prepare_astra_retained_sheet_current_green import chunks
    triangle = np.asarray(triangle, float); local_signs = np.asarray(local_signs, np.int8)
    payload = {"piece_triangle_xy_m": triangle[None], "piece_parent_free_ordinal": np.asarray([0]),
               "original_free_triangle_xy_m": triangle[None], "compact_local_columns": np.arange(3)[None],
               "local_signs": local_signs[None]}
    points, _, basis, weight = next(chunks(payload, chunk_size=1))
    points, basis, weight = points[0], basis[0], weight[0]
    distance = np.linalg.norm(points[:, None] - points[None, :], axis=-1); inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > 0)
    return MU0_4PI * np.einsum("q,qic,qr,rjc,r->ij", weight, basis, inverse, basis, weight)


def beta_l(beta_q: float, reflection: complex) -> float:
    m = (1 + abs(reflection)) / (1 - abs(reflection)); eta = beta_q / (m + beta_q)
    lower, upper = (1-eta)**4/(1+eta)**2, (1+eta)**4/(1-eta)**2
    return float(max(1-lower, upper-1))


def energy_indicator(previous: np.ndarray, current: np.ndarray) -> float:
    chol = np.linalg.cholesky((current + current.T) / 2)
    error = np.linalg.solve(chol, previous-current)
    error = np.linalg.solve(chol, error.T).T
    return float(np.max(abs(np.linalg.eigvalsh((error + error.T) / 2))))


def assemble_group(index: int, triangle: np.ndarray, beta: float, reflection: complex) -> dict:
    import astra_prism_rt0_self as rt0
    previous = None; history = []; geometry_bound = beta_l(beta, reflection)
    for order in (8, 16, 32, 64, 128, 256, 512, 1024):
        physical = rt0.prism_rt0_self(triangle, H, order)
        quad = None if previous is None else energy_indicator(previous, physical)
        indicator = float((quad + geometry_bound) / (1-geometry_bound)) if quad is not None else None
        history.append({"order": order, "quadrature_energy_indicator": quad, "combined_indicator": indicator})
        if indicator is not None and indicator <= 5e-5: break
        previous = physical
    if history[-1]["combined_indicator"] is None or history[-1]["combined_indicator"] > 5e-5:
        raise RuntimeError({"group": index, "history": history})
    return {"group": index, "canonical_block_h": physical.tolist(), "beta_L": geometry_bound, "history": history}


def checkpoint(path: Path, completed: list[dict]) -> None:
    staged = path.with_suffix(".tmp"); staged.write_text(json.dumps(completed, indent=2, allow_nan=False), encoding="utf-8"); staged.replace(path)


def smoke(output: Path) -> None:
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    completed = []
    try:
        _smoke(output, completed)
    except Exception as error:
        (output / "failure.json").write_text(json.dumps({"status": "STOP_SINGLE_PRISM_CURRENT_SELF_SMOKE", "error": repr(error),
                                                          "completed": completed}, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise


def _smoke(output: Path, completed: list[dict]) -> None:
    for path, value in PINS.items(): assert sha(path) == value, path
    with np.load(REGISTRY, allow_pickle=False) as z:
        prisms, group_bound = z["prisms"], z["group_bound"]
        prism_columns = int(z["prism_columns"])
        group, original_rows, charge_columns = z["group"][:prism_columns], z["original_rows"][:prism_columns], z["columns"][:prism_columns]
    with np.load(CURRENT, allow_pickle=False) as z:
        piece, parent, original, signs, columns = z["piece_triangle_xy_m"], z["piece_parent_free_ordinal"], z["original_free_triangle_xy_m"], z["local_signs"], z["compact_local_columns"]
    from astra_stratified_charge_green import source_background
    _, eps, _ = source_background(); reflection = (eps[1]-eps[0])/(eps[1]+eps[0])
    parent_count = np.bincount(parent, minlength=len(original))
    single_piece_by_parent = np.full(len(original), -1, dtype=np.int64)
    single_piece_mask = parent_count[parent] == 1
    single_piece_by_parent[parent[single_piece_mask]] = np.flatnonzero(single_piece_mask)
    q0_member = int(np.flatnonzero(charge_columns == 0)[0]); q0_group = int(group[q0_member])
    requested = [q0_group, 0, 1, 953, 102037, 102038, len(prisms)-1, len(prisms)//2]
    requested = list(dict.fromkeys(requested))
    for index in range(len(prisms)):
        if len(requested) == 8: break
        if index not in requested: requested.append(index)
    if len(requested) != 8: raise RuntimeError("eight distinct smoke groups unavailable")
    candidate_members = np.flatnonzero(np.isin(group, requested))
    members_by_group = {index: candidate_members[group[candidate_members] == index] for index in requested}
    selected = []
    for index in requested:
        match = None
        for member in members_by_group[index]:
            row = int(original_rows[member])
            if row >= len(parent_count) or parent_count[row] != 1: continue
            piece_index = int(single_piece_by_parent[row]); actual = original[row]; candidate = piece[piece_index]
            opposite = np.sum((candidate[[1, 2, 0]] - candidate[[2, 0, 1]])**2, axis=1)
            member_canonical = candidate[np.argsort(opposite, kind="stable")]
            if not np.array_equal(member_canonical, prisms[index]): continue
            perm = rt0_signs_old_to_canonical(actual, member_canonical)
            if np.array_equal(actual[perm], member_canonical):
                match = (index, int(member), row, piece_index, perm, member_canonical); break
        if match is None: raise RuntimeError(f"group {index} has no uncut representative member")
        selected.append(match)
    selected_by_group = {index: (member, row, piece_index, perm, member_canonical)
                         for index, member, row, piece_index, perm, member_canonical in selected}
    jobs = [(int(index), prisms[index], float(group_bound[index]), reflection) for index in selected_by_group]
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(assemble_group, *job) for job in jobs]
        for future in as_completed(futures):
            item = future.result(); index = item["group"]
            member, row, piece_index, perm, member_canonical = selected_by_group[index]
            if parent_count[row] != 1: raise AssertionError("selected group is not one actual current piece")
            actual = original[row]; canonical_point = saved_six_point_block(member_canonical, np.ones(3))
            actual_point = saved_six_point_block(actual, signs[row]); transformed = np.zeros((3, 3)); transformed[np.ix_(perm, perm)] = canonical_point; transformed *= signs[row, :, None] * signs[row, None, :]
            point_mapping_relative = float(np.linalg.norm(actual_point-transformed) /
                                           max(np.linalg.norm(actual_point), np.linalg.norm(transformed), 1e-300))
            if point_mapping_relative > 2e-8: raise AssertionError("canonical point block mapping")
            canonical = np.asarray(item.pop("canonical_block_h")); physical = np.zeros((3, 3)); physical[np.ix_(perm, perm)] = canonical; physical *= signs[row, :, None] * signs[row, None, :]
            item.update(original_charge_row=row, permutation_old_of_canonical=perm.tolist(), local_signs=signs[row].tolist(), global_current_columns=columns[row].tolist(),
                        point_mapping_relative=point_mapping_relative, physical_block_h=physical.tolist(), saved_point_block_h=actual_point.tolist(),
                        self_delta_block_h=(physical-actual_point).tolist())
            completed.append(item); checkpoint(output / "checkpoint.json", completed)
    report = {"status": "PASS_SINGLE_PRISM_CURRENT_SELF_SMOKE", "driver_sha256": sha(Path(__file__)), "pins": {str(k.relative_to(ROOT)):v for k,v in PINS.items()}, "selected_members": [{"group":a,"member":b,"original_row":c,"piece_index":d,"permutation_old_of_canonical":e.tolist()} for a,b,c,d,e,_ in selected], "cases": sorted(completed, key=lambda x:x["group"]), "scope": "Eight representative uncut same-prism full 3x3 RT0 blocks only; no full run, union, nonself, global L, or board solve."}
    (output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--smoke", action="store_true"); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args()
    if not args.smoke: parser.error("smoke only")
    smoke(args.output.resolve())
