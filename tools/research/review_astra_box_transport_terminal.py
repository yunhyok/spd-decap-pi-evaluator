"""SPD Decap PI Evaluator v0.23.1: saved 74-current terminal review.

Reconstruct saved algebra, charge, work, and 72-to-74 changes without solving
or integrating any Green kernel.
"""
from pathlib import Path
import argparse
import hashlib
import json

import numpy as np
from numpy.polynomial.legendre import leggauss


ROOT = Path(__file__).resolve().parents[2]
EPS0 = 8.8541878128e-12
MU0 = 4e-7 * np.pi
PINS = {
    "producer": ("tools/research/diagnose_astra_box_transport_terminal.py", "20150fcc43e4a29a9228951b1ad0eed964b0edb62918bef0d9cbe4ecd6e43fd3"),
    "driver": ("outputs/research/astra-box-transport-terminal-01/driver-at-run.py", "20150fcc43e4a29a9228951b1ad0eed964b0edb62918bef0d9cbe4ecd6e43fd3"),
    "result": ("outputs/research/astra-box-transport-terminal-01/result.json", "5d918e896a95ef2019fe71d8521a59229d23c00acf8e79800b2cec71391fc124"),
    "fields": ("outputs/research/astra-box-transport-terminal-01/fields.npz", "efe1aa21c869dd8367ffbac66671f84c4d0619b5e444dc736c83503906671385"),
    "space": ("outputs/research/astra-box-transport-current-space-01/space.npz", "ecba005f8b8edfa81913f54396b6d23ebc2348878bebdee84d52fb4fadbe9d35"),
    "cross_result": ("outputs/research/astra-box-transport-current-cross-01/result.json", "63dcf75f44286f6a387f5343310be863c9765b1967606679057608568ec2e1bd"),
    "cross_q10": ("outputs/research/astra-box-transport-current-cross-01/cross-q10.npz", "4d0067fc2a9d692ef28541496d683579397c4ad24065094d42181b24be88a05a"),
    "cross_q14": ("outputs/research/astra-box-transport-current-cross-01/cross-q14.npz", "e7188a5a4e989f864be95d41b04b6cc3afd418d4d4fd583a43cc18ad0fda5352"),
    "baseline_result": ("outputs/research/astra-box-potential-terminal-full-green-01/result.json", "9a3ab8025980d1948d0cf4e6b199fc606812da562a682ff63c6b4175a0b83699"),
    "baseline_fields": ("outputs/research/astra-box-potential-terminal-full-green-01/fields.npz", "11c8904ba937fdf7afc5d141c2418e26dbd3e5142efe251c40f871b65b878a39"),
    "kernels": ("outputs/research/astra-refined-3d-box-kernels-01/kernels.npz", "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02"),
    "source": ("outputs/research/astra-native-frequency-stamps-01/source-frequency-inputs.json", "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(actual, expected):
    return float(np.linalg.norm(actual - expected) / max(float(np.linalg.norm(expected)), np.finfo(float).tiny))


def run(output):
    assert not output.exists()
    for path, expected in PINS.values():
        assert sha(ROOT / path) == expected, path
    result = json.loads((ROOT / PINS["result"][0]).read_bytes())
    baseline = json.loads((ROOT / PINS["baseline_result"][0]).read_bytes())
    source = json.loads((ROOT / PINS["source"][0]).read_bytes())
    assert result["status"] == "PASS_TRANSPORT_TERMINAL_DIAGNOSTIC"
    assert result["script_sha256"] == PINS["producer"][1] and result["fields_sha256"] == PINS["fields"][1]
    assert result["cross_result_sha256"] == PINS["cross_result"][1]
    assert result["program"] == "SPD Decap PI Evaluator" and result["version"] == "0.23.1"
    assert baseline["status"] == "PASS_FULL_GREEN_POTENTIAL_TERMINAL_DIAGNOSTIC"

    copper = source["stackup_layers"][0]
    assert copper["name"] == "Signal$TOP"
    sigma = float(copper["conductivity_s_m"])
    eps = EPS0 * float(copper["dk"]) * (1 - 1j * float(copper["df"]))
    case_lookup = {(float(case["frequency_hz"]), int(case["cross_order"])): case for case in result["cases"]}
    assert len(case_lookup) == 12
    drives = np.array([[1, 0, 0.5], [0, 1, -0.5]], complex)
    _, polar_weights = leggauss(20)
    sphere_weights = np.repeat(polar_weights, 48) * 2 * np.pi / 48

    maxima = {name: 0.0 for name in (
        "old_system_block_relative", "charge_new_coupling_absolute", "rhs_relative", "current_block_reciprocity_relative",
        "componentwise_backward", "modal_relative", "contact_charge_relative", "noncontact_charge_relative",
        "potential_relative", "terminal_current_relative", "continuity_relative", "contact_potential_relative",
        "admittance_reciprocity_relative", "differential_current_balance_relative", "impedance_relative",
        "absorption_relative", "terminal_power_relative", "radiation_relative", "power_closure_report_absolute",
        "omitted_residual_relative", "omitted_residual_report_absolute", "change_report_absolute",
        "q10_q14_current_report_absolute", "q10_q14_admittance_report_absolute"
    )}
    reconstructed_comparisons = []

    with np.load(ROOT / PINS["fields"][0], allow_pickle=False) as saved, np.load(
        ROOT / PINS["baseline_fields"][0], allow_pickle=False
    ) as old, np.load(ROOT / PINS["kernels"][0], allow_pickle=False) as kernel:
        mass = saved["mass"]
        bn = saved["noncontact_divergence"]
        bc = saved["contact_divergence"]
        contact = saved["contact_faces"].astype(int)
        noncontact = saved["noncontact_faces"].astype(int)
        vc = saved["terminal_potentials"]
        frequencies = kernel["frequencies_hz"]
        static_scalar = kernel["static_scalar_per_m"][48:, 48:]
        scalar_tail = kernel["scalar_tail_per_m"][:, 48:, 48:]
        assert mass.shape == (74, 74) and bn.shape == (32, 74) and bc.shape == (16, 74)
        assert np.count_nonzero(bn[:, 72:]) == 0 and np.count_nonzero(bc[:, 72:]) == 0
        assert np.array_equal(bn[:, :72], old["noncontact_divergence"])
        assert np.array_equal(bc[:, :72], old["contact_divergence"])
        assert np.array_equal(vc, old["terminal_potentials"])
        old_indices = np.r_[0:72, 74:90]
        charge_scale = EPS0 * 1e-4

        for fi, frequency in enumerate(frequencies):
            omega = 2 * np.pi * frequency
            k = omega * np.sqrt(MU0 * EPS0)
            gamma = sigma + 1j * omega * eps
            ratio = 1 - 1j * omega * EPS0 / gamma
            p = (static_scalar + scalar_tail[fi] - 1j * k) / (4 * np.pi * EPS0)
            pnn = p[np.ix_(noncontact, noncontact)]
            pnc = p[np.ix_(noncontact, contact)]
            pcn = p[np.ix_(contact, noncontact)]
            pcc = p[np.ix_(contact, contact)]
            old_current = old[f"case_{fi:02d}_current"]
            old_reference = np.vstack((old_current, np.zeros((2, 2), complex)))

            per_order = {}
            for order in (10, 14):
                case = case_lookup[(float(frequency), order)]
                prefix = f"q{order}_{fi:02d}_"
                system = saved[prefix + "system"]
                rhs = saved[prefix + "rhs"]
                solution = saved[prefix + "scaled_solution"]
                scale = saved[prefix + "current_scale"]
                modal = saved[prefix + "current"]
                qc = saved[prefix + "contact_charge"]
                qn = saved[prefix + "noncontact_charge"]
                phi = saved[prefix + "potential"]
                y = saved[prefix + "terminal_current"]
                far = saved[prefix + "transverse_body_far"]
                reported_residual = saved[prefix + "omitted_test_residual"]

                maxima["old_system_block_relative"] = max(maxima["old_system_block_relative"], relative(system[np.ix_(old_indices, old_indices)], old[f"case_{fi:02d}_system"]))
                maxima["charge_new_coupling_absolute"] = max(maxima["charge_new_coupling_absolute"], float(np.max(abs(system[74:, 72:74]))), float(np.max(abs(system[72:74, 74:]))))
                expected_rhs = np.zeros((90, 2), complex)
                expected_rhs[old_indices] = old[f"case_{fi:02d}_rhs"]
                maxima["rhs_relative"] = max(maxima["rhs_relative"], relative(rhs, expected_rhs))
                physical_current_block = system[:74, :74] / scale[None, :]
                maxima["current_block_reciprocity_relative"] = max(maxima["current_block_reciprocity_relative"], relative(physical_current_block, physical_current_block.T))
                residual = system @ solution - rhs
                backward = float(np.max(abs(residual) / np.maximum(abs(system) @ abs(solution) + abs(rhs), np.finfo(float).tiny)))
                maxima["componentwise_backward"] = max(maxima["componentwise_backward"], backward)

                rebuilt_modal = scale[:, None] * solution[:74]
                rebuilt_qc = charge_scale * solution[74:]
                rebuilt_qn = 1j * ratio * bn @ rebuilt_modal / omega
                charge = np.zeros((120, 2), complex)
                charge[contact] = rebuilt_qc
                charge[noncontact] = rebuilt_qn
                rebuilt_phi = p @ charge
                rebuilt_y = vc.T @ bc @ rebuilt_modal
                maxima["modal_relative"] = max(maxima["modal_relative"], relative(modal, rebuilt_modal))
                maxima["contact_charge_relative"] = max(maxima["contact_charge_relative"], relative(qc, rebuilt_qc))
                maxima["noncontact_charge_relative"] = max(maxima["noncontact_charge_relative"], relative(qn, rebuilt_qn))
                maxima["potential_relative"] = max(maxima["potential_relative"], relative(phi, rebuilt_phi))
                maxima["terminal_current_relative"] = max(maxima["terminal_current_relative"], relative(y, rebuilt_y))
                continuity = ratio * bn @ modal + 1j * omega * qn
                continuity_scale = abs(ratio * bn) @ abs(modal) + omega * abs(qn)
                maxima["continuity_relative"] = max(maxima["continuity_relative"], float(np.max(abs(continuity) / np.maximum(continuity_scale, np.finfo(float).tiny))))
                contact_error = relative(phi[contact], vc)
                reciprocity = relative(y, y.T)
                maxima["contact_potential_relative"] = max(maxima["contact_potential_relative"], contact_error)
                maxima["admittance_reciprocity_relative"] = max(maxima["admittance_reciprocity_relative"], reciprocity)

                differential = y @ drives[:, 2]
                balance = float(abs(differential.sum()) / np.linalg.norm(differential))
                impedance = 1 / ((differential[0] - differential[1]) / 2)
                reported_impedance = complex(*case["differential_impedance_ohm"])
                maxima["differential_current_balance_relative"] = max(maxima["differential_current_balance_relative"], balance)
                maxima["impedance_relative"] = max(maxima["impedance_relative"], abs(impedance - reported_impedance) / abs(reported_impedance))

                driven = modal @ drives
                absorption = np.real(1 / gamma) * np.real(np.diag(driven.conj().T @ mass @ driven))
                terminal_power = np.real(np.sum(drives.conj() * (y @ drives), axis=0))
                radiation = omega * MU0 * k / (16 * np.pi * np.pi) * np.einsum("n,ndj,ndj->j", sphere_weights, far.conj(), far).real
                maxima["absorption_relative"] = max(maxima["absorption_relative"], relative(absorption, case["absorption_w"]))
                maxima["terminal_power_relative"] = max(maxima["terminal_power_relative"], relative(terminal_power, case["terminal_input_power_w"]))
                maxima["radiation_relative"] = max(maxima["radiation_relative"], relative(radiation, case["radiation_from_body_current_w"]))
                closure = (terminal_power - absorption - radiation) / (abs(terminal_power) + abs(absorption) + radiation)
                maxima["power_closure_report_absolute"] = max(maxima["power_closure_report_absolute"], float(np.max(abs(closure - case["port_minus_loss_body_radiation_relative"]))))

                omitted = physical_current_block[72:74, :72] @ old_current @ drives
                maxima["omitted_residual_relative"] = max(maxima["omitted_residual_relative"], relative(reported_residual, omitted))
                denominator = abs(1 / gamma) * np.sqrt(np.real(np.diag((old_reference @ drives).conj().T @ mass @ (old_reference @ drives))))
                omitted_metric = np.linalg.norm(omitted / np.sqrt(mass.diagonal()[72:74])[:, None], axis=0) / denominator
                maxima["omitted_residual_report_absolute"] = max(maxima["omitted_residual_report_absolute"], float(np.max(abs(omitted_metric - case["omitted_new_test_residual_mass_dual_over_resistive_field"]))))
                delta = (modal - old_reference) @ drives
                change = np.sqrt(np.real(np.diag(delta.conj().T @ mass @ delta)) / np.real(np.diag(driven.conj().T @ mass @ driven)))
                maxima["change_report_absolute"] = max(maxima["change_report_absolute"], float(np.max(abs(change - case["current_mass_change_vs72_relative_new"]))))
                per_order[order] = (modal, y)

            difference = per_order[10][0] - per_order[14][0]
            current_change = float(np.sqrt(np.real(np.trace(difference.conj().T @ mass @ difference)) / np.real(np.trace(per_order[14][0].conj().T @ mass @ per_order[14][0]))))
            admittance_change = relative(per_order[10][1], per_order[14][1])
            reported = result["quadrature_comparisons"][fi]
            maxima["q10_q14_current_report_absolute"] = max(maxima["q10_q14_current_report_absolute"], abs(current_change - reported["current_mass_q10_q14_relative"]))
            maxima["q10_q14_admittance_report_absolute"] = max(maxima["q10_q14_admittance_report_absolute"], abs(admittance_change - reported["admittance_q10_q14_relative"]))
            reconstructed_comparisons.append({"frequency_hz": float(frequency), "current_mass_q10_q14_relative": current_change, "admittance_q10_q14_relative": admittance_change})

    gates = {
        "saved_linear_system": maxima["old_system_block_relative"] < 2e-14 and maxima["charge_new_coupling_absolute"] == 0 and maxima["rhs_relative"] < 2e-14,
        "reciprocity_and_equations": maxima["current_block_reciprocity_relative"] < 2e-12 and maxima["componentwise_backward"] < 1e-12,
        "current_and_charge": max(maxima[key] for key in ("modal_relative", "contact_charge_relative", "noncontact_charge_relative", "potential_relative", "terminal_current_relative")) < 2e-14,
        "continuity_and_contacts": maxima["continuity_relative"] < 2e-12 and maxima["contact_potential_relative"] < 2e-12,
        "terminal_observables": maxima["admittance_reciprocity_relative"] < 2e-12 and maxima["differential_current_balance_relative"] < 2e-12 and maxima["impedance_relative"] < 2e-14,
        "work": max(maxima[key] for key in ("absorption_relative", "terminal_power_relative", "radiation_relative", "power_closure_report_absolute")) < 2e-12,
        "omitted_test_and_change": maxima["omitted_residual_relative"] < 1e-9 and max(
            maxima[key] for key in ("omitted_residual_report_absolute", "change_report_absolute")
        ) < 2e-12,
        "quadrature_comparison": maxima["q10_q14_current_report_absolute"] < 2e-14 and maxima["q10_q14_admittance_report_absolute"] < 2e-14,
    }
    gates = {name: bool(value) for name, value in gates.items()}
    assert all(gates.values()), {"gates": gates, "maxima": maxima}
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_74_CURRENT_TRANSPORT_TERMINAL_ALGEBRA_WORK_WITH_SCOPE",
        "reviewer_sha256": sha(Path(__file__)),
        "pins": PINS,
        "gates": gates,
        "maxima": maxima,
        "quadrature_comparisons": reconstructed_comparisons,
        "scope": (
            "Read-only saved-array review. It reconstructs the scaled system residual, old72 embedded block, independent contact/noncontact charges, "
            "terminal current, impedance, absorption, body-current radiation, omitted-new-test residual, 72-to-74 current change, and q10/q14 changes. "
            "It does not integrate a Green kernel or solve a linear system. The body-only far field excludes the ideal external circuit. "
            "The 72-to-74 change and omitted-test residual demonstrate basis relevance, not spatial convergence, a physical return, multilayer source closure, board Z, or PowerSI accuracy."
        ),
    }
    output.mkdir(parents=True)
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": review["status"], "gates": gates, "maxima": maxima}, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
