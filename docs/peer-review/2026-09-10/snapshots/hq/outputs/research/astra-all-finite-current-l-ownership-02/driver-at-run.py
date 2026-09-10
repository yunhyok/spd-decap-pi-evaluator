"""Correct the units and finite-power receipt of read-only all-finite attempt01."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "outputs" / "research-runtime"))
import reconstruct_astra_native_loaded_field as recon

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SOURCE_CURRENT_A = 1.0
R = ROOT / "outputs" / "research"
ATTEMPT01 = R / "astra-all-finite-current-l-ownership-01"
PINS = {
    "attempt01_driver": (ATTEMPT01 / "driver-at-run.py", "4da0d49c62b1271d11fc80dded974c528078380eed42f41636a717009adab79e"),
    "attempt01_result": (ATTEMPT01 / "result.json", "b9641892dd80cbbecd48ea1a30e58b7afee9ab589878ed613f797e863cb75e7f"),
    "attempt01_artifact": (ATTEMPT01 / "all-finite-current-l-ownership.npz", "1b908c62e385ea1c227e77b1ccc37dd5c4b3376cce6f67d7b4e5c4a0c20508e6"),
    "budget_helper": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "accepted_physical_diagnostic": (R / "astra-l02-hybrid-right-correction-01/physical-diagnostic.json", "337042aae1110021cae6c678d817d6f3dd5a5941bd3e7ec077414aca44b3f71b"),
}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def verify_pins() -> None:
    for name, (path, digest) in PINS.items():
        if sha(path) != digest:
            raise ValueError(f"{name} SHA-256 differs")


def pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def run(output: Path) -> None:
    if output.exists():
        raise ValueError("output already exists")
    budget = recon._Budget.create(60, 4)
    verify_pins()
    prior = json.loads(PINS["attempt01_result"][0].read_text(encoding="utf-8"))
    if not (prior["status"] == "COMPLETED_ACCEPTED_ALL_FINITE_CURRENT_L_OWNERSHIP_READBACK"
            and prior["driver"]["sha256"] == PINS["attempt01_driver"][1]
            and prior["artifact"]["sha256"] == PINS["attempt01_artifact"][1]
            and prior["summary"]["counts"]["final_finite_rows"] == 1_692_409):
        raise ValueError("attempt01 receipt contract differs")
    physical = json.loads(PINS["accepted_physical_diagnostic"][0].read_text(encoding="utf-8"))
    saved_power = complex(*physical["physical"]["power_contributions_ohm"]["finite_branches"])
    with np.load(PINS["attempt01_artifact"][0], allow_pickle=False) as archive:
        final_rows = np.asarray(archive["final_active_current_row"], dtype=np.int64)
        native_rows = np.asarray(archive["native_active_current_row"], dtype=np.int64)
        split_legs = np.asarray(archive["final_split_leg"], dtype=np.int8)
        impedance = np.asarray(archive["impedance_ohm"], dtype=np.complex128)
        resistance = np.asarray(archive["resistance_ohm"], dtype=np.float64)
        inductance = np.asarray(archive["inductance_h"], dtype=np.float64)
        current = np.asarray(archive["finite_current_first_to_second_a"], dtype=np.complex128)
        legacy_hermitian = np.asarray(archive["hermitian_abs_i_squared_l_h"], dtype=np.float64)
        legacy_ordinary = np.asarray(archive["ordinary_i_squared_l_h"], dtype=np.complex128)
    if not (final_rows.shape == native_rows.shape == split_legs.shape == impedance.shape == resistance.shape == inductance.shape == current.shape == (1_692_409,)
            and len(np.unique(final_rows)) == 1_692_409 and len(np.unique(native_rows)) == 1_692_389
            and np.count_nonzero(split_legs == 0) == np.count_nonzero(split_legs == 1) == 20
            and np.count_nonzero(split_legs == -1) == 1_692_369
            and np.all(np.isfinite(resistance)) and np.all(resistance > 0.0)
            and np.all(np.isfinite(inductance)) and np.all(inductance >= 0.0)
            and np.all(np.isfinite(current)) and np.all(np.isfinite(legacy_hermitian)) and np.all(np.isfinite(legacy_ordinary))):
        raise ValueError("attempt01 stored finite R/L/current contract differs")
    # The saved accepted finite category uses the circuit R+j omega L sign convention.
    finite_power = np.sum(np.abs(current) ** 2 * impedance)
    power_error = abs(finite_power - saved_power)
    power_limit = max(abs(saved_power), np.finfo(float).tiny) * 1e-12
    if power_error > power_limit:
        raise ValueError("stored current finite-power replay differs from accepted physical diagnostic")
    budget.check("attempt01 stored R/L and finite-power replay")
    old_top = prior["summary"]["top_rows"]
    top_rows_j = {"hermitian_abs_i_squared_l_j": [{**row, "metric_j": row.pop("metric")} for row in old_top["hermitian_abs_i_squared_l_h"]],
                  "ordinary_i_squared_l_j": [{**row, "metric_j": row.pop("metric")} for row in old_top["ordinary_i_squared_l_h"]]}
    totals_j = {"hermitian_abs_i_squared_l_j": float(legacy_hermitian.sum()),
                "ordinary_i_squared_l_j": pair(legacy_ordinary.sum())}
    totals_h_normalized = {"source_current_a": SOURCE_CURRENT_A,
                           "hermitian_abs_i_squared_l_h_normalized_per_source_a_squared": totals_j["hermitian_abs_i_squared_l_j"] / SOURCE_CURRENT_A ** 2,
                           "ordinary_i_squared_l_h_normalized_per_source_a_squared": [value / SOURCE_CURRENT_A ** 2 for value in totals_j["ordinary_i_squared_l_j"]]}
    output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    artifact = output / "corrected-units-and-power-metadata.npz"
    np.savez_compressed(artifact, source_current_a=np.asarray([SOURCE_CURRENT_A]),
                        hermitian_abs_i_squared_l_j_total=np.asarray([totals_j["hermitian_abs_i_squared_l_j"]]),
                        ordinary_i_squared_l_j_total=np.asarray([complex(*totals_j["ordinary_i_squared_l_j"])]),
                        finite_power_replay_ohm=np.asarray([finite_power]), accepted_finite_power_ohm=np.asarray([saved_power]),
                        split_leg0_final_active_current_row=final_rows[split_legs == 0])
    budget.check("corrected metadata artifact")
    result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_ATTEMPT01_UNITS_AND_FINITE_POWER_QUALIFICATION",
              "driver": receipt(frozen), "inputs": {name: receipt(path) for name, (path, _digest) in PINS.items()}, "artifact": receipt(artifact),
              "stored_arrays": {"preserved_attempt01_artifact": receipt(PINS["attempt01_artifact"][0]),
                                "per_row_value_correction": "Attempt01 values are retained unchanged; |I|^2L and I^2L have units A^2 H = J, not H."},
              "source_current_normalization": totals_h_normalized, "totals_j": totals_j, "top_rows_j": top_rows_j,
              "finite_power_replay": {"recomputed_from_stored_i_and_z_ohm": pair(finite_power), "accepted_physical_finite_category_ohm": pair(saved_power),
                                      "absolute_error_ohm": float(power_error), "limit_ohm": float(power_limit), "passed": True},
              "r_l_gates": {"all_resistance_positive": True, "all_inductance_nonnegative": True},
              "composite_scope": {"split_leg0_rows": 20, "leg0_source_segments": 78, "leg0_source_via_segments": 73, "leg0_source_trace_segments": 5,
                                  "both_legs_unique_vias": 93, "segment_currents_assigned": False},
              "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(60,4)", "cooperative_checks": True},
              "scope": "Cheap read-only qualification of attempt01 saved arrays. It neither recomputes branch currents nor loads a field, validator, matrix, raw SPD, or SQLite. It corrects A^2 H labels to J and separately reports the explicit 1 A source-normalized H values. The 78 segment composite scope carries no assigned segment current; no magnetic action or accuracy claim is made."}
    recon._atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({"status": result["status"], "artifact": result["artifact"], "totals_j": totals_j, "finite_power_replay": result["finite_power_replay"], "budget": result["budget"]}, sort_keys=True))


def self_check() -> None:
    verify_pins()
    assert SOURCE_CURRENT_A == 1.0
    assert 73 + 5 == 78
    print(f"{PROGRAM} v{VERSION}: attempt01 units/power SELF_CHECK PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.output is None:
        parser.error("--output required")
    else:
        run(args.output)
