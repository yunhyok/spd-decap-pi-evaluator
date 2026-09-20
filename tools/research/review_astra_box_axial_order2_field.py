"""SPD Decap PI Evaluator v0.23.1 saved 136-current q10/q14 review.

Rebuild the saved systems and physical response from frozen arrays.  This does
not solve a system or repeat Green/radiation quadrature.
"""
from hashlib import sha256
from pathlib import Path
import json

import numpy as np

import diagnose_astra_constant_current_3d_field as old


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/diagnose_astra_constant_current_3d_field.py":
        "2cd3eeff4071297fede205c91bccac31e25768b6eecb97fa7f4e2ca2aa610a95",
    "tools/research/diagnose_astra_box_axial_order2_field.py":
        "4b651fdfcf3c044c955db5813b54c3ae32607bbc30d809cfe116f05b0fed886c",
    "tools/research/qualify_astra_box_axial_order2_green.py":
        "61502577cb4b3e8738f7e09b5c975229c5c8e4528ce6a8950d46610d31779050",
    "outputs/research/astra-box-axial-order2-space-02/result.json":
        "d9e0ce8a6d716a5779ff05c655e88da053f71c7aa575507d62b1030f165f0b1e",
    "outputs/research/astra-box-axial-order2-space-02/space.npz":
        "bec9ac376c8373fc89cefa222c74aa10a9b1bf400b93195f1687934761b842d7",
    "outputs/research/astra-box-axial-order2-cross-01/result.json":
        "cd2f2aa2653f90c95787fc4269b3857e2525791526e041f185acae0224afe2c1",
    "outputs/research/astra-box-axial-order2-cross-01/cross-q10.npz":
        "99b4d510ca28ca963e8cca77992dd6b576af4699e38a114630f769088ebac686",
    "outputs/research/astra-box-axial-order2-cross-01/cross-q14.npz":
        "07b268d0b621eb9fbfef78f113ddd363f4740a374861abd9c0e967d6e3723ec3",
    "tools/research/qualify_astra_box_xy_symmetry_enrichment.py":
        "425c59ccd4a014a58994a8581fe3292836bc909b657b44908ff777c91234bfa5",
    "outputs/research/astra-box-xy-symmetry-enrichment-03/result.json":
        "c49b620eb5a82876e27e4e15854c78bb5fbd581d3cf7915bf0af9bfdf7cfe285",
    "outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz":
        "7b62b3eadd299f949988e02394ded0726cb662764821e95088f1b114d87c22d7",
    "outputs/research/astra-box-xy-symmetry-enrichment-03/kernels-q10.npz":
        "d1a7eb9bdd6c24660a1939fc445b5cadf6b0efbae97bdd153f6e4d416630a6ed",
    "outputs/research/astra-box-xy-symmetry-enrichment-03/kernels-q14.npz":
        "54356b7dfa99b098c4144be671d90a9d2c68c69085741b4004108d4b542e8d28",
    "outputs/research/astra-box-axial-order2-q10-field-01/result.json":
        "1402c4824750943c71b5b2ee6591633a9e34e1f6b5db5d52cffeef9b2fd60dc8",
    "outputs/research/astra-box-axial-order2-q10-field-01/fields.npz":
        "596153e973f8adadd6e070ef4a574fbe852649b0d485ca7c23c0fdff3c9560c5",
    "outputs/research/astra-box-axial-order2-q14-field-01/result.json":
        "fd70c918438b901b48fec7d4f7e0dadb325c868ad7295eafd445844884820a3d",
    "outputs/research/astra-box-axial-order2-q14-field-01/fields.npz":
        "e63a46a121a7f065bb1b69ea982568a644bbd762828fb50e6d701450b68c696e",
    "outputs/research/astra-box-xy-q14-field-01/result.json":
        "e1214855b42564c446002036543cd8801b04c969e5512d256a8bbf540649f2ba",
    "outputs/research/astra-box-xy-q14-field-01/fields.npz":
        "55e4409d0ca89f21287e331133cf24bf32701f03584d7949089453bd567488cf",
    "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz":
        "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02",
}


def digest(path):
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def relative(actual, reference):
    return float(
        np.linalg.norm(actual - reference)
        / max(np.linalg.norm(reference), np.finfo(float).tiny)
    )


def componentwise_backward(matrix, solution, rhs):
    residual = matrix @ solution - rhs
    denominator = np.abs(matrix) @ np.abs(solution) + np.abs(rhs)
    return float(np.max(
        np.abs(residual) / np.maximum(denominator, np.finfo(float).tiny)
    ))


def load_npz(path):
    with np.load(path, allow_pickle=False) as saved:
        return {name: saved[name] for name in saved.files}


def main():
    for relative_path, expected in PINS.items():
        actual = digest(ROOT / relative_path)
        assert actual == expected, (relative_path, actual)

    xy_qualification = json.loads(
        (ROOT / "outputs/research/astra-box-xy-symmetry-enrichment-03/result.json")
        .read_bytes()
    )
    assert xy_qualification["status"] == "PASS_XY_MAGNETIC_ENRICHMENT"
    assert xy_qualification["frozen_charge_symmetry_qualified"] is False
    charge_covariance = xy_qualification["checks"][
        "charge_energy_mass_scaled_covariance_relative"
    ]
    assert charge_covariance > 1e-10
    qualification = json.loads(
        (ROOT / "outputs/research/astra-box-axial-order2-space-02/result.json")
        .read_bytes()
    )
    assert qualification["status"] == "PASS_BOX_AXIAL_ORDER2_MASS_SPACE"
    assert qualification["space_sha256"] == PINS[
        "outputs/research/astra-box-axial-order2-space-02/space.npz"
    ]

    cross_qualification = json.loads(
        (ROOT / "outputs/research/astra-box-axial-order2-cross-01/result.json")
        .read_bytes()
    )
    assert cross_qualification["status"] == "PASS_AXIAL_ORDER2_RT0_CROSS"

    results = {}
    fields = {}
    kernels = {}
    expected_artifacts = {
        "q10": {
            "result": PINS[
                "outputs/research/astra-box-axial-order2-q10-field-01/result.json"
            ],
            "fields": PINS[
                "outputs/research/astra-box-axial-order2-q10-field-01/fields.npz"
            ],
            "kernels": PINS[
                "outputs/research/astra-box-axial-order2-cross-01/cross-q10.npz"
            ],
        },
        "q14": {
            "result": PINS[
                "outputs/research/astra-box-axial-order2-q14-field-01/result.json"
            ],
            "fields": PINS[
                "outputs/research/astra-box-axial-order2-q14-field-01/fields.npz"
            ],
            "kernels": PINS[
                "outputs/research/astra-box-axial-order2-cross-01/cross-q14.npz"
            ],
        },
    }
    for label in ("q10", "q14"):
        base = ROOT / f"outputs/research/astra-box-axial-order2-{label}-field-01"
        results[label] = json.loads((base / "result.json").read_bytes())
        fields[label] = load_npz(base / "fields.npz")
        kernels[label] = load_npz(
            ROOT / f"outputs/research/astra-box-axial-order2-cross-01/cross-{label}.npz"
        )
        result = results[label]
        expected = expected_artifacts[label]
        assert result["status"] == "COMPLETE_BOX_AXIAL_ORDER2_FIELD"
        assert result["script_sha256"] == PINS[
            "tools/research/diagnose_astra_box_axial_order2_field.py"
        ]
        assert result["fields_sha256"] == expected["fields"]
        assert result["space_sha256"] == qualification["space_sha256"]
        assert result["kernels_sha256"] == expected["kernels"]
        assert result["higher_helper_sha256"] == PINS[
            "tools/research/qualify_astra_box_axial_order2_green.py"
        ]
        assert result["frozen_charge_symmetry_qualified"] is False

    space = load_npz(
        ROOT / "outputs/research/astra-box-axial-order2-space-02/space.npz"
    )
    mass = space["mass"]
    order = space["coordinate_order"]
    divergence_range = space["boundary_divergence_range"]
    moment_rhs = space["uniform_curl_rhs"]
    expected_order = np.r_[np.arange(77), np.arange(124, 136), np.arange(77, 124)]
    assert mass.shape == (136, 136)
    assert np.array_equal(order, expected_order)
    assert np.array_equal(fields["q10"]["mass"], mass)
    assert np.array_equal(fields["q14"]["mass"], mass)
    assert np.array_equal(fields["q10"]["coordinate_order"], order)
    assert np.array_equal(fields["q14"]["coordinate_order"], order)
    assert np.array_equal(kernels["q10"]["coordinate_order"], order)
    assert np.array_equal(kernels["q14"]["coordinate_order"], order)
    assert np.array_equal(
        kernels["q10"]["frequencies_hz"], kernels["q14"]["frequencies_hz"]
    )
    frequencies = kernels["q14"]["frequencies_hz"]
    split = 89
    old_indices = np.argsort(order)[:124]
    new_indices = np.arange(77, 89)

    old_kernel = load_npz(
        ROOT / "outputs/research/astra-box-xy-symmetry-enrichment-03/kernels-q14.npz"
    )
    old_block_checks = {}
    for label in ("q10", "q14"):
        old_block_checks[f"{label}_static_exact"] = bool(np.array_equal(
            kernels[label]["static_green"][np.ix_(old_indices, old_indices)],
            old_kernel["static_green"],
        ))
        old_block_checks[f"{label}_tails_exact"] = bool(np.array_equal(
            kernels[label]["real_retarded_tail"][:, old_indices[:, None], old_indices],
            old_kernel["real_retarded_tail"],
        ))

    static_scale = np.sqrt(np.diag(kernels["q14"]["static_green"]))
    static_delta = np.abs(
        kernels["q10"]["static_green"][:, new_indices]
        - kernels["q14"]["static_green"][:, new_indices]
    ) / (static_scale[:, None] * static_scale[new_indices][None, :])
    kernel_checks = {
        **old_block_checks,
        "q10_q14_new_static_diagonal_scaled_max": float(np.max(static_delta)),
        "q10_q14_full_real_tails_relative": relative(
            kernels["q10"]["real_retarded_tail"],
            kernels["q14"]["real_retarded_tail"],
        ),
    }

    with np.load(
        ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz",
        allow_pickle=False,
    ) as saved:
        tetrahedra = saved["tetrahedra_m"]
        triangles = saved["triangles_m"]
        boundary, _, base_q, _, _ = old.frame(tetrahedra, triangles)
        scalar_indices = 48 + boundary
        scalar_static = saved["static_scalar_per_m"][
            np.ix_(scalar_indices, scalar_indices)
        ]
        scalar_tails = saved["scalar_tail_per_m"][
            :, scalar_indices[:, None], scalar_indices
        ].real
    assert np.array_equal(divergence_range, base_q)

    with np.load(
        ROOT / "outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz",
        allow_pickle=False,
    ) as saved:
        old_moment_rhs = saved["uniform_curl_rhs"]
    baseline_fields = load_npz(
        ROOT / "outputs/research/astra-box-xy-q14-field-01/fields.npz"
    )
    baseline_result = json.loads(
        (ROOT / "outputs/research/astra-box-xy-q14-field-01/result.json")
        .read_bytes()
    )

    rows, properties = old.source.source_inputs()
    drives = np.array([[1, 0, 1], [0, 1, 1j]], complex)
    cases = []
    for index, frequency in enumerate(frequencies):
        omega = 2 * np.pi * frequency
        _, _, _, gamma = old.source.materials(rows, properties, frequency)
        kappa = gamma[0] - 1j * omega * old.source.EPS0
        scalar = (
            divergence_range.T
            @ (scalar_static + scalar_tails[index])
            @ divergence_range
            / (4 * np.pi * old.source.EPS0)
        )
        rebuilt = {}
        for label in ("q10", "q14"):
            saved = fields[label]
            solution = saved[f"case_{index:02d}_scaled_solution"]
            modal = saved[f"case_{index:02d}_modal_current"]
            rhs = saved[f"case_{index:02d}_incident_rhs"]
            radiation_matrix = saved[f"case_{index:02d}_radiation_matrix"]
            system = (
                mass / kappa
                + 1j * omega * 1e-7
                * (
                    kernels[label]["static_green"]
                    + kernels[label]["real_retarded_tail"][index]
                )
                + radiation_matrix
            )
            system[:, split:] *= omega
            system[split:, split:] += scalar / 1j
            expected_modal = solution.copy()
            expected_modal[split:] *= omega
            charge = 1j * divergence_range @ solution[split:]
            reaction = rhs.T @ modal
            dipole = moment_rhs.T @ modal
            currents = modal @ drives
            absorption = (
                (1 / kappa).real
                * np.diag(currents.conj().T @ mass @ currents).real
            )
            radiation = np.diag(
                currents.conj().T @ radiation_matrix @ currents
            ).real
            extinction = np.real(np.sum(
                currents.conj() * (rhs @ drives), axis=0
            ))
            rebuilt[label] = {
                "modal": modal,
                "charge": charge,
                "reaction": reaction,
                "dipole": dipole,
                "absorption": absorption,
                "radiation": radiation,
                "extinction": extinction,
                "backward": componentwise_backward(system, solution, rhs),
                "modal_scale_relative": relative(modal, expected_modal),
                "saved_charge_relative": relative(
                    saved[f"case_{index:02d}_boundary_charge"], charge
                ),
                "saved_reaction_relative": relative(
                    saved[f"case_{index:02d}_reaction"], reaction
                ),
                "saved_dipole_relative": relative(
                    saved[f"case_{index:02d}_magnetic_dipole_am2"], dipole
                ),
                "power_relative": float(np.max(
                    np.abs(extinction - absorption - radiation)
                    / np.maximum(
                        np.abs(extinction) + np.abs(absorption) + np.abs(radiation),
                        np.finfo(float).tiny,
                    )
                )),
            }

        q10 = rebuilt["q10"]
        q14 = rebuilt["q14"]
        old_modal = np.vstack((
            baseline_fields[f"case_{index:02d}_modal_current"],
            np.zeros((12, 2)),
        ))[order]
        modal_delta = q14["modal"] - old_modal
        current_change = np.sqrt(
            np.diag(modal_delta.conj().T @ mass @ modal_delta).real
            / np.diag(q14["modal"].conj().T @ mass @ q14["modal"]).real
        )
        old_loss = np.asarray(baseline_result["cases"][index]["absorption_w"])
        absorption_change = (q14["absorption"] - old_loss) / q14["absorption"]
        old_dipole = old_moment_rhs.T @ baseline_fields[
            f"case_{index:02d}_modal_current"
        ]
        main = q14["dipole"][[1, 0], [0, 1]]
        old_main = old_dipole[[1, 0], [0, 1]]
        dipole_change = np.abs(main - old_main) / np.abs(main)
        report = results["q14"]["cases"][index]
        report_dipole = np.asarray([
            complex(real, imag) for real, imag in report["magnetic_dipole_main_am2"]
        ])
        q10_q14_delta = q10["modal"] - q14["modal"]
        case = {
            "frequency_hz": float(frequency),
            "q14_componentwise_backward": q14["backward"],
            "q14_modal_scale_relative": q14["modal_scale_relative"],
            "q14_saved_charge_relative": q14["saved_charge_relative"],
            "q14_saved_reaction_relative": q14["saved_reaction_relative"],
            "q14_saved_dipole_relative": q14["saved_dipole_relative"],
            "q14_power_relative": q14["power_relative"],
            "q14_reported_absorption_relative": relative(
                np.asarray(report["absorption_w"]), q14["absorption"]
            ),
            "q14_reported_extinction_relative": relative(
                np.asarray(report["extinction_w"]), q14["extinction"]
            ),
            "q14_reported_radiation_relative": relative(
                np.asarray(report["radiation_w"]), q14["radiation"]
            ),
            "q14_reported_current_change_relative": relative(
                np.asarray(report["current_mass_l2_change_by_linear_drive"]),
                current_change,
            ),
            "q14_reported_absorption_change_relative": relative(
                np.asarray(report["absorption_change_relative_new"]),
                absorption_change,
            ),
            "q14_reported_dipole_relative": relative(report_dipole, main),
            "q14_reported_dipole_change_relative": relative(
                np.asarray(report["complex_magnetic_dipole_main_change_relative_new"]),
                dipole_change,
            ),
            "q14_reported_absorption_xy_relative": float(abs(
                report["absorption_xy_difference_relative"]
                - abs(q14["absorption"][0] - q14["absorption"][1])
                / max(abs(q14["absorption"][:2]))
            )),
            "q10_q14_modal_mass_relative": float(np.sqrt(
                np.trace(q10_q14_delta.conj().T @ mass @ q10_q14_delta).real
                / np.trace(q14["modal"].conj().T @ mass @ q14["modal"]).real
            )),
            "q10_q14_charge_relative": relative(q10["charge"], q14["charge"]),
            "q10_q14_reaction_relative": relative(q10["reaction"], q14["reaction"]),
            "q10_q14_dipole_relative": relative(q10["dipole"], q14["dipole"]),
            "order2_vs_old124_bilinear_reaction_relative": relative(
                q14["reaction"],
                baseline_fields[f"case_{index:02d}_reaction"],
            ),
        }
        cases.append(case)

    maximums = {
        key: float(max(case[key] for case in cases))
        for key in cases[0]
        if key != "frequency_hz"
    }
    gates = {
        "input_links": all(old_block_checks.values()),
        "q10_q14_kernel_refinement":
            kernel_checks["q10_q14_new_static_diagonal_scaled_max"] < 1e-6,
        "saved_field_algebra": max(
            maximums["q14_componentwise_backward"],
            maximums["q14_modal_scale_relative"],
            maximums["q14_saved_charge_relative"],
            maximums["q14_saved_reaction_relative"],
            maximums["q14_saved_dipole_relative"],
        ) < 1e-11,
        "saved_field_power": max(
            maximums["q14_power_relative"],
            maximums["q14_reported_absorption_relative"],
            maximums["q14_reported_extinction_relative"],
            maximums["q14_reported_radiation_relative"],
        ) < 1e-8,
        "reported_changes": max(
            maximums["q14_reported_current_change_relative"],
            maximums["q14_reported_absorption_change_relative"],
            maximums["q14_reported_dipole_relative"],
            maximums["q14_reported_dipole_change_relative"],
            maximums["q14_reported_absorption_xy_relative"],
        ) < 1e-8,
    }
    gates = {key: bool(value) for key, value in gates.items()}
    assert all(gates.values()), (gates, kernel_checks, maximums)

    output = ROOT / "outputs/research/astra-box-axial-order2-field-review-01"
    assert not output.exists()
    output.mkdir()
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_SAVED_AXIAL_ORDER2_Q10_Q14_FIELD_ALGEBRA_WITH_SCOPE",
        "input_sha256": PINS,
        "reviewer_sha256": digest(Path(__file__)),
        "frozen_charge_symmetry_qualified": False,
        "frozen_charge_covariance_failure": charge_covariance,
        "kernel_checks": kernel_checks,
        "maximum_checks": maximums,
        "cases": cases,
        "gates": gates,
        "scope": (
            "No-solve reconstruction of the saved 136-current q10/q14 systems, "
            "modal scaling, currents, boundary charge, bilinear reaction, magnetic "
            "dipole, absorption, extinction and power using the saved radiation "
            "matrices. The old124 comparison is a synthetic incident-field response, "
            "not port impedance. Radiation quadrature is not independently repeated. "
            "The frozen 2.734e-6 charge covariance failure remains; this review does "
            "not certify full-operator xy symmetry, basis/frequency/mesh convergence, "
            "material interfaces, source geometry, board Z or PowerSI accuracy."
        ),
    }
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
    print(json.dumps({
        "status": review["status"],
        "reviewer_sha256": review["reviewer_sha256"],
        "receipt_sha256": digest(output / "independent-review.json"),
        "maximum_checks": maximums,
    }))


if __name__ == "__main__":
    main()
