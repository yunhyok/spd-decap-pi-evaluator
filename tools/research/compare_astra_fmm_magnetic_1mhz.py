"""Compare a frozen, converged conditional L25 magnetic response at 1 MHz."""
import argparse
import json
from pathlib import Path
import numpy as np
from compare_astra_rt0_board_1mhz import sha
from compare_astra_loaded_development_points import errors

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "outputs/research/astra-l25-self-magnetic-board-1mhz-01/result.json"
PRIMARY = ROOT / "docs/evaluation-research/astra_primary_band_comparison_2026-09-07.json"
PINS = {CONTROL: "36d293fa18b32f88a195d71a20934fe678e1b3cec49eed6cdd39f2cb0ce687ab",
        PRIMARY: "ac08d7ea36a09989fb83e9113af399db41f698bf68e061e3c75020f4c41a5f8d",
        Path(__file__).with_name("compare_astra_loaded_development_points.py"): "0e12464d1297caa9964404a2a0777d22964d6817069f752641b2d7945c8f4f4f",
        Path(__file__).with_name("compare_astra_rt0_board_1mhz.py"): "c98cafb9d5cb1f5f6ed2623c9b33c5d83e231c3bfd2a5a7b5f823c79d1c8faba"}
CANDIDATE_PINS = {
    "script_sha256": "e88c3edc31b023cfcb8fd7046713bf8268824d3ecce0cad8805cb88366b25554",
    "operator_sha256": "2c880f428b18f40ee9fabca98154be9f7c7c6f6b91a6dfc74a2bb09810a8daa2",
    "self_receipt_sha256": "c930d29e7437c62aabc45efe58acbcfbde6fa2b9bd2646254711b1ea9a8dd7ed",
    "near_receipt_sha256": "6b4df2af404ec46fdd983efc5fc6e00a3fed4c8babf05a3eaa30cf239261f174",
    "board_driver_sha256": "edb0d82ff2b9af9b44259541cca0d2abdef7e52c4c1ebd29ba2c990e7d65ef98"}


def require_accepted(receipt):
    if any(receipt.get(key) != value for key, value in CANDIDATE_PINS.items()):
        raise ValueError("Candidate producer or magnetic/source basis differs")
    point = receipt["point"]
    if (receipt["status"] != "COMPLETED_CONDITIONAL_L25_FMM_MAGNETIC_FINITE_1MHZ"
            or point["gmres_info"] != 0 or not point["gates"]
            or not all(value is True for value in point["gates"].values())):
        raise ValueError("A frozen converged field passing all physical gates is required")


def main(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    assert sha(args.result) == args.result_sha256
    actual = json.loads(args.result.read_bytes())
    require_accepted(actual)  # Reject failed/recovered fields before reading reference data.
    assert sha(args.result.parent / "driver-at-run.py") == actual["script_sha256"]
    for path, digest in PINS.items():
        assert sha(path) == digest, path
    control, primary = [json.loads(path.read_bytes()) for path in (CONTROL, PRIMARY)]
    assert control["status"] == "COMPLETED_CONDITIONAL_L25_SAME_TRIANGLE_SELF_L_FINITE_1MHZ"
    assert actual["rail_id"] == control["rail_id"] == primary["rail_id"] == "ADC_VDD_075_VTRIP_SRAM/0"
    assert actual["frequency_hz"] == control["frequency_hz"] == 1e6
    assert primary["reference_port_one_based"] == 18
    for key in ("inputs", "source_l14_inputs", "dc_step_artifacts", "gc_receipt"):
        assert actual["source"][key] == control["source"][key], key
    raw_meta = actual["source"]["source_l14_inputs"]["raw"]
    assert sha(Path(raw_meta["path"])) == raw_meta["sha256"]
    with np.load(raw_meta["path"], allow_pickle=False) as raw:
        batch, = raw["batch_port_indices"]
        p, n = raw["global_to_active_indices"][raw["solve_port_reduced_nodes"][int(batch)]]
    reference_row, = [row for row in primary["points"] if row["frequency_hz"] == 1e6]
    reference = complex(*reference_row["reference_zdd_ohm"])
    candidates = [("rt0_r_only" if row["alpha"] == 0 else "same_triangle_self",
                   CONTROL.parent, row["field"], row["point"]) for row in control["points"]]
    assert [name for name, *_ in candidates] == ["rt0_r_only", "same_triangle_self"]
    candidates.append(("l25_full_centroid_self_shared_edge", args.result.parent, actual["field"], actual["point"]))
    rows = []
    for name, directory, meta, point in candidates:
        field = directory / meta["path"]
        assert sha(field) == meta["sha256"]
        assert all(point["category_passivity"].values())
        with np.load(field, allow_pickle=False) as archive:
            voltage = archive["active_voltage_v"]
            assert voltage.shape == (1483296,) and np.isfinite(voltage).all()
            z = complex(voltage[p]-voltage[n])
        assert abs(z-complex(*point["zdd_ohm"])) < 1e-15
        rows.append({"model": name, "field": {"path": str(field.resolve()), "sha256": meta["sha256"]},
                     "zdd_ohm": point["zdd_ohm"], "errors": errors(z, reference)})
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
              "status": "COMPLETED_CONDITIONAL_L25_FMM_MAGNETIC_1MHZ_COMPARISON",
              "script_sha256": sha(Path(__file__)),
              "inputs": {**{str(path): digest for path, digest in PINS.items()}, str(args.result.resolve()): args.result_sha256},
              "rail_id": actual["rail_id"], "frequency_hz": 1e6, "reference_port_one_based": 18,
              "reference_zdd_ohm": reference_row["reference_zdd_ohm"], "device_active_indices": [int(p), int(n)], "points": rows,
              "remaining_rt0_reference_gap_reduction_fraction": 1-rows[2]["errors"]["complex_gap_ohm"]/rows[0]["errors"]["complex_gap_ohm"],
              "scope": "Post-freeze comparison only; no reference-fitted coefficient. Same source/RT0/G/C/contacts/native-via basis. Full candidate includes L25 centroid mutual, exact triangle self and selected shared-edge correction. Other geometric near pairs, 415 shared-edge tails, cross-layer magnetic currents and complete return remain unqualified. A final-field three-point diagnostic is separate. This single development point is not quadrature/mesh convergence, global PSD, broadband, unseen or product accuracy acceptance."}
    payload = json.dumps(result, indent=2, allow_nan=False)+"\n"
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(payload)
    print(json.dumps({"status": result["status"], "points": rows, "comparison_sha256": sha(args.output)}, allow_nan=False))


def self_check():
    good = {**CANDIDATE_PINS, "status": "COMPLETED_CONDITIONAL_L25_FMM_MAGNETIC_FINITE_1MHZ",
            "point": {"gmres_info": 0, "gates": {"physical": True}}}
    require_accepted(good)
    for bad in ({**good, "status": "RECOVERED_UNVALIDATED_L25_FMM_FINITE_FIELD"},
                {**good, "operator_sha256": "0"*64},
                {**good, "point": {"gmres_info": None, "gates": {"physical": True}}},
                {**good, "point": {"gmres_info": 0, "gates": {"physical": False}}}):
        try:
            require_accepted(bad)
        except ValueError:
            continue
        raise AssertionError("Unvalidated candidate was accepted")
    assert errors(1+2j, 1+2j)["complex_relative_error"] == 0
    print("SPD Decap PI Evaluator v0.23.1: comparison acceptance SELF_CHECK PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--result-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif any(value is None for value in (args.result, args.result_sha256, args.output)):
        parser.error("--result, --result-sha256 and --output required")
    else:
        main(args)
