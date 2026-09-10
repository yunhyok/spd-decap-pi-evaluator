"""Compare the four native loaded development points with the pinned input pair."""
import cmath
from hashlib import sha256
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/evaluation-research"
RUN = ROOT / "outputs/research/astra-native-loaded-development-01"
OUTPUT = DOCS / "astra_loaded_development_comparison_2026-09-07.json"
RAIL = "ADC_VDD_075_VTRIP_SRAM/0"


def errors(native, reference):
    assert all(math.isfinite(v) for z in (native, reference) for v in (z.real, z.imag))
    assert native != 0 and reference != 0
    return {
        "magnitude_error_db": 20 * math.log10(abs(native / reference)),
        "complex_relative_error": abs(native - reference) / abs(reference),
        "complex_gap_ohm": abs(native - reference),
        "phase_error_deg": math.degrees(cmath.phase(native / reference)),
    }


def main():
    assert errors(1 + 2j, 1 + 2j) == dict.fromkeys(
        ("magnitude_error_db", "complex_relative_error", "complex_gap_ohm", "phase_error_deg"), 0
    )
    assert math.isclose(errors(2j, 1j)["magnitude_error_db"], 6.020599913279624)
    assert errors(-1j, 1j)["complex_relative_error"] == 2
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    paths = {
        "native": DOCS / "astra_native_loaded_development_rail_2026-09-07.json",
        "reference": DOCS / "astra_loaded_development_reference_2026-09-07.json",
        "selection": DOCS / "astra_loaded_development_selection_2026-09-07.json",
        "pair_contract": Path(r"D:\SPD-Decap-PI-Evaluator-W6\fb36288781dcc0b884950ef5a486c474090ceebd\260729\run_manifest.json"),
        "native_driver": ROOT / "tools/research/probe_astra_native_loaded_development_rail.py",
    }
    inputs, docs = {}, {}
    for key, path in paths.items():
        content = path.read_bytes()
        inputs[key] = {"path": str(path), "size_bytes": len(content), "sha256": sha256(content).hexdigest()}
        if key != "native_driver":
            docs[key] = json.loads(content)
    native, ref, selection = (docs[key] for key in ("native", "reference", "selection"))
    assert native["status"] == "COMPLETED_ACTUAL_SOURCE_MOUNTED_DEVELOPMENT_RAIL_FOUR_POINT"
    assert ref["status"] == "ACCEPT_EXISTING_LOADED_DEVELOPMENT_REFERENCE_POINTS_ONLY"
    assert native["rail_id"] == ref["rail_id"] == selection["rail_id"] == RAIL
    assert native["version"] == ref["version"] == selection["version"] == "0.23.1"
    assert native["state"] == "source_mounted_scenario_manifest"
    assert inputs["selection"]["sha256"] == "99d0f77fcceb9d07b97aa65953dd42b8d106b7f2485f42f11ee643134d64b8f9"
    assert ref["selection_sha256"] == inputs["selection"]["sha256"]
    assert native["selection"]["prior_selection_receipt"]["sha256"] == ref["selection_sha256"]
    assert native["selection"]["prior_selection_receipt"]["reference_port_one_based"] == ref["port_one_based"] == 18
    assert ref["port_header"] == "2nd_SITE0-" + RAIL
    assert RAIL not in selection["excluded_holdout_rails"]
    assert set(native["selection"]["all_excluded_holdout_rails"]) == set(selection["excluded_holdout_rails"])
    assert native["selection"]["selected_before_project_compile_or_response_solve"]
    assert selection["selection_written_before_target_reference_extraction"]
    assert native["selection"]["selected_enabled_source_mounted_count"] == 421
    assert native["scenario_population"]["enabled_source_mounted_by_rail"][RAIL] == 421
    assert native["scenario_population"]["enabled_source_mounted_count"] == native["termination_cluster_count"] == 11050
    assert native["identities"]["driver_code_sha256"] == inputs["native_driver"]["sha256"] == "3c1fd9c4cd2c3247e2b1f2ae41c78ed6310d25092a92e6b9751a7593e4fcdf52"
    assert native["identities"]["solver_profile"] == "layerwise_admittance_v1"
    assert native["bundle"]["load_validated_all_declared_member_hashes"]
    assert inputs["pair_contract"]["sha256"] == "2b14f90e762abc49833518137812e8fc97fcde0e9c145795b7384210cfd9f5de"
    paired = docs["pair_contract"]["evidence"]["inputs"]
    # These hashes identify different files; the historical manifest binds the supplied pair.
    assert native["bundle"]["source_sha256"] == selection["source_spd_sha256"] == paired["source"]["sha256"]
    assert ref["reference_touchstone_sha256"] == selection["reference_touchstone_sha256"] == paired["touchstone"]["sha256"]
    references = {int(point["frequency_hz"]): point for point in ref["points"]}
    assert set(references) == {1000000, 10000000, 100000000, 1000000000}
    assert set(map(int, native["points"])) == set(references)
    rows = []
    for frequency, reference_point in sorted(references.items()):
        point = native["points"][str(frequency)]
        checkpoint_path = RUN / f"frequency-{frequency}.json"
        checkpoint_bytes = checkpoint_path.read_bytes()
        checkpoint = json.loads(checkpoint_bytes)
        assert all(checkpoint[key] == value for key, value in point.items())
        for key in ("rail_id", "state", "bundle", "identities", "device_port", "selection", "termination_cluster_count"):
            assert checkpoint[key] == native[key], key
        assert point["status"] == "COMPLETED_ACTUAL_SOURCE_MOUNTED_DEVICE_POINT"
        assert point["frequency_hz"] == frequency
        z = complex(*point["device_zdd_ohm"])
        y = complex(*point["effective_admittance_s"])
        target = complex(*reference_point["reference_zdd_ohm"])
        assert z.real >= 0 and point["device_zdd_real_nonnegative"]
        assert abs(z * y - 1) < 1e-12
        assert math.isclose(abs(z), point["device_zdd_magnitude_ohm"], rel_tol=1e-12)
        rows.append({
            "frequency_hz": frequency,
            "native_zdd_ohm": point["device_zdd_ohm"],
            "reference_zdd_ohm": reference_point["reference_zdd_ohm"],
            **errors(z, target),
            "solver_diagnostics": point["diagnostics"],
            "reference_conversion_diagnostics": {key: reference_point[key] for key in ("s_to_z_condition", "s_to_z_relative_residual")},
            "checkpoint": {"path": str(checkpoint_path), "sha256": sha256(checkpoint_bytes).hexdigest()},
        })
    result = {
        "program": native["program"], "version": native["version"],
        "status": "COMPLETED_LOADED_DEVELOPMENT_FOUR_POINT_COMPARISON",
        "rail_id": RAIL, "source_enabled_decap_count": 421, "reference_port_one_based": 18,
        "state": native["state"], "inputs": inputs,
        "comparison_code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "points": rows,
        "limitations": [
            "One source-selected development rail at four fixed frequencies is not broadband accuracy or frozen holdout validation.",
            "This measures the existing native source-mounted model; no physical parameter was changed or fit to PowerSI.",
            "The historical manifest establishes only the supplied SPD and Touchstone pair, not equivalence to the old candidate solver.",
            "Native residual and pivot diagnostics are retained; a small backward residual is not a forward error guarantee.",
        ],
    }
    with OUTPUT.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "points": [{key: row[key] for key in ("frequency_hz", "magnitude_error_db", "complex_relative_error", "phase_error_deg")} for row in rows], "output": str(OUTPUT)}))


if __name__ == "__main__":
    main()
