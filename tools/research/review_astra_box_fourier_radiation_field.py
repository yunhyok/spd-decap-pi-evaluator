"""Saved-algebra review for frozen 72-coordinate Fourier-radiation control."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/diagnose_astra_box_enriched_field.py": "1f7e66cd49aae53bc1b4b490f16a27a25e33310f8393638346ba0437ff25dcca",
    "outputs/research/astra-box-fourier-radiation-field-01/result.json": "cba91f5f3eecfce25a87f6b39290a6d7ee17a0d27c653b92ebb4b8db75dfe770",
    "outputs/research/astra-box-fourier-radiation-field-01/fields.npz": "398190889c906612962b2126ccfba76256ea72b86f261649436930038f338b64",
}

def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def relative(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a-b) / max(np.linalg.norm(b), np.finfo(float).tiny))

def main() -> None:
    for name, expected in PINS.items():
        assert digest(ROOT / name) == expected, name
    import diagnose_astra_constant_current_3d_field as old
    result_path = ROOT / "outputs/research/astra-box-fourier-radiation-field-01/result.json"
    fields_path = ROOT / "outputs/research/astra-box-fourier-radiation-field-01/fields.npz"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    kernel_path = ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz"
    with np.load(kernel_path, allow_pickle=False) as kernel:
        tet, tri = kernel["tetrahedra_m"], kernel["triangles_m"]
        frequencies = kernel["frequencies_hz"]
    boundary, _, q, _, split = old.frame(tet, tri)
    assert split == 25 and q.shape == (48, 47)
    drives = np.array([[1, 0, 1], [0, 1, 1j]], complex)
    rows, properties = old.source.source_inputs()
    cases = []
    with np.load(fields_path, allow_pickle=False) as data:
        mass = data["mass"]
        assert mass.shape == (72, 72)
        for index, frequency in enumerate(frequencies):
            omega = 2*np.pi*frequency
            scaled = data[f"case_{index:02d}_scaled_solution"]
            modal = data[f"case_{index:02d}_modal_current"]
            rhs = data[f"case_{index:02d}_incident_rhs"]
            charge = data[f"case_{index:02d}_boundary_charge"]
            reaction = data[f"case_{index:02d}_reaction"]
            radiation_matrix = data[f"case_{index:02d}_radiation_matrix"]
            expected_modal = scaled.copy(); expected_modal[25:] *= omega
            u = modal @ drives
            direct_charge = 1j*q @ scaled[25:]
            direct_reaction = rhs.T @ modal
            extinction = np.real(np.sum(u.conj()*(rhs @ drives), axis=0))
            radiation = np.diag(u.conj().T @ radiation_matrix @ u).real
            _, _, _, gamma = old.source.materials(rows, properties, float(frequency))
            kappa = gamma[0] - 1j*omega*old.source.EPS0
            absorption = (1/kappa).real*np.diag(u.conj().T @ mass @ u).real
            saved_case = result["cases"][index]
            closure = float(np.max(abs(extinction-absorption-radiation)/(abs(extinction)+abs(absorption)+abs(radiation))))
            cases.append({"frequency_hz": float(frequency),
                          "modal_scaling_relative": relative(modal, expected_modal),
                          "charge_continuity_relative": relative(charge, direct_charge),
                          "bilinear_reaction_relative": relative(reaction, direct_reaction),
                          "radiation_matrix_symmetry_relative": relative(radiation_matrix, radiation_matrix.T),
                          "radiation_minimum_eigenvalue": float(np.linalg.eigvalsh((radiation_matrix+radiation_matrix.T)/2).min()),
                          "extinction_relative_to_result": relative(extinction, np.array(saved_case["extinction_w"])),
                          "absorption_relative_to_result": relative(absorption, np.array(saved_case["absorption_w"])),
                          "radiation_relative_to_result": relative(radiation, np.array(saved_case["radiation_w"])),
                          "power_closure_relative": closure,
                          "field_l2_change_from_frozen_48": saved_case["field_l2_change_from_frozen_48"]})
    assert max(c["modal_scaling_relative"] for c in cases) == 0.0
    assert max(c["charge_continuity_relative"] for c in cases) == 0.0
    payload = {
        "program": "SPD Decap PI Evaluator", "review": "independent saved old72 Fourier-radiation field control",
        "status": "ACCEPT_OLD72_REPRODUCTION_ONLY", "input_sha256": PINS,
        "reviewer_sha256": digest(Path(__file__)), "saved_checks": cases,
        "formula_review": {
            "common_origin": "Frozen driver uses a common box center and expm1(-i k (r-center).n), then adds the analytic total-current term. This avoids cancellation while retaining the shared origin phase; incident RHS carries exp(-i k center_z).",
            "continuity": "Stored modal current is exactly the scaled solution with the 47 boundary-range coordinates multiplied by omega; stored charge is exactly i q s, the driver’s distributional current-charge convention.",
            "radiation": "For exp(+j omega t), frozen driver uses omega*mu*k/(16 pi^2) times the transverse Fourier Hermitian Gram, adds its real symmetric part to impedance, and recomputes far-field radiation from the same sign convention.",
            "power": "Saved incident RHS, bilinear reaction rhs.T@modal, Hermitian extinction, material absorption, and radiation were independently recomputed from saved values without a solve.",
        },
        "scope": "72-coordinate old RT0 closed-plus-boundary-range reproduction control only. It does not qualify enriched/q14 kernels, basis convergence, material junctions, terminals, board, or PowerSI accuracy.",
    }
    out = ROOT / "outputs/research/astra-box-fourier-radiation-field-review-01"
    assert not out.exists(), out
    out.mkdir()
    target = out / "independent-review.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": payload["status"], "receipt_sha256": digest(target)}, sort_keys=True))

if __name__ == "__main__":
    main()
