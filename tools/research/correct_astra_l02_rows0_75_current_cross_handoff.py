"""SPD Decap PI Evaluator v0.23.1: correct the saved rows 0/75 cross handoff.

This performs saved-matrix algebra only.  It does not integrate a kernel or
form a board action.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np


PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
SOURCE = ROOT / "tools" / "research" / "assemble_astra_l02_rows0_75_whitened_current_cross.py"
RESULT = RESEARCH / "astra-l02-rows0-75-whitened-current-cross-20260912-04" / "result.json"
MATRICES = RESULT.with_name("rows0-75-whitened-current-cross.npz")
OUTPUT = RESEARCH / "astra-l02-rows0-75-current-cross-handoff-20260912-05"
PINS = {
    SOURCE: "47dda2776c457100b5daff8e71043ef10504f9b6fd10c7092f8bf767260076da",
    RESULT: "84bfd2e7cb4dc37bdfba7a2626cfa9847e13faa8538556e641da4dcef9c721f3",
    MATRICES: "85207af698e98dccb444db19361167664f10b03c1e2e25c795a41d19d773e48c",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    for path, expected in PINS.items():
        if digest(path) != expected:
            raise ValueError(f"pinned saved input changed: {path}")
    saved_result = json.loads(RESULT.read_text(encoding="utf-8"))
    if saved_result["status"] != "PASS_ROWS0_75_WHITENED_RT0_CROSS":
        raise ValueError("saved integral result is not accepted")
    with np.load(MATRICES, allow_pickle=False) as saved:
        columns = saved["global_current_columns"]
        incidence = saved["local_to_global_incidence"]
        physical_local = saved["physical_local_h"]
        point_local = saved["point_local_h"]

    local_self_delta = np.zeros((6, 6), dtype=np.float64)
    local_self_delta[:3, :3] = physical_local[:3, :3] - point_local[:3, :3]
    local_self_delta[3:, 3:] = physical_local[3:, 3:] - point_local[3:, 3:]
    local_cross_delta = np.zeros((6, 6), dtype=np.float64)
    local_cross_delta[:3, 3:] = physical_local[:3, 3:] - point_local[:3, 3:]
    local_cross_delta[3:, :3] = physical_local[3:, :3] - point_local[3:, :3]

    point_global = incidence.T @ point_local @ incidence
    self_delta_global = incidence.T @ local_self_delta @ incidence
    cross_delta_global = incidence.T @ local_cross_delta @ incidence
    physical_global = incidence.T @ physical_local @ incidence
    reconstructed = point_global + self_delta_global + cross_delta_global

    rng = np.random.default_rng(20260912)
    witness = rng.normal(size=len(columns)) + 1j * rng.normal(size=len(columns))
    matrix_difference = float(np.linalg.norm(reconstructed - physical_global))
    matrix_denominator = float(max(np.linalg.norm(value) for value in
                                   (point_global, self_delta_global, cross_delta_global, physical_global)))
    matrix_error = matrix_difference / max(matrix_denominator, 1e-300)
    component_actions = [value @ witness for value in
                         (point_global, self_delta_global, cross_delta_global, physical_global)]
    action_difference = float(np.linalg.norm(sum(component_actions[:3]) - component_actions[3]))
    action_denominator = float(max(np.linalg.norm(value) for value in component_actions))
    action_error = action_difference / max(action_denominator, 1e-300)
    cross_reciprocity = float(np.linalg.norm(cross_delta_global - cross_delta_global.T) /
                              max(np.linalg.norm(cross_delta_global), np.linalg.norm(cross_delta_global.T), 1e-300))
    shared = int(np.flatnonzero(columns == 75)[0])
    shared_diagonal = float(cross_delta_global[shared, shared])
    gates = {
        "saved_integral_result_passed": True,
        "matrix_reconstruction": matrix_error <= 2e-15,
        "complex_action_reconstruction": action_error <= 2e-15,
        "cross_delta_reciprocity": cross_reciprocity <= 2e-12,
        "shared_current_has_cross_diagonal": shared_diagonal != 0.0,
        "no_kernel_integration_or_board_action": True,
    }
    if not all(gates.values()):
        raise ValueError(f"corrected handoff gates failed: {gates}")

    OUTPUT.mkdir(exist_ok=False)
    row, col = np.nonzero(cross_delta_global)
    artifact = OUTPUT / "rows0-75-cross-delta.npz"
    np.savez_compressed(
        artifact,
        global_current_columns=columns,
        cross_delta_row=row,
        cross_delta_col=col,
        cross_delta_data_h=cross_delta_global[row, col],
        cross_delta_global_h=cross_delta_global,
        actual_point_component_global_h=point_global,
        existing_local_self_delta_global_h=self_delta_global,
        reconstructed_physical_component_global_h=reconstructed,
        source_physical_component_global_h=physical_global,
        complex_witness=witness,
    )
    report = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_CORRECTED_ROWS0_75_CROSS_HANDOFF_SAVED_ONLY",
        "driver_sha256": digest(Path(__file__)),
        "artifact_sha256": digest(artifact),
        "inputs": {str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        "superseded_saved_field": {
            "artifact": str(MATRICES.relative_to(ROOT)),
            "field": "sparse_correction_data_h",
            "reason": "It stores the full physical component. Adding it to the existing point action and local self delta double-counts both terms.",
        },
        "corrected_rule": "existing point action + existing local self delta + saved cross delta = saved physical two-cell component",
        "global_current_columns": columns.tolist(),
        "shared_global_current": 75,
        "shared_current_cross_delta_diagonal_h": shared_diagonal,
        "metrics": {
            "matrix_reconstruction_relative": matrix_error,
            "matrix_reconstruction_absolute_h": matrix_difference,
            "matrix_reconstruction_denominator_h": matrix_denominator,
            "complex_action_reconstruction_relative": action_error,
            "complex_action_reconstruction_absolute_h_a": action_difference,
            "complex_action_reconstruction_denominator_h_a": action_denominator,
            "cross_delta_reciprocity_relative": cross_reciprocity,
        },
        "gates": gates,
        "failed_pilot_audit": {
            "20260912-02": "Observer coordinates subtracted the anchor although the affine vertex term was already anchor-relative; this broke the RT0 affine field and directional reciprocity.",
            "20260912-03": "Rows of inv(L.T) were used as coefficients; the required basis columns are rows of inv(L), so the attempted basis was not self-energy normalized.",
        },
        "scope": "Saved two-cell matrices only. The cross delta retains its legitimate diagonal on shared current 75. Other touched global currents have incomplete support, and this is not a board or Green action.",
    }
    (OUTPUT / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "metrics": report["metrics"]}), flush=True)


if __name__ == "__main__":
    main()
