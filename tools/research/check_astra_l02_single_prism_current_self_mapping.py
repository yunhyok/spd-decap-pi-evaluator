"""SPD Decap PI Evaluator v0.23.1: saved eight-case RT0 point-map check."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
SMOKE = R / "astra-l02-single-prism-current-self-20260912-smoke-04/result.json"
CURRENT = R / "astra-retained-sheet-current-green-20260912-02/current-support.npz"
DRIVER = ROOT / "tools/research/assemble_astra_l02_single_prism_current_self.py"
PINS = {SMOKE: "9f1f427a615f5bec8d15a84a106a7a24f4f6260a19f152c77163405760cfe3db",
        CURRENT: "24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b",
        DRIVER: "93a87a20fa5b0aa46654d3376f3177226f1e3a1d103abc1d467d593f7ebc13ae"}
MU0_4PI = 1e-7


def sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def point_rule(triangle: np.ndarray, signs: np.ndarray):
    from prepare_astra_retained_sheet_current_green import chunks
    triangle = np.asarray(triangle, dtype=float)
    payload = {"piece_triangle_xy_m": triangle[None], "piece_parent_free_ordinal": np.asarray([0]),
               "original_free_triangle_xy_m": triangle[None], "compact_local_columns": np.arange(3)[None],
               "local_signs": np.asarray(signs, dtype=np.int8)[None]}
    points, _, basis, weights = next(chunks(payload, chunk_size=1))
    return points[0], basis[0], weights[0]


def block(points: np.ndarray, basis: np.ndarray, weights: np.ndarray) -> np.ndarray:
    distance = np.linalg.norm(points[:, None] - points[None, :], axis=-1)
    inverse = np.divide(1.0, distance, out=np.zeros_like(distance), where=distance > 0)
    return MU0_4PI * np.einsum("q,qic,qr,rjc,r->ij", weights, basis, inverse, basis, weights)


def run(output: Path) -> None:
    if output.exists():
        raise FileExistsError(output)
    for path, expected in PINS.items():
        if sha(path) != expected:
            raise ValueError(f"input hash differs: {path.name}")
    saved = json.loads(SMOKE.read_text(encoding="utf-8"))
    cases = {int(case["group"]): case for case in saved["cases"]}
    selected = saved["selected_members"]
    with np.load(CURRENT, allow_pickle=False) as z:
        original = z["original_free_triangle_xy_m"]
        local_signs = z["local_signs"]
    rows = []
    for item in selected:
        group, row = int(item["group"]), int(item["original_row"])
        permutation = np.asarray(item["permutation_old_of_canonical"], dtype=np.int64)
        signs = local_signs[row]
        points, unsigned_basis, weights = point_rule(original[row], np.ones(3, dtype=np.int8))
        direct_points, signed_basis, direct_weights = point_rule(original[row], signs)
        if not np.array_equal(points, direct_points) or not np.array_equal(weights, direct_weights):
            raise AssertionError("authoritative point rule changed with signs")
        direct = block(points, signed_basis, weights)
        canonical_same_rule = block(points, unsigned_basis[:, permutation, :], weights)
        reconstructed = np.zeros((3, 3))
        reconstructed[np.ix_(permutation, permutation)] = canonical_same_rule
        reconstructed *= signs[:, None] * signs[None, :]
        mapping_relative = float(np.linalg.norm(direct - reconstructed) /
                                 max(np.linalg.norm(direct), np.linalg.norm(reconstructed), 1e-300))
        saved_point = np.asarray(cases[group]["saved_point_block_h"])
        saved_relative = float(np.linalg.norm(direct - saved_point) /
                               max(np.linalg.norm(direct), np.linalg.norm(saved_point), 1e-300))
        rows.append({"group": group, "original_row": row, "same_rule_mapping_relative": mapping_relative,
                     "saved_point_replay_relative": saved_relative,
                     "regenerated_canonical_mapping_relative_diagnostic": float(cases[group]["point_mapping_relative"])})
    gates = {"eight_cases": len(rows) == 8,
             "same_authoritative_rule_permutation_and_sign": max(row["same_rule_mapping_relative"] for row in rows) <= 2e-12,
             "saved_actual_point_block_replay": max(row["saved_point_replay_relative"] for row in rows) <= 2e-12,
             "no_physical_green_reintegration": True}
    report = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
              "status": "PASS_SAVED_RT0_POINT_MAPPING" if all(gates.values()) else "STOP_SAVED_RT0_POINT_MAPPING",
              "driver_sha256": sha(Path(__file__)), "inputs": {str(path.relative_to(ROOT)): value for path, value in PINS.items()},
              "gates": gates, "cases": rows,
              "scope": "Saved eight-case physical matrices are reused. Only the authoritative six-point current rule is regenerated to check local permutation/sign mapping; no physical Green integration or full run."}
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    (output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if not all(gates.values()):
        raise RuntimeError("saved point mapping failed")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
