"""Superseding sign-convention review for frozen BubbleGreen01 radiation data."""
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
    "tools/research/review_astra_box_polynomial_green.py": "150d87f8425aec27933f89a07d9d963348bccdfc811d071c4f136f5676abdefa",
    "outputs/research/astra-box-polynomial-green-review-01/independent-review.json": "5978780f361ddb2528ff5a717631c3d6679bcc9429b9a3aef6da817f59f7e799",
}


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    for name, expected in PINS.items():
        assert digest(ROOT / name) == expected, name

    first = json.loads(
        (ROOT / "outputs/research/astra-box-polynomial-green-review-01/independent-review.json")
        .read_text(encoding="utf-8")
    )
    with np.load(
        ROOT / "outputs/research/astra-box-polynomial-green-01/kernels.npz",
        allow_pickle=False,
    ) as data:
        tail = np.asarray(data["retarded_tail_m5"])
        frequencies = np.asarray(data["frequencies_hz"])

    assert tail.shape == (6, 48, 48)
    assert frequencies.shape == (6,)
    diagonal_imaginary = np.diagonal(tail, axis1=1, axis2=2).imag
    radiation_diagonal = -diagonal_imaginary
    negative_imaginary = np.count_nonzero(diagonal_imaginary < 0.0, axis=1)
    positive_imaginary = np.count_nonzero(diagonal_imaginary > 0.0, axis=1)
    negative_radiation = np.count_nonzero(radiation_diagonal < 0.0, axis=1)
    assert np.array_equal(negative_imaginary, np.full(6, 22))
    assert np.array_equal(positive_imaginary, np.full(6, 26))
    assert np.array_equal(negative_radiation, np.full(6, 26))

    payload = {
        "program": "SPD Decap PI Evaluator v0.23.1",
        "review": "BubbleGreen01 radiation-sign superseding addendum",
        "status": "ACCEPT_STATIC_AND_MOMENTS_ONLY_REJECT_WEAK_RADIATION",
        "supersedes_review_sha256": PINS[
            "outputs/research/astra-box-polynomial-green-review-01/independent-review.json"
        ],
        "input_sha256": PINS,
        "reviewer_sha256": digest(Path(__file__)),
        "sign_convention": {
            "time_dependence": "exp(+j omega t)",
            "physical_radiation_kernel": "-Im(K)",
            "passive_coordinate_requirement": "diagonal(-Im(K)) >= 0",
        },
        "saved_npz_counts": {
            "negative_imaginary_diagonals_per_frequency": negative_imaginary.tolist(),
            "positive_imaginary_diagonals_per_frequency": positive_imaginary.tolist(),
            "negative_radiation_diagonals_per_frequency": negative_radiation.tolist(),
            "positive_imaginary_frequency_resolved_count": int(positive_imaginary.sum()),
            "negative_radiation_frequency_resolved_count": int(negative_radiation.sum()),
            "minimum_imaginary_diagonal": float(diagonal_imaginary.min()),
            "maximum_imaginary_diagonal": float(diagonal_imaginary.max()),
        },
        "correction": (
            "The first review counted 22 negative Im(K) diagonal entries per frequency, "
            "but those correspond to positive coordinate losses in -Im(K). The rejected "
            "entries are the 26 positive Im(K) diagonals per frequency, equivalently the "
            "26 negative diagonals of -Im(K)."
        ),
        "retained_scope": first["scope"],
    }

    out = ROOT / "outputs/research/astra-box-polynomial-green-review-02"
    assert not out.exists(), out
    out.mkdir()
    target = out / "independent-review.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "reviewer_sha256": payload["reviewer_sha256"],
                "receipt_sha256": digest(target),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
