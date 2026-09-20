"""Compare four actual mounted points; retain a constant-R algebraic shadow."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from probe_astra_rank_one_branch_update import _rank_one

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/evaluation-research"
OUTPUT = DOCS / "astra_native_frequency_comparison_2026-09-07.json"


def pair(z):
    return [float(z.real), float(z.imag)]


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    names = {
        "one": "astra_native_device_branch_2port_2026-09-07.json",
        "high": "astra_native_device_branch_high_frequency_2026-09-07.json",
        "ref_one": "astra_powersi_1mhz_reference_2026-09-07.json",
        "ref_high": "astra_powersi_followup_reference_2026-09-07.json",
        "trace": "astra_native_trace_comparison_2026-09-07-02.json",
        "pair_contract": r"D:\SPD-Decap-PI-Evaluator-W6\fb36288781dcc0b884950ef5a486c474090ceebd\260729\run_manifest.json",
    }
    docs, inputs = {}, {}
    for name, filename in names.items():
        path = DOCS / filename
        content = path.read_bytes()
        docs[name] = json.loads(content)
        inputs[name] = {"path": str(path), "sha256": sha256(content).hexdigest()}
    one, high = docs["one"], docs["high"]
    assert docs["trace"]["inputs"]["native"]["sha256"] == inputs["one"]["sha256"]
    assert high["identities"]["base_driver_sha256"] == one["identities"]["driver_code_sha256"]
    assert one["status"] == "COMPLETED_ACTUAL_NATIVE_TWO_PORT_DIAGNOSTIC"
    assert high["status"] in ("COMPLETED_ACTUAL_MOUNTED_THREE_FREQUENCY_DIAGNOSTIC", "PARTIAL_ACTUAL_MOUNTED_THREE_FREQUENCY_DIAGNOSTIC")
    for key in ("rail_id", "version"):
        assert one[key] == high[key]
    for key in ("source_sha256", "scenario_sha256", "design_fingerprint", "manifest_sha256"):
        assert one["bundle"][key] == high["bundle"][key], key
    for key in ("solver_profile", "probe_port_manifest_sha256", "source_model_evidence_sha256", "base_substrate_identity_sha256", "loaded_scenario_identity_sha256", "termination_manifest_sha256"):
        assert one["identities"][key] == high["identities"][key], key
    joint = high["trace_via_joint_incidence"]
    assert joint["source_mounted_termination_cluster_count"] == 0
    assert joint["native_via_link_ids_after_scenario_binding"] == one["trace_via_joint_incidence"]["native_via_link_ids_after_scenario_binding"]
    assert high["ports"]["manifest"] == one["ports"]["probe_port_manifest"]
    branch = one["ports"]["branch"]
    for key in ("owner_id", "link_id", "resistance_ohm", "inductance_h"):
        assert branch[key] == high["ports"]["branch_" + key], key
    for ref in (docs["ref_one"], docs["ref_high"]):
        assert ref["rail_id"] == one["rail_id"] and ref["port_one_based"] == 44
    assert docs["ref_one"]["source_sha256"] == docs["ref_high"]["source_sha256"]
    assert inputs["pair_contract"]["sha256"] == "2b14f90e762abc49833518137812e8fc97fcde0e9c145795b7384210cfd9f5de"
    paired_inputs = docs["pair_contract"]["evidence"]["inputs"]
    # The SPD and Touchstone have distinct hashes; this manifest pins their supplied pairing.
    assert one["bundle"]["source_sha256"] == paired_inputs["source"]["sha256"]
    assert docs["ref_one"]["source_sha256"] == paired_inputs["touchstone"]["sha256"]
    rtrace = docs["trace"]["states"]["source_mounted_scenario_manifest"]["cases"]["refined_dc"]["trace_resistance_ohm"]
    refs = {1e6: docs["ref_one"]["reference_zdd_ohm"]}
    refs.update({point["frequency_hz"]: point["reference_zdd_ohm"] for point in docs["ref_high"]["points"]})
    points = {1e6: {"status": "COMPLETED_ACTUAL_MOUNTED_POINT", "response": one["states"]["source_mounted_scenario_manifest"]}}
    points.update({point["frequency_hz"]: point for point in high["points"].values()})
    assert set(points) == set(refs) == {1e6, 1e7, 1e8, 1e9}
    rows = []
    for frequency, point in sorted(points.items()):
        if point["status"] != "COMPLETED_ACTUAL_MOUNTED_POINT":
            rows.append({"frequency_hz": frequency, "status": point["status"], "error": point["error"]})
            continue
        state = point["response"]
        z = np.array([[complex(*value) for value in row] for row in state["z_ohm"]])
        reference = complex(*refs[frequency])
        assert np.all(np.isfinite(z)) and z[0, 0].real >= 0
        assert state["minimum_hermitian_eigenvalue_ohm"] >= 0
        assert np.min(np.linalg.eigvalsh((z + z.conj().T) / 2)) >= 0
        zv = branch["resistance_ohm"] + 2j * np.pi * frequency * branch["inductance_h"]
        current = z[1, 0] / zv
        assert branch["resistance_ohm"] * abs(current) ** 2 <= z[0, 0].real * (1 + 1e-9)
        updated, dy, denominator = _rank_one(z, zv, rtrace)
        delta = -dy * z[0, 1] * z[1, 0] / denominator
        zero, zero_dy, zero_den = _rank_one(z, zv, 0)
        assert zero == z[0, 0] and zero_dy == 0 and zero_den == 1
        y2 = np.linalg.inv(z)
        y2[1, 1] += dy
        assert abs(np.linalg.inv(y2)[0, 0] - updated) <= 1e-9 * abs(updated)
        native_y, ref_y = 1 / z[0, 0], 1 / reference
        rows.append({
            "frequency_hz": frequency, "status": "COMPARED_ACTUAL_MOUNTED_POINT",
            "native_zdd_ohm": pair(z[0, 0]), "reference_zdd_ohm": pair(reference),
            "magnitude_error_db": float(20 * np.log10(abs(z[0, 0] / reference))),
            "complex_relative_error": float(abs(z[0, 0] - reference) / abs(reference)),
            "complex_gap_ohm": float(abs(z[0, 0] - reference)),
            "native_phase_deg": float(np.angle(z[0, 0], deg=True)),
            "reference_phase_deg": float(np.angle(reference, deg=True)),
            "phase_error_deg": float(np.angle(z[0, 0] / reference, deg=True)),
            "native_admittance_s": pair(native_y), "reference_admittance_s": pair(ref_y),
            "native_susceptance_over_omega_pf": float(native_y.imag / (2 * np.pi * frequency) * 1e12),
            "reference_susceptance_over_omega_pf": float(ref_y.imag / (2 * np.pi * frequency) * 1e12),
            "branch_current_per_device_amp": pair(current),
            "constant_R_algebraic_shadow": {
                "added_resistance_ohm": rtrace, "delta_zdd_ohm": pair(delta),
                "absolute_delta_ohm": float(abs(delta)),
                "delta_to_existing_gap_ratio": float(abs(delta) / abs(z[0, 0] - reference)),
            },
            "cancellation_ratio": state["polarization_cancellation_ratio"],
            "solver_diagnostics": state["diagnostics"],
        })
    result = {
        "program": one["program"], "version": one["version"], "inputs": inputs,
        "comparison_code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "status": "COMPLETED_SPARSE_NATIVE_ERROR_DIAGNOSTIC" if all(row["status"] == "COMPARED_ACTUAL_MOUNTED_POINT" for row in rows) else "PARTIAL_SPARSE_NATIVE_ERROR_DIAGNOSTIC",
        "rail_id": one["rail_id"], "state": "source_mounted_scenario_manifest", "points": rows,
        "limitations": [
            "Four diagnostic points on one rail are not broadband or unseen validation and do not locate a resonance precisely.",
            "Susceptance divided by omega is an equivalent response indicator, may be negative for an inductive response, and is not a source material/capacitance parameter.",
            "The constant-R shadow at high frequency is algebra only: it does not represent skin/proximity effects, external or mutual L, distributed return paths or actual AC trace physics.",
            "Cancellation and solver residual/pivot diagnostics are retained; a tiny residual is not a forward error guarantee.",
            "No PowerSI fitting or native physical parameter change occurred.",
            "The historical run manifest is used only to bind the supplied SPD/Touchstone pair, not to claim that its old candidate solver is the current native model.",
        ],
    }
    with OUTPUT.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "output": str(OUTPUT)}))


if __name__ == "__main__":
    main()
