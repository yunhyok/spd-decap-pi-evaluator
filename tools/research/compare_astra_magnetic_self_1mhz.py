"""Post-freeze comparison of the finite same-triangle self-L ablation."""
import json
from pathlib import Path
import numpy as np
from compare_astra_rt0_board_1mhz import sha
from compare_astra_loaded_development_points import errors

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs/research/astra-l25-self-magnetic-board-1mhz-01"
PRIMARY = ROOT / "docs/evaluation-research/astra_primary_band_comparison_2026-09-07.json"
PINS = {RUN / "result.json": "36d293fa18b32f88a195d71a20934fe678e1b3cec49eed6cdd39f2cb0ce687ab",
        PRIMARY: "ac08d7ea36a09989fb83e9113af399db41f698bf68e061e3c75020f4c41a5f8d",
        Path(__file__).with_name("compare_astra_loaded_development_points.py"): "0e12464d1297caa9964404a2a0777d22964d6817069f752641b2d7945c8f4f4f",
        Path(__file__).with_name("compare_astra_rt0_board_1mhz.py"): "c98cafb9d5cb1f5f6ed2623c9b33c5d83e231c3bfd2a5a7b5f823c79d1c8faba"}


def main():
    for path, digest in PINS.items():
        assert sha(path) == digest, path
    actual, primary = json.loads((RUN / "result.json").read_bytes()), json.loads(PRIMARY.read_bytes())
    assert actual["status"] == "COMPLETED_CONDITIONAL_L25_SAME_TRIANGLE_SELF_L_FINITE_1MHZ"
    assert actual["rail_id"] == primary["rail_id"] == "ADC_VDD_075_VTRIP_SRAM/0"
    assert actual["frequency_hz"] == 1e6 and primary["reference_port_one_based"] == 18
    row, = [point for point in primary["points"] if point["frequency_hz"] == 1e6]
    reference = complex(*row["reference_zdd_ohm"])
    raw_meta = actual["source"]["source_l14_inputs"]["raw"]
    assert sha(Path(raw_meta["path"])) == raw_meta["sha256"]
    with np.load(raw_meta["path"], allow_pickle=False) as raw:
        batch, = raw["batch_port_indices"]
        p, n = raw["global_to_active_indices"][raw["solve_port_reduced_nodes"][int(batch)]]
    rows = []
    for point in actual["points"]:
        field = RUN / point["field"]["path"]
        assert sha(field) == point["field"]["sha256"]
        assert all(point["point"]["category_passivity"].values())
        with np.load(field, allow_pickle=False) as archive:
            z = complex(archive["active_voltage_v"][p]-archive["active_voltage_v"][n])
        assert abs(z-complex(*point["point"]["zdd_ohm"])) < 1e-15
        rows.append({"alpha": point["alpha"], "zdd_ohm": point["point"]["zdd_ohm"], "errors": errors(z, reference)})
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
              "status": "COMPLETED_CONDITIONAL_SELF_MAGNETIC_1MHZ_COMPARISON",
              "script_sha256": sha(Path(__file__)), "inputs": {str(p): digest for p, digest in PINS.items()},
              "rail_id": actual["rail_id"], "frequency_hz": 1e6, "reference_port_one_based": 18,
              "reference_zdd_ohm": row["reference_zdd_ohm"], "device_active_indices": [int(p), int(n)], "points": rows,
              "remaining_rt0_reference_gap_reduction_fraction": 1-rows[1]["errors"]["complex_gap_ohm"]/rows[0]["errors"]["complex_gap_ohm"],
              "scope": "Reference read only after actual results were frozen; no fitted coefficient. Same geometry/RT0/G/C/contacts/native-via basis. Alpha1 adds only exact same-triangle sheet partial L and solves fresh currents/voltages. Intertriangle/interlayer mutual and magnetic return composition remain absent, so this is a conditional numerical ablation, not complete magnetic physics, mesh convergence, broadband or product accuracy acceptance."}
    output = ROOT / "docs/evaluation-research/astra_self_magnetic_1mhz_comparison_2026-09-07.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"points": rows, "remaining_gap_reduction_fraction": result["remaining_rt0_reference_gap_reduction_fraction"], "comparison_sha256": sha(output)}))


if __name__ == "__main__":
    main()
