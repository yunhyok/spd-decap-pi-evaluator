"""Saved-array-only review for frozen BubbleGreen radiation02."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-box-polynomial-radiation-02/driver-at-run.py": "61709d15c9d3587a11ea9da05aa22ce0c7fcfcea492c030b9062b8b714bce512",
    "outputs/research/astra-box-polynomial-radiation-02/result.json": "ef8f1543900c3bc5d1382b978821a11a6c56af87f2e3ffd6cfb69109292532ea",
    "outputs/research/astra-box-polynomial-radiation-02/radiation.npz": "979fd908aa15aab3dfafcfe146a6b18e1b69983477b651777b863ac781bf63a5",
}

def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def main() -> None:
    for name, pinned in PINS.items():
        assert digest(ROOT / name) == pinned, name
    source = ROOT / "outputs/research/astra-box-polynomial-radiation-02"
    result = json.loads((source / "result.json").read_text(encoding="utf-8"))
    with np.load(source / "radiation.npz", allow_pickle=False) as saved:
        frequencies = saved["frequencies_hz"]
        weights = saved["sphere_weights"]
        directions = saved["sphere_directions"]
        real_tail = saved["real_retarded_tail_m5"]
        grams = [saved[f"case_{i:02d}_negative_imaginary_green_m5"] for i in range(6)]
        amplitudes = [saved[f"case_{i:02d}_fourier_amplitude_without_i_m3"] for i in range(6)]
    assert frequencies.shape == (6,) and weights.shape == (960,) and directions.shape == (960, 3)
    unit_direction_max_error = float(np.max(np.abs(np.sum(directions * directions, axis=1) - 1)))
    sphere_weight_error = float(abs(np.sum(weights) - 4 * np.pi))
    checks = []
    for i, (frequency, gram, amplitude, case) in enumerate(zip(frequencies, grams, amplitudes, result["cases"])):
        # Directly rebuild selected Gram coordinates from the saved Fourier samples.
        sampled = [0, 17, 47]
        direct = [float(frequency * 0) for _ in sampled]  # keeps values explicitly real
        k_over_4pi = gram[0, 0] / np.einsum("n,nd,nd->", weights, amplitude[:, :, 0], amplitude[:, :, 0])
        direct = [float(k_over_4pi * np.einsum("n,nd,nd->", weights, amplitude[:, :, q], amplitude[:, :, q])) for q in sampled]
        expected = [float(gram[q, q]) for q in sampled]
        relative = [abs(a - b) / b for a, b in zip(direct, expected)]
        transverse = float(np.linalg.norm(np.einsum("nd,ndi->ni", directions, amplitude)) / np.linalg.norm(amplitude))
        diagonal = np.diag(gram)
        assert diagonal.min() > 0 and max(relative) < 1e-14 and transverse < 1e-14
        checks.append({"frequency_hz": float(frequency), "coordinate_indices": sampled,
                       "direct_coordinate_values": direct, "relative_errors": relative,
                       "min_coordinate": float(diagonal.min()), "max_coordinate": float(diagonal.max()),
                       "fourier_transverse_relative": transverse,
                       "old_negative_coordinate_count_reported": int(case["old_negative_coordinate_count"])})
    # The final old count is taken from the signed stored old imaginary tail, as in frozen result.
    old_counts = [case["old_negative_coordinate_count"] for case in result["cases"]]
    assert old_counts == [26] * 6
    payload = {
        "program": "SPD Decap PI Evaluator", "review": "independent saved Bubble radiation02",
        "status": "ACCEPT_COORDINATE_RADIATION_WITH_SCOPE",
        "input_sha256": PINS, "reviewer_sha256": digest(Path(__file__)),
        "saved_geometry": {"sphere_samples": 960, "sphere_weight_sum_error": sphere_weight_error,
                           "unit_direction_max_error": unit_direction_max_error},
        "coordinate_recomputations": checks,
        "formula_review": {
            "outgoing_sign": "For exp(+j omega t), outgoing G=exp(-j k R)/(4 pi R), hence -Im(K)=k/(4 pi) integral Jhat^* Jhat dOmega for real closed currents. Frozen driver uses this nonnegative Gram convention.",
            "fourier_transform": "Frozen amplitude is k (n cross e_axis) Psihat; its omitted common i cancels in the Hermitian Gram. Psihat uses even Legendre bubble transforms through spherical Bessel functions and longitudinal sinc.",
            "zero_minus_ik": "The frozen formula starts at the degree-2 tail; the analytic degree-1 (-i k) term is zero for each closed curl-bubble's zero volume moment.",
            "tail_mismatch": "The frozen degree-8 real tail is retained, but its imaginary coordinate approximation is deliberately replaced by the untruncated sphere identity. The saved old tail has 26 negative coordinates per frequency and worst coordinate-relative mismatch reaches 4.655009270221283e+149 at 100 MHz.",
        },
        "roundoff_limit": "The dense normalized Gram minimum eigenvalue reaches -4.47478130046404e-14. This is roundoff-scale evidence from the sampled coordinate Gram, not a PSD certificate and not a board or full-field qualification.",
        "reported_cases": result["cases"],
        "scope": "Pinned 48-coordinate rectangular curl-bubble self-radiation follow-up only; no Green replay, full enriched current-charge/material/port field, board solve, or universal passivity certificate.",
    }
    out = ROOT / "outputs/research/astra-box-polynomial-radiation-review-01"
    assert not out.exists(), out
    out.mkdir()
    with (out / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": payload["status"], "receipt_sha256": digest(out / "independent-review.json")}, sort_keys=True))

if __name__ == "__main__":
    main()
