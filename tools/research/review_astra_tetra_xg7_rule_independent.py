"""SPD Decap PI Evaluator v0.23.1: independent saved XG7 moment audit (no FMM)."""
import argparse
from hashlib import sha256
import json
from math import factorial
from pathlib import Path
from time import monotonic

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/qualify_astra_tetra_xg7_rule.py": "c5e9a816e3318220fbc46c6886a0734437d37a1a470bb2c787bedf54d855801e",
    "outputs/research/astra-tetra-xg7-rule-01/result.json": "856eb24fde0ae66cef5eebeb1e00e3f1eee72aecf650826dc75bb548df177e8b",
    "outputs/research/astra-tetra-xg7-rule-01/rule-comparison.npz": "75ba6ecd0fb04254f89dd7d7ba21af98579ca0426a133f5a142e55cda33802a5",
}


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def audit():
    for name, expected in PINS.items():
        assert digest(ROOT / name) == expected, name
    with np.load(ROOT / "outputs/research/astra-tetra-xg7-rule-01/rule-comparison.npz", allow_pickle=False) as saved:
        bary = saved["barycentric"]
        weights = saved["normalized_weights"]
    assert bary.shape == (31, 4) and weights.shape == (31,)
    assert np.isfinite(bary).all() and np.isfinite(weights).all()
    assert np.all(bary > 0.0) and np.all(weights > 0.0)
    bary_sum_error = float(np.max(np.abs(bary.sum(axis=1) - 1.0)))
    weight_sum_error = float(abs(weights.sum() - 1.0))
    assert bary_sum_error <= 8 * np.finfo(float).eps
    assert weight_sum_error <= 8 * np.finfo(float).eps

    moment_errors, worst = [], (-1.0, None)
    for ax in range(8):
        for ay in range(8 - ax):
            for az in range(8 - ax - ay):
                exact = 6.0 * factorial(ax) * factorial(ay) * factorial(az) / factorial(ax + ay + az + 3)
                got = float(weights @ ((bary[:, 1] ** ax) * (bary[:, 2] ** ay) * (bary[:, 3] ** az)))
                relative = abs(got - exact) / exact
                moment_errors.append(relative)
                worst = max(worst, (relative, (ax, ay, az, got, exact)))
    assert len(moment_errors) == 120 and worst[0] <= 1e-12

    # Deliberately asymmetric physical tetrahedron.  This independently checks
    # the affine map and the integrated-outward-flux RT0 affine moment.
    vertices = np.array([[2e-6, -3e-6, 1e-6], [9e-6, 1e-6, 4e-6],
                         [-1e-6, 8e-6, 2e-6], [3e-6, 2e-6, 12e-6]])
    volume = abs(np.linalg.det((vertices[1:] - vertices[0]).T)) / 6.0
    points = bary @ vertices
    center = vertices.mean(axis=0)
    first = volume * np.einsum("p,pi->i", weights, points)
    first_exact = volume * center
    first_relative = float(np.linalg.norm(first - first_exact) / np.linalg.norm(first_exact))
    rt0 = np.stack([(points - vertices[i]) / (3.0 * volume) for i in range(4)], axis=1)
    integrated = volume * np.einsum("p,piv->iv", weights, rt0)
    integrated_exact = np.stack([(center - vertices[i]) / 3.0 for i in range(4)])
    rt0_relative = float(np.linalg.norm(integrated - integrated_exact) / np.linalg.norm(integrated_exact))
    assert first_relative <= 1e-12 and rt0_relative <= 1e-12
    return {
        "point_count": int(len(weights)), "monomial_count": len(moment_errors),
        "barycentric_min": float(bary.min()), "barycentric_max": float(bary.max()),
        "weight_min": float(weights.min()), "weight_max": float(weights.max()),
        "barycentric_row_sum_max_abs": bary_sum_error, "normalized_weight_sum_abs": weight_sum_error,
        "maximum_relative_monomial_error": float(worst[0]), "worst_monomial": worst[1],
        "physical_tetra_volume_m3": float(volume), "physical_affine_first_moment_relative": first_relative,
        "physical_rt0_affine_integral_relative": rt0_relative,
    }


def main(output):
    start = monotonic()
    output.mkdir(parents=False, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        metrics = audit()
        status, failure = "PASS_INDEPENDENT_XG7_RULE_AUDIT", None
    except Exception as exc:
        metrics, status, failure = {}, "STOP_INDEPENDENT_XG7_RULE_AUDIT", repr(exc)
    np.savez_compressed(output / "audit.npz", **{k: np.asarray(v) for k, v in metrics.items() if k != "worst_monomial"})
    result = {"status": status, "failure": failure, "program": "SPD Decap PI Evaluator", "version": "0.23.1",
              "pins": PINS, "metrics": metrics, "scope": "Saved-rule domain and polynomial/affine moments only; no FMM, singular integration, field, or board claim.",
              "elapsed_s": monotonic() - start, "driver_sha256": digest(Path(__file__)),
              "artifact_sha256": digest(output / "audit.npz")}
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False))
    return 0 if failure is None else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(main(args.output.resolve()))
