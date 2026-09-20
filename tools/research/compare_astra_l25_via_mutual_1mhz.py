"""Post-freeze comparison of the selected350-via mutual-only 1 MHz ablation."""
import json
from pathlib import Path
import numpy as np
from compare_astra_rt0_board_1mhz import sha
from compare_astra_loaded_development_points import errors

ROOT = Path(__file__).resolve().parents[2]
ACTUAL = ROOT / "outputs/research/astra-l25-via-mutual-board-1mhz-01/result.json"
BASELINE = ROOT / "outputs/research/astra-l25-rt0-board-1mhz-01/result.json"
PRIMARY = ROOT / "docs/evaluation-research/astra_primary_band_comparison_2026-09-07.json"
PINS = {ACTUAL: "561bfe4f3d63b7bd08eb5909f01fca1372382dc7f0bd3c5ce725d212d9ee8d5c",
        BASELINE: "61a48c10c054e65accc32203429d6b1c49b215693e12d439493a106d7c9e1752",
        PRIMARY: "ac08d7ea36a09989fb83e9113af399db41f698bf68e061e3c75020f4c41a5f8d",
        Path(__file__).with_name("compare_astra_loaded_development_points.py"): "0e12464d1297caa9964404a2a0777d22964d6817069f752641b2d7945c8f4f4f",
        Path(__file__).with_name("compare_astra_rt0_board_1mhz.py"): "c98cafb9d5cb1f5f6ed2623c9b33c5d83e231c3bfd2a5a7b5f823c79d1c8faba"}


def main():
    assert errors(1+2j, 1+2j)["complex_relative_error"] == 0
    for path, digest in PINS.items():
        assert sha(path) == digest, path
    actual, baseline, primary = [json.loads(path.read_bytes()) for path in (ACTUAL, BASELINE, PRIMARY)]
    assert actual["status"] == "COMPLETED_CONDITIONAL_L25_350_VIA_MUTUAL_1MHZ"
    assert sha(ACTUAL.parent/"driver-at-run.py") == actual["script_sha256"] == "0ec5514846b0f416d54f6a9df78f7527f46ed109b5f4ab4bfa7a8eb42a21f97a"
    assert actual["baseline_sha256"] == PINS[BASELINE]
    assert actual["rail_id"] == baseline["rail_id"] == primary["rail_id"] == "ADC_VDD_075_VTRIP_SRAM/0"
    assert actual["frequency_hz"] == baseline["frequency_hz"] == 1e6 and primary["reference_port_one_based"] == 18
    for key in ("inputs", "source_l14_inputs", "dc_step_artifacts", "gc_receipt"):
        assert actual["source"][key] == baseline[key], key
    raw_meta = actual["source"]["source_l14_inputs"]["raw"]
    assert sha(Path(raw_meta["path"])) == raw_meta["sha256"]
    with np.load(raw_meta["path"], allow_pickle=False) as raw:
        batch, = raw["batch_port_indices"]
        p, n = raw["global_to_active_indices"][raw["solve_port_reduced_nodes"][int(batch)]]
    reference_row, = [row for row in primary["points"] if row["frequency_hz"] == 1e6]
    reference, rows = complex(*reference_row["reference_zdd_ohm"]), []
    for name, path, receipt in (("original_native_via_diagonal", BASELINE, baseline), ("selected350_mutual", ACTUAL, actual)):
        point = receipt["point"]
        assert all(point["category_passivity"].values())
        assert max(point["physical_checks"][-1][key] for key in ("kcl_max_a", "constitutive_max_v")) < 1e-7
        field = path.parent/receipt["field"]["path"]
        assert sha(field) == receipt["field"]["sha256"]
        with np.load(field, allow_pickle=False) as archive:
            v = archive["active_voltage_v"]
            assert v.shape == (1483296,) and np.isfinite(v).all()
            z = complex(v[p]-v[n])
        assert abs(z-complex(*point["zdd_ohm"])) < 1e-15
        assert point["power_closure_error_ohm"] < abs(z)*1e-7
        rows.append({"model": name, "zdd_ohm": point["zdd_ohm"], "field_sha256": receipt["field"]["sha256"], "errors": errors(z, reference)})
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
              "status": "COMPLETED_CONDITIONAL_SELECTED350_VIA_MUTUAL_1MHZ_COMPARISON",
              "script_sha256": sha(Path(__file__)), "inputs": {str(path): digest for path, digest in PINS.items()},
              "rail_id": actual["rail_id"], "frequency_hz": 1e6, "reference_port_one_based": 18,
              "device_active_indices": [int(p), int(n)], "reference_zdd_ohm": reference_row["reference_zdd_ohm"], "points": rows,
              "remaining_rt0_reference_gap_reduction_fraction": 1-rows[1]["errors"]["complex_gap_ohm"]/rows[0]["errors"]["complex_gap_ohm"],
              "scope": "Reference read after source350 result freeze; no fitted coefficient. Only selected-via offdiagonal mutual is added, with native R/L diagonal unchanged and original selected350 stamps replaced once. DC L14/P1 and L25/RT0 sheets and other native terms are identical. This single development point does not certify full return, other-via or sheet magnetic coupling, quadrature/mesh convergence, broadband, unseen or product accuracy."}
    output = ACTUAL.parent/"comparison.json"
    payload = json.dumps(result, indent=2, allow_nan=False)+"\n"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(payload)
    print(json.dumps({"points": rows, "remaining_gap_reduction_fraction": result["remaining_rt0_reference_gap_reduction_fraction"], "comparison_sha256": sha(output)}, allow_nan=False))


if __name__ == "__main__":
    main()
