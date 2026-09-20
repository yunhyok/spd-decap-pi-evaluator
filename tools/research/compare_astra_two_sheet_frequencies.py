"""Compare four frozen two-DC-sheet samples with the existing PowerSI reference."""
from pathlib import Path
import json
import math

from compare_astra_loaded_development_points import errors
from reconstruct_astra_native_loaded_field import _sha256_file, _atomic_exclusive_json

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs/research/astra-two-sheet-frequency-shadow-01"
DOCS = ROOT / "docs/evaluation-research"
OUTPUT = DOCS / "astra_two_sheet_four_frequency_comparison_2026-09-07.json"
PINS = {
    "native": (DOCS / "astra_native_loaded_development_rail_2026-09-07.json", "e2c1f16ce4ff3998445e09c6cde2d1b23cdbc4d2c5e102e32c5f3d915e51790d"),
    "reference": (DOCS / "astra_loaded_development_reference_2026-09-07.json", "761d61334678ceaca0db55bc8c5539bb080ee36363eae33a2c419a1ea5c06430"),
    "one_mhz": (ROOT / "outputs/research/astra-l14-l25-sheet-r-shadow-01/result.json", "d5a9873fb81c21773dbca79b96a83496078bdbfaa3b345478d0d21a4448e0530"),
    "higher_f": (RUN / "result.json", "f580a47697385efdd234f6c8738119285bc3cac94a4ad1827ffd5e7b449f4372"),
    "port_zdd_review": (RUN / "one-mhz-field-zdd-check.json", "afecf2f13d39567e1ea1fff5935e7561cdf450ab7290029baf9794824c643be7"),
}


def main():
    assert errors(1+2j,1+2j)["complex_relative_error"] == 0
    assert math.isclose(errors(2j,1j)["magnitude_error_db"], 6.020599913279624)
    docs = {}
    for name, (path, expected) in PINS.items():
        assert _sha256_file(path) == expected, name
        docs[name] = json.loads(path.read_text(encoding="utf-8"))
    rail = "ADC_VDD_075_VTRIP_SRAM/0"
    assert all(docs[name]["rail_id"] == rail for name in ("native","reference","one_mhz","higher_f"))
    assert docs["native"]["status"] == "COMPLETED_ACTUAL_SOURCE_MOUNTED_DEVELOPMENT_RAIL_FOUR_POINT"
    assert docs["higher_f"]["status"] == "COMPLETED_CONDITIONAL_TWO_DC_SHEET_HIGH_FREQUENCY_SHADOW"
    assert docs["one_mhz"]["status"] == "COMPLETED_CONDITIONAL_L14_L25_DC_SHEET_SHADOW"
    assert docs["reference"]["port_one_based"] == 18
    actual = {1e6: docs["one_mhz"]["points"][0]} | {p["frequency_hz"]: p for p in docs["higher_f"]["points"]}
    assert len(docs["higher_f"]["points"]) == 3 and set(actual) == {1e6,1e7,1e8,1e9}
    reference = {p["frequency_hz"]: complex(*p["reference_zdd_ohm"]) for p in docs["reference"]["points"]}
    points = []
    for frequency in sorted(actual):
        native_row = docs["native"]["points"][f"{frequency:.0f}"]
        assert native_row["frequency_hz"] == frequency
        assert native_row["status"] == "COMPLETED_ACTUAL_SOURCE_MOUNTED_DEVICE_POINT"
        native = complex(*native_row["device_zdd_ohm"])
        point, ref = actual[frequency], reference[frequency]
        value = complex(*point["zdd_ohm"])
        assert all(math.isfinite(x) for z in (native, value, ref) for x in (z.real,z.imag))
        bound_native = point.get("original_native_zdd_ohm", docs["one_mhz"]["baseline"]["original_native_zdd_ohm"])
        assert abs(complex(*bound_native)-native) <= 1e-18
        before, after = errors(native,ref), errors(value,ref)
        points.append({"frequency_hz":frequency,"native_zdd_ohm":[native.real,native.imag],"two_sheet_zdd_ohm":[value.real,value.imag],"reference_zdd_ohm":[ref.real,ref.imag],
            "native_error":before,"two_sheet_error":after,"complex_gap_reduction_fraction":1-after["complex_gap_ohm"]/before["complex_gap_ohm"],
            "native_frequency_status_and_finite_baseline_binding_checked":True})
    result = {"program":"SPD Decap PI Evaluator","version":"0.23.1","status":"COMPLETED_CONDITIONAL_TWO_SHEET_FOUR_POINT_COMPARISON","rail_id":rail,"reference_port_one_based":18,
        "inputs":{k:{"path":str(p),"sha256":h} for k,(p,h) in PINS.items()},"points":points,"script_sha256":_sha256_file(Path(__file__)),
        "limitations":["Conditional DC-sheet replacement; original sampled caps and per-gap dispersion retained, no PowerSI parameter fit.",
            "Four development samples only; no dense-band, mesh/contact convergence, magnetic/skin/proximity or unseen validation.",
            "Magnitude agreement alone does not certify complex impedance accuracy."]}
    _atomic_exclusive_json(OUTPUT,result)
    print(json.dumps({"status":result["status"],"points":points},separators=(",",":")))


if __name__ == "__main__":
    main()
