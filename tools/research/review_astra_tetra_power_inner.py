"""Independent saved/static review of tetra distance-power inner01."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/qualify_astra_tetra_power_inner.py": "ac5705ef325a828df1dcb90251d211504ddcee7d87ef4dbf70b667913e90ea11",
    "outputs/research/astra-tetra-power-inner-01/result.json": "e6270f5b782a54e36d9f32d2103e638a403d8916ba80a4c7efe614fa15f061d9",
    "outputs/research/astra-tetra-power-inner-01/powers.npz": "b37e67cce95f66d02424a52fd9423998e6f047c3b2b44bdcb0fb09b1dddc5b18",
}

def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def main() -> None:
    for name, expected in PINS.items():
        assert digest(ROOT / name) == expected, name
    source = ROOT / "outputs/research/astra-tetra-power-inner-01"
    result = json.loads((source / "result.json").read_text(encoding="utf-8"))
    kernel_path = ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz"
    with np.load(source / "powers.npz", allow_pickle=False) as powers, np.load(kernel_path, allow_pickle=False) as kernels:
        tetrahedra = kernels["tetrahedra_m"] / 1e-4
        volume_errors = []
        positivity = []
        for cell in (0, 17, 47):
            values = powers[f"cell_{cell}_powers"]
            volume = abs(np.linalg.det((tetrahedra[cell][1:] - tetrahedra[cell][0]).T)) / 6
            volume_errors.append(float(abs(values[1, 0] - volume) / volume))
            positivity.append(bool(np.all(values > 0)))
    assert max(volume_errors) < 1e-14 and all(positivity)
    checks = result["checks"]
    odd_max = max(row["odd_quadrature_relative"] for row in checks)
    odd_refinement_max = max(row["odd_quadrature_refinement_relative"] for row in checks)
    payload = {
        "program": "SPD Decap PI Evaluator", "review": "independent saved/static tetra power-inner01",
        "status": "ACCEPT_RECURRENCES_WITH_FINITE_QUADRATURE_SCOPE", "input_sha256": PINS,
        "reviewer_sha256": digest(Path(__file__)),
        "saved_array_checks": {"cells": [0, 17, 47], "p0_volume_relative_errors": volume_errors,
                               "all_saved_power_values_positive": positivity},
        "recurrence_review": {
            "line": "For p=1..7, frozen helper lines 32-33 use L_p=(B_p+p r0^2 L_(p-2))/(p+1), with p=-1,0 bases. The p+1 denominator and sign follow the one-dimensional primitive derivative.",
            "face": "Frozen helper line 34 uses A_p=(sum_e signed_distance_e L_{p,e}+p h^2 A_(p-2))/(p+2). Its p+2 denominator is the planar divergence identity.",
            "volume": "Frozen helper lines 42-45 sum outward-face signed heights times A_p and divide indexed p=-1..7 by p+3 (array denominators 2..10). The p=0 volume identity is independently rechecked from saved geometry.",
            "orientation": "Pinned static faces() reverses a face if its normal points toward the opposite vertex; therefore face normal is outward, while observer-to-face height is intentionally signed. Triangle normal distance h is absolute only where the planar recurrence requires h^2.",
            "translation_scaling": "Translation occurs before recurrence evaluation; dividing scaled values by s^(p+3) for p=-1..7 matches the volume-integral dimensions.",
        },
        "reported_convergence": checks,
        "finite_quadrature_limit": {
            "q26_odd_interior_max_relative_error": odd_max,
            "q18_to_q26_odd_max_relative_change": odd_refinement_max,
            "finding": "Odd interior q26 is finite-quadrature evidence only (5.36709458982218e-06 error; q18-to-q26 1.8976694772997065e-05), not an exact primitive certification. Exterior odd checks are much tighter but do not remove this interior limitation.",
        },
        "scope": "Constant-source tetra R^-1 through R^7 inner integrals in normalized geometry. No observer integration, RT0-bubble or other cross-kernel, finite-frequency field, material junction, port, or board qualification.",
    }
    out = ROOT / "outputs/research/astra-tetra-power-inner-review-01"
    assert not out.exists(), out
    out.mkdir()
    target = out / "independent-review.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": payload["status"], "receipt_sha256": digest(target)}, sort_keys=True))

if __name__ == "__main__":
    main()
