"""Compare the frozen conditional RT0 board point without another response solve."""
import hashlib
import json
from pathlib import Path

import numpy as np
from compare_astra_loaded_development_points import errors

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/evaluation-research"
RUN = ROOT / "outputs/research/astra-l25-rt0-board-1mhz-01"
PINS = {
    "primary": (DOCS / "astra_primary_band_comparison_2026-09-07.json", "ac08d7ea36a09989fb83e9113af399db41f698bf68e061e3c75020f4c41a5f8d"),
    "actual": (RUN / "result.json", "61a48c10c054e65accc32203429d6b1c49b215693e12d439493a106d7c9e1752"),
    "errors_helper": (ROOT / "tools/research/compare_astra_loaded_development_points.py", "0e12464d1297caa9964404a2a0777d22964d6817069f752641b2d7945c8f4f4f"),
}


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    assert errors(1+2j, 1+2j)["complex_relative_error"] == 0
    for path, digest in PINS.values():
        assert sha(path) == digest, str(path)
    primary, actual = [json.loads(PINS[key][0].read_bytes()) for key in ("primary", "actual")]
    assert actual["status"] == "COMPLETED_CONDITIONAL_L14_P1_L25_RT0_BOARD_1MHZ"
    assert primary["rail_id"] == actual["rail_id"] == "ADC_VDD_075_VTRIP_SRAM/0"
    assert primary["reference_port_one_based"] == 18 and actual["frequency_hz"] == 1e6
    reference_row, = [row for row in primary["points"] if row["frequency_hz"] == 1e6]
    assert actual["saved_p1_board_zdd_ohm"] == reference_row["two_sheet_zdd_ohm"]
    point = actual["point"]
    assert all(point["category_passivity"].values()) and point["physical_checks"][-1]["kcl_max_a"] < 1e-7
    z = complex(*point["zdd_ohm"])
    assert sha(RUN / "field.npz") == actual["field"]["sha256"]
    raw_input = actual["source_l14_inputs"]["raw"]
    assert sha(Path(raw_input["path"])) == raw_input["sha256"]
    with np.load(raw_input["path"], allow_pickle=False) as raw, np.load(RUN / "field.npz", allow_pickle=False) as field:
        batch, = raw["batch_port_indices"]
        p, n = raw["global_to_active_indices"][raw["solve_port_reduced_nodes"][int(batch)]]
        field_z = complex(field["active_voltage_v"][p]-field["active_voltage_v"][n])
    assert abs(field_z-z) < 1e-15
    reference = complex(*reference_row["reference_zdd_ohm"])
    old = complex(*reference_row["two_sheet_zdd_ohm"])
    native = complex(*reference_row["native_zdd_ohm"])
    before, after = errors(old, reference), errors(z, reference)
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_CONDITIONAL_RT0_BOARD_1MHZ_COMPARISON", "rail_id": actual["rail_id"], "frequency_hz": 1e6, "reference_port_one_based": 18, "inputs": {key: {"path": str(path), "sha256": digest} for key, (path, digest) in PINS.items()}, "driver_sha256": sha(Path(__file__)), "zdd_ohm": {"rt0_board": point["zdd_ohm"], "p1_board": reference_row["two_sheet_zdd_ohm"], "reference": reference_row["reference_zdd_ohm"], "native": reference_row["native_zdd_ohm"]}, "errors": {"rt0_vs_reference": after, "p1_vs_reference": before, "native_vs_reference": errors(native, reference)}, "remaining_p1_reference_gap_reduction_fraction": 1-after["complex_gap_ohm"]/before["complex_gap_ohm"], "actual_field_z_reproduction_ohm": abs(field_z-z), "device_active_indices": [int(p), int(n)], "limitations": ["One development point, conditional changed L25 mesh/RT0/P0 G/C basis while L14 and other native physics remain fixed. Not an isolated magnetic change or a converged-board claim.", "The10.99298% single DC-pair bracket does not bound this AC Device response; no dense-band or holdout result. Actual-field independent review remains separate."]}
    output = DOCS / "astra_rt0_board_1mhz_comparison_2026-09-07.json"
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"status": result["status"], "errors": result["errors"], "p1_gap_reduction_fraction": result["remaining_p1_reference_gap_reduction_fraction"], "comparison_sha256": sha(output)}))


if __name__ == "__main__":
    main()
