"""Compare frozen source-derived L14 or L14+L25 DC sheet results at 1 MHz."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import compare_astra_l14_trace_r_shadow as previous
from reconstruct_astra_native_loaded_field import _atomic_exclusive_json


def compare(shadow_path: Path, shadow_sha256: str, output: Path) -> dict:
    documents, inputs = {}, {}
    files = {name: value for name, value in previous.FILES.items() if name != "shadow"}
    files["shadow"] = (shadow_path, shadow_sha256)
    for name, (path, expected) in files.items():
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        if digest != expected:
            raise ValueError(f"frozen {name} hash mismatch")
        documents[name] = json.loads(data)
        inputs[name] = {"path": str(path), "sha256": digest, "size_bytes": len(data)}
    shadow, reference, baseline = (documents[name] for name in ("shadow", "reference", "baseline_comparison"))
    two_sheet = shadow["status"] == "COMPLETED_CONDITIONAL_L14_L25_DC_SHEET_SHADOW"
    native_input = shadow
    if two_sheet:
        prior_input = shadow["inputs"]["l14_result"]
        expected = "b0401b240cd855dc4beffbc2f8920c2f285022885c2940660601cc41aa2adca5"
        prior_bytes = Path(prior_input["path"]).read_bytes()
        if prior_input["sha256"] != expected or sha256(prior_bytes).hexdigest() != expected:
            raise ValueError("frozen L14 baseline hash differs")
        native_input = json.loads(prior_bytes)
        inputs["l14_baseline"] = prior_input
    rail = "ADC_VDD_075_VTRIP_SRAM/0"
    if not (
        shadow["status"] in ("COMPLETED_CONDITIONAL_L14_SHEET_R_SHADOW", "COMPLETED_CONDITIONAL_L14_SHEET_R_SHADOW_EPSILON1_ONLY", "COMPLETED_CONDITIONAL_L14_L25_DC_SHEET_SHADOW")
        and shadow["rail_id"] == reference["rail_id"] == baseline["rail_id"] == rail
        and shadow["frequency_hz"] == 1e6
        and reference["port_one_based"] == 18
        and native_input["inputs"]["raw"]["sha256"] == "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"
    ):
        raise ValueError("source, completion, rail, frequency or port contract differs")
    ref_point = next(p for p in reference["points"] if p["frequency_hz"] == 1e6)
    base_point = next(p for p in baseline["points"] if p["frequency_hz"] == 1e6)
    actual = [p for p in shadow["points"] if p["epsilon_sheet_resistance_scale"] == 1.0]
    if len(actual) != 1:
        raise ValueError("exactly one actual-source-resistance point is required")
    base_key = "original_native_zdd_ohm" if two_sheet else "collapsed_zdd_ohm"
    base_z = complex(*shadow["baseline"][base_key])
    if abs(base_z - complex(*base_point["native_zdd_ohm"])) > 1e-18:
        raise ValueError("baseline differs from the frozen development comparison")
    ref_z, new_z = complex(*ref_point["reference_zdd_ohm"]), complex(*actual[0]["zdd_ohm"])
    before, after = previous.metrics(base_z, ref_z), previous.metrics(new_z, ref_z)
    reduction = before["complex_gap_ohm"] - after["complex_gap_ohm"]
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "COMPLETED_CONDITIONAL_L14_L25_DC_SHEET_SHADOW_COMPARISON" if two_sheet else "COMPLETED_CONDITIONAL_L14_SHEET_R_SHADOW_COMPARISON",
        "rail_id": rail, "frequency_hz": 1e6, "reference_port_one_based": 18,
        "inputs": inputs, "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "metrics_helper_sha256": sha256(Path(previous.__file__).read_bytes()).hexdigest(),
        "reference_zdd_ohm": previous.pair(ref_z), "baseline": before,
        "conditional_sheet_r_shadow": after,
        "conditional_change": {
            "delta_zdd_ohm": previous.pair(new_z - base_z),
            "complex_gap_reduction_ohm": reduction,
            "complex_gap_reduction_fraction": reduction / before["complex_gap_ohm"],
            "absolute_magnitude_error_reduction_db": abs(before["magnitude_error_db"]) - abs(after["magnitude_error_db"]),
        },
        "limitations": [
            "Source-derived fixed-mesh DC sheet resistance with unchanged native external terms; no reference fitting.",
            "One development rail at 1 MHz; no mesh/electrode convergence, AC magnetic/return validation or unseen accuracy promotion.",
            "Positive gap reduction indicates improvement only for this conditional point; negative indicates worsening.",
        ],
    }
    if two_sheet:
        prior_points = [p for p in native_input["points"] if p["epsilon_sheet_resistance_scale"] == 1.0]
        if len(prior_points) != 1:
            raise ValueError("exactly one prior L14 epsilon-1 point is required")
        prior_z = complex(*prior_points[0]["zdd_ohm"])
        if prior_z != complex(*shadow["baseline"]["l14_only_zdd_ohm"]):
            raise ValueError("two-sheet L14 baseline differs")
        prior_metrics = previous.metrics(prior_z, ref_z)
        result["l14_only_baseline"] = prior_metrics
        result["incremental_l25_change"] = {
            "delta_zdd_ohm": previous.pair(new_z - prior_z),
            "complex_gap_reduction_ohm": prior_metrics["complex_gap_ohm"] - after["complex_gap_ohm"],
            "absolute_magnitude_error_reduction_db": abs(prior_metrics["magnitude_error_db"]) - abs(after["magnitude_error_db"]),
        }
    _atomic_exclusive_json(output, result)
    return result


def self_check() -> None:
    baseline = json.loads(previous.FILES["baseline_comparison"][0].read_text(encoding="utf-8"))
    z = next(p["native_zdd_ohm"] for p in baseline["points"] if p["frequency_hz"] == 1e6)
    control = {"status": "COMPLETED_CONDITIONAL_L14_SHEET_R_SHADOW", "rail_id": baseline["rail_id"], "frequency_hz": 1e6, "inputs": {"raw": {"sha256": "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"}}, "baseline": {"collapsed_zdd_ohm": z}, "points": [{"epsilon_sheet_resistance_scale": 1.0, "zdd_ohm": z}]}
    with TemporaryDirectory(prefix="sheet-comparison-control-") as temporary:
        path = Path(temporary) / "synthetic-control.json"
        path.write_text(json.dumps(control), encoding="utf-8")
        digest = sha256(path.read_bytes()).hexdigest()
        result = compare(path, digest, Path(temporary) / "zero-change.json")
        assert result["conditional_change"]["complex_gap_reduction_ohm"] == 0.0
        try:
            compare(path, "0" * 64, Path(temporary) / "must-not-exist.json")
        except ValueError as error:
            assert "hash mismatch" in str(error)
        else:
            raise AssertionError("mismatched result hash was accepted")
        prior_path = Path(__file__).resolve().parents[2] / "outputs/research/astra-l14-sheet-r-shadow-01/result.json"
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        prior_z = next(p["zdd_ohm"] for p in prior["points"] if p["epsilon_sheet_resistance_scale"] == 1.0)
        control.update(status="COMPLETED_CONDITIONAL_L14_L25_DC_SHEET_SHADOW",
                       inputs={"l14_result": {"path": str(prior_path), "sha256": "b0401b240cd855dc4beffbc2f8920c2f285022885c2940660601cc41aa2adca5"}},
                       baseline={"original_native_zdd_ohm": z, "l14_only_zdd_ohm": prior_z},
                       points=[{"epsilon_sheet_resistance_scale": 1.0, "zdd_ohm": prior_z}])
        path.write_text(json.dumps(control), encoding="utf-8")
        result = compare(path, sha256(path.read_bytes()).hexdigest(), Path(temporary) / "zero-increment.json")
        assert result["incremental_l25_change"]["delta_zdd_ohm"] == [0.0, 0.0]
        assert result["incremental_l25_change"]["complex_gap_reduction_ohm"] == 0.0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow", type=Path)
    parser.add_argument("--shadow-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        print("SELF_CHECK PASS: zero-change comparison and mismatched-hash rejection")
        raise SystemExit(0)
    if not all((args.shadow, args.shadow_sha256, args.output)):
        parser.error("--shadow, --shadow-sha256 and --output are required")
    result = compare(args.shadow.resolve(), args.shadow_sha256, args.output.resolve())
    print(json.dumps({"status": result["status"], "change": result["conditional_change"]}, separators=(",", ":")))
