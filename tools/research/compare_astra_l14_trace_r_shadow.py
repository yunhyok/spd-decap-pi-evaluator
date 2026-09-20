#!/usr/bin/env python3
"""Compare the completed conditional L14 trace-R shadow to the pinned 1 MHz reference."""

from __future__ import annotations

import argparse
import cmath
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FILES = {
    "shadow": (
        ROOT / "outputs/research/astra-native-loaded-vtrip-trace-r-shadow-02/result.json",
        "69ec5706966f892c0dbd0e7888532fd07b02255004207d58b35bec7dbc12e00c",
    ),
    "reference": (
        ROOT / "docs/evaluation-research/astra_loaded_development_reference_2026-09-07.json",
        "761d61334678ceaca0db55bc8c5539bb080ee36363eae33a2c419a1ea5c06430",
    ),
    "baseline_comparison": (
        ROOT / "docs/evaluation-research/astra_loaded_development_comparison_2026-09-07.json",
        "3c3574ca191afb54f928a3851fbdfbaa7e512434544ca4d464a2a14b84212bac",
    ),
}


def pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def metrics(value: complex, reference: complex) -> dict[str, Any]:
    gap = abs(value - reference)
    return {
        "zdd_ohm": pair(value),
        "magnitude_ohm": abs(value),
        "magnitude_error_db": 20.0 * math.log10(abs(value) / abs(reference)),
        "complex_gap_ohm": gap,
        "complex_relative_error": gap / abs(reference),
        "phase_error_deg": math.degrees(cmath.phase(value / reference)),
    }


def main(output: Path) -> None:
    if output.exists():
        raise FileExistsError(output)
    documents: dict[str, Any] = {}
    inputs: dict[str, Any] = {}
    for name, (path, expected) in FILES.items():
        data = path.read_bytes()
        actual = sha256(data).hexdigest()
        if actual != expected:
            raise ValueError(f"input hash mismatch: {path.name}")
        documents[name] = json.loads(data)
        inputs[name] = {
            "path": str(path),
            "sha256": actual,
            "size_bytes": len(data),
        }
    shadow = documents["shadow"]
    reference_doc = documents["reference"]
    baseline_doc = documents["baseline_comparison"]
    if (
        shadow["status"] != "COMPLETED_CONDITIONAL_L14_TRACE_R_SHADOW"
        or shadow["frequency_hz"] != 1.0e6
        or reference_doc["rail_id"] != "ADC_VDD_075_VTRIP_SRAM/0"
        or reference_doc["port_one_based"] != 18
        or baseline_doc["rail_id"] != reference_doc["rail_id"]
    ):
        raise ValueError("rail, state, or frequency contract differs")
    reference_point = next(
        row for row in reference_doc["points"] if row["frequency_hz"] == 1.0e6
    )
    baseline_point = next(
        row for row in baseline_doc["points"] if row["frequency_hz"] == 1.0e6
    )
    epsilon_one = next(
        row
        for row in shadow["points"]
        if row["epsilon_trace_resistance_scale"] == 1.0
    )
    reference = complex(*reference_point["reference_zdd_ohm"])
    baseline = complex(*shadow["baseline"]["collapsed_zdd_ohm"])
    shadow_zdd = complex(*epsilon_one["zdd_ohm"])
    if abs(baseline - complex(*baseline_point["native_zdd_ohm"])) > 1.0e-18:
        raise ValueError("shadow baseline differs from the pinned native comparison")
    baseline_metrics = metrics(baseline, reference)
    shadow_metrics = metrics(shadow_zdd, reference)
    delta = shadow_zdd - baseline
    gap_reduction = (
        baseline_metrics["complex_gap_ohm"] - shadow_metrics["complex_gap_ohm"]
    )
    result = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "COMPLETED_CONDITIONAL_L14_TRACE_R_SHADOW_COMPARISON",
        "rail_id": "ADC_VDD_075_VTRIP_SRAM/0",
        "frequency_hz": 1.0e6,
        "reference_port_one_based": 18,
        "inputs": inputs,
        "comparison_code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "reference_zdd_ohm": pair(reference),
        "baseline": baseline_metrics,
        "conditional_trace_r_shadow": shadow_metrics,
        "conditional_change": {
            "delta_zdd_ohm": pair(delta),
            "delta_zdd_abs_ohm": abs(delta),
            "magnitude_error_improvement_db": (
                shadow_metrics["magnitude_error_db"]
                - baseline_metrics["magnitude_error_db"]
            ),
            "complex_gap_reduction_ohm": gap_reduction,
            "complex_gap_reduction_fraction_of_baseline_gap": (
                gap_reduction / baseline_metrics["complex_gap_ohm"]
            ),
            "complex_relative_error_change": (
                shadow_metrics["complex_relative_error"]
                - baseline_metrics["complex_relative_error"]
            ),
            "phase_error_change_deg": (
                shadow_metrics["phase_error_deg"]
                - baseline_metrics["phase_error_deg"]
            ),
        },
        "interpretation": (
            "The conditional L14 centerline DC trace-R shadow changes the 1 MHz "
            "response only slightly and does not account for the measured complex gap."
        ),
        "limitations": [
            "The reference was read only after the source-derived shadow result was frozen; no reference value set a model parameter.",
            "This is one development rail and one frequency, not a held-out or broadband accuracy result.",
            "The shadow omits distributed magnetic/return physics, skin effect, finite-width spreading, and non-ideal artwork-sheet resistance.",
        ],
    }
    encoded = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    temporary = output.with_name(f".{output.name}.tmp")
    with temporary.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
    try:
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print(result["status"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "docs/evaluation-research/astra_l14_trace_r_shadow_comparison_2026-09-07.json",
    )
    main(parser.parse_args().output.resolve())
