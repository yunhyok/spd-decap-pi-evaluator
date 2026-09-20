"""Pinned global-gate addendum for the independent group-correction review."""

from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-xg7-jac27-group-correction-review-addendum-02"
PINS = {
    "outputs/research/astra-xg7-jac27-group-correction-02/result.json": "6db19ad72f1477c5f21f4eb6708fb55532884955fce1328fd543ccafa9871491",
    "outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz": "ca0c1d600316fd704aff4e741f956a36eed9c1fd224d10f1384c3aaf17ce9c44",
    "outputs/research/astra-xg7-jac27-group-correction-review-01/result.json": "e29a74d952918e959c3b168e3e013cd7fd3f3e31167ca75a6e753c5709f582a0",
    "outputs/research/astra-xg7-jac27-group-correction-review-01/review.npz": "16bcbfa9d6c35447994681c11980fdc9c36e057a9806868b65b9cae806bf764f",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    started = monotonic()
    for name, expected in PINS.items():
        assert digest(ROOT / name) == expected, name
    producer = json.loads(
        (ROOT / "outputs/research/astra-xg7-jac27-group-correction-02/result.json").read_text()
    )
    independent = json.loads(
        (ROOT / "outputs/research/astra-xg7-jac27-group-correction-review-01/result.json").read_text()
    )
    assert producer["status"] == "QUALIFIED_XG7_JAC27_GROUP_CORRECTIONS"
    assert independent["status"] == "PASS_SAVED_GROUP_CORRECTION02_REVIEW"
    with np.load(
        ROOT / "outputs/research/astra-xg7-jac27-group-correction-review-01/review.npz",
        allow_pickle=False,
    ) as saved:
        jacobi = saved["independently_corrected_jac27"]
        xg7 = saved["independently_corrected_xg31"]
        stored_delta = saved["independently_signed_delta"]
    with np.load(
        ROOT / "outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz",
        allow_pickle=False,
    ) as saved:
        fixed_jac_baseline = saved["prior_jac27_matrix"]

    signed_delta = xg7 - jacobi
    updated_scale = np.linalg.inv(np.linalg.cholesky((xg7 + xg7.T) / 2.0))
    fixed_scale = np.linalg.inv(
        np.linalg.cholesky((fixed_jac_baseline + fixed_jac_baseline.T) / 2.0)
    )
    updated_spectral = float(
        np.linalg.norm(updated_scale @ signed_delta @ updated_scale.T, 2)
    )
    fixed_spectral = float(
        np.linalg.norm(fixed_scale @ signed_delta @ fixed_scale.T, 2)
    )
    fixed_frobenius = float(
        np.linalg.norm(fixed_scale @ signed_delta @ fixed_scale.T, "fro")
    )
    checks = {
        "independent_delta_reconstruction_relative": float(
            np.linalg.norm(signed_delta - stored_delta)
            / max(np.linalg.norm(signed_delta), np.finfo(float).tiny)
        ),
        "updated_xg_spectral": updated_spectral,
        "fixed_jac_spectral": fixed_spectral,
        "fixed_jac_frobenius": fixed_frobenius,
        "producer_updated_metric_absolute": abs(
            updated_spectral
            - producer["metrics"]["signed_delta_updated_xg_spectral"]
        ),
        "producer_fixed_metric_absolute": abs(
            fixed_spectral
            - producer["metrics"]["signed_delta_fixed_jac_spectral"]
        ),
        "updated_xg_symmetry_relative": float(
            np.linalg.norm(xg7 - xg7.T) / np.linalg.norm(xg7)
        ),
        "updated_xg_min_eigenvalue_h": float(
            np.linalg.eigvalsh((xg7 + xg7.T) / 2.0).min()
        ),
    }
    gates = {
        "independent_delta_exact": checks["independent_delta_reconstruction_relative"]
        < 1.0e-14,
        "updated_physical_rule_gate": checks["updated_xg_spectral"] < 5.0e-5,
        "producer_updated_metric": checks["producer_updated_metric_absolute"]
        < 1.0e-12,
        "producer_fixed_metric": checks["producer_fixed_metric_absolute"] < 1.0e-12,
        "updated_symmetry": checks["updated_xg_symmetry_relative"] < 1.0e-12,
        "updated_positive": checks["updated_xg_min_eigenvalue_h"] > 0.0,
    }
    gates = {name: bool(value) for name, value in gates.items()}
    assert all(gates.values()), {name: value for name, value in gates.items() if not value}

    OUT.mkdir(parents=False, exist_ok=False)
    arrays = OUT / "gate-arrays.npz"
    np.savez_compressed(
        arrays,
        independently_corrected_jac27=jacobi,
        independently_corrected_xg31=xg7,
        independently_signed_delta=signed_delta,
        updated_xg_scale=updated_scale,
        fixed_jac_scale=fixed_scale,
    )
    receipt = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_WITH_SCOPE",
        "pins": PINS,
        "checks": checks,
        "gates": gates,
        "elapsed_s": monotonic() - started,
        "reviewer_sha256": digest(Path(__file__)),
        "artifact_sha256": digest(arrays),
        "scope": (
            "This addendum hard-pins the canonical producer and the earlier review's "
            "independently reconstructed 40,928 point-pair blocks, then independently "
            "evaluates the updated-XG physical 5e-5 rule-difference gate. It adds no "
            "new integration. The gate is empirical between two corrected static vector "
            "rules on five local lifts; it is not an absolute Green-error, scalar, "
            "charge, field, port, or board certificate."
        ),
        "supersedes": (
            "The initial addendum attempt used the corrected Jacobi matrix for the "
            "producer's deliberately fixed pre-update scale and stopped before writing "
            "an output directory; this fresh receipt uses the pinned pre-update matrix."
        ),
    }
    (OUT / "result.json").write_text(
        json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
