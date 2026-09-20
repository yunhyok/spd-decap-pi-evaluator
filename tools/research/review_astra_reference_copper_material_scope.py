"""Review the frozen L04 uniform-copper 1D material control without rerunning it."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "outputs/research/astra-reference-copper-material-scope-01"
AUDIT = SOURCE / "audit.py"
RESULT = SOURCE / "result.json"
EXPECTED_AUDIT = "dd8a7c8d83ae0b8f7b28fea19937be14fa934cff4ae3219af2798ef767791634"
EXPECTED_RESULT = "0c1fa24325d54688a909bc6a1d5419ba895b9b346fb8ee51e2ed9a25290c9bf3"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def review() -> dict[str, object]:
    started = time.monotonic()
    require(digest(AUDIT) == EXPECTED_AUDIT, "audit hash")
    require(digest(RESULT) == EXPECTED_RESULT, "result hash")
    result = json.loads(RESULT.read_bytes())
    require(result["program"] == "SPD Decap PI Evaluator" and result["version"] == "0.23.1", "program/version")
    require(result["status"] == "PASS_SOURCE_COPPER_1D_MATERIAL_CONTROL", "producer status")
    require(result["script_sha256"] == EXPECTED_AUDIT, "result/audit binding")
    for name, item in result["inputs"].items():
        require(digest(Path(item["path"])) == item["sha256"], f"input hash: {name}")

    material = result["source_material"]
    sigma = float(material["conductivity_s_per_m"])
    thickness = float(material["thickness_um"]) * 1e-6
    mu = 4.0 * math.pi * 1e-7
    require(material["layer_name"] == "Signal$L04(DGND)" and material["material_name"] == "COPPER", "source material")
    require(sigma == 59_590_000.0 and material["thickness_um"] == 20.0 and result["permeability_assumption"] == "mu_r=1", "material values")
    rdc = 1.0 / (sigma * thickness)
    delta_equal = 1.0 / (math.pi * mu * sigma * thickness**2)
    require(math.isclose(result["rdc_ohm_per_square"], rdc, rel_tol=1e-15), "DC sheet resistance")
    require(math.isclose(result["delta_equals_thickness_hz"], delta_equal, rel_tol=1e-15), "delta=t frequency")

    frequencies = [1e3, 1e4, 1e5, 1e6, 1e7, 1e8]
    require([row["frequency_hz"] for row in result["points"]] == frequencies, "frequency points")
    for frequency, row in zip(frequencies, result["points"], strict=True):
        skin_depth_um = 1e6 / math.sqrt(math.pi * frequency * mu * sigma)
        require(math.isclose(row["skin_depth_um"], skin_depth_um, rel_tol=1e-15), "skin depth")
        require(row["common_mode_resistance_over_dc"] >= 1.0, "nonnegative AC resistance increase")
        require(0.0 <= row["transfer_over_self_magnitude"] <= 1.0, "transfer/self magnitude")
    controls = result["controls"]
    require(len(controls) == 24 and {row["frequency_hz"] for row in controls} == set(frequencies), "control coverage")
    maxima = {
        "boundary_voltage_relative_error": max(row["boundary_voltage_relative_error"] for row in controls),
        "current_relative_error": max(row["current_relative_error"] for row in controls),
        "power_relative_error": max(row["power_relative_error"] for row in controls),
    }
    require(max(maxima.values()) < 1e-9, "producer numerical control gate")

    return {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_WITH_SCOPE_CORRECTION_SOURCE_COPPER_1D_MATERIAL_REVIEW",
        "reviewer_sha256": digest(Path(__file__)),
        "reviewed": {"audit_sha256": EXPECTED_AUDIT, "result_sha256": EXPECTED_RESULT},
        "blocking_findings": [],
        "scope_corrections": [{
            "severity": "P2",
            "result_text": "Common mode is one face excitation",
            "correct_interpretation": "(Zself+Ztransfer)/2 is the impedance for the restricted symmetric split K0=K1=Ktotal/2. It is one two-face excitation mode, not a one-face excitation and not a replacement for the arbitrary two-face matrix.",
        }],
        "boundary_sign_review": {
            "status": "PASS",
            "derivation": "With exp(+jωt), E_x''=jωμσE_x and H_y=-E_x'/(jωμ). Outward surface currents are K0=H_y(0) at the lower face and K1=-H_y(t) at the upper face, so E'(0)=-jωμK0 and E'(t)=+jωμK1.",
        },
        "power_definition_review": {
            "status": "PASS",
            "identity": "K^H E_face = sigma integral |E|^2 dz + j omega mu integral |H|^2 dz",
            "normalization": "A peak/RMS factor is unnecessary for this equality because the same phasor normalization is used on both sides.",
        },
        "independence_review": {
            "status": "PASS_WITH_LIMIT",
            "evidence": "The control solves the two boundary derivatives for cosh/sinh coefficients with a direct 2x2 solve, then integrates current and complex power with 32-point volume quadrature. It does not reuse the coth/csch matrix to construct that field.",
            "limit": "The common-mode equality is a cross-implementation identity within the same material law, not separate experimental or board evidence.",
        },
        "numerical_summary": {
            **maxima,
            "dc_ohm_per_square": rdc,
            "one_mhz_transfer_over_self_magnitude": result["points"][3]["transfer_over_self_magnitude"],
            "hundred_mhz_common_resistance_over_dc": result["points"][5]["common_mode_resistance_over_dc"],
            "hundred_mhz_transfer_over_self_magnitude": result["points"][5]["transfer_over_self_magnitude"],
        },
        "scope": "Accepts the pinned uniform isotropic 20 um copper, mu_r=1, 1D finite-thickness material kernel control from 1 kHz through 100 MHz. It does not select common-mode-only physics, qualify lateral geometry or interfaces, include proximity/fringing/external or interlayer mutual/G-C, certify board or PowerSI accuracy, or permit shielding/omission decisions from skin-depth/thickness alone. Pytest was unavailable; this is a frozen direct-assert research control rather than regression-suite acceptance.",
        "elapsed_s": time.monotonic() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    value = review()
    with (output / "independent-review.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "numerical_summary": value["numerical_summary"]}))


if __name__ == "__main__":
    main()
