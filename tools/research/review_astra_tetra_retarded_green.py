"""Read-only independent receipt for the frozen retarded tetra Green control."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tools/research/qualify_astra_tetra_retarded_green.py"
RESULT = ROOT / "outputs/research/astra-tetra-retarded-green-02/result.json"
BLOCKS = RESULT.parent / "blocks.npz"
PINS = {
    "helper": "a575dce0fe2d0f87a3b041209d18055f061f04c6f9510f8b0a5cca0b609bf12d",
    "result": "51edd52ae9b58334847d9afba72902ed233767f0f977b2a6c56b7c1e267a6ca9",
    "blocks": "c0f241d084c72d76afc85880dca4604c92b42dfcdac6aba11654045a57fc83f2",
}


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def flatten(blocks):
    return blocks.transpose(0, 2, 1, 3).reshape(24, 24)


def loop_from_tetrahedra(tetrahedra):
    owners = {}
    for ti, tetra in enumerate(tetrahedra):
        for fi in range(4):
            face = np.delete(tetra, fi, axis=0)
            owners.setdefault(tuple(sorted(map(tuple, face))), []).append((ti, fi))
    pairs = [p for p in owners.values() if len(p) == 2]
    assert len(pairs) == 6
    incidence = np.zeros((6, 6))
    lift = np.zeros((24, 6))
    for edge, ((a, ai), (b, bi)) in enumerate(pairs):
        incidence[a, edge], incidence[b, edge] = 1, -1
        lift[4 * a + ai, edge], lift[4 * b + bi, edge] = 1, -1
    _, singular, vh = np.linalg.svd(incidence)
    assert singular[-2] > .1 and singular[-1] < 1e-14
    loop = lift @ (vh[-1] / np.linalg.norm(vh[-1]))
    return loop, singular


def series_tail(moments, k, length):
    value = np.zeros_like(moments[0], dtype=complex)
    for n in range(2, 9):
        value += (-1j * k * length) ** n / math.factorial(n) * moments[n - 2] / length
    return 1e-7 * value


def rel(a, b):
    return float(np.linalg.norm(a - b) / np.linalg.norm(b))


def main(output):
    assert all(sha(path) == PINS[name] for name, path in (("helper", HELPER), ("result", RESULT), ("blocks", BLOCKS)))
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    with np.load(BLOCKS, allow_pickle=False) as data:
        tetrahedra = data["tetrahedra_m"]
        static = data["static_blocks"]
        regular = data["regular_blocks"]
        tails = data["tail_blocks"]
        moments = data["distance_moments"]
        frequencies = data["frequencies_hz"]
        loop_saved = data["loop_face_flux"]
        basis_moments = data["exact_basis_volume_moments"]
    assert static.shape == (6, 6, 4, 4)
    assert regular.shape == tails.shape == (6, 6, 6, 4, 4)
    assert moments.shape == (7, 6, 6, 4, 4)
    assert np.isfinite(static).all() and np.isfinite(tails).all() and np.isfinite(moments).all()
    dimensions = np.ptp(tetrahedra.reshape(-1, 3), axis=0)
    length = float(np.linalg.norm(dimensions))
    # Match the frozen helper's declared SI constants, rather than a rounded c.
    c0 = 1 / math.sqrt((4e-7 * math.pi) * 8.8541878128e-12)
    wave = 2 * np.pi * frequencies / c0
    rebuilt_tails = np.array([series_tail(moments, k, length) for k in wave])
    lead = np.array([-1j * k * 1e-7 * np.einsum("aid,bjd->abij", basis_moments, basis_moments) for k in wave])
    loop, singular = loop_from_tetrahedra(tetrahedra)
    assert min(np.linalg.norm(loop - loop_saved), np.linalg.norm(loop + loop_saved)) < 1e-12
    dipole = loop @ basis_moments.reshape(24, 3)
    loop_tail = np.array([loop @ flatten(tail) @ loop for tail in tails])
    reciprocal_tail = [rel(tail, tail.transpose(1, 0, 3, 2)) for tail in tails]
    reciprocal_full = [rel(static + r, (static + r).transpose(1, 0, 3, 2)) for r in regular]
    out = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_RETARDED_TETRA_FROZEN_CONTROL",
        "pins": {name: {"path": str(path.relative_to(ROOT)).replace("\\\\", "/"), "sha256": sha(path)} for name, path in (("helper", HELPER), ("result", RESULT), ("blocks", BLOCKS))},
        "saved_shapes": {"static": list(static.shape), "regular": list(regular.shape), "tails": list(tails.shape), "moments": list(moments.shape)},
        "checks": {
            "tail_series_rebuild_relative": rel(rebuilt_tails, tails),
            "regular_equals_exact_minus_ik_moment_plus_tail_relative": rel(regular, lead + tails),
            "conjugacy_relative": max(rel(series_tail(moments, -k, length), tail.conj()) for k, tail in zip(wave, tails)),
            "tail_raw_reciprocity_max": max(reciprocal_tail),
            "full_raw_reciprocity_max": max(reciprocal_full),
            "closed_loop_boundary_flux_max": float(np.max(abs(loop.reshape(6, 4).sum(axis=1)))),
            "closed_loop_zero_moment_norm_m": float(np.linalg.norm(dipole)),
            "loop_tail_imaginary_negative": bool(np.all(loop_tail.imag < 0)),
            "loop_tail_radiation_h_at_100MHz": float(-loop_tail[-1].imag),
            "loop_incidence_null_singular": float(singular[-1]),
            "q12_to_q16_tail_change_reported": result["refinement"][-1]["tail_matrix_relative_change"],
            "q12_to_q16_loop_real_change_reported": result["refinement"][-1]["loop_real_tail_relative_change"],
            "q16_uniform_box_error_reported": result["refinement"][-1]["tail_uniform_box_relative_error"],
            "q16_loop_radiation_relative_error_reported": result["cases"][-1]["loop_radiation_relative_error"],
        },
        "scope": "Read-only saved-array review. Confirms the frozen controlled six-tetra exp(-ikR)/R RT0 primitive only; it does not qualify a material or board solve.",
    }
    gates = out["checks"]
    assert gates["tail_series_rebuild_relative"] < 1e-14
    assert gates["regular_equals_exact_minus_ik_moment_plus_tail_relative"] < 1e-14
    assert gates["conjugacy_relative"] < 1e-14
    assert gates["tail_raw_reciprocity_max"] < 1e-12 and gates["full_raw_reciprocity_max"] < 1e-10
    assert gates["closed_loop_boundary_flux_max"] < 2e-15 and gates["closed_loop_zero_moment_norm_m"] < 1e-18
    assert gates["loop_tail_imaginary_negative"]
    assert gates["q12_to_q16_tail_change_reported"] < 1e-3 and gates["q12_to_q16_loop_real_change_reported"] < 1e-3
    output.mkdir(parents=True, exist_ok=False)
    target = output / "independent-review.json"
    temp = output / "independent-review.json.tmp"
    temp.write_text(json.dumps(out, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, target)
    print(json.dumps({"status": out["status"], "sha256": sha(target)}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output.resolve())
