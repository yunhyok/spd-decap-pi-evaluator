"""Saved-field derivative toward symmetric two-face internal sheet impedance."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import csc_matrix

from spd_decap_pi._core.solver.tri_fem_sheet import common_mode_copper_sheet_impedance
from reconstruct_astra_native_loaded_field import _sha256_file, _atomic_exclusive_json

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
OUTPUT = R / "astra-two-sheet-frequency-shadow-01/internal-sheet-direction.json"
PINS = {
    "one_mhz": (R / "astra-l14-l25-sheet-r-shadow-01/result.json", "d5a9873fb81c21773dbca79b96a83496078bdbfaa3b345478d0d21a4448e0530"),
    "higher_f": (R / "astra-two-sheet-frequency-shadow-01/result.json", "f580a47697385efdd234f6c8738119285bc3cac94a4ad1827ffd5e7b449f4372"),
    "port_review": (R / "astra-two-sheet-frequency-shadow-01/independent-comparison-review.json", "5ed7c6138d7bcaeab91eac28f7e56a24338afa326ac206a71151e5b7b0f32e56"),
    "l14_drive": (R / "astra-l14-sheet-mesh-preflight-05/sheet-electrode-drive.npz", "05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d"),
    "l25_drive": (R / "astra-l25-sheet-drive-02/sheet-electrode-drive.npz", "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266"),
    "internal_kernel": (ROOT / "src/spd_decap_pi/_core/solver/tri_fem_sheet.py", "16eb6fd122fb5cf8d7ab60bb4b0364b760cb354806c02651d2847ede633640e0"),
}
SHEETS = {
    "l14": (20e-6, 147057, "l14_sheet_active_indices", "l14_sheet_dc", 718402, 756889),
    "l25": (32e-6, 532524, "sheet_active_indices", "sheet_dc", 258027, 903945),
}
pair = lambda z: [float(np.real(z)), float(np.imag(z))]


def self_check():
    # A complex reciprocal two-sheet circuit distinguishes transpose from adjoint.
    g1, g2 = np.array([[2., -.5], [-.5, .5]]), np.diag([0., .7])
    rest = np.diag([.3+.8j, .6-.2j])
    q1, q2, rhs = .1+.7j, .3+.2j, np.array([1., 0.])
    solve = lambda a: np.linalg.solve(rest+g1/(1+a*q1)+g2/(1+a*q2), rhs)
    v, step = solve(0), 1e-5
    derivative = q1*(v @ g1 @ v)+q2*(v @ g2 @ v)
    finite = (rhs @ solve(step)-rhs @ solve(-step))/(2*step)
    assert abs(derivative-finite) < 1e-9
    assert abs(derivative-(q1*np.vdot(v,g1@v)+q2*np.vdot(v,g2@v))) > 1e-3
    assert common_mode_copper_sheet_impedance(0., 59590000., 20e-6) == 1/(59590000.*20e-6)
    return {"complex_toy_derivative_error_ohm":float(abs(derivative-finite)), "transpose_not_adjoint_checked":True}


def main():
    started = time.perf_counter()
    checks, docs, matrices, inputs = self_check(), {}, {}, {}
    for name, (path, expected) in PINS.items():
        assert _sha256_file(path) == expected, name
        inputs[name] = {"path":str(path), "sha256":expected}
        if path.suffix == ".json":
            docs[name] = json.loads(path.read_text(encoding="utf-8"))
    assert docs["port_review"]["status"] == "ACCEPT_CONDITIONAL_FOUR_FREQUENCY_COMPARISON"
    rail = "ADC_VDD_075_VTRIP_SRAM/0"
    assert docs["one_mhz"]["rail_id"] == docs["higher_f"]["rail_id"] == rail
    for name, (_, size, *_) in SHEETS.items():
        with np.load(PINS[name+"_drive"][0], allow_pickle=False) as d:
            matrix = csc_matrix((d["conductance_data"],d["conductance_indices"],d["conductance_indptr"]),shape=tuple(d["conductance_shape"]))
        assert matrix.shape == (size,size) and np.all(np.isfinite(matrix.data))
        matrices[name] = matrix
    samples = [(1e6,docs["one_mhz"]["points"][0])] + [(p["frequency_hz"],p) for p in docs["higher_f"]["points"]]
    assert [f for f,_ in samples] == [1e6,1e7,1e8,1e9]
    points = []
    for frequency, point in samples:
        path = Path(point["field"]["path"])
        assert path.is_relative_to(R) and _sha256_file(path) == point["field"]["sha256"]
        sheets, total = {}, 0j
        with np.load(path, allow_pickle=False) as field:
            voltage = field["active_voltage"]
            assert voltage.shape == (1436468,) and np.all(np.isfinite(voltage))
            assert voltage[2699]-voltage[2656] == complex(*point["zdd_ohm"])
            for name, (thickness,size,key,category,target,start) in SHEETS.items():
                active = field[key]
                assert np.array_equal(active,np.r_[target,np.arange(start,start+size-1)])
                original = voltage[active]
                # Differentiate the actual frozen matrix in its saved circuit gauge.
                v = original
                current = matrices[name] @ v
                bilinear, hermitian = v @ current, np.vdot(v,current)
                recorded = complex(*point["power_contributions_ohm"][category])
                tolerance = max(1e-12,1e-7*abs(recorded))
                assert hermitian.real > 0 and abs(hermitian.imag) < tolerance
                assert abs(hermitian-recorded) < tolerance
                assert abs(bilinear) <= hermitian.real+tolerance
                shifted = original-original[0]
                gauge_error = abs(bilinear-shifted @ (matrices[name] @ shifted))
                dc = 1/(59590000.*thickness)
                internal = common_mode_copper_sheet_impedance(frequency,59590000.,thickness)
                q = internal/dc-1
                direction = q*bilinear
                total += direction
                sheets[name] = {"conductivity_s_per_m":59590000.,"thickness_m":thickness,
                    "dc_ohm_per_square":dc,"conditional_internal_ohm_per_square":pair(internal),
                    "relative_constitutive_direction":pair(q),"relative_constitutive_direction_abs":abs(q),
                    "bilinear_sheet_coefficient_ohm":pair(bilinear),"hermitian_joule_coefficient_ohm":pair(hermitian),
                    "saved_joule_difference_ohm":abs(hermitian-recorded),"bilinear_gauge_shift_difference_ohm":float(gauge_error),
                    "bilinear_gauge_shift_difference_over_joule":float(gauge_error/hermitian.real),
                    "device_direction_ohm_per_alpha":pair(direction)}
        points.append({"frequency_hz":frequency,"field":point["field"],"baseline_zdd_ohm":point["zdd_ohm"],
            "sheets":sheets,"combined_device_direction_ohm_per_alpha":pair(total)})
    result = {"program":"SPD Decap PI Evaluator","version":"0.23.1","rail_id":rail,
        "status":"COMPLETED_CONDITIONAL_INTERNAL_SHEET_LOCAL_DIRECTION","inputs":inputs,"points":points,
        "self_check":checks,"elapsed_s":time.perf_counter()-started,"script_sha256":_sha256_file(Path(__file__)),
        "formula":"Zsheet(alpha)=Rdc+alpha*(Zcommon_mode-Rdc); dZdd/dalpha at0=sum((Zcommon_mode/Rdc-1)*v.T*Gdc*v), for the saved unit-current reciprocal circuit.",
        "limitations":["Symmetric two-face internal impedance only; no source evidence establishes equal physical face currents.",
            "Derivative at the frozen DC model, not a finite-alpha1 response, error bound, or accuracy improvement.",
            "No external magnetic/proximity/return operator or changed native via R/L; no PowerSI reference is used.",
            "Saved meshes/voltages only; no geometry or global LU repeated. Fixed electrode and mesh limitations remain.",
            "Derivative uses the frozen circuit gauge; finite-precision sheet row sums cause the separately reported local gauge-shift discrepancy."]}
    _atomic_exclusive_json(OUTPUT,result)
    print(json.dumps({"status":result["status"],"elapsed_s":result["elapsed_s"],"points":[{"frequency_hz":p["frequency_hz"],"direction_ohm_per_alpha":p["combined_device_direction_ohm_per_alpha"],"q_abs":{k:s["relative_constitutive_direction_abs"] for k,s in p["sheets"].items()}} for p in points]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check",action="store_true")
    args = parser.parse_args()
    print(json.dumps(self_check())) if args.self_check else main()
