"""SPD Decap PI Evaluator v0.23.1: independent saved Fourier VIE review.

This reads the frozen current/charge result and fields only.  It does not call
the producer, rebuild a board model, or read an SPD/scenario.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import monotonic

import numpy as np
from numpy.polynomial.legendre import leggauss


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
EPS0 = 8.8541878128e-12
MU0 = 4e-7 * np.pi

PINS = {
    "driver": ("astra-fourier-current-charge-04/driver-at-run.py", "a46ebbab2ed79b77158391d8af93fcfaec9835325bda9438122e2865a595550d"),
    "result": ("astra-fourier-current-charge-04/result.json", "fd9bd4e00bc57ff960057151066ffc6bd2d5c77862feaef9f9cb40e3f39a652c"),
    "fields": ("astra-fourier-current-charge-04/fields.npz", "0f0dc7303deda5cd921047dbaddc2f17dc7c80e7c379cb18b95e5bb26f6ccc50"),
    "source": ("astra-native-frequency-stamps-01/source-frequency-inputs.json", "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc"),
    "low": ("astra-native-low-band-stamps-01/receipt.json", "1968b9091d7eab184c2c0ad195fdc847661377484624a4ac4985d06e42216299"),
    "high": ("astra-native-frequency-stamps-01/receipt.json", "4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856"),
    "low_review": ("astra-native-low-band-stamps-01/independent-review.json", "1fed75a2227ffb90f937914ebf2b112614736b632b9fec053ac9876125168228"),
    "high_review": ("astra-native-frequency-stamps-01/independent-review.json", "74d4476018645392c66164990ee7a44c08a09ca9594edc7df7dc95fff1ad2f94"),
    "tm_review": ("astra-joint-tm-boundary-review-01/independent-review.json", "14dcc30f9c092b4a1f2131cfca65e64c54393c5e9a2bc001dd7125e9ed07fb49"),
}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def relative(actual, expected) -> float:
    actual, expected = np.asarray(actual), np.asarray(expected)
    return float(np.linalg.norm(actual - expected) / max(np.linalg.norm(expected), np.finfo(float).tiny))


def materials(source: dict, low: dict, high: dict, frequency: float):
    rows = source["stackup_layers"][:3]
    require([row["name"] for row in rows] == ["Signal$TOP", "Medium$DR0102", "Signal$L02(DGND)"], "source layer order")
    require([row["thickness_um"] for row in rows] == [25.0, 30.0, 20.0], "source thickness")
    require(rows[0]["conductivity_s_m"] == rows[2]["conductivity_s_m"] == 59590000.0, "source copper sigma")
    properties = {}
    for receipt in (low, high):
        gap = receipt["gap_properties"][0]
        require(gap["upper_layer"] == rows[0]["name"] and gap["lower_layer"] == rows[2]["name"], "source gap endpoints")
        for f, dk, df in zip(receipt["frequencies_hz"], gap["dk"], gap["df"], strict=True):
            if f in properties:
                require(np.allclose(properties[f], (dk, df), rtol=1e-14, atol=0), "overlap frequency property")
            properties[f] = (dk, df)
    require(frequency in properties, f"missing source dielectric frequency {frequency}")
    eps, sigma = [], []
    for index, row in enumerate(rows):
        dk, df = properties[frequency] if index == 1 else (row["dk"], row["df"])
        eps.append(EPS0 * dk * (1 - 1j * df))
        sigma.append(float(row["conductivity_s_m"] or 0))
    thickness = np.asarray([row["thickness_um"] * 1e-6 for row in rows])
    eps, sigma = np.asarray(eps), np.asarray(sigma)
    return rows, thickness, eps, sigma, sigma + 1j * 2 * np.pi * frequency * eps


def boundary_operator(n: int) -> np.ndarray:
    b = np.zeros((4, 3 * (n + 1)))
    b[0, 0], b[-1, -1] = 1, -1
    for interface in (1, 2):
        b[interface, interface * (n + 1) - 1] = -1
        b[interface, interface * (n + 1)] = 1
    return b


def conforming_maps(n: int, ratio: np.ndarray):
    size = 3 * (n + 1)
    c = np.zeros((size, 3 * (n - 1)), complex)
    for material in range(3):
        c[material * (n + 1) + 1:(material + 1) * (n + 1) - 1,
          material * (n - 1):(material + 1) * (n - 1)] = np.eye(n - 1)
    d = np.zeros((size, 4), complex)
    d[0, 0], d[-1, -1] = 1, -1
    jumps = []
    for interface in (1, 2):
        jump = ratio[interface] - ratio[interface - 1]
        jumps.append(abs(jump))
        left, right = interface * (n + 1) - 1, interface * (n + 1)
        d[left, interface] = ratio[interface - 1] / jump
        d[right, interface] = ratio[interface] / jump
    return c, d, jumps


def exact_tm(thickness, gamma, omega, kx):
    q = np.sqrt(kx * kx + 1j * omega * MU0 * gamma)
    diag = q / gamma / np.tanh(q * thickness)
    off = -q / gamma / np.sinh(q * thickness)
    system = np.zeros((4, 4), complex)
    for layer in range(3):
        system[layer:layer + 2, layer:layer + 2] += np.array([[diag[layer], off[layer]], [off[layer], diag[layer]]])
    h = np.zeros((4, 2), complex)
    h[0, 0], h[-1, 1] = 1, -1
    h[1:3] = np.linalg.solve(system[1:3, 1:3], -system[1:3][:, [0, 3]] @ h[[0, 3]])
    reaction = system @ h
    return q, h, np.vstack((reaction[0], -reaction[-1]))


def exact_field(q, endpoints, gamma, thickness, layer, z):
    p, length = q[layer], thickness[layer]
    left, right = endpoints[layer], endpoints[layer + 1]
    h = (np.sinh(p * (length - z))[:, None] * left + np.sinh(p * z)[:, None] * right) / np.sinh(p * length)
    derivative = p * (-np.cosh(p * (length - z))[:, None] * left + np.cosh(p * z)[:, None] * right) / np.sinh(p * length)
    return h, -derivative / gamma[layer]


def saved_field_metrics(j, charge, h, zport, thickness, gamma, omega, kx, q, exact_h):
    n = j.shape[0] // 3 - 1
    ratio = (gamma - 1j * omega * EPS0) / gamma
    kappa = gamma - 1j * omega * EPS0
    b = boundary_operator(n)
    drives = np.array([[1, 0, 1], [0, 1, 1j]], complex)
    x, w = leggauss(16)
    u, w = (x + 1) / 2, w / 2
    electric_errors, magnetic_errors = [], []
    power = np.zeros((3, 3), complex)
    for material, length in enumerate(thickness):
        err = norm = herr = hnorm = 0.0
        for element in range(n):
            offset = material * (n + 1) + element
            hl, hr = h[offset:offset + 2]
            jl, jr = j[offset:offset + 2]
            exact, exact_ex = exact_field(q, exact_h, gamma, thickness, material, (element + u) * length / n)
            exact_ez = 1j * kx * exact / gamma[material]
            candidate_h = (1 - u[:, None]) * hl + u[:, None] * hr
            candidate_ez = ((1 - u[:, None]) * jl + u[:, None] * jr) / kappa[material]
            candidate_ex = np.broadcast_to(1j * (jr - jl) / (kx * (length / n) * kappa[material]), candidate_ez.shape)
            weight = (w * length / n)[:, None]
            err += np.sum(weight * (np.abs(candidate_ex - exact_ex) ** 2 + np.abs(candidate_ez - exact_ez) ** 2))
            norm += np.sum(weight * (np.abs(exact_ex) ** 2 + np.abs(exact_ez) ** 2))
            herr += np.sum(weight * np.abs(candidate_h - exact) ** 2)
            hnorm += np.sum(weight * np.abs(exact) ** 2)
            e2 = np.abs(candidate_ex @ drives) ** 2 + np.abs(candidate_ez @ drives) ** 2
            power[0] += gamma[material].real * np.sum(weight * e2, axis=0)
            power[1] += 1j * omega * MU0 * np.sum(weight * np.abs(candidate_h @ drives) ** 2, axis=0)
            power[2] -= 1j * gamma[material].imag * np.sum(weight * e2, axis=0)
        electric_errors.append(float(np.sqrt(err / norm)))
        magnetic_errors.append(float(np.sqrt(herr / hnorm)))
    exact_j = np.zeros_like(j)
    for material in range(3):
        block = slice(material * (n + 1), (material + 1) * (n + 1))
        exact_j[block] = ratio[material] * 1j * kx * np.linspace(exact_h[material], exact_h[material + 1], n + 1)
    exact_charge = -(b @ exact_j) / (1j * omega)
    left, right, exact_interface = [], [], []
    for interface in (1, 2):
        index = interface * (n + 1)
        left.append(1j * (j[index - 1] - j[index - 2]) / (kx * (thickness[interface - 1] / n) * kappa[interface - 1]))
        right.append(1j * (j[index + 1] - j[index]) / (kx * (thickness[interface] / n) * kappa[interface]))
        exact_interface.append(exact_field(q, exact_h, gamma, thickness, interface, np.array([0.0]))[1][0])
    left, right, exact_interface = np.asarray(left), np.asarray(right), np.asarray(exact_interface)
    tangent_scale = max(np.linalg.norm(exact_interface), 0.5 * (np.linalg.norm(left) + np.linalg.norm(right)))
    pin = np.diag(drives.conj().T @ zport @ drives)
    closure = np.abs(pin - power.sum(axis=0)) / (np.abs(pin) + np.abs(power).sum(axis=0))
    return {
        "electric": electric_errors,
        "magnetic": magnetic_errors,
        "charge": relative(charge, exact_charge),
        "inner_charge": relative(charge[1:3], exact_charge[1:3]),
        "tangent": float(np.linalg.norm(left - right) / tangent_scale),
        "power_closure": float(np.max(closure)),
        "complex_power_closure": float(closure[-1]),
        "continuity": relative(b @ j + 1j * omega * charge, np.abs(b) @ np.abs(j) + omega * np.abs(charge)),
        "exact_charge": exact_charge,
        "power": power,
    }


def kernel_identity_coupon(order: int = 48) -> float:
    """Independent two-cell tensor/split-triangle quadrature identity check."""
    lengths = (0.17, 0.23)
    boundaries = np.array([0.0, lengths[0], sum(lengths)])
    elements = ((boundaries[0], boundaries[1], (0, 1)), (boundaries[1], boundaries[2], (2, 3)))
    kx, k0 = 7.0, 2.0
    alpha = np.sqrt(kx * kx - k0 * k0)
    x, w = leggauss(order)
    u, w = (x + 1) / 2, w / 2
    qmat = np.zeros((4, 4))
    derivative_term = np.zeros((4, 4))
    m0 = np.zeros((4, 4))

    def add_pair(first, second, triangular=False):
        a, b, ids = first
        c, d, jds = second
        h, g = b - a, d - c
        if triangular:
            s, t = u[:, None], u[None, :]
            weight = h * h * s * w[:, None] * w[None, :]
            for left_first in (True, False):
                uu, vv = (s, s * t) if left_first else (s * t, s)
                z1, z2 = a + h * uu, a + h * vv
                p1 = np.stack((1 - uu, uu), axis=-1)
                p2 = np.stack((1 - vv, vv), axis=-1)
                green = np.exp(-alpha * np.abs(z1 - z2)) / (2 * alpha)
                for li, gi in enumerate(ids):
                    for lj, gj in enumerate(jds):
                        qmat[gi, gj] += np.sum(weight * green * p1[..., li] * p2[..., lj])
                        derivative_term[gi, gj] += np.sum(weight * green * ((-1, 1)[li] / h) * ((-1, 1)[lj] / h))
        else:
            uu, vv = u[:, None], u[None, :]
            weight = h * g * w[:, None] * w[None, :]
            z1, z2 = a + h * uu, c + g * vv
            p1 = np.stack((np.broadcast_to(1 - uu, weight.shape), np.broadcast_to(uu, weight.shape)), axis=-1)
            p2 = np.stack((np.broadcast_to(1 - vv, weight.shape), np.broadcast_to(vv, weight.shape)), axis=-1)
            green = np.exp(-alpha * np.abs(z1 - z2)) / (2 * alpha)
            for li, gi in enumerate(ids):
                for lj, gj in enumerate(jds):
                    qmat[gi, gj] += np.sum(weight * green * p1[..., li] * p2[..., lj])
                    derivative_term[gi, gj] += np.sum(weight * green * ((-1, 1)[li] / h) * ((-1, 1)[lj] / g))

    for first_index, first in enumerate(elements):
        a, b, ids = first
        h = b - a
        m0[np.ix_(ids, ids)] += h * np.array([[1 / 3, 1 / 6], [1 / 6, 1 / 3]])
        for second_index, second in enumerate(elements):
            add_pair(first, second, triangular=first_index == second_index)
    bmat = np.array([[1, 0, 0, 0], [0, -1, 1, 0], [0, 0, 0, -1]], float)
    potential = np.exp(-alpha * np.abs(boundaries[:, None] - boundaries[None, :])) / (2 * alpha)
    surface = np.zeros((3, 4))
    for surface_index, location in enumerate(boundaries):
        for a, b, ids in elements:
            h = b - a
            z = a + h * u
            phi = np.column_stack((1 - u, u))
            derivative = -0.5 * np.sign(location - z) * np.exp(-alpha * np.abs(location - z))
            surface[surface_index, ids] += (h * w * derivative) @ phi
    direct = qmat + derivative_term / (kx * kx)
    identity = (m0 + k0 * k0 * qmat + bmat.T @ potential @ bmat - bmat.T @ surface - surface.T @ bmat) / (kx * kx)
    return relative(identity, direct)


def review(output: Path) -> int:
    started = monotonic()
    require(not output.exists(), "review output must be new")
    loaded = {}
    input_rows = {}
    for name, (relative_path, expected) in PINS.items():
        path = RESEARCH / relative_path
        require(sha(path) == expected, f"hash:{name}")
        input_rows[name] = {"path": str(path), "sha256": expected}
        if path.suffix == ".json":
            loaded[name] = json.loads(path.read_bytes())
    result = loaded["result"]
    require(result["program"] == "SPD Decap PI Evaluator" and result["version"] == "0.23.1", "program/version")
    require(result["status"] == "PASS_FOURIER_CURRENT_CHARGE_CONTROL" and result["failures"] == [], "producer status")
    require(result["script_sha256"] == PINS["driver"][1] and result["fields_sha256"] == PINS["fields"][1], "result artifact binding")
    require(result["reference_script_sha256"] == "10032e17838f0f6f0105051424679c29de3ef83acc63cdc9d8075523368ed247", "reference pin")
    require(result["source_pins"] == {name: list(PINS[name]) for name in ("source", "low", "high", "low_review", "high_review")}, "source pin map")
    require(loaded["low_review"]["status"].startswith("ACCEPT") and loaded["high_review"]["status"].startswith("ACCEPT"), "source reviews")
    require(loaded["tm_review"]["status"] == "ACCEPT_JOINT_TM_BOUNDARY_CONTROL_INDEPENDENT_REVIEW", "TM reference review")
    expected_cases = [(period, frequency) for period in (1e-3, 1e-4) for frequency in (1e3, 1e4, 1e5, 1e6, 1e7, 1e8)]
    require([(row["period_m"], row["frequency_hz"]) for row in result["cases"]] == expected_cases, "case order")

    maxima = {
        "saved_metric_reproduction_absolute": 0.0,
        "h_from_contrast_current_relative": 0.0,
        "source_normal_flux_relative": 0.0,
        "charge_continuity_relative": 0.0,
        "charge_split_relative": 0.0,
        "incident_amplitude_relative": 0.0,
        "outer_charge_relative": 0.0,
        "prescribed_h_relative": 0.0,
        "three_point_z_relative": 0.0,
        "exact_z_entry_relative": 0.0,
        "exact_material_electric_l2": 0.0,
        "exact_material_magnetic_l2": 0.0,
        "exact_inner_charge_relative": 0.0,
        "one_sided_tangential_e_relative_jump": 0.0,
        "complex_power_relative_closure": 0.0,
        "reciprocity_relative_frobenius": 0.0,
        "off_diagonal_reciprocity": 0.0,
        "magnetic_identity_reported": 0.0,
        "backward": 0.0,
        "original_backward": 0.0,
    }
    min_resistance = np.inf
    min_ratio_jump = np.inf
    worst_z = None
    with np.load(RESEARCH / PINS["fields"][0], allow_pickle=False) as fields:
        require(len(fields.files) == 12 * 7, "field key count")
        for case_index, case in enumerate(result["cases"]):
            final = case["refinement"][-1]
            prefix = f"case_{case_index:02d}_"
            required_keys = ("j", "charge", "h", "z", "primary_charge_scaled", "correction_charge_scaled", "incident_amplitudes")
            require(all(prefix + key in fields for key in required_keys), f"field keys case{case_index}")
            j, charge, h, zport, primary, correction, incident_amplitudes = (fields[prefix + key] for key in required_keys)
            n = int(final["cells_per_layer"])
            require(j.shape == h.shape == (3 * (n + 1), 2) and charge.shape == primary.shape == correction.shape == (4, 2), f"array shapes case{case_index}")
            require(zport.shape == incident_amplitudes.shape == (2, 2), f"port shapes case{case_index}")
            _, thickness, eps, sigma, gamma = materials(loaded["source"], loaded["low"], loaded["high"], case["frequency_hz"])
            omega, kx = 2 * np.pi * case["frequency_hz"], 2 * np.pi / case["period_m"]
            kappa, ratio = gamma - 1j * omega * EPS0, (gamma - 1j * omega * EPS0) / gamma
            b = boundary_operator(n)
            c, d, ratio_jumps = conforming_maps(n, ratio)
            min_ratio_jump = min(min_ratio_jump, *ratio_jumps)
            maxima["source_normal_flux_relative"] = max(maxima["source_normal_flux_relative"], *(relative(j[m * (n + 1) - 1] / ratio[m - 1], j[m * (n + 1)] / ratio[m]) for m in (1, 2)))
            require(relative(b @ c, np.zeros((4, c.shape[1]))) < 1e-14 and relative(b @ d, np.eye(4)) < 1e-13, f"C/D identities case{case_index}")
            reconstructed_h = np.repeat(gamma / (1j * kx * kappa), n + 1)[:, None] * j
            maxima["h_from_contrast_current_relative"] = max(maxima["h_from_contrast_current_relative"], relative(h, reconstructed_h))
            full_t = omega * charge
            continuity_residual = b @ j + 1j * full_t
            continuity_scale = np.abs(b) @ np.abs(j) + np.abs(full_t)
            continuity_relative = float(np.max(np.linalg.norm(continuity_residual, axis=1) /
                                               np.maximum(np.linalg.norm(continuity_scale, axis=1), np.finfo(float).tiny)))
            maxima["charge_continuity_relative"] = max(maxima["charge_continuity_relative"], continuity_relative)
            maxima["charge_split_relative"] = max(maxima["charge_split_relative"], relative(primary + correction, full_t))
            expected_incident = np.diag([ratio[0] / 2, -ratio[-1] / 2])
            expected_incident[0] += correction[0] / (2 * kx)
            expected_incident[1] -= correction[-1] / (2 * kx)
            maxima["incident_amplitude_relative"] = max(maxima["incident_amplitude_relative"], relative(incident_amplitudes, expected_incident))
            expected_outer = np.zeros((2, 2), complex)
            expected_outer[0, 0], expected_outer[1, 1] = -kx * ratio[0], -kx * ratio[-1]
            maxima["outer_charge_relative"] = max(maxima["outer_charge_relative"], relative(full_t[[0, -1]], expected_outer))
            prescribed = np.vstack((h[0], -h[-1]))
            maxima["prescribed_h_relative"] = max(maxima["prescribed_h_relative"], relative(prescribed, np.eye(2)))
            dz0, dz2 = thickness[0] / n, thickness[-1] / n
            reconstructed_z = np.vstack((-(-3 * h[0] + 4 * h[1] - h[2]) / (2 * dz0 * gamma[0]),
                                          -(3 * h[-1] - 4 * h[-2] + h[-3]) / (2 * dz2 * gamma[-1])))
            maxima["three_point_z_relative"] = max(maxima["three_point_z_relative"], relative(zport, reconstructed_z))
            q, exact_h, exact_z = exact_tm(thickness, gamma, omega, kx)
            entry_errors = np.abs(zport - exact_z) / np.maximum(np.abs(exact_z), np.finfo(float).tiny)
            entry_max = float(np.max(entry_errors))
            maxima["exact_z_entry_relative"] = max(maxima["exact_z_entry_relative"], entry_max)
            if worst_z is None or entry_max > worst_z["relative_error"]:
                location = np.unravel_index(np.argmax(entry_errors), entry_errors.shape)
                worst_z = {"case_index": case_index, "frequency_hz": case["frequency_hz"], "period_m": case["period_m"], "entry": list(map(int, location)), "relative_error": entry_max}
            independently = saved_field_metrics(j, charge, h, zport, thickness, gamma, omega, kx, q, exact_h)
            maxima["exact_material_electric_l2"] = max(maxima["exact_material_electric_l2"], max(independently["electric"]))
            maxima["exact_material_magnetic_l2"] = max(maxima["exact_material_magnetic_l2"], max(independently["magnetic"]))
            maxima["exact_inner_charge_relative"] = max(maxima["exact_inner_charge_relative"], independently["inner_charge"])
            maxima["one_sided_tangential_e_relative_jump"] = max(maxima["one_sided_tangential_e_relative_jump"], independently["tangent"])
            maxima["complex_power_relative_closure"] = max(maxima["complex_power_relative_closure"], independently["power_closure"])
            reciprocity = relative(zport, zport.T)
            maxima["reciprocity_relative_frobenius"] = max(maxima["reciprocity_relative_frobenius"], reciprocity)
            maxima["off_diagonal_reciprocity"] = max(maxima["off_diagonal_reciprocity"], float(abs(zport[0, 1] - zport[1, 0]) / (abs(zport[0, 1]) + abs(zport[1, 0]))))
            min_resistance = min(min_resistance, float(np.linalg.eigvalsh((zport + zport.conj().T) / 2).min()))
            maxima["magnetic_identity_reported"] = max(maxima["magnetic_identity_reported"], float(final["magnetic_identity_relative_difference"]))
            maxima["backward"] = max(maxima["backward"], float(final["backward"]))
            maxima["original_backward"] = max(maxima["original_backward"], float(final["original_backward"]))
            comparisons = {
                "impedance_entry_relative_error": entry_max,
                "inner_charge_relative_error": independently["inner_charge"],
                "one_sided_tangential_e_relative_jump": independently["tangent"],
                "recovered_port_poynting_relative_closure": independently["power_closure"],
                "complex_combination_poynting_relative_closure": independently["complex_power_closure"],
                "reciprocity_relative_frobenius": reciprocity,
            }
            for name, value in comparisons.items():
                maxima["saved_metric_reproduction_absolute"] = max(maxima["saved_metric_reproduction_absolute"], abs(value - float(final[name])))
            require(entry_max < 1e-3 and max(independently["electric"]) < 0.02 and independently["inner_charge"] < 1e-3, f"physical oracle gates case{case_index}")
            require(independently["tangent"] < 0.05 and independently["power_closure"] < 1e-3, f"interface/power gates case{case_index}")
            require(reciprocity < 1e-10 and min_resistance >= 0, f"reciprocity/passivity case{case_index}")
            require(max(final["backward"], final["original_backward"]) < 1e-12 and final["continuity"] < 1e-12, f"equation gates case{case_index}")
            require(final["magnetic_identity_relative_difference"] < 1e-11, f"producer identity gate case{case_index}")
            require(final["impedance_entry_relative_error"] < 0.1 * case["refinement"][0]["impedance_entry_relative_error"], f"Z refinement case{case_index}")
            require(final["one_sided_tangential_e_relative_jump"] < 0.2 * case["refinement"][0]["one_sided_tangential_e_relative_jump"], f"tangent refinement case{case_index}")

    coupon = kernel_identity_coupon()
    print(json.dumps({"review_preflight_metrics": maxima, "coupon": coupon}, allow_nan=False))
    require(coupon < 2e-12, "independent magnetic identity coupon")
    require(maxima["saved_metric_reproduction_absolute"] < 2e-11, "saved metric reproduction")
    require(maxima["h_from_contrast_current_relative"] < 2e-13 and maxima["source_normal_flux_relative"] < 2e-13, "current/H conformity")
    require(maxima["charge_continuity_relative"] < 2e-13 and maxima["charge_split_relative"] < 2e-13, "charge reconstruction")
    require(maxima["incident_amplitude_relative"] < 2e-13 and maxima["outer_charge_relative"] < 2e-13, "prescribed-H charge map")
    require(maxima["prescribed_h_relative"] < 2e-13 and maxima["three_point_z_relative"] < 2e-13, "saved port reconstruction")

    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_FOURIER_CURRENT_CHARGE_CONTROL_INDEPENDENT_REVIEW",
        "reviewer_sha256": sha(Path(__file__)),
        "inputs": input_rows,
        "case_count": len(result["cases"]),
        "metrics": {**maxima, "minimum_port_resistance_eigenvalue_ohm": min_resistance,
                    "minimum_material_ratio_jump_magnitude": min_ratio_jump,
                    "independent_kernel_identity_coupon_relative": coupon},
        "worst_impedance_entry": worst_z,
        "gates": {
            "all_hashes_and_source_reviews_bound": True,
            "saved_arrays_have_exact_case_shapes": True,
            "contrast_current_reconstructs_saved_h": True,
            "common_total_normal_flux_reconstructed": True,
            "charge_t0_tau_and_prescribed_h_reconstructed": True,
            "independent_three_point_port_z_reconstructed": True,
            "independent_tm_oracle_and_material_fields_pass": True,
            "independent_inner_charge_and_tangent_fields_pass": True,
            "independent_basis_and_complex_combination_poynting_pass": True,
            "reciprocity_and_passivity_pass": True,
            "independent_kernel_identity_coupon_pass": True,
            "reported_original_and_shifted_equations_pass": True,
            "reported_refinement_gates_pass": True,
        },
        "findings": [],
        "limitations": [
            "The interface D coordinate divides by the material contrast-ratio jump. Equal adjacent materials must be merged, and near-equal interfaces need a separately conditioned limiting basis before this discretization is general.",
            "The accepted control is one-dimensional in z with a prescribed nonzero lateral Fourier wavenumber. It does not qualify kx=0, finite lateral edges, vias, pad junctions, three-dimensional singular quadrature, a board port, or PowerSI accuracy.",
        ],
        "scope": "Accepts the frozen source-pinned TOP/DR0102/L02 Fourier current/contrast-charge Galerkin control. The independent review reconstructs material data, common total normal flux, charge and prescribed-H maps, the exact TM port/field oracle, P1 volume power for two basis drives plus [1,i], and a separate split-triangle magnetic integration-by-parts coupon. Constructed continuity identities are not counted as independent physical validation. Vacuum Green potentials own the magnetic field in this control; no internal or gap inductance is added.",
        "elapsed_s": monotonic() - started,
    }
    output.mkdir(parents=True)
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"status": review["status"], "metrics": review["metrics"], "elapsed_s": review["elapsed_s"]}, allow_nan=False))
    return 0


def self_check() -> int:
    require(kernel_identity_coupon(36) < 5e-12, "kernel identity coupon")
    ratio = np.array([0.91 - 0.02j, 0.63 + 0.01j, 0.98 - 0.03j])
    c, d, _ = conforming_maps(3, ratio)
    b = boundary_operator(3)
    require(relative(b @ c, np.zeros((4, c.shape[1]))) < 1e-14, "coupon BC")
    require(relative(b @ d, np.eye(4)) < 1e-14, "coupon BD")
    for interface in (1, 2):
        left, right = interface * 4 - 1, interface * 4
        require(relative(d[left, interface] / ratio[interface - 1], d[right, interface] / ratio[interface]) < 1e-14, "coupon total flux")
    print(json.dumps({"status": "PASS_SELF_CHECK"}))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        raise SystemExit(self_check())
    require(args.output is not None, "--output required")
    raise SystemExit(review(args.output.resolve()))
