"""Read-only saved-array receipt for the finite 3-D current/charge control."""
import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / "tools/research/diagnose_astra_3d_current_charge_field.py"
RESULT = ROOT / "outputs/research/astra-3d-current-charge-field-02/result.json"
FIELDS = RESULT.parent / "fields.npz"
VECTOR = ROOT / "outputs/research/astra-tetra-retarded-green-02/blocks.npz"
ELECTRIC = ROOT / "outputs/research/astra-charge-retarded-green-02/charge.npz"
STATIC_CHARGE = ROOT / "outputs/research/astra-tetra-charge-green-01/charge.npz"
PINS = {
    "driver": "4a6e46e9dc674b872ea205e0921a0673b29f8d4f2c91fd0d8f2a493b13baef10",
    "result": "484d6f46b6ea93e261e8b81d0958ecdc949fdf38c4658815604d0e13e051dbfb",
    "fields": "3625526b832b76fcfa32727d33bcb0d52a179e56728801ee4ace26d68f26ab2d",
    "vector": "c0f241d084c72d76afc85880dca4604c92b42dfcdac6aba11654045a57fc83f2",
    "electric": "05c122a8d4d599a7a8371678b7e580fa28d599bc972a3107d8fbf1970890b43c",
    "static_charge": "ed22fe446522687f89f8021fe6d122110bbb4b0282c97377d4a8984d33b02b60",
}


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def flatten(blocks):
    return blocks.transpose(0, 2, 1, 3).reshape(24, 24)


def rel(a, b):
    return float(np.linalg.norm(a - b) / np.linalg.norm(b))


def main(output):
    paths = (("driver", DRIVER), ("result", RESULT), ("fields", FIELDS), ("vector", VECTOR), ("electric", ELECTRIC), ("static_charge", STATIC_CHARGE))
    assert all(sha(path) == PINS[name] for name, path in paths)
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    with np.load(FIELDS, allow_pickle=False) as data:
        loop = data["loop"]
        basis = data["range_basis"]
        mass = data["geometric_mass"]
        saved = {key: data[key] for key in data.files if key.startswith("case_")}
    with np.load(VECTOR, allow_pickle=False) as data:
        tetrahedra = data["tetrahedra_m"]
        moments = data["exact_basis_volume_moments"].reshape(24, 3)
        ltail = np.array([flatten(x) for x in data["tail_blocks"]])
    with np.load(ELECTRIC, allow_pickle=False) as data:
        B = data["distributional_divergence"]
        ptail = data["regular_tail_per_m"]
    with np.load(STATIC_CHARGE, allow_pickle=False) as data:
        entities = list(tetrahedra) + list(data["triangles_m"])
        pstatic_raw = data["normalized_coulomb_matrix_per_m"]
    with np.load(ROOT / "outputs/research/astra-tetra-static-green-01/blocks.npz", allow_pickle=False) as data:
        lstatic_raw = flatten(data["box_local_rt0_blocks"])
    centroids = np.array([entity.mean(axis=0) for entity in entities])
    drives = np.array([[1, 0, 1], [0, 1, 1j]], complex)
    mu0, eps0 = 4e-7 * np.pi, 8.8541878128e-12
    factor = 1 / (4 * np.pi * eps0)
    continuity, dipole, reaction, radiation_rebuild, superposition = [], [], [], [], []
    for index, case in enumerate(result["cases"]):
        omega = 2 * np.pi * case["frequency_hz"]
        k = omega * np.sqrt(mu0 * eps0)
        current = saved[f"case_{index:02d}_current"]
        charge = saved[f"case_{index:02d}_charge"]
        react = saved[f"case_{index:02d}_reaction"]
        dip = saved[f"case_{index:02d}_charge_dipole"]
        exact = saved[f"case_{index:02d}_exact_current_volume_moment"]
        continuity.append(float(np.max(abs(B @ current + 1j * omega * charge) / np.maximum(abs(B) @ abs(current) + omega * abs(charge), np.finfo(float).tiny))))
        dipole.append(rel(dip, exact / (1j * omega)))
        reaction.append(rel(react, react.T))
        current_drive = current @ drives
        charge_drive = charge @ drives
        jmom = exact @ drives
        magnetic_r = omega * (1e-7 * k * np.sum(abs(jmom) ** 2, axis=0) - np.diag(current_drive.conj().T @ ltail[index].imag @ current_drive).real)
        scalar_r = omega * factor * np.diag(charge_drive.conj().T @ ptail[index].imag @ charge_drive).real
        radiation_rebuild.append(magnetic_r + scalar_r)
        superposition.append(float(max(abs(np.asarray(case["extinction_w"])[2] - sum(case["extinction_w"][:2])) / case["extinction_w"][2], abs(np.asarray(case["absorption_w"])[2] - sum(case["absorption_w"][:2])) / case["absorption_w"][2], abs(np.asarray(case["radiation_w"])[2] - sum(case["radiation_w"][:2])) / case["radiation_w"][2])))
    radiation_rebuild = np.array(radiation_rebuild)
    reported_radiation = np.array([case["radiation_w"] for case in result["cases"]])
    reported_extinction = np.array([case["extinction_w"] for case in result["cases"]])
    checks = {
        "static_magnetic_symmetric_pairing_change": float(np.linalg.norm((lstatic_raw + lstatic_raw.T) / 2 - lstatic_raw) / np.linalg.norm(lstatic_raw)),
        "static_scalar_symmetric_pairing_change": float(np.linalg.norm((pstatic_raw + pstatic_raw.T) / 2 - pstatic_raw) / np.linalg.norm(pstatic_raw)),
        "B_loop_zero_max": float(np.max(abs(B @ loop))),
        "centroid_B_plus_moment_relative": rel(centroids.T @ B, -moments.T),
        "continuity_relative_max": max(continuity), "dipole_relative_max": max(dipole), "reaction_reciprocity_max": max(reaction),
        "radiation_real_imag_rebuild_relative": rel(radiation_rebuild, reported_radiation),
        "independent_radiation_relative_max_reported": max(case["radiation_relative_error"] for case in result["cases"]),
        "power_balance_relative_max_reported": max(case["extinction_power_relative_error"] for case in result["cases"]),
        "one_plus_i_superposition_relative_max": max(superposition),
        "all_absorption_positive": bool(all(min(case["absorption_w"]) > 0 for case in result["cases"])),
        "all_radiation_positive": bool(all(min(case["radiation_w"]) > 0 for case in result["cases"])),
    }
    assert abs(checks["static_magnetic_symmetric_pairing_change"] - result["quadrature_relative_changes"]["static_magnetic"]) < 1e-24
    assert abs(checks["static_scalar_symmetric_pairing_change"] - result["quadrature_relative_changes"]["static_scalar"]) < 1e-18
    assert checks["B_loop_zero_max"] == 0 and checks["centroid_B_plus_moment_relative"] < 1e-13
    assert checks["continuity_relative_max"] < 1e-12 and checks["dipole_relative_max"] < 1e-10 and checks["reaction_reciprocity_max"] < 1e-5
    assert checks["radiation_real_imag_rebuild_relative"] < 1e-12
    assert checks["independent_radiation_relative_max_reported"] < 1e-5 and checks["power_balance_relative_max_reported"] < 1e-5
    assert checks["one_plus_i_superposition_relative_max"] < 1e-10
    assert checks["all_absorption_positive"] and checks["all_radiation_positive"]
    review = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "ACCEPT_3D_CURRENT_CHARGE_FIELD_FROZEN_CONTROL",
        "pins": {name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)} for name, path in paths}, "checks": checks,
        "scope": "Saved finite synthetic 100x100x25um vacuum copper-box diagnostic only; no mesh/material-interface/source-solid/port/board accuracy claim."}
    output.mkdir(parents=True, exist_ok=False)
    temp, final = output / "independent-review.json.tmp", output / "independent-review.json"
    temp.write_text(json.dumps(review, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, final)
    print(json.dumps({"status": review["status"], "sha256": sha(final)}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output.resolve())
