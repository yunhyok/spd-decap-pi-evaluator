"""Read-only receipt for the frozen neutral scalar retarded-charge primitive."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tools/research/qualify_astra_charge_retarded_green.py"
RESULT = ROOT / "outputs/research/astra-charge-retarded-green-02/result.json"
ARRAYS = RESULT.parent / "charge.npz"
VECTOR = ROOT / "outputs/research/astra-tetra-retarded-green-02/blocks.npz"
STATIC = ROOT / "outputs/research/astra-tetra-static-green-01/blocks.npz"
PINS = {
    "helper": "cbe0f2b0e6a5b414b8fa481119db228a1e82dcd05cd70dbf0bed84e13c39a1c9",
    "result": "17d1a5c35d06e0481c7735aca117ca7273b41db02b47e1ea313353707ca81de9",
    "arrays": "05c122a8d4d599a7a8371678b7e580fa28d599bc972a3107d8fbf1970890b43c",
    "vector": "c0f241d084c72d76afc85880dca4604c92b42dfcdac6aba11654045a57fc83f2",
    "static": "d1c0b21d327328d1d42f7c12d1a2bf3009b61addb2d83593fce175044105c654",
}


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tail(moments, k, length):
    value = np.zeros_like(moments[0], dtype=complex)
    for n in range(2, 9):
        value += (-1j * k * length) ** n / math.factorial(n) * moments[n - 2] / length
    return value


def relative(a, b):
    return float(np.linalg.norm(a - b) / np.linalg.norm(b))


def main(output):
    paths = (("helper", HELPER), ("result", RESULT), ("arrays", ARRAYS), ("vector", VECTOR), ("static", STATIC))
    assert all(sha(path) == PINS[name] for name, path in paths)
    receipt = json.loads(RESULT.read_text(encoding="utf-8"))
    with np.load(ARRAYS, allow_pickle=False) as data:
        static = data["static_matrix_per_m"]
        tails = data["regular_tail_per_m"]
        moments = data["scalar_distance_moments"]
        B = data["distributional_divergence"]
        frequencies = data["frequencies_hz"]
        polarization = data["uniform_current_charge"]
    with np.load(VECTOR, allow_pickle=False) as data:
        tetrahedra = data["tetrahedra_m"]
        vector_moments = data["distance_moments"]
    with np.load(STATIC, allow_pickle=False) as data:
        flux = data["uniform_current_face_flux"]
    assert static.shape == B.shape == (24, 24)
    assert tails.shape == (6, 24, 24) and moments.shape == (7, 24, 24)
    assert polarization.shape == (24, 3) and np.isfinite(tails).all() and np.isfinite(moments).all()
    length = float(np.linalg.norm(np.ptp(tetrahedra.reshape(-1, 3), axis=0)))
    c0 = 1 / math.sqrt((4e-7 * math.pi) * 8.8541878128e-12)
    wave = 2 * math.pi * frequencies / c0
    rebuilt = np.array([tail(moments, k, length) for k in wave])
    neutral_constant = np.array([B.T @ (-1j * k * np.ones((24, 24))) @ B for k in wave])
    volumes = abs(np.linalg.det((tetrahedra[:, 1:] - tetrahedra[:, :1]).transpose(0, 2, 1))) / 6
    expected_volume = np.einsum("ai,pabij,bj->pab", flux[:, :, 0], vector_moments, flux[:, :, 0]) / (volumes[:, None] * volumes[None])
    checks = {
        "tail_series_rebuild_relative": relative(rebuilt, tails),
        "neutral_B_column_sum_max": float(np.max(abs(B.sum(axis=0)))),
        "exact_minus_ik_ones_projected_max": float(np.max(abs(neutral_constant))),
        "volume_moment_reuse_relative": relative(moments[:, :6, :6], expected_volume),
        "tail_raw_reciprocity_max": max(relative(x, x.T) for x in tails),
        "tail_conjugacy_max": max(relative(tail(moments, -k, length), x.conj()) for k, x in zip(wave, tails)),
        "projected_tail_q16_reciprocity": relative(B.T @ tails[-1] @ B, (B.T @ tails[-1] @ B).T),
        "q12_to_q16_projected_change_reported": receipt["refinement"][-1]["projected_tail_relative_change"],
        "q16_tail_reciprocity_reported": receipt["refinement"][-1]["raw_tail_reciprocity"],
        "all_six_radiation_positive": bool(all(min(x["polarization_radiation_m3"]) > 0 for x in receipt["cases"])),
        "all_six_radiation_relative_error_max": max(x["radiation_relative_error"] for x in receipt["cases"]),
        "all_six_series_relative_bound_max": max(x["series_bound_relative_to_tail"] for x in receipt["cases"]),
    }
    assert checks["tail_series_rebuild_relative"] < 1e-14
    assert checks["neutral_B_column_sum_max"] == 0 and checks["exact_minus_ik_ones_projected_max"] == 0
    assert checks["volume_moment_reuse_relative"] < 1e-14
    assert checks["tail_raw_reciprocity_max"] < 1e-12 and checks["tail_conjugacy_max"] < 1e-14
    assert checks["projected_tail_q16_reciprocity"] < 1e-12
    assert checks["q12_to_q16_projected_change_reported"] < 1e-3
    assert checks["all_six_radiation_positive"] and checks["all_six_radiation_relative_error_max"] < 1e-8
    assert checks["all_six_series_relative_bound_max"] < 1e-16
    review = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "ACCEPT_NEUTRAL_RETARDED_CHARGE_FROZEN_CONTROL",
        "pins": {name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)} for name, path in paths},
        "saved_shapes": {"static": list(static.shape), "tails": list(tails.shape), "moments": list(moments.shape), "divergence": list(B.shape)},
        "checks": checks,
        "scope": "Saved-array verification of the six-tetra normalized P0 neutral charge primitive only. It does not qualify material, port, source-solid, or board accuracy.",
    }
    output.mkdir(parents=True, exist_ok=False)
    temporary = output / "independent-review.json.tmp"
    final = output / "independent-review.json"
    temporary.write_text(json.dumps(review, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, final)
    print(json.dumps({"status": review["status"], "sha256": sha(final)}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output.resolve())
