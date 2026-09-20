"""80-digit direct-transform addendum for frozen box-bubble radiation02."""
from __future__ import annotations

from fractions import Fraction
from hashlib import sha256
import json
from math import prod
from pathlib import Path

import mpmath as mp
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-box-polynomial-radiation-02/driver-at-run.py": "61709d15c9d3587a11ea9da05aa22ce0c7fcfcea492c030b9062b8b714bce512",
    "outputs/research/astra-box-polynomial-radiation-02/result.json": "ef8f1543900c3bc5d1382b978821a11a6c56af87f2e3ffd6cfb69109292532ea",
    "outputs/research/astra-box-polynomial-radiation-02/radiation.npz": "979fd908aa15aab3dfafcfe146a6b18e1b69983477b651777b863ac781bf63a5",
    "tools/research/review_astra_box_polynomial_radiation.py": "4a7c88092b14e3bcebd351f862d9e670d3c34693b6bb21d9ca4bc5851fd7064b",
    "outputs/research/astra-box-polynomial-radiation-review-01/independent-review.json": "41bbfd9d53b90fa369af47bf04158fda002362daf3358b66b94f7a45fe7e71e4",
}
DIMENSIONS_M = (mp.mpf("1e-4"), mp.mpf("1e-4"), mp.mpf("2.5e-5"))
MU0 = 4 * mp.pi * mp.mpf("1e-7")
EPS0 = mp.mpf("8.8541878128e-12")


def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def legendre(order: int, x: mp.mpf) -> mp.mpf:
    if order == 0:
        return mp.mpf(1)
    p0, p1 = mp.mpf(1), x
    for n in range(1, order):
        p0, p1 = p1, ((2 * n + 1) * x * p1 - n * p0) / (n + 1)
    return p1


def legendre_coefficients(order: int) -> list[Fraction]:
    if order == 0:
        return [Fraction(1)]
    p0, p1 = [Fraction(1)], [Fraction(0), Fraction(1)]
    for n in range(1, order):
        xp1 = [Fraction(0), *p1]
        width = max(len(xp1), len(p0))
        xp1 += [Fraction(0)] * (width - len(xp1))
        p0 += [Fraction(0)] * (width - len(p0))
        p2 = [((2 * n + 1) * xp1[i] - n * p0[i]) / (n + 1) for i in range(width)]
        p0, p1 = p1, p2
    return p1


def bubble_coefficients(order: int) -> list[Fraction]:
    p = legendre_coefficients(order)
    result = [Fraction(0)] * (len(p) + 2)
    for degree, coefficient in enumerate(p):
        result[degree] += coefficient
        result[degree + 2] -= coefficient
    return result


def direct_transform(order: int, alpha: mp.mpf) -> mp.mpf:
    return mp.quad(
        lambda x: (1 - x * x) * legendre(order, x) * mp.cos(alpha * x),
        [-1, 0, 1],
    )


def moment_series_transform(order: int, alpha: mp.mpf) -> mp.mpf:
    coefficients = bubble_coefficients(order)
    terms = []
    for index in range(80):
        moment = sum(
            (coefficient * Fraction(2, degree + 2 * index + 1)
             for degree, coefficient in enumerate(coefficients)
             if (degree + 2 * index) % 2 == 0),
            Fraction(0),
        )
        exact_moment = mp.mpf(moment.numerator) / moment.denominator
        terms.append((-1) ** index * alpha ** (2 * index) * exact_moment / mp.factorial(2 * index))
    return mp.fsum(terms)


def sinc(alpha: mp.mpf) -> mp.mpf:
    return mp.mpf(1) if alpha == 0 else mp.sin(alpha) / alpha


def cross_direction_axis(direction: list[mp.mpf], axis: int) -> list[mp.mpf]:
    x, y, z = direction
    return ([0, z, -y], [-z, 0, x], [y, -x, 0])[axis]


def direct_amplitude(
    frequency_hz: float,
    direction_f64: np.ndarray,
    coordinate: int,
) -> list[mp.mpf]:
    direction = [mp.mpf(repr(float(value))) for value in direction_f64]
    wave_number = 2 * mp.pi * mp.mpf(repr(float(frequency_hz))) * mp.sqrt(MU0 * EPS0)
    alpha = [wave_number * direction[i] * DIMENSIONS_M[i] / 2 for i in range(3)]
    axis, local = divmod(coordinate, 16)
    i, j = divmod(local, 4)
    a, b = (axis + 1) % 3, (axis + 2) % 3
    psi = (
        max(DIMENSIONS_M)
        * prod(DIMENSIONS_M)
        / 4
        * sinc(alpha[axis])
        * direct_transform(2 * i, alpha[a])
        * direct_transform(2 * j, alpha[b])
    )
    return [wave_number * component * psi for component in cross_direction_axis(direction, axis)]


def relative_vector_error(expected: np.ndarray, actual: list[mp.mpf]) -> mp.mpf:
    differences = [abs(mp.mpf(repr(float(expected[i]))) - actual[i]) for i in range(3)]
    scale = max([abs(value) for value in actual] + [mp.mpf("1e-300")])
    return max(differences) / scale


def main() -> None:
    mp.mp.dps = 80
    for name, expected in PINS.items():
        assert digest(ROOT / name) == expected, name

    fixed_transform_checks = []
    for frequency_hz in (1e3, 1e8):
        wave_number = 2 * mp.pi * frequency_hz * mp.sqrt(MU0 * EPS0)
        alpha = wave_number * DIMENSIONS_M[0] / (2 * mp.sqrt(14))
        for order in (0, 2, 4, 6):
            direct = direct_transform(order, alpha)
            series = moment_series_transform(order, alpha)
            relative = abs(direct - series) / max(abs(direct), abs(series), mp.mpf("1e-300"))
            assert relative < mp.mpf("1e-40")
            fixed_transform_checks.append(
                {
                    "frequency_hz": frequency_hz,
                    "legendre_order": order,
                    "alpha": mp.nstr(alpha, 25),
                    "direct_integral": mp.nstr(direct, 25),
                    "moment_series_relative_error": float(relative),
                }
            )

    amplitude_checks = []
    with np.load(
        ROOT / "outputs/research/astra-box-polynomial-radiation-02/radiation.npz",
        allow_pickle=False,
    ) as data:
        directions = np.asarray(data["sphere_directions"])
        frequencies = np.asarray(data["frequencies_hz"])
        for case_index in (0, 5):
            amplitudes = np.asarray(data[f"case_{case_index:02d}_fourier_amplitude_without_i_m3"])
            for direction_index, coordinate in ((137, 0), (321, 17), (511, 47)):
                direct = direct_amplitude(
                    frequencies[case_index], directions[direction_index], coordinate
                )
                relative = relative_vector_error(
                    amplitudes[direction_index, :, coordinate], direct
                )
                assert relative < mp.mpf("2e-13")
                amplitude_checks.append(
                    {
                        "frequency_hz": float(frequencies[case_index]),
                        "direction_index": direction_index,
                        "coordinate": coordinate,
                        "direct_amplitude_max_abs_m3": mp.nstr(max(map(abs, direct)), 25),
                        "saved_relative_error": float(relative),
                    }
                )

    payload = {
        "program": "SPD Decap PI Evaluator v0.23.1",
        "review": "independent 80-digit box-bubble Fourier transform addendum",
        "status": "ACCEPT_COORDINATE_RADIATION_TRANSFORM_AND_SCALE_WITH_SCOPE",
        "input_sha256": PINS,
        "reviewer_sha256": digest(Path(__file__)),
        "precision_decimal_digits": mp.mp.dps,
        "fixed_nonzero_transform_checks": fixed_transform_checks,
        "saved_amplitude_scale_checks": amplitude_checks,
        "maximum_transform_relative_error": max(
            item["moment_series_relative_error"] for item in fixed_transform_checks
        ),
        "maximum_saved_amplitude_relative_error": max(
            item["saved_relative_error"] for item in amplitude_checks
        ),
        "independence": (
            "Direct mpmath quadrature evaluates (1-s^2)P_n(s)cos(alpha s). An exact-rational "
            "monomial moment series provides a second transform path. Selected saved Fourier-current "
            "amplitudes are then assembled directly without scipy.special.spherical_jn."
        ),
        "scope": (
            "Selected coordinate transforms and amplitudes for the pinned rectangular curl-bubble "
            "self-radiation follow-up only. This is not a dense-Gram PSD, RT0-bubble cross, field, "
            "material, port, board, or PowerSI accuracy certificate."
        ),
    }
    out = ROOT / "outputs/research/astra-box-polynomial-radiation-review-02"
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
                "maximum_saved_amplitude_relative_error": payload[
                    "maximum_saved_amplitude_relative_error"
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
