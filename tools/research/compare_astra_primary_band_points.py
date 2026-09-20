"""Combine checked low-band pairs with the existing four-point DC-sheet comparison."""
import argparse
import json
import math
from pathlib import Path

from compare_astra_loaded_development_points import errors
from reconstruct_astra_native_loaded_field import _sha256_file, _atomic_exclusive_json

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/evaluation-research"
OUTPUT = DOCS / "astra_primary_band_comparison_2026-09-07.json"
PINS = {
    "native_low_baseline": (ROOT / "outputs/research/astra-primary-low-band-native-02/result.json", "4036d18172ba24a2d58b5d028bdf8c9c9c1dea4e90ccdff50e0744b711396791"),
    "low_reference": (DOCS / "astra_powersi_lowband_reference_2026-09-07.json", "013d2a685b15fda6fc827e589e172913e25319086f672b5593e38e6975537cb1"),
    "higher_comparison": (DOCS / "astra_two_sheet_four_frequency_comparison_2026-09-07.json", "042da3f43f7671763d745409f283d5bb4c4e5635ca1f3708e9749799ec2ff793"),
}


def main(args):
    assert errors(1+2j,1+2j)["complex_relative_error"] == 0
    assert math.isclose(errors(2j,1j)["magnitude_error_db"],6.020599913279624)
    docs, inputs = {}, {}
    for name,(path,expected) in (PINS | {"low_run":(args.result,args.sha256)}).items():
        assert _sha256_file(path) == expected,name
        docs[name] = json.loads(path.read_text(encoding="utf-8"))
        inputs[name] = {"path":str(path.resolve()),"sha256":expected}
    rail = "ADC_VDD_075_VTRIP_SRAM/0"
    assert all(d["rail_id"] == rail for d in docs.values())
    assert docs["low_run"]["status"] == "COMPLETED_PRIMARY_LOW_BAND_NATIVE_TWO_DC_SHEET_COMPARISON"
    assert docs["native_low_baseline"]["status"] == "COMPLETED_PRIMARY_LOW_BAND_ORIGINAL_SOURCE_POINTS"
    assert docs["low_run"]["inputs"]["native_low_baseline"]["sha256"] == PINS["native_low_baseline"][1]
    assert docs["low_reference"]["status"] == "ACCEPT_EXISTING_LOWBAND_REFERENCE_POINTS_ONLY"
    assert docs["higher_comparison"]["status"] == "COMPLETED_CONDITIONAL_TWO_SHEET_FOUR_POINT_COMPARISON"
    assert docs["low_reference"]["port_one_based"] == docs["higher_comparison"]["reference_port_one_based"] == 18
    references = {p["frequency_hz"]:p for p in docs["low_reference"]["points"]}
    points = []
    assert [p["frequency_hz"] for p in docs["low_run"]["points"]] == [1e3,1e4,1e5]
    for point in docs["low_run"]["points"]:
        frequency = point["frequency_hz"]
        baseline = point["native_point"]
        assert baseline == next(p for p in docs["native_low_baseline"]["points"] if p["frequency_hz"] == frequency)
        assert point["physical_residual_max_abs_a"] < 1e-7
        assert baseline["status"] == "COMPLETED_ORIGINAL_SOURCE_FREQUENCY_POINT" and baseline["frequency_hz"] == frequency
        native = complex(*baseline["zdd_ohm"])
        assert native == complex(*point["original_native_zdd_ohm"])
        value,ref = complex(*point["zdd_ohm"]),complex(*references[frequency]["reference_zdd_ohm"])
        assert all(math.isfinite(x) for z in (native,value,ref) for x in (z.real,z.imag)) and min(abs(native),abs(value),abs(ref)) > 0
        before,after = errors(native,ref),errors(value,ref)
        points.append({"frequency_hz":frequency,"native_zdd_ohm":[native.real,native.imag],"two_sheet_zdd_ohm":[value.real,value.imag],"reference_zdd_ohm":[ref.real,ref.imag],
            "native_error":before,"two_sheet_error":after,"complex_gap_reduction_fraction":1-after["complex_gap_ohm"]/before["complex_gap_ohm"] if before["complex_gap_ohm"] else None,
            "native_frequency_status_and_finite_baseline_binding_checked":True})
    points += docs["higher_comparison"]["points"]
    assert [p["frequency_hz"] for p in points] == [1e3,1e4,1e5,1e6,1e7,1e8,1e9]
    for point in points:
        point["assessment_band"] = "primary_1khz_to_100mhz" if point["frequency_hz"] <= 1e8 else "secondary_1ghz"
    result = {"program":"SPD Decap PI Evaluator","version":"0.23.1","rail_id":rail,"reference_port_one_based":18,
        "status":"COMPLETED_CONDITIONAL_PRIMARY_BAND_DECADE_COMPARISON","inputs":inputs,"points":points,"script_sha256":_sha256_file(Path(__file__)),
        "limitations":["Six decade anchors in the primary band, not continuous-band or unseen validation;1GHz is secondary.",
            "Same conditional fixed DC sheets; no AC magnetic/skin/contact/mesh accuracy promotion.",
            "Imported low-frequency dielectric endpoint clamp is retained, not fitted to PowerSI."]}
    _atomic_exclusive_json(OUTPUT,result)
    print(json.dumps({"status":result["status"],"points":[{"frequency_hz":p["frequency_hz"],"band":p["assessment_band"],"native_error":p["native_error"],"two_sheet_error":p["two_sheet_error"]} for p in points]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result",type=Path,required=True)
    parser.add_argument("--sha256",required=True)
    main(parser.parse_args())
