"""SPD Decap PI Evaluator v0.23.1 saved axial cross/field review.

Rebuild the q10/q14 Green blocks from saved primitive columns and re-evaluate
the saved q14 algebra and power without a solve or Green integration.
"""
from hashlib import sha256
from math import factorial
from pathlib import Path
import json

import numpy as np

import diagnose_astra_constant_current_3d_field as old


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/qualify_astra_box_axial_green.py":
        "c24cf015c4c2788914c67cca8539411194d20734851835293acd051f49925801",
    "tools/research/diagnose_astra_box_axial_field.py":
        "27619157c6fc1886de290e60eb194b764050df4cbb49a2c3c578ddeb2f8d39e5",
    "outputs/research/astra-box-axial-polynomial-green-01/result.json":
        "a0f2b2e497196b7f9717d6963af4e546c31b9fc8e944a4fde82b336f0c823c09",
    "outputs/research/astra-box-axial-polynomial-green-01/kernels.npz":
        "9d78c4560b21da78bfdd21225e38fd8c356ade17258081ba3fad2b0c2fc9eae2",
    "outputs/research/astra-box-axial-current-space-02/result.json":
        "285dd00015e65400868e2729fddc794d2681cb14925f465c545e3f399fd6f740",
    "outputs/research/astra-box-axial-current-space-02/space.npz":
        "42504b92ebfb93fe401baf294a57ef9d362a29f65026f03b12f065b94a6f05c7",
    "outputs/research/astra-box-axial-cross-01/result.json":
        "e5faf3e6253fb2ad8ca9b3c982cc0a493eaace41312b7a3c3fdecdeebe63f13d",
    "outputs/research/astra-box-axial-cross-01/cross-q10.npz":
        "61e042d88a02d9928f283b740d1d6b2422afc31061fd3e851d1009f0aac68818",
    "outputs/research/astra-box-axial-cross-01/cross-q14.npz":
        "be29a881b5fea4d8215c61154c05171836ea3f46cb0270c1b4d7bde9409286ce",
    "outputs/research/astra-box-axial-q10-field-01/result.json":
        "acd76a5890eaa7631e6530368cc487b68e63b63bd44fd9a5a7785d1aad4634b5",
    "outputs/research/astra-box-axial-q10-field-01/fields.npz":
        "41f62cca74e1e65f01b15065967491c328b4b1aaf184b37f9faed4e8d35b4b6d",
    "outputs/research/astra-box-axial-q14-field-01/result.json":
        "7700be7762350cba5428d7861eb9dbbabc9d4dc4f7d772eb44d3b8eeeb657754",
    "outputs/research/astra-box-axial-q14-field-01/fields.npz":
        "59510348006dc8a15f2c8f8951e27994055bb188633635b3c173bfbbbcb51fe6",
    "outputs/research/astra-box-enriched-kernels-02/cross-q18.npz":
        "1f4bcfaa4803662fb86db21d0c8bfd19e58e0e04176ecc5f408379c92b3720cd",
    "outputs/research/astra-box-enriched-q18-field-01/result.json":
        "eb2eca822694238bf13f1d57987cbc5dab7f76216616e22037d9c0ec246ead9e",
    "outputs/research/astra-box-enriched-q18-field-01/fields.npz":
        "419cdddf1fb25b4dc009107dfdbaac46008611bbc4869e79b2446dcaf235c36e",
    "outputs/research/astra-box-polynomial-current-space-01/space.npz":
        "41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9",
    "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz":
        "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02",
    "outputs/research/astra-box-axial-field-review-01/independent-review.json":
        "ce0ba8d405db3210e34c56eddff9fc8af2c000f98ec9238c0251076f802df8c2",
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
    return float(np.max(np.abs(residual) / np.maximum(denominator, np.finfo(float).tiny)))


def rebuild_green(cross_path, polynomial, old_static, old_tails, old_order, frequencies, length):
    with np.load(cross_path, allow_pickle=False) as saved:
        rt0 = saved["rt0_axial_distance_powers"]
        stored_static = saved["static_green"]
        stored_tails = saved["real_retarded_tail"]
        stored_order = saved["coordinate_order_from_old120_plus2"]
    columns = np.concatenate((rt0, polynomial[:, :48]), axis=1)[:, old_order]
    order = np.r_[np.arange(73), np.arange(120, 122), np.arange(73, 120)]
    static = np.block([
        [old_static, columns[0]],
        [columns[0].T, polynomial[0, 48:]],
    ])[np.ix_(order, order)]
    tails = []
    for index, frequency in enumerate(frequencies):
        k = 2 * np.pi * frequency * np.sqrt(old.source.MU0 * old.source.EPS0)
        cross_tail = sum(
            ((-1j * k * length) ** power / factorial(power) * columns[power] / length).real
            for power in (2, 4, 6, 8)
        )
        self_tail = sum(
            (
                (-1j * k * length) ** power
                / factorial(power)
                * polynomial[power, 48:]
                / length
            ).real
            for power in (2, 4, 6, 8)
        )
        tails.append(np.block([
            [old_tails[index], cross_tail],
            [cross_tail.T, self_tail],
        ])[np.ix_(order, order)])
    tails = np.asarray(tails)
    return {
        "rt0": rt0,
        "columns": columns,
        "order": order,
        "static": static,
        "tails": tails,
        "stored_static": stored_static,
        "stored_tails": stored_tails,
        "stored_order": stored_order,
    }


def main():
    for relative_path, expected in PINS.items():
        actual = digest(ROOT / relative_path)
        assert actual == expected, (relative_path, actual)
    cross_result = json.loads(
        (ROOT / "outputs/research/astra-box-axial-cross-01/result.json").read_bytes()
    )
    q10_result = json.loads(
        (ROOT / "outputs/research/astra-box-axial-q10-field-01/result.json").read_bytes()
    )
    q14_result = json.loads(
        (ROOT / "outputs/research/astra-box-axial-q14-field-01/result.json").read_bytes()
    )
    assert cross_result["status"] == "PASS_AXIAL_RT0_CROSS"
    assert q10_result["status"] == q14_result["status"] == "COMPLETE_BOX_AXIAL_FIELD"

    with np.load(
        ROOT / "outputs/research/astra-box-axial-polynomial-green-01/kernels.npz",
        allow_pickle=False,
    ) as saved:
        polynomial = saved["polynomial_axial_distance_powers"]
    with np.load(
        ROOT / "outputs/research/astra-box-enriched-kernels-02/cross-q18.npz",
        allow_pickle=False,
    ) as saved:
        old_static = saved["static_green"]
        old_tails = saved["real_retarded_tail"]
    with np.load(
        ROOT / "outputs/research/astra-box-polynomial-current-space-01/space.npz",
        allow_pickle=False,
    ) as saved:
        old_order = saved["n48_coordinate_order"]
    with np.load(
        ROOT / "outputs/research/astra-box-axial-current-space-02/space.npz",
        allow_pickle=False,
    ) as saved:
        mass = saved["mass"]
        divergence_range = saved["boundary_divergence_range"]
        space_order = saved["coordinate_order"]
    with np.load(
        ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz",
        allow_pickle=False,
    ) as saved:
        tetrahedra = saved["tetrahedra_m"]
        triangles = saved["triangles_m"]
        frequencies = saved["frequencies_hz"]
        boundary, _, base_q, _, _ = old.frame(tetrahedra, triangles)
        scalar_indices = 48 + boundary
        scalar_static = saved["static_scalar_per_m"][np.ix_(scalar_indices, scalar_indices)]
        scalar_tails = saved["scalar_tail_per_m"][
            :, scalar_indices[:, None], scalar_indices
        ].real
    assert np.array_equal(divergence_range, base_q)
    dimensions = np.ptp(tetrahedra.reshape(-1, 3), axis=0)
    length = float(np.max(dimensions))

    rebuilt = {}
    for label in ("q10", "q14"):
        rebuilt[label] = rebuild_green(
            ROOT / f"outputs/research/astra-box-axial-cross-01/cross-{label}.npz",
            polynomial,
            old_static,
            old_tails,
            old_order,
            frequencies,
            length,
        )
    assert np.array_equal(space_order, rebuilt["q14"]["order"])

    static_scale = np.sqrt(np.diag(rebuilt["q14"]["static"]))
    new_columns = np.r_[73:75]
    q10_q14_cross_static = float(np.max(
        np.abs(
            rebuilt["q10"]["static"][:, new_columns]
            - rebuilt["q14"]["static"][:, new_columns]
        )
        / (
            static_scale[:, None]
            * static_scale[new_columns][None, :]
        )
    ))
    cross_checks = {
        "q10_saved_static_rebuild_relative": relative(
            rebuilt["q10"]["stored_static"], rebuilt["q10"]["static"]
        ),
        "q14_saved_static_rebuild_relative": relative(
            rebuilt["q14"]["stored_static"], rebuilt["q14"]["static"]
        ),
        "q10_saved_real_tails_rebuild_relative": relative(
            rebuilt["q10"]["stored_tails"], rebuilt["q10"]["tails"]
        ),
        "q14_saved_real_tails_rebuild_relative": relative(
            rebuilt["q14"]["stored_tails"], rebuilt["q14"]["tails"]
        ),
        "q10_order_exact": bool(np.array_equal(
            rebuilt["q10"]["stored_order"], rebuilt["q10"]["order"]
        )),
        "q14_order_exact": bool(np.array_equal(
            rebuilt["q14"]["stored_order"], rebuilt["q14"]["order"]
        )),
        "q10_q14_cross_static_diagonal_scaled_max": q10_q14_cross_static,
        "q10_q14_full_real_tails_relative": relative(
            rebuilt["q10"]["tails"], rebuilt["q14"]["tails"]
        ),
    }

    rows, properties = old.source.source_inputs()
    drives = np.array([[1, 0, 1], [0, 1, 1j]], complex)
    field_sets = {}
    for label in ("q10", "q14"):
        with np.load(
            ROOT / f"outputs/research/astra-box-axial-{label}-field-01/fields.npz",
            allow_pickle=False,
        ) as saved:
            field_sets[label] = {name: saved[name] for name in saved.files}
        assert np.array_equal(field_sets[label]["coordinate_order"], rebuilt[label]["order"])
        assert np.array_equal(field_sets[label]["mass"], mass)
    with np.load(
        ROOT / "outputs/research/astra-box-enriched-q18-field-01/fields.npz",
        allow_pickle=False,
    ) as saved:
        baseline_fields = {name: saved[name] for name in saved.files}
    baseline_result = json.loads(
        (ROOT / "outputs/research/astra-box-enriched-q18-field-01/result.json").read_bytes()
    )

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
        per_order = {}
        for label in ("q10", "q14"):
            saved = field_sets[label]
            solution = saved[f"case_{index:02d}_scaled_solution"]
            modal = saved[f"case_{index:02d}_modal_current"]
            rhs = saved[f"case_{index:02d}_incident_rhs"]
            radiation_matrix = saved[f"case_{index:02d}_radiation_matrix"]
            system = (
                mass / kappa
                + 1j * omega * 1e-7
                * (rebuilt[label]["static"] + rebuilt[label]["tails"][index])
                + radiation_matrix
            )
            system[:, 75:] *= omega
            system[75:, 75:] += scalar / 1j
            expected_modal = solution.copy()
            expected_modal[75:] *= omega
            charge = 1j * divergence_range @ solution[75:]
            reaction = rhs.T @ modal
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
            per_order[label] = {
                "solution": solution,
                "modal": modal,
                "rhs": rhs,
                "reaction": reaction,
                "currents": currents,
                "absorption": absorption,
                "radiation": radiation,
                "extinction": extinction,
                "backward": componentwise_backward(system, solution, rhs),
                "modal_scale_relative": relative(modal, expected_modal),
                "charge_relative": relative(
                    saved[f"case_{index:02d}_boundary_charge"], charge
                ),
                "reaction_relative": relative(
                    saved[f"case_{index:02d}_reaction"], reaction
                ),
                "power_relative": float(np.max(
                    np.abs(extinction - absorption - radiation)
                    / np.maximum(
                        np.abs(extinction) + np.abs(absorption) + np.abs(radiation),
                        np.finfo(float).tiny,
                    )
                )),
            }
        q14 = per_order["q14"]
        q10 = per_order["q10"]
        old_modal = np.vstack((
            baseline_fields[f"case_{index:02d}_modal_current"],
            np.zeros((2, 2)),
        ))[rebuilt["q14"]["order"]]
        difference = q14["modal"] - old_modal
        current_change = np.sqrt(
            np.diag(difference.conj().T @ mass @ difference).real
            / np.diag(q14["modal"].conj().T @ mass @ q14["modal"]).real
        )
        old_loss = np.asarray(baseline_result["cases"][index]["absorption_w"])
        absorption_change = (q14["absorption"] - old_loss) / q14["absorption"]
        report = q14_result["cases"][index]
        cases.append({
            "frequency_hz": float(frequency),
            "q14_componentwise_backward": q14["backward"],
            "q14_modal_scale_relative": q14["modal_scale_relative"],
            "q14_saved_charge_relative": q14["charge_relative"],
            "q14_saved_reaction_relative": q14["reaction_relative"],
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
            "q10_q14_modal_mass_relative": float(
                np.sqrt(
                    np.trace(
                        (q10["modal"] - q14["modal"]).conj().T
                        @ mass
                        @ (q10["modal"] - q14["modal"])
                    ).real
                    / np.trace(q14["modal"].conj().T @ mass @ q14["modal"]).real
                )
            ),
            "q10_q14_charge_relative": relative(
                field_sets["q10"][f"case_{index:02d}_boundary_charge"],
                field_sets["q14"][f"case_{index:02d}_boundary_charge"],
            ),
            "q10_q14_bilinear_reaction_relative": relative(
                q10["reaction"], q14["reaction"]
            ),
            "axial_vs_old_bilinear_reaction_relative": relative(
                q14["reaction"],
                baseline_fields[f"case_{index:02d}_reaction"],
            ),
        })

    maximums = {
        key: float(max(case[key] for case in cases))
        for key in cases[0]
        if key != "frequency_hz"
    }
    gates = {
        "saved_cross_rebuild": max(
            cross_checks["q10_saved_static_rebuild_relative"],
            cross_checks["q14_saved_static_rebuild_relative"],
            cross_checks["q10_saved_real_tails_rebuild_relative"],
            cross_checks["q14_saved_real_tails_rebuild_relative"],
        ) < 1e-13
        and cross_checks["q10_order_exact"]
        and cross_checks["q14_order_exact"],
        "q10_q14_cross_refinement":
            cross_checks["q10_q14_cross_static_diagonal_scaled_max"] < 1e-6,
        "saved_field_algebra": max(
            maximums["q14_componentwise_backward"],
            maximums["q14_modal_scale_relative"],
            maximums["q14_saved_charge_relative"],
            maximums["q14_saved_reaction_relative"],
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
        ) < 1e-8,
    }
    assert all(gates.values()), (gates, maximums, cross_checks)

    output = ROOT / "outputs/research/astra-box-axial-field-review-02"
    assert not output.exists()
    output.mkdir()
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_SAVED_AXIAL_CROSS_AND_FIELD_WITH_SCOPE",
        "input_sha256": PINS,
        "reviewer_sha256": digest(Path(__file__)),
        "cross_checks": cross_checks,
        "maximum_checks": maximums,
        "cases": cases,
        "gates": gates,
        "supersedes":
            "field-review-01 checked saved modal scaling/reaction/symmetry only; "
            "this receipt additionally rebuilds Green blocks, equations, charge, "
            "physical power, and reported changes.",
        "scope":
            "No-solve review of saved q10/q14 axial RT0 cross and synthetic-box "
            "field algebra. Bilinear reaction and current changes are diagnostic "
            "responses, not port impedance. It does not establish complete 3D, "
            "frequency, mesh, material-interface, port, board, or PowerSI convergence.",
    }
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
    print(json.dumps({
        "status": review["status"],
        "reviewer_sha256": review["reviewer_sha256"],
        "receipt_sha256": digest(output / "independent-review.json"),
    }))


if __name__ == "__main__":
    main()
