"""One existing PowerSI reference point; never fit the source model."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from spd_decap_pi._core.io.touchstone import read_touchstone, s_to_z, validate_port_manifest

SOURCE = Path(r"D:\S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p")
EXPECTED = "c5fca21da6f3b1f1e097ac6fc4c2c4f40a44201617502a9e5bf864e2a5fe7a11"
RAIL = "ADC_VDD_180_VQPS_SYS_1_AON/0"
OUTPUT = ROOT / "docs/evaluation-research/astra_powersi_1mhz_reference_2026-09-07.json"


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    started = monotonic()
    assert SOURCE.stat().st_size == 303974090
    with SOURCE.open("rb") as stream:
        assert sha256(stream.read()).hexdigest() == EXPECTED
    network = read_touchstone(SOURCE)
    validate_port_manifest(network, {RAIL: 44}, require_complete_header=True)
    indices = np.flatnonzero(network.frequencies_hz == 1e6)
    assert len(indices) == 1, "exact 1 MHz reference point is required"
    index = int(indices[0])
    selected = replace(network, frequencies_hz=network.frequencies_hz[index:index + 1],
                       s_parameters=network.s_parameters[index:index + 1])
    converted = s_to_z(selected)
    z = complex(converted.z_parameters[0, 43, 43])
    assert np.isfinite(z) and z.real >= 0
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "ACCEPT_EXISTING_REFERENCE_POINT_ONLY", "source_path": str(SOURCE),
        "source_sha256": EXPECTED, "rail_id": RAIL, "port_one_based": 44,
        "port_header": network.port_mapping[44], "frequency_hz": 1e6,
        "source_frequency_index": index, "source_record_count": len(network.frequencies_hz),
        "reference_ohm": network.reference_ohm, "full_matrix_ports": 92,
        "reference_zdd_ohm": [z.real, z.imag],
        "s_to_z_condition": float(converted.condition_numbers[0]),
        "s_to_z_relative_residual": float(converted.relative_residuals[0]),
        "elapsed_s": monotonic() - started,
        "scope": "Existing source-mounted PowerSI export; full 92-port S-to-Z at one exact frequency, all other port currents zero. No interpolation, fitting, new PowerSI run, source-SPD scan or candidate solve. A deliberately unloaded candidate is a different board state and must be labelled accordingly.",
    }
    with OUTPUT.open("x", encoding="utf-8") as out:
        json.dump(result, out, indent=2, allow_nan=False)
        out.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
