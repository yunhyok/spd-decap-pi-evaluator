"""SPD Decap PI Evaluator v0.23.1: saved source terminal review.

Reconstruct the frozen 46-variable residual, charges, terminal observables,
material work, and interface traces.  No Green integration or linear solve.
"""
from __future__ import annotations

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
    "producer": ("tools/research/diagnose_astra_source_potential_terminal.py", "a5ee10439987824c37455d32618c43635f2e6ba27406f51e650fa98d786c7d2c"),
    "driver": ("outputs/research/astra-source-potential-terminal-02/driver-at-run.py", "a5ee10439987824c37455d32618c43635f2e6ba27406f51e650fa98d786c7d2c"),
    "result": ("outputs/research/astra-source-potential-terminal-02/result.json", "4603933591de9d5cbb29f2a44a425a18b631665ce30f6538e074c3aa21d16fd1"),
    "fields": ("outputs/research/astra-source-potential-terminal-02/fields.npz", "33262a8b2fad33a718af80e7cf341f9611d65d5fa18e28447107528503b1544d"),
    "contacts": ("outputs/research/astra-source-potential-contacts-01/contacts.npz", "55294d51fdb4da642499e38e7e613a226d9fc5561d2159e73f38de1f853aa21a"),
    "low_kernel": ("outputs/research/astra-source-potential-kernels-01/kernels-v14-f28.npz", "6f1e9d601acb36cc0db23603fcf21b8a821f5c638567dd37dd66a61d4d485f61"),
    "high_kernel": ("outputs/research/astra-source-potential-kernels-01/kernels-v20-f36.npz", "bffa3704cff006606f987911cb4121efaa39a8bf6c16cf704a7b4600afbcf437"),
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def relative(actual, expected) -> float:
    return float(np.linalg.norm(np.asarray(actual) - np.asarray(expected)) / max(float(np.linalg.norm(expected)), np.finfo(float).tiny))


def face_pairs(distributional: np.ndarray) -> list[list[tuple[int, int]]]:
    result = []
    for row in distributional:
        result.append([(int(index // 4), int(index % 4)) for index in np.flatnonzero(row)])
    return result


def run(output: Path) -> None:
    for path, expected in PINS.values():
        assert digest(ROOT / path) == expected, path
    result = json.loads((ROOT / PINS["result"][0]).read_text(encoding="utf-8"))
    assert result["status"] == "STOP_SOURCE_TERMINAL_PHYSICAL_DIAGNOSTIC"
    assert result["program"] == "SPD Decap PI Evaluator" and result["version"] == "0.23.1"
    assert result["script_sha256"] == PINS["producer"][1] and result["fields_sha256"] == PINS["fields"][1]
    assert set(name for name, passed in result["gates"].items() if not passed) == {"interface_own_trace_below_one_percent"}
    case_lookup = {(float(case["frequency_hz"]), case["kernel_tag"]): case for case in result["cases"]}
    assert len(case_lookup) == 12

    with np.load(ROOT / PINS["contacts"][0], allow_pickle=False) as source:
        tetrahedra = source["tetrahedra_m"]
        triangles = source["triangles_m"]
        mid = source["material_id"]
        distributional = source["distributional_face_divergence"]
    with np.load(ROOT / PINS["fields"][0], allow_pickle=False) as saved:
        arrays = {name: saved[name] for name in saved.files}
    transform = arrays["real_current_transform"]
    h = arrays["cell_integrated_current_map"]
    mass = arrays["mass"]
    bn = arrays["noncontact_divergence"]
    bc = arrays["contact_divergence"]
    active = arrays["active_charge_faces"].astype(int)
    contact = arrays["contact_faces"].astype(int)
    noncontact = arrays["noncontact_faces"].astype(int)
    vmap = arrays["terminal_potential_map"]
    dmap = arrays["differential_voltage_map"]
    cmap = arrays["common_voltage_map"]
    assert transform.shape == (72, 36) and h.shape == (18, 3, 36) and mass.shape == (36, 36)
    assert bn.shape == (24, 36) and bc.shape == (8, 36)
    assert np.array_equal(np.sort(np.r_[contact, noncontact]), np.sort(active)) and len(np.intersect1d(contact, noncontact)) == 0
    lookup = {int(face): index for index, face in enumerate(active)}
    nc = np.array([lookup[int(face)] for face in noncontact])
    cc = np.array([lookup[int(face)] for face in contact])
    owners = face_pairs(distributional)
    assert sum(len(owners[int(face)]) == 2 for face in active) == 4
    assert all(len(owners[int(face)]) == 1 for face in contact)
    volumes = np.abs(np.linalg.det(tetrahedra[:, 1:] - tetrahedra[:, :1])) / 6
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) / 2
    drives = np.array([[1, 0, 1], [0, 1, 1j]], complex)
    _, polar_weights = leggauss(20)
    sphere_weights = np.repeat(polar_weights, 48) * 2 * pi / 48
    kernel_paths = {"v14f28": PINS["low_kernel"][0], "v20f36": PINS["high_kernel"][0]}
    kernels = {}
    for tag, path in kernel_paths.items():
        with np.load(ROOT / path, allow_pickle=False) as bundle:
            assert np.array_equal(bundle["active_charge_faces"], active)
            kernels[tag] = (bundle["frequencies_hz"], bundle["face_green_per_m"])

    names = (
        "scaled_residual_componentwise", "unscaled_residual_componentwise", "modal_relative", "contact_charge_relative",
        "noncontact_charge_relative", "charge_relative", "potential_relative", "terminal_voltage_relative", "terminal_current_relative",
        "admittance_relative", "continuity_relative", "contact_potential_relative", "pair_kcl_report_absolute",
        "noncontact_neutrality_report_absolute", "total_charge_report_absolute", "admittance_reciprocity_report_absolute",
        "passivity_report_absolute_s", "impedance_relative", "material_electric_report_relative", "material_current_report_relative",
        "interface_own_trace_report_absolute", "interface_geometric_trace_report_absolute", "absorption_relative",
        "terminal_power_relative", "radiation_relative", "power_closure_report_absolute", "constitutive_electric_relative",
        "constitutive_total_current_relative", "q_refinement_current_report_absolute", "q_refinement_admittance_report_absolute",
    )
    maxima = {name: 0.0 for name in names}
    reconstructed_refinement = []
    per_order = {}
    worst_interface = 0.0

    for fi, frequency in enumerate(np.array([1e3, 1e4, 1e5, 1e6, 1e7, 1e8])):
        omega = 2 * pi * frequency
        k = omega * np.sqrt(MU0 * EPS0)
        per_order[fi] = {}
        for tag, (frequencies, face_green) in kernels.items():
            assert float(frequencies[fi]) == frequency
            case = case_lookup[(float(frequency), tag)]
            prefix = f"{tag}_{fi:02d}_"
            system = arrays[prefix + "system"]
            rhs = arrays[prefix + "rhs"]
            solution = arrays[prefix + "scaled_solution"]
            current_scale = arrays[prefix + "current_scale"]
            charge_scale = arrays[prefix + "charge_scale"]
            columns = np.r_[current_scale, charge_scale, np.ones(2)]
            unknown = columns[:, None] * solution
            unscaled = system / columns[None, :]
            scaled_residual = system @ solution - rhs
            unscaled_residual = unscaled @ unknown - rhs
            maxima["scaled_residual_componentwise"] = max(maxima["scaled_residual_componentwise"], float(np.max(abs(scaled_residual) / np.maximum(abs(system) @ abs(solution) + abs(rhs), np.finfo(float).tiny))))
            maxima["unscaled_residual_componentwise"] = max(maxima["unscaled_residual_componentwise"], float(np.max(abs(unscaled_residual) / np.maximum(abs(unscaled) @ abs(unknown) + abs(rhs), np.finfo(float).tiny))))

            modal = unknown[:36]
            qc = unknown[36:44]
            common = unknown[44:46]
            gamma = arrays[prefix + "cell_gamma"]
            kappa = gamma - 1j * omega * EPS0
            qn = 1j * bn @ modal / omega
            charge = np.zeros((32, 2), complex)
            charge[nc] = qn
            charge[cc] = qc
            p = face_green[fi] / (4 * pi * EPS0)
            potential = p @ charge
            terminal_voltage = dmap + cmap @ common
            ratio = kappa[0] / gamma[0]
            assert abs(gamma[0] - gamma[-1]) < 1e-14 * abs(gamma[0])
            terminal_current = vmap.T @ bc @ modal / ratio
            admittance = dmap.T @ terminal_current
            for key, actual in (("modal_relative", modal), ("contact_charge_relative", qc), ("noncontact_charge_relative", qn),
                                ("charge_relative", charge), ("potential_relative", potential), ("terminal_voltage_relative", terminal_voltage),
                                ("terminal_current_relative", terminal_current), ("admittance_relative", admittance)):
                saved_name = {"modal_relative": "current", "admittance_relative": "port_admittance"}.get(key, key.removesuffix("_relative"))
                maxima[key] = max(maxima[key], relative(actual, arrays[prefix + saved_name]))

            continuity = bn @ modal + 1j * omega * qn
            scale = abs(bn) @ abs(modal) + omega * abs(qn)
            maxima["continuity_relative"] = max(maxima["continuity_relative"], float(np.max(abs(continuity) / np.maximum(scale, np.finfo(float).tiny))))
            maxima["contact_potential_relative"] = max(maxima["contact_potential_relative"], relative(potential[cc], vmap @ terminal_voltage))
            pair_values = []
            for left, right in ((0, 2), (1, 3)):
                pair_values.append(abs(terminal_current[left] + terminal_current[right]) / np.maximum(np.hypot(abs(terminal_current[left]), abs(terminal_current[right])), np.finfo(float).tiny))
            maxima["pair_kcl_report_absolute"] = max(maxima["pair_kcl_report_absolute"], float(np.max(abs(np.asarray(pair_values) - np.asarray(case["pair_kcl_relative"])))))
            noncontact_neutrality = abs(qn.sum(axis=0)) / np.maximum(np.sum(abs(qn), axis=0), np.finfo(float).tiny)
            total_neutrality = abs(charge.sum(axis=0)) / np.maximum(np.sum(abs(charge), axis=0), np.finfo(float).tiny)
            maxima["noncontact_neutrality_report_absolute"] = max(maxima["noncontact_neutrality_report_absolute"], float(np.max(abs(noncontact_neutrality - case["noncontact_charge_sum_relative"]))))
            maxima["total_charge_report_absolute"] = max(maxima["total_charge_report_absolute"], float(np.max(abs(total_neutrality - case["total_including_independent_contact_charge_sum_relative"]))))
            reciprocity = relative(admittance, admittance.T)
            maxima["admittance_reciprocity_report_absolute"] = max(maxima["admittance_reciprocity_report_absolute"], abs(reciprocity - case["reciprocal_differential_admittance_relative"]))
            eig = np.linalg.eigvalsh((admittance + admittance.conj().T) / 2)
            maxima["passivity_report_absolute_s"] = max(maxima["passivity_report_absolute_s"], float(np.max(abs(eig - case["passive_admittance_eigenvalues_s"]))))
            impedance = 1 / np.diag(admittance)
            reported_impedance = np.array([complex(*value) for value in case["shorted_port_impedance_ohm"]])
            maxima["impedance_relative"] = max(maxima["impedance_relative"], relative(impedance, reported_impedance))

            driven = modal @ drives
            density_j = np.einsum("tdi,ij->tdj", h, driven) / volumes[:, None, None]
            electric = density_j / kappa[:, None, None]
            total_current = gamma[:, None, None] * electric
            maxima["constitutive_electric_relative"] = max(maxima["constitutive_electric_relative"], relative(electric, arrays[prefix + "electric_field"]))
            maxima["constitutive_total_current_relative"] = max(maxima["constitutive_total_current_relative"], relative(total_current, arrays[prefix + "total_cell_current"]))
            for region, reported_material in enumerate(case["material_observables"]):
                selected = mid == region
                rms_e = np.sqrt(np.einsum("t,tdj,tdj->j", volumes[selected], electric[selected].conj(), electric[selected]).real / volumes[selected].sum())
                rms_u = np.sqrt(np.einsum("t,tdj,tdj->j", volumes[selected], total_current[selected].conj(), total_current[selected]).real)
                maxima["material_electric_report_relative"] = max(maxima["material_electric_report_relative"], relative(rms_e, reported_material["electric_rms_v_m"]))
                maxima["material_current_report_relative"] = max(maxima["material_current_report_relative"], relative(rms_u, reported_material["total_current_l2_a_sqrt_inverse_m"]))

            face_u = (transform @ driven).reshape(18, 4, 3) * (gamma / kappa)[:, None, None]
            rebuilt_interfaces = []
            for pair_id in ((0, 1), (1, 2)):
                jump = np.zeros(3)
                own = np.zeros(3)
                geometric = np.zeros(3)
                count = 0
                for face, pairs in enumerate(owners):
                    if len(pairs) != 2:
                        continue
                    (a, af), (bcell, bf) = pairs
                    if tuple(sorted((int(mid[a]), int(mid[bcell])))) != pair_id:
                        continue
                    left, right = face_u[a, af], face_u[bcell, bf]
                    jump += abs(left + right) ** 2 / areas[face]
                    own += (abs(left) ** 2 + abs(right) ** 2) / areas[face]
                    geometric += 2 * abs(left) * abs(right) / areas[face]
                    count += 1
                assert count == 2
                rebuilt_interfaces.append((np.sqrt(jump / np.maximum(own, np.finfo(float).tiny)), np.sqrt(jump / np.maximum(geometric, np.finfo(float).tiny))))
            for rebuilt, reported_interface in zip(rebuilt_interfaces, case["interfaces"]):
                own, geometric = rebuilt
                maxima["interface_own_trace_report_absolute"] = max(maxima["interface_own_trace_report_absolute"], float(np.max(abs(own - reported_interface["normal_u_jump_own_trace_relative"]))))
                maxima["interface_geometric_trace_report_absolute"] = max(maxima["interface_geometric_trace_report_absolute"], float(np.max(abs(geometric - reported_interface["normal_u_jump_geometric_trace_relative"]))))
                worst_interface = max(worst_interface, float(np.max(own)))

            loss = np.einsum("t,tdj,tdj->j", volumes * gamma.real, electric.conj(), electric).real
            terminal_power = np.real(np.sum((terminal_voltage @ drives).conj() * (terminal_current @ drives), axis=0))
            far = arrays[prefix + "transverse_body_far"]
            radiation = omega * MU0 * k / (16 * pi * pi) * np.einsum("n,ndj,ndj->j", sphere_weights, far.conj(), far).real
            closure = (terminal_power - loss - radiation) / (abs(terminal_power) + abs(loss) + radiation)
            maxima["absorption_relative"] = max(maxima["absorption_relative"], relative(loss, case["absorption_w"]))
            maxima["terminal_power_relative"] = max(maxima["terminal_power_relative"], relative(terminal_power, case["terminal_input_power_w"]))
            maxima["radiation_relative"] = max(maxima["radiation_relative"], relative(radiation, case["radiation_from_body_current_w"]))
            maxima["power_closure_report_absolute"] = max(maxima["power_closure_report_absolute"], float(np.max(abs(closure - case["port_minus_loss_body_radiation_relative"]))))
            per_order[fi][tag] = (modal, admittance)

        low_modal, low_y = per_order[fi]["v14f28"]
        high_modal, high_y = per_order[fi]["v20f36"]
        delta = low_modal - high_modal
        current_change = float(np.sqrt(np.trace(delta.conj().T @ mass @ delta).real / np.trace(high_modal.conj().T @ mass @ high_modal).real))
        admittance_change = relative(low_y, high_y)
        reported = result["quadrature_comparisons"][fi]
        maxima["q_refinement_current_report_absolute"] = max(maxima["q_refinement_current_report_absolute"], abs(current_change - reported["current_mass_refinement_relative"]))
        maxima["q_refinement_admittance_report_absolute"] = max(maxima["q_refinement_admittance_report_absolute"], abs(admittance_change - reported["port_admittance_refinement_relative"]))
        reconstructed_refinement.append({"frequency_hz": float(frequency), "current_mass_refinement_relative": current_change, "port_admittance_refinement_relative": admittance_change})

    gates = {
        "saved_46_system": max(maxima["scaled_residual_componentwise"], maxima["unscaled_residual_componentwise"]) < 1e-12,
        "current_charge_and_constitutive": max(maxima[key] for key in ("modal_relative", "contact_charge_relative", "noncontact_charge_relative", "charge_relative", "potential_relative", "terminal_voltage_relative", "terminal_current_relative", "admittance_relative", "constitutive_electric_relative", "constitutive_total_current_relative")) < 2e-13,
        "continuity_contacts_and_circuit": max(maxima[key] for key in ("continuity_relative", "contact_potential_relative", "pair_kcl_report_absolute", "noncontact_neutrality_report_absolute", "total_charge_report_absolute", "admittance_reciprocity_report_absolute", "passivity_report_absolute_s", "impedance_relative")) < 2e-12,
        "material_interface_reports": max(maxima[key] for key in ("material_electric_report_relative", "material_current_report_relative", "interface_own_trace_report_absolute", "interface_geometric_trace_report_absolute")) < 2e-12,
        "work": max(maxima[key] for key in ("absorption_relative", "terminal_power_relative", "radiation_relative", "power_closure_report_absolute")) < 2e-12,
        "quadrature_reports": max(maxima["q_refinement_current_report_absolute"], maxima["q_refinement_admittance_report_absolute"]) < 2e-14,
        "stop_interface_reproduced": worst_interface > 0.01,
    }
    gates = {key: bool(value) for key, value in gates.items()}
    assert all(gates.values()), {"gates": gates, "maxima": maxima}
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_SOURCE_TERMINAL_ALGEBRA_WORK__CONFIRM_STOP_INTERFACE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": {key: list(value) for key, value in PINS.items()},
        "gates": gates,
        "maxima": maxima,
        "worst_interface_own_trace_relative": worst_interface,
        "quadrature_comparisons": reconstructed_refinement,
        "scope": (
            "Read-only saved-array review. It recomputes the 46-variable scaled/unscaled residual, independent contact and noncontact charges, "
            "contact potentials, both pair KCLs, admittance/Z/passivity, constitutive cell fields, per-material norms, interface own/geometric trace "
            "ratios, terminal work, absorption, body-current far power, and q14/q20 refinement. No Green integral or linear solve is repeated. "
            "The algebraic result is accepted while the physical result remains STOP because the interface own-trace jump exceeds 1%. Total charge "
            "including independent contact charge is reported without forced neutralization. Body-only far power omits the ideal external source/short. "
            "No material-interface, source-solid, continuum, board-Z, or PowerSI accuracy approval."
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
