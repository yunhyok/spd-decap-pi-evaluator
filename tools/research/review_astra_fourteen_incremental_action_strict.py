"""Strict no-FMM review of the saved fourteen-current incremental action."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-fourteen-current-incremental-action-review-04"
PINS = {
    "tools/research/probe_astra_fourteen_current_incremental_action.py":
        "5d66daef4a9395becd6804c0062e046a51fdcc44452efef41e9690542a78e482",
    "outputs/research/astra-fourteen-current-incremental-action-01/result.json":
        "a88bfe63450ee4f3e47ba2b187b904d2aae007de7946f34df83cacaf006de07d",
    "outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz":
        "382137f5398cb7c1532c12ba264653172d59f90f2baabdc6462b4e25fff3de49",
    "outputs/research/astra-joint-fourteen-current-space-01/fourteen-current-space.npz":
        "3125d32b03485347679a360c93adcb1f35948c51987ae12110348743dbca967a",
    "outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz":
        "a58b0943bdef0baccb6669882d33e500bbb5edb668113d17858bff5e7c5f7c0a",
    "outputs/research/astra-seven-basis-full-action-01/full-actions.npz":
        "13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b",
    "outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz":
        "29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
}
C0 = 299_792_458.0


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as saved:
        return {name: saved[name] for name in saved.files}


def relative_difference(actual, expected, scale=None) -> float:
    actual = np.asarray(actual)
    expected = np.asarray(expected)
    if scale is None:
        scale = max(float(np.linalg.norm(expected)), 1.0e-300)
    return float(np.linalg.norm(actual - expected) / scale)


def scatter(columns: np.ndarray, signs: np.ndarray, local: np.ndarray) -> np.ndarray:
    answer = np.zeros((12546, local.shape[-1]), dtype=local.dtype)
    np.add.at(
        answer,
        columns.ravel(),
        (signs[:, :, None] * local).reshape(-1, local.shape[-1]),
    )
    return answer


def run() -> None:
    started = monotonic()
    assert not OUT.exists()
    probe = np.asarray([1.0, -2.0, 3.0])
    assert relative_difference(probe, probe) == 0.0
    assert abs(float(np.linalg.norm(probe) / np.linalg.norm(probe)) - 1.0) == 0.0
    for relative, expected in PINS.items():
        assert digest(ROOT / relative) == expected, relative

    result = json.loads(
        (ROOT / "outputs/research/astra-fourteen-current-incremental-action-01/result.json").read_text()
    )
    action = load(ROOT / "outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz")
    space = load(ROOT / "outputs/research/astra-joint-fourteen-current-space-01/fourteen-current-space.npz")
    parent = load(ROOT / "outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz")
    first = load(ROOT / "outputs/research/astra-seven-basis-full-action-01/full-actions.npz")
    registry = load(ROOT / "outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz")
    mesh = load(ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz")
    current = load(ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz")

    q = space["face_flux_basis"]
    resistance_gram = space["physical_r_gram"]
    columns = current["local_rt0_face_columns"]
    signs = current["local_rt0_face_signs"]
    tetrahedra = mesh["vertices_local_um"][mesh["cells"]] * 1.0e-6
    local = signs[:, :, None] * q[columns]
    assert q.shape == (12546, 14)
    assert np.array_equal(q, action["face_currents"])
    assert np.array_equal(q[:, :7], parent["face_currents"])
    assert relative_difference(resistance_gram, action["resistance_gram"]) == 0.0

    moments = np.einsum(
        "cin,cid->nd", local, tetrahedra.mean(axis=1)[:, None] - tetrahedra
    ) / 3.0
    moment_error = relative_difference(moments, action["exact_integrated_currents"])
    rank_term = 1.0e-7 * moments @ moments.T
    rank_term_error = relative_difference(rank_term, action["minus_ik_coefficient"])
    terminal_map = np.c_[first["terminal_flux"], np.zeros((4, 7))]
    terminal_error = relative_difference(terminal_map, action["terminal_flux"])
    assert max(moment_error, rank_term_error, terminal_error) < 1.0e-13

    pair_a = np.r_[registry["pair_a"], parent["new_pair_a"]]
    pair_b = np.r_[registry["pair_b"], parent["new_pair_b"]]
    exact_pair = np.r_[
        registry["reference_forward_reverse_h"][registry["pair_reference_index"], 0],
        parent["reference_forward_reverse_h"][parent["new_pair_reference_index"], 0],
    ]
    assert len(pair_a) == len(np.unique(pair_a * 5304 + pair_b)) == 150584
    assert np.all(pair_a < pair_b)

    rule_metrics = {}
    matrices = {}
    rebuilt_near = {}
    rebuilt_full = {}
    for rule in ("jacobi", "xg31"):
        point_pair = np.r_[
            first[f"{rule}_pair_point_blocks"],
            parent[f"{rule}_new_pair_point_blocks"],
        ]
        assert point_pair.shape == exact_pair.shape == (150584, 4, 4)
        local_new = local[:, :, 7:]
        near_local = np.einsum(
            "cij,cjn->cin",
            registry["exact_cell_self_h"] - first[f"{rule}_self_point_blocks"],
            local_new,
        )
        np.add.at(
            near_local,
            pair_a,
            np.einsum(
                "pij,pjn->pin", exact_pair - point_pair, local_new[pair_b]
            ),
        )
        np.add.at(
            near_local,
            pair_b,
            np.einsum(
                "pji,pjn->pin", exact_pair - point_pair, local_new[pair_a]
            ),
        )
        near_action = scatter(columns, signs, near_local)
        near_error = relative_difference(
            near_action, action[f"{rule}_new_near_action"]
        )
        assert near_error < 2.0e-13

        # The saved local FMM point action is independently scattered and then
        # combined with the rebuilt exact self/pair correction.
        point_action = scatter(
            columns, signs, action[f"{rule}_new_local_point_action"]
        )
        new_full = point_action + near_action
        new_full_error = relative_difference(
            new_full, action[f"{rule}_new_full_action"]
        )
        assert new_full_error < 2.0e-13
        full_action = np.c_[parent[f"{rule}_full_action"], new_full]
        full_action_error = relative_difference(
            full_action, action[f"{rule}_full_action"]
        )
        assert full_action_error < 2.0e-13

        matrix = q.T @ full_action
        matrix_error = relative_difference(matrix, action[f"{rule}_matrix"])
        old_block_error = relative_difference(
            matrix[:7, :7], parent[f"{rule}_matrix"]
        )
        cross_error = relative_difference(
            matrix[:7, 7:], matrix[7:, :7].T
        )
        symmetry_error = relative_difference(matrix, matrix.T)
        assert max(matrix_error, old_block_error) < 2.0e-13
        assert max(cross_error, symmetry_error) < 1.0e-10

        energy_scale = np.linalg.inv(
            np.linalg.cholesky((matrix + matrix.T) / 2.0)
        )
        energy_scale_error = relative_difference(
            energy_scale, action[f"{rule}_energy_scale"]
        )
        assert energy_scale_error < 2.0e-13

        # Fresh ordinary direct sums at every saved target check both source
        # coefficients and the saved FMM output without invoking FMM again.
        barycentric = action[f"{rule}_barycentric"]
        weights = action[f"{rule}_normalized_weights"]
        points_cell = np.einsum("pi,cid->cpd", barycentric, tetrahedra)
        weighted_local = (
            (points_cell[:, :, None] - tetrahedra[:, None])
            * weights[None, :, None, None]
            / 3.0
        )
        sources = np.einsum(
            "cin,cpid->cpnd", local, weighted_local, optimize=True
        ).reshape(-1, 14, 3)
        points = points_cell.reshape(-1, 3)
        direct_ids = action[f"{rule}_direct_ids"]
        distances = np.linalg.norm(points[direct_ids, None] - points[None], axis=2)
        threshold = np.ptp(points, axis=0).max() * np.finfo(float).eps
        inverse = np.divide(
            1.0, distances, out=np.zeros_like(distances), where=distances > threshold
        )
        direct = np.einsum("tp,pnd->tnd", inverse, sources[:, 7:])
        direct_reference_error = relative_difference(
            direct, action[f"{rule}_direct_reference"]
        )
        direct_fmm_error = relative_difference(
            direct, action[f"{rule}_fmm_checks"]
        )
        assert direct_reference_error < 2.0e-13
        assert direct_fmm_error < 1.0e-10

        matrices[rule] = matrix
        rebuilt_near[rule] = near_action
        rebuilt_full[rule] = full_action
        rule_metrics[rule] = {
            "near_action_relative": near_error,
            "new_full_action_relative": new_full_error,
            "full_action_relative": full_action_error,
            "matrix_relative": matrix_error,
            "old7_block_relative": old_block_error,
            "old_new_cross_reciprocity_relative": cross_error,
            "matrix_symmetry_relative": symmetry_error,
            "energy_scale_relative": energy_scale_error,
            "fresh_direct_reference_relative": direct_reference_error,
            "fresh_direct_vs_saved_fmm_relative": direct_fmm_error,
        }

    response_metrics = {}
    result_history = {row["rule"]: row for row in result["history"]}
    eigenvalues, eigenvectors = np.linalg.eigh(
        (resistance_gram + resistance_gram.T) / 2.0
    )
    root_r = (eigenvectors * np.sqrt(eigenvalues)) @ eigenvectors.T
    forcing = np.r_[np.eye(7), np.zeros((7, 7))]
    for rule in ("jacobi", "xg31"):
        expected_rows = {
            float(row["frequency_hz"]): row
            for row in result_history[rule]["local_retained7_to14_diagnostics"]
        }
        rows = []
        maximum_saved_difference = 0.0
        for frequency in (1.0e3, 1.0e6, 1.0e7, 1.0e8, 1.0e9):
            omega = 2.0 * np.pi * frequency
            impedance = (
                resistance_gram
                + 1j * omega * matrices[rule]
                + omega * (omega / C0) * rank_term
            )
            response14 = np.linalg.solve(impedance, forcing)
            response7 = np.linalg.solve(impedance[:7, :7], np.eye(7))
            difference = response14 - np.r_[response7, np.zeros((7, 7))]
            modal_change = float(
                np.linalg.norm(root_r @ difference)
                / np.linalg.norm(root_r @ response14)
            )
            admittance7 = terminal_map[:, :7] @ np.linalg.solve(
                impedance[:7, :7], terminal_map[:, :7].T
            )
            admittance14 = terminal_map @ np.linalg.solve(
                impedance, terminal_map.T
            )
            admittance_change = float(
                np.linalg.norm(admittance14 - admittance7)
                / np.linalg.norm(admittance14)
            )
            expected = expected_rows[frequency]
            saved_difference = max(
                abs(modal_change - expected["unit_modal_current_change_r_frobenius"]),
                abs(admittance_change - expected["local_four_contact_admittance_relative"]),
            )
            maximum_saved_difference = max(maximum_saved_difference, saved_difference)
            rows.append(
                {
                    "frequency_hz": frequency,
                    "unit_modal_current_change_r_frobenius": modal_change,
                    "local_four_contact_admittance_relative": admittance_change,
                }
            )
        assert maximum_saved_difference < 5.0e-13
        response_metrics[rule] = {
            "maximum_saved_absolute_difference": maximum_saved_difference,
            "rows": rows,
        }

    xg_energy = np.linalg.inv(
        np.linalg.cholesky((matrices["xg31"] + matrices["xg31"].T) / 2.0)
    )
    rule_difference = float(
        np.linalg.norm(
            xg_energy @ (matrices["xg31"] - matrices["jacobi"]) @ xg_energy.T,
            2,
        )
    )
    reported_rule_difference = result["metrics"]["empirical_fourteen_energy_difference"]
    assert abs(rule_difference - reported_rule_difference) < 1.0e-14
    assert (rule_difference < 5.0e-5) is result["metrics"]["empirical_fourteen_rule_gate"]

    OUT.mkdir(parents=True)
    metrics_path = OUT / "independent-metrics.npz"
    np.savez_compressed(
        metrics_path,
        integrated_current_moments=moments,
        terminal_flux=terminal_map,
        jacobi_rebuilt_near_action=rebuilt_near["jacobi"],
        xg31_rebuilt_near_action=rebuilt_near["xg31"],
        jacobi_rebuilt_full_action=rebuilt_full["jacobi"],
        xg31_rebuilt_full_action=rebuilt_full["xg31"],
        jacobi_matrix=matrices["jacobi"],
        xg31_matrix=matrices["xg31"],
    )
    gates = {
        "pinned_inputs": True,
        "difference_metric_self_check": True,
        "all_150584_raw_pairs_replayed": len(pair_a) == 150584,
        "moments_rank_term_terminal": max(moment_error, rank_term_error, terminal_error) < 1.0e-13,
        "both_near_actions": max(row["near_action_relative"] for row in rule_metrics.values()) < 2.0e-13,
        "both_full_actions": max(row["full_action_relative"] for row in rule_metrics.values()) < 2.0e-13,
        "both_matrices_and_cross": max(
            max(row["matrix_relative"], row["old_new_cross_reciprocity_relative"])
            for row in rule_metrics.values()
        ) < 1.0e-10,
        "fresh_direct_targets": max(row["fresh_direct_vs_saved_fmm_relative"] for row in rule_metrics.values()) < 1.0e-10,
        "saved_local_response_reconstructed": max(
            row["maximum_saved_absolute_difference"] for row in response_metrics.values()
        ) < 5.0e-13,
        "reported_rule_stop_reconstructed": rule_difference >= 5.0e-5,
    }
    assert all(gates.values())
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_INCREMENTAL_FOURTEEN_ACTION_WITH_RULE_STOP",
        "elapsed_s": monotonic() - started,
        "reviewer_sha256": digest(Path(__file__)),
        "pins": PINS,
        "metrics_artifact_sha256": digest(metrics_path),
        "metrics": {
            "pair_count": len(pair_a),
            "integrated_current_moment_relative": moment_error,
            "minus_ik_rank_term_relative": rank_term_error,
            "terminal_map_relative": terminal_error,
            "rules": rule_metrics,
            "responses": response_metrics,
            "empirical_fourteen_energy_difference": rule_difference,
            "empirical_fourteen_rule_gate": rule_difference < 5.0e-5,
        },
        "gates": gates,
        "supersedes": (
            "The prior compressed reviewer compared norms without subtracting arrays and then misplaced the rule body "
            "outside its loop. It produced no receipt; its apparent relative-1 near mismatch was a reviewer defect."
        ),
        "scope": (
            "Independent saved-source, local correction, scatter, direct-target, matrix and defined local-response "
            "reconstruction for the fourteen-current vector action. The empirical Jacobi/XG rule gate remains STOP. "
            "No scalar/contact charge, exterior return, material coupling, actual field/current solution, impedance, "
            "mesh/current-space convergence, board response or PowerSI accuracy is approved."
        ),
    }
    (OUT / "independent-review.json").write_text(
        json.dumps(review, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({"status": review["status"], "elapsed_s": review["elapsed_s"], "metrics": review["metrics"]}))


if __name__ == "__main__":
    try:
        run()
    except Exception:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "failure.json").write_text(
            json.dumps(
                {
                    "status": "STOP_STRICT_FOURTEEN_ACTION_REVIEW",
                    "traceback": traceback.format_exc(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        raise
