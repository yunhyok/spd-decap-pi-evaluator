"""Independently reconstruct incident-field extinction for the saved 3-D box control."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path

import numpy as np
from numpy.polynomial.legendre import leggauss


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "outputs/research/astra-3d-current-charge-field-02/result.json"
FIELDS = RESULT.parent / "fields.npz"
VECTOR = ROOT / "outputs/research/astra-tetra-retarded-green-02/blocks.npz"
REVIEW01 = ROOT / "outputs/research/astra-3d-current-charge-field-review-01/independent-review.json"
PINS = {
    RESULT: "484d6f46b6ea93e261e8b81d0958ecdc949fdf38c4658815604d0e13e051dbfb",
    FIELDS: "3625526b832b76fcfa32727d33bcb0d52a179e56728801ee4ace26d68f26ab2d",
    VECTOR: "c0f241d084c72d76afc85880dca4604c92b42dfcdac6aba11654045a57fc83f2",
    REVIEW01: "69ffe0d99830eb174b51313d7bc917f7f47c7f2eb3122ec8228e8cd2b9333878",
    ROOT / "outputs/research/astra-native-frequency-stamps-01/source-frequency-inputs.json":
        "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc",
    ROOT / "outputs/research/astra-native-low-band-stamps-01/receipt.json":
        "1968b9091d7eab184c2c0ad195fdc847661377484624a4ac4985d06e42216299",
    ROOT / "outputs/research/astra-native-frequency-stamps-01/receipt.json":
        "4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856",
    ROOT / "outputs/research/astra-native-low-band-stamps-01/independent-review.json":
        "1fed75a2227ffb90f937914ebf2b112614736b632b9fec053ac9876125168228",
    ROOT / "outputs/research/astra-native-frequency-stamps-01/independent-review.json":
        "74d4476018645392c66164990ee7a44c08a09ca9594edc7df7dc95fff1ad2f94",
}
MU0 = 4.0e-7 * np.pi
EPS0 = 8.8541878128e-12


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def relative(actual: np.ndarray, reference: np.ndarray) -> float:
    return float(np.linalg.norm(actual - reference) / np.linalg.norm(reference))


def tetra_quadrature(tetra: np.ndarray, order: int) -> tuple[np.ndarray, np.ndarray]:
    """Independent tensor-Duffy rule on a tetrahedron."""

    coordinate, weight = leggauss(order)
    coordinate, weight = (coordinate + 1.0) / 2.0, weight / 2.0
    u, v, t = np.meshgrid(coordinate, coordinate, coordinate, indexing="ij")
    weights = (
        weight[:, None, None]
        * weight[None, :, None]
        * weight[None, None, :]
        * (1.0 - u) ** 2
        * (1.0 - v)
    ).ravel()
    barycentric = np.column_stack(
        (
            ((1.0 - u) * (1.0 - v) * (1.0 - t)).ravel(),
            u.ravel(),
            ((1.0 - u) * v).ravel(),
            ((1.0 - u) * (1.0 - v) * t).ravel(),
        )
    )
    volume = abs(float(np.linalg.det((tetra[1:] - tetra[0]).T))) / 6.0
    weights *= 6.0 * volume
    if not np.isclose(weights.sum(), volume, rtol=2.0e-14, atol=0.0):
        raise RuntimeError("independent tetrahedron quadrature does not conserve volume")
    return barycentric @ tetra, weights


def incident_rhs(
    tetrahedra: np.ndarray,
    moments: np.ndarray,
    loop: np.ndarray,
    range_basis: np.ndarray,
    wave_number: float,
    order: int,
) -> np.ndarray:
    phase = np.zeros((24, 2), dtype=complex)
    for cell, tetra in enumerate(tetrahedra):
        points, weights = tetra_quadrature(tetra, order)
        volume = abs(float(np.linalg.det((tetra[1:] - tetra[0]).T))) / 6.0
        basis = (points[:, None, :] - tetra[None, :, :]) / (3.0 * volume)
        remainder = np.expm1(-1j * wave_number * points[:, 2])
        phase[4 * cell : 4 * cell + 4] = np.einsum(
            "p,pid,p->id", weights, basis[:, :, :2], remainder
        )
    full = moments[:, :2] + phase
    # The integer loop has exactly zero constant volume moment.  Keeping its
    # expm1 remainder separate avoids a low-frequency subtraction.
    return np.vstack((loop.T @ phase, range_basis.T @ full))


def run(output: Path) -> None:
    for path, expected in PINS.items():
        if digest(path) != expected:
            raise RuntimeError(f"input hash changed: {path.relative_to(ROOT)}")
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    if result["status"] != "COMPLETE_3D_CURRENT_CHARGE_FIELD_DIAGNOSTIC":
        raise RuntimeError("coupled field result status changed")
    with np.load(FIELDS, allow_pickle=False) as data:
        loop = data["loop"]
        range_basis = data["range_basis"]
        saved_solution = [data[f"case_{index:02d}_scaled_solution"] for index in range(6)]
        saved_current = [data[f"case_{index:02d}_current"] for index in range(6)]
    with np.load(VECTOR, allow_pickle=False) as data:
        tetrahedra = data["tetrahedra_m"]
        moments = data["exact_basis_volume_moments"].reshape(24, 3)
        frequencies = data["frequencies_hz"]
    if not np.array_equal(frequencies, np.array([1e3, 1e4, 1e5, 1e6, 1e7, 1e8])):
        raise RuntimeError("frequency contract changed")
    if np.max(abs(loop.T @ moments)) >= 1.0e-14 * np.linalg.norm(moments):
        raise RuntimeError("loop lost its exact-zero moment contract")

    transform = np.column_stack((loop, range_basis))
    drives = np.array([[1.0, 0.0, 1.0], [0.0, 1.0, 1j]], dtype=complex)
    cases: list[dict[str, float]] = []
    all_extinction = []
    for index, frequency in enumerate(frequencies):
        omega = 2.0 * np.pi * float(frequency)
        wave_number = omega * np.sqrt(MU0 * EPS0)
        rhs8 = incident_rhs(tetrahedra, moments, loop, range_basis, wave_number, 8)
        rhs16 = incident_rhs(tetrahedra, moments, loop, range_basis, wave_number, 16)
        coefficients = saved_solution[index].copy()
        coefficients[1:] *= omega
        current_rebuilt = transform @ coefficients
        if relative(current_rebuilt, saved_current[index]) >= 2.0e-15:
            raise RuntimeError("saved current is inconsistent with scaled solution")
        modal = coefficients @ drives
        extinction8 = np.real(np.sum(np.conj(modal) * (rhs8 @ drives), axis=0))
        extinction16 = np.real(np.sum(np.conj(modal) * (rhs16 @ drives), axis=0))
        reported = np.asarray(result["cases"][index]["extinction_w"])
        absorption = np.asarray(result["cases"][index]["absorption_w"])
        radiation = np.asarray(result["cases"][index]["radiation_w"])
        denominator = abs(extinction16) + abs(absorption) + abs(radiation)
        closure = abs(extinction16 - absorption - radiation) / denominator
        entry = {
            "frequency_hz": float(frequency),
            "rhs_q8_to_q16_relative": relative(rhs8, rhs16),
            "extinction_q16_to_reported_relative": relative(extinction16, reported),
            "independent_extinction_power_closure_max": float(np.max(closure)),
            "current_reconstruction_relative": relative(current_rebuilt, saved_current[index]),
        }
        cases.append(entry)
        all_extinction.append(extinction16)

    maxima = {
        key: max(case[key] for case in cases)
        for key in (
            "rhs_q8_to_q16_relative",
            "extinction_q16_to_reported_relative",
            "independent_extinction_power_closure_max",
            "current_reconstruction_relative",
        )
    }
    if maxima["rhs_q8_to_q16_relative"] >= 1.0e-12:
        raise RuntimeError("incident-field quadrature did not converge")
    if maxima["extinction_q16_to_reported_relative"] >= 1.0e-10:
        raise RuntimeError("independent extinction does not reproduce the saved report")
    if maxima["independent_extinction_power_closure_max"] >= 1.0e-5:
        raise RuntimeError("independent extinction fails the physical power gate")

    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_3D_CURRENT_CHARGE_INDEPENDENT_EXTINCTION_RECONSTRUCTION",
        "pins": {
            str(path.relative_to(ROOT)).replace("\\", "/"): expected
            for path, expected in PINS.items()
        },
        "incident_field": {
            "phasor_convention": "exp(+j omega t)",
            "basis": "two transverse unit electric fields exp(-j k z)",
            "quadrature": "independent tensor-Duffy q8/q16; expm1 loop remainder plus exact range moments",
        },
        "cases": cases,
        "maxima": maxima,
        "scope": (
            "Read-only incident-RHS and sesquilinear extinction reconstruction for the frozen finite "
            "synthetic 100x100x25 um vacuum copper-box control. No kernel, solve, source-solid, port, "
            "board, or PowerSI accuracy claim."
        ),
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    temporary = output / "independent-review.json.tmp"
    temporary.write_text(json.dumps(review, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, output / "independent-review.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/research/astra-3d-current-charge-field-review-02",
    )
    run(parser.parse_args().output.resolve())
