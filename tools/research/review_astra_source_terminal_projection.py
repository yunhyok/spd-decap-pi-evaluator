"""SPD Decap PI Evaluator v0.23.1: saved source projection review.

Check exact material maps, projected equations, terminal observables, and work
from frozen arrays.  No Green integration or linear solve.
"""
from hashlib import sha256
from math import pi
from pathlib import Path
import argparse
import json

import numpy as np
from numpy.polynomial.legendre import leggauss


ROOT = Path(__file__).resolve().parents[2]
EPS0 = 8.8541878128e-12
MU0 = 4e-7 * pi
PINS = {
    "producer": ("tools/research/diagnose_astra_source_terminal_projection.py", "aa4966545eb04d3ee894a7ba73bea4a5403d74708179a128b0717c78b3000f7e"),
    "driver": ("outputs/research/astra-source-terminal-projection-01/driver-at-run.py", "aa4966545eb04d3ee894a7ba73bea4a5403d74708179a128b0717c78b3000f7e"),
    "result": ("outputs/research/astra-source-terminal-projection-01/result.json", "84ad12c23b6b3301a1f0e1a0da8c2c66150beac0e390af6f26cd62e014cb6614"),
    "fields": ("outputs/research/astra-source-terminal-projection-01/fields.npz", "ef6f9663a0a287ae0d0c88aec2938b2504b7a1753faf1ee01ad100e2ee72a33a"),
    "base_fields": ("outputs/research/astra-source-potential-terminal-02/fields.npz", "33262a8b2fad33a718af80e7cf341f9611d65d5fa18e28447107528503b1544d"),
    "contacts": ("outputs/research/astra-source-potential-contacts-01/contacts.npz", "55294d51fdb4da642499e38e7e613a226d9fc5561d2159e73f38de1f853aa21a"),
}


def digest(path):
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def relative(actual, expected):
    return float(np.linalg.norm(np.asarray(actual) - np.asarray(expected)) / max(float(np.linalg.norm(expected)), np.finfo(float).tiny))


def run(output):
    for path, expected in PINS.values():
        assert digest(ROOT / path) == expected, path
    result = json.loads((ROOT / PINS["result"][0]).read_text(encoding="utf-8"))
    assert result["status"] == "COMPLETE_SOURCE_TERMINAL_INTERFACE_PROJECTION_DIAGNOSTIC"
    assert result["program"] == "SPD Decap PI Evaluator" and result["version"] == "0.23.1"
    cases = {(case["kernel_tag"], float(case["frequency_hz"])): case for case in result["cases"]}
    assert len(cases) == 12
    with np.load(ROOT / PINS["fields"][0], allow_pickle=False) as data:
        saved = {name: data[name] for name in data.files}
    with np.load(ROOT / PINS["base_fields"][0], allow_pickle=False) as data:
        base = {name: data[name] for name in data.files}
    with np.load(ROOT / PINS["contacts"][0], allow_pickle=False) as data:
        tetrahedra = data["tetrahedra_m"]
        mid = data["material_id"]
        distributional = data["distributional_face_divergence"]
    d = saved["real_total_map"]
    e = saved["real_abf_map"]
    ut = saved["total_current_transform"]
    t = base["real_current_transform"]
    assert d.shape == e.shape == (36, 32) and ut.shape == (72, 32)
    assert np.array_equal(d, np.rint(d)) and np.array_equal(e, np.rint(e))
    assert np.array_equal(t @ d, ut)
    abf = ut.reshape(18, 4, 32).copy()
    abf[mid != 1] = 0
    assert np.array_equal(t @ e, abf.reshape(72, 32))
    owner_count = np.count_nonzero(distributional, axis=1)
    assert np.count_nonzero((distributional @ ut)[owner_count == 2]) == 0
    assert np.count_nonzero(ut.reshape(18, 4, 32)[mid == 1, :, :8]) == 0
    assert np.count_nonzero(base["noncontact_divergence"] @ d[:, :9]) == 0
    assert np.count_nonzero(base["noncontact_divergence"] @ e[:, :9]) == 0

    volumes = np.abs(np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1])) / 6
    drives = np.array([[1, 0, 1], [0, 1, 1j]], complex)
    _, polar_weights = leggauss(20)
    sphere_weights = np.repeat(polar_weights, 48) * 2 * pi / 48
    maxima = {name: 0.0 for name in (
        "trial_map_relative", "projected_operator_relative", "projected_rhs_relative", "scaled_backward",
        "lifted_current_relative", "physical_residual_relative", "projected_residual_relative", "continuity_relative",
        "terminal_voltage_relative", "terminal_current_relative", "admittance_relative", "z_change_report_absolute",
        "abf_e_change_report_absolute", "admittance_reciprocity_report_absolute", "absorption_report_relative",
        "terminal_power_report_relative", "radiation_report_relative", "power_closure_report_absolute",
    )}
    summaries = {method: {"max_z_change_relative": 0.0, "max_abf_e_change_relative": 0.0, "max_power_closure_relative": 0.0} for method in ("hermitian", "bilinear")}
    frequencies = np.array([1e3, 1e4, 1e5, 1e6, 1e7, 1e8])
    for fi, frequency in enumerate(frequencies):
        omega = 2 * pi * frequency
        k = omega * np.sqrt(MU0 * EPS0)
        old_prefix = f"v20f36_{fi:02d}_"
        gamma = base[old_prefix + "cell_gamma"]
        gcu = gamma[mid == 0][0]
        gabf = gamma[mid == 1][0]
        r = 1 - 1j * omega * EPS0 / gcu
        dr = 1j * omega * EPS0 * (gabf - gcu) / (gcu * gabf)
        expected_c = r * d + dr * e
        old_columns = np.r_[base[old_prefix + "current_scale"], base[old_prefix + "charge_scale"], np.ones(2)]
        old_operator = base[old_prefix + "system"] / old_columns[None, :]
        old_rhs = base[old_prefix + "rhs"]
        for method in ("hermitian", "bilinear"):
            prefix = f"{method}_{fi:02d}_"
            case = cases[(method, float(frequency))]
            trial = saved[prefix + "trial_map"]
            expected_trial = np.zeros((46, 42), complex)
            expected_trial[:36, :32] = expected_c
            expected_trial[36:, 32:] = np.eye(10)
            maxima["trial_map_relative"] = max(maxima["trial_map_relative"], relative(trial, expected_trial))
            test = trial.conj().T if method == "hermitian" else trial.T
            projected_operator = test @ old_operator @ trial
            projected_rhs = test @ old_rhs
            full_scale = saved[prefix + "full_scale"]
            system = saved[prefix + "system"]
            rhs = saved[prefix + "rhs"]
            maxima["projected_operator_relative"] = max(maxima["projected_operator_relative"], relative(system / full_scale[None, :], projected_operator))
            maxima["projected_rhs_relative"] = max(maxima["projected_rhs_relative"], relative(rhs, projected_rhs))
            solution = saved[prefix + "scaled_solution"]
            residual = system @ solution - rhs
            maxima["scaled_backward"] = max(maxima["scaled_backward"], float(np.max(abs(residual) / np.maximum(abs(system) @ abs(solution) + abs(rhs), np.finfo(float).tiny))))
            unknown = full_scale[:, None] * solution
            lifted = trial @ unknown
            maxima["lifted_current_relative"] = max(maxima["lifted_current_relative"], relative(lifted[:36], saved[prefix + "current"]))
            physical_residual = old_operator @ lifted - old_rhs
            maxima["physical_residual_relative"] = max(maxima["physical_residual_relative"], relative(physical_residual, saved[prefix + "physical_old_equation_residual"]))
            projected_residual = test @ physical_residual
            projected_scale = abs(test) @ (abs(old_operator) @ abs(lifted) + abs(old_rhs))
            maxima["projected_residual_relative"] = max(maxima["projected_residual_relative"], float(np.max(abs(projected_residual) / np.maximum(projected_scale, np.finfo(float).tiny))))

            modal = lifted[:36]
            qn = 1j * base["noncontact_divergence"] @ modal / omega
            maxima["continuity_relative"] = max(maxima["continuity_relative"], relative(qn, saved[prefix + "noncontact_charge"]))
            common = lifted[44:]
            terminal_voltage = base["differential_voltage_map"] + base["common_voltage_map"] @ common
            terminal_current = base["terminal_potential_map"].T @ base["contact_divergence"] @ modal / r
            admittance = base["differential_voltage_map"].T @ terminal_current
            maxima["terminal_voltage_relative"] = max(maxima["terminal_voltage_relative"], relative(terminal_voltage, saved[prefix + "terminal_voltage"]))
            maxima["terminal_current_relative"] = max(maxima["terminal_current_relative"], relative(terminal_current, saved[prefix + "terminal_current"]))
            maxima["admittance_relative"] = max(maxima["admittance_relative"], relative(admittance, saved[prefix + "port_admittance"]))
            old_y = base[old_prefix + "port_admittance"]
            z_change = abs(1 / np.diag(admittance) - 1 / np.diag(old_y)) / abs(1 / np.diag(admittance))
            maxima["z_change_report_absolute"] = max(maxima["z_change_report_absolute"], float(np.max(abs(z_change - case["shorted_z_change_relative_new"]))))
            reciprocity = relative(admittance, admittance.T)
            maxima["admittance_reciprocity_report_absolute"] = max(maxima["admittance_reciprocity_report_absolute"], abs(reciprocity - case["reciprocal_differential_admittance_relative"]))

            electric = saved[prefix + "electric_field"]
            total_current = saved[prefix + "total_cell_current"]
            assert relative(total_current, gamma[:, None, None] * electric) < 2e-13
            old_e = base[old_prefix + "electric_field"]
            selected = mid == 1
            difference = electric[selected] - old_e[selected]
            abf_change = np.sqrt(np.einsum("t,tdj,tdj->j", volumes[selected], difference.conj(), difference).real / np.einsum("t,tdj,tdj->j", volumes[selected], electric[selected].conj(), electric[selected]).real)
            maxima["abf_e_change_report_absolute"] = max(maxima["abf_e_change_report_absolute"], float(np.max(abs(abf_change - case["abf_electric_change_relative_new"]))))
            absorption = np.einsum("t,tdj,tdj->j", volumes * gamma.real, electric.conj(), electric).real
            terminal_power = np.real(np.sum((terminal_voltage @ drives).conj() * (terminal_current @ drives), axis=0))
            far = saved[prefix + "transverse_body_far"]
            radiation = omega * MU0 * k / (16 * pi * pi) * np.einsum("n,ndj,ndj->j", sphere_weights, far.conj(), far).real
            closure = (terminal_power - absorption - radiation) / (abs(terminal_power) + abs(absorption) + radiation)
            maxima["absorption_report_relative"] = max(maxima["absorption_report_relative"], relative(absorption, case["absorption_w"]))
            maxima["terminal_power_report_relative"] = max(maxima["terminal_power_report_relative"], relative(terminal_power, case["terminal_input_power_w"]))
            maxima["radiation_report_relative"] = max(maxima["radiation_report_relative"], relative(radiation, case["radiation_from_body_current_w"]))
            maxima["power_closure_report_absolute"] = max(maxima["power_closure_report_absolute"], float(np.max(abs(closure - case["port_minus_loss_body_radiation_relative"]))))
            summaries[method]["max_z_change_relative"] = max(summaries[method]["max_z_change_relative"], float(np.max(z_change)))
            summaries[method]["max_abf_e_change_relative"] = max(summaries[method]["max_abf_e_change_relative"], float(np.max(abf_change)))
            summaries[method]["max_power_closure_relative"] = max(summaries[method]["max_power_closure_relative"], float(np.max(abs(closure))))

    gates = {
        "exact_maps_and_trial": max(maxima["trial_map_relative"], maxima["lifted_current_relative"]) < 2e-13,
        "projected_system": max(maxima["projected_operator_relative"], maxima["projected_rhs_relative"], maxima["scaled_backward"], maxima["projected_residual_relative"]) < 2e-12,
        "saved_physical_residual": maxima["physical_residual_relative"] < 2e-13,
        "terminal_and_continuity": max(maxima[key] for key in ("continuity_relative", "terminal_voltage_relative", "terminal_current_relative", "admittance_relative", "z_change_report_absolute", "admittance_reciprocity_report_absolute")) < 2e-12,
        "material_and_work_reports": max(maxima[key] for key in ("abf_e_change_report_absolute", "absorption_report_relative", "terminal_power_report_relative", "radiation_report_relative", "power_closure_report_absolute")) < 2e-12,
    }
    gates = {key: bool(value) for key, value in gates.items()}
    assert all(gates.values()), {"gates": gates, "maxima": maxima}
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_SOURCE_TERMINAL_PROJECTION_SENSITIVITY_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": {key: list(value) for key, value in PINS.items()},
        "gates": gates,
        "maxima": maxima,
        "summaries": summaries,
        "scope": (
            "Read-only saved-array review of the exact integer D/E maps and 36-to-32 complex material trial map, C.H/C.T projected "
            "operators and residuals, terminal quantities, ABF field changes, and saved body-current work. No Green integral or solve is repeated. "
            "Normal continuity is built into the trial map and is not an independent interface validation. The very small terminal-Z sensitivity "
            "is specific to this short declared return control and does not bound the 3% ABF-field change, a board response, or PowerSI error. "
            "C.H and C.T remain distinct approximations; neither result is promoted as a continuum reference."
        ),
    }
    output.mkdir(parents=True)
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
