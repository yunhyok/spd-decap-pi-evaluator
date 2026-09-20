"""Independent saved-field review of the source-bound TM interface control."""
from __future__ import annotations

import argparse
from hashlib import file_digest
import json
from pathlib import Path
from time import monotonic
import zipfile

import numpy as np
from numpy.polynomial.legendre import leggauss


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
RUN = RESEARCH / "astra-joint-tm-boundary-03"
EPS0 = 8.8541878128e-12
MU0 = 4.0e-7 * np.pi
FREQUENCIES = (1.0e3, 1.0e4, 1.0e5, 1.0e6, 1.0e7, 1.0e8)
PERIODS = (1.0e-3, 1.0e-4)
PINS = {
    "driver": (RUN / "driver-at-run.py", "10032e17838f0f6f0105051424679c29de3ef83acc63cdc9d8075523368ed247"),
    "live_driver": (ROOT / "tools/research/qualify_astra_joint_tm_boundary.py", "10032e17838f0f6f0105051424679c29de3ef83acc63cdc9d8075523368ed247"),
    "result": (RUN / "result.json", "5289b97fbcbc6f6a79303c1c7c4523f01abc341a82600dbf90c7acdd19e17e98"),
    "fields": (RUN / "fields.npz", "ca6cec38c1075386f1546d30c20c644e83090447f4eafb114ec95997415cdc86"),
    "source": (RESEARCH / "astra-native-frequency-stamps-01/source-frequency-inputs.json", "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc"),
    "low": (RESEARCH / "astra-native-low-band-stamps-01/receipt.json", "1968b9091d7eab184c2c0ad195fdc847661377484624a4ac4985d06e42216299"),
    "high": (RESEARCH / "astra-native-frequency-stamps-01/receipt.json", "4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856"),
    "low_review": (RESEARCH / "astra-native-low-band-stamps-01/independent-review.json", "1fed75a2227ffb90f937914ebf2b112614736b632b9fec053ac9876125168228"),
    "high_review": (RESEARCH / "astra-native-frequency-stamps-01/independent-review.json", "74d4476018645392c66164990ee7a44c08a09ca9594edc7df7dc95fff1ad2f94"),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def relative(actual: np.ndarray, expected: np.ndarray) -> float:
    scale = max(float(np.linalg.norm(expected)), np.finfo(float).tiny)
    return float(np.linalg.norm(actual - expected) / scale)


def source_contract() -> tuple[list[dict[str, object]], dict[float, tuple[float, float]]]:
    source = json.loads(PINS["source"][0].read_bytes())
    rows = source["stackup_layers"][:3]
    if [row["name"] for row in rows] != ["Signal$TOP", "Medium$DR0102", "Signal$L02(DGND)"]:
        raise ValueError("source layer order changed")
    if [row["thickness_um"] for row in rows] != [25.0, 30.0, 20.0]:
        raise ValueError("source thickness changed")
    if rows[0]["conductivity_s_m"] != 59_590_000.0 or rows[2]["conductivity_s_m"] != 59_590_000.0:
        raise ValueError("source copper conductivity changed")
    properties: dict[float, tuple[float, float]] = {}
    for label in ("low", "high"):
        receipt = json.loads(PINS[label][0].read_bytes())
        gap = receipt["gap_properties"][0]
        if (gap["upper_layer"], gap["lower_layer"]) != (rows[0]["name"], rows[2]["name"]):
            raise ValueError("source gap identity changed")
        for frequency, dk, df in zip(receipt["frequencies_hz"], gap["dk"], gap["df"], strict=True):
            value = (float(dk), float(df))
            if float(frequency) in properties and properties[float(frequency)] != value:
                raise ValueError("overlapping source dispersion changed")
            properties[float(frequency)] = value
    return rows, properties


def materials(
    rows: list[dict[str, object]], properties: dict[float, tuple[float, float]], frequency: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    epsilon: list[complex] = []
    conductivity: list[float] = []
    for index, row in enumerate(rows):
        dk, df = properties[frequency] if index == 1 else (float(row["dk"]), float(row["df"]))
        epsilon.append(EPS0 * dk * (1.0 - 1.0j * df))
        conductivity.append(float(row["conductivity_s_m"] or 0.0))
    thickness = np.array([float(row["thickness_um"]) * 1.0e-6 for row in rows])
    eps = np.asarray(epsilon, dtype=np.complex128)
    sigma = np.asarray(conductivity, dtype=np.float64)
    omega = 2.0 * np.pi * frequency
    return thickness, eps, sigma, sigma + 1.0j * omega * eps


def local_admittance(q: complex, gamma: complex, thickness: float) -> np.ndarray:
    """Independent face-admittance form, rather than the producer's H endpoint stiffness."""
    half = 0.5 * q * thickness
    tangent = np.tanh(half)
    characteristic = q / gamma
    common_y = tangent / characteristic
    differential_y = 1.0 / (characteristic * tangent)
    diagonal = 0.5 * (common_y + differential_y)
    off = 0.5 * (common_y - differential_y)
    return np.array(((diagonal, off), (off, diagonal)), dtype=np.complex128)


def admittance_oracle(
    thickness: np.ndarray, gamma: np.ndarray, omega: float, kx: float
) -> tuple[np.ndarray, np.ndarray, float]:
    q = np.sqrt(kx * kx + 1.0j * omega * MU0 * gamma)
    blocks = [local_admittance(q[i], gamma[i], thickness[i]) for i in range(3)]
    assembled = np.zeros((4, 4), dtype=np.complex128)
    for index, block in enumerate(blocks):
        assembled[index : index + 2, index : index + 2] += block
    interior = np.array((1, 2))
    boundary = np.array((0, 3))
    ii = assembled[np.ix_(interior, interior)]
    ib = assembled[np.ix_(interior, boundary)]
    bi = assembled[np.ix_(boundary, interior)]
    bb = assembled[np.ix_(boundary, boundary)]
    row_scale = np.max(np.abs(ii), axis=1)
    lift = -np.linalg.solve(ii / row_scale[:, None], ib / row_scale[:, None])
    port_y = bb + bi @ lift
    port_z = np.linalg.inv(port_y)
    electric = np.zeros((4, 2), dtype=np.complex128)
    electric[boundary] = port_z
    electric[interior] = lift @ port_z
    h_candidates: list[list[np.ndarray]] = [[], [], [], []]
    for index, block in enumerate(blocks):
        currents = block @ electric[index : index + 2]
        h_candidates[index].append(currents[0])
        h_candidates[index + 1].append(-currents[1])
    h = np.empty((4, 2), dtype=np.complex128)
    for index, candidates in enumerate(h_candidates):
        h[index] = np.mean(candidates, axis=0)
    residual = assembled @ electric
    denominator = np.abs(assembled) @ np.abs(electric)
    backward = float(
        np.max(
            np.abs(residual[interior])
            / np.maximum(denominator[interior], np.finfo(float).tiny)
        )
    )
    return port_z, h, backward


def analytic_field(
    q: np.ndarray,
    h: np.ndarray,
    gamma: np.ndarray,
    thickness: np.ndarray,
    layer: int,
    z: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    wave = q[layer]
    depth = thickness[layer]
    left, right = h[layer], h[layer + 1]
    field_h = (
        np.sinh(wave * (depth - z))[:, None] * left
        + np.sinh(wave * z)[:, None] * right
    ) / np.sinh(wave * depth)
    derivative = wave * (
        -np.cosh(wave * (depth - z))[:, None] * left
        + np.cosh(wave * z)[:, None] * right
    ) / np.sinh(wave * depth)
    return field_h, -derivative / gamma[layer]


def analytic_power(
    thickness: np.ndarray,
    eps: np.ndarray,
    sigma: np.ndarray,
    gamma: np.ndarray,
    omega: float,
    kx: float,
    h: np.ndarray,
) -> np.ndarray:
    points, weights = leggauss(48)
    q = np.sqrt(kx * kx + 1.0j * omega * MU0 * gamma)
    result = np.zeros((3, h.shape[1]), dtype=np.complex128)
    for layer, depth in enumerate(thickness):
        z = 0.5 * depth * (points + 1.0)
        weight = 0.5 * depth * weights
        field_h, ex = analytic_field(q, h, gamma, thickness, layer, z)
        ez = 1.0j * kx * field_h / gamma[layer]
        electric = np.abs(ex) ** 2 + np.abs(ez) ** 2
        result[0] += (sigma[layer] - omega * eps[layer].imag) * np.sum(weight[:, None] * electric, axis=0)
        result[1] += 1.0j * omega * MU0 * np.sum(weight[:, None] * np.abs(field_h) ** 2, axis=0)
        result[2] -= 1.0j * omega * eps[layer].real * np.sum(weight[:, None] * electric, axis=0)
    return result


def interface_charge(
    h: np.ndarray, eps: np.ndarray, gamma: np.ndarray, omega: float, kx: float
) -> tuple[np.ndarray, np.ndarray, float]:
    free: list[np.ndarray] = []
    contrast: list[np.ndarray] = []
    identity = 0.0
    for interface in (1, 2):
        ez = 1.0j * kx * h[interface][None, :] / gamma[interface - 1 : interface + 1, None]
        e = eps[interface - 1 : interface + 1, None]
        g = gamma[interface - 1 : interface + 1, None]
        rho_free = e[1] * ez[1] - e[0] * ez[0]
        rho_contrast = EPS0 * (ez[1] - ez[0])
        current_contrast = (g - 1.0j * omega * EPS0) * ez
        scale = np.sum(np.abs(current_contrast), axis=0) + omega * np.abs(rho_contrast)
        identity = max(
            identity,
            float(np.max(np.abs(current_contrast[1] - current_contrast[0] + 1.0j * omega * rho_contrast) / np.maximum(scale, np.finfo(float).tiny))),
        )
        free.append(rho_free)
        contrast.append(rho_contrast)
    return np.asarray(free), np.asarray(contrast), identity


def fem_reconstruction(
    thickness: np.ndarray,
    eps: np.ndarray,
    sigma: np.ndarray,
    gamma: np.ndarray,
    omega: float,
    kx: float,
    h: np.ndarray,
) -> tuple[np.ndarray, float, np.ndarray, float]:
    n = (len(h) - 1) // 3
    lengths = np.repeat(thickness / n, n)
    layer = np.repeat(np.arange(3), n)
    a = 1.0 / gamma[layer]
    b = kx * kx * a + 1.0j * omega * MU0
    local_diag = a / lengths + b * lengths / 3.0
    off = -a / lengths + b * lengths / 6.0
    diagonal = np.r_[local_diag, 0.0] + np.r_[0.0, local_diag]
    reaction = diagonal[:, None] * h
    reaction[:-1] += off[:, None] * h[1:]
    reaction[1:] += off[:, None] * h[:-1]
    denominator = np.abs(diagonal[:, None] * h)
    denominator[:-1] += np.abs(off[:, None] * h[1:])
    denominator[1:] += np.abs(off[:, None] * h[:-1])
    backward = float(np.max(np.abs(reaction[1:-1]) / np.maximum(denominator[1:-1], np.finfo(float).tiny)))
    z = np.vstack((reaction[0], -reaction[-1]))

    points, weights = leggauss(6)
    power = np.zeros((3, h.shape[1]), dtype=np.complex128)
    for material, depth in enumerate(thickness):
        cell_depth = depth / n
        for cell in range(n):
            left, right = h[material * n + cell : material * n + cell + 2]
            shape = 0.5 * (points + 1.0)
            field_h = (1.0 - shape)[:, None] * left + shape[:, None] * right
            ex = np.broadcast_to(-(right - left) / (cell_depth * gamma[material]), field_h.shape)
            ez = 1.0j * kx * field_h / gamma[material]
            electric = np.abs(ex) ** 2 + np.abs(ez) ** 2
            weight = 0.5 * cell_depth * weights
            power[0] += (sigma[material] - omega * eps[material].imag) * np.sum(weight[:, None] * electric, axis=0)
            power[1] += 1.0j * omega * MU0 * np.sum(weight[:, None] * np.abs(field_h) ** 2, axis=0)
            power[2] -= 1.0j * omega * eps[material].real * np.sum(weight[:, None] * electric, axis=0)
    return z, backward, power, float(np.max(np.abs(q_times_cell(thickness, gamma, omega, kx, n))))


def q_times_cell(thickness: np.ndarray, gamma: np.ndarray, omega: float, kx: float, n: int) -> np.ndarray:
    return np.sqrt(kx * kx + 1.0j * omega * MU0 * gamma) * thickness / n


def tangential_jump(
    thickness: np.ndarray,
    gamma: np.ndarray,
    omega: float,
    kx: float,
    exact_h: np.ndarray,
    fem_h: np.ndarray,
) -> float:
    q = np.sqrt(kx * kx + 1.0j * omega * MU0 * gamma)
    n = (len(fem_h) - 1) // 3
    left_fields, right_fields, reference = [], [], []
    for interface in (1, 2):
        left = analytic_field(q, exact_h, gamma, thickness, interface - 1, np.array([thickness[interface - 1]]))[1][0]
        right = analytic_field(q, exact_h, gamma, thickness, interface, np.array([0.0]))[1][0]
        reference.append(0.5 * (left + right))
        left_fields.append(-(fem_h[interface * n] - fem_h[interface * n - 1]) / (thickness[interface - 1] / n * gamma[interface - 1]))
        right_fields.append(-(fem_h[interface * n + 1] - fem_h[interface * n]) / (thickness[interface] / n * gamma[interface]))
    left_array, right_array, reference_array = map(np.asarray, (left_fields, right_fields, reference))
    scale = max(float(np.linalg.norm(reference_array)), 0.5 * float(np.linalg.norm(left_array) + np.linalg.norm(right_array)), np.finfo(float).tiny)
    return float(np.linalg.norm(left_array - right_array) / scale)


def run(output: Path) -> dict[str, object]:
    started = monotonic()
    for label, (path, expected) in PINS.items():
        if sha256(path) != expected:
            raise ValueError(f"{label} SHA-256 changed")
    result = json.loads(PINS["result"][0].read_bytes())
    if result.get("program") != PROGRAM or result.get("version") != VERSION:
        raise ValueError("program/version changed")
    if result.get("status") != "PASS_JOINT_TM_BOUNDARY_CONTROL" or result.get("failures") != []:
        raise ValueError("producer result is not a clean PASS")
    if result.get("script_sha256") != PINS["driver"][1] or result.get("fields_sha256") != PINS["fields"][1]:
        raise ValueError("producer output binding changed")
    for label in ("source", "low", "high", "low_review", "high_review"):
        if result["inputs"][label]["sha256"] != PINS[label][1]:
            raise ValueError(f"producer {label} binding changed")
    if "algebraic post-elimination identities" not in result.get("scope", ""):
        raise ValueError("producer overstates its current/charge test scope")

    rows, properties = source_contract()
    expected_cases = [(period, frequency) for period in PERIODS for frequency in FREQUENCIES]
    if [(case["controlled_period_m"], case["frequency_hz"]) for case in result["cases"]] != expected_cases:
        raise ValueError("case order changed")
    with zipfile.ZipFile(PINS["fields"][0]) as package:
        if package.testzip() is not None:
            raise ValueError("fields NPZ CRC failed")

    maxima = {
        "admittance_oracle_z_relative": 0.0,
        "admittance_oracle_h_relative": 0.0,
        "admittance_interface_current_backward": 0.0,
        "saved_exact_power_relative": 0.0,
        "saved_exact_charge_relative": 0.0,
        "exact_complex_combination_power_closure": 0.0,
        "fem_reaction_z_relative": 0.0,
        "fem_unscaled_backward": 0.0,
        "fem_saved_power_relative": 0.0,
        "fem_complex_combination_power_closure": 0.0,
        "fem_tangential_jump_metric_difference": 0.0,
        "reported_final_z_entry_relative": 0.0,
        "reported_final_material_field_relative": 0.0,
        "reported_final_charge_relative": 0.0,
        "reported_final_power_closure": 0.0,
        "reported_final_tangential_jump": 0.0,
        "reported_max_q_cell": 0.0,
        "reported_max_condition": 0.0,
        "reported_min_resistance_eigenvalue": float("inf"),
    }
    combination = np.array((1.0 + 0.0j, 0.31 + 0.73j))
    with np.load(PINS["fields"][0], allow_pickle=False) as archive:
        expected_keys = {
            f"case_{index:02d}_{suffix}"
            for index in range(len(expected_cases))
            for suffix in (
                "exact_h", "fem_h", "exact_z", "fem_z", "free_interface_charge",
                "contrast_interface_charge", "exact_power_terms", "fem_power_terms",
            )
        }
        if set(archive.files) != expected_keys:
            raise ValueError("fields NPZ layout changed")
        for index, case in enumerate(result["cases"]):
            frequency = float(case["frequency_hz"])
            period = float(case["controlled_period_m"])
            omega = 2.0 * np.pi * frequency
            kx = 2.0 * np.pi / period
            thickness, eps, sigma, gamma = materials(rows, properties, frequency)
            prefix = f"case_{index:02d}_"
            exact_h = np.asarray(archive[prefix + "exact_h"], dtype=np.complex128)
            fem_h = np.asarray(archive[prefix + "fem_h"], dtype=np.complex128)
            exact_z = np.asarray(archive[prefix + "exact_z"], dtype=np.complex128)
            fem_z = np.asarray(archive[prefix + "fem_z"], dtype=np.complex128)
            if exact_h.shape != (4, 2) or fem_h.shape != (385, 2) or exact_z.shape != (2, 2) or fem_z.shape != (2, 2):
                raise ValueError(f"case {index} saved array shape changed")
            if not np.array_equal(exact_h[[0, 3]], np.array(((1.0, 0.0), (0.0, -1.0)))):
                raise ValueError(f"case {index} exact port basis changed")
            if not np.array_equal(fem_h[[0, -1]], exact_h[[0, 3]]):
                raise ValueError(f"case {index} FEM port basis changed")

            oracle_z, oracle_h, oracle_interface = admittance_oracle(thickness, gamma, omega, kx)
            maxima["admittance_oracle_z_relative"] = max(maxima["admittance_oracle_z_relative"], relative(oracle_z, exact_z))
            maxima["admittance_oracle_h_relative"] = max(maxima["admittance_oracle_h_relative"], relative(oracle_h, exact_h))
            maxima["admittance_interface_current_backward"] = max(maxima["admittance_interface_current_backward"], oracle_interface)

            exact_power = analytic_power(thickness, eps, sigma, gamma, omega, kx, exact_h)
            saved_exact_power = np.asarray(archive[prefix + "exact_power_terms"], dtype=np.complex128)
            maxima["saved_exact_power_relative"] = max(maxima["saved_exact_power_relative"], relative(exact_power, saved_exact_power))
            free, contrast, identity = interface_charge(exact_h, eps, gamma, omega, kx)
            maxima["saved_exact_charge_relative"] = max(
                maxima["saved_exact_charge_relative"],
                relative(free, archive[prefix + "free_interface_charge"]),
                relative(contrast, archive[prefix + "contrast_interface_charge"]),
                identity,
            )
            combined_h = exact_h @ combination[:, None]
            combined_power = analytic_power(thickness, eps, sigma, gamma, omega, kx, combined_h).sum()
            combined_pin = np.vdot(combination, exact_z @ combination)
            combined_scale = abs(combined_pin) + np.sum(np.abs(analytic_power(thickness, eps, sigma, gamma, omega, kx, combined_h)))
            maxima["exact_complex_combination_power_closure"] = max(
                maxima["exact_complex_combination_power_closure"],
                float(abs(combined_pin - combined_power) / max(combined_scale, np.finfo(float).tiny)),
            )

            reconstructed_z, backward, reconstructed_power, q_cell = fem_reconstruction(
                thickness, eps, sigma, gamma, omega, kx, fem_h
            )
            maxima["fem_reaction_z_relative"] = max(
                maxima["fem_reaction_z_relative"], relative(reconstructed_z, fem_z)
            )
            maxima["fem_unscaled_backward"] = max(maxima["fem_unscaled_backward"], backward)
            maxima["fem_saved_power_relative"] = max(
                maxima["fem_saved_power_relative"],
                relative(reconstructed_power, archive[prefix + "fem_power_terms"]),
            )
            combined_fem_power = (reconstructed_power @ np.abs(combination) ** 2).sum()
            # Cross terms require direct integration, so reconstruct the combined field itself.
            _, _, combined_terms, _ = fem_reconstruction(
                thickness, eps, sigma, gamma, omega, kx, fem_h @ combination[:, None]
            )
            combined_fem_power = combined_terms.sum()
            combined_fem_pin = np.vdot(combination, fem_z @ combination)
            combined_fem_scale = abs(combined_fem_pin) + np.sum(np.abs(combined_terms))
            maxima["fem_complex_combination_power_closure"] = max(
                maxima["fem_complex_combination_power_closure"],
                float(abs(combined_fem_pin - combined_fem_power) / max(combined_fem_scale, np.finfo(float).tiny)),
            )
            tangent = tangential_jump(thickness, gamma, omega, kx, exact_h, fem_h)
            reported = case["refinement"][-1]
            maxima["fem_tangential_jump_metric_difference"] = max(
                maxima["fem_tangential_jump_metric_difference"],
                abs(tangent - float(reported["one_sided_tangential_e_relative_jump"])),
            )
            maxima["reported_final_z_entry_relative"] = max(maxima["reported_final_z_entry_relative"], float(reported["impedance_component_relative_error"]))
            maxima["reported_final_material_field_relative"] = max(maxima["reported_final_material_field_relative"], max(float(value) for value in reported["electric_field_relative_l2_by_material"]))
            maxima["reported_final_charge_relative"] = max(maxima["reported_final_charge_relative"], float(reported["free_charge_relative_error"]), float(reported["contrast_charge_relative_error"]))
            maxima["reported_final_power_closure"] = max(maxima["reported_final_power_closure"], float(reported["power_relative_closure"]))
            maxima["reported_final_tangential_jump"] = max(maxima["reported_final_tangential_jump"], float(reported["one_sided_tangential_e_relative_jump"]))
            maxima["reported_max_q_cell"] = max(maxima["reported_max_q_cell"], q_cell, float(reported["maximum_abs_q_times_cell"]))
            maxima["reported_max_condition"] = max(maxima["reported_max_condition"], float(case["exact_interface_condition"]), float(reported["condensed_interface_condition"]))
            maxima["reported_min_resistance_eigenvalue"] = min(maxima["reported_min_resistance_eigenvalue"], float(reported["minimum_port_resistance_eigenvalue"]))
            coarse = case["refinement"][0]
            previous = case["refinement"][-2]
            if not (
                reported["one_sided_tangential_e_relative_jump"] < 0.2 * coarse["one_sided_tangential_e_relative_jump"]
                and reported["one_sided_tangential_e_relative_jump"] <= previous["one_sided_tangential_e_relative_jump"]
            ):
                raise ValueError(f"case {index} tangential field did not refine")

    gates = {
        "all_hashes_and_source_reviews_bound": True,
        "producer_scope_labels_charge_residual_as_identity": True,
        "independent_face_admittance_z": maxima["admittance_oracle_z_relative"] < 2.0e-11,
        "independent_face_admittance_h": maxima["admittance_oracle_h_relative"] < 2.0e-11,
        "independent_face_current_continuity": maxima["admittance_interface_current_backward"] < 2.0e-11,
        "independent_48point_power_matches_saved": maxima["saved_exact_power_relative"] < 2.0e-12,
        "independent_interface_charge_matches_saved": maxima["saved_exact_charge_relative"] < 2.0e-12,
        "analytic_complex_combination_poynting": maxima["exact_complex_combination_power_closure"] < 2.0e-10,
        "saved_fem_solves_original_uncondensed_equations": maxima["fem_unscaled_backward"] < 1.0e-12,
        "saved_fem_boundary_reaction_matches": maxima["fem_reaction_z_relative"] < 1.0e-12,
        "independent_fem_power_matches_saved": maxima["fem_saved_power_relative"] < 2.0e-12,
        "fem_complex_combination_poynting": maxima["fem_complex_combination_power_closure"] < 2.0e-10,
        "tangential_jump_metric_reproduced": maxima["fem_tangential_jump_metric_difference"] < 1.0e-12,
        "reported_original_gates_remain_passed": (
            maxima["reported_final_z_entry_relative"] < 1.0e-3
            and maxima["reported_final_material_field_relative"] < 2.0e-2
            and maxima["reported_final_charge_relative"] < 1.0e-3
            and maxima["reported_final_power_closure"] < 1.0e-9
            and maxima["reported_final_tangential_jump"] < 5.0e-2
            and maxima["reported_min_resistance_eigenvalue"] >= 0.0
        ),
    }
    if not all(gates.values()):
        failed = [name for name, passed in gates.items() if not passed]
        raise ValueError(f"independent joint-boundary gates failed: {failed}; metrics={maxima}")

    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_JOINT_TM_BOUNDARY_CONTROL_INDEPENDENT_REVIEW",
        "reviewer_sha256": sha256(Path(__file__)),
        "inputs": {label: {"path": str(path), "sha256": expected} for label, (path, expected) in PINS.items()},
        "source_geometry": {
            "layers": [row["name"] for row in rows],
            "thickness_um": [row["thickness_um"] for row in rows],
            "copper_conductivity_s_per_m": 59_590_000.0,
            "fourier_periods_m": list(PERIODS),
            "frequencies_hz": list(FREQUENCIES),
        },
        "metrics": maxima,
        "gates": gates,
        "findings": [],
        "scope": "Accepts the saved asymmetric TOP/DR0102/L02 periodic-TM Maxwell boundary control and its condensed numerical realization. An independently assembled local face-admittance oracle, 48-point volume Poynting integral, interface charges, and the saved final P1 fields were checked without calling or rerunning the producer. This is not a mixed-current/charge VIE, finite-edge/via/junction control, board-port result, global reference, or PowerSI accuracy evidence.",
        "elapsed_s": monotonic() - started,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return review


def main() -> None:
    parser = argparse.ArgumentParser(description=PROGRAM)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review = run(args.output.resolve())
    print(json.dumps({"status": review["status"], "metrics": review["metrics"], "elapsed_s": review["elapsed_s"]}, allow_nan=False))


if __name__ == "__main__":
    main()
