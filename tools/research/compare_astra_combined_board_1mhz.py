"""Compare a frozen converged three-sheet R/G/C field with the existing R-only control."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from compare_astra_loaded_development_points import errors

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "docs/evaluation-research/astra_rt0_board_1mhz_comparison_2026-09-07.json"
CONTROL_SHA = "b3441baaa1549cff6d0b7eb9aa39f3a048ed6ca7efff71954e76481d41ca814f"
OPERATOR_SHA = "45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require_accepted(actual):
    solver_keys = {
        "COMPLETED_CONDITIONAL_L02_L14_L25_BLOCK_GMRES_1MHZ": "gmres",
        "COMPLETED_CONDITIONAL_L02_L14_L25_BLOCK_LGMRES_1MHZ": "lgmres",
    }
    key = solver_keys.get(actual["status"])
    if (key is None or actual[key]["info"] != 0 or actual["frequency_hz"] != 1e6
            or actual["inputs"]["operator"]["sha256"] != OPERATOR_SHA):
        raise ValueError("A frozen converged field of the pinned combined operator is required")
    p = actual["physical"]
    z = complex(*p["zdd_ohm"])
    if not (p["csc_kcl_max_abs_a"] < 1e-7
            and p["explicit_branch_kcl_max_abs_a"] < 1e-7
            and p["constitutive_max_abs_v"] < 1e-7
            and p["normalized_backward_residual"] <= 1e-9
            and p["power_closure_error_ohm"] <= abs(z)*1e-7
            and p["category_passivity"] and all(p["category_passivity"].values())):
        raise ValueError("The combined field did not pass its physical gates")


def main(args):
    if args.output.exists():
        raise FileExistsError(args.output)
    assert sha(args.result) == args.result_sha256
    actual = json.loads(args.result.read_bytes())
    require_accepted(actual)  # Reject failed fields before accessing reference values.
    assert sha(args.result.parent / "driver-at-run.py") == actual["driver"]["sha256"]
    field_path = Path(actual["field"]["path"])
    assert field_path.resolve() == (args.result.parent / "field.npz").resolve()
    assert sha(field_path) == actual["field"]["sha256"]
    assert sha(CONTROL) == CONTROL_SHA
    assert sha(Path(__file__).with_name("compare_astra_loaded_development_points.py")) == (
        "0e12464d1297caa9964404a2a0777d22964d6817069f752641b2d7945c8f4f4f")
    control = json.loads(CONTROL.read_bytes())
    assert control["status"] == "COMPLETED_CONDITIONAL_RT0_BOARD_1MHZ_COMPARISON"
    assert control["reference_port_one_based"] == 18 and control["frequency_hz"] == 1e6
    p, n = control["device_active_indices"]
    with np.load(field_path, allow_pickle=False) as field:
        v = field["active_voltage_v"]
        assert v.shape == (2340069,) and np.all(np.isfinite(v))
        assert np.array_equal(field["source_positive_negative_gauge_active_indices"], (p, n, 0))
        assert np.array_equal(field["source_current_amplitude_a"], (1.0,))
        assert field["combined_operator_sha256_utf8"].tobytes().decode() == OPERATOR_SHA
        z = complex(v[p]-v[n])
    assert abs(z-complex(*actual["physical"]["zdd_ohm"])) < 1e-15
    reference = complex(*control["zdd_ohm"]["reference"])
    baseline = complex(*control["zdd_ohm"]["rt0_board"])
    before, after = errors(baseline, reference), errors(z, reference)
    assert before == control["errors"]["rt0_vs_reference"]
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "COMPLETED_CONDITIONAL_COMBINED_R_GC_1MHZ_COMPARISON",
        "rail_id": control["rail_id"], "reference_port_one_based": 18,
        "device_active_indices": [p, n], "frequency_hz": 1e6,
        "inputs": {"result": {"path": str(args.result.resolve()), "sha256": args.result_sha256},
                   "control": {"path": str(CONTROL), "sha256": CONTROL_SHA}},
        "driver_sha256": sha(Path(__file__)), "field": actual["field"],
        "zdd_ohm": {"reference": [reference.real, reference.imag],
                    "l14_l25_r_gc": [baseline.real, baseline.imag],
                    "l02_l14_l25_r_gc": [z.real, z.imag]},
        "errors": {"l14_l25_r_gc": before, "l02_l14_l25_r_gc": after},
        "complex_relative_error_reduction_percentage_points": 100*(before["complex_relative_error"]-after["complex_relative_error"]),
        "remaining_reference_gap_reduction_fraction": 1-after["complex_gap_ohm"]/before["complex_gap_ohm"],
        "conditional_operator_change_abs_ohm": float(abs(z-baseline)),
        "scope": "Post-freeze comparison at one previously used development point. Adds the existing L02 P1 distributed R/G/C and its once-owned finite rewiring to L14 P1/L25 RT0. No fitted coefficient, new magnetic operator, independent holdout, mesh convergence or broadband acceptance. The earlier 23.7629% magnetic candidate is a different model, not the direct control.",
    }
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"result": result, "sha256": sha(args.output)}, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--result-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    assert errors(1+2j, 1+2j)["complex_relative_error"] == 0
    main(parser.parse_args())
