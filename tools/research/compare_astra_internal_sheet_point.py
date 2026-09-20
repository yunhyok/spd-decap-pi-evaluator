"""Compare one saved conditional symmetric two-face internal-sheet 100 MHz point."""
import argparse, json, math
from pathlib import Path
from compare_astra_loaded_development_points import errors
from reconstruct_astra_native_loaded_field import _atomic_exclusive_json, _sha256_file

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/evaluation-research"
PRIMARY = DOCS / "astra_primary_band_comparison_2026-09-07.json"
PRIMARY_SHA256 = "ac08d7ea36a09989fb83e9113af399db41f698bf68e061e3c75020f4c41a5f8d"
OUTPUT = DOCS / "astra_internal_sheet_100mhz_comparison_2026-09-07.json"
RAIL, FREQUENCY_HZ = "ADC_VDD_075_VTRIP_SRAM/0", 100_000_000.0

def pair(value, label):
    assert isinstance(value, (list, tuple)) and len(value) == 2, label
    result = complex(float(value[0]), float(value[1]))
    assert math.isfinite(result.real) and math.isfinite(result.imag), label
    return result

def self_check():
    assert errors(1 + 2j, 1 + 2j)["complex_relative_error"] == 0
    assert math.isclose(errors(2j, 1j)["magnitude_error_db"], 6.020599913279624)
    assert pair([1.0, -2.0], "pair") == 1 - 2j
    print(json.dumps({"status": "SELF_CHECK_PASS", "program": "SPD Decap PI Evaluator", "version": "0.23.1"}))

def validate_point(point):
    assert float(point["frequency_hz"]) == FREQUENCY_HZ
    zdd = pair(point["zdd_ohm"], "internal zdd")
    assert zdd.real >= -1e-12
    residual = float(point["physical_residual_max_abs_a"])
    assert math.isfinite(residual) and residual < 1e-7
    for key in ("normalized_backward_residual", "pivot_abs_ratio", "power_closure_error_ohm"):
        assert math.isfinite(float(point[key]))
    total = 0j
    for name, value in point["power_contributions_ohm"].items():
        contribution = pair(value, f"power.{name}")
        assert contribution.real >= -1e-12, f"non-passive power.{name}"
        total += contribution
    assert abs(total - zdd) <= max(abs(zdd), math.ulp(1.0)) * 1e-7
    evidence = point["conditional_symmetric_two_face_internal_sheet"]
    for layer, thickness in (("l14", 20e-6), ("l25", 32e-6)):
        row = evidence[layer]
        assert float(row["conductivity_s_per_m"]) == 59_590_000.0
        assert float(row["thickness_m"]) == thickness
        rdc = float(row["dc_ohm_per_square"])
        assert math.isclose(rdc, 1.0 / (59_590_000.0 * thickness), rel_tol=0, abs_tol=1e-18)
        zcm, scale = pair(row["internal_ohm_per_square"], f"{layer}.Zcm"), pair(row["admittance_scale"], f"{layer}.scale")
        assert zcm != 0 and zcm.real >= -1e-12 and scale.real > 0 and abs(scale - rdc / zcm) < 1e-14
    return zdd, residual, evidence

def main(args):
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    result_path, result_sha = args.result.resolve(), args.sha256.lower()
    assert _sha256_file(PRIMARY) == PRIMARY_SHA256, "primary comparison hash changed"
    assert _sha256_file(result_path) == result_sha, "internal result hash mismatch"
    primary, result = json.loads(PRIMARY.read_text(encoding="utf-8")), json.loads(result_path.read_text(encoding="utf-8"))
    assert primary["program"] == result["program"] == "SPD Decap PI Evaluator" and primary["version"] == result["version"] == "0.23.1"
    assert primary["status"] == "COMPLETED_CONDITIONAL_PRIMARY_BAND_DECADE_COMPARISON" and result["status"] == "COMPLETED_CONDITIONAL_INTERNAL_SHEET_100MHZ"
    assert primary["rail_id"] == result["rail_id"] == RAIL and primary["reference_port_one_based"] == 18
    assert len(result["points"]) == 1
    point, primary_points = result["points"][0], [p for p in primary["points"] if float(p["frequency_hz"]) == FREQUENCY_HZ]
    assert len(primary_points) == 1
    primary_point, actual = primary_points[0], None
    actual, residual, evidence = validate_point(point)
    reference, native = pair(primary_point["reference_zdd_ohm"], "reference"), pair(primary_point["native_zdd_ohm"], "native")
    two_sheet = pair(primary_point["two_sheet_zdd_ohm"], "two-sheet")
    assert point["original_native_zdd_ohm"] == primary_point["native_zdd_ohm"] and point["two_sheet_dc_zdd_ohm"] == primary_point["two_sheet_zdd_ohm"]
    assert all(value != 0 for value in (actual, native, two_sheet, reference))
    output = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_CONDITIONAL_INTERNAL_SHEET_100MHZ_COMPARISON",
        "rail_id": RAIL, "frequency_hz": FREQUENCY_HZ, "reference_port_one_based": 18,
        "inputs": {"primary_comparison": {"path": str(PRIMARY.resolve()), "sha256": PRIMARY_SHA256}, "internal_result": {"path": str(result_path), "sha256": result_sha}},
        "zdd_ohm": {"internal_sheet": [actual.real, actual.imag], "reference": [reference.real, reference.imag], "native": [native.real, native.imag], "two_sheet_dc": [two_sheet.real, two_sheet.imag]},
        "comparisons": {"internal_vs_reference": errors(actual, reference), "native_vs_reference": errors(native, reference), "two_sheet_dc_vs_reference": errors(two_sheet, reference), "internal_vs_two_sheet_dc": errors(actual, two_sheet)},
        "constitutive_pins": evidence, "checks": {"baseline_two_sheet_exact": True, "native_baseline_exact": True, "physical_residual_max_abs_a": residual, "physical_residual_below_1e-7_a": True, "finite_complex_power_passivity": True},
        "limitations": ["One conditional symmetric two-face internal-only 100 MHz point; no fit or derivative extrapolation.", "No external magnetic coupling, return-path, proximity, broadband or PowerSI accuracy claim."],
        "script_sha256": _sha256_file(Path(__file__))}
    _atomic_exclusive_json(OUTPUT, output)
    print(json.dumps({"status": output["status"], "output": str(OUTPUT)}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--result", type=Path); parser.add_argument("--sha256"); parser.add_argument("--self-check", action="store_true"); args = parser.parse_args()
    if args.self_check: self_check()
    elif args.result is None or not args.sha256: parser.error("--result and --sha256 are required unless --self-check is used")
    else: main(args)
