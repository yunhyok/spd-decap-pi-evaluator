"""SPD Decap PI Evaluator v0.23.1: bounded seven-rule attribution addendum.

This does not repeat any point-pair integration.  It pins the completed
attribution, reconstructs the XG31 energy scale from the saved XG31 matrix,
and checks the pair universes and exact-correction exclusion against the raw-H
registry.  The outside block remains an algebraic remainder, not an
independently evaluated far-field block.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic
import traceback

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/attribute_astra_seven_xg31_jac27_pairs.py": "3e192a8fdc81fc912f7a4194a2d59990cdf47ef1d926974750b365ad68989a8e",
    "outputs/research/astra-seven-xg31-jac27-pair-attribution-01/result.json": "376117c9c9b64dedcb3f6d7c1f15b3c31b495d1aed24c8690c1665cfa33ed303",
    "outputs/research/astra-seven-xg31-jac27-pair-attribution-01/attribution.npz": "8a21bcd0a08a57537930af01cce954b8ba5d01a09c0f85f95d7ced09318dbf02",
    "tools/research/probe_astra_seven_basis_full_action.py": "cee40c743a43ecd25ca80532e30ad2f2a7edfa121d5484d9183990a2f83e92ca",
    "outputs/research/astra-seven-basis-full-action-01/result.json": "0c7305addd7251b33f794a8cdab5cf9ab1908826e5edc7bc34f4d43f53abc5ef",
    "outputs/research/astra-seven-basis-full-action-01/full-actions.npz": "13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b",
    "outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz": "29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933",
    "tools/research/probe_astra_fmm3d_runtime.py": "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def spectral(scale: np.ndarray, block: np.ndarray) -> float:
    return float(np.linalg.norm(scale @ block @ scale.T, 2))


def group_scores(scale: np.ndarray, block: np.ndarray) -> np.ndarray:
    transformed = np.einsum("ab,gbc,dc->gad", scale, block, scale)
    return np.linalg.norm(transformed, axis=(1, 2))


def pair_keys(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    assert a.dtype.kind in "iu" and b.dtype.kind in "iu"
    assert np.all(a < b)
    return (a.astype(np.int64) << np.int64(32)) | b.astype(np.int64)


def run(output: Path) -> int:
    started = monotonic()
    arrays: dict[str, np.ndarray] = {}
    try:
        for relative, expected in PINS.items():
            assert digest(ROOT / relative) == expected, relative
        producer_result = json.loads((ROOT / "outputs/research/astra-seven-xg31-jac27-pair-attribution-01/result.json").read_text())
        full_result = json.loads((ROOT / "outputs/research/astra-seven-basis-full-action-01/result.json").read_text())
        assert producer_result["status"] == "PASS_SEVEN_PAIR_RULE_ATTRIBUTION"
        assert full_result["status"] == "COMPLETED_SEVEN_BASIS_FULL_FINE_ACTION"
        assert full_result["empirical_seven_mode_rule_gate"] is False

        with np.load(ROOT / "outputs/research/astra-seven-basis-full-action-01/full-actions.npz", allow_pickle=False) as saved:
            jacobi = np.asarray(saved["jacobi_matrix"], dtype=np.float64)
            xg31 = np.asarray(saved["xg31_matrix"], dtype=np.float64)
            saved_xg_scale = np.asarray(saved["xg31_energy_scale"], dtype=np.float64)
        with np.load(ROOT / "outputs/research/astra-seven-xg31-jac27-pair-attribution-01/attribution.npz", allow_pickle=False) as saved:
            a = {name: saved[name] for name in saved.files}
        with np.load(ROOT / "outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz", allow_pickle=False) as saved:
            registry_a = saved["pair_a"].astype(np.int64)
            registry_b = saved["pair_b"].astype(np.int64)

        sym_xg = (xg31 + xg31.T) / 2.0
        xg_scale = np.linalg.solve(np.linalg.cholesky(sym_xg), np.eye(7))
        identity_error = float(np.linalg.norm(xg_scale @ sym_xg @ xg_scale.T - np.eye(7), 2))
        saved_scale_error = float(np.linalg.norm(xg_scale - saved_xg_scale) / max(np.linalg.norm(xg_scale), 1e-300))

        ta, tb = a["touch_pair_a"], a["touch_pair_b"]
        na, nb = a["nontouch_pair_a"], a["nontouch_pair_b"]
        tk, nk = a["touch_keep"].astype(bool), a["nontouch_keep"].astype(bool)
        assert len(ta) == len(tb) == len(tk) == 416_525
        assert len(na) == len(nb) == len(nk) == 257_477
        touch_keys, nontouch_keys = pair_keys(ta, tb), pair_keys(na, nb)
        assert len(np.unique(touch_keys)) == len(touch_keys)
        assert len(np.unique(nontouch_keys)) == len(nontouch_keys)
        assert len(np.intersect1d(touch_keys, nontouch_keys, assume_unique=True)) == 0

        excluded = np.r_[touch_keys[~tk], nontouch_keys[~nk]]
        registry_keys = pair_keys(registry_a, registry_b)
        assert len(excluded) == len(registry_keys) == 58_252
        assert np.array_equal(np.sort(excluded), np.sort(registry_keys))

        full = xg31 - jacobi
        touch = np.sum(a["touch_delta"], axis=0)
        nontouch = np.sum(a["nontouch_delta"], axis=0)
        outside = full - touch - nontouch
        full_replay = float(np.linalg.norm(full - a["full_delta"]) / max(np.linalg.norm(full), 1e-300))
        touch_replay = float(np.linalg.norm(touch - a["touch_sum"]) / max(np.linalg.norm(full), 1e-300))
        nontouch_replay = float(np.linalg.norm(nontouch - a["nontouch_sum"]) / max(np.linalg.norm(full), 1e-300))
        outside_replay = float(np.linalg.norm(outside - a["outside_sum"]) / max(np.linalg.norm(full), 1e-300))

        touch_group_score = group_scores(xg_scale, a["touch_group_delta"])
        nontouch_group_score = group_scores(xg_scale, a["nontouch_group_delta"])
        touch_order = np.argsort(touch_group_score)[::-1]
        nontouch_order = np.argsort(nontouch_group_score)[::-1]

        gates = {
            "all_inputs_pinned": True,
            "pair_universes_exact": True,
            "registry_exclusion_exact": True,
            "xg31_scale_reconstructed": identity_error < 2e-12 and saved_scale_error < 2e-12,
            "saved_blocks_replayed": max(full_replay, touch_replay, nontouch_replay, outside_replay) < 2e-12,
        }
        assert all(gates.values()), gates
        arrays = {
            "xg31_energy_scale_reconstructed": xg_scale,
            "touch_group_score_updated_xg": touch_group_score,
            "nontouch_group_score_updated_xg": nontouch_group_score,
            "touch_group_order_updated_xg": touch_order,
            "nontouch_group_order_updated_xg": nontouch_order,
        }
        metrics = {
            "touch_pair_count": int(len(ta)),
            "nontouch_pair_count": int(len(na)),
            "registry_excluded_pair_count": int(len(excluded)),
            "xg31_identity_error": identity_error,
            "xg31_saved_scale_relative_error": saved_scale_error,
            "full_replay_relative": full_replay,
            "touch_replay_full_scale": touch_replay,
            "nontouch_replay_full_scale": nontouch_replay,
            "outside_replay_full_scale": outside_replay,
            "updated_xg_full_spectral": spectral(xg_scale, full),
            "updated_xg_touch_spectral": spectral(xg_scale, touch),
            "updated_xg_nontouch_spectral": spectral(xg_scale, nontouch),
            "updated_xg_outside_spectral": spectral(xg_scale, outside),
            "updated_xg_touch_top_groups": [
                {"group": int(a["touch_group_id"][i]), "score_frobenius": float(touch_group_score[i])}
                for i in touch_order[:20]
            ],
            "updated_xg_nontouch_top_groups": [
                {"group": int(a["nontouch_group_id"][i]), "score_frobenius": float(nontouch_group_score[i])}
                for i in nontouch_order[:20]
            ],
        }
        status, failure = "PASS_SEVEN_PAIR_RULE_ATTRIBUTION_ADDENDUM", None
    except Exception:
        gates, metrics = {}, {}
        status, failure = "STOP_SEVEN_PAIR_RULE_ATTRIBUTION_ADDENDUM", traceback.format_exc()

    artifact = output / "addendum.npz"
    np.savez_compressed(artifact, **arrays)
    result = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": status,
        "failure": failure,
        "pins": PINS,
        "gates": gates,
        "metrics": metrics,
        "elapsed_s": monotonic() - started,
        "driver_sha256": digest(Path(__file__)),
        "artifact_sha256": digest(artifact),
        "scope": (
            "Saved attribution addendum only. It independently reconstructs the XG31 whitening scale and pair/exclusion "
            "universes without repeating point-pair integration. The outside matrix is defined by full-touch-nontouch and "
            "is bookkeeping, not an independently evaluated far block. No Green accuracy, field, port, board, or PowerSI claim."
        ),
    }
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result), flush=True)
    return 0 if failure is None else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.worker:
        raise SystemExit(run(output))
    assert digest(ROOT / "tools/research/probe_astra_fmm3d_runtime.py") == PINS["tools/research/probe_astra_fmm3d_runtime.py"]
    from probe_astra_fmm3d_runtime import guarded_source_worker

    output.mkdir(parents=False, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guarded_source_worker(
        output,
        max_runtime_s=180,
        worker_command=[sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--output", str(output)],
    ))
