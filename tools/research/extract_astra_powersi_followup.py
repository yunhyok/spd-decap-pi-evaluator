"""Exact existing reference points for the mounted native follow-up."""
from dataclasses import replace
from hashlib import sha256
import json
from time import monotonic

import numpy as np

from extract_astra_powersi_1mhz import (
    EXPECTED, RAIL, ROOT, SOURCE, read_touchstone, s_to_z, validate_port_manifest,
)

OUTPUT = ROOT / "docs/evaluation-research/astra_powersi_followup_reference_2026-09-07.json"
FREQUENCIES = (1e7, 1e8, 1e9)


if __name__ == "__main__":
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    started = monotonic()
    assert SOURCE.stat().st_size == 303974090
    with SOURCE.open("rb") as stream:
        assert sha256(stream.read()).hexdigest() == EXPECTED
    network = read_touchstone(SOURCE)
    validate_port_manifest(network, {RAIL: 44}, require_complete_header=True)
    indices = []
    for frequency in FREQUENCIES:
        found = np.flatnonzero(network.frequencies_hz == frequency)
        assert len(found) == 1, f"exact reference point absent: {frequency} Hz"
        indices.append(int(found[0]))
    selected = replace(network, frequencies_hz=network.frequencies_hz[indices],
                       s_parameters=network.s_parameters[indices])
    converted = s_to_z(selected)
    points = []
    for position, index in enumerate(indices):
        z = complex(converted.z_parameters[position, 43, 43])
        assert np.isfinite(z) and z.real >= 0
        points.append({
            "frequency_hz": float(network.frequencies_hz[index]),
            "source_frequency_index": index, "reference_zdd_ohm": [z.real, z.imag],
            "s_to_z_condition": float(converted.condition_numbers[position]),
            "s_to_z_relative_residual": float(converted.relative_residuals[position]),
        })
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "ACCEPT_EXISTING_REFERENCE_POINTS_ONLY", "source_path": str(SOURCE),
        "source_sha256": EXPECTED, "rail_id": RAIL, "port_one_based": 44,
        "port_header": network.port_mapping[44], "reference_ohm": network.reference_ohm,
        "full_matrix_ports": 92, "points": points, "elapsed_s": monotonic() - started,
        "scope": "Existing source-mounted PowerSI export; full92-port S-to-Z at three exact frequencies with other port currents zero. No interpolation, fitting, new PowerSI run, raw SPD scan or candidate solve.",
    }
    with OUTPUT.open("x", encoding="utf-8") as out:
        json.dump(result, out, indent=2, allow_nan=False)
        out.write("\n")
    print(json.dumps(result))
