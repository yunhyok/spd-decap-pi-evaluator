"""Independent saved-array review for frozen BubbleGreen01; no Green replay."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-box-polynomial-green-01/driver-at-run.py": "4759564dab5b587933962847fd9b716905524ed8976c023660adf3b1df4836ad",
    "outputs/research/astra-box-polynomial-green-01/result.json": "95cdd1c2138955283afafcc589920b7a0ca444d224b8bfc27edc1f48c9af70ae",
    "outputs/research/astra-box-polynomial-green-01/kernels.npz": "0fe3974797549d384811594c19e327f9eeee3a5cbe72dcccd0519977ef8ea96b",
}

def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def main() -> None:
    for name, pinned in PINS.items():
        assert digest(ROOT / name) == pinned, name
    src = ROOT / "outputs/research/astra-box-polynomial-green-01"
    result = json.loads((src / "result.json").read_text(encoding="utf-8"))
    with np.load(src / "kernels.npz", allow_pickle=False) as data:
        static = data["static_green_m5"]
        moments = data["distance_moments_m6"]
        tail = data["retarded_tail_m5"]
        frequencies = data["frequencies_hz"]
    assert static.shape == (48, 48) and moments.shape == (7, 48, 48)
    assert tail.shape == (6, 48, 48) and frequencies.shape == (6,)
    static_skew = float(np.max(np.abs(static - static.T)))
    static_min_eigenvalue = float(np.linalg.eigvalsh((static + static.T) / 2).min())
    tail_skew = float(max(np.max(np.abs(x - x.T)) for x in tail))
    diagonal_imaginary = np.diagonal(tail, axis1=1, axis2=2).imag
    negative_by_frequency = [int(np.count_nonzero(row < 0)) for row in diagonal_imaginary]
    assert negative_by_frequency == [22] * 6
    assert static_skew < 1e-30 and static_min_eigenvalue > 0
    payload = {
        "program": "SPD Decap PI Evaluator",
        "review": "independent saved/static BubbleGreen01",
        "status": "ACCEPT_STATIC_AND_MOMENTS_ONLY_REJECT_WEAK_RADIATION",
        "input_sha256": PINS,
        "reviewer_sha256": digest(Path(__file__)),
        "shapes": {"static_green_m5": list(static.shape), "distance_moments_m6": list(moments.shape), "retarded_tail_m5": list(tail.shape)},
        "saved_matrix_checks": {
            "static_max_absolute_skew": static_skew,
            "static_minimum_eigenvalue": static_min_eigenvalue,
            "tail_max_absolute_skew": tail_skew,
            "reported_static_symmetry_relative": result["static_symmetry_relative"],
            "reported_distance_squared_magnetic_moment_identity_relative": result["distance_squared_magnetic_moment_identity_relative"],
            "reported_constant_current_moment_relative": result["constant_current_moment_relative"],
        },
        "quadrature_and_projection": {"reported_quadrature": result["quadrature"], "reported_gaussian_scalar_relative": result["independent_scalar_gaussian_relative_error"], "reported_cell_average_projection": result["cell_average_projection"], "reported_degree8_remainder_bound": result["degree8_maximum_element_remainder_bound_m5"]},
        "radiation_rejection": {
            "negative_imaginary_diagonal_count_per_frequency": negative_by_frequency,
            "negative_imaginary_diagonal_count_frequency_resolved": int(sum(negative_by_frequency)),
            "largest_magnitude_negative_imaginary_diagonal": float(diagonal_imaginary.min()),
            "largest_positive_imaginary_diagonal": float(diagonal_imaginary.max()),
            "finding": "Frozen saved NPZ contains 22 negative imaginary diagonals per frequency (132 frequency-resolved), rather than the requested 26. Any negative dissipative/radiative diagonal prevents qualification of weak radiation.",
        },
        "analytic_static_review": {
            "convolution_and_scaling": "Frozen driver forms difference-box correlations, integrates three max-coordinate Duffy pyramids with Jacobian 8*r^2, then applies V^2/L to the static 1/r term and V^2 to distance moments. The C/V derivative terms carry reciprocal squared relative-length factors.",
            "parity": "Only same-axis blocks are stored; frozen driver identifies cross-axis blocks as simultaneous-reflection odd.",
            "retardation": "The saved tail starts at n=2; the analytic n=1 (-i k) term is zero because the curl-bubble modes have zero volume moment. Degree-8 moments and the stated remainder bound are retained.",
        },
        "scope": "Rectangular homogeneous curl-bubble self Green blocks only. This receipt does not qualify weak radiation, RT0-bubble cross blocks, finite-frequency fields, charge/material/port/board accuracy, or a board solve.",
    }
    out = ROOT / "outputs/research/astra-box-polynomial-green-review-01"
    assert not out.exists(), out
    out.mkdir()
    target = out / "independent-review.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": payload["status"], "receipt_sha256": digest(target)}, sort_keys=True))

if __name__ == "__main__":
    main()
