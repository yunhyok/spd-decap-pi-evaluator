"""Choose a source-populated non-holdout rail before extracting its reference."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np

from extract_astra_powersi_1mhz import EXPECTED, ROOT, SOURCE, read_touchstone, s_to_z, validate_port_manifest

DOCS = ROOT / "docs/evaluation-research"
SELECTION = DOCS / "astra_loaded_development_selection_2026-09-07.json"
REFERENCE = DOCS / "astra_loaded_development_reference_2026-09-07.json"
REPORT = Path(r"D:\SPD-Decap-PI-Evaluator-W6\fb36288781dcc0b884950ef5a486c474090ceebd\260729\correlation\correlation_report.json")


def write(path, result):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    if SELECTION.exists() or REFERENCE.exists():
        raise FileExistsError("selection/reference output already exists")
    native_path = DOCS / "astra_native_device_branch_2port_2026-09-07.json"
    native_bytes, report_bytes = native_path.read_bytes(), REPORT.read_bytes()
    assert sha256(report_bytes).hexdigest() == "969e40046e3a099d09557ba7500693460362336d76b067962436bd3b5177abc4"
    native, report = json.loads(native_bytes), json.loads(report_bytes)
    excluded = {rail for key, rails in report["score_split"].items() if "holdout" in key for rail in rails}
    counts = native["scenario_population"]["enabled_by_rail"]
    eligible = [rail for rail, count in counts.items() if count > 0 and rail not in excluded and rail in report["touchstone"]["manifest_92"]]
    rail = min(eligible, key=lambda name: (-counts[name], name.casefold()))
    assert rail == "ADC_VDD_075_VTRIP_SRAM/0" and counts[rail] == 421
    port = report["touchstone"]["manifest_92"][rail]
    assert port == 18 and rail not in excluded
    selection = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "PINNED_SOURCE_SELECTED_LOADED_DEVELOPMENT_CASE",
        "rail_id": rail, "source_enabled_decap_count": counts[rail], "port_one_based": port,
        "selection_rule": "Largest positive source-enabled decap count among represented non-holdout rails; casefold rail-id tie-break. No PowerSI error/response used to select.",
        "excluded_holdout_rails": sorted(excluded),
        "native_population_input": {"path": str(native_path), "sha256": sha256(native_bytes).hexdigest()},
        "split_and_port_manifest_input": {"path": str(REPORT), "sha256": sha256(report_bytes).hexdigest()},
        "source_spd_sha256": native["bundle"]["source_sha256"],
        "reference_touchstone_sha256": EXPECTED,
        "selection_written_before_target_reference_extraction": True,
        "scope": "New development case; does not alter frozen holdout membership or claim unseen validation.",
    }
    write(SELECTION, selection)
    started = monotonic()
    with SOURCE.open("rb") as stream:
        assert sha256(stream.read()).hexdigest() == EXPECTED
    network = read_touchstone(SOURCE)
    validate_port_manifest(network, {rail: port}, require_complete_header=True)
    frequencies = (1e6, 1e7, 1e8, 1e9)
    indices = []
    for frequency in frequencies:
        found = np.flatnonzero(network.frequencies_hz == frequency)
        assert len(found) == 1
        indices.append(int(found[0]))
    converted = s_to_z(replace(network, frequencies_hz=network.frequencies_hz[indices], s_parameters=network.s_parameters[indices]))
    points = []
    for position, index in enumerate(indices):
        z = complex(converted.z_parameters[position, port - 1, port - 1])
        assert np.isfinite(z) and z.real >= 0
        points.append({"frequency_hz": float(network.frequencies_hz[index]), "source_frequency_index": index,
                       "reference_zdd_ohm": [z.real, z.imag], "s_to_z_condition": float(converted.condition_numbers[position]),
                       "s_to_z_relative_residual": float(converted.relative_residuals[position])})
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "ACCEPT_EXISTING_LOADED_DEVELOPMENT_REFERENCE_POINTS_ONLY",
        "selection_sha256": sha256(SELECTION.read_bytes()).hexdigest(),
        "rail_id": rail, "port_one_based": port, "port_header": network.port_mapping[port],
        "source_path": str(SOURCE), "reference_touchstone_sha256": EXPECTED,
        "full_matrix_ports": 92, "reference_ohm": network.reference_ohm,
        "points": points, "elapsed_s": monotonic() - started,
        "scope": "Existing source-mounted reference at four exact frequencies, after source-only rail selection. No interpolation, fitting, native solve or holdout-score inspection.",
    }
    write(REFERENCE, result)
    print(json.dumps(result))
