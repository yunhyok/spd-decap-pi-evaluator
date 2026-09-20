"""SPD Decap PI Evaluator v0.23.1: saved full-Green potential-terminal review.

Reconstruct the frozen Omar/Jiao-style contact-potential system and its
reported observables from saved matrices and fields.  This performs no field
solve and no Green-function integration.
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
    "producer": (
        "tools/research/diagnose_astra_box_potential_terminal_full_green.py",
        "e499970cd7f5020ff84fbb7c110363d3fa0c192a9dac41ef5c07cb8b535b7f1d",
    ),
    "driver": (
        "outputs/research/astra-box-potential-terminal-full-green-01/driver-at-run.py",
        "e499970cd7f5020ff84fbb7c110363d3fa0c192a9dac41ef5c07cb8b535b7f1d",
    ),
    "result": (
        "outputs/research/astra-box-potential-terminal-full-green-01/result.json",
        "9a3ab8025980d1948d0cf4e6b199fc606812da562a682ff63c6b4175a0b83699",
    ),
    "fields": (
        "outputs/research/astra-box-potential-terminal-full-green-01/fields.npz",
        "11c8904ba937fdf7afc5d141c2418e26dbd3e5142efe251c40f871b65b878a39",
    ),
    "kernels": (
        "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz",
        "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02",
    ),
    "source": (
        "outputs/research/astra-native-frequency-stamps-01/source-frequency-inputs.json",
        "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc",
    ),
    "paper": (
        "outputs/research/omar-jiao-2013-circuit-vie.pdf",
        "4314e3caac5356f78e4a7a8ec6348936a7ce357aeb0a032d33768c0cc59ade4a",
    ),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(actual, expected):
    scale = max(float(np.linalg.norm(expected)), np.finfo(float).tiny)
    return float(np.linalg.norm(actual - expected) / scale)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def tetra_volume(vertices):
    return abs(np.linalg.det((vertices[1:] - vertices[0]).T)) / 6


def boundary_faces(triangles, lower, upper):
    result = []
    for index, triangle in enumerate(triangles):
        if any(
            np.all(np.abs(triangle[:, axis] - bound) < 1e-14)
            for axis in range(3)
            for bound in (lower[axis], upper[axis])
        ):
            result.append(index)
    return np.array(result, dtype=int)


def run(output):
    require(not output.exists(), "review output already exists")
    for path, expected in PINS.values():
        require(sha(ROOT / path) == expected, f"pin mismatch: {path}")

    result = json.loads((ROOT / PINS["result"][0]).read_bytes())
    source = json.loads((ROOT / PINS["source"][0]).read_bytes())
    require(result["status"] == "PASS_FULL_GREEN_POTENTIAL_TERMINAL_DIAGNOSTIC", "producer status")
    require(result["script_sha256"] == PINS["producer"][1], "producer hash binding")
    require(result["fields_sha256"] == PINS["fields"][1], "field hash binding")
    require(result["program"] == "SPD Decap PI Evaluator" and result["version"] == "0.23.1", "program identity")
    require("omitted the leading -ik term" in result["correction"], "full-Green correction provenance")

    copper = source["stackup_layers"][0]
    require(copper["name"] == "Signal$TOP", "source copper row")
    sigma = float(copper["conductivity_s_m"])
    eps = EPS0 * float(copper["dk"]) * (1 - 1j * float(copper["df"]))

    with np.load(ROOT / PINS["kernels"][0], allow_pickle=False) as kernel, np.load(
        ROOT / PINS["fields"][0], allow_pickle=False
    ) as saved:
        tetrahedra = kernel["tetrahedra_m"]
        triangles = kernel["triangles_m"]
        frequencies = kernel["frequencies_hz"]
        divergence = kernel["distributional_divergence"]
        static_scalar = kernel["static_scalar_per_m"]
        scalar_tail = kernel["scalar_tail_per_m"]

        transform = saved["current_transform"]
        cell_map = saved["cell_current_map"]
        bn = saved["noncontact_divergence"]
        bc = saved["contact_divergence"]
        contact = saved["contact_faces"].astype(int)
        noncontact = saved["noncontact_faces"].astype(int)
        vcontact = saved["terminal_potentials"]
        mass = saved["geometric_mass"]

        require(tetrahedra.shape == (48, 4, 3) and triangles.shape == (120, 3, 3), "geometry shapes")
        require(divergence.shape == (168, 192), "divergence shape")
        require(transform.shape == (192, 72) and cell_map.shape == (48, 3, 72), "current-map shapes")
        require(bn.shape == (32, 72) and bc.shape == (16, 72), "divergence block shapes")
        require(contact.shape == (16,) and noncontact.shape == (32,), "contact partition shapes")
        require(vcontact.shape == (16, 2) and mass.shape == (72, 72), "terminal/mass shapes")

        lower = tetrahedra.reshape(-1, 3).min(axis=0)
        upper = tetrahedra.reshape(-1, 3).max(axis=0)
        dimensions = upper - lower
        boundary = boundary_faces(triangles, lower, upper)
        require(len(boundary) == 48, "boundary-face count")
        require(np.array_equal(np.sort(np.r_[contact, noncontact]), np.sort(boundary)), "contact partition coverage")
        require(np.all(np.abs(triangles[contact[:8], :, 0] - lower[0]) < 1e-14), "left contacts")
        require(np.all(np.abs(triangles[contact[8:], :, 0] - upper[0]) < 1e-14), "right contacts")
        require(np.array_equal(vcontact[:8], np.tile([1.0, 0.0], (8, 1))), "left potential map")
        require(np.array_equal(vcontact[8:], np.tile([0.0, 1.0], (8, 1))), "right potential map")

        full_b = divergence @ transform
        surface_b = full_b[48:]
        total_moment = -triangles.mean(axis=1).T @ surface_b
        interior = np.setdiff1d(np.arange(120), boundary)
        require(np.count_nonzero(full_b[:48]) == 0, "bulk divergence is not exactly zero")
        require(np.count_nonzero(surface_b[interior]) == 0, "internal normal jump is not exactly zero")
        require(np.array_equal(bn, surface_b[noncontact]), "saved noncontact B")
        require(np.array_equal(bc, surface_b[contact]), "saved contact B")
        require(np.count_nonzero(bn[:, :40]) == 0, "40-column noncontact null space")
        require(np.linalg.matrix_rank(bn[:, 40:]) == 32, "32-column noncontact range")

        volumes = np.array([tetra_volume(tetra) for tetra in tetrahedra])
        rebuilt_mass = np.einsum("tdi,tdj,t->ij", cell_map, cell_map, 1 / volumes)
        mass_relative = relative(mass, rebuilt_mass)

        # Independent uniform +x witness.  Since B stores minus outward flux,
        # the left terminal current is +A and the right terminal current is -A.
        target_moment = (volumes[:, None] * np.array([1.0, 0.0, 0.0])).reshape(-1)
        uniform_coeff, *_ = np.linalg.lstsq(cell_map.reshape(-1, 72), target_moment, rcond=None)
        uniform_moment_relative = relative(cell_map.reshape(-1, 72) @ uniform_coeff, target_moment)
        uniform_noncontact = float(np.linalg.norm(bn @ uniform_coeff))
        uniform_terminal = vcontact.T @ bc @ uniform_coeff
        area = dimensions[1] * dimensions[2]
        terminal_orientation_relative = float(
            np.linalg.norm(uniform_terminal - np.array([area, -area])) / (np.sqrt(2) * area)
        )
        differential_face_voltage = vcontact @ np.array([0.5, -0.5])
        weak_source_work = float(np.real(uniform_coeff @ (bc.T @ differential_face_voltage)))
        weak_source_work_relative = abs(weak_source_work - area) / area

        dc_resistance = dimensions[0] / (sigma * area)
        require(abs(dc_resistance / result["dc_resistance_ohm"] - 1) < 1e-14, "DC resistance metadata")
        charge_scale = EPS0 * max(dimensions)
        drives = np.array([[1, 0, 0.5], [0, 1, -0.5]], complex)
        _, sphere_weights = leggauss(20)
        sphere_weights = np.repeat(sphere_weights, 48) * 2 * np.pi / 48

        maxima = {
            "system_relative": 0.0,
            "rhs_relative": 0.0,
            "componentwise_backward": 0.0,
            "current_reconstruction_relative": 0.0,
            "noncontact_charge_relative": 0.0,
            "contact_charge_relative": 0.0,
            "continuity_relative": 0.0,
            "contact_potential_relative": 0.0,
            "terminal_current_relative": 0.0,
            "admittance_reciprocity_relative": 0.0,
            "differential_current_balance_relative": 0.0,
            "impedance_relative": 0.0,
            "absorption_relative": 0.0,
            "terminal_power_relative": 0.0,
            "body_far_radiation_relative": 0.0,
            "reported_metric_absolute": 0.0,
            "body_only_power_closure_relative": 0.0,
        }

        for fi, frequency in enumerate(frequencies):
            case = result["cases"][fi]
            require(float(frequency) == float(case["frequency_hz"]), "frequency order")
            omega = 2 * np.pi * frequency
            k = omega * np.sqrt(MU0 * EPS0)
            gamma = sigma + 1j * omega * eps
            ratio = 1 - 1j * omega * EPS0 / gamma
            current_scale = saved[f"case_{fi:02d}_current_scale"]
            expected_scale = np.full(72, 1 / dc_resistance)
            expected_scale[40:] = omega * charge_scale
            require(relative(current_scale, expected_scale) < 1e-15, "low-frequency column scale")

            # Frozen tails begin at Taylor n=2.  The canonical corrected run
            # restores the exact n=1 terms in both Green operators.
            p = (static_scalar[48:, 48:] + scalar_tail[fi, 48:, 48:] - 1j * k) / (4 * np.pi * EPS0)
            pnn = p[np.ix_(noncontact, noncontact)]
            pnc = p[np.ix_(noncontact, contact)]
            pcn = p[np.ix_(contact, noncontact)]
            pcc = p[np.ix_(contact, contact)]
            bj = ratio * bn
            aq = bj * current_scale[None, :] / (1j * omega)
            volume_kernel = static_scalar[:48, :48] + scalar_tail[fi, :48, :48]
            magnetic = (
                sum(cell_map[:, axis].T @ volume_kernel @ cell_map[:, axis] for axis in range(3))
                - 1j * k * (total_moment.T @ total_moment)
            )
            upper = (
                mass / gamma + 1j * omega * 1e-7 * ratio * magnetic
            ) * current_scale[None, :] + bn.T @ pnn @ aq
            expected_system = np.block(
                [[upper, -bn.T @ pnc * charge_scale], [-pcn @ aq, pcc * charge_scale]]
            )
            expected_rhs = np.vstack((bc.T @ vcontact, vcontact))

            system = saved[f"case_{fi:02d}_system"]
            rhs = saved[f"case_{fi:02d}_rhs"]
            solution = saved[f"case_{fi:02d}_scaled_solution"]
            modal = saved[f"case_{fi:02d}_current"]
            qc = saved[f"case_{fi:02d}_contact_charge"]
            qn = saved[f"case_{fi:02d}_noncontact_charge"]
            qall = saved[f"case_{fi:02d}_total_charge"]
            phi = saved[f"case_{fi:02d}_potential"]
            terminal_current = saved[f"case_{fi:02d}_terminal_current"]
            far = saved[f"case_{fi:02d}_transverse_volume_far"]

            maxima["system_relative"] = max(maxima["system_relative"], relative(system, expected_system))
            maxima["rhs_relative"] = max(maxima["rhs_relative"], relative(rhs, expected_rhs))
            residual = system @ solution - rhs
            backward = float(
                np.max(abs(residual) / np.maximum(abs(system) @ abs(solution) + abs(rhs), np.finfo(float).tiny))
            )
            maxima["componentwise_backward"] = max(maxima["componentwise_backward"], backward)

            rebuilt_modal = current_scale[:, None] * solution[:72]
            rebuilt_qc = charge_scale * solution[72:]
            rebuilt_qn = 1j * bj @ rebuilt_modal / omega
            rebuilt_qall = np.zeros((120, 2), complex)
            rebuilt_qall[noncontact] = rebuilt_qn
            rebuilt_qall[contact] = rebuilt_qc
            rebuilt_phi = p @ rebuilt_qall
            rebuilt_terminal = vcontact.T @ bc @ rebuilt_modal
            maxima["current_reconstruction_relative"] = max(maxima["current_reconstruction_relative"], relative(modal, rebuilt_modal))
            maxima["noncontact_charge_relative"] = max(maxima["noncontact_charge_relative"], relative(qn, rebuilt_qn))
            maxima["contact_charge_relative"] = max(maxima["contact_charge_relative"], relative(qc, rebuilt_qc))
            require(relative(qall, rebuilt_qall) < 1e-14, "total charge reconstruction")
            require(relative(phi, rebuilt_phi) < 1e-14, "potential reconstruction")
            maxima["terminal_current_relative"] = max(maxima["terminal_current_relative"], relative(terminal_current, rebuilt_terminal))

            continuity = bj @ modal + 1j * omega * qn
            continuity_scale = abs(bj) @ abs(modal) + omega * abs(qn)
            continuity_relative = float(
                np.max(abs(continuity) / np.maximum(continuity_scale, np.finfo(float).tiny))
            )
            maxima["continuity_relative"] = max(maxima["continuity_relative"], continuity_relative)
            contact_potential_relative = relative(phi[contact], vcontact)
            maxima["contact_potential_relative"] = max(maxima["contact_potential_relative"], contact_potential_relative)
            reciprocity = relative(terminal_current, terminal_current.T)
            maxima["admittance_reciprocity_relative"] = max(maxima["admittance_reciprocity_relative"], reciprocity)

            differential = terminal_current @ drives[:, 2]
            current_balance = float(abs(differential.sum()) / max(np.linalg.norm(differential), np.finfo(float).tiny))
            loop_current = (differential[0] - differential[1]) / 2
            impedance = 1 / loop_current
            reported_impedance = complex(*case["differential_impedance_ohm"])
            maxima["differential_current_balance_relative"] = max(maxima["differential_current_balance_relative"], current_balance)
            maxima["impedance_relative"] = max(maxima["impedance_relative"], abs(impedance - reported_impedance) / abs(reported_impedance))

            driven_modal = modal @ drives
            absorption = np.real(1 / gamma) * np.real(
                np.diag(driven_modal.conj().T @ mass @ driven_modal)
            )
            terminal_power = np.real(np.sum(drives.conj() * (terminal_current @ drives), axis=0))
            radiation = omega * MU0 * k / (16 * np.pi * np.pi) * np.einsum(
                "n,ndj,ndj->j", sphere_weights, far.conj(), far
            ).real
            reported_absorption = np.array(case["absorption_w"])
            reported_terminal_power = np.array(case["terminal_input_power_w"])
            reported_radiation = np.array(case["radiation_from_volume_current_w"])
            maxima["absorption_relative"] = max(maxima["absorption_relative"], relative(absorption, reported_absorption))
            maxima["terminal_power_relative"] = max(maxima["terminal_power_relative"], relative(terminal_power, reported_terminal_power))
            maxima["body_far_radiation_relative"] = max(maxima["body_far_radiation_relative"], relative(radiation, reported_radiation))
            body_closure = (terminal_power - absorption - radiation) / (
                abs(terminal_power) + abs(absorption) + radiation
            )
            maxima["body_only_power_closure_relative"] = max(
                maxima["body_only_power_closure_relative"], float(np.max(abs(body_closure)))
            )

            report_values = [
                abs(backward - case["backward"][-1]),
                abs(contact_potential_relative - case["contact_potential_relative"]),
                abs(reciprocity - case["reciprocal_admittance_relative"]),
                abs(current_balance - case["differential_current_balance_relative"]),
                float(np.max(abs(body_closure - np.array(case["port_minus_loss_volume_radiation_relative"])))),
            ]
            maxima["reported_metric_absolute"] = max(maxima["reported_metric_absolute"], *report_values)

            if fi == 0:
                dc_relative = abs(impedance.real / dc_resistance - 1)
                current_density = np.einsum("tdi,i->td", cell_map, modal @ drives[:, 2]) / volumes[:, None]
                target = np.array([sigma / dimensions[0], 0.0, 0.0])
                dc_current_relative = float(
                    np.sqrt(
                        np.einsum("t,td,td->", volumes, (current_density - target).conj(), current_density - target).real
                        / (volumes.sum() * np.dot(target, target))
                    )
                )

    gates = {
        "saved_system": maxima["system_relative"] < 2e-14 and maxima["rhs_relative"] < 2e-14,
        "equations": maxima["componentwise_backward"] < 1e-12,
        "topology_and_mass": mass_relative < 2e-14 and uniform_moment_relative < 2e-14,
        "weak_source_sign": terminal_orientation_relative < 2e-14 and weak_source_work_relative < 2e-14,
        "independent_contact_charge": maxima["contact_charge_relative"] < 2e-14,
        "noncontact_continuity": maxima["continuity_relative"] < 2e-12,
        "contact_potential": maxima["contact_potential_relative"] < 2e-12,
        "terminal_observables": maxima["impedance_relative"] < 2e-14 and maxima["terminal_power_relative"] < 2e-14,
        "low_frequency_dc": dc_relative < 1e-8 and dc_current_relative < 1e-2,
        "reported_metrics": maxima["reported_metric_absolute"] < 2e-13,
    }
    gates = {name: bool(value) for name, value in gates.items()}
    require(all(gates.values()), f"review gate failed: {gates}")

    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_FULL_GREEN_POTENTIAL_TERMINAL_ALGEBRA_DC_SIGN_WITH_SCOPE",
        "reviewer_sha256": sha(Path(__file__)),
        "pins": PINS,
        "gates": gates,
        "topology": {
            "current_coordinates": 72,
            "noncontact_charge_null_coordinates": 40,
            "noncontact_charge_range_coordinates": 32,
            "contact_faces": 16,
            "noncontact_boundary_faces": 32,
            "uniform_noncontact_divergence_absolute": uniform_noncontact,
            "uniform_terminal_orientation_relative": terminal_orientation_relative,
            "weak_source_work_relative": weak_source_work_relative,
            "mass_reconstruction_relative": mass_relative,
        },
        "maxima": maxima,
        "low_frequency": {
            "dc_resistance_ohm": float(dc_resistance),
            "one_khz_resistance_relative_to_dc": float(dc_relative),
            "one_khz_current_l2_relative_to_uniform_dc": dc_current_relative,
        },
        "derivation": {
            "divergence_convention": "B is integrated volume divergence minus outward boundary flux.",
            "noncontact_continuity": "B_J,n J + j*omega*q_n = 0, hence q_n = j*B_J,n*J/omega.",
            "weak_current_equation": "Integral U dot grad(phi) = -B_U^T phi; known contact potential moves to +B_U,c^T V_c on the RHS.",
            "contact_equation": "P_cn*q_n + P_cc*q_c = V_c, with q_c independent of the current-divergence relation.",
            "terminal_current": "I_c = B_U,c*U is positive into the modeled body; source work is Re(V_c^H I_c).",
        },
        "scope": (
            "Read-only reconstruction of the corrected frozen 48-tetra homogeneous-copper full-Green potential-terminal diagnostic. "
            "The saved Green matrices are only multiplied, never reintegrated, and no linear system is solved. "
            "The earlier terminal01 omitted the exact leading -ik vector and scalar Green terms and remains unaccepted; "
            "this review explicitly restores those terms while reconstructing the canonical saved system. "
            "The contact constraints use face-average Galerkin potentials rather than Omar/Jiao centroid collocation. "
            "The result validates the discrete sign, current/charge partition, low-frequency scaling, DC transport resistance, "
            "and saved terminal observables. It does not model an external source/return lead. The body-current far field omits "
            "that continuation, and its maximum power discrepancy is retained rather than treated as a closed-source radiation certificate. "
            "It does not qualify a mixed-material interface, actual source solid, board port, board impedance, or PowerSI accuracy."
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
