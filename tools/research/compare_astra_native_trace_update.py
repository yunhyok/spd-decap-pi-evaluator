"""Apply the verified trace branch update to an actual native 2-port receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from probe_astra_rank_one_branch_update import _rank_one

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/evaluation-research"


def packed(z: complex) -> list[float]:
    return [float(z.real), float(z.imag)]


def run(native_path: Path) -> dict:
    paths = {
        "native": native_path,
        "trace": DOCS / "astra_dyadic_trace_sheet_2026-09-07.json",
        "bounds": DOCS / "astra_trace_dc_bounds_2026-09-06.json",
        "reference": DOCS / "astra_powersi_1mhz_reference_2026-09-07.json",
        "patch": DOCS / "astra_isolated_trace_patch_2026-09-06.json",
        "basis": ROOT / "outputs/research/astra-step4-basis-01/ownership-basis.json",
    }
    docs, inputs = {}, {}
    for name, path in paths.items():
        data = path.read_bytes()
        docs[name] = json.loads(data)
        inputs[name] = {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest()}
    native, trace, bounds, reference = (docs[name] for name in ("native", "trace", "bounds", "reference"))
    patch, basis = docs["patch"], docs["basis"]
    if native["status"] != "COMPLETED_ACTUAL_NATIVE_TWO_PORT_DIAGNOSTIC":
        raise ValueError("actual native diagnostic did not complete")
    if native["frequency_hz"] != reference["frequency_hz"] or native["rail_id"] != reference["rail_id"]:
        raise ValueError("native/reference frequency or rail mismatch")
    if trace["input_sha256"] != bounds["input_sha256"]:
        raise ValueError("trace/bound source patch mismatch")
    if inputs["patch"]["sha256"] != trace["input_sha256"]:
        raise ValueError("trace patch hash mismatch")
    if native["bundle"]["source_sha256"] != basis["meta"]["source_sha256"]:
        raise ValueError("native/source ownership basis mismatch")
    if Path(native["bundle"]["path"]).resolve() != Path(basis["bundle"]["path"]).resolve() or native["bundle"]["size_bytes"] != basis["bundle"]["size_bytes"]:
        raise ValueError("native bundle differs from the verified ownership bundle")
    if native["version"] != basis["version"] or native["rail_id"] != patch["source_trace"]["trace"]["net_name"]:
        raise ValueError("native version/trace net mismatch")
    branch = native["ports"]["branch"]
    if branch["owner_id"] != "via:via336274":
        raise ValueError("unexpected native branch owner")
    owner = next(item for item in patch["native_owner_refs"] if item["owner_id"] == branch["owner_id"])
    link = next(item for item in patch["native_links"] if item["ordinal"] == owner["link_ordinal"])
    if any(branch[key] != link[key] for key in ("link_id", "resistance_ohm", "inductance_h")):
        raise ValueError("native branch differs from the verified trace boundary")
    joint = native["trace_via_joint_incidence"]
    if joint["node_id"] != patch["native_degree_two_node"]["node_id"] or not joint["no_source_mounted_termination_attaches_at_joint"]:
        raise ValueError("loaded trace joint has an unverified/additional termination")
    if joint["native_via_link_ids_after_scenario_binding"] != sorted(item["link_id"] for item in patch["native_links"]):
        raise ValueError("loaded trace joint is no longer the proven degree-two boundary")
    zv = branch["resistance_ohm"] + 2j * np.pi * native["frequency_hz"] * branch["inductance_h"]
    r = next(row["r_ohm"] for row in trace["sheet_rows"] if row["circle_sides"] == 128 and row["level"] == 3)
    if not bounds["r_lower_ohm"] <= r <= bounds["r_upper_ohm"]:
        raise ValueError("trace resistance outside declared DC bounds")
    zref = complex(*reference["reference_zdd_ohm"])
    states = {}
    for name, state in native["states"].items():
        z = np.asarray([[complex(*item) for item in row] for row in state["z_ohm"]])
        if z[0, 0].real < 0 or branch["resistance_ohm"] <= 0:
            raise ValueError("passive branch power bound requires positive native R and nonnegative Re(Zdd)")
        if state["minimum_hermitian_eigenvalue_ohm"] < 0 or np.min(np.linalg.eigvalsh((z + z.conj().T) / 2)) < 0:
            raise ValueError("recorded/recomputed two-port passivity check failed")
        branch_power = float(branch["resistance_ohm"] * abs(z[1, 0] / zv) ** 2)
        if branch_power > z[0, 0].real * (1 + 1e-9):
            raise ValueError("native branch power exceeds total Device real input power")
        current_squared_ceiling = float(z[0, 0].real / branch["resistance_ohm"])
        zero, dy0, den0 = _rank_one(z, zv, 0.0)
        assert zero == z[0, 0] and dy0 == 0 and den0 == 1, "R=0 must recover baseline"
        cases = {}
        for label, resistance in (("dc_lower", bounds["r_lower_ohm"]), ("refined_dc", r), ("dc_upper", bounds["r_upper_ohm"])):
            updated, dy, denominator = _rank_one(z, zv, resistance)
            delta = -dy * z[0, 1] * z[1, 0] / denominator
            energy_delta_ceiling = float(abs(dy) * abs(zv) ** 2 * current_squared_ceiling / abs(denominator))
            assert abs(delta) <= energy_delta_ceiling * (1 + 1e-9), "transfer violates passive native branch power bound"
            # An independent reduced 2-port admittance update, not a second board solve.
            y = np.linalg.inv(z)
            y[1, 1] += dy
            inverse_updated = np.linalg.inv(y)[0, 0]
            agreement = float(abs(inverse_updated - updated) / max(abs(updated), 1e-30))
            assert agreement < 1e-9, "reduced admittance inversion disagrees"
            case = {
                "trace_resistance_ohm": resistance,
                "updated_zdd_ohm": packed(updated),
                "delta_zdd_ohm": packed(delta),
                "absolute_delta_ohm": float(abs(delta)),
                "relative_complex_change": float(abs(delta) / abs(z[0, 0])),
                "magnitude_change_db": float(20 * np.log10(abs(updated) / abs(z[0, 0]))),
                "delta_y_s": packed(dy),
                "denominator": packed(denominator),
                "reduced_inverse_relative_agreement": agreement,
                "passive_branch_energy_delta_ceiling_ohm": energy_delta_ceiling,
            }
            if name == "source_mounted_scenario_manifest":
                case["reference_comparison"] = {
                    "baseline_signed_magnitude_error_db": float(20 * np.log10(abs(z[0, 0]) / abs(zref))),
                    "updated_signed_magnitude_error_db": float(20 * np.log10(abs(updated) / abs(zref))),
                    "baseline_relative_complex_error": float(abs(z[0, 0] - zref) / abs(zref)),
                    "updated_relative_complex_error": float(abs(updated - zref) / abs(zref)),
                    "delta_to_existing_complex_error_ratio": float(abs(delta) / max(abs(z[0, 0] - zref), 1e-30)),
                    "energy_ceiling_to_existing_complex_error_ratio": float(energy_delta_ceiling / max(abs(z[0, 0] - zref), 1e-30)),
                }
            cases[label] = case
        states[name] = {
            "baseline_zdd_ohm": packed(z[0, 0]),
            "native_branch_current_per_device_amp": packed(z[1, 0] / zv),
            "native_branch_power_ohm_at_device_1A": branch_power,
            "native_branch_energy_and_two_port_passivity_checks": True,
            "passive_branch_current_squared_ceiling": current_squared_ceiling,
            "d_zdd_d_trace_resistance_at_zero": packed((z[0, 1] / zv) ** 2),
            "polarization_cancellation_ratio": state["polarization_cancellation_ratio"],
            "zero_resistance_check": True,
            "cases": cases,
        }
    return {
        "program": native["program"], "version": native["version"],
        "status": "COMPLETED_CONDITIONAL_ACTUAL_BOARD_TRACE_SHADOW",
        "inputs": inputs, "frequency_hz": native["frequency_hz"], "rail_id": native["rail_id"],
        "native_identities": native["identities"], "native_branch": branch,
        "new_trace_owner": bounds["new_trace_owner"], "states": states,
        "energy_bound_basis": "For 1 A Device excitation in the passive reciprocal native circuit, Rv*|Iv|^2 <= Re(Zdd) and Iv=Zbd/Zv. Therefore |delta Zdd| <= |delta Y|*|Zv|^2*Re(Zdd)/(Rv*|1+delta Y*Zbb|). This conditional circuit bound uses self responses, not subtractive transfer extraction; it is not a forward numerical error bound or a bound on unmodeled AC physics.",
        "limitations": [
            "Conditional DC trace resistance inserted in series with the verified native Via336274 branch; native G/C and other via R/L are unchanged.",
            "This is an algebraic shadow of the actual native response, not a full-board physical promotion or a new full-matrix recomposition.",
            "DC resistance bounds are not ordered bounds on complex Device impedance or on actual AC conductor physics.",
            "Polarization cancellation ratio is a diagnostic, not an error bound; tiny updates require adequate transfer accuracy.",
            "Only the source-mounted state is compared with the existing source-mounted PowerSI export, at one exact frequency.",
            "No fitting, broadband accuracy acceptance, installer or product change.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, default=DOCS / "astra_native_device_branch_2port_2026-09-07.json")
    parser.add_argument("--output", type=Path, default=DOCS / "astra_native_trace_comparison_2026-09-07.json")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite an existing receipt")
    result = run(args.native)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    print(f"{result['program']} v{result['version']}: {result['status']} -> {args.output}")
